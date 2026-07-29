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
from pipeline.parse import candidate_phrases, foreign_side, parse, strip_footer
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


# --- Длина куска, в котором ищется топоним -----------------------------------

def test_wordy_alert_still_reaches_the_geocoder():
    """Оповещение РВК занимает 110 символов одной фразой. При пороге 90 оно
    молча теряло место, и тревога по району уходила на регион канала."""
    text = ("Воздушная тревога в связи с угрозой непосредственного удара "
            "беспилотных воздушных судов по Лискинскому району.")
    observation = parse(text)
    assert observation.signal_type == "alarm"
    assert any("Лискинскому" in phrase for phrase in observation.place_phrases)


def test_head_of_enumeration_is_not_lost_with_its_preamble():
    """В сводках первый элемент перечисления склеен с преамбулой, а остальные
    идут короткими кусками. Из-за порога голова списка пропадала, и регион,
    названный первым, единственный не попадал на карту."""
    phrases = candidate_phrases(
        "За прошедшую ночь силами противовоздушной обороны было уничтожено "
        "133 БПЛА над территориями Белгородской, Брянской, Курской областей")
    assert any("Белгородской" in phrase for phrase in phrases)
    assert "Брянской" in phrases


def test_chunk_beyond_the_limit_is_still_dropped():
    """Порог не снят, а поднят: в куске длиннее 120 символов посторонних слов
    больше, чем пользы, и стеммер начинает находить им тёзок в справочнике."""
    long_line = ("Уважаемые подписчики напоминаем всем жителям и гостям нашего "
                 "района о том что при получении любого сигнала следует "
                 "действовать спокойно и строго по инструкции")
    assert len(long_line) > 120
    assert candidate_phrases(long_line) == []


# --- Чужая сторона ----------------------------------------------------------

@pytest.mark.parametrize("text", [
    # Станица Львовская Северского района: под «львов\w*» в справочнике 54
    # российские зоны, под «николаев\w*» — 543. В корпусе рядом стояли
    # «меры предосторожности», но класс caution больше не событие — сигналом
    # кейса служит работа ПВО.
    "Львовская работа ПВО, меры предосторожности",
    "Николаевка, Береговое, Песчаное Республика Крым С моря тревога по БПЛА",
    "Винницкое, Кольчугино Республика Крым Фиксация БПЛА с севера",
    # «Новочеркасск» содержит «черкасск», «Полтавская» — станица Кубани.
    "Новочеркасск, работа ПВО по БПЛА",
    # Чужая область названа точкой старта, событие — на нашей территории.
    "Брянская область противник планирует запуски БПЛА из Черниговской и "
    "Сумской области в направлении. При фиксациях сообщим",
])
def test_russian_namesake_is_not_a_foreign_report(text):
    """Одного слова для опознания чужой стороны мало: у каждого украинского
    названия в справочнике есть российские тёзки."""
    assert not foreign_side(text)
    assert parse(text).relevant


@pytest.mark.parametrize("text", [
    "🔥В Одессе взрывы, на месте прилёта поднимается чёрный дым",
    "⚡Под Киевом ликвидировано возгорание трёх частных домов после удара "
    "БПЛА по выставке беспилотников",
    # Неоднозначное имя плюс оборот, кладущий событие на украинскую землю.
    "🔥В контролируемом ВСУ Херсоне зафиксированы пожары вследствие прилётов",
    "Минобороны показало кадры уничтожения судов ВСУ в Николаеве: в порту "
    "«Николаев» поражены два сухогруза",
])
def test_foreign_summary_is_still_dropped(text):
    """Убрать слова целиком было нельзя — украинские сводки вернулись бы."""
    assert foreign_side(text)
    assert not parse(text).relevant


@pytest.mark.parametrize("text", [
    "🔥Момент прилёта БПЛА по Эпицентру в Кривом Роге",
    "Ночью нанесён удар по объектам в районе Кривого Рога",
])
def test_foreign_place_is_caught_in_oblique_case(text):
    """Шаблон в именительном падеже пропускал «в Кривом Роге», и пересказ
    чужого удара становился прилётом с severity 9."""
    assert not parse(text).relevant


