"""Как обстановка превращается в цвет зоны.

    ingest/.venv/bin/python -m pytest tests/test_state.py -q

Здесь проверяется правило, из-за которого район горел красным, когда
фиксаций там давно не было: цвет выбирает не самое страшное событие за
шесть часов, а самое весомое сейчас.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from api.server import ZONE_FADE_BY_LEVEL, fade_window, zone_fade

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def ago(**kwargs) -> str:
    return (NOW - timedelta(**kwargs)).isoformat()


def test_fresh_event_burns_at_full():
    assert zone_fade(ago(seconds=0), NOW) == 1.0


def test_district_goes_out_in_half_an_hour():
    """Борт пересекает средний район за полчаса.

    Считано по тем аппаратам, что здесь летают: украинские дальнобойные
    Хорнет, Бобр, Дартс, Лютый — крейсер порядка 150 км/ч, район поперёк
    73 км. Через полчаса без нового сообщения фиксация означает не «он
    здесь», а «он был здесь и ушёл».
    """
    assert zone_fade(ago(minutes=5), NOW, "district", "uav") == pytest.approx(0.62, abs=0.03)
    assert zone_fade(ago(minutes=15), NOW, "district", "uav") == pytest.approx(0.35, abs=0.03)
    assert zone_fade(ago(minutes=35), NOW, "district", "uav") == 0.12


def test_region_holds_much_longer_than_district():
    """Регион поперёк 400 км — его пересекают часа за два, а не за двадцать минут."""
    assert (zone_fade(ago(minutes=30), NOW, "region", "uav")
            > zone_fade(ago(minutes=30), NOW, "district", "uav"))


def test_rocket_leaves_the_zone_faster_than_a_drone():
    """«Нептун» идёт вшестеро быстрее дрона и покидает зону во столько же раз раньше."""
    assert (zone_fade(ago(minutes=6), NOW, "district", "rocket")
            < zone_fade(ago(minutes=6), NOW, "district", "uav"))


def test_naval_drone_lingers():
    """Безэкипажный катер ползёт по морю и висит в зоне дольше всех."""
    assert (zone_fade(ago(minutes=20), NOW, "district", "bek")
            > zone_fade(ago(minutes=20), NOW, "district", "uav"))


def test_window_never_shorter_than_the_reporting_lag():
    """Сообщение и так приходит с задержкой — меньше восьми минут не берём."""
    assert fade_window("place", "rocket") == 8 * 60


def test_first_minutes_matter_most():
    """Разница между «пять минут» и «час» важнее, чем между «два» и «три»."""
    early = zone_fade(ago(minutes=5), NOW) - zone_fade(ago(minutes=35), NOW)
    late = zone_fade(ago(minutes=125), NOW) - zone_fade(ago(minutes=155), NOW)
    assert early > late


def test_old_event_does_not_vanish():
    """Событие ещё не закрыто — стирать его с карты рано."""
    assert zone_fade(ago(hours=10), NOW) == 0.12


def test_future_stamp_is_treated_as_fresh():
    """Часы источника могут уйти вперёд; отрицательный возраст не считаем."""
    assert zone_fade((NOW + timedelta(minutes=5)).isoformat(), NOW) == 1.0


def test_stale_detection_loses_to_fresh_alarm():
    """Ради этого правило и появилось.

    Двухчасовая фиксация девятого уровня весит 9 x 0.33 = 3.0, свежая
    тревога седьмого — 7 x 1.0 = 7.0. Район красится тревогой, а не
    фиксацией, которая была и прошла.
    """
    stale = 9 * zone_fade(ago(hours=2), NOW)
    fresh = 7 * zone_fade(ago(minutes=1), NOW)
    assert fresh > stale


def test_when_everything_is_old_the_strongest_still_wins_but_dim():
    """Если свежего нет вовсе, цвет остаётся прежним, но приглушённым."""
    fade = zone_fade(ago(hours=2), NOW)
    assert 9 * fade > 7 * fade
    assert fade < 0.4


def test_windows_follow_zone_size():
    """Срок берётся из размера зоны, а не из круглого числа часов."""
    assert (ZONE_FADE_BY_LEVEL["place"]
            < ZONE_FADE_BY_LEVEL["district"]
            < ZONE_FADE_BY_LEVEL["region"])
