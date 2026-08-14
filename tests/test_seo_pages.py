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
    for hour, signal in (
        (8, "danger"), (9, "alarm"), (10, "detection"),
        (11, "detection"), (12, "detection"),
    ):
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

    assert regions["region"]["events"] == 5
    assert cities["gorodskoy_okrug_testograd_region"]["events"] == 5
    assert days["2026-08-09"]["events"] == 5
    assert days["2026-08-09"]["regions"] == Counter({"region": 5})
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


def test_district_names_are_inflected_word_by_word():
    assert seo.inflect_district("Погарский район") == "Погарском районе"
    assert seo.inflect_district("Абыйский улус") == "Абыйском улусе"
    assert seo.inflect_district("Тандинский кожуун") == "Тандинском кожууне"
    assert seo.inflect_district("район имени Лазо") == "районе имени Лазо"
    assert seo.inflect_district(
        "Немецкий Национальный район") == "Немецком Национальном районе"
    assert seo.inflect_district(
        "Анабарский национальный (долгано-эвенкийский) район"
    ) == "Анабарском национальном (долгано-эвенкийском) районе"
    assert seo.inflect_district("Предгорный район") == "Предгорном районе"
    assert seo.district_slug("Погарский район") == "pogarskiy"
    assert seo.district_slug("район имени Лазо") == "imeni-lazo"


