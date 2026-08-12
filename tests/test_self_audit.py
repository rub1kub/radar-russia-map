"""Самоаудит: что попадает в ночной отчёт владельцу.

    ingest/.venv/bin/python -m pytest tests/test_self_audit.py -q

Отчёт ловит тяжёлые события на одном-двух источниках: фейк из кривого
разбора обычно живёт на одном сообщении, массово подтверждённое —
почти наверняка настоящее.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.db import connect
from scripts.self_audit import MAX_SOURCES, report, suspicious


@pytest.fixture()
def db(tmp_path):
    connection = connect(tmp_path / "radar.db")
    connection.execute(
        "INSERT INTO zones (id, level, name_ru, lat, lon)"
        " VALUES ('z1','district','Тестовск',50,40)")
    return connection


def event(connection, eid, signal, sources, when="datetime('now','-2 hours')"):
    connection.execute(
        f"INSERT INTO events (id, first_seen_at, last_seen_at, status,"
        f" signal_type, threat_type, severity, confidence, source_count,"
        f" zone_id, zone_path)"
        f" VALUES (?, {when}, {when}, 'active', ?, 'uav', 8, 0.5, ?,"
        f" 'z1', '[\"z1\"]')",
        (eid, signal, sources))
    connection.execute(
        "INSERT INTO raw_messages (source_key, message_id, posted_at, text)"
        " VALUES ('ch', 1, datetime('now'), 'Тестовск сбитие БПЛА')")
    raw_id = connection.execute("SELECT MAX(id) m FROM raw_messages").fetchone()["m"]
    connection.execute(
        "INSERT INTO event_sources (event_id, raw_message_id, source_key,"
        " contributed_at, role) VALUES (?,?,?,datetime('now'),'first')",
        (eid, raw_id, "ch"))
    connection.commit()


def test_lone_intercept_is_suspicious(db):
    event(db, "e1", "intercept", 1)
    items = suspicious(db)
    assert len(items) == 1
    assert items[0]["zone"] == "Тестовск"
    assert "сбитие" in items[0]["text"]


def test_well_confirmed_event_is_not(db):
    """Восемь голосов — не кандидат в фейки, владельца не дёргаем."""
    event(db, "e1", "intercept", MAX_SOURCES + 6)
    assert suspicious(db) == []


def test_alarm_is_not_reported(db):
    """Тревоги и опасности в отчёт не идут: аудит — про тяжёлые значки."""
    event(db, "e1", "alarm", 1)
    assert suspicious(db) == []


def test_old_event_is_not_reported(db):
    event(db, "e1", "intercept", 1, when="datetime('now','-3 days')")
    assert suspicious(db) == []


def test_report_mentions_zone_and_text(db):
    event(db, "e1", "impact", 2)
    text = report(suspicious(db))
    assert "Тестовск" in text and "Удар" in text and "сбитие БПЛА" in text
