"""Отбой гражданского предупреждения требует понятной области действия."""

from datetime import datetime, timedelta, timezone

from pipeline.db import connect
from pipeline.fuse import Fuser
from pipeline.geocode import Geocoder
from pipeline.parse import Observation, parse
from pipeline.source_region import civil_clear_guards, resolve_observation_zones
from pipeline.textnorm import name_variants

BASE = datetime(2026, 10, 25, tzinfo=timezone.utc)


def geocoder(tmp_path):
    c = connect(tmp_path / "scope.db")
    for zone_id, parent, level, name in (
        ("region", None, "region", "Тестовая область"),
        ("south", "region", "district", "Южный район"),
        ("village", "south", "place", "Тестовый Посёлок"),
        ("north", "region", "district", "Северный район"),
    ):
        c.execute("INSERT INTO zones (id,parent_id,level,name_ru) VALUES (?,?,?,?)",
                  (zone_id, parent, level, name))
        for variant in name_variants(name, level):
            c.execute("INSERT OR IGNORE INTO zone_names VALUES (?,?,'variant')", (variant, zone_id))
    c.commit()
    return Geocoder(c)


def warning(fuser, geo, zone_id, raw_id):
    return fuser.add(raw_id=raw_id, source_key="source", tier="official", moment=BASE,
                     observation=Observation(signal_type="danger", threat_type="uav", severity=5),
                     zone_path=geo.zone_path(zone_id), lat=None, lon=None, level=geo.zones[zone_id]["level"])


def test_exception_protects_descendants_and_does_not_declare_whole_region_clear(tmp_path):
    geo = geocoder(tmp_path)
    fuser = Fuser()
    events = {zone: warning(fuser, geo, zone, index)
              for index, zone in enumerate(("region", "south", "village", "north"), 1)}
    observation = parse("Тестовая область — отбой опасности по БПЛА. Опасность сохраняется в Южном районе.")
    resolved, used_fallback = resolve_observation_zones(geo, observation, "region")
    guards = civil_clear_guards(geo, observation, "region", used_fallback, "official")
    assert "south" in guards["civil_excluded"]
    for place in resolved:
        fuser.add(raw_id=100, source_key="source", tier="official", moment=BASE + timedelta(minutes=2),
                  observation=observation, zone_path=geo.zone_path(place.zone_id),
                  lat=None, lon=None, level=place.level, **guards)
    assert events["region"].resolved_at is None
    assert events["south"].resolved_at is None
    assert events["village"].resolved_at is None
    assert events["north"].resolved_at == BASE + timedelta(minutes=2)


def test_only_explicit_region_wide_official_fallback_can_clear_civil_warning(tmp_path):
    geo = geocoder(tmp_path)
    for text, tier, can_clear in (
        ("Отмена угрозы атаки", "official", False),
        ("Отбой опасности по всей территории региона", "regional", False),
        ("Отбой опасности по всей территории региона", "official", True),
    ):
        fuser = Fuser()
        event = warning(fuser, geo, "region", 1)
        observation = parse(text)
        guards = civil_clear_guards(geo, observation, "region", True, tier)
        fuser.add(raw_id=2, source_key="source", tier=tier, moment=BASE + timedelta(minutes=1),
                  observation=observation, zone_path=["region"], lat=None, lon=None,
                  level="region", **guards)
        assert (event.resolved_at is not None) is can_clear


def test_promised_allclear_and_explanation_are_not_civil_cancellations(tmp_path):
    geo = geocoder(tmp_path)
    for text in (
        "Тестовая область. Без фиксаций БПЛА. Скоро будет отбой беспилотной опасности.",
        "Тестовая область. Хотим уточнить, что мы вводим сигнал «Воздушная тревога» только в случае опасности. Сигнал «Отбой» означает завершение.",
    ):
        fuser = Fuser()
        event = warning(fuser, geo, "region", 1)
        observation = parse(text)
        guards = civil_clear_guards(geo, observation, "region", False, "official")
        if observation.relevant:
            fuser.add(raw_id=2, source_key="source", tier="official", moment=BASE + timedelta(minutes=1),
                      observation=observation, zone_path=["region"], lat=None, lon=None,
                      level="region", **guards)
        assert event.resolved_at is None
