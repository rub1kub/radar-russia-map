"""Опознание районов по реестру ОКТМО.

    ingest/.venv/bin/python -m pytest tests/test_oktmo.py -q

Проверяется разбор реестра, приведение канцелярского имени к обиходному и
само опознание — на тех случаях, из-за которых модуль и появился.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pipeline.oktmo import Municipality, Registry, same_place, tidy


# --- Приведение имени -------------------------------------------------------

@pytest.mark.parametrize("official,expected", [
    ("Муниципальный округ город-курорт Анапа", "Анапа"),
    ("город-герой Новороссийск", "Новороссийск"),
    ("Кош-Агачский муниципальный район", "Кош-Агачский район"),
    ("Бабынинский муниципальный округ", "Бабынинский округ"),
    ("Муниципальный округ Балезинский район", "Балезинский район"),
    ("ЗАТО Северск", "Северск"),
    ("город Тверь", "Тверь"),
    ("Котлас", "Котлас"),
])
def test_official_name_becomes_spoken(official, expected):
    assert tidy(official) == expected


def test_bare_adjective_gets_its_unit():
    """«Владивостокский» — округ. Без слова единицы подпись читается обрубком."""
    assert tidy("Владивостокский") == "Владивостокский округ"


def test_same_place_ignores_unit_word():
    assert same_place("Бежаницкий район", "Бежаницкий округ")
    # Обрезанное имя из чужого набора границ — не то же самое, что полное.
    assert not same_place("городской округ Новороссий", "Новороссийск")


# --- Опознание --------------------------------------------------------------

REGISTRY = Registry([
    Municipality(("03", "501"), "Анапа",
                 frozenset({"анапа", "анапская", "витязево", "супсех", "гостагаевская"})),
    Municipality(("03", "720"), "Новороссийск",
                 frozenset({"новороссийск", "абрау-дюрсо", "раевская", "верхнебаканский"})),
    Municipality(("27", "701"), "Калининград", frozenset({"калининград"})),
    Municipality(("03", "601"), "Первомайский округ", frozenset({"первомайский"})),
    Municipality(("35", "701"), "Вологда", frozenset({"вологда"})),
    Municipality(("35", "619"), "Вологодский район",
                 frozenset({"молочное", "марфино", "непотягово", "фофанцево", "дубровское"})),
])


def test_composition_identifies_the_district():
    found = REGISTRY.match([("анапа", 90_000), ("анапская", 12_000), ("витязево", 5_000)])
    assert found and found[0].name == "Анапа"


def test_neighbour_does_not_win_on_a_single_shared_name():
    """Один общий тёзка — не довод: так «Новороссийск» и садился на Анапу."""
    assert REGISTRY.match([("раевская", 300), ("неизвестное", 100)]) is None


def test_city_okrug_written_as_one_settlement_is_found():
    """Реестр знает городской округ одним НП — самим городом.

    Требование трёх совпадений отвергало Калининград, Пермь и Тверь, и
    полигон Калининграда оставался под чужим именем «Королёв».
    """
    places = [("калининград", 470_000), ("первомайский", 400), ("прибрежный", 200)]
    found = REGISTRY.match(places, region="27")
    assert found and found[0].name == "Калининград"


def test_village_namesake_does_not_rename_a_district():
    """Деревня Первомайский внутри чужого полигона — не Первомайский округ."""
    places = [("непотягово", 300), ("первомайский", 150)]
    assert REGISTRY.match(places, region="03") is None


def test_strong_composition_beats_the_city_inside_it():
    """Полигон Вологодского района содержит саму Вологду со своим округом.

    Сотня совпавших сёл района весит больше, и район не должен стать городом.
    """
    places = [("вологда", 300_000), ("молочное", 5_000), ("марфино", 400),
              ("непотягово", 300), ("фофанцево", 200)]
    found = REGISTRY.match(places, region="35")
    assert found and found[0].name == "Вологодский район"


def test_unknown_places_match_nothing():
    assert REGISTRY.match([("зализнычне", 400), ("биленьке", 300)]) is None


# --- Реестр целиком ---------------------------------------------------------

def test_real_registry_covers_the_country():
    registry = Registry.load()
    assert len(registry.entries) > 2_000
    names = {entry.name for entry in registry.entries}
    assert {"Анапа", "Новороссийск", "Калининград"} <= names
    # Канцелярских обёрток в готовых именах остаться не должно.
    assert not [name for name in names if name.lower().startswith("муниципальн")]
