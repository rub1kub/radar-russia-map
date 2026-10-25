"""Охват опубликованной истории, сохраняемый до удаления провенанса."""

from __future__ import annotations

import sqlite3


def remember_day_sources(connection: sqlite3.Connection) -> int:
    before = connection.total_changes
    connection.execute(
        """INSERT OR IGNORE INTO history_day_sources (day, source_key)
           SELECT DISTINCT date(e.first_seen_at, '+3 hours'), s.source_key
           FROM events e JOIN event_sources s ON s.event_id = e.id
           WHERE date(e.first_seen_at, '+3 hours') IS NOT NULL""")
    return connection.total_changes - before
