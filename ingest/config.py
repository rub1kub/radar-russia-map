"""Конфигурация ingest-слоя: креденшелы Telegram и список источников."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

INGEST_DIR = Path(__file__).resolve().parent
DATA_DIR = INGEST_DIR / "data"
SESSION_DIR = DATA_DIR / "sessions"
RAW_DIR = DATA_DIR / "raw"

load_dotenv(INGEST_DIR / ".env")

SESSION_NAME = os.getenv("TG_SESSION_NAME", "radar")


@dataclass(frozen=True)
class Source:
    """Публичный канал-источник оповещений.

    tier:
      official — МЧС, РСЧС, оперштабы: авторитетный источник, вес выше прочих;
      federal  — широкое покрытие, телеграфный формат, низкий шум;
      regional — узкая география (в основном Кубань), низкий шум;
      mixed    — оповещения вперемешку с новостями, нужен фильтр релевантности.
    """

    key: str
    username: str
    label: str
    tier: str = "regional"
    subscribers: int = 0
    region: str = "other"
    # Каналы одного владельца — не независимые свидетельства. Клоны вида
    # "Радар.ру | X область" на десяток регионов ведёт один оператор, и при
    # расчёте достоверности вся сеть должна считаться за один голос.
    network: str | None = None
    # Общеновостные каналы могут случайно совпасть со словами парсера.
    # Для них в события проходят только явные оперативные сообщения.
    strict_alerts: bool = False


# Все 12 каналов папки "Радары", проверены через folders.py 27.07.2026.
SOURCES: list[Source] = [
    Source("lpr1_treugolnik", "lpr1_treugolnik", "Lpr 1", "federal", 924_432, network="lpr1"),
    Source("vrv_radar", "vrv_radar", "Радар ВРВ", "federal", 269_737),
    Source("locatorru", "locatorru", "Локатор России", "federal", 197_377),
    Source("radar_rvk", "radar_rvk", "Радар РВК", "federal", 129_580),
    Source("lpr1_krasnodar", "lpr1_Krasnodar_alarm", "Краснодарский край оповещения", "regional", 95_761, network="lpr1"),
    Source("pra_vo_zn", "PRA_VO_ZN", "ПРАВО ЗНАТЬ", "mixed", 49_602),
    Source("rschs_krd_adygea", "radar_rschs_krd_adygea", "ЧП Кубань и Адыгея", "mixed", 17_884),
    Source("kubanoidici", "kubanoidici24838", "Кубанский Вестник", "regional", 9_909, region="krasnodar"),
    Source("krasnodarskiy_dozor", "krasnodarskiy_dozor_radar", "Краснодарский Дозор", "regional", 9_641, region="krasnodar"),
    Source("montkub", "montkub", "Мониторинг Кубани", "regional", 5_679),
    Source("kubtrevoga93", "kubtrevoga93", "Оповещения Кубани", "regional", 5_669, region="krasnodar"),
    Source("krasnodar_dozor", "krasnodar_dozor_radar", "Дозор Краснодара", "regional", 756),

    # --- Региональные ленты, найденные ingest/discover.py 27.07.2026 --------
    Source("radar_adygeya", "radar_adygeya", "Радар Адыгея", "regional", 18659, region="adygea", network=None),
    Source("radar_adygea", "radar_adygea", "Радар Адыгея", "regional", 928, region="adygea", network=None),
    Source("radarb31", "radarb31", "Радар Белгород", "federal", 139236, region="belgorod", network=None),
    Source("rodnoy_belgorod", "rodnoy_belgorod", "РОДНОЙ БЕЛГОРОД | РАДАР", "regional", 2316, region="belgorod", network="rodnoy"),
    Source("radar_beigorod", "radar_beIgorod", "Радар Белгород", "regional", 1732, region="belgorod", network=None),
    Source("radar_bryanskk", "radar_bryanskk", "Радар Брянск", "regional", 41579, region="bryansk", network=None),
    Source("radarbryansk2", "RadarBryansk2", "Наш Брянск. Радар", "regional", 4623, region="bryansk", network="nash"),
    Source("radarbransk", "radarBransk", "РАДАР БРЯНСКИЙ ФРОНТ", "regional", 1534, region="bryansk", network=None),
    Source("crimea_radar82", "crimea_radar82", "Радар Крым", "regional", 98945, region="crimea", network=None),
    Source("bad_news_crimea", "bad_news_crimea", "Радар Крым", "regional", 51324, region="crimea", network=None),
    Source("radarcrimea", "radarcrimea", "Радар КРЫМ (без рекламы)", "regional", 31590, region="crimea", network=None),
    Source("dpr_channel", "DPR_channel", "📢 Оповещения Радар ДНР", "regional", 16012, region="dnr", network=None),
    Source("bplaurz", "bplaurz", "Оповещение БПЛА/Ракетная опасность [Донецк/М", "regional", 193, region="dnr", network=None),
    Source("chs_dvina_donetsk_dnr", "chs_Dvina_Donetsk_DNR", "ЧС Донецк и ДНР | Оповещение тревог", "regional", 106, region="dnr", network=None),
    Source("radar_kaluga", "radar_kaluga", "Радар Калуга", "regional", 37531, region="kaluga", network=None),
    Source("radar_kras", "radar_kras", "Радар Краснодар", "regional", 57143, region="krasnodar", network=None),
    Source("first_radar", "first_radar", "Первый Радар | Краснодарский край", "regional", 31087, region="krasnodar", network=None),
    Source("krasnodar_nebo_radar", "krasnodar_nebo_radar", "Краснодарское Небо | РАДАР", "regional", 24332, region="krasnodar", network=None),
    Source("trevoga46", "TREVOGA46", "ВОЗДУШНАЯ ТРЕВОГА 46 | Курская область", "regional", 45417, region="kursk", network=None),
    Source("kursk_radar1", "kursk_radar1", "Курский Радар", "regional", 11747, region="kursk", network=None),
    Source("radar_kurskk", "radar_kurskk", "Радар Курск", "regional", 3096, region="kursk", network=None),
    Source("radar_lipetskk", "radar_lipetskk", "Радар Липецк", "regional", 83401, region="lipetsk", network=None),
    Source("radar_ru_lipetsk", "radar_ru_lipetsk", "Радар.ру | Липецкая область ( обстрелы, БПЛА", "regional", 19309, region="lipetsk", network="radar_ru"),
    Source("lipetsk_radar", "Lipetsk_radar", "Липецкая область • Оповещение", "regional", 15443, region="lipetsk", network=None),
    Source("radar_lnr", "radar_lnr", "181 Обстановка | Тревоги и опасности ЛНР", "regional", 6058, region="lnr", network=None),
    Source("radar1_lnr", "radar1_lnr", "Радар ЛНР", "regional", 2517, region="lnr", network=None),
    Source("luganskbpl", "luganskbpl", "БПЛА ЛУГАНСК | Внимание опасность.", "regional", 114, region="lnr", network=None),
    Source("radar_moscoww", "radar_moscoww", "Радар Москва", "federal", 467224, region="moscow", network=None),
    Source("trevoga_moscow", "trevoga_moscow", "Тревога Москва • РАДАР", "regional", 488, region="moscow", network=None),
    Source("radar_orei", "radar_oreI", "Радар Орел", "regional", 39483, region="orel", network=None),
    Source("oryoltrevoga", "oryoltrevoga", "ОРЁЛ ТРЕВОГА", "regional", 7546, region="orel", network=None),
    Source("radar_orel", "Radar_Orel", "Радар. Орёл и область", "regional", 3466, region="orel", network=None),
    Source("radarrussiia", "radarrussiia", "Радар по всей России | БПЛА", "federal", 1422164, region="other", network=None),
    Source("russiamonitoring_radar_bpla", "russiamonitoring_radar_bpla", "мониторинг.рф", "federal", 266259, region="other", network=None),
    Source("radar_plus_bpla", "radar_plus_bpla", "Радар ПЛЮС • БПЛА • Мониторинг обстановки 24", "federal", 176809, region="other", network=None),
    Source("radar_penzaa", "radar_penzaa", "Радар Пенза", "regional", 20865, region="penza", network=None),
    Source("radarpnzzz", "radarPNZzz", "Радар Пенза | Пензенская область", "regional", 1648, region="penza", network=None),
    Source("radarpnz", "radarPNZ", "Мониторинг БПЛА | Пензенская область", "regional", 1272, region="penza", network=None),
    Source("radar_sochi_krasnodar_rostov", "radar_sochi_krasnodar_rostov", "📡Радар Юг | Сочи , Ростов, Краснодар, Ставро", "federal", 111664, region="rostov", network=None),
    Source("radar_rostovv", "radar_rostovv", "Радар Ростов", "regional", 63012, region="rostov", network=None),
    Source("radar_tgn", "radar_tgn", "Таганрог РАДАР", "regional", 5004, region="rostov", network=None),
    Source("radar_ryazan62", "Radar_Ryazan62", "Радар. Рязань и область", "regional", 66464, region="ryazan", network=None),
    Source("radar_ryazan", "radar_ryazan", "Радар Рязань", "regional", 19150, region="ryazan", network=None),
    Source("radar_samaraa", "radar_samaraa", "Радар Самара", "regional", 77674, region="samara", network=None),
    Source("radar_samaraaaa", "radar_samaraaaa", "Радар БПЛА Самара📡", "regional", 24546, region="samara", network=None),
    Source("radar_smr", "radar_smr", "Радар БПЛА Самара 2📡", "regional", 1616, region="samara", network=None),
    Source("rada_saratov", "rada_saratov", "Радар Саратов", "federal", 255305, region="saratov", network=None),
    Source("radar_saratov_bpla", "radar_saratov_bpla", "Радар Саратов 🇷🇺", "regional", 24648, region="saratov", network=None),
    Source("radarsaratoov", "radarSaratoov", "Радар 'Саратов' 164", "regional", 13193, region="saratov", network=None),
    Source("radar_smolensk", "radar_smolensk", "Радар Смоленск", "regional", 66159, region="smolensk", network=None),
    Source("radar_ru_smolensk", "radar_ru_smolensk", "Радар.ру | Смоленская область ( обстрелы, БП", "regional", 26374, region="smolensk", network="radar_ru"),
    Source("smolensk_radar", "smolensk_radar", "Наш Смоленск. Радар", "regional", 5273, region="smolensk", network="nash"),
    Source("radar_tambov", "radar_tambov", "Радар Тамбов", "regional", 18428, region="tambov", network=None),
    Source("radar_tuia", "radar_tuIa", "Радар Тула", "federal", 100434, region="tula", network=None),
    Source("mytula_radar", "myTula_radar", "Моя Тула | Радар", "regional", 6981, region="tula", network="moy"),
    Source("rodnaya_tula", "rodnaya_tula", "РОДНАЯ ТУЛА | РАДАР", "regional", 1732, region="tula", network="rodnoy"),
    Source("radar_volgograd", "radar_volgograd", "Радар Волгоград", "regional", 94210, region="volgograd", network=None),
    Source("volgograd_radar", "volgograd_radar", "Радар Волгоград", "regional", 15783, region="volgograd", network=None),
    Source("myvolgograd_radar", "myVolgograd_radar", "Мой Волгоград | Радар", "regional", 5759, region="volgograd", network="moy"),
    Source("radar_voronezh", "radar_voronezh", "Радар Воронеж", "regional", 57067, region="voronezh", network=None),
    Source("radar_voron", "radar_voron", "Радар Воронежский", "regional", 13882, region="voronezh", network=None),
    Source("radar_alertss", "radar_alertss", "ЧП ВОРОНЕЖ НОВОСТИ", "regional", 2415, region="voronezh", network=None),

    # --- Аэропорты, 07.08.2026 ----------------------------------------
    # «Говорит Росавиация»: единственный первоисточник ограничений на
    # приём и выпуск — все прочие пересказывают его. Формат стабильный:
    # «Аэропорт X. ВВЕДЕНЫ/СНЯТЫ временные ограничения…»
    #
    # Второй ключ — тот же канал по числовому id: у сообщений совпадает
    # текст вплоть до футера «Говорит Росавиация | MAX», а уникального
    # содержимого ни у одной стороны нет (проверено по первым 40
    # сообщениям). Один network — иначе слияние считало бы эхо одного
    # источника за независимое подтверждение.
    Source("favt_info", "favt_info", "Говорит Росавиация", "official", 137_000, region="other", network="favt"),
    Source("ch1938794947", "c1938794947", "Говорит Росавиация (по id)", "official", 137_000, region="other", network="favt"),

    # --- Официальные ленты и вторая волна радаров, 27.07.2026 ---------
    Source("mchs31", "mchs31", "МЧС Белгородской области", "official", 18925, region="belgorod", network=None),
    Source("operativno31", "operativno31", "Оперштаб Белгородской области", "official", 14959, region="belgorod", network=None),
    Source("mchs_bryansk", "mchs_bryansk", "МЧС Брянской области", "official", 19399, region="bryansk", network=None),
    Source("gubernator_46", "gubernator_46", "Оперштаб Курской области 🇷🇺", "official", 184399, region="kursk", network=None),
    Source("rschs_moscow77", "rschs_moscow77", "РСЧС Москва", "official", 8048, region="moscow", network=None),
    Source("rschs_prigranichie1", "Rschs_prigranichie1", "РСЧС Приграни́чье", "official", 34841, region="other", network=None),
    Source("rschs_prigranichie", "rschs_prigranichie", "ЧС Приграни́чье", "official", 28430, region="other", network=None),
    Source("rschs_24", "rschs_24", "РСЧС 24/7", "official", 8514, region="other", network=None),
    Source("rschs_31", "RSCHS_31", "Оповещения РСЧС 🚨", "official", 5938, region="other", network=None),
    Source("mchs36", "mchs36", "МЧС Воронежской области", "official", 28294, region="voronezh", network=None),
    Source("radar_astrakhan", "radar_astrakhan", "🇷🇺 Радар Астрахани | Оповещение 🇷🇺", "regional", 6108, region="astrakhan", network=None),
    # Многорегиональные ленты: домашнего субъекта у них нет, и приписывать
    # ему сообщения без места нельзя. «Радар Юга» с region="astrakhan"
    # за неделю дал 63 фейковых события на Астраханской области — туда
    # падало всё, что не разобралось, включая сообщения про Адлер.
    Source("radar_yuga", "radar_yuga", "Радар 'Небо Юга' Ростов, Волгоград, Астрахан", "regional", 5915, region="other", network=None),
    Source("radar_ivanovo", "radar_ivanovo", "Радар Иваново и область", "regional", 9613, region="ivanovo", network=None),
    Source("ivanovo_radar", "ivanovo_radar", "Радар БПЛА | ЧП | Ивановская область", "regional", 1154, region="ivanovo", network=None),
    Source("radar_izhevsk", "radar_izhevsk", "Радар Ижевск", "regional", 1507, region="izhevsk", network=None),
    Source("kazan_radar", "kazan_radar", "Радар Казань | ПФО", "regional", 4427, region="kazan", network=None),
    Source("radar_ru_kursk", "radar_ru_kursk", "Радар.ру | Курская область ( обстрелы, БПЛА,", "regional", 933, region="kursk", network="radar_ru"),
    Source("safeskyrf_nizhniy_novgorod", "safeskyRF_Nizhniy_Novgorod", "Радар • Нижний Новгород", "regional", 1671, region="nnovgorod", network=None),
    Source("rodnoy_nijnii_novgorod", "rodnoy_nijnii_novgorod", "РОДНОЙ НИЖНИЙ НОВГОРОД | РАДАР", "regional", 516, region="nnovgorod", network="rodnoy"),
    Source("radar_rossia_bpla", "radar_rossia_bpla", "Мониторинг.ру | БПЛА", "regional", 45258, region="other", network=None),
    Source("bplarussiaru", "bplarussiaru", "БПЛА Россия - мониторинг", "regional", 27453, region="other", network=None),
    Source("radar_pskovv", "radar_pskovv", "Радар Псков", "regional", 5844, region="pskov", network=None),
    Source("radar_pskov", "radar_pskov", "Радар Псков | БПЛА", "regional", 1796, region="pskov", network=None),
    Source("radarsaratov", "radarSaratov", "Саратовский Радар", "regional", 3153, region="saratov", network=None),
    Source("radarsar64", "RadarSar64", "Радар Саратовской области", "regional", 1877, region="saratov", network=None),
    Source("sochinskoe_nebo_radar", "sochinskoe_nebo_radar", "Сочинское Небо | РАДАР", "regional", 11028, region="sochi", network=None),
    Source("radar_peterburg", "radar_peterburg", "Радар Питер и Ленинградская область", "federal", 151500, region="spb", network=None),
    Source("radar_piter", "radar_piter", "Радар Санкт-Петербург", "regional", 38524, region="spb", network=None),
    Source("stavropol_radar", "stavropol_radar", "Радар Ставропольского края", "regional", 27963, region="stavropol", network=None),
    Source("radarbpb", "radarbpb", "Радар Ставропольского Края", "regional", 1474, region="stavropol", network=None),
    Source("radarsverdlovska", "radarsverdlovska", "Радар Свердловская Область (БПЛА, ракеты, аэ", "regional", 4640, region="sverdlovsk", network=None),
    Source("radar_tver", "radar_tver", "Радар Тверь", "regional", 40038, region="tver", network=None),
    Source("mytver_radar", "myTver_radar", "Моя Тверь | Радар", "regional", 4427, region="tver", network="moy"),
    Source("radar_vladimir", "radar_vladimir", "Радар Владимир", "regional", 12937, region="vladimir", network=None),
    Source("radarvladimir", "radarvladimir", "Радар Владимирской области", "regional", 889, region="vladimir", network=None),
    Source("rodnoyi_volgograd", "rodnoyi_volgograd", "РОДНОЙ ВОЛГОГРАД | РАДАР", "regional", 1261, region="volgograd", network="rodnoy"),
    Source("voronezh_radar1", "voronezh_radar1", "Мой Воронеж | 112 РАДАР", "regional", 3402, region="voronezh", network="moy"),
    Source("radarrn136", "radarRN136", "ВОРОНЕЖСКИЙ РАДАР", "regional", 2603, region="voronezh", network=None),
    Source("radar_yaroslavl", "radar_yaroslavl", "Радар Ярославль", "regional", 51829, region="yaroslavl", network=None),
    Source("radaryr", "radarYR", "РАДАР. Ярославль. Кострома. Иваново", "regional", 4797, region="other", network=None),

    # --- Официальные каналы регионов и федеральный МЧС, 28.07.2026 ----------
    # Губернаторы и оперштабы объявляют тревогу и подтверждают последствия —
    # это тот слой, который отличает «канал написал» от «власть объявила».
    Source("mchs_official", "mchs_official", "МЧС России", "official", 78958, region="other", network=None),
    Source("mchs_kuban", "mchs_kuban", "МЧС Краснодарского края", "official", 7142, region="krasnodar", network=None),
    Source("opershtab23", "opershtab23", "Оперативный штаб — Краснодарский край", "official", 51377, region="krasnodar", network=None),
    Source("vvgladkov", "vvgladkov", "Гладков (Белгородская область)", "official", 433387, region="belgorod", network=None),
    Source("gusev_36", "gusev_36", "Гусев (Воронежская область)", "official", 127080, region="voronezh", network=None),
    Source("rostovregion", "RostovRegion", "Правительство Ростовской области", "official", 13906, region="rostov", network=None),
    Source("aksenov82", "Aksenov82", "Аксёнов (Крым)", "official", 141832, region="crimea", network=None),
    Source("razvozhaev", "razvozhaev", "Развожаев (Севастополь)", "official", 220990, region="sevastopol", network=None),
    Source("sevastopolofficial", "sevastopolofficial", "Правительство Севастополя", "official", 13025, region="sevastopol", network=None),
    # Не радар, а режим проезда по мосту: перекрытия идут рядом с угрозой и
    # разбираются как инфраструктурное сообщение, а не как тревога.
    Source("most_official", "most_official", "Крымский мост: оперативная информация", "official", 478182, region="crimea", network=None),

    # --- Федеральные агрегаторы --------------------------------------------
    Source("kupolrussia", "kupolrussia", "Купол России", "federal", 212299, region="other", network=None),
    # Ветки сети Lpr 1 по регионам. Сеть распознаётся графом перепостов, но
    # здесь она названа явно: одному оператору один голос, сколько бы веток
    # он ни завёл.
    Source("lpralarm", "LPRalarm", "LPR оповещения", "federal", 156724, region="other", network="lpr1"),
    Source("lpr1_bryansk", "lpr1_Bryansk_alarm", "Брянская область оповещения", "regional", 43106, region="bryansk", network="lpr1"),
    Source("lpr1_kursk", "lpr1_Kursk_alarm", "Курская область оповещения", "regional", 25211, region="kursk", network="lpr1"),
    Source("lpr1_rostov", "lpr1_Rostov_alarm", "Ростовская область оповещения", "regional", 50415, region="rostov", network="lpr1"),
    Source("lpr1_crimea", "lpr1_Crimea_Alarm", "Крым и Севастополь оповещения", "regional", 195617, region="crimea", network="lpr1"),
    Source("lpr1_kherson", "lpr1_Kherson_alarm", "Херсонская и Запорожская области оповещения", "regional", 26970, region="kherson", network="lpr1"),

    # --- Региональные ленты -------------------------------------------------
    Source("radar_rostov", "radar_rostov", "Радар Р.О | Ростовская область", "mixed", 24950, region="rostov", network=None),
    Source("radar_tatarstann", "radar_tatarstann", "Радар Татарстан", "regional", 44034, region="kazan", network=None),

    # --- Найдено ingest/discover.py 28.07.2026 по 68 региональным запросам ---
    # Из 189 проверенных лент отобраны 95. Не взяты: украинские каналы
    # воздушной тревоги (Одесса, «Повітряна тривога») — это чужая территория
    # и на нашей карте им места нет; рекламные («Рупор России»); а также
    # каналы, у которых имя не совпадает с содержимым («radar_kazan_bpla» с
    # заголовком «ЧП Челябинск новости») — такому источнику нельзя приписать
    # регион.
    #
    # Клоновые сети помечены явно: «Радар.ру | X», «РОДНОЙ X», «Мой X»,
    # «X Небо» — это по одному оператору на сеть, и голос у сети один.
    Source("rrpfo", "RRPFO", "Радар ПФО • Оповещения по Поволжью", "federal", 33666, region="other", network=None),
    Source("chuvashiya_radar", "chuvashiya_radar", "Радар Чебоксары", "regional", 26859, region="chuvashia", network=None),
    Source("radar_cheboksary", "Radar_Cheboksary", "Радар. Чебоксары и республика", "regional", 26288, region="chuvashia", network=None),
    Source("bpla_21", "bpla_21", "БПЛА Чувашия • Оповещения Чувашии", "regional", 2251, region="chuvashia", network=None),
    Source("radarcheboksary", "RadarCheboksary", "Радар Чебоксары | БПЛА/Ракеты", "regional", 825, region="chuvashia", network=None),
    Source("radar_ekaterinburgg", "radar_ekaterinburgg", "Радар Екатеринбург и Свердловская область", "regional", 26781, region="sverdlovsk", network=None),
    Source("radar_ekaterenburg", "Radar_ekaterenburg", "Локатор Екатеринбурга и региона", "regional", 492, region="sverdlovsk", network=None),
    Source("radar_196", "radar_196", "Радар Екатеринбург, Пермь, Челябинск", "regional", 363, region="other", network=None),
    Source("radar_chelyabinskkk", "radar_chelyabinskkk", "Радар Челябинск", "regional", 16320, region="chelyabinsk", network=None),
    Source("radar_174", "radar_174", "Радар БПЛА Челябинск 774", "regional", 381, region="chelyabinsk", network=None),
    Source("radar_omskkk", "radar_omskkk", "Радар Омск и область", "regional", 12146, region="omsk", network=None),
    Source("radar_vologda", "radar_vologda", "Радар Вологда и область", "regional", 20954, region="vologda", network=None),
    Source("radar_ulyanovsk", "radar_ulyanovsk", "Радар Ульяновск", "regional", 8904, region="ulyanovsk", network=None),
    Source("radar_orenburg", "radar_orenburg", "Радар Оренбург", "regional", 7080, region="orenburg", network=None),
    Source("orenburgradar", "orenburgradar", "Радар Оренбург", "regional", 1204, region="orenburg", network=None),
    Source("radar_156", "radar_156", "Радар БПЛА | Оренбургская область", "regional", 804, region="orenburg", network=None),
    Source("alertperm", "AlertPerm", "Радар Пермь l БПЛА", "regional", 1698, region="perm", network=None),
    Source("permkrairadar", "permkrairadar", "Радар Пермский край", "regional", 332, region="perm", network=None),
    Source("radarpermekb", "radarpermekb", "Радар Пермь,Екатеринбург, Челябинск, Тюмень", "regional", 580, region="other", network=None),
    Source("radar_tyumen", "radar_tyumen", "Радар Тюмень", "regional", 884, region="tyumen", network=None),
    Source("radar_astrakhann", "radar_astrakhann", "Радар Астрахань", "regional", 4832, region="astrakhan", network=None),
    Source("bpla_26", "bpla_26", "Беспилотная опасность и оповещения Ставропольс", "regional", 3327, region="stavropol", network=None),
    Source("stavropol_radarrr", "stavropol_radarrr", "Ставропольский Оповеститель", "regional", 861, region="stavropol", network=None),
    Source("radarpiter", "radarPiter", "РАДАР. Питер. Мурманск. Петрозаводск", "regional", 360, region="other", network=None),
    Source("opoveschenie_bpla", "opoveschenie_bpla", "Оповещения БПЛА Татарстан ⚠️", "regional", 707, region="kazan", network=None),
    Source("tokmak_alert", "tokmak_alert", "🔴 Токмак: Тревога БПЛА", "regional", 1469, region="zaporozhye", network=None),
    Source("pushilindenis", "PushilinDenis", "Пушилин Д.В.", "official", 81834, region="dnr", network=None),
    Source("radarrussia_bpla", "Radarrussia_bpla", "Радар по РФ! | БПЛА, ПВО, РО", "federal", 37547, region="other", network=None),
    Source("radar_bpla_trevoga", "Radar_bpla_trevoga", "Радар 24/7", "federal", 21681, region="other", network=None),
    Source("radar_rossii", "radar_rossii", "РАДАР ПО ВСЕЙ РОССИИ | БПЛА,Тревога,Ракетная о", "federal", 725, region="other", network=None),
    Source("bpla_russ", "BPLA_RUSS", "мониторинг. БПЛА", "federal", 733, region="other", network=None),
    Source("radar_sochiii", "radar_sochiii", "Радар Сочи(Адлер, Лазаревское, Сириус)", "regional", 64991, region="sochi", network=None),
    Source("sochiradar", "sochiradar", "📣 СОЧИ РАДАР", "regional", 35907, region="sochi", network=None),
    Source("sochi_radar", "sochi_radar", "Радар Сочи | Адлер", "regional", 5500, region="sochi", network=None),
    Source("radar23region", "radar23region", "Радар Сочи Краснодарский край 🚨🚨🚨 БПЛА", "regional", 643, region="sochi", network=None),
    Source("radar_engels", "radar_engels", "Радар Энгельс", "regional", 26934, region="saratov", network=None),
    Source("radar_krasdar", "radar_krasdar", "Краснодарский Радар", "regional", 23101, region="krasnodar", network=None),
    Source("krasnodarsirens", "krasnodarsirens", "Радар Краснодарского края", "regional", 13432, region="krasnodar", network=None),
    Source("radarkrasnodar", "RadarKrasnodar", "Наш Краснодар. Радар", "regional", 4823, region="krasnodar", network="nash"),
    Source("bpla_radar", "bpla_radar", "Радар023 | Черноморское побережье", "regional", 323, region="krasnodar", network=None),
    Source("novorossiysk_radar", "novorossiysk_radar", "Радар Новороссийск", "regional", 7994, region="krasnodar", network=None),
    Source("first_radar_novorossiysk", "first_radar_novorossiysk", "Первый Радар | Новороссийск, Геленджик", "regional", 6242, region="krasnodar", network=None),
    Source("novorossiyskiy_dozor_radar", "novorossiyskiy_dozor_radar", "Новороссийский Дозор | РАДАР", "regional", 5710, region="krasnodar", network=None),
    Source("anapa_alert", "anapa_alert", "Оповещение | Радар Анапа", "regional", 2179, region="krasnodar", network=None),
    Source("radaraanapa", "radaraanapa", "Радар Анапа | РСЧС | БПЛА, ракеты, самолёты", "regional", 1174, region="krasnodar", network=None),
    Source("radar_sevastopol_crimea", "radar_sevastopol_crimea", "Радар Севастополь | Крым", "regional", 9778, region="sevastopol", network=None),
    Source("sevairalarm", "SevAirAlarm", "Воздушная тревога Севастополь", "regional", 533, region="sevastopol", network=None),
    Source("sevlpr", "sevlpr", "Севастопольский треугольник lpr1 - Радар трево", "regional", 1893, region="sevastopol", network="lpr1"),
    Source("crimea_aa", "crimea_aa", "Радар Крым", "regional", 8272, region="crimea", network=None),
    Source("radar_crimeaa", "radar_crimeaa", "Радар Крым", "regional", 2872, region="crimea", network=None),
    Source("kr_trevoga", "kr_trevoga", "Керчь Крым - Ракетная опасность", "regional", 1136, region="crimea", network=None),
    Source("trevoga_prigran_bpla", "trevoga_prigran_bpla", "🚨Тревога Приграни́чья", "regional", 13503, region="other", network=None),
    Source("lipetsk_trevoga", "Lipetsk_trevoga", "Липецкая тревога", "regional", 7813, region="lipetsk", network=None),
    Source("alarmlip", "alarmLip", "‼️ Липецк. Воздушная тревога", "regional", 2912, region="lipetsk", network=None),
    Source("alarm48rus", "alarm48rus", "Воздушная тревога 48 ❌ Липецк БПЛА", "regional", 312, region="lipetsk", network=None),
    Source("radar_lipetssk", "radar_lipetssk", "Липецкий Радар", "regional", 740, region="lipetsk", network=None),
    Source("mylipetsk_radar", "myLipetsk_radar", "Мой Липецк | Радар", "regional", 5066, region="lipetsk", network="moy"),
    Source("rodnoylipetsk", "rodnoylipetsk", "РОДНОЙ ЛИПЕЦК | РАДАР", "regional", 2100, region="lipetsk", network="rodnoy"),
    Source("radarvoronezh31", "radarvoronezh31", "Воронежский радар", "regional", 5892, region="voronezh", network=None),
    Source("radar_voronezha", "radar_voronezha", "РОДНОЙ ВОРОНЕЖ | РАДАР", "regional", 1344, region="voronezh", network="rodnoy"),
    Source("orel_radar_bpla", "orel_radar_bpla", "Резонанс | Орёл БПЛА", "regional", 5055, region="orel", network=None),
    Source("orel_garnizon", "orel_garnizon", "Гарнизон: Орёл (Радар) 🇷🇺", "regional", 4881, region="orel", network=None),
    Source("radar_ru_orel", "radar_ru_orel", "Радар.ру | Орловская область ( обстрелы, БПЛА,", "regional", 8680, region="orel", network="radar_ru"),
    Source("rostovskoe_nebo_radar", "rostovskoe_nebo_radar", "Ростовское Небо | РАДАР", "regional", 7931, region="rostov", network="nebo"),
    Source("radardon", "radardon", "Радар Ростов", "regional", 2106, region="rostov", network=None),
    Source("myrostov_radar", "myRostov_radar", "Мой Ростов | Радар", "regional", 2574, region="rostov", network="moy"),
    Source("radar_ru_rostov", "radar_ru_rostov", "Радар.ру | Ростовская область ( обстрелы, БПЛА", "regional", 4609, region="rostov", network="radar_ru"),
    Source("nebo_taganrog", "nebo_taganrog", "Небо Таганрога | Радар, тревоги, оповещения | ", "regional", 2559, region="rostov", network="nebo"),
    Source("krd_radar", "krd_radar", "🚀 Радар Краснодар | Ростов", "regional", 4265, region="other", network=None),
    Source("reserv_ug", "reserv_ug", "📡Радар ЮГА📡: Ростов, Краснодар, Ставрополь, Во", "regional", 1259, region="other", network=None),
    Source("volgogradskoe_nebo_radar", "volgogradskoe_nebo_radar", "Волгоградское Небо | РАДАР", "regional", 6320, region="volgograd", network="nebo"),
    Source("volgograd_radar1", "volgograd_radar1", "Мой Волгоград | 112 РАДАР", "regional", 1792, region="volgograd", network="moy"),
    Source("mytambov_radar", "myTambov_radar", "Мой Тамбов | Радар", "regional", 4379, region="tambov", network="moy"),
    Source("rodnoy_tambov", "rodnoy_tambov", "РОДНОЙ ТАМБОВ | РАДАР", "regional", 896, region="tambov", network="rodnoy"),
    Source("radar_ru_tambov", "radar_ru_tambov", "Радар.ру | Тамбовская область ( обстрелы, БПЛА", "regional", 2750, region="tambov", network="radar_ru"),
    Source("mysamara_radar", "mySamara_radar", "Моя Самара | Радар", "regional", 4276, region="samara", network="moy"),
    Source("samara_radar", "samara_radar", "Радар Самарской области | ПФО", "regional", 1739, region="samara", network=None),
    Source("radarsam163", "radarsam163", "Радар Самара | БПЛА, Ракеты, Оповещения", "regional", 1555, region="samara", network=None),
    Source("radar_samaraskayaobl", "radar_samaraskayaobl", "Радар Самарская область", "regional", 845, region="samara", network=None),
    Source("radar_ru_tula", "radar_ru_tula", "Радар.ру | Тульская область ( обстрелы, БПЛА, ", "regional", 3636, region="tula", network="radar_ru"),
    Source("radar_ru_kaluga", "radar_ru_kaluga", "Радар.ру | Калужская область ( обстрелы, БПЛА,", "regional", 3575, region="kaluga", network="radar_ru"),
    Source("rodnaya_kalyga", "rodnaya_kalyga", "РОДНАЯ КАЛУГА | РАДАР", "regional", 968, region="kaluga", network="rodnoy"),
    Source("radar_smolensk0", "Radar_Smolensk0", "Радар. Смоленск и область", "regional", 3481, region="smolensk", network=None),
    Source("rodnoy_smolensk", "rodnoy_smolensk", "РОДНОЙ СМОЛЕНСК | РАДАР", "regional", 2233, region="smolensk", network="rodnoy"),
    Source("dorogobuzh_smolensk_radar", "dorogobuzh_smolensk_radar", "Радар Смоленск и Дорогобуж", "regional", 684, region="smolensk", network=None),
    Source("rodnoy_bryansk", "rodnoy_bryansk", "РОДНОЙ БРЯНСК | РАДАР", "regional", 2955, region="bryansk", network="rodnoy"),
    Source("radarbr32", "RadarBr32", "❗️ Радар Брянской области ❗️", "regional", 424, region="bryansk", network=None),
    Source("bel_radar_31", "Bel_Radar_31", "Радар Белгородской области", "regional", 1940, region="belgorod", network=None),
    Source("radar_belgorod131", "Radar_Belgorod131", "Радар Белгород Оповещения", "regional", 773, region="belgorod", network=None),
    Source("radar_ru_belgorod", "radar_ru_belgorod", "Радар.ру | Белгородская область ( обстрелы, БП", "regional", 600, region="belgorod", network="radar_ru"),
    Source("rodnaya_tver", "rodnaya_tver", "РОДНАЯ ТВЕРЬ | РАДАР", "regional", 773, region="tver", network="rodnoy"),
    Source("tverskoe_nebo_radar", "tverskoe_nebo_radar", "Тверское Небо | РАДАР", "regional", 747, region="tver", network="nebo"),
    Source("rodnaya_ryazan", "rodnaya_ryazan", "РОДНАЯ РЯЗАНЬ | РАДАР", "regional", 553, region="ryazan", network="rodnoy"),
    Source("penzaradar", "PenzaRadar", "Радар ПЕНЗА‼️", "regional", 1805, region="penza", network=None),
    Source("radar_77_76_37_44_52_33_35", "radar_77_76_37_44_52_33_35", "РАДАР Москва. Ярославль. Иваново. Кострома. Ни", "regional", 2230, region="other", network=None),

    # --- Найдено проходом по непокрытым субъектам, 29.07.2026 ----------------
    # Из 75 проверенных лент отобрано 17. Не взяты, как и прежде: украинские
    # каналы воздушной тревоги, реклама («Рупор России») и каналы, у которых
    # имя не совпадает с содержимым («Радар — новости Новосибирска» это ДПС
    # и городские новости, доля оповещений 0-3%).
    Source("radar_ru_russiaa", "radar_ru_russiaa", "Радар.ру • Приграничье • По всей России", "federal", 114836, region="other", network="radar_ru"),
    Source("radar_bashkortostan", "radar_bashkortostan", "Радар Башкортостан", "regional", 49076, region="bashkortostan", network=None),
    Source("radar_102", "radar_102", "Радар Башкортостан | БПЛА", "regional", 6667, region="bashkortostan", network=None),
    Source("radar_bashkortostan102", "Radar_Bashkortostan102", "Радар — Башкортостан!", "regional", 997, region="bashkortostan", network=None),
    Source("bshradar", "bshradar", "Радар Башкортостан | БПЛА (2)", "regional", 374, region="bashkortostan", network=None),
    Source("radar_mordoviya", "radar_mordoviya", "Радар Мордовия", "regional", 7353, region="mordovia", network=None),
    Source("internetsaransk", "internetsaransk", "Радар Саранска", "regional", 4341, region="mordovia", network=None),
    Source("radar_dagestann", "radar_dagestann", "Радар Дагестан", "regional", 4814, region="dagestan", network=None),
    Source("radarhmao", "radarhmao", "Радар ХМАО-Югра", "regional", 3059, region="khmao", network=None),
    Source("radar_komi", "radar_komi", "Радар Коми", "regional", 432, region="komi", network=None),
    Source("radarkomi", "radarkomi", "РАДАР КОМИ", "regional", 362, region="komi", network=None),
    Source("radarspbbpla", "radarspbbpla", "Радар БПЛА Санкт-Петербург и Ленобласть", "regional", 2354, region="spb", network=None),
    Source("radarpiterbpla", "radarpiterbpla", "Мониторинг Питер и Ленинградская область", "regional", 661, region="spb", network=None),
    Source("pforadar", "pforadar", "Купол Приволжья", "federal", 469, region="other", network=None),
    Source("radar_yuzniy", "radar_yuzniy", "Южный РАДАР", "regional", 357, region="other", network=None),
    Source("rschschyvashi", "RSCHSChyvashi", "РСЧС Чувашии", "official", 177, region="chuvashia", network=None),
    Source("sochi_online", "sochi_online", "Сочи Онлайн", "mixed", 112098, region="sochi", network=None),
    # Добавлен по просьбе владельца проекта: оповещения по приграничью
    # вперемешку с новостями, фильтр релевантности обязателен.
    Source("atypicalday", "atypicalday", "Приграничье", "mixed", 64473, region="other", network=None),

    # Добавлен по просьбе владельца проекта, 10.08.2026. Основная география
    # сообщений — Краснодарский край, формат — короткие оперативные отметки.
    Source(
        "bplagroza", "bplagroza", "БПЛА ПРОЛЕТЫ ОПАСНОСТЬ / ГРОЗА",
        "regional", 1071, region="krasnodar", network=None,
    ),

    # --- Новые региональные источники, проверены 10.08.2026 ----------------
    # Выборка последних 200 публикаций проверена через Telegram API. Каналы
    # властей и СМИ общеновостные, поэтому для них включён строгий фильтр.
    # «Радар. Москва и область» и «Рупор России» ведёт одна редакция — это
    # один голос при расчёте достоверности, несмотря на разные каналы.
    Source(
        "drozdenko_lo", "drozdenko_au_lo", "Александр Дрозденко",
        "official", 80_775, region="leningrad", strict_alerts=True,
    ),
    Source(
        "sitnikov_kostroma", "sk_sitnikov", "Сергей Ситников",
        "official", 7_366, region="kostroma", strict_alerts=True,
    ),
    Source(
        "evraev_yaroslavl", "evraevmikhail", "Михаил Евраев",
        "official", 17_879, region="yaroslavl", strict_alerts=True,
    ),
    Source(
        "shutkin_udmurtia", "AAShutkin", "Шуткин без шуток",
        "official", 9_896, region="udmurtia", strict_alerts=True,
    ),
    Source(
        "kirov_region", "kirovreg43", "Кировская область",
        "official", 5_474, region="kirov", strict_alerts=True,
    ),
    Source(
        "marpravda", "marpravda", "Марийская правда",
        "mixed", 2_551, region="mari_el", strict_alerts=True,
    ),
    Source(
        "regionvn53", "regionvn53", "ЧП53 Великий Новгород",
        "mixed", 44_484, region="novgorod", strict_alerts=True,
    ),
    Source(
        "nn52signal", "nn52signal", "ОПОВЕЩЕНИЕ 52",
        "regional", 122_162, region="nnovgorod",
    ),
    Source(
        "radar_moscow_99", "Radar_Moscow_99", "Радар. Москва и область",
        "regional", 43_174, region="moscow_oblast", network="rupor",
    ),
    Source(
        "ruporruss", "ruporruss", "Рупор России",
        "federal", 22_005, region="other", network="rupor",
    ),
]


def sources_from_env() -> list[Source]:
    """Переопределение списка через TG_SOURCES="key:username:label,..."."""
    raw = os.getenv("TG_SOURCES", "").strip()
    if not raw:
        return SOURCES

    parsed: list[Source] = []
    for chunk in raw.split(","):
        parts = [part.strip() for part in chunk.split(":")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        label = parts[2] if len(parts) > 2 and parts[2] else parts[1]
        parsed.append(Source(key=parts[0], username=parts[1].lstrip("@"), label=label))

    return parsed or SOURCES


def require_credentials() -> tuple[int, str]:
    """Читает api_id/api_hash из .env. Значения никогда не логируются."""
    api_id = os.getenv("TG_API_ID", "").strip()
    api_hash = os.getenv("TG_API_HASH", "").strip()

    if not api_id or not api_hash:
        sys.exit(
            "Не заданы TG_API_ID / TG_API_HASH.\n"
            f"Скопируйте {INGEST_DIR / '.env.example'} в {INGEST_DIR / '.env'} "
            "и впишите значения с https://my.telegram.org/apps"
        )

    if not api_id.isdigit():
        sys.exit("TG_API_ID должен быть числом.")

    return int(api_id), api_hash


def ensure_dirs() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)


def build_client():
    """Клиент Kurigram с сессией в ingest/data/sessions."""
    from pyrogram import Client

    api_id, api_hash = require_credentials()
    ensure_dirs()

    return Client(
        name=SESSION_NAME,
        api_id=api_id,
        api_hash=api_hash,
        workdir=str(SESSION_DIR),
        app_version="Radar Ingest 0.1",
        device_model="radar-ingest",
        system_version="macOS",
    )


def session_file() -> Path:
    return SESSION_DIR / f"{SESSION_NAME}.session"


def require_session() -> None:
    if not session_file().exists():
        sys.exit(
            "Сессия не найдена. Сначала авторизуйтесь вручную:\n"
            "  ingest/.venv/bin/python ingest/auth.py"
        )
