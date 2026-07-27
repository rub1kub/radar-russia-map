"""Разрешение топонимов сообщения в зоны справочника.

Главная сложность — омонимы: 22 177 названий НП повторяются. Разрешаются по
контексту сообщения: если в тексте назван регион или район, кандидат из этой
ветки иерархии выигрывает.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import lru_cache

from .textnorm import norm_key

# Названия НП, совпадающие с обиходными словами. Принимаются только при
# явном контексте региона или района, иначе дают массовые ложные срабатывания.
AMBIGUOUS = {
    "мир", "победа", "восход", "заря", "новый", "новая", "central", "рассвет",
    "дружба", "родина", "звезда", "маяк", "октябрьский", "первомайский",
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

MAX_NGRAM = 4


@dataclass
class Resolved:
    zone_id: str
    level: str
    name: str
    lat: float | None
    lon: float | None
    phrase: str


class Geocoder:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.by_name: dict[str, list[str]] = {}
        self.zones: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        for row in self.connection.execute("SELECT norm, zone_id FROM zone_names"):
            self.by_name.setdefault(row["norm"], []).append(row["zone_id"])
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

    def _scan(self, phrase: str) -> list[tuple[str, list[str]]]:
        """Скользящее сопоставление n-грамм, длинные совпадения приоритетнее.

        В реальных сообщениях топоним окружен мусором: «🔴Краснодар и ближайшие»,
        «Славянский район и далее в глубь». Точное совпадение всего фрагмента
        здесь не работает.
        """
        words = norm_key(phrase).split()
        found: list[tuple[str, list[str]]] = []
        index = 0
        while index < len(words):
            for size in range(min(MAX_NGRAM, len(words) - index), 0, -1):
                key = " ".join(words[index:index + size])
                if len(key) < 4:
                    continue
                if size == 1 and key in STOPWORDS:
                    continue
                hits = self.by_name.get(key)
                if hits:
                    found.append((key, hits))
                    index += size
                    break
            else:
                index += 1
        return found

    def resolve(self, phrases: list[str]) -> list[Resolved]:
        """Разрешить фразы сообщения в зоны, снимая омонимию контекстом."""
        candidates: list[tuple[str, list[str]]] = []
        for phrase in phrases:
            candidates.extend(self._scan(phrase))

        # Контекст — однозначно определенные регионы и районы сообщения.
        context: set[str] = set()
        for _phrase, hits in candidates:
            for zone_id in hits:
                zone = self.zones[zone_id]
                if zone["level"] in ("region", "district") and len(hits) <= 2:
                    context.update(self.chain(zone_id))

        resolved: list[Resolved] = []
        used: set[str] = set()
        for phrase, hits in candidates:
            key = norm_key(phrase)
            best = self._pick(hits, context, key)
            if best is None or best in used:
                continue
            used.add(best)
            zone = self.zones[best]
            resolved.append(Resolved(best, zone["level"], zone["name_ru"],
                                     zone["lat"], zone["lon"], phrase))
        return resolved

    def _pick(self, hits: list[str], context: set[str], key: str) -> str | None:
        scored: list[tuple[float, str]] = []
        for zone_id in hits:
            zone = self.zones[zone_id]
            in_context = bool(set(self.chain(zone_id)) & context)

            if key in AMBIGUOUS and not in_context:
                continue

            # Одиночное слово, совпавшее с безвестной деревней и не поддержанное
            # контекстом, — почти всегда шум: «Суда», «Север», «Победа».
            if (" " not in key and not in_context and zone["level"] == "place"
                    and (zone["population"] or 0) < 1_000):
                continue

            score = 0.0
            if in_context:
                score += 1_000_000
            # Административные единицы надежнее одноименных деревень.
            score += (2 - LEVEL_RANK[zone["level"]]) * 10_000
            score += min(zone["population"] or 0, 500_000) / 100
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