@pytest.mark.parametrize("text", [
    "Новоалексеевка Херсонская область РФ тревога по БПЛА",
    "Каховка, Херсонская область РФ БПЛА разведчик",
])
def test_russian_side_marker_clears_a_foreign_lookalike(text):
    """Ленты новых регионов сами помечают свою землю: «область РФ». Такая
    пометка снимает подозрение с имени, которое звучит как украинское."""
    assert not foreign_side(text)
    assert parse(text).relevant


# --- Оборонная новость ------------------------------------------------------

@pytest.mark.parametrize("text", [
    "❗️ Полки беспилотных систем созданы в составе ВМФ России. Один из таких "
    "полков был создан 1 июля на Северном флоте, сообщил главком ВМФ.",
    "❗️ До конца текущего года в состав ВМФ России войдёт первый атомный "
    "подводный ракетный крейсер с гиперзвуковыми ракетами «Циркон».",
    "Иран пригрозил нанести ракетный удар по Украине после атаки ВСУ на "
    "иранские торговые корабли в Каспийском море",
    "⚓️Сегодня День Военно-Морского Флота. В это непростое время, когда угрозы "
    "с моря требуют максимальной боеготовности, мы говорим спасибо морякам",
])
def test_armament_news_is_not_an_observation(text):
    """Тип угрозы в такой новости назван, глагола обстановки нет — и короткая
    строка уходила в detection/4, а событие ложилось на регион канала."""
    assert not parse(text).relevant


@pytest.mark.parametrize("text,signal", [
    ("Новороссийск готовность к комбинированной атаке БПЛА+БЭК. "
     "Хотят сорвать праздник ВМФ", "alarm"),
    ("Ракета уничтожена силами ВКС, летящая на Екатеринбург. "
     "Отбой ракетной опасности", "allclear"),
])
def test_service_branch_inside_a_real_alert_survives(text, signal):
    """Гасится новостной оборот вокруг рода войск, а не сами слова: «ВКС» и
    «ВМФ» стоят и в настоящих оповещениях."""
    observation = parse(text)
    assert observation.relevant
    assert observation.signal_type == signal


@pytest.mark.parametrize("text", [
    # Тип корабля — существительное, а не оборот: в приморских лентах он
    # стоит и в настоящем наблюдении. Ветка ловила эти слова напрямую.
    "Фиксация БЭК в направлении корвета на рейде Новороссийска",
    "Опасность атаки БПЛА в Севастополе, рядом фрегат и подводная лодка",
    # На Минобороны ссылается половина оповещений.
    "Ракетная опасность в Орловской области. Министр обороны подтвердил угрозу",
])
def test_hardware_noun_alone_does_not_kill_an_alert(text):
    """Оборонная новость опознаётся оборотом. Название корабля и ссылка на
    министра обороны не ловили в корпусе ни одного сообщения в одиночку, зато
    снимали с карты живое оповещение."""
    assert parse(text).relevant


def test_channel_branding_does_not_clear_a_foreign_report():
    """Вето российской стороны перебивает даже однозначно чужой город, поэтому
    оно должно опираться на пометку территории, а не на самоназвание ленты:
    футер «Оповещение Кубани» снимал подозрение со сводки об ударе по Одессе."""
    text = ("❗️ В течение дня Вооруженными Силами Российской Федерации "
            "продолжено нанесение ударов по портам Украины. Поражены: в порту "
            "«Черноморск» контейнерный терминал; южнее н.п. Одесса морское "
            "судно типа «балкер».\n❗️Оповещение Кубани")
    assert foreign_side(strip_footer(text))
    assert not parse(text).relevant


