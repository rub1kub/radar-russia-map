"""Разрешение топонимов сообщения в зоны справочника.

Главная сложность — омонимы: 22 177 названий НП повторяются. Разрешаются по
контексту сообщения: если в тексте назван регион или район, кандидат из этой
ветки иерархии выигрывает.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import lru_cache

from .textnorm import norm_key, stem_key, stem_word

# Названия НП, совпадающие с обиходными словами. Принимаются только при
# явном контексте региона или района, иначе дают массовые ложные срабатывания.
AMBIGUOUS = {
    "мир", "победа", "восход", "заря", "новый", "новая", "central", "рассвет",
    "дружба", "родина", "звезда", "маяк", "октябрьский", "первомайский",
    "старый", "старая", "старое", "большой", "большая", "малый", "малая",
    "северный", "южный", "западный", "восточный", "центральный", "лесной",
    "луговое", "озерное", "речное", "садовое", "степное", "красное", "белое",
    "россия", "украина", "тыл", "море", "берег", "город", "район", "область",
}

LEVEL_RANK = {"region": 0, "district": 1, "place": 2}

# Лексика обстановки. Никогда не является топонимом, даже если в справочнике
# есть одноименная деревня.
STOPWORDS = {
    "опасность", "опасности", "тревога", "тревоги", "бпла", "фиксация", "фиксации",
    "фиксациям", "внимание", "меры", "мера", "безопасности", "предосторожности",
    "отбой", "работа", "пво", "сбитие", "сбит", "взрыв", "гром", "сирена",
    "направлении", "направление", "сторону", "далее", "близлежащие", "ближайшие",
    "пригород", "глубь", "угроза", "атака", "атаки", "массовая", "максимальная",
    "ракета", "ракетная", "авиация", "дрон", "дроны", "цель", "цели", "борт",
    "район", "районы", "область", "области", "край", "республика", "округ",
    "город", "поселение", "территории", "муниципальных", "образований",
    "информация", "экстренная", "беспилотная", "движение", "аэропорт",
    "соблюдать", "продолжаем", "паники", "погодные", "условия", "еще", "ещё",
    "суда", "судно", "судов", "гражданские", "побережье", "полуостров",
    "тыл", "приграничье", "акватория", "море", "залив", "мост", "аэропорту",
}

# Стеммированные варианты защитных списков. Без них «по краю», «в районе»,
# «моря», «мерами» проскочили бы мимо STOPWORDS в стеммированном проходе.
STOPWORD_STEMS = {stem_word(word) for word in STOPWORDS}
AMBIGUOUS_STEMS = {stem_word(word) for word in AMBIGUOUS}

# Физико-географические объекты справочником не покрыты, но их определения
# выглядят как топонимы. Прилагательное перед таким словом относится к нему,
# а не к одноименному хутору: «с Азовского моря» — не станица Азовская,
# «Таманского полуострова» — не посёлок Таманский, «в северной части» — не
# посёлок Северный.
FEATURE_HEADS = {
    "море", "залив", "полуостров", "мост", "побережье", "коса", "лиман",
    "водохранилище", "часть", "направление", "тэс", "аэс", "гэс", "нпз",
}
FEATURE_HEAD_STEMS = {stem_word(word) for word in FEATURE_HEADS}

# Ниже этого порога населенный пункт слишком безвестен, чтобы его называли
# одним словом в сводке. Отсекает «Примерный», «Крайний», «Ударное».
MIN_PLACE_POPULATION = 1_000

# Стеммированное одиночное слово без контекста принимается, только если за
# ключом стоит что-то заметное: регион или крупный НП. Иначе среди 2 327
# районов и 209 789 НП найдётся тёзка любому обиходному прилагательному —
# «в свободном доступе» разрешалось в городской округ Свободный.
PROMINENT_POPULATION = 20_000

# Окончания прилагательных. Одиночное прилагательное в косвенном падеже почти
# всегда называет административную единицу с опущенным типовым словом:
# «в Воронежской» — область, а не село Воронежское; «Краснодарского и
# Ставропольского» — края. Существительное («Михайловки») такой пометки не
# получает и разрешается обычным порядком.
ADJECTIVE_ENDINGS = (
    "ого", "его", "ому", "ему", "ыми", "ими",
    "ая", "яя", "ое", "ее", "ую", "юю", "ые", "ие", "ых", "их",
    "ым", "им", "ом", "ем", "ой", "ей", "ий", "ый",
)

MAX_NGRAM = 4


@dataclass
class Resolved:
    zone_id: str
    level: str
    name: str
    lat: float | None
    lon: float | None
    phrase: str


@dataclass
class Match:
    """Совпадение n-граммы со справочником."""

    key: str
    zone_ids: list[str]
    exact: bool
    adjectival: bool = False


class Geocoder:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.by_name: dict[str, list[str]] = {}
        self.by_stem: dict[str, list[str]] = {}
        self.zones: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        # Стеммированный индекс строится здесь, а не в справочнике: zone_names
        # заполняет gazetteer, и держать там производный ключ значило бы
        # пересобирать весь справочник ради правки стеммера.
        stem_sets: dict[str, set[str]] = {}
        for row in self.connection.execute("SELECT norm, zone_id FROM zone_names"):
            norm, zone_id = row["norm"], row["zone_id"]
            self.by_name.setdefault(norm, []).append(zone_id)
            stem_sets.setdefault(stem_key(norm), set()).add(zone_id)
        # Порядок фиксируем, чтобы разбор был воспроизводимым от запуска к запуску.
        self.by_stem = {stem: sorted(ids) for stem, ids in stem_sets.items()}

        for row in self.connection.execute(
            "SELECT id, parent_id, level, name_ru, lat, lon, population FROM zones"
        ):
            self.zones[row["id"]] = dict(row)

    @lru_cache(maxsize=100_000)
    def chain(self, zone_id: str) -> tuple[str, ...]:
        """Цепочка от зоны к корню: [зона, район, регион]."""
        path: list[str] = []
        current: str | None = zone_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            path.append(current)
            zone = self.zones.get(current)
            current = zone["parent_id"] if zone else None
        return tuple(path)

    @staticmethod
    def _is_event_word(key: str, size: int) -> bool:
        """Одиночное слово из лексики обстановки — не топоним ни в каком падеже."""
        return size == 1 and (key in STOPWORDS or stem_word(key) in STOPWORD_STEMS)

    @staticmethod
    def _heads_feature(words: list[str], after: int) -> bool:
        """Следом за n-граммой стоит физико-географический объект?"""
        return after < len(words) and stem_word(words[after]) in FEATURE_HEAD_STEMS

    def _lookup(self, words: list[str], index: int,
                exact: bool) -> tuple[str, list[str], int] | None:
        """Самая длинная n-грамма с позиции index, найденная в нужном индексе."""
        table = self.by_name if exact else self.by_stem
        for size in range(min(MAX_NGRAM, len(words) - index), 0, -1):
            key = " ".join(words[index:index + size])
            if not exact:
                key = stem_key(key)
            if len(key) < 4 or self._is_event_word(key, size):
                continue
            if not exact and self._heads_feature(words, index + size):
                continue
            hits = table.get(key)
            if hits:
                return key, hits, size
        return None

    def _match(self, words: list[str], index: int) -> tuple[Match, int] | None:
        """Матч с позиции index: сначала точный индекс, затем стеммированный."""
        hit = self._lookup(words, index, exact=True)
        exact = hit is not None
        if hit is None:
            hit = self._lookup(words, index, exact=False)
        if hit is None:
            return None
        key, zone_ids, size = hit
        adjectival = (not exact and size == 1
                      and words[index].endswith(ADJECTIVE_ENDINGS))
        return Match(key, zone_ids, exact, adjectival), size

    def _scan(self, phrase: str) -> list[Match]:
        """Скользящее сопоставление n-грамм, длинные совпадения приоритетнее.

        В реальных сообщениях топоним окружен мусором: «🔴Краснодар и ближайшие»,
        «Славянский район и далее в глубь». Точное совпадение всего фрагмента
        здесь не работает.

        С каждой позиции сначала пробуется точный индекс и только потом
        стеммированный: справочник в именительном падеже надёжнее огрызка.
        """
        words = norm_key(phrase).split()
        found: list[Match] = []
        index = 0
        while index < len(words):
            hit = self._match(words, index)
            if hit is None:
                index += 1
                continue
            match, size = hit
            found.append(match)
            index += size
        return found

    def resolve(self, phrases: list[str]) -> list[Resolved]:
        """Разрешить фразы сообщения в зоны, снимая омонимию контекстом."""
        candidates: list[Match] = []
        for phrase in phrases:
            candidates.extend(self._scan(phrase))

        # Контекст — однозначно определенные регионы и районы сообщения.
        context: set[str] = set()
        for match in candidates:
            for zone_id in match.zone_ids:
                zone = self.zones[zone_id]
                if zone["level"] in ("region", "district") and len(match.zone_ids) <= 2:
                    context.update(self.chain(zone_id))

        resolved: list[Resolved] = []
        used: set[str] = set()
        for match in candidates:
            best = self._pick(match, context)
            if best is None or best in used:
                continue
            used.add(best)
            zone = self.zones[best]
            resolved.append(Resolved(best, zone["level"], zone["name_ru"],
                                     zone["lat"], zone["lon"], match.key))
        return resolved

    def _candidates(self, match: Match) -> list[str]:
        """Зоны-кандидаты матча: для одиночного прилагательного — только админ.

        Иначе выигрывает случайный одноименный хутор, попавший в контекстный
        регион: «от Воронежской и Тамбовской областей» разрешалось в село
        Воронежское Тамбовской области вместо Воронежской области.
        """
        if not match.adjectival:
            return match.zone_ids
        admin = [zone_id for zone_id in match.zone_ids
                 if self.zones[zone_id]["level"] != "place"]
        return admin or match.zone_ids

    def _is_prominent(self, zone_ids: list[str]) -> bool:
        """Есть ли за ключом регион или крупный НП, узнаваемый без контекста."""
        for zone_id in zone_ids:
            zone = self.zones[zone_id]
            if zone["level"] == "region":
                return True
            if (zone["population"] or 0) >= PROMINENT_POPULATION:
                return True
        return False

    def _pick(self, match: Match, context: set[str]) -> str | None:
        key = match.key
        single = " " not in key
        # Стеммированное одиночное слово — самый рискованный класс: «Мирное»,
        # «Мирный» и «Мира» сходятся в один ключ.
        weak = single and not match.exact

        ambiguous = key in AMBIGUOUS or (single and stem_word(key) in AMBIGUOUS_STEMS)

        candidates = self._candidates(match)
        prominent = weak and self._is_prominent(candidates)

        scored: list[tuple[float, str]] = []
        for zone_id in candidates:
            zone = self.zones[zone_id]
            in_context = bool(set(self.chain(zone_id)) & context)

            if ambiguous and not in_context:
                continue
            if weak and not in_context and not prominent:
                continue

            # Одиночное слово, совпавшее с безвестной деревней и не поддержанное
            # контекстом, — почти всегда шум: «Суда», «Север», «Победа».
            # Для стеммированного слова контекст от порога не спасает: «примерно»
            # и «крайне» тоже попадают в контекстный регион.
            if (single and zone["level"] == "place"
                    and (zone["population"] or 0) < MIN_PLACE_POPULATION
                    and (weak or not in_context)):
                continue

            score = 0.0
            if in_context:
                score += 1_000_000
            # Административные единицы надежнее одноименных деревень.
            score += (2 - LEVEL_RANK[zone["level"]]) * 10_000
            score += min(zone["population"] or 0, 500_000) / 100
            # Точное совпадение всегда весомее стеммированного.
            if not match.exact:
                score -= 5_000
            scored.append((score, zone_id))

        if not scored:
            return None
        scored.sort(key=lambda item: (-item[0], item[1]))
        return scored[0][1]

    def primary(self, resolved: list[Resolved]) -> Resolved | None:
        """Самая специфичная зона наблюдения."""
        if not resolved:
            return None
        return sorted(resolved, key=lambda item: (-LEVEL_RANK[item.level],))[0]

    def zone_path(self, zone_id: str) -> list[str]:
        return list(self.chain(zone_id))
