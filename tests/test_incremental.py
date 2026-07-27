"""Тесты инкрементального прохода.

    ingest/.venv/bin/python -m pytest tests/test_incremental.py -q

Проверяется не разбор текста (это tests/test_pipeline.py), а поведение вокруг
базы: отметка о прогрессе, восстановление открытых событий и то, что проход
ничего не удаляет. Все тесты работают на своей временной базе с крошечным
справочником — боевая ingest/data/radar.db не трогается.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pipeline.db import connect
from pipeline.geocode import Geocoder
from pipeline.incremental import (
    LATE_GRACE,
    WATERMARK_KEY,
    load_open_events,
    read_watermark,
    run_once,
)
from pipeline.textnorm import name_variants

BASE = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)

# Два федеральных канала дают разную достоверность на одном и на двух
# подтверждениях — на этом ловится потеря sources при восстановлении.
TIERS = {"alpha": "federal", "beta": "federal", "gamma": "regional"}

ZONES = [
    ("rostov_oblast", None, "region", "Ростовская область", 47.2, 39.7, 4_200_000),
    ("azovskiy_rayon", "rostov_oblast", "district", "Азовский район", 47.1, 39.4, 90_000),
]

DANGER = "Азовский район\nОпасность по БПЛА"
ALLCLEAR = "Азовский район\nОтбой опасности по БПЛА"


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "radar.db")
    for zone_id, parent, level, name, lat, lon, population in ZONES:
        connection.execute(
            "INSERT INTO zones (id, parent_id, level, name_ru, lat, lon, population)"
            " VALUES (?,?,?,?,?,?,?)",
            (zone_id, parent, level, name, lat, lon, population),
        )
        for variant in name_variants(name, level):
            connection.execute(
                "INSERT OR IGNORE INTO zone_names (norm, zone_id, kind) VALUES (?,?,?)",
                (variant, zone_id, "variant"),
            )
    connection.commit()
    return connection


@pytest.fixture
def geocoder(db):
    return Geocoder(db)


def post(connection, message_id: int, source: str, minute: int, text: str = DANGER,
         second: int = 0) -> None:
    moment = BASE + timedelta(minutes=minute, seconds=second)
    connection.execute(
        "INSERT INTO raw_messages (source_key, chat_id, message_id, posted_at, text)"
        " VALUES (?,?,?,?,?)",
        (source, 1, message_id, moment.isoformat(), text),
    )
    connection.commit()


def sweep(connection, geocoder, minute: int, second: int = 0, grace=LATE_GRACE) -> dict:
    """Проход «в момент» minute:second от BASE.

    Момент задается явно, потому что от него зависит и старение событий, и
    граница придержанного хвоста: сообщение разбирается не раньше, чем через
    LATE_GRACE после публикации.
    """
    return run_once(connection, geocoder=geocoder, tiers=TIERS, grace=grace,
                    now=BASE + timedelta(minutes=minute, seconds=second))


def rows(connection, sql: str) -> list:
    return connection.execute(sql).fetchall()


def test_second_pass_without_new_messages_changes_nothing(db, geocoder):
    """Идемпотентность: повторный проход не плодит события и провенанс."""
    post(db, 1, "alpha", 0)
    first = sweep(db, geocoder, 3)
    assert first["scanned"] == 1
    assert first["new_events"] == 1

    before = (rows(db, "SELECT COUNT(*) n FROM events")[0]["n"],
              rows(db, "SELECT COUNT(*) n FROM event_sources")[0]["n"])

    second = sweep(db, geocoder, 4)
    assert second["scanned"] == 0
    assert second["new_events"] == 0

    after = (rows(db, "SELECT COUNT(*) n FROM events")[0]["n"],
             rows(db, "SELECT COUNT(*) n FROM event_sources")[0]["n"])
    assert after == before
    assert read_watermark(db) == 1


def test_new_message_joins_event_from_previous_pass(db, geocoder):
    """Второй источник в окне слияния подтверждает событие, а не создает свое."""
    post(db, 1, "alpha", 0)
    sweep(db, geocoder, 3)
    alone = rows(db, "SELECT confidence FROM events")[0]["confidence"]

    post(db, 2, "beta", 3)
    stats = sweep(db, geocoder, 6)

    assert stats["restored_open"] == 1
    assert stats["new_events"] == 0

    events = rows(db, "SELECT id, source_count, confidence FROM events")
    assert len(events) == 1
    assert events[0]["source_count"] == 2
    assert events[0]["confidence"] > alone

    keys = rows(db, "SELECT DISTINCT source_key FROM event_sources ORDER BY source_key")
    assert [row["source_key"] for row in keys] == ["alpha", "beta"]


def test_restored_event_keeps_all_sources(db, geocoder):
    """Восстановление возвращает столько источников, сколько в source_count."""
    post(db, 1, "alpha", 0)
    post(db, 2, "beta", 2)
    sweep(db, geocoder, 5)

    stored = rows(db, "SELECT id, source_count, confidence FROM events")[0]
    assert stored["source_count"] > 1

    restored = load_open_events(db, TIERS)
    assert len(restored) == 1
    assert len(restored[0].sources) == stored["source_count"]
    assert restored[0].confidence == stored["confidence"]
    assert set(restored[0].sources.values()) == {"federal"}


def test_allclear_closes_event_restored_from_db(db, geocoder):
    """Отбой в следующем проходе закрывает уже записанное событие."""
    post(db, 1, "alpha", 0)
    sweep(db, geocoder, 3)

    post(db, 2, "beta", 4, text=ALLCLEAR)
    stats = sweep(db, geocoder, 7)

    assert stats["new_events"] == 0
    event = rows(db, "SELECT status, resolved_at FROM events")[0]
    assert event["resolved_at"] is not None
    assert event["status"] == "resolved"
    assert not load_open_events(db, TIERS)


def test_pass_never_deletes_earlier_events(db, geocoder):
    """Проход дописывает, а не пересобирает: старое событие остается на месте."""
    post(db, 1, "alpha", 0)
    sweep(db, geocoder, 3)
    old_id = rows(db, "SELECT id FROM events")[0]["id"]

    # Уводим событие далеко в прошлое, чтобы оно закрылось и выпало из окна.
    post(db, 2, "gamma", 600)
    sweep(db, geocoder, 603)

    ids = {row["id"] for row in rows(db, "SELECT id FROM events")}
    assert old_id in ids
    assert len(ids) == 2

    old = db.execute("SELECT status FROM events WHERE id = ?", (old_id,)).fetchone()
    assert old["status"] == "resolved"


def test_missing_watermark_is_seeded_from_provenance(db, geocoder):
    """Первый проход поверх базы от rebuild не разбирает историю заново."""
    post(db, 1, "alpha", 0)
    post(db, 2, "beta", 2)
    sweep(db, geocoder, 5)
    before = rows(db, "SELECT COUNT(*) n FROM events")[0]["n"]

    db.execute("DELETE FROM pipeline_state WHERE key = ?", (WATERMARK_KEY,))
    db.commit()
    assert read_watermark(db) == 2

    stats = sweep(db, geocoder, 6)
    assert stats["scanned"] == 0
    assert rows(db, "SELECT COUNT(*) n FROM events")[0]["n"] == before


def test_late_message_still_confirms_instead_of_splitting(db, geocoder):
    """Сообщение, опоздавшее к слушателю, подтверждает событие, а не двоит его.

    Каналы приходят не в порядке публикации: на живой выборке сосед по id
    оказывался старше предыдущего на 1-4 секунды. Fuser не присоединяет
    наблюдение старше last_seen, поэтому без придержанного хвоста такая пара
    разбиралась в разных проходах и давала два события вместо одного.
    """
    # Окно задается явно: тест про поведение, а не про подобранное значение
    # LATE_GRACE, и не должен ломаться при его настройке.
    grace = timedelta(minutes=2)

    # alpha опубликован секундой позже, но пришел к слушателю первым.
    post(db, 1, "alpha", 10, second=1)
    early = sweep(db, geocoder, 10, second=5, grace=grace)
    assert early["scanned"] == 0, "свежий хвост должен быть придержан"
    assert early["held"] == 1

    post(db, 2, "beta", 10, second=0)
    settled = sweep(db, geocoder, 13, grace=grace)
    assert settled["scanned"] == 2
    assert settled["held"] == 0

    events = rows(db, "SELECT id, source_count FROM events")
    assert len(events) == 1, "опоздавшее сообщение не должно создавать второе событие"
    assert events[0]["source_count"] == 2


def test_without_grace_late_message_splits_event(db, geocoder):
    """Тот же сценарий без задержки разваливается — ради этого она и введена."""
    post(db, 1, "alpha", 10, second=1)
    sweep(db, geocoder, 10, second=5, grace=timedelta(0))
    post(db, 2, "beta", 10, second=0)
    sweep(db, geocoder, 10, second=6, grace=timedelta(0))

    assert len(rows(db, "SELECT id FROM events")) == 2


def test_watermark_stops_before_held_tail(db, geocoder):
    """Отметка встает перед придержанным сообщением, а не на максимум id.

    Иначе сообщение с меньшим posted_at, но большим id, оказалось бы позади
    отметки и не разобралось бы никогда.
    """
    grace = timedelta(minutes=2)
    post(db, 1, "alpha", 0)
    post(db, 2, "beta", 20)

    stats = sweep(db, geocoder, 21, grace=grace)
    assert stats["scanned"] == 1
    assert stats["held"] == 1
    assert read_watermark(db) == 1

    later = sweep(db, geocoder, 23, grace=grace)
    assert later["scanned"] == 1
    assert read_watermark(db) == 2


def test_stale_watermark_below_provenance_is_ignored(db, geocoder):
    """Отметка, отставшая от провенанса, не заставляет разбирать хвост заново.

    reset_derived() не чистит pipeline_state, поэтому после rebuild в базе
    остается старая отметка. Разбор уже учтенных сообщений плодил бы дубли.
    """
    post(db, 1, "alpha", 0)
    post(db, 2, "beta", 2)
    sweep(db, geocoder, 5)
    before = rows(db, "SELECT COUNT(*) n FROM events")[0]["n"]

    db.execute("UPDATE pipeline_state SET value = '0' WHERE key = ?", (WATERMARK_KEY,))
    db.commit()
    assert read_watermark(db) == 2

    stats = sweep(db, geocoder, 6)
    assert stats["scanned"] == 0
    assert rows(db, "SELECT COUNT(*) n FROM events")[0]["n"] == before