def test_territory_marker_still_clears_a_neighbouring_foreign_name():
    """Настоящая пометка территории вето сохраняет: «в сторону ЛНР, ДНР» —
    наша земля, а Днепропетровская область в том же тексте точка старта."""
    text = ("От Днепропетровской области много БПЛА в сторону ЛНР, ДНР, "
            "Ростовская область, Воронежская область")
    assert not foreign_side(text)
    assert parse(text).relevant


# --- Классификация ----------------------------------------------------------

@pytest.mark.parametrize("text,signal", [
    ("Краснодарский край\nОпасность по БПЛА", "danger"),
    ("Азов, фиксация БПЛА", "detection"),
    ("Ростов-на-Дону, работа ПВО по БПЛА", "intercept"),
    ("Ярославская область Отбой опасности по БПЛА", "allclear"),
    ("Новороссийск погодные условия\nБез паники", "retracted"),
])
def test_signal_classification(text, signal):
    assert parse(text).signal_type == signal


@pytest.mark.parametrize("text", [
    "Темрюкский район\nМеры предосторожности!!!",
    "Евпатория и близлежащие Республика Крым Внимание по БПЛА",
    # Ловушка: без перехвата классом caution «обломки» уходили бы через
    # WEAK_SIGNALS в impact, и памятка МЧС красила район взрывом.
    "Не подходите к обломкам БПЛА, звоните 112",
])
def test_caution_is_not_an_event(text):
    """Призыв к бдительности — не событие: класс caution на карту не идёт.

    Решение владельца проекта: «меры безопасности» и «внимание» ничего не
    говорят о происходящем, событий из них не делаем.
    """
    assert parse(text).relevant is False


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
    def __init__(self, signal="danger", threat="uav", severity=5, body=""):
        self.signal_type = signal
        self.threat_type = threat
        self.severity = severity
        self.direction_deg = None
        self.target_count = None
        self.body = body


def add(fuser, minute, source, tier="federal", zone_path=None, level="district",
        network=None, **kwargs):
    return fuser.add(
        raw_id=minute, source_key=source, tier=tier,
        moment=datetime(2026, 7, 27, 10, minute, tzinfo=timezone.utc),
        observation=FakeObservation(**kwargs),
        zone_path=zone_path or ["azovskiy_rayon", "rostov_oblast"],
        lat=47.1, lon=39.4, level=level, network=network,
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


# --- Адресность отбоя -------------------------------------------------------

def _fuser_with(threat, zone="azovskiy_rayon"):
    """Fuser с одним открытым событием заданной угрозы."""
    from datetime import datetime, timezone
    from pipeline.fuse import Fuser

    class Obs:
        signal_type = "danger"
        severity = 5
        direction_deg = None
        target_count = None
        def __init__(self, t): self.threat_type = t

    fuser = Fuser()
    fuser.add(raw_id=1, source_key="a", tier="federal",
              moment=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc),
              observation=Obs(threat), zone_path=[zone, "rostov_oblast"],
              lat=47.1, lon=39.4, level="district")
    return fuser


def _clear(fuser, threat, minute=5, zone="azovskiy_rayon"):
    from datetime import datetime, timezone

    class Obs:
        signal_type = "allclear"
        severity = 0
        direction_deg = None
        target_count = None
        def __init__(self, t): self.threat_type = t

    return fuser.add(raw_id=2, source_key="b", tier="federal",
                     moment=datetime(2026, 7, 27, 10, minute, tzinfo=timezone.utc),
                     observation=Obs(threat), zone_path=[zone, "rostov_oblast"],
                     lat=47.1, lon=39.4, level="district")


def test_allclear_clears_only_the_named_threat():
    """«Отбой ракетной опасности» не снимает тревогу по БПЛА."""
    fuser = _fuser_with("uav")
    _clear(fuser, "rocket")
    assert fuser.events[0].resolved_at is None


def test_allclear_clears_matching_threat():
    fuser = _fuser_with("rocket")
    _clear(fuser, "rocket")
    assert fuser.events[0].resolved_at is not None


