from __future__ import annotations

import pytest

import scripts.routes_page as rp


def _route(points, posted="2026-08-10T22:00:00+00:00", threat="uav"):
    return {"points": points, "posted_at": posted, "threat": threat}


ANAPA = (44.89, 37.32, "Анапа")
RAEVSKAYA = (44.83, 37.56, "Раевская")
NOVOROSSIYSK = (44.72, 37.77, "Новороссийск")


@pytest.fixture(scope="module")
def land() -> rp.Land:
    return rp.Land()


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


def test_projection_maps_bbox_corners():
    projection = rp.Projection((44.0, 37.0, 46.0, 40.0), 300, 200)
    x0, y0 = projection.xy(46.0, 37.0)   # северо-запад -> левый верх
    assert (x0, y0) == (0.0, 0.0)
    x1, y1 = projection.xy(44.0, 37.0)   # юг ниже севера
    assert y1 > y0


def test_flow_path_smooths_kinked_chains():
    path = rp.flow_path([(0.0, 0.0), (50.0, 40.0), (100.0, 0.0)])
    # Излом сглажен квадратичной кривой через середину отрезка.
    assert "Q50.0 40.0 75.0 20.0" in path
    # Дуга по управляющей точке — один сегмент Q.
    arc = rp.flow_path([(0.0, 0.0), (100.0, 0.0)], (50.0, 30.0))
    assert arc == "M0.0 0.0Q50.0 30.0 100.0 0.0"


def test_coastal_corridor_bends_over_the_sea(land):
    if not land.rings:
        pytest.skip("regions.json недоступен")
    # Туапсе -> Сочи: суша строго к северо-востоку, море к юго-западу.
    control = land.sea_control((44.10, 39.08), (43.60, 39.73))
    assert control is not None
    assert not land.is_land(*control)
    # Сухопутное плечо в глубине области дугу не получает.
    assert land.sea_control((52.60, 36.0), (52.97, 37.05)) is None


def test_graph_merges_legs_and_directions():
    routes = [_route([ANAPA, RAEVSKAYA, NOVOROSSIYSK])] * 4
    routes += [_route([NOVOROSSIYSK, RAEVSKAYA])] * 1  # обратный ход
    nodes, edges = rp.build_graph(routes, [])

    names = {n["name"] for n in nodes}
    assert names == {"Анапа", "Раевская", "Новороссийск"}
    # Два плеча; Раевская—Новороссийск слита из 4 туда + 1 обратно.
    assert len(edges) == 2
    merged = next(e for e in edges if e["count"] == 5)
    assert (nodes[merged["a"]]["name"], nodes[merged["b"]]["name"]) == (
        "Раевская", "Новороссийск")
    assert (merged["forward"], merged["backward"]) == (4, 1)


def test_graph_counts_computed_transitions_separately():
    routes = [_route([ANAPA, NOVOROSSIYSK])] * 3
    transitions = [{
        "a": (ANAPA[0], ANAPA[1]), "b": (NOVOROSSIYSK[0], NOVOROSSIYSK[1]),
        "start": "Анапа", "end": "Новороссийск", "count": 7,
    }]
    _, edges = rp.build_graph(routes, transitions)

    assert len(edges) == 1
    assert edges[0]["count"] == 10
    assert edges[0]["named"] == 3
    assert edges[0]["computed"] == 7


def test_page_is_a_gallery_with_canonical_and_disclaimer(land):
    routes = [_route([ANAPA, RAEVSKAYA, NOVOROSSIYSK])] * 12
    transitions = [{
        "a": (44.89, 37.32), "b": (44.72, 37.77),
        "start": "Анапа", "end": "Геленджик", "count": 7,
    }]
    html = rp.build_page(routes, transitions, land, "15 августа, 12:00 МСК")

    assert 'rel="canonical" href="https://tihoenebo.com/marshruty/"' in html
    assert "Анапа → Новороссийск" in html
    assert html.count("<svg") >= 2  # общая карта и хотя бы одна карточка
    assert "магистраль" in html                      # легенда графа
    assert "восстановлено по фиксациям" in html      # раскладка в подсказке
    assert 'class="ant' in html                      # бегущие штрихи
    assert "hero-zoom" in html                       # управление приближением
    assert 'href="#kor-0"' in html                   # ребро ведёт к карточке
    assert "может опаздывать и ошибаться" in html
