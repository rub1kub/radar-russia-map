"""Нейтральное наблюдение не создаёт угрозу и не отменяет предупреждение."""

from datetime import datetime, timedelta, timezone

import pytest

from pipeline.db import connect
from pipeline.geocode import Geocoder
from pipeline.incremental import run_once
from pipeline.parse import parse
from pipeline.rebuild import rebuild


@pytest.mark.parametrize("text", [
    "Краснодар Вертолет в небе🟢",
    "Краснодар Вертолёт в небе 🟢",
    "Краснодар\nВертолет в небе🟢",
    "Краснодар\nВ НЕБЕ ВЕРТОЛЁТ 🟢",
    "Вертолёт в небе.",
    "Вертолёты в небе 🟢",
    "Краснодар Вертолет в небе🟢\nРадар Юга | Подписаться",
])
def test_neutral_helicopter_report_does_not_create_alert_or_clear(text):
    observation = parse(text)
    assert not observation.relevant
    assert observation.signal_type not in {"allclear", "retracted"}


def test_green_emoji_alone_does_not_cancel_independent_civil_warning():
    observation = parse("Объявлена воздушная тревога. Вертолёт в небе 🟢")
    assert observation.relevant
    assert observation.signal_type == "alarm"


def test_explicit_civil_allclear_is_not_replaced_by_neutral_phrase():
    observation = parse("Отбой воздушной тревоги. Вертолёт в небе 🟢")
    assert observation.relevant
    assert observation.signal_type == "allclear"


def test_neutral_reposts_neither_create_event_nor_resolve_existing_warning(tmp_path):
    connection = connect(tmp_path / "test.db")
    connection.execute("INSERT INTO zones(id,level,name_ru) VALUES ('test','region','Тестовая область')")
    connection.execute("INSERT INTO zone_names VALUES ('тестовая область','test','primary')")
    base = datetime(2026, 10, 26, 10, tzinfo=timezone.utc)
    connection.execute(
        "INSERT INTO raw_messages(source_key,message_id,posted_at,text) VALUES ('one',1,?,?)",
        (base.isoformat(), "Тестовая область. Объявлена воздушная тревога."))
    for index, source in enumerate(("one", "two", "three", "four"), 2):
        connection.execute(
            "INSERT INTO raw_messages(source_key,message_id,posted_at,text) VALUES (?,?,?,?)",
            (source, index, (base + timedelta(minutes=1)).isoformat(),
             "Тестовая область Вертолет в небе🟢"))
    connection.commit()
    run_once(connection, geocoder=Geocoder(connection), now=base + timedelta(minutes=2), grace=timedelta(0))
    events = connection.execute("SELECT signal_type,resolved_at FROM events").fetchall()
    assert [tuple(row) for row in events] == [("alarm", None)]
    assert connection.execute("SELECT COUNT(*) FROM event_sources").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0] == 5

    rebuild(connection)
    assert [tuple(row) for row in connection.execute("SELECT signal_type,resolved_at FROM events")] == [("alarm", None)]
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