def test_general_allclear_clears_everything():
    """Отбой без названной угрозы снимает всё в зоне."""
    fuser = _fuser_with("uav")
    _clear(fuser, "unknown")
    assert fuser.events[0].resolved_at is not None


def test_allclear_clears_event_of_unknown_threat():
    """Событие без опознанной угрозы гасится любым отбоем: иначе оно
    провисит до истечения срока, хотя канал его уже снял."""
    fuser = _fuser_with("unknown")
    _clear(fuser, "rocket")
    assert fuser.events[0].resolved_at is not None


# --- Вес фиксации -----------------------------------------------------------

def test_detection_outranks_a_warning():
    """«Фиксация» конкретнее «опасности»: борт уже видят, а не может прилететь.

    Раньше оба сигнала давали жёлтый, и 19% событий — подтверждённые
    наблюдения — терялись на фоне общей тревожности.
    """
    detection = parse("Азов, фиксация БПЛА")
    danger = parse("Краснодарский край, опасность по БПЛА")
    assert detection.signal_type == "detection"
    assert danger.signal_type == "danger"
    assert detection.severity > danger.severity


def test_detection_is_on_the_red_level():
    """Красный начинается с восьмёрки — тем же уровнем, что сбитие и взрыв."""
    assert parse("Азов, фиксация БПЛА").severity >= 8
    assert parse("Ростов-на-Дону, работа ПВО по БПЛА").severity >= 8


def test_detection_still_below_impact():
    """Взрыв остаётся тяжелее фиксации: это уже последствие, а не наблюдение."""
    assert parse("Взрыв в Таганроге").severity > parse("Азов, фиксация БПЛА").severity


def test_alarm_sits_between_warning_and_detection():
    warning = parse("Краснодарский край, опасность по БПЛА").severity
    alarm = parse("Тула, тревога по БПЛА").severity
    detection = parse("Азов, фиксация БПЛА").severity
    assert warning < alarm < detection


# --- Склейка: что считается подтверждением ----------------------------------

def test_district_and_region_stay_separate_events():
    """У района своё событие, у области своё.

    Слияние родственных зон ломало карту хуже всего остального: событие
    начиналось областной тревогой, впитывало сообщения про один город, и
    «уточняем характер взрыва» поднимало весь край до девятого уровня —
    сорок районов краснели из-за одного Новороссийска.
    """
    fuser = Fuser()
    add(fuser, 0, "a", zone_path=["rostov_oblast"], level="region")
    add(fuser, 6, "b", zone_path=["azovskiy_rayon", "rostov_oblast"])
    assert len(fuser.events) == 2
    assert {event.zone_id for event in fuser.events} == {"rostov_oblast", "azovskiy_rayon"}


def test_impact_in_one_town_does_not_repaint_its_region():
    """Взрыв в городе — событие города, а не области."""
    fuser = Fuser()
    add(fuser, 0, "a", zone_path=["rostov_oblast"], level="region", severity=7, signal="alarm")
    add(fuser, 4, "b", zone_path=["azovskiy_rayon", "rostov_oblast"], severity=9, signal="impact")
    region = next(event for event in fuser.events if event.zone_id == "rostov_oblast")
    assert region.signal_type == "alarm"
    assert region.severity == 7


def test_broader_observation_does_not_confirm_narrower_event():
    """Область не подтверждает город.

    «Все Приазовье Краснодарского края, опасность БПЛА» шло в счётчик
    события по Ейску как подтверждение именно Ейска — 305 таких случаев
    на корпусе. Теперь у области своё событие, а карта закрашивает регион
    по цепочке зон и без слияния.
    """
    fuser = Fuser()
    add(fuser, 0, "a", zone_path=["yeysk", "yeyskiy_rayon", "krasnodarskiy_kray"],
        level="place")
    add(fuser, 6, "b", zone_path=["krasnodarskiy_kray"], level="region")
    assert len(fuser.events) == 2
    assert all(event.independent_sources == 1 for event in fuser.events)


