"""Тесты прореживания контуров, которые отдаёт /api/v1/geo.

Клиент рисует эти полигоны заново на каждое движение карты, поэтому лишние
вершины стоят не трафика, а тепла телефона. Но упрощение легко переходит в
порчу: контур схлопывается в отрезок, остров исчезает вместе с регионом.
Здесь зафиксировано, где проходит граница.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.geometry import (_douglas_peucker, _drop_specks, _ring_area,
                          _simplify_ring, round_geometry)


def _square(x: float, y: float, size: float) -> list[list[float]]:
    return [[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]


def test_straight_run_collapses_to_its_ends():
    line = [[0, 0], [1, 0.0001], [2, 0], [3, 0.0001], [4, 0]]
    assert _douglas_peucker(line, 0.01) == [[0, 0], [4, 0]]


def test_corner_survives_the_tolerance():
    line = [[0, 0], [1, 1], [2, 0]]
    assert _douglas_peucker(line, 0.1) == line


def test_closed_ring_does_not_collapse():
    """У замкнутой линии хорда «начало-конец» нулевая.

    Прямой проход Дугласа-Пекера считал бы все вершины лежащими на ней и
    возвращал бы отрезок вместо контура — регион исчезал бы с карты.
    """
    ring = _square(0, 0, 10)
    simplified = _simplify_ring(ring, 0.5)
    assert simplified is not None
    assert len(simplified) >= 4
    assert simplified[0] == simplified[-1]
    assert _ring_area(simplified) > 90


def test_wobbles_smaller_than_tolerance_are_dropped():
    ring = [[0, 0], [5, 0.02], [10, 0], [10, 10], [0, 10], [0, 0]]
    simplified = _simplify_ring(ring, 0.5)
    assert len(simplified) < len(ring)


def test_tiny_islands_go_but_never_the_whole_region():
    big = [_square(0, 0, 10)]
    speck = [_square(50, 50, 0.001)]
    kept = _drop_specks([big, speck], min_area=0.002)
    assert kept == [big]

    # Регион целиком из мелочи обязан остаться хоть чем-то: пустое место на
    # карте читается как «здесь ничего нет», а это неправда.
    only_specks = _drop_specks([speck, [_square(60, 60, 0.0005)]], min_area=0.002)
    assert len(only_specks) == 1


def test_geometry_survives_the_whole_pipeline():
    geometry = {"type": "Polygon", "coordinates": [_square(37.0, 55.0, 1.0)]}
    out = round_geometry(geometry, precision=2, tolerance=0.01, min_area=0.002)
    assert out is not None
    ring = out["coordinates"][0]
    assert ring[0] == ring[-1]
    assert _ring_area(ring) > 0.9
