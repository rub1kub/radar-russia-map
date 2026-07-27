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

from api.server import ZONE_FADE, zone_fade

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def ago(**kwargs) -> str:
    return (NOW - timedelta(**kwargs)).isoformat()


def test_fresh_event_burns_at_full():
    assert zone_fade(ago(seconds=0), NOW) == 1.0


def test_hour_old_is_about_two_thirds():
    assert zone_fade(ago(hours=1), NOW) == pytest.approx(0.667, abs=0.01)


def test_old_event_does_not_vanish():
    """Событие ещё не закрыто — стирать его с карты рано."""
    assert zone_fade(ago(hours=10), NOW) == 0.25


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


def test_fade_threshold_matches_the_pipeline():
    """Порог тот же, что у затухания события и у выцветания значков."""
    assert ZONE_FADE == timedelta(hours=3)