VERBATIM = (
    "За прошедшую ночь силами противовоздушной обороны было уничтожено "
    "328 украинских беспилотных летательных аппаратов над территориями "
    "Белгородской, Брянской и Воронежской областей"
)


def test_verbatim_repost_is_not_an_independent_voice():
    """Один текст, разосланный двумя лентами, — одно свидетельство.

    Граф перепостов ловит постоянные сети, но утренняя сводка расходится
    по лентам, между которыми связи нет, и каждая копия считалась
    отдельным подтверждением.
    """
    fuser = Fuser()
    add(fuser, 0, "a", body=VERBATIM)
    add(fuser, 3, "b", body=VERBATIM)
    assert len(fuser.events) == 1
    assert fuser.events[0].independent_sources == 1
    # Провенанс при этом сохраняется: видно, кто и что принёс.
    assert len(fuser.events[0].sources) == 2


def test_own_wording_stays_an_independent_voice():
    fuser = Fuser()
    add(fuser, 0, "a", body=VERBATIM)
    add(fuser, 3, "b", body=VERBATIM.replace("328", "17") + " и Ростовской области")
    assert fuser.events[0].independent_sources == 2


def test_short_identical_text_is_still_two_voices():
    """«Опасность по БПЛА» две ленты пишут одинаково просто потому,
    что иначе не скажешь."""
    fuser = Fuser()
    add(fuser, 0, "a", body="Ростовская область Опасность по БПЛА")
    add(fuser, 3, "b", body="Ростовская область Опасность по БПЛА")
    assert fuser.events[0].independent_sources == 2


# --- Сводки задним числом ---------------------------------------------------

@pytest.mark.parametrize("text", [
    "За прошедшую ночь силами ПВО уничтожено 328 БПЛА над территориями "
    "Белгородской, Брянской, Воронежской областей",
    "⚡️ В течение прошедшей ночи в период с 20.00 мск 25 июля до 8.00 мск "
    "26 июля дежурными силами ПВО перехвачено 47 БПЛА над Крымом",
    "Минувшей ночью над Ростовской областью уничтожены беспилотники",
])
def test_recap_of_finished_night_is_not_a_live_event(text):
    assert parse(text).relevant is False


@pytest.mark.parametrize("text", [
    "Ростовская область Опасность по БПЛА",
    "Отбой опасности БПЛА в Воронежской области",
])
def test_live_alert_survives_recap_filter(text):
    assert parse(text).relevant is True


# --- Подпись против цвета ---------------------------------------------------

# Полосы заливки на карте. Подпись события берётся из типа сигнала, цвет —
# из уровня, и человек читает их вместе: «Фиксация» жёлтым означает, что
# одно из двух врёт.
BANDS = {
    "detection": (8, 10), "intercept": (8, 10), "impact": (8, 10),
    "alarm": (6, 7), "danger": (4, 5), "infra": (0, 3),
}


@pytest.mark.parametrize("text", [
    "Борисоглебск, Воронежская область - ещё БПЛА.",
    "Раевская В Вашем направлении единичный БПЛА",
    "Новоазовск, в небе БПЛА на электротяге",
    "Зафиксированы БПЛА в сторону Серпухова со стороны Тульской области",
    "Шебекинский район, Белгородская область - высокая активность БПЛА",
    "Брянск, новостройка  БПЛА",
])
def test_sighting_is_red(text):
    """«Фиксация» — борт уже видят, и это красный уровень.

    Запасной разбор телеграфных строк ставил 4, и 142 события из 717
    подписывались «Фиксация», а красились жёлтым, как обычное
    предупреждение.
    """
    observation = parse(text)
    assert observation.signal_type == "detection"
    assert observation.severity >= 8


