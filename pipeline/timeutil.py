"""Единая шкала времени.

Правило: в базе всё хранится в UTC с явным смещением, показывается в МСК.
Раньше posted_at писался наивным локальным временем, а received_at — настоящим
UTC, и rebuild штамповал локальное как UTC. На машине в MSK это выглядело
правильно, на сервере в UTC вся лента уехала бы на три часа.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def to_utc(value: datetime) -> datetime:
    """Привести к UTC. Наивное значение считается локальным временем машины."""
    if value.tzinfo is None:
        return value.astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def utc_iso(value: datetime) -> str:
    return to_utc(value).isoformat()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value: str, *, assume: timezone = MSK) -> datetime:
    """Разобрать метку из базы.

    Значения без смещения — наследие старого формата: это локальное МСК.
    """
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=assume)
    return moment.astimezone(timezone.utc)
