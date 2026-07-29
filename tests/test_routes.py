"""Маршрут, названный самим сообщением.

    ingest/.venv/bin/python -m pytest tests/test_routes.py -q

Линия рисуется только тогда, когда источник сам описал путь: «от Анапы
через Раевскую на Новороссийск». Склейка маршрутов из разных сообщений —
догадка, и в таблицу фактов она не попадает.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.geocode import Resolved
from pipeline.parse import parse
from pipeline.routes import extract_route

# Реальная геометрия Кубани: Анапа -> Раевская -> Новороссийск, ~30 км пути.
ANAPA = Resolved("anapa", "district", "Анапа", 44.89, 37.32, "анапа")
RAEVSKAYA = Resolved("raevskaya", "place", "Раевская", 44.83, 37.56, "раевская")
NOVOROSSIYSK = Resolved("novorossiysk", "district", "Новороссийск", 44.72, 37.77, "новороссийск")
KRAI = Resolved("krasnodarskiy_kray", "region", "Краснодарский край", 45.6, 39.0, "край")


def test_declared_route_is_extracted():
    text = "Анапа, через Раевскую в сторону Новороссийска — фиксация БПЛА"
    route = extract_route(text, parse(text), [ANAPA, RAEVSKAYA, NOVOROSSIYSK])
    assert route is not None
    # Порядок точек — порядок текста: откуда и куда.
    assert [p[2] for p in route] == ["Анапа", "Раевская", "Новороссийск"]


def test_list_of_places_is_not_a_route():
    """Перечисление адресатов предупреждения — не траектория."""
    text = "Анапа, Раевская, Новороссийск — опасность БПЛА"
    assert extract_route(text, parse(text), [ANAPA, RAEVSKAYA, NOVOROSSIYSK]) is None


def test_single_point_is_not_a_route():
    text = "Фиксация БПЛА, курс на Новороссийск"
    assert extract_route(text, parse(text), [NOVOROSSIYSK]) is None


def test_region_is_a_direction_not_a_waypoint():
    """«В сторону Белгородской области» — направление, а не точка на линии."""
    text = "Анапа: БПЛА идёт на Новороссийск, далее в сторону края"
    route = extract_route(text, parse(text), [ANAPA, KRAI, NOVOROSSIYSK])
    assert route is not None
    assert [p[2] for p in route] == ["Анапа", "Новороссийск"]


def test_allclear_has_nothing_to_draw():
    text = "Отбой опасности БПЛА, через час вернёмся"
    assert extract_route(text, parse(text), [ANAPA, NOVOROSSIYSK]) is None


def test_geocoder_miss_does_not_draw_across_the_country():
    """Плечо в тысячу километров — тёзка из другого края, а не маршрут."""
    far = Resolved("belogorsk_amur", "district", "Белогорск", 50.9, 128.5, "белогорск")
    text = "Анапа, БПЛА курс на Белогорск"
    assert extract_route(text, parse(text), [ANAPA, far]) is None


def test_namesake_leg_over_the_sea_is_not_a_route():
    """Хутор «Большой» рисовал линию в Сочи через Чёрное море: 289 км одним
    плечом. Настоящие маршруты корпуса идут по соседним районам."""
    bolshoy = Resolved("bolshoy", "place", "Большой", 46.8, 39.9, "большой")
    sochi = Resolved("sochi", "district", "Сочи", 43.6, 39.7, "сочи")
    text = "БПЛА идёт на Сочи, Большой в зоне внимания"
    assert extract_route(text, parse(text), [bolshoy, sochi]) is None


def test_address_list_zigzag_is_not_a_route():
    """«Армавир, Белоглинский, Новопокровский... в направлении X» — районы
    в порядке перечисления, а не полёта: извилистость до 8 против 1.0-1.1
    у настоящих маршрутов. Зигзаг не рисуем."""
    armavir = Resolved("armavir", "district", "Армавир", 44.99, 41.12, "армавир")
    beloglin = Resolved("beloglinsky", "district", "Белоглинский район", 46.07, 40.86, "белоглинский")
    novopokr = Resolved("novopokrovsky", "district", "Новопокровский район", 45.95, 40.70, "новопокровский")
    tbilis = Resolved("tbilissky", "district", "Тбилисский район", 45.36, 40.19, "тбилисский")
    text = ("Армавир, Белоглинский район, Новопокровский район, Тбилисский район "
            "опасность БПЛА в направлении Кропоткина")
    assert extract_route(text, parse(text), [armavir, beloglin, novopokr, tbilis]) is None