@pytest.mark.parametrize("text", [
    # Ровно тот текст, что повесил работу ПВО на Волгоградскую область:
    # слова «ПВО» и «БПЛА» есть, наблюдения нет, места нет — и событие
    # легло на регион самого канала.
    "❗️ Разведки США и Франции предоставили Украине данные о расположении "
    "систем ПВО на территории России, что позволило составлять маршруты "
    "полётов БПЛА и выбирать наиболее уязвимые цели. , — Financial Times",
    "⚡️Крупнейший НПЗ в России остановил работу — Reuters. Всего из-за атак "
    "дронов выведены из строя несколько заводов",
    "❗️Франция планирует передать Украине новейшую РСЗО Thundart",
])
def test_media_analysis_is_not_an_observation(text):
    """Газетная аналитика со ссылкой на издание — не наблюдение.

    Подпись «— Financial Times» в живом оповещении не встречается, а
    «ПВО» и «БПЛА» в такой статье есть всегда.
    """
    assert parse(text).relevant is False


def test_foreign_aircraft_watch_survives():
    """Наблюдение за чужой авиацией — это обстановка, а не новость.

    Более широкая пробная версия правила гасила и его: «НАТО» рядом с
    глаголом действия. Признак пересказа — подпись издания и разведка как
    субъект, а не упоминание альянса. Текст взят из корпуса дословно: в нём
    есть и объявленная опасность, и названные места.
    """
    text = ("⚡️Внимание, высокая активность НАТОвской авиации\n\n"
            "Сразу два самолёта дальней радиолокационной разведки Boeing "
            "RC-135W Rivet Joint и Bombardier Artemis ведут агрессивную "
            "разведку Крыма и Краснодарского края в акватории Чёрного моря.\n\n"
            "В зоне особого внимания Севастополь, западный Крым, Феодосия, "
            "Крымский мост и Новороссийск.\n\n"
            "В Крыму уже в третий раз объявлена беспилотная опасность, "
            "ожидаем провокаций.")
    assert parse(text).relevant is True


@pytest.mark.parametrize("text", [
    "⚡️10 погибших и 100 раненых в результате удара по выставке под Киевом",
    "6 человек погибли, 26 пострадали при атаке на предприятие в Кирове",
    "Кадры с места падения дрона на стоянку склада в Екатеринбурге",
])
def test_casualty_report_is_not_a_sighting(text):
    """Живая сводка не считает погибших — она предупреждает.

    Такие заголовки укладываются в две строки и проходили запасным
    разбором как фиксация, то есть красным на карту.
    """
    assert parse(text).relevant is False


@pytest.mark.parametrize("text,signal", [
    ("МЧС сообщает: беспилотная опасность на территории Липецкой области", "danger"),
    ("Экстренная информация РСЧС: воздушная тревога в Белгородской области", "alarm"),
])
def test_official_bump_stays_inside_its_band(text, signal):
    """Надбавка за официальность добавляет веса, но не меняет цвет.

    Без потолка «Тревога» от МЧС уходила в красную полосу, а «Опасность»
    с полусотней целей — в оранжевую, и подпись расходилась с заливкой.
    """
    observation = parse(text)
    assert observation.signal_type == signal
    low, high = BANDS[signal]
    assert low <= observation.severity <= high


@pytest.mark.parametrize("text", [
    "‼️ ВНИМАНИЕ! Идет массовая атака на регионы России. Для оперативного "
    "получения всех экстренных оповещений просьба подписаться на канал "
    "«Рупор России» и включить уведомления. ПОДПИСАТЬСЯ: https://t.me/x",
    "Этот канал спас миллионы жизней оповещая о тревогах, подпишитесь на канал",
    # Реклама сети «Город 24/7»: слова «ТРЕВОГА» и «РАКЕТНЫМ атакам» плюс
    # каталог городов. Разбор поверил и влепил ракетную опасность в 168
    # зон одним сообщением — от Абакана до Вологды.
    "❗️ВНИМАНИЕ, ТРЕВОГА❗️ Многие регионы РФ подверглись массовым РАКЕТНЫМ "
    "атакам от ВСУ. Ищите свой регион и подписывайтесь, сейчас крайне важно "
    "быть начеку: Москва 24/7 Питер 24/7 Абакан 24/7 Архангельск 24/7",
])
def test_channel_advertisement_is_not_an_alert(text):
    """Объявление о самом канале — не оповещение.

    Места в нём нет, и на карту оно попадало по региону источника: красная
    метка над областью, под которой не выделено ни одного района.
    """
    assert parse(text).relevant is False


