"""Маршрут, названный самим сообщением.

    ingest/.venv/bin/python -m pytest tests/test_routes.py -q

Линия рисуется только тогда, когда источник сам описал путь: «от Анапы
через Раевскую на Новороссийск». Склейка маршрутов из разных сообщений —
догадка, и в таблицу фактов она не попадает.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.geocode import Resolved
from pipeline.parse import parse
from pipeline.routes import SEA_OFFSET_KM, extract_route

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


# --- Акватории ---------------------------------------------------------
# Море лежит в справочнике уровнем «регион», но «в Азовское море» — это
# описанный путь, а не адресат предупреждения.
AZOV = Resolved("azovskoe_more", "region", "Азовское море", 46.26, 37.15, "море")
BERDYANSK = Resolved("berdyanskiy", "district", "Бердянский район",
                     46.75, 36.79, "бердянский")
SEAS = frozenset({"azovskoe_more", "chernoe_more"})


def test_sea_is_a_route_point_when_named_as_destination():
    """«Бердянский район пролёты БПЛА в Азовское море» — это маршрут.

    Без акваторий такие сообщения терялись целиком: за две недели корпуса
    их почти полторы тысячи.
    """
    text = "Бердянский район пролёты БПЛА в Азовское море"
    route = extract_route(text, parse(text), [BERDYANSK, AZOV], SEAS)
    assert route is not None
    assert [p[2] for p in route] == ["Бердянский район", "Азовское море"]


def test_sea_without_sea_ids_stays_a_region():
    """Без списка акваторий море остаётся обычным регионом и не рисуется."""
    text = "Бердянский район пролёты БПЛА в Азовское море"
    assert extract_route(text, parse(text), [BERDYANSK, AZOV]) is None


def test_far_sea_point_is_pulled_to_the_shore():
    """Центр акватории — сторона света, а не место.

    Чёрное море от Керченского полуострова — 230 км: линия «в направлении
    Чёрного моря» шла бы через весь Крым. Точка ставится в море рядом с
    берегом, в ту же сторону.
    """
    black = Resolved("chernoe_more", "region", "Чёрное море", 43.61, 34.5, "море")
    gornostaevka = Resolved("gornostaevka", "place", "Горностаевка",
                            45.32, 36.28, "горностаевка")
    text = ("Горностаевка тревога по БПЛА с северо-запада и далее "
            "в направлении Черного моря")
    route = extract_route(text, parse(text), [gornostaevka, black], SEAS)
    assert route is not None
    lat0, lon0 = route[0][0], route[0][1]
    lat1, lon1 = route[1][0], route[1][1]
    dx = (lon1 - lon0) * 111.0 * math.cos(math.radians((lat0 + lat1) / 2))
    dy = (lat1 - lat0) * 111.0
    assert math.hypot(dx, dy) == pytest.approx(SEA_OFFSET_KM, rel=0.02)
    # Направление сохранено: точка лежит юго-западнее берега, в море.
    assert lat1 < lat0 and lon1 < lon0
