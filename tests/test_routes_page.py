from __future__ import annotations

import math
import sqlite3

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
    """Карточка галереи рисуется тем же сплайном, что и трассы на карте."""
    path = rp.flow_path([(0.0, 0.0), (50.0, 40.0), (100.0, 0.0)])
    # Одно звено на точку заменяется десятками — это уже кривая.
    assert path.count("L") > 15
    assert path.startswith("M0.0 0.0")
    assert path.endswith("L100.0 0.0")
    # Морская отводка входит обычной путевой точкой и тоже сглаживается.
    arc = rp.flow_path([(0.0, 0.0), (100.0, 0.0)], (50.0, 30.0))
    assert arc.count("L") > 15


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


def test_graph_counts_reconstructed_waves_separately():
    """Пересказ источника и наша догадка считаются порознь.

    Подсказка на карте показывает долю вычисленного — значит, счёт должен
    их различать, а не сваливать в одно число.
    """
    routes = [_route([ANAPA, NOVOROSSIYSK])] * 3
    wave = [{"lat": ANAPA[0], "lon": ANAPA[1], "name": "Анапа"},
            {"lat": NOVOROSSIYSK[0], "lon": NOVOROSSIYSK[1],
             "name": "Новороссийск"}]
    _, edges = rp.build_graph(routes, [wave] * 7)

    assert len(edges) == 1
    assert edges[0]["count"] == 10
    assert edges[0]["named"] == 3
    assert edges[0]["computed"] == 7


def test_export_graph_marks_trunks_arcs_and_anchors(land):
    if not land.rings:
        pytest.skip("regions.json недоступен")
    tuapse = (44.10, 39.08, "Туапсе")
    sochi = (43.60, 39.73, "Сочи")
    routes = [_route([tuapse, sochi])] * 12
    nodes, edges = rp.build_graph(routes, [])
    graph = rp.export_graph(nodes, edges, land,
                            {("Туапсе", "Сочи"): "kor-0"},
                            {"routes": 12})

    assert len(graph["chains"]) == 1
    chain = graph["chains"][0]
    assert (chain["from"], chain["to"]) == ("Туапсе", "Сочи")
    assert chain["t"] == 1                      # магистраль
    assert chain["kor"] == "kor-0"              # ссылка на карточку
    assert chain["nm"] == 12 and chain["cp"] == 0
    # Туапсе — Сочи идёт вдоль берега: дуга над морем посчитана в точки,
    # клиенту остаётся нарисовать линию.
    assert len(chain["pts"]) >= 10
    names = {label["name"] for label in graph["labels"]}
    assert names == {"Туапсе", "Сочи"}


def test_smooth_path_keeps_waypoints_and_kills_corners():
    """Прямой угол между районами — артефакт ломаной, а не траектория.

    Борт самолётной схемы на 150 км/ч разворачивается радиусом ~400 м: на
    масштабе карты угол физически невозможен. Кривая обязана пройти через
    те же путевые точки, но без излома и без петли.
    """
    corner = [(50.0, 36.0), (50.0, 37.0), (51.0, 37.0)]
    curve = rp.smooth_path(corner, steps=12)

    for point in corner:
        assert any(abs(c[0] - point[0]) < 1e-6 and abs(c[1] - point[1]) < 1e-6
                   for c in curve)

    def angle(a, b, c) -> float:
        v1 = (b[0] - a[0], (b[1] - a[1]) * 0.64)
        v2 = (c[0] - b[0], (c[1] - b[1]) * 0.64)
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < 1e-12 or n2 < 1e-12:
            return 0.0
        cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
        return math.degrees(math.acos(cos))

    worst = max(angle(curve[i], curve[i + 1], curve[i + 2])
                for i in range(len(curve) - 2))
    assert worst < 35
    # Центростремительный Catmull-Rom не даёт петель: длина почти не растёт.
    length = lambda path: sum(math.dist(a, b) for a, b in zip(path, path[1:]))
    assert length(curve) < length(corner) * 1.15


