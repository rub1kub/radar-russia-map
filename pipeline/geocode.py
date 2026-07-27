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
    # Стороны света в сводках всегда описывают направление подлёта («на восток»,
    # «с востока»), но в справочнике есть посёлок Восток — и он собирал их все.
    "восток", "запад", "север", "юг",
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

# Окончания, которые у существительного не встречаются: «Раевскую»,
# «Анапского», «Воронежскими» — только прилагательное.
ADJECTIVE_ONLY_ENDINGS = (
    "ого", "его", "ому", "ему", "ыми", "ими",
    "ая", "яя", "ое", "ее", "ую", "юю", "ые", "ие", "ых", "их",
    "ым", "им", "ий", "ый",
)
# «-ой/-ей/-ом/-ем» несут двойную нагрузку: «Воронежской» — прилагательное,
# «Анапой», «Ростовом» — существительное. Для выбора админ-единицы этого
# хватает (в _candidates есть безопасный откат), для отсечения чужих тёзок
# нет — там нужен только ADJECTIVE_ONLY_ENDINGS.
#
# Одиночное прилагательное в косвенном падеже почти всегда называет
# административную единицу с опущенным типовым словом: «в Воронежской» —
# область, а не село Воронежское; «Краснодарского и Ставропольского» — края.
ADJECTIVE_ENDINGS = ADJECTIVE_ONLY_ENDINGS + ("ом", "ем", "ой", "ей")

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
    # Исходная словоформа одиночного стеммированного матча: по её окончанию
    # видно, прилагательное это или существительное. У остальных матчей пусто.
    word: str = ""

    @property
    def adjectival(self) -> bool:
        return self.word.endswith(ADJECTIVE_ENDINGS)

    @property
    def adjective_only(self) -> bool:
        """Окончание, невозможное у существительного."""
        return self.word.endswith(ADJECTIVE_ONLY_ENDINGS)


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
    def _is_event_word(key: str, size: int, stemmed: bool) -> bool:
        """Одиночное слово из лексики обстановки — не топоним ни в каком падеже.

        Ключ стеммированного прохода уже обрезан, и стеммировать его повторно
        нельзя: stem_word не идемпотентна («мория» -> «мори» -> «мор»), от
        второго прохода под запрет попадали посторонние имена.
        """
        if size != 1:
            return False
        stem = key if stemmed else stem_word(key)
        return key in STOPWORDS or stem in STOPWORD_STEMS

    @staticmethod
    def _heads_feature(words: list[str], index: int, size: int) -> bool:
        """N-грамма — определение к стоящему следом физико-географическому объекту?

        Проверяется в обоих проходах: «Таманский полуостров» и «Крымский мост»
        стоят в сводках в именительном падеже и попадали в точный индекс мимо
        этого правила, разрешаясь в посёлок Таманский и Крымский район.

        Существительное перед объектом, наоборот, называет самостоятельное
        место: «Новороссийск НПЗ», «Славянск-на-Кубани НПЗ» — это города.
        """
        after = index + size
        if after >= len(words) or stem_word(words[after]) not in FEATURE_HEAD_STEMS:
            return False
        return words[after - 1].endswith(ADJECTIVE_ENDINGS)

    def _lookup(self, words: list[str], index: int,
                exact: bool) -> tuple[str, list[str], int] | None:
        """Самая длинная n-грамма с позиции index, найденная в нужном индексе."""
        table = self.by_name if exact else self.by_stem
        for size in range(min(MAX_NGRAM, len(words) - index), 0, -1):
            key = " ".join(words[index:index + size])
            if not exact:
                key = stem_key(key)
            if len(key) < 4 or self._is_event_word(key, size, stemmed=not exact):
                continue
            if self._heads_feature(words, index, size):
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
        word = "" if exact or size != 1 else words[index]
        return Match(key, zone_ids, exact, word), size

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

        # Ключ стеммированного прохода уже обрезан, второй проход запрещён:
        # см. оговорку про идемпотентность в _is_event_word.
        stem = key if not match.exact else stem_word(key)
        ambiguous = key in AMBIGUOUS or (single and stem in AMBIGUOUS_STEMS)

        candidates = self._candidates(match)
        # Прилагательное в косвенном падеже, за которым в справочнике стоят
        # только НП, без контекста почти всегда чужой тёзка: «через Раевскую»
        # под Новороссийском разрешалось в Раевский в Башкортостане (1 600 км).
        stray_adjective = match.adjective_only and all(
            self.zones[zone_id]["level"] == "place" for zone_id in candidates)
        prominent = weak and not stray_adjective and self._is_prominent(candidates)

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
            # Штрафовать стеммированное совпадение здесь бессмысленно: скоринг
            # идёт внутри одного матча, где match.exact общий для всех зон, и
            # константа не меняет порядок. Приоритет точной формы обеспечивает
            # порядок проходов в _match.
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
