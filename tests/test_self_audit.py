"""Самоаудит: что попадает в ночной отчёт владельцу.

    ingest/.venv/bin/python -m pytest tests/test_self_audit.py -q

Отчёт отбирает ограниченную выборку для ручной проверки. Число источников
не доказывает истинность или ошибочность сообщения.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.db import connect
from scripts.self_audit import MAX_SOURCES, mass_clears, report, report_chunks, summary, suspicious


@pytest.fixture()
def db(tmp_path):
    connection = connect(tmp_path / "radar.db")
    connection.execute(
        "INSERT INTO zones (id, level, name_ru, lat, lon)"
        " VALUES ('z1','district','Тестовск',50,40)")
    return connection


def event(connection, eid, signal, sources, when="datetime('now','-2 hours')",
          text="Тестовск сбитие БПЛА"):
    connection.execute(
        f"INSERT INTO events (id, first_seen_at, last_seen_at, status,"
        f" signal_type, threat_type, severity, confidence, source_count,"
        f" zone_id, zone_path)"
        f" VALUES (?, {when}, {when}, 'active', ?, 'uav', 8, 0.5, ?,"
        f" 'z1', '[\"z1\"]')",
        (eid, signal, sources))
    connection.execute(
        "INSERT INTO raw_messages (source_key, message_id, posted_at, text)"
        " VALUES ('ch', ?, datetime('now'), ?)",
        (connection.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0] + 1, text))
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


def test_low_confidence_civil_alarm_is_reported(db):
    event(db, "e1", "alarm", 1, text="Тестовск объявлена воздушная тревога")
    assert suspicious(db)[0]["signal"] == "alarm"


def test_regulator_confidence_does_not_flood_civil_review(db):
    event(db, "e1", "infra", 1, text="Аэропорт Тестовск закрыт")
    db.execute("UPDATE events SET confidence=0.72")
    assert suspicious(db) == []


def test_old_event_is_not_reported(db):
    event(db, "e1", "intercept", 1, when="datetime('now','-3 days')")
    assert suspicious(db) == []


def test_report_mentions_zone_and_text(db):
    event(db, "e1", "intercept", 2)
    text = report(suspicious(db))
    assert "Тестовск" in text and "Перехват" in text and "сбитие БПЛА" in text


def test_report_shows_the_message_that_set_the_signal(db):
    """Событие открылось тревогой, перехват принесло второе сообщение — в
    отчёте должен стоять его текст, иначе честное событие выглядит фейком
    (так случилось с Алчевском: «тревога БПЛА» при тяжёлом значке)."""
    # Первое сообщение — только тревога, перехват приносит второе.
    event(db, "e1", "intercept", 2, text="Тестовск тревога по БПЛА")
    db.execute(
        "INSERT INTO raw_messages (source_key, message_id, posted_at, text)"
        " VALUES ('ch2', 2, datetime('now'), 'Тестовск работает ПВО по БПЛА')")
    raw_id = db.execute("SELECT MAX(id) m FROM raw_messages").fetchone()["m"]
    db.execute(
        "INSERT INTO event_sources (event_id, raw_message_id, source_key,"
        " contributed_at, role) VALUES ('e1',?,?,datetime('now','+1 minute'),'confirm')",
        (raw_id, "ch2"))
    db.commit()

    items = suspicious(db)
    assert "работает ПВО" in items[0]["text"]


def test_window_is_true_twenty_four_hours_and_times_are_moscow(db):
    now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
    for eid, hours in (("old", 36), ("recent", 2), ("future", -1)):
        at = (now - timedelta(hours=hours)).isoformat()
        event(db, eid, "alarm", 1, when=f"'{at}'", text="Объявлена воздушная тревога")
    items = suspicious(db, now=now)
    assert len(items) == 1
    assert items[0]["at"] == "13:00"
    assert summary(db, now=now) == {"alarm": {"events": 1, "candidates": 1}}


def test_repeated_zones_do_not_hide_other_source_messages(db):
    event(db, "e0", "danger", 1, text="Тестовск Опасность по БПЛА")
    shared = db.execute("SELECT MIN(id) FROM raw_messages").fetchone()[0]
    for index in range(1, 15):
        eid = f"e{index}"
        event(db, eid, "danger", 1, text="Тестовск Опасность по БПЛА")
        db.execute("UPDATE event_sources SET raw_message_id=? WHERE event_id=?", (shared, eid))
        db.commit()
    event(db, "older", "danger", 1, when="datetime('now','-3 hours')",
          text="Другое объявление опасности")
    items = suspicious(db)
    assert len(items) == 2
    assert len({item["raw_id"] for item in items}) == 2
    assert any("Другое" in item["text"] for item in items)


def test_mass_clear_window_and_moscow_time(db):
    now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
    for index in range(3):
        event(db, f"e{index}", "alarm", 1, when=f"'{now.isoformat()}'")
    for index, hours in enumerate((36, 1), 100):
        at = (now - timedelta(hours=hours)).isoformat()
        cursor = db.execute(
            "INSERT INTO raw_messages(source_key,message_id,posted_at,text) VALUES ('clear',?,?,?)",
            (index, at, "Отбой воздушной тревоги"))
        for event_id in ("e0", "e1", "e2"):
            db.execute("INSERT INTO event_sources VALUES (?,?,?,?,'resolve')",
                       (event_id, cursor.lastrowid, "clear", at))
    db.commit()
    clears = mass_clears(db, now=now)
    assert len(clears) == 1
    assert clears[0]["at"] == "14:00"
    assert clears[0]["closed"] == 3


def test_report_escapes_source_html_and_chunks_without_cutting_tags():
    items = [{"zone": "<Тест&>", "signal": "alarm", "at": "13:00", "sources": 1,
              "text": "<b>" + "&" * 150 + "</b>"} for _ in range(12)]
    text = report(items)
    assert "&lt;Тест&amp;&gt;" in text
    assert "<b><Тест" not in text
    assert "кандидаты" in text
    chunks = report_chunks(text)
    assert len(chunks) > 1
    assert "\n".join(chunks) == text
    assert all(len(chunk) <= 3800 for chunk in chunks)
    assert all(chunk.count("<i>") == chunk.count("</i>") for chunk in chunks)
