from __future__ import annotations

import scripts.routes_page as rp


def _route(points, posted="2026-08-10T22:00:00+00:00", threat="uav"):
    return {"points": points, "posted_at": posted, "threat": threat}


ANAPA = (44.89, 37.32, "Анапа")
RAEVSKAYA = (44.83, 37.56, "Раевская")
NOVOROSSIYSK = (44.72, 37.77, "Новороссийск")


def test_corridors_group_by_endpoints_and_respect_threshold():
    routes = [_route([ANAPA, RAEVSKAYA, NOVOROSSIYSK])] * rp.MIN_CORRIDOR
    routes += [_route([ANAPA, NOVOROSSIYSK])] * 2  # тот же коридор, короче
    routes += [_route([RAEVSKAYA, ANAPA])] * 3     # обратный — другой, редкий

    corridors = rp.build_corridors(routes)

    assert len(corridors) == 1
    corridor = corridors[0]
    assert (corridor["start"], corridor["end"]) == ("Анапа", "Новороссийск")
    assert corridor["count"] == rp.MIN_CORRIDOR + 2
    # Лицо коридора — самая частая цепочка, с промежуточной Раевской.
    assert [p[2] for p in corridor["face"]] == ["Анапа", "Раевская",
                                                "Новороссийск"]


def test_corridor_night_share_uses_moscow_clock():
    # 22:00 UTC = 01:00 МСК — ночь; 09:00 UTC = 12:00 МСК — день.
    routes = ([_route([ANAPA, NOVOROSSIYSK], "2026-08-10T22:00:00+00:00")] * 8
              + [_route([ANAPA, NOVOROSSIYSK], "2026-08-10T09:00:00+00:00")] * 2)
    corridor = rp.build_corridors(routes)[0]
    assert corridor["night_share"] == 80


def test_segments_merge_repeated_legs():
    routes = [_route([ANAPA, RAEVSKAYA, NOVOROSSIYSK])] * 3
    segments = rp.build_segments(routes)
    # Два плеча, каждое повторено трижды; единичных нет.
    assert sorted(s[4] for s in segments) == [3, 3]


def test_single_legs_stay_off_the_hero_map():
    assert rp.build_segments([_route([ANAPA, NOVOROSSIYSK])]) == []


def test_projection_maps_bbox_corners():
    projection = rp.Projection((44.0, 37.0, 46.0, 40.0), 300, 200)
    x0, y0 = projection.xy(46.0, 37.0)   # северо-запад -> левый верх
    assert (x0, y0) == (0.0, 0.0)
    x1, y1 = projection.xy(44.0, 37.0)   # юг ниже севера
    assert y1 > y0


def test_page_is_a_gallery_with_canonical_and_disclaimer():
    routes = [_route([ANAPA, RAEVSKAYA, NOVOROSSIYSK])] * 12
    html = rp.build_page(routes, "15 августа, 12:00 МСК")

    assert 'rel="canonical" href="https://tihoenebo.com/marshruty/"' in html
    assert "Анапа → Новороссийск" in html
    assert html.count("<svg") >= 2  # общая карта и хотя бы одна карточка
    assert "достраивает" in html
    assert "может опаздывать и ошибаться" in html
