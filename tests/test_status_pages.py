"""Тесты статусных страниц: аэропорты, мост, воронка бота."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from scripts import status_pages


def test_deeplink_payload_matches_bot_implementation():
    """Формула payload в генераторе и в боте обязана совпадать.

    Функция продублирована намеренно (генератор ходит без fastapi), и
    единственная защита от расхождения — этот тест.
    """
    from api import telegram
    for zone_id in ("kurskaya_oblast",
                    "kerch_leninskiy_rayon_respublika_krym",
                    "x" * 70, "y" * 130):
        assert (status_pages.zone_start_payload(zone_id)
                == telegram.zone_start_payload(zone_id))
        # Telegram пускает в start-payload максимум 64 знака.
        assert len(status_pages.zone_start_payload(zone_id)) <= 64


def test_airport_registry_zones_exist():
    """Каждая зона реестра аэропортов должна существовать в справочнике."""
    from pipeline.db import connect
    connection = connect()
    if connection.execute("SELECT COUNT(*) FROM zones").fetchone()[0] == 0:
        pytest.skip("справочник не построен")
    known = {row[0] for row in connection.execute("SELECT id FROM zones")}
    for name, _, _, zone_ids in status_pages.AIRPORTS:
        for zone_id in zone_ids:
            assert zone_id in known, f"{name}: зоны {zone_id} нет"


def _bridge_db(tmp_path, messages):
    connection = sqlite3.connect(tmp_path / "bridge.db")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE raw_messages (posted_at TEXT, text TEXT)")
    connection.executemany(
        "INSERT INTO raw_messages VALUES (?, ?)", messages)
    connection.commit()
    return connection


def test_bridge_timeline_pairs_closures_with_reopenings(tmp_path):
    from pipeline.timeutil import now_utc
    from datetime import timedelta
    base = now_utc() - timedelta(days=1)
    stamp = lambda minutes: (base + timedelta(minutes=minutes)).isoformat()
    connection = _bridge_db(tmp_path, [
        (stamp(0), "Движение автотранспорта по Крымскому мосту "
                   "временно перекрыто."),
        # Пересказ той же новости вторым каналом — не второе перекрытие.
        (stamp(1), "Крымский мост закрыт"),
        (stamp(45), "Движение автотранспорта по Крымскому мосту "
                    "возобновлено."),
        # Сообщение без моста не участвует.
        (stamp(50), "Движение по улице Ленина перекрыто"),
    ])
    steps = status_pages.bridge_timeline(connection)
    assert [step["state"] for step in steps] == ["closed", "open"]


def test_bridge_page_reports_current_state(tmp_path):
    from pipeline.timeutil import now_utc
    from datetime import timedelta
    base = now_utc() - timedelta(hours=2)
    connection = _bridge_db(tmp_path, [
        (base.isoformat(), "Движение автотранспорта по Крымскому мосту "
                           "временно перекрыто."),
    ])
    html = status_pages.bridge_page(connection, "тест")
    assert "перекрыт" in html
    assert "Движение перекрыто" in html


def test_airport_card_shows_closed_state():
    row = {
        "name": "Сочи (Адлер)", "city": "Сочи",
        "region_slug": "krasnodarskiy-kray",
        "zone_ids": ("sochi_krasnodarskiy_kray",),
        "closed": True, "since": "2026-08-19T10:00:00+00:00",
        "reopened": None, "closures": 5, "avg_minutes": 44,
    }
    card = status_pages.airport_card(row)
    assert "закрыт — действуют ограничения" in card
    assert "5 закрытий" in card
    assert "44 мин" in card
    assert 'class="closed"' in card


def test_minutes_word_switches_to_hours():
    assert status_pages.minutes_word(44) == "44 мин"
    assert "ч" in status_pages.minutes_word(180)
