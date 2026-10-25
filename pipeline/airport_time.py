"""Время объявления аэропорта, а не время его поздней перепечатки."""

from __future__ import annotations

import re
import sqlite3
from bisect import bisect_right
from datetime import datetime, timedelta

from .parse import parse, strip_footer
from .timeutil import parse_utc

FAVT_SOURCES = ("favt_info", "ch1938794947")
COPY_WINDOW = timedelta(hours=24)
AIRPORT_STATUS_TTL = timedelta(hours=24)


def notice_key(text: str) -> str:
    # Переносы, эмодзи и звёздочки сносок у копии могут отличаться.
    return " ".join(re.findall(r"\w+", strip_footer(text).casefold()))


class AirportNoticeTimes:
    def __init__(self, connection: sqlite3.Connection,
                 since: datetime | None = None) -> None:
        self.connection = connection
        self.since = since
        self._official: dict[str, list[datetime]] | None = None

    def _load(self) -> dict[str, list[datetime]]:
        if self._official is None:
            self._official = {}
            bound = " AND posted_at >= ?" if self.since else ""
            params = (*FAVT_SOURCES,)
            if self.since:
                params += ((self.since - COPY_WINDOW).isoformat(),)
            rows = self.connection.execute(
                "SELECT posted_at, text FROM raw_messages"
                " WHERE source_key IN (?, ?)" + bound + " ORDER BY posted_at",
                params)
            for row in rows:
                key = notice_key(row["text"])
                self._official.setdefault(key, []).append(parse_utc(row["posted_at"]))
        return self._official

    def moment(self, row, observation) -> datetime:
        published = parse_utc(row["posted_at"])
        if observation.threat_type != "airport":
            return published
        forwarded = row["forwarded_at"]
        if forwarded:
            try:
                original = parse_utc(forwarded)
            except (ValueError, TypeError):
                pass
            else:
                if original <= published:
                    return original
        # Повторная публикация самим регулятором — новое объявление,
        # даже если его стандартная формулировка совпала со вчерашней.
        if row["source_key"] in FAVT_SOURCES:
            return published
        moments = self._load().get(notice_key(row["text"]), [])
        index = bisect_right(moments, published) - 1
        if index >= 0 and published - moments[index] <= COPY_WINDOW:
            return moments[index]
        return published


def order_airport_reposts(rows, clock: AirportNoticeTimes) -> None:
    """При полном проигрывании пересылка регулятора стоит во времени оригинала."""
    def order(row):
        if row["source_key"] in FAVT_SOURCES and row["forwarded_at"]:
            observation = parse(row["text"])
            if observation.threat_type == "airport":
                return clock.moment(row, observation).isoformat()
        return row["posted_at"]
    rows.sort(key=order)