def test_tracks_follow_drone_physics():
    """Волна собирается по скорости и курсу, а не по близости.

    Три фиксации на одной прямой с шагом в полчаса и 75 км — это 150 км/ч,
    крейсерская скорость борта: трек. Четвёртая через минуту в стороне —
    другой борт, и в трек не идёт.
    """
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE zones (id TEXT PRIMARY KEY, level TEXT, name_ru TEXT,
                            source_id TEXT);
        CREATE TABLE events (zone_id TEXT, lat REAL, lon REAL,
                             severity INTEGER, first_seen_at TEXT);
        """
    )
    chain = [("a", 50.0, 36.0, "2026-08-10T00:00:00+00:00"),
             ("b", 50.0, 37.05, "2026-08-10T00:30:00+00:00"),
             ("c", 50.0, 38.10, "2026-08-10T01:00:00+00:00"),
             ("x", 55.0, 50.00, "2026-08-10T00:31:00+00:00")]
    for zone, lat, lon, stamp in chain:
        connection.execute("INSERT INTO zones VALUES (?,?,?,?)",
                           (zone, "district", zone.upper(), None))
        connection.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                           (zone, lat, lon, 8, stamp))

    tracks = rp.reconstruct_tracks(connection)

    assert len(tracks) == 1
    assert [point["zone"] for point in tracks[0]] == ["a", "b", "c"]


def test_assets_are_versioned_against_the_weekly_cache(land):
    """Имена бандла и данных фиксированы, Apache кэширует их на неделю.

    Без метки версии браузер неделю показывал старую карту — правки
    уезжали на боевой, а владелец видел прежнюю.
    """
    routes = [_route([ANAPA, NOVOROSSIYSK])] * 12
    html = rp.build_page(routes, [], land, "16 августа, 12:00 МСК",
                         versions={"js": "aaa11111", "css": "bbb22222",
                                   "data": "ccc33333"})

    assert "/assets/marshruty-map.js?v=aaa11111" in html
    assert "/assets/marshruty-map.css?v=bbb22222" in html
    assert 'data-version="ccc33333"' in html


def test_sea_corridors_stay_out_of_the_gallery():
    """«Новороссийск → Чёрное море» — уход за берег, а не коридор."""
    sea = (44.30, 37.20, "Чёрное море")
    routes = ([_route([NOVOROSSIYSK, sea])] * 20
              + [_route([ANAPA, NOVOROSSIYSK])] * 20)

    names = [(c["start"], c["end"])
             for c in rp.build_corridors(routes, skip_names={"Чёрное море"})]

    assert names == [("Анапа", "Новороссийск")]


def test_chains_merge_consecutive_edges_into_one_route():
    tuapse = (44.10, 39.08, "Туапсе")
    sochi = (43.60, 39.73, "Сочи")
    routes = ([_route([ANAPA, NOVOROSSIYSK])] * 6
              + [_route([NOVOROSSIYSK, tuapse])] * 5
              + [_route([tuapse, sochi])] * 4)
    nodes, edges = rp.build_graph(routes, [])
    chains = rp.assemble_chains(nodes, edges)

    # Три плеча одного направления склеились в одну трассу Анапа — Сочи.
    assert len(chains) == 1
    chain = chains[0]
    assert nodes[chain[0]["a"]]["name"] == "Анапа"
    assert nodes[chain[-1]["b"]]["name"] == "Сочи"


def test_page_is_a_gallery_with_map_container(land):
    routes = [_route([ANAPA, RAEVSKAYA, NOVOROSSIYSK])] * 12
    transitions = [{
        "a": (44.89, 37.32), "b": (44.72, 37.77),
        "start": "Анапа", "end": "Геленджик", "count": 7,
    }]
    html = rp.build_page(routes, transitions, land, "15 августа, 12:00 МСК")

    assert 'rel="canonical" href="https://tihoenebo.com/marshruty/"' in html
    assert "Анапа → Новороссийск" in html
    assert html.count("<svg") >= 1               # карточки галереи
    assert 'id="routes-map"' in html             # контейнер OpenLayers
    assert "/assets/marshruty-map.js" in html
    assert "/assets/marshruty-map.css" in html
    assert 'id="kor-0"' in html                  # якорь карточки для клика
    assert "может опаздывать и ошибаться" in html
