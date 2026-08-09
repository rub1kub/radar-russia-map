from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone

import scripts.seo_pages as seo


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE zones (
            id TEXT PRIMARY KEY,
            parent_id TEXT,
            level TEXT NOT NULL,
            name_ru TEXT NOT NULL,
            population INTEGER
        );
        CREATE TABLE events (
            zone_path TEXT,
            zone_id TEXT,
            signal_type TEXT,
            threat_type TEXT,
            first_seen_at TEXT
        );
        """
    )
    connection.executemany(
        "INSERT INTO zones VALUES (?, ?, ?, ?, ?)",
        [
            ("region", None, "region", "Тестовая область", None),
            ("gorodskoy_okrug_testograd_region", "region", "district",
             "городской округ Тестоград", None),
            ("testograd", "gorodskoy_okrug_testograd_region", "place",
             "Тестоград", 120_000),
        ],
    )
    for hour, signal in ((8, "danger"), (9, "alarm"), (10, "detection")):
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
            (json.dumps(["testograd", "gorodskoy_okrug_testograd_region",
                         "region"]),
             "testograd", signal, "uav",
             f"2026-08-09T{hour:02d}:00:00+00:00"),
        )
    return connection


def _json_ld(html: str) -> list[dict]:
    return [
        json.loads(raw)
        for raw in re.findall(
            r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    ]


def test_collects_region_city_and_daily_stats_in_one_pass(monkeypatch):
    monkeypatch.setattr(
        seo, "now_utc",
        lambda: datetime(2026, 8, 9, 12, tzinfo=timezone.utc),
    )
    regions, cities, days = seo.collect_stats(_connection())

    assert regions["region"]["events"] == 3
    assert cities["gorodskoy_okrug_testograd_region"]["events"] == 3
    assert days["2026-08-09"]["events"] == 3
    assert days["2026-08-09"]["regions"] == Counter({"region": 3})
    assert days["2026-08-09"]["recent"][0][2] == "detection"


def test_city_catalog_requires_real_activity_and_population(monkeypatch):
    monkeypatch.setattr(
        seo, "now_utc",
        lambda: datetime(2026, 8, 9, 12, tzinfo=timezone.utc),
    )
    connection = _connection()
    _, city_stats, _ = seo.collect_stats(connection)
    catalog = seo.build_city_catalog(
        connection, city_stats, {"region": ("Тестовая область", "test-region")})

    assert [(item["name"], item["slug"]) for item in catalog] == [
        ("Тестоград", "testograd")
    ]
    assert catalog[0]["admin_name"] == "городской округ Тестоград"


def test_city_names_are_inflected_for_search_titles():
    assert seo.inflect_city("Краснодар") == "Краснодаре"
    assert seo.inflect_city("Ростов-на-Дону") == "Ростове-на-Дону"
    assert seo.inflect_city("Нижний Новгород") == "Нижнем Новгороде"
    assert seo.inflect_city("Чебоксары") == "Чебоксарах"
    assert seo.inflect_city("Рязань") == "Рязани"
    assert seo.inflect_city("Жуковский") == "Жуковском"
    assert seo.inflect_city("Раменское") == "Раменском"
    assert seo.inflect_city("Алёшки") == "Алёшках"
    assert seo.inflect_city("Луховицы") == "Луховицах"
    assert seo.inflect_city("Елец") == "Ельце"
    assert seo.inflect_city("Малоярославец") == "Малоярославце"


def test_city_name_comes_from_matching_place_not_district_adjective():
    assert seo.city_name_for_area(
        "Бугульминский", [("Бугульма", 91_900), ("Карабаш", 5_149)]) == "Бугульма"
    assert seo.city_name_for_area(
        "Можайский", [("Можайск", 31_557)]) == "Можайск"
    assert seo.city_name_for_area(
        "Льговский", [("Льговский", 23_500), ("Льгов", 0)],
        "lgovskiy_kurskaya_oblast") == "Льгов"
    assert seo.city_name_for_area(
        "городской округ Лесосибирс", [("Лесосибирск", 65_945)]) == "Лесосибирск"
    assert seo.city_name_for_area(
        "городской округ Тюмень", [("Тюмен", 847_488)]) == "Тюмень"
    assert seo.city_name_for_area(
        "Богородский городской округ", [("Богородск", 34_000),
                                          ("Ногинск", 103_000)],
        "bogorodskiy_gorodskoy_okrug_moskovskaya_oblast") == "Ногинск"
    assert seo.city_name_for_area(
        "Артемовский округ", [("Бахмут", 80_500),
                               ("Артемовский", 0)],
        "artemovskiy_okrug_donetskaya_narodnaya_respublika") == "Бахмут"
    assert seo.city_slug("Лесосибирск") == "lesosibirsk"
    assert seo.canonical_city_name("г. Солнечногорск") == "Солнечногорск"
    assert seo.canonical_city_name("Озеры") == "Озёры"
    assert seo.canonical_city_name("Ликино-Дулево") == "Ликино-Дулёво"
    assert seo.canonical_city_name("Малоярославетс") == "Малоярославец"


def test_city_manifest_keeps_published_url_when_activity_expires(tmp_path):
    manifest = tmp_path / "city" / "manifest.json"
    current = [{
        "zone_id": "city-zone", "name": "Тестоград",
        "admin_name": "городской округ Тестоград", "region_id": "region",
        "region_name": "Тестовая область", "region_slug": "test-region",
        "slug": "testograd", "stats": {"events": 3},
    }]
    first = seo.persistent_city_catalog(
        current, {"city-zone": {"events": 3}}, manifest)
    second = seo.persistent_city_catalog([], {}, manifest)

    assert first[0]["slug"] == "testograd"
    assert second[0]["slug"] == "testograd"
    assert second[0]["stats"] is None


def test_city_manifest_applies_explicit_slug_migration(tmp_path):
    manifest = tmp_path / "city" / "manifest.json"
    old = [{
        "zone_id": "bogorodskiy_gorodskoy_okrug_moskovskaya_oblast",
        "name": "Богородск", "admin_name": "Богородский городской округ",
        "region_id": "moskovskaya_oblast", "region_name": "Московская область",
        "region_slug": "moskovskaya-oblast", "slug": "bogorodsk",
    }]
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    current = [dict(old[0], name="Ногинск", slug="noginsk", stats={"events": 3})]

    catalog = seo.persistent_city_catalog(current, {}, manifest)

    assert catalog[0]["name"] == "Ногинск"
    assert catalog[0]["slug"] == "noginsk"
    redirect = seo.city_redirect_page("noginsk")
    assert 'content="noindex,follow"' in redirect
    assert 'rel="canonical" href="https://tihoenebo.com/city/noginsk/"' in redirect
    assert 'content="noindex,follow"' in seo.city_retired_page()


def test_legacy_city_manifest_is_rebuilt_instead_of_preserved(tmp_path):
    manifest = tmp_path / "city" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps([{
        "zone_id": "stale", "name": "Ошибочный",
        "admin_name": "Ошибочный округ", "region_id": "region",
        "region_name": "Тестовая область", "region_slug": "test-region",
        "slug": "oshibochnyy",
    }], ensure_ascii=False), encoding="utf-8")

    catalog = seo.persistent_city_catalog([], {}, manifest)

    assert catalog == []
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved == {"version": seo.CITY_MANIFEST_VERSION, "cities": []}


def test_digest_manifest_keeps_older_published_days(tmp_path):
    digest_dir = tmp_path / "svodka"
    old_page = digest_dir / "2026-08-08" / "index.html"
    old_page.parent.mkdir(parents=True)
    old_page.write_text("old", encoding="utf-8")
    empty_page = digest_dir / "2026-08-07" / "index.html"
    empty_page.parent.mkdir(parents=True)
    empty_page.write_text("empty", encoding="utf-8")
    (digest_dir / "manifest.json").write_text(
        '{"2026-08-07":{"events":0},"2026-08-08":{"events":7}}',
        encoding="utf-8",
    )

    history = seo.digest_history(
        ["2026-08-09"], {"2026-08-09": {"events": 3}}, digest_dir)

    assert history == {
        "2026-08-08": {"events": 7},
        "2026-08-09": {"events": 3},
    }


def test_city_page_has_canonical_parent_and_valid_structured_data():
    stats = {
        "events": 3, "days": {datetime(2026, 8, 9).date()},
        "last": "2026-08-09T10:00:00+00:00", "today": 3, "fresh": 1,
        "districts": Counter(), "signals": Counter({"detection": 3}),
        "threats": Counter({"uav": 3}), "hours": Counter({10: 3}),
        "recent": [],
    }
    html = seo.page(
        "Краснодар", "krasnodar", [], stats,
        path_prefix="city",
        parent=("Краснодарский край",
                "https://tihoenebo.com/region/krasnodarskiy-kray/"),
        admin_name="городской округ Краснодар",
        map_region_slug="krasnodarskiy-kray",
        neighbours=[("Анапа", "anapa")],
        updated="9 августа, 15:00 МСК",
    )

    assert "Тревога и БПЛА в Краснодаре сейчас" in html
    assert 'rel="canonical" href="https://tihoenebo.com/city/krasnodar/"' in html
    assert '/?region=krasnodarskiy-kray' in html
    assert "городской округ Краснодар" in html
    documents = _json_ld(html)
    assert documents[0]["@type"] == "BreadcrumbList"
    assert documents[1]["about"]["containedInPlace"]["name"] == "Краснодарский край"
    assert documents[2]["@type"] == "FAQPage"


def test_daily_digest_is_an_article_with_live_region_links():
    stats = {
        "events": 3, "regions": Counter({"region": 3}),
        "signals": Counter({"detection": 2, "alarm": 1}),
        "threats": Counter({"uav": 3}), "recent": [],
    }
    html = seo.digest_page(
        "2026-08-09", stats,
        {"region": ("Тестовая область", "test-region")},
        "2026-08-08", None, "9 августа, 15:00 МСК",
        "2026-08-09T15:00:00+03:00",
    )

    assert "Сводка тревог и БПЛА за 9 августа 2026 года" in html
    assert '/region/test-region/' in html
    documents = _json_ld(html)
    article = next(item for item in documents if item["@type"] == "Article")
    assert article["datePublished"] == "2026-08-09T00:00:00+03:00"
    assert article["dateModified"] == "2026-08-09T15:00:00+03:00"
