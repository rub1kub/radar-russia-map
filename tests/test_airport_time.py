"""Время и смысл объявлений о работе гражданских аэропортов."""

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from api.notify_throttle import is_stale
from pipeline.airport_time import AirportNoticeTimes
from pipeline.db import connect
from pipeline.parse import parse

BASE = datetime(2026, 10, 25, 2, 28, 45, tzinfo=timezone.utc)
OPENED = (
    "⬜️Аэропорты\n\n— ГЕЛЕНДЖИК\n\n— КРАСНОДАР (Пашковский)\n\n"
    "✈️СНЯТЫ дополнительные временные ограничения* на прием и выпуск "
    "воздушных судов.\n\n"
    "✈️Ограничения вводили для обеспечения безопасности полетов.\n\n"
    "*Согласно действующим NOTAM, аэропорт Геленджик принимает регулярные "
    "рейсы с 8:30 до 20:00, аэропорт Краснодар — с с 9:30 до 23:00\n"
    "✈️Говорит Росавиация | MAX")


@pytest.mark.parametrize("text", [
    OPENED,
    OPENED.replace("СНЯТЫ дополнительные временные", "СНЯТЫ дополнительные* временные"),
    OPENED.replace("СНЯТЫ дополнительные временные", "СНЯТЫ ранее введенные временные"),
    "Внимание! " + OPENED,
])
def test_additional_restrictions_lifted_means_open(text):
    observation = parse(text)
    assert observation.relevant
    assert (observation.signal_type, observation.threat_type) == ("allclear", "airport")


@pytest.mark.parametrize("text", [
    "Аэропорт Краснодар будет открыт завтра.",
    "Аэропорт Краснодар может быть открыт утром.",
])
def test_future_opening_is_not_a_current_status(text):
    assert not parse(text).relevant


@pytest.mark.parametrize("text", [
    "Аэропорт Краснодар закрыт. Ограничения будут сняты в 15:00.",
    "Аэропорт Краснодар: не сняты дополнительные временные ограничения.",
])
def test_negation_and_future_opening_do_not_open_a_closed_airport(text):
    observation = parse(text)
    assert observation.relevant
    assert (observation.signal_type, observation.threat_type) == ("infra", "airport")


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "radar.db")
    yield connection
    connection.close()


def post(db, source="favt_info", moment=BASE, text=OPENED, forwarded_at=None):
    cursor = db.execute(
        "INSERT INTO raw_messages (source_key, message_id, posted_at, text, forwarded_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (source, db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0] + 1,
         moment.isoformat(), text, forwarded_at))
    return db.execute("SELECT * FROM raw_messages WHERE id = ?", (cursor.lastrowid,)).fetchone()


def test_seven_hour_old_reprint_keeps_regulator_time_and_is_not_live(db):
    post(db)
    later = BASE + timedelta(hours=7)
    row = post(db, source="regional", moment=later,
               text=OPENED.replace("\n\n", "\n"))
    moment = AirportNoticeTimes(db, later).moment(row, parse(row["text"]))
    assert moment == BASE
    assert is_stale({"status": "resolved", "resolved_at": moment.isoformat()},
                    int(later.timestamp()))


def test_forwarded_time_is_kept_without_original_channel_in_database(db):
    later = BASE + timedelta(hours=7)
    row = post(db, source="regional", moment=later, forwarded_at=BASE.isoformat())
    assert AirportNoticeTimes(db).moment(row, parse(OPENED)) == BASE


@pytest.mark.parametrize("forwarded_at", ["not-a-date", (BASE + timedelta(days=1)).isoformat()])
def test_invalid_or_future_forward_time_does_not_backdate_notice(db, forwarded_at):
    row = post(db, source="regional", forwarded_at=forwarded_at)
    assert AirportNoticeTimes(db).moment(row, parse(OPENED)) == BASE


def test_regulator_can_publish_same_template_again(db):
    post(db)
    later = BASE + timedelta(hours=7)
    row = post(db, moment=later)
    assert AirportNoticeTimes(db).moment(row, parse(OPENED)) == later


def test_reprint_matches_latest_preceding_official_publication(db):
    post(db, moment=BASE - timedelta(hours=1))
    post(db)
    post(db, moment=BASE + timedelta(hours=12))
    row = post(db, source="regional", moment=BASE + timedelta(hours=7))
    assert AirportNoticeTimes(db).moment(row, parse(OPENED)) == BASE


def test_template_from_previous_days_is_not_assumed_to_be_same_notice(db):
    post(db)
    later = BASE + timedelta(days=2)
    row = post(db, source="regional", moment=later)
    assert AirportNoticeTimes(db).moment(row, parse(OPENED)) == later


def test_other_signals_keep_their_existing_time_rules(db):
    row = post(db, source="regional", forwarded_at=(BASE - timedelta(hours=7)).isoformat())
    assert AirportNoticeTimes(db).moment(row, SimpleNamespace(threat_type="unknown")) == BASE


def test_forward_time_migration_keeps_existing_raw_messages(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as legacy:
        legacy.execute(
            "CREATE TABLE raw_messages (id INTEGER PRIMARY KEY, source_key TEXT NOT NULL,"
            " chat_id INTEGER, message_id INTEGER NOT NULL, posted_at TEXT NOT NULL,"
            " received_at TEXT, text TEXT NOT NULL, views INTEGER, UNIQUE(source_key,message_id))")
        legacy.execute(
            "INSERT INTO raw_messages (source_key,message_id,posted_at,text) VALUES (?,?,?,?)",
            ("favt_info", 1, BASE.isoformat(), OPENED))
    connection = connect(path)
    row = connection.execute("SELECT text, forwarded_at FROM raw_messages").fetchone()
    assert row["text"] == OPENED
    assert row["forwarded_at"] is None
    connection.close()