def test_subscription_footer_does_not_kill_a_real_alert():
    """Обычная подпись в конце — не реклама: её снимает strip_footer."""
    observation = parse("Балашовский район Саратовская область Фиксация БПЛА\n\n"
                        "❗️Радар Саратов - @rada_saratov | Подписаться")
    assert observation.relevant
    assert observation.signal_type == "detection"


@pytest.mark.parametrize("text,signal,severity", [
    ("Новороссийск\nуточняем характер взрыва", "alarm", 7),
    ("Проверяем информацию о работе ПВО в Таганроге", "alarm", 7),
])
def test_unconfirmed_report_is_an_alarm_not_a_fact(text, signal, severity):
    """«Уточняем характер взрыва» — вопрос к самому себе, а не взрыв.

    Слово «взрыв» давало impact девятого уровня, высший на шкале; часом
    позже те же ленты написали, что по звукам всё штатно.
    """
    observation = parse(text)
    assert observation.signal_type == signal
    assert observation.severity == severity


def test_asserted_impact_with_hedged_details_stays_impact():
    """Оговорка про подробности не отменяет названный удар."""
    observation = parse(
        "В Белгороде беспилотник ВСУ атаковал грузовой автомобиль. "
        "По предварительной информации, пострадавших нет"
    )
    assert observation.signal_type == "impact"


# --- Эхо отменённой тревоги -------------------------------------------------

def test_late_alarm_after_allclear_does_not_reopen():
    """Медленная лента присылает тревогу уже после отбоя.

    Раньше такое сообщение заводило новое событие, следующая копия отбоя его
    закрывала, и в ленте выстраивался ряд одинаковых отбоев по одной зоне —
    до шести подряд.
    """
    fuser = Fuser()
    add(fuser, 0, "a")
    add(fuser, 5, "b", signal="allclear", severity=0)
    add(fuser, 7, "c")
    assert len(fuser.events) == 1
    assert fuser.events[0].resolved_at is not None


def test_stronger_report_after_allclear_opens_a_new_event():
    """Взрыв после отбоя — новое событие, а не эхо отменённого."""
    fuser = Fuser()
    add(fuser, 0, "a")
    add(fuser, 5, "b", signal="allclear", severity=0)
    add(fuser, 7, "c", signal="impact", severity=9)
    assert len(fuser.events) == 2


def test_alarm_long_after_allclear_is_a_new_event():
    """Через полчаса после отбоя это уже новая тревога, а не опоздание."""
    fuser = Fuser()
    add(fuser, 0, "a")
    add(fuser, 5, "b", signal="allclear", severity=0)
    add(fuser, 40, "c")
    assert len(fuser.events) == 2


def test_repost_does_not_refresh_the_event():
    """Перепост не приносит нового и время события двигать не должен.

    Сообщение, пересказанное через семь минут, делало событие «свежим» и
    оставляло зону гореть на карте, хотя новых наблюдений не было.
    """
    fuser = Fuser()
    text = (
        "Краснофлотское, Петропавловский район, Воронежская область — БПЛА вдоль "
        "границы с Верхнедонским районом Ростовской области и далее на Волгоградскую"
    )
    add(fuser, 0, "a", body=text)
    add(fuser, 7, "b", body=text)
    event = fuser.events[0]
    assert event.independent_sources == 1
    assert event.last_seen == datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)


def test_own_wording_does_refresh_the_event():
    fuser = Fuser()
    add(fuser, 0, "a", body="Краснофлотское, опасность по БПЛА")
    add(fuser, 7, "b", body="Краснофлотское, работа ПВО, слышны взрывы")
    assert fuser.events[0].last_seen == datetime(2026, 7, 27, 10, 7, tzinfo=timezone.utc)
