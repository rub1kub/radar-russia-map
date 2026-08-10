"""Срок хранения: корпус и журнал бота.

    ingest/.venv/bin/python -m pytest tests/test_retention.py -q

Журнал бота живёт в таблице, которую создаёт api/telegram.py при первом
обращении, — в базе конвейера её может не быть вовсе. Чистка обязана это
переживать, иначе ежедневный таймер падает на машине, где бот не поднят.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.db import connect
from pipeline.retention import (
    activity_stats,
    has_table,
    purge_activity,
)
from pipeline.timeutil import now_utc

ACTIVITY_SCHEMA = """
CREATE TABLE tg_activity (
    chat_id INTEGER NOT NULL,
    kind    TEXT NOT NULL,
    at      TEXT NOT NULL
);
"""


@pytest.fixture()
def db(tmp_path):
    return connect(tmp_path / "radar.db")


def log(connection, days_ago: float, kind: str = "start") -> None:
    stamp = (now_utc() - timedelta(days=days_ago)).isoformat()
    connection.execute(
        "INSERT INTO tg_activity (chat_id, kind, at) VALUES (?,?,?)",
        (1084693264, kind, stamp))
    connection.commit()


def test_missing_table_is_not_an_error(db):
    """База конвейера без бота — журнала нет, и это нормально."""
    assert has_table(db, "tg_activity") is False
    assert activity_stats(db, now_utc().isoformat()) == {
        "activity_total": 0, "activity_older": 0}
    assert purge_activity(db, now_utc().isoformat()) == 0


def test_old_entries_go_fresh_ones_stay(db):
    db.executescript(ACTIVITY_SCHEMA)
    log(db, 120)
    log(db, 91)
    log(db, 89)
    log(db, 0.5, "webapp")

    cutoff = (now_utc() - timedelta(days=90)).isoformat()
    assert activity_stats(db, cutoff) == {"activity_total": 4, "activity_older": 2}

    assert purge_activity(db, cutoff) == 2
    left = db.execute("SELECT COUNT(*) n FROM tg_activity").fetchone()["n"]
    assert left == 2


def test_purge_is_idempotent(db):
    db.executescript(ACTIVITY_SCHEMA)
    log(db, 120)
    cutoff = (now_utc() - timedelta(days=90)).isoformat()

    assert purge_activity(db, cutoff) == 1
    assert purge_activity(db, cutoff) == 0