def test_district_catalog_takes_rayons_not_covered_by_city_pages():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE zones (
            id TEXT PRIMARY KEY, parent_id TEXT,
            level TEXT NOT NULL, name_ru TEXT NOT NULL, population INTEGER
        );
        """
    )
    connection.executemany(
        "INSERT INTO zones VALUES (?, ?, ?, ?, ?)",
        [
            ("region", None, "region", "Брянская область", None),
            ("region2", None, "region", "Ростовская область", None),
            ("pogarskiy", "region", "district", "Погарский район", None),
            ("kamenskiy_b", "region", "district", "Каменский район", None),
            ("kamenskiy_r", "region2", "district", "Каменский район", None),
            ("okrug", "region", "district", "городской округ Тестоград", None),
            ("quiet", "region", "district", "Тихий район", None),
        ],
    )
    regions = {"region": ("Брянская область", "bryanskaya-oblast"),
               "region2": ("Ростовская область", "rostovskaya-oblast")}
    city_stats = {
        "pogarskiy": {"events": 20}, "kamenskiy_b": {"events": 16},
        "kamenskiy_r": {"events": 17}, "okrug": {"events": 50},
        "quiet": {"events": 3},
    }

    catalog = seo.build_district_catalog(
        connection, city_stats, regions, {"okrug"})

    # Городской округ уже занят городской страницей, тихий район не прошёл
    # порог; тёзки получают суффикс субъекта.
    assert [(item["name"], item["slug"]) for item in catalog] == [
        ("Каменский район", "kamenskiy-bryanskaya-oblast"),
        ("Каменский район", "kamenskiy-rostovskaya-oblast"),
        ("Погарский район", "pogarskiy"),
    ]


def test_rayon_page_inflects_and_links_district_hub():
    stats = {
        "events": 21, "days": {datetime(2026, 8, 9).date()},
        "last": "2026-08-09T10:00:00+00:00", "today": 2, "fresh": 1,
        "districts": Counter(), "signals": Counter({"danger": 21}),
        "threats": Counter({"uav": 21}), "hours": Counter({10: 21}),
        "recent": [],
    }
    html = seo.page(
        "Погарский район", "pogarskiy", [], stats,
        path_prefix="rayon",
        parent=("Брянская область",
                "https://tihoenebo.com/region/bryanskaya-oblast/"),
        admin_name="Погарский район",
        map_region_slug="bryanskaya-oblast",
        neighbours=[("Почепский район", "pochepskiy")],
        updated="9 августа, 15:00 МСК",
    )

    assert "Тревога и БПЛА в Погарском районе сейчас" in html
    assert ('rel="canonical" href="https://tihoenebo.com/rayon/pogarskiy/"'
            in html)
    assert '<a href="/rayon/">Районы</a>' in html
    assert '/?region=bryanskaya-oblast' in html
    assert '/rayon/pochepskiy/' in html
    documents = _json_ld(html)
    assert documents[0]["itemListElement"][1]["item"].endswith("/rayon/")
    assert documents[1]["about"]["containedInPlace"]["name"] == "Брянская область"


def test_region_page_links_districts_with_own_pages():
    html = seo.page(
        "Брянская область", "bryanskaya-oblast",
        [("Погарский район", "/rayon/pogarskiy/"), ("Тихий район", None)],
        None,
        neighbours=[("Калужская область", "kaluzhskaya-oblast")],
        updated="9 августа, 15:00 МСК",
    )

    assert '<a href="/rayon/pogarskiy/">Погарский район</a>' in html
    assert "<li>Тихий район</li>" in html


def test_district_index_groups_by_region():
    rayons = [{
        "zone_id": "pogarskiy", "name": "Погарский район",
        "admin_name": "Погарский район", "region_id": "region",
        "region_name": "Брянская область", "region_slug": "bryanskaya-oblast",
        "slug": "pogarskiy", "stats": {"events": 20},
    }]

    html = seo.district_index_page(rayons, "9 августа, 18:30 МСК")

    assert '<link rel="canonical" href="https://tihoenebo.com/rayon/"' in html
    assert "Тревога и БПЛА по районам России" in html
    assert '/rayon/pogarskiy/' in html
    assert '/region/bryanskaya-oblast/' in html
    documents = _json_ld(html)
    assert documents[1]["mainEntity"]["numberOfItems"] == 1


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
    assert "city/noginsk" in seo.city_legacy_page("noginsk", {"noginsk"})
    assert "canonical" not in seo.city_legacy_page("noginsk", set())


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
    assert documents[0]["itemListElement"][1]["item"].endswith("/city/")
    assert [item["position"] for item in documents[0]["itemListElement"]] == [1, 2, 3, 4]
    assert documents[1]["about"]["containedInPlace"]["name"] == "Краснодарский край"
    assert documents[2]["@type"] == "FAQPage"


def test_city_index_groups_regions_and_lists_every_city():
    cities = [
        {
            "zone_id": "city-1", "name": "Краснодар",
            "admin_name": "городской округ Краснодар",
            "region_id": "region-1", "region_name": "Краснодарский край",
            "region_slug": "krasnodarskiy-kray", "slug": "krasnodar",
            "stats": {"events": 3},
        },
        {
            "zone_id": "city-2", "name": "Анапа",
            "admin_name": "городской округ Анапа",
            "region_id": "region-1", "region_name": "Краснодарский край",
            "region_slug": "krasnodarskiy-kray", "slug": "anapa",
            "stats": {"events": 5},
        },
    ]

    html = seo.city_index_page(cities, "9 августа, 18:30 МСК")

    assert '<link rel="canonical" href="https://tihoenebo.com/city/"' in html
    assert "Тревога и БПЛА по городам России" in html
    assert '/region/krasnodarskiy-kray/' in html
    assert '/city/anapa/' in html
    assert '/city/krasnodar/' in html
    documents = _json_ld(html)
    assert documents[0]["@type"] == "BreadcrumbList"
    assert documents[1]["@type"] == "CollectionPage"
    assert documents[1]["mainEntity"]["numberOfItems"] == 2
    assert len(documents[1]["mainEntity"]["itemListElement"]) == 2


def test_saint_petersburg_page_uses_proven_search_aliases():
    html = seo.page(
        "Санкт-Петербург", "sankt-peterburg", ["Адмиралтейский район"], None,
        neighbours=[("Ленинградская область", "leningradskaya-oblast")],
        updated="9 августа, 18:30 МСК",
    )

    assert "СПб" in html
    assert "Питер" in html
    assert "Где смотреть тревогу в СПб и Питере?" in html
    documents = _json_ld(html)
    assert documents[1]["about"]["alternateName"] == ["СПб", "Питер"]
    questions = documents[2]["mainEntity"]
    assert any(item["name"] == "Где смотреть тревогу в СПб и Питере?"
               for item in questions)


def test_prerender_links_city_catalog_from_homepage(tmp_path, monkeypatch):
    monkeypatch.setattr(seo, "OUT", tmp_path)
    (tmp_path / "index.html").write_text(
        "before<!-- prerender:start -->old<!-- prerender:end -->after",
        encoding="utf-8",
    )

    filled = seo.fill_prerender(
        [("Тестовая область", "test-region", "source", "region")],
        {"region": {"events": 3}}, "9 августа, 18:30 МСК", 150,
    )

    assert filled is True
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'href="/city/"' in html
    assert "Сводки по 150 городам России" in html
    assert 'href="/region/test-region/"' in html


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
