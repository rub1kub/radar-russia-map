"""Тесты геокодера: украинские тёзки и разрешение по контексту.

    ingest/.venv/bin/python -m pytest tests/test_geocode.py -q

В справочнике 667 российских зон носят имена, производные от украинских
городов: 309 Николаевок, 39 Николаевских, 22 Черниговки, 15 Львовых,
11 Харьковских, Одесское, хутор Кривой Рог. Отбрасывать такие сообщения
целиком нельзя — Николаевка в Крыму, Черниговка в Приморье и станица
Львовская на Кубани шлют настоящие оповещения. Различать их приходится
здесь, по контексту сообщения.

Справочник открывается только на чтение: параллельно с тестами работает
подписка, и трогать базу на запись нельзя.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pipeline.db import DB_PATH
from pipeline.geocode import Geocoder

from conftest import bare_name


@pytest.fixture(scope="module")
def geocoder():
    if not DB_PATH.exists():
        pytest.skip("базы нет")
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    if connection.execute("SELECT COUNT(*) n FROM zones").fetchone()["n"] == 0:
        pytest.skip("справочник не построен: pipeline.gazetteer")
    return Geocoder(connection)


def region_of(geocoder: Geocoder, zone_id: str) -> str:
    """Регион, в котором лежит зона."""
    return geocoder.zones[geocoder.chain(zone_id)[-1]]["name_ru"]


def names(resolved) -> list[str]:
    """Имена зон без типового слова — почему так, см. tests/conftest.py."""
    return [bare_name(item.name) for item in resolved]


def names_of(geocoder: Geocoder, text: str, home: str | None = None) -> list[str]:
    """Имена зон сообщения так, как их видит конвейер: с разбором и drop_covered."""
    from pipeline.parse import parse

    observation = parse(text)
    if not observation.relevant:
        return []
    return names(geocoder.drop_covered(
        geocoder.resolve(observation.place_phrases, home=home)))


# --- Чужой город не садится на российского тёзку ----------------------------

@pytest.mark.parametrize("phrase", [
    "удар по Николаеву",
    "По Харькову работала авиация",
    "Взрывы в Одессе",
    # Село Львове Херсонской области на 2 565 жителей проходило порог
    # безвестности и забирало себе каждый пересказ удара по Львову.
    "Взрывы во Львове",
    # Хутор Кривой Рог Сосковского района Орловской области — единственная
    # зона за этим ключом, и «прилёт по Эпицентру в Кривом Роге» ложился на
    # неё событием severity 9.
    "Момент прилёта БПЛА по Эпицентру в Кривом Роге",
    "Прилет по Чернигову",
    "Под Киевом разбирали завалы",
])
def test_foreign_city_needs_russian_context(geocoder, phrase):
    """Без названного российского региона это Украина, а не хутор-тёзка."""
    assert geocoder.resolve([phrase]) == []


@pytest.mark.parametrize("phrase", [
    "Пуски БПЛА от Николаевской области",
    "Николаевская область пуски БПЛА",
    "От Харьковской области в сторону Белгородской",
    "пгт Затока, Одесская область, вылет реактивных фиксаций",
])
def test_foreign_oblast_is_not_a_russian_namesake(geocoder, phrase):
    """Все российские регионы в справочнике есть. Раз «X область» региона не
    даёт, названа чужая единица — и разбор не должен подбирать под неё
    Николаевский район Хабаровского края за 8 000 километров."""
    resolved = geocoder.resolve([phrase])
    assert not any(item.name.startswith(("Николаевск", "Николаевская",
                                         "Харьковск", "Одесск"))
                   for item in resolved)


def test_foreign_oblast_does_not_swallow_the_russian_one(geocoder):
    """Правило «чужой области» проверяет и голое прилагательное: справочник
    хранит ДНР и ЛНР республиками, а каналы пишут «Донецкая область»."""
    for phrase in ("Донецкая область", "Луганская область"):
        resolved = geocoder.resolve([phrase])
        assert [item.level for item in resolved] == ["region"], phrase

    # Новые регионы названы так же, как украинские города, но они наши.
    assert names(geocoder.resolve(["Херсонская область РФ"])) == ["Херсонская область"]
    assert names(geocoder.resolve(["Запорожская область РФ"])) == ["Запорожская область"]


# --- Российский тёзка с контекстом --------------------------------------------

@pytest.mark.parametrize("phrases,name,region", [
    (["Николаевка", "Белгородская область"], "Николаевка", "Белгородская область"),
    (["Николаевка, Береговое, Песчаное", "Республика Крым"],
     "Николаевка", "Республика Крым"),
    (["Черниговка", "Приморский край"], "Черниговка", "Приморский край"),
    (["Одесское", "Омская область"], "Одесское", "Омская область"),
    (["Харьковский", "Ростовская область"], "Харьковский", "Ростовская область"),
    (["Львовское", "Северский район"], "Львовское", "Краснодарский край"),
    (["Николаевск", "Волгоградская область"], "Николаевск", "Волгоградская область"),
])
def test_named_region_wins_for_a_namesake(geocoder, phrases, name, region):
    """Названный регион снимает омонимию: из 309 Николаевок выбирается та,
    что лежит внутри него."""
    hits = [item for item in geocoder.resolve(phrases) if item.name == name]
    assert hits, f"{name} не разрешилось"
    assert region_of(geocoder, hits[0].zone_id) == region


def test_derived_russian_names_are_untouched(geocoder):
    """Правило чужих городов ловит только сам город: стеммер даёт «николаев»,
    а Николаевка, Черниговка, Львовское и Харьковский — «николаевк»,
    «черниговк», «львовск», «харьковск». Отдельного списка исключений нет,
    и эти имена по-прежнему разрешаются без контекста."""
    for phrase in ("Николаевка", "Черниговка", "Одесское"):
        resolved = geocoder.resolve([phrase])
        assert names(resolved) == [phrase], phrase


# --- Контекст против самоподтверждения ---------------------------------------

@pytest.mark.parametrize("phrases,region", [
    (["Петропавловский район", "Воронежская область"], "Воронежская область"),
    (["Вяземский район", "Смоленская область"], "Смоленская область"),
    (["Тамбовский район", "Тамбовская область"], "Тамбовская область"),
    (["Дубенский район", "Тульская область"], "Тульская область"),
])
def test_district_lands_in_the_named_region(geocoder, phrases, region):
    """Район подтверждал сам себя, и названная рядом область его уже не
    перевешивала: «Петропавловский район. Воронежская область» уезжало в
    Петропавловский район Алтайского края."""
    hits = [item for item in geocoder.resolve(phrases) if item.level == "district"]
    assert hits
    assert region_of(geocoder, hits[0].zone_id) == region


def test_region_namesake_loses_to_the_stanitsa_it_shares_a_name_with(geocoder):
    """Станица Ленинградская — Кубань, но одноимённая область сама себя
    ставила контекстом и забирала бонус. Соседние станицы в том же
    сообщении говорят о географии больше, чем матч о самом себе."""
    resolved = geocoder.resolve(["Ленинградская", "Каневская", "Тихорецк",
                                 "Тимашевск"])
    hits = [item for item in resolved if item.name.startswith("Ленинградск")]
    assert hits
    assert region_of(geocoder, hits[0].zone_id) == "Краснодарский край"


@pytest.mark.parametrize("phrases,name,region", [
    # Повтор считался вторым свидетелем, и одноимённая область снова
    # подтверждала сама себя — ровно та ошибка, которую правило и снимает.
    (["Ленинградская", "Ленинградская", "Каневская", "Тихорецк"],
     "Ленинградская", "Краснодарский край"),
    (["Петропавловский район", "Петропавловский район", "Воронежская область"],
     "Петропавловский район", "Воронежская область"),
    (["Николаевка", "Николаевка", "Белгородская область"],
     "Николаевка", "Белгородская область"),
])
def test_repeated_name_is_not_a_second_witness(geocoder, phrases, name, region):
    """«Остальные слова» считаются по ключу, а не по позиции: дважды названное
    имя — тот же свидетель, а не второй."""
    hits = [item for item in geocoder.resolve(phrases) if item.name == name]
    assert hits, f"{name} не разрешилось"
    assert region_of(geocoder, hits[0].zone_id) == region


def test_lone_match_is_its_own_witness(geocoder):
    """Отказ от собственного вклада только разводит тёзок. Если других
    свидетельств в сообщении нет, у матча остаётся его собственное — иначе
    «Тихорецк» одним словом перестаёт геокодироваться вовсе. После сверки с
    ОКТМО это точное имя города, а не ошибочный алиас Тихорецкого района."""
    assert names(geocoder.resolve(["Тихорецк"])) == ["Тихорецк"]
    assert names(geocoder.resolve(["ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ в Шебекинском МО"])) \
        == ["Шебекинский район"]


# --- Обиходные названия групп районов ---------------------------------------

def test_priazovye_of_kuban_expands_to_its_districts(geocoder):
    """«Приазовское побережье» Кубани — это шесть азовских районов.

    Такого имени в справочнике нет: это физическая география. Раньше
    сообщение ложилось на весь край целиком.
    """
    names = names_of(geocoder, "🔴Краснодарский край Опасность БПЛА "
                               "Все приазовское побережье")
    assert "Ейский район" in names
    assert "Темрюкский район" in names
    assert "Сочи" not in names          # это Причерноморье, а не Приазовье


def test_priazovye_means_a_different_thing_in_each_region(geocoder):
    """Своё Приазовье у Кубани, Дона и Запорожья — путать их нельзя."""
    rostov = names_of(geocoder, "Ростовская область, Приазовье — опасность по БПЛА")
    assert "Азовский район" in rostov and "Неклиновский район" in rostov
    assert not any(name.startswith("Ейск") for name in rostov)

    zaporozhye = names_of(geocoder, "Запорожская область Приазовье опасность БПЛА")
    # По корню, а не по полному имени: типовое слово зоны справочник менял
    # уже дважды (округ -> район), а проверяется здесь не оно, а то, что
    # запорожское Приазовье развернулось в свои районы, а не в кубанские.
    assert any(name.startswith("Приазовский") for name in zaporozhye), zaporozhye


def test_group_without_a_region_is_not_guessed(geocoder):
    """Без названного региона группа не разворачивается: чьё Приазовье
    имелось в виду, из текста не следует."""
    assert names_of(geocoder, "Приазовье опасность по БПЛА") == []


def test_source_region_decides_the_group(geocoder):
    """Регион канала годится, когда текст региона не назвал."""
    krasnodar = region_zone(geocoder, "Краснодарский край")
    names = names_of(geocoder, "Приазовье опасность по БПЛА", home=krasnodar)
    assert "Приморско-Ахтарский район" in names


def test_prichernomorye_is_the_black_sea_coast(geocoder):
    """Причерноморье Кубани — черноморские курорты, а не азовские районы."""
    krasnodar = region_zone(geocoder, "Краснодарский край")
    names = names_of(geocoder, "Все причерноморье тревога по БПЛА", home=krasnodar)
    assert "Сочи" in names and "Новороссийск" in names
    assert "Ейский район" not in names


# --- Города новых регионов, которых нет под своим именем ---------------------

def test_city_of_a_new_region_is_not_a_namesake_in_siberia(geocoder):
    """«ДНР: Часов Яр, Артёмовск, Соледар» уезжало в Красноярский край.

    Набор UKR ADM2 даёт только округа, и в точном индексе под «Артемовск»
    стоял единственный тёзка — посёлок Курагинского района на 2179 жителей.
    """
    hits = geocoder.resolve(["Артёмовск ДНР УАБ тревога"])
    assert hits
    assert region_of(geocoder, hits[0].zone_id) == "Донецкая Народная Республика"


def test_named_region_still_wins_for_the_siberian_namesake(geocoder):
    """Псевдоним добавляет кандидата, а не подменяет: названный регион
    по-прежнему главнее."""
    hits = [item for item in
            geocoder.resolve(["Артемовск, Красноярский край — опасность БПЛА"])
            if item.level == "place"]
    assert hits
    assert region_of(geocoder, hits[0].zone_id) == "Красноярский край"


# --- Регион источника разводит тёзок ----------------------------------------

def region_zone(geocoder: Geocoder, name: str) -> str:
    return next(zone_id for zone_id, zone in geocoder.zones.items()
                if zone["level"] == "region" and zone["name_ru"] == name)


def test_source_region_resolves_homonyms(geocoder):
    """Крымская лента, написавшая «Белогорск», имеет в виду свой Белогорск,
    а не амурский, куда разбор уезжал по населению."""
    crimea = region_zone(geocoder, "Республика Крым")
    hits = geocoder.resolve(["Белогорск"], home=crimea)
    assert hits
    assert region_of(geocoder, hits[0].zone_id) == "Республика Крым"


def test_named_region_beats_source_region(geocoder):
    """Явно названный регион главнее прописки канала: та же лента вправе
    пересказать чужую сводку."""
    crimea = region_zone(geocoder, "Республика Крым")
    hits = [item for item in
            geocoder.resolve(["Белогорск", "Амурская область"], home=crimea)
            if item.level != "region"]
    assert hits
    assert region_of(geocoder, hits[0].zone_id) == "Амурская область"


def test_source_region_does_not_rescue_weak_matches(geocoder):
    """Дом — только для ранжирования выживших кандидатов. Обиходное слово
    без контекста по-прежнему отбрасывается, а не садится на местную
    деревню."""
    crimea = region_zone(geocoder, "Республика Крым")
    assert geocoder.resolve(["победа будет за нами"], home=crimea) == []


# --- Уточнение против отдельного места --------------------------------------

def test_named_region_after_district_is_only_a_qualifier(geocoder):
    """«Лискинский район, Воронежская область» — одно место, а не два.

    Наблюдение создавалось на обе зоны, и областные наблюдения из разных
    районов сливались в одно региональное событие: каждый источник сообщал
    про свой район, а счётчик подтверждений рос так, будто все говорили об
    одном. Область не теряется — карта поднимает событие по цепочке зон.
    """
    kept = geocoder.drop_covered(
        geocoder.resolve(["Лискинский район", "Воронежская область"])
    )
    assert [item.name for item in kept] == ["Лискинский район"]


def test_place_wins_over_its_district_and_region(geocoder):
    kept = geocoder.drop_covered(
        geocoder.resolve(["Колыбелка", "Лискинский район", "Воронежская область"])
    )
    assert [item.name for item in kept] == ["Колыбелка"]


def test_unrelated_zones_all_survive(geocoder):
    """Два соседних региона в одном сообщении — два разных места."""
    kept = geocoder.drop_covered(
        geocoder.resolve(["Ярославская область", "Костромская область"])
    )
    assert {item.name for item in kept} == {"Ярославская область", "Костромская область"}


def test_lone_region_is_kept(geocoder):
    kept = geocoder.drop_covered(geocoder.resolve(["Воронежская область"]))
    assert [item.name for item in kept] == ["Воронежская область"]


# --- Новые регионы и подписи районов ----------------------------------------

@pytest.mark.parametrize("region", [
    "Республика Крым",
    "Донецкая Народная Республика",
    "Луганская Народная Республика",
    "Херсонская область",
    "Запорожская область",
])
def test_new_regions_have_districts(geocoder, region):
    """Районов у этих регионов не было вовсе.

    Шесть с половиной тысяч НП висели прямо под регионом: сообщение про
    Бахчисарайский район не находило зоны, а карта не могла закрасить район.
    """
    zone_id = next(
        zid for zid, zone in geocoder.zones.items()
        if zone["level"] == "region" and zone["name_ru"] == region
    )
    districts = [
        zone for zone in geocoder.zones.values()
        if zone["level"] == "district" and zone["parent_id"] == zone_id
    ]
    assert len(districts) >= 10


def test_district_of_named_place_matches_it(geocoder):
    """Подпись района не должна съезжать на соседний.

    Полигон Анапского округа назывался «городской округ Новороссий», полигон
    Калининграда — «Королёв», полигон Ставрополя — «Тольятти».
    """
    for place, district in (
        ("Анапа", "Анапа"),
        ("Новороссийск", "Новороссийск"),
        ("Калининград", "Калининград"),
        ("Ставрополь", "Ставрополь"),
    ):
        resolved = geocoder.resolve([place])
        assert resolved, place
        chain = geocoder.chain(resolved[0].zone_id)
        names = [bare_name(geocoder.zones[zid]["name_ru"]) for zid in chain]
        assert district in names, f"{place}: {names}"


def test_every_district_knows_its_region(geocoder):
    """Родитель района нужен карте, а не только разбору.

    В тихом районе карта не знает его зоны — соответствие полигонов зонам
    строится из счётчиков обстановки. Родитель, записанный в файл полигонов,
    и позволяет показать обстановку по области вместо ленты всей страны.
    """
    orphans = [
        zone["name_ru"] for zone in geocoder.zones.values()
        if zone["level"] == "district" and not zone["parent_id"]
    ]
    assert orphans == []


def test_district_polygons_carry_their_region():
    import json
    from pipeline.db import ROOT

    payload = json.loads((ROOT / "public" / "data" / "districts.json").read_text(encoding="utf-8"))
    features = payload["features"]
    without = [f for f in features if not (f.get("properties") or {}).get("region")]
    assert not without, f"без региона: {len(without)} из {len(features)}"


def test_polygons_carry_their_zone():
    """Полигон без зоны — мёртвый полигон.

    По полю zone карта красит регион при тревоге и открывает карточку по
    клику. Поле дописывает справочник, а пересборка данных однажды его
    снесла у всех 2416 районов: штамп протух от правки самого скрипта.
    Теперь prepare-data переносит привязки из прошлого файла, и этот тест
    сторожит результат.
    """
    import json
    from pipeline.db import ROOT

    for name in ("regions.json", "districts.json"):
        features = json.loads((ROOT / "public" / "data" / name).read_text(encoding="utf-8"))["features"]
        without = [f for f in features if not (f.get("properties") or {}).get("zone")]
        assert not without, f"{name}: без зоны {len(without)} из {len(features)}"


def test_short_city_names_resolve(geocoder):
    """Трёхбуквенные города не гибнут на страже длины.

    Страж отсекает ключи короче четырёх букв как мусор — и вместе с мусором
    отсекал Уфу с её миллионом жителей: сообщения «Аэропорт Уфа, ограничения
    на ИВП» не находили зоны вовсе.
    """
    resolved = geocoder.resolve(["Уфа"])
    assert resolved
    assert "respublika_bashkortostan" in geocoder.zone_path(resolved[0].zone_id)


def test_bashkiria_is_bashkortostan(geocoder):
    """«Башкирия» — обиходное имя республики, каналы пишут только так."""
    resolved = geocoder.resolve(["Республика Башкирия Опасность по БПЛА"])
    assert resolved
    assert geocoder.zones[resolved[0].zone_id]["name_ru"] == "Республика Башкортостан"


def test_three_letter_villages_stay_below_threshold(geocoder):
    """Деревни в три буквы остаются за порогом: там по-прежнему шум."""
    assert geocoder.resolve(["Яя"]) == [] or all(
        (geocoder.zones[item.zone_id]["population"] or 0) >= 20_000
        for item in geocoder.resolve(["Яя"])
    )
