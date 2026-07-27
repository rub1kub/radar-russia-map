"""Тесты парсера, геокодера, слияния и времени.

    ingest/.venv/bin/python -m pytest tests -q

Парсер и геокодер меняются постоянно — без тестов каждая правка была
непроверяемой. Здесь зафиксировано поведение на реальных формулировках
из каналов папки «Радары».
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pipeline.fuse import Fuser
from pipeline.geocode import Geocoder
from pipeline.parse import parse, strip_footer
from pipeline.textnorm import (expand_units, form_gender, name_gender, norm_key,
                               slugify, stem_key, stem_word, strip_unit)
from pipeline.timeutil import MSK, parse_utc, to_utc


# --- Нормализация -----------------------------------------------------------

def test_slug_is_stable_latin():
    assert slugify("Ростовская область") == "rostovskaya_oblast"
    assert slugify("Ханты-Мансийский АО") == "khanty_mansiyskiy_ao"


def test_norm_key_folds_yo_and_punctuation():
    assert norm_key("Тимашёвский район!") == "тимашевский район"
    assert norm_key("Ростов-на-Дону") == "ростов-на-дону"


def test_strip_unit_removes_type_word():
    assert strip_unit("Азовский район") == "азовский"
    assert strip_unit("городской округ Краснодар") == "краснодар"


def test_stem_key_folds_case_endings():
    assert stem_key(norm_key("Ростовской области")) == "ростовск област"
    assert stem_key(norm_key("Краснодарскому краю")) == "краснодарск кра"
    assert stem_key(norm_key("Тимашёвского района")) == "тимашевск район"


@pytest.mark.parametrize("name", ["азов", "ейск", "мга", "уфа"])
def test_stemmer_keeps_short_names_intact(name):
    """Огрызок из двух букв дал бы массовые ложные совпадения."""
    assert stem_word(name) == name


def test_norm_key_folds_e_variants():
    """В справочнике НП записан «Туапсэ», каналы пишут «Туапсе»."""
    assert norm_key("Туапсэ") == norm_key("Туапсе")
    assert norm_key("Зугрэс") == norm_key("Зугрес")


def test_expand_units_needs_a_dot_for_single_letters():
    """Точка отличает сокращение от предлога, поэтому «с Азовского» цело."""
    assert expand_units("ст. Динская") == "станица Динская"
    assert expand_units("х.Недвиговка") == "хутор Недвиговка"
    assert expand_units("Мангушский р-н") == "Мангушский район "
    assert expand_units("с Азовского моря") == "с Азовского моря"
    assert expand_units("и т.д. дальше") == "и т.д. дальше"


@pytest.mark.parametrize("word,gender", [
    ("раевской", "f"), ("попасная", "f"), ("мирное", "n"),
    ("донецкий", "m"), ("раменского", ""), ("воронежскими", ""),
])
def test_form_gender_admits_ignorance(word, gender):
    """У «-ого/-ому» и множественного числа рода нет — так и отвечаем."""
    assert form_gender(word) == gender


@pytest.mark.parametrize("name,gender", [
    ("Раевская", "f"), ("Раевский", "m"), ("Мирное", "n"), ("Анапа", "f"),
    ("Станица Луганская", "f"),
])
def test_name_gender_reads_last_word(name, gender):
    assert name_gender(name) == gender


# --- Футеры и релевантность -------------------------------------------------

@pytest.mark.parametrize("text", [
    "Анапский район\nОпасность по БПЛА\n\nМониторинг Кубани - Подписаться",
    "г. Краснодар тревога по БПЛА\n\n❗️Дозор Краснодара - подписаться",
    "Ростов-на-Дону - опасность.\n\n📡Локатор России - @locatorru",
])
def test_footer_is_stripped(text):
    body = strip_footer(text)
    assert "одписа" not in body.lower()
    assert "@" not in body


@pytest.mark.parametrize("footer", [
    "🚨РОДНОЙ БЕЛГОРОД\xa0 | РАДАР",
    "🌐 Радар Юг | 📲 МЫ в MAX",
    "✈️ Наш ТГ | 📲 Наш MAX",
    "💬Мы в Telegram🇷🇺мы в MAX",
    "📢 По данным каналов мониторинга",
    "Радар ЛНР 🟢 Чат | Обратная связь",
    "🎯Липецкая Область • Оповещения | Дать нам голоса | Обратная связь с командой.",
    "💬 Мониторинг PNZ | 💬 Мы в Максе",
    "ОРЁЛ ТРЕВОГА",
    "Telegram - t.me/radar_tgn",
])
def test_new_channel_footers_are_stripped(footer):
    """У каждого из 62 новых каналов свой футер, и почти в каждом есть
    топоним. Неснятый футер geocode принимает за место события."""
    assert strip_footer(f"Таганрог опасность по БПЛА\n\n{footer}") == "Таганрог опасность по БПЛА"


def test_long_line_mentioning_channel_is_not_a_footer():
    """Футер — короткая подпись. Абзац, где просто упомянута обратная связь,
    это текст сообщения, и вырезать его нельзя."""
    line = ("Для подписчиков, свидетелей пролетов и сбитий БПЛА работает "
            "обратная связь по кнопке снизу в описании канала")
    assert strip_footer(line) == line


def test_news_without_situation_is_irrelevant():
    observation = parse("Глава МИД заявил, что Зеленский не уйдёт от ответа")
    assert not observation.relevant


@pytest.mark.parametrize("text,threat", [
    ("Брянск, новостройка  БПЛА", "uav"),
    ("Суземка фпв на оптоволокне по жд", "fpv"),
    ("Рохманово Брянской области и близлежащие БПЛА дарст", "uav"),
    ("По трассе Новоайдар-Счастье на юг БПЛА Хорнет", "uav"),
    ("Бобров, Воронежская область - в небе БПЛА", "uav"),
])
def test_telegraph_format_without_verb_is_detection(text, threat):
    """Новые региональные ленты пишут телеграфом: место плюс тип борта, без
    единого глагола. Раньше такие строки уходили в нерелевантные."""
    observation = parse(text)
    assert observation.relevant
    assert observation.signal_type == "detection"
    assert observation.threat_type == threat


@pytest.mark.parametrize("text", [
    "Ростов-на-Дону\nНа подлете \nГотовность !!!!!!\nВсе от окон",
    "Погарский район стоп колёса все в укрытие",
    "НОВОРОССИЙСК\nУКРЫТИЯ НЕ ПОКИДАЕМ\nЕЩЕ ЛЕТЯТ\nОТ ОКОН!!!!",
    "Приготовиться к пускам Уаб самолётами противника",
    "Враг планирует ближе к ночи массовый запуск БПЛА в тыловые регионы",
])
def test_alarm_without_the_word_alarm(text):
    """Новые каналы объявляют тревогу, ни разу не сказав «тревога»."""
    assert parse(text).signal_type == "alarm"


@pytest.mark.parametrize("text,signal", [
    # Объявление отбоя — отбой.
    ("Отбой ракетной опасности", "allclear"),
    ("Сочи отмена тревоги по БПЛА", "allclear"),
    # Ожидание отбоя — всё ещё активная тревога.
    ("Объявлена РАКЕТНАЯ ОПАСНОСТЬ. Оставайтесь в укрытии до сигнала «Отбой»", "alarm"),
    ("Укрытия не покидаем до отбоя!", "alarm"),
    ("Никакого отбоя нет, угроза сохраняется", "danger"),
])
def test_waiting_for_allclear_is_not_allclear(text, signal):
    """Памятка «дождитесь сигнала Отбой» гасила тревогу в момент объявления."""
    assert parse(text).signal_type == signal


def test_household_signal_needs_named_threat():
    """«Пожар» и «обломки» сами по себе — городская хроника."""
    assert not parse("Крупный пожар в районе Таращанцев, 64. Очевидцы сообщают "
                     "о сильном возгорании").relevant
    observation = parse("Обломки беспилотников упали в станице Раевской, "
                        "произошло возгорание")
    assert observation.signal_type == "impact"
    assert observation.threat_type == "uav"


@pytest.mark.parametrize("text,threat", [
    ("Погарский район еще Дартс от гг в тыл", "uav"),
    ("Суземка фпв на оптоволокне", "fpv"),
    ("Предварительно, от 6 ракет типа: «Фламинго»(FP-5)", "rocket"),
    ("Приготовиться к пускам Уаб самолётами противника", "kab"),
])
def test_new_threat_names(text, threat):
    """Новые ленты называют борт по типу: Дартс, Хорнет, Фламинго."""
    assert parse(text).threat_type == threat


def test_count_in_new_units():
    """Счёт целей идёт не только в БПЛА: «единицы», «штуки», «ракеты»."""
    assert parse("БЭК в количестве от 6 единиц, БПЛА в районе 25 штук").target_count == 25
    assert parse("Предварительно, от 6 ракет типа «Фламинго»").target_count == 6
    # «Единицы техники» — это пожарные машины, а не цели.
    assert parse("На месте работали 125 специалистов и 39 единиц техники").target_count is None


@pytest.mark.parametrize("text", [
    # Чужая война: слова обстановки есть, а класть на карту России нечего.
    "В контролируемом ВСУ Херсоне зафиксированы пожары вследствие прилётов",
    # Памятка: объясняет, как себя вести, а не что происходит.
    "Уровни тревог. Какие бывают и что означают: внимание, опасность, тревога по БПЛА",
    "Уважаемые жители! Напоминаем: при получении сигнала «Ракетная опасность» "
    "проследуйте в ближайшее укрытие. Дождитесь отбоя тревоги!",
    # Сводка последствий: отчёт о вчерашнем налёте, а не текущая обстановка.
    "‼️ ВСУ продолжают атаки по ЛНР: Погибли два человека, ещё семь получили ранения\n\n"
    "➖ В Перевальске атакован гражданский грузовик, погиб 41-летний водитель\n\n"
    "➖ В Первомайске ударили по автомобилю газовой службы, водитель погиб\n\n"
    "➖ В Луганске атакован пассажирский микроавтобус, ранен 43-летний водитель. "
    "Пострадавшему оказана медицинская помощь, он госпитализирован в больницу\n\n"
    "Силами ПВО и мобильными огневыми группами сбито более 40 воздушных целей\n\n"
    "💬 Читать в Telegram | 💬 Читать в MAX | 🔗 Наш чат",
])
def test_retelling_is_not_an_observation(text):
    assert not parse(text).relevant


def test_alert_with_news_words_still_relevant():
    observation = parse("Белгородская область\nТревога по БПЛА")
    assert observation.relevant
    assert observation.signal_type == "alarm"


def test_long_launch_warning_naming_foreign_origin_survives():
    """Предупреждение о пусках называет чужой регион как точку старта и не
    влезает в лимит статьи. Правило про пересказ гасило его целиком, хотя это
    самое ценное сообщение — упреждающее."""
    observation = parse(
        "Враг готовит массированные пуски по двум направлениям.\n\n"
        "От Харьковской области в сторону Белгородской области с последующим "
        "движением на Воронежскую область.\n\n"
        "Из Полтавской области в направлении Луганской Народной Республики и "
        "далее на Ростовскую область.\n\n"
        "Общее количество планируемых к запуску БПЛА составляет от 200 единиц"
    )
    assert observation.relevant
    assert observation.signal_type == "alarm"


def test_denial_of_attack_is_not_an_impact():
    """«Склад не был атакован, враг сеет панику» — опровержение слуха.
    Слово «атакован» из отрицания давало событие с severity 9."""
    observation = parse(
        "Крупный региональный логистический центр «Wildberries» в Сарапуле, "
        "Удмуртской Республике не был атакован вражескими БПЛА. "
        "Враг пытается посеять панику."
    )
    assert observation.signal_type == "retracted"
    assert observation.severity == 0


@pytest.mark.parametrize("text", [
    "Фиксаций над нашим воздушным пространством не наблюдаем.\n"
    "В случае возникновения угрозы незамедлительно сообщим!",
    "На данный момент по обстановке тихо.\n"
    "При возникновении непосредственной угрозы оповестим немедленно.",
    "Пока по обстановке тихо, в случае появления угрозы незамедлительно сообщим",
])
def test_all_quiet_report_is_not_an_event(text):
    """Дежурный доклад о тишине — сообщение о том, что события НЕТ. Слова
    «фиксация» и «угроза» внутри него давали наблюдение severity 4-5."""
    assert not parse(text).relevant


def test_quiet_report_keeps_the_real_sighting_next_to_it():
    """Гасить доклад о тишине можно только когда тип угрозы не назван:
    вторая половина фразы бывает настоящим наблюдением."""
    observation = parse("На данный момент в регионе тихо.\n"
                        "Над Тульской и Липецкой областями фиксации БПЛА.")
    assert observation.relevant
    assert observation.signal_type == "detection"


def test_thousand_separator_is_not_a_target_count():
    """«2 426 130 единиц» — численность армии из указа, а не 130 целей.
    Счёт поднимал severity и клал на карту фантом."""
    observation = parse("Штатная численность Вооруженных Сил Российской Федерации "
                        "установлена в количестве 2 426 130 единиц")
    assert observation.target_count is None


def test_activity_without_named_threat_is_not_a_detection():
    """«Большой активности не проявляет» — про мессенджер, а не про небо.
    Активность считается сигналом только рядом с типом борта."""
    assert not parse("Контакты с Telegram по восстановлению доступа продолжаются, "
                     "но пока большой активности с его стороны нет").relevant
    assert parse("Попасная - Первомайск | Большая активность фпв").signal_type == "detection"


# --- Классификация ----------------------------------------------------------

@pytest.mark.parametrize("text,signal", [
    ("Краснодарский край\nОпасность по БПЛА", "danger"),
    ("Азов, фиксация БПЛА", "detection"),
    ("Ростов-на-Дону, работа ПВО по БПЛА", "intercept"),
    ("Ярославская область Отбой опасности по БПЛА", "allclear"),
    ("Новороссийск погодные условия\nБез паники", "retracted"),
    ("Темрюкский район\nМеры предосторожности!!!", "caution"),
])
def test_signal_classification(text, signal):
    assert parse(text).signal_type == signal


@pytest.mark.parametrize("text,threat", [
    ("Опасность по БПЛА", "uav"),
    ("Ракетная опасность", "rocket"),
    ("Угроза БЭК в акватории", "bek"),
])
def test_threat_classification(text, threat):
    assert parse(text).threat_type == threat


def test_allclear_has_zero_severity():
    assert parse("Отбой опасности по БПЛА").severity == 0


def test_target_count_extracted():
    assert parse("Ещё 2 БПЛА от Новобелая в сторону Воронежа").target_count == 2
    assert parse("Много фиксаций БПЛА").target_count == 10


# --- Геокодер (на реальном справочнике) -------------------------------------

@pytest.fixture(scope="module")
def geocoder():
    from pipeline.db import connect
    connection = connect()
    if connection.execute("SELECT COUNT(*) n FROM zones").fetchone()["n"] == 0:
        pytest.skip("справочник не построен: pipeline.gazetteer")
    return Geocoder(connection)


def test_geocoder_finds_place_inside_noise(geocoder):
    resolved = geocoder.resolve(["🔴Краснодар и ближайшие"])
    assert any("краснодар" in item.name.lower() for item in resolved)


def test_geocoder_resolves_homonym_by_context(geocoder):
    resolved = geocoder.resolve(["Успенское", "Краснодарский край"])
    names = {item.name for item in resolved}
    assert "Краснодарский край" in names
    chains = [geocoder.zone_path(item.zone_id) for item in resolved if item.name == "Успенское"]
    assert any("krasnodarskiy_kray" in " ".join(chain) for chain in chains)


def test_geocoder_ignores_event_vocabulary(geocoder):
    assert geocoder.resolve(["Опасность по БПЛА", "Меры безопасности"]) == []


@pytest.mark.parametrize("phrase,expected", [
    ("в Ростовской области", "Ростовская область"),
    ("по Краснодарскому краю", "Краснодарский край"),
    ("Тимашёвского района", "Тимашёвский район"),
    ("над Анапой", "Анапа"),
])
def test_geocoder_resolves_oblique_case(geocoder, phrase, expected):
    """Справочник в именительном падеже, сводки — в косвенном."""
    assert expected in {item.name for item in geocoder.resolve([phrase])}


def test_exact_form_wins_over_stemmed(geocoder):
    """Именительный падеж идёт точным индексом, косвенный — стеммированным,
    но обе формы приводят к одной зоне."""
    assert all(match.exact for match in geocoder._scan("Ростовская область"))
    assert not any(match.exact for match in geocoder._scan("Ростовской области"))
    assert ([item.zone_id for item in geocoder.resolve(["Ростовская область"])]
            == [item.zone_id for item in geocoder.resolve(["Ростовской области"])])


@pytest.mark.parametrize("phrase", [
    "была размещена в свободном доступе",   # городской округ Свободный
    "по старой трассе",                     # Старая Деревня
    "в сторону примерно",                   # посёлок Примерный
    "крайне много БПЛА",                    # посёлок Крайний
    "с Азовского моря",                     # станица Азовская
    "в северной части района",              # посёлок Северный
    "над нашим воздушным пространством",    # посёлок Наш
    "Берегите себя и своих близких",        # деревня Своя
])
def test_stemmed_match_does_not_invent_places(geocoder, phrase):
    """Стеммер схлопывает словоформы, а значит легко ловит обиходные слова."""
    assert geocoder.resolve([phrase]) == []


@pytest.mark.parametrize("phrase,expected", [
    ("ЛНР Белокуракинский район", "Луганская Народная Республика"),
    ("ДНР Горловка", "Донецкая Народная Республика"),
    ("От 7 БПЛА над Удмуртской Республикой", "Удмуртия"),
    ("Чувашская Республика - опасность по БПЛА", "Чувашия"),
])
def test_region_aliases_cover_new_regions(geocoder, phrase, expected):
    """Каналы новых регионов пишут аббревиатуру, справочник — полное имя."""
    assert expected in {item.name for item in geocoder.resolve([phrase])}


@pytest.mark.parametrize("phrase,expected", [
    ("Движение автотранспорта по Крымскому мосту перекрыто", "Керчь"),
    ("❗️Таманский полуостров", "Темрюкский район"),
])
def test_landmark_aliases_fall_back_to_containing_zone(geocoder, phrase, expected):
    """Мост и Тамань справочником не покрыты, но лежат внутри известных зон."""
    assert expected in {item.name for item in geocoder.resolve([phrase])}


def test_missing_district_falls_back_to_its_city(geocoder):
    """«Анапского района» в справочнике нет — есть город Анапа."""
    assert "Анапа" in {item.name for item in geocoder.resolve(["Анапский район"])}
    assert "Анапа" in {item.name for item in geocoder.resolve(["Анапского района"])}


def test_unit_marker_beats_same_named_region(geocoder):
    """«Ст. Воронежская» под Усть-Лабинском — станица, а не область за 700 км."""
    names = {item.name for item in geocoder.resolve(["Ст. Воронежская"])}
    assert names == {"Воронежская"}


def test_unit_marker_keeps_the_okrug_that_contains_the_city(geocoder):
    """Маркер не должен дробить одно место на две зоны: «г. Краснодар»."""
    plain = geocoder.resolve(["Краснодар"])
    marked = geocoder.resolve(["г. Краснодар"])
    assert [item.zone_id for item in plain] == [item.zone_id for item in marked]


@pytest.mark.parametrize("phrase,expected", [
    ("над Краснодаром", "городской округ Краснодар"),
    ("в сторону Новороссийска", "Новороссийск"),
    ("в Лисичанске", "Лисичанск"),
])
def test_noun_in_oblique_case_survives_junk_homonyms(geocoder, phrase, expected):
    """Обрезок совпал с полным именем зоны — это существительное, а не мусор.

    Заодно проверяется запасной стеммированный проход: в справочнике есть
    хутор с именем «Лисичанске», и он перехватывал точный проход.
    """
    assert expected in {item.name for item in geocoder.resolve([phrase])}


def test_gender_separates_homonyms_of_different_gender(geocoder):
    """«в станице Раевской» — не посёлок Раевский в Башкортостане."""
    names = {item.name for item in geocoder.resolve(
        ["Обломки упали в станице Раевской под Новороссийском"])}
    assert "Раевский" not in names
    assert "Новороссийск" in names


def test_major_city_beats_same_named_district_elsewhere(geocoder):
    """«Донецк» — миллионник в ДНР, а не городской округ в Ростовской области."""
    resolved = geocoder.resolve(["Донецк"])
    assert [item.name for item in resolved] == ["Донецк"]
    assert "donetskaya_narodnaya_respublika" in geocoder.zone_path(resolved[0].zone_id)


# --- Слияние ----------------------------------------------------------------

class FakeObservation:
    def __init__(self, signal="danger", threat="uav", severity=5):
        self.signal_type = signal
        self.threat_type = threat
        self.severity = severity
        self.direction_deg = None
        self.target_count = None


def add(fuser, minute, source, tier="federal", **kwargs):
    return fuser.add(
        raw_id=minute, source_key=source, tier=tier,
        moment=datetime(2026, 7, 27, 10, minute, tzinfo=timezone.utc),
        observation=FakeObservation(**kwargs),
        zone_path=["azovskiy_rayon", "rostov_oblast"],
        lat=47.1, lon=39.4, level="district",
    )


def test_same_zone_within_window_merges():
    fuser = Fuser()
    add(fuser, 0, "a")
    add(fuser, 3, "b")
    assert len(fuser.events) == 1
    assert fuser.events[0].source_count if hasattr(fuser.events[0], "source_count") else True
    assert len(fuser.events[0].sources) == 2


def test_far_apart_in_time_does_not_merge():
    fuser = Fuser()
    add(fuser, 0, "a")
    add(fuser, 40, "b")
    assert len(fuser.events) == 2


def test_confidence_grows_with_independent_sources():
    fuser = Fuser()
    add(fuser, 0, "a")
    one = fuser.events[0].confidence
    add(fuser, 1, "b")
    two = fuser.events[0].confidence
    add(fuser, 2, "c")
    assert one < two < fuser.events[0].confidence <= 1.0


def test_repeat_from_same_source_does_not_inflate_confidence():
    fuser = Fuser()
    add(fuser, 0, "a")
    first = fuser.events[0].confidence
    add(fuser, 1, "a")
    assert fuser.events[0].confidence == first


def test_allclear_resolves_open_event():
    fuser = Fuser()
    add(fuser, 0, "a")
    add(fuser, 5, "b", signal="allclear", severity=0)
    assert fuser.events[0].resolved_at is not None
    assert fuser.events[0].status(datetime(2026, 7, 27, 10, 6, tzinfo=timezone.utc)) == "resolved"


def test_status_fades_then_closes():
    fuser = Fuser()
    add(fuser, 0, "a")
    event = fuser.events[0]
    base = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    assert event.status(base + timedelta(minutes=10)) == "active"
    assert event.status(base + timedelta(minutes=60)) == "fading"
    assert event.status(base + timedelta(hours=4)) == "resolved"


# --- Время ------------------------------------------------------------------

def test_naive_legacy_value_is_read_as_msk():
    assert parse_utc("2026-07-27T12:42:09") == datetime(2026, 7, 27, 9, 42, 9, tzinfo=timezone.utc)


def test_aware_value_is_preserved():
    assert parse_utc("2026-07-27T09:42:09+00:00").hour == 9


def test_to_utc_normalizes_offset():
    moscow_noon = datetime(2026, 7, 27, 12, 0, tzinfo=MSK)
    assert to_utc(moscow_noon) == datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc)
