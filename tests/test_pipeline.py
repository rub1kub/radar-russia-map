"""Тесты парсера, геокодера, слияния и времени.

    ingest/.venv/bin/python -m pytest tests -q

Парсер и геокодер меняются постоянно — без тестов каждая правка была
непроверяемой. Здесь зафиксировано поведение на реальных формулировках
из каналов папки «Радары».
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pipeline.fuse import Fuser
from pipeline.geocode import Geocoder
from pipeline.parse import parse, strip_footer
from pipeline.textnorm import norm_key, slugify, strip_unit
from pipeline.timeutil import MSK, parse_utc, to_utc


# --- Нормализация -----------------------------------------------------------

def test_slug_is_stable_latin():
    assert slugify("Ростовская область") == "rostovskaya_oblast"
    assert slugify("Ханты-Мансийский АО") == "khanty_mansiyskiy_ao"


def test_norm_key_folds_yo_and_punctuation():
    assert norm_key("Тимашёвский район!") == "тимашевский район"
    assert norm_key("Ростов-на-Дону") == "ростов-на-дону"


def test_strip_unit_removes_type_word():
    assert strip_unit("Азовский район") == "азовский"
    assert strip_unit("городской округ Краснодар") == "краснодар"


# --- Футеры и релевантность -------------------------------------------------

@pytest.mark.parametrize("text", [
    "Анапский район\nОпасность по БПЛА\n\nМониторинг Кубани - Подписаться",
    "г. Краснодар тревога по БПЛА\n\n❗️Дозор Краснодара - подписаться",
    "Ростов-на-Дону - опасность.\n\n📡Локатор России - @locatorru",
])
def test_footer_is_stripped(text):
    body = strip_footer(text)
    assert "одписа" not in body.lower()
    assert "@" not in body


def test_news_without_situation_is_irrelevant():
    observation = parse("Глава МИД заявил, что Зеленский не уйдёт от ответа")
    assert not observation.relevant


def test_alert_with_news_words_still_relevant():
    observation = parse("Белгородская область\nТревога по БПЛА")
    assert observation.relevant
    assert observation.signal_type == "alarm"


# --- Классификация ----------------------------------------------------------

@pytest.mark.parametrize("text,signal", [
    ("Краснодарский край\nОпасность по БПЛА", "danger"),
    ("Азов, фиксация БПЛА", "detection"),
    ("Ростов-на-Дону, работа ПВО по БПЛА", "intercept"),
    ("Ярославская область Отбой опасности по БПЛА", "allclear"),
    ("Новороссийск погодные условия\nБез паники", "retracted"),
    ("Темрюкский район\nМеры предосторожности!!!", "caution"),
])
def test_signal_classification(text, signal):
    assert parse(text).signal_type == signal


@pytest.mark.parametrize("text,threat", [
    ("Опасность по БПЛА", "uav"),
    ("Ракетная опасность", "rocket"),
    ("Угроза БЭК в акватории", "bek"),
])
def test_threat_classification(text, threat):
    assert parse(text).threat_type == threat


def test_allclear_has_zero_severity():
    assert parse("Отбой опасности по БПЛА").severity == 0


def test_target_count_extracted():
    assert parse("Ещё 2 БПЛА от Новобелая в сторону Воронежа").target_count == 2
    assert parse("Много фиксаций БПЛА").target_count == 10


# --- Геокодер (на реальном справочнике) -------------------------------------

@pytest.fixture(scope="module")
def geocoder():
    from pipeline.db import connect
    connection = connect()
    if connection.execute("SELECT COUNT(*) n FROM zones").fetchone()["n"] == 0:
        pytest.skip("справочник не построен: pipeline.gazetteer")
    return Geocoder(connection)


def test_geocoder_finds_place_inside_noise(geocoder):
    resolved = geocoder.resolve(["🔴Краснодар и ближайшие"])
    assert any("краснодар" in item.name.lower() for item in resolved)


def test_geocoder_resolves_homonym_by_context(geocoder):
    resolved = geocoder.resolve(["Успенское", "Краснодарский край"])
    names = {item.name for item in resolved}
    assert "Краснодарский край" in names
    chains = [geocoder.zone_path(item.zone_id) for item in resolved if item.name == "Успенское"]
    assert any("krasnodarskiy_kray" in " ".join(chain) for chain in chains)


def test_geocoder_ignores_event_vocabulary(geocoder):
    assert geocoder.resolve(["Опасность по БПЛА", "Меры безопасности"]) == []


# --- Слияние ----------------------------------------------------------------

class FakeObservation:
    def __init__(self, signal="danger", threat="uav", severity=5):
        self.signal_type = signal
        self.threat_type = threat
        self.severity = severity
        self.direction_deg = None
        self.target_count = None


def add(fuser, minute, source, tier="federal", **kwargs):
    return fuser.add(
        raw_id=minute, source_key=source, tier=tier,
        moment=datetime(2026, 7, 27, 10, minute, tzinfo=timezone.utc),
        observation=FakeObservation(**kwargs),
        zone_path=["azovskiy_rayon", "rostov_oblast"],
        lat=47.1, lon=39.4, level="district",
    )


def test_same_zone_within_window_merges():
    fuser = Fuser()
    add(fuser, 0, "a")
    add(fuser, 3, "b")
    assert len(fuser.events) == 1
    assert fuser.events[0].source_count if hasattr(fuser.events[0], "source_count") else True
    assert len(fuser.events[0].sources) == 2


def test_far_apart_in_time_does_not_merge():
    fuser = Fuser()
    add(fuser, 0, "a")
    add(fuser, 40, "b")
    assert len(fuser.events) == 2


def test_confidence_grows_with_independent_sources():
    fuser = Fuser()
    add(fuser, 0, "a")
    one = fuser.events[0].confidence
    add(fuser, 1, "b")
    two = fuser.events[0].confidence
    add(fuser, 2, "c")
    assert one < two < fuser.events[0].confidence <= 1.0


def test_repeat_from_same_source_does_not_inflate_confidence():
    fuser = Fuser()
    add(fuser, 0, "a")
    first = fuser.events[0].confidence
    add(fuser, 1, "a")
    assert fuser.events[0].confidence == first


def test_allclear_resolves_open_event():
    fuser = Fuser()
    add(fuser, 0, "a")
    add(fuser, 5, "b", signal="allclear", severity=0)
    assert fuser.events[0].resolved_at is not None
    assert fuser.events[0].status(datetime(2026, 7, 27, 10, 6, tzinfo=timezone.utc)) == "resolved"


def test_status_fades_then_closes():
    fuser = Fuser()
    add(fuser, 0, "a")
    event = fuser.events[0]
    base = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    assert event.status(base + timedelta(minutes=10)) == "active"
    assert event.status(base + timedelta(minutes=60)) == "fading"
    assert event.status(base + timedelta(hours=4)) == "resolved"


# --- Время ------------------------------------------------------------------

def test_naive_legacy_value_is_read_as_msk():
    assert parse_utc("2026-07-27T12:42:09") == datetime(2026, 7, 27, 9, 42, 9, tzinfo=timezone.utc)


def test_aware_value_is_preserved():
    assert parse_utc("2026-07-27T09:42:09+00:00").hour == 9


def test_to_utc_normalizes_offset():
    moscow_noon = datetime(2026, 7, 27, 12, 0, tzinfo=MSK)
    assert to_utc(moscow_noon) == datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)
