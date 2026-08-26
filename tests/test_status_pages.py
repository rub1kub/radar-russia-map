"""Тесты статусных страниц: аэропорты по ленте Росавиации, мост, воронка."""
from __future__ import annotations

import sqlite3
import sys
from datetime import timedelta
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
    for key, name, _, _, zone_ids in status_pages.AIRPORTS:
        for zone_id in zone_ids:
            assert zone_id in known, f"{name}: зоны {zone_id} нет"


@pytest.mark.parametrize("header,expected", [
    ("🔻Аэропорт ЯРОСЛАВЛЬ (Туношна)\n", ["ЯРОСЛАВЛЬ"]),
    ("⬜️ Аэропорты\n\n— ГЕЛЕНДЖИК\n\n— КРАСНОДАР (Пашковский)\n",
     ["ГЕЛЕНДЖИК", "КРАСНОДАР"]),
    # Регулятор теряет пробел и скобку: «НИЖНЕКАМСК(Бегишево».
    ("Аэропорты\n\n✅ БУГУЛЬМА\n\n✅ НИЖНЕКАМСК(Бегишево\n",
     ["БУГУЛЬМА", "НИЖНЕКАМСК"]),
    # И делает опечатки: «ЖУКОВСКЙ» прижимается к известному имени.
    ("🔻Аэропорты \n\nДОМОДЕДОВО\n\nЖУКОВСКЙ\n",
     ["ДОМОДЕДОВО", "ЖУКОВСКИЙ"]),
    ("▫️ГЕЛЕНДЖИК\n", ["ГЕЛЕНДЖИК"]),
    ("Аэропорты\n\n🔻ВНУКОВО\n🔻НИЖНИЙ НОВГОРОД (Стригино)\n",
     ["ВНУКОВО", "НИЖНИЙ НОВГОРОД"]),
])
def test_favt_airport_names(header, expected):
    assert status_pages.favt_airport_names(header) == expected


def _favt_db(tmp_path, messages):
    connection = sqlite3.connect(tmp_path / "favt.db")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE raw_messages (source_key TEXT, posted_at TEXT, "
        "text TEXT)")
    connection.executemany(
        "INSERT INTO raw_messages VALUES (?, ?, ?)", messages)
    connection.commit()
    return connection


def test_airport_rows_pair_official_messages(tmp_path):
    """Пара «ВВЕДЕНЫ — СНЯТЫ» даёт закрытие с длительностью."""
    from pipeline.timeutil import now_utc
    base = now_utc() - timedelta(hours=6)
    stamp = lambda minutes: (base + timedelta(minutes=minutes)).isoformat()
    closed_text = ("🔻Аэропорт СОЧИ\n\n🚫 ВВЕДЕНЫ временные ограничения "
                   "на прием и выпуск воздушных судов.")
    open_text = ("⬜️ Аэропорт СОЧИ\n\n✈️СНЯТЫ ограничения на прием и "
                 "выпуск воздушных судов.")
    connection = _favt_db(tmp_path, [
        ("favt_info", stamp(0), closed_text),
        # Тот же канал под вторым ключом источника — дубль, не событие.
        ("ch1938794947", stamp(0), closed_text),
        ("favt_info", stamp(90), open_text),
        ("ch1938794947", stamp(90), open_text),
    ])
    rows = {row["key"]: row for row in status_pages.airport_rows(connection)}
    sochi = rows["СОЧИ"]
    assert sochi["closed"] is False
    assert sochi["closures"] == 1
    assert sochi["median_minutes"] == 90
    assert sochi["reopened"] == stamp(90)


def test_airport_rows_current_closure(tmp_path):
    from pipeline.timeutil import now_utc
    base = now_utc() - timedelta(hours=2)
    connection = _favt_db(tmp_path, [
        ("favt_info", base.isoformat(),
         "🔻Аэропорт КАЗАНЬ\n\n🚫 ВВЕДЕНЫ временные ограничения "
         "на прием и выпуск воздушных судов."),
    ])
    rows = {row["key"]: row for row in status_pages.airport_rows(connection)}
    assert rows["КАЗАНЬ"]["closed"] is True
    assert rows["КАЗАНЬ"]["since"] == base.isoformat()
    # Закрытые — первыми в списке.
    assert status_pages.airport_rows(connection)[0]["key"] == "КАЗАНЬ"


def test_airport_stale_closure_is_not_shown_closed(tmp_path):
    """Закрытие без снятия старше суток — пропущенное сообщение, не сутки
    без полётов."""
    from pipeline.timeutil import now_utc
    old = now_utc() - timedelta(hours=40)
    connection = _favt_db(tmp_path, [
        ("favt_info", old.isoformat(),
         "🔻Аэропорт ПЕНЗА\n\n🚫 ВВЕДЕНЫ временные ограничения "
         "на прием и выпуск воздушных судов."),
    ])
    rows = {row["key"]: row for row in status_pages.airport_rows(connection)}
    assert rows["ПЕНЗА"]["closed"] is False


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
    base = now_utc() - timedelta(hours=2)
    connection = _bridge_db(tmp_path, [
        (base.isoformat(), "Движение автотранспорта по Крымскому мосту "
                           "временно перекрыто."),
    ])
    html = status_pages.bridge_page(connection, "тест")
    assert (
        "<title>Крымский мост сейчас — открыт или закрыт, "
        "обстановка онлайн</title>" in html
    )
    assert "Движение перекрыто" in html


def test_airport_card_shows_closed_state():
    row = {
        "key": "СОЧИ", "name": "Сочи (Адлер)", "city": "Сочи",
        "region_slug": "krasnodarskiy-kray",
        "zone_ids": ("sochi_krasnodarskiy_kray",),
        "closed": True,
        "since": "2026-08-19T10:00:00+00:00",
        "reopened": None, "closures": 5, "median_minutes": 44,
    }
    card = status_pages.airport_card(row)
    assert "Закрыт" in card
    assert "44 мин" in card
    assert 'class="closed"' in card
    # Поисковая строка ловит и аэропорт, и город.
    assert 'data-q="сочи (адлер) сочи"' in card


def test_minutes_word_switches_to_hours():
    assert status_pages.minutes_word(44) == "44 мин"
    assert status_pages.minutes_word(180) == "3 ч"
    assert status_pages.minutes_word(210) == "3,5 ч"
