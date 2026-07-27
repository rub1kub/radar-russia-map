"""Слияние наблюдений разных источников в события.

Ключ кластеризации — не текст, а тройка (зона, тип угрозы, временное окно).
Совпадение текста без времени бесполезно: «Краснодарский край, опасность по
БПЛА» повторяется месяцами, а настоящее подтверждение приходит из 4 лент за 44 с.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Вес источника в расчете достоверности (см. tier в ingest/config.py).
TIER_WEIGHT = {"federal": 0.55, "regional": 0.4, "mixed": 0.25}

SAME_ZONE_WINDOW = timedelta(minutes=5)
PARENT_ZONE_WINDOW = timedelta(minutes=15)
FADE_AFTER = timedelta(minutes=45)
CLOSE_AFTER = timedelta(hours=3)

# Радиус неопределенности по уровню зоны.
ACCURACY_M = {"place": 4_000, "district": 12_000, "region": 40_000}

RESOLVING = {"allclear", "retracted"}


@dataclass
class Event:
    id: str
    zone_id: str
    zone_path: list[str]
    threat_type: str
    signal_type: str
    severity: int
    first_seen: datetime
    last_seen: datetime
    resolved_at: datetime | None = None
    lat: float | None = None
    lon: float | None = None
    accuracy_m: int = 12_000
    direction_deg: int | None = None
    target_count: int | None = None
    sources: dict[str, str] = field(default_factory=dict)   # source_key -> tier
    # network_id -> tier. Клоны одной сети дают один голос: десяток лент вида
    # "Радар.ру | X область" ведёт один оператор, и считать их независимыми
    # подтверждениями значит выдумывать достоверность.
    networks: dict[str, str] = field(default_factory=dict)
    contributions: list[tuple[int, str, str, datetime]] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """Вероятностное объединение независимых свидетельств.

        Один federal-источник дает 0.55, два — 0.80, три — 0.91.
        Считается по сетям, а не по каналам: иначе пять клонов одного
        оператора выглядели бы как пять независимых подтверждений.
        """
        miss = 1.0
        for tier in (self.networks or self.sources).values():
            miss *= 1.0 - TIER_WEIGHT.get(tier, 0.25)
        return round(1.0 - miss, 3)

    @property
    def independent_sources(self) -> int:
        """Сколько независимых голосов стоит за событием."""
        return len(self.networks or self.sources)

    def status(self, now: datetime) -> str:
        if self.resolved_at:
            return "resolved"
        if now - self.last_seen > CLOSE_AFTER:
            return "resolved"
        if now - self.last_seen > FADE_AFTER:
            return "fading"
        return "active"


def make_id(zone_id: str, threat: str, moment: datetime) -> str:
    seed = f"{zone_id}|{threat}|{moment.isoformat()}"
    return hashlib.sha1(seed.encode()).hexdigest()[:16]


class Fuser:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self._open: list[Event] = []

    def _match(self, zone_path: list[str], threat: str, moment: datetime) -> Event | None:
        zone_id = zone_path[0]
        best: Event | None = None
        for event in reversed(self._open):
            if event.resolved_at:
                continue
            if event.threat_type != threat and "unknown" not in (event.threat_type, threat):
                continue

            gap = moment - event.last_seen
            if gap < timedelta(0):
                continue

            if event.zone_id == zone_id and gap <= SAME_ZONE_WINDOW:
                return event
            # Родственные зоны: район и его регион, НП и его район.
            if gap <= PARENT_ZONE_WINDOW and (
                zone_id in event.zone_path or event.zone_id in zone_path
            ):
                best = best or event
        return best

    def _prune(self, now: datetime) -> None:
        self._open = [
            event for event in self._open
            if not event.resolved_at and now - event.last_seen <= CLOSE_AFTER
        ]

    def add(self, *, raw_id: int, source_key: str, tier: str, moment: datetime,
            observation, zone_path: list[str], lat, lon, level: str,
            network: str | None = None) -> Event | None:
        """Добавить наблюдение. Возвращает затронутое событие."""
        self._prune(moment)
        zone_id = zone_path[0]

        # Отбой закрывает открытые события в этой зоне и ниже.
        if observation.signal_type in RESOLVING:
            closed = None
            for event in self._open:
                if event.resolved_at:
                    continue
                if event.zone_id == zone_id or zone_id in event.zone_path:
                    event.resolved_at = moment
                    event.last_seen = max(event.last_seen, moment)
                    event.contributions.append((raw_id, source_key, "resolve", moment))
                    closed = event
            return closed

        existing = self._match(zone_path, observation.threat_type, moment)
        if existing:
            existing.last_seen = max(existing.last_seen, moment)
            existing.severity = max(existing.severity, observation.severity)
            if observation.threat_type != "unknown":
                existing.threat_type = observation.threat_type
            if observation.direction_deg is not None:
                existing.direction_deg = observation.direction_deg
            if observation.target_count:
                existing.target_count = max(existing.target_count or 0, observation.target_count)
            role = "confirm" if source_key not in existing.sources else "repeat"
            existing.sources.setdefault(source_key, tier)
            existing.networks.setdefault(network or source_key, tier)
            existing.contributions.append((raw_id, source_key, role, moment))
            return existing

        event = Event(
            id=make_id(zone_id, observation.threat_type, moment),
            zone_id=zone_id,
            zone_path=zone_path,
            threat_type=observation.threat_type,
            signal_type=observation.signal_type,
            severity=observation.severity,
            first_seen=moment,
            last_seen=moment,
            lat=lat,
            lon=lon,
            accuracy_m=ACCURACY_M.get(level, 12_000),
            direction_deg=observation.direction_deg,
            target_count=observation.target_count,
            sources={source_key: tier},
            networks={(network or source_key): tier},
            contributions=[(raw_id, source_key, "first", moment)],
        )
        self.events.append(event)
        self._open.append(event)
        return event
