"""Посадочные регионов и городов, ежедневные сводки и sitemap.

Карта это одностраничное приложение: у неё один адрес и пустой div для
робота, и по запросу «тревога в Белгородской области» поисковику показать
нечего. Отсюда отдельные страницы субъектов и городов с живыми данными,
а также архив ежедневных сводок.

Раньше страницы собирал scripts/build-seo-pages.mjs, и они отличались
только названием региона да списком районов. Ровно такую штамповку
поисковики и называют дорвеями: страница, которой не было бы, если бы не
поиск. Поэтому сводка теперь настоящая — сколько сообщений пришло за месяц,
когда было последнее, какие районы называют чаще, в какие часы. Этих цифр
нет больше нигде: они собираются здесь же, из своего корпуса.

Из-за данных генератор переехал на сервер и на Python: база живёт там, и
только там сводка может быть свежей. Запускается после выкатки и по
таймеру:

    python -m scripts.seo_pages          # собрать страницы и sitemap
    python -m scripts.seo_pages --ping   # и позвать роботов через IndexNow
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.db import DB_PATH, ROOT
from pipeline.textnorm import short_name, slugify
from pipeline.timeutil import MSK, now_utc

SITE = "https://tihoenebo.com"
OUT = ROOT / "dist"
DATA = ROOT / "public" / "data"

# Окно сводки. Месяц — чтобы попадали и тихие регионы: за неделю у половины
# субъектов нет ничего, и страница снова стала бы пустым шаблоном.
# Сколько последних событий показывает посадочная страница.
RECENT_EVENTS = 6
WINDOW = timedelta(days=30)
TOP_DISTRICTS = 6
# Городская страница должна иметь собственные данные, а не одно случайное
# упоминание за месяц. Порог дал 150 устойчивых страниц на боевом корпусе:
# достаточно для длинного хвоста, но без сотен почти пустых дорвеев.
CITY_MIN_EVENTS = 5
CITY_MIN_POPULATION = 20_000
DIGEST_DAYS = 30
DIGEST_RECENT_EVENTS = 20
CITY_MANIFEST_VERSION = 3
# Сколько соседей по алфавиту показать в перелинковке. Робот ходит по
# ссылкам, и без них посадочные висят каждая сама по себе.
NEIGHBOURS = 6

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")

# Подпись одного события в списке последних (единственное число).
SIGNAL_ONE = {
    "detection": "фиксация борта",
    "intercept": "перехват",
    "impact": "взрыв",
    "alarm": "тревога",
    "danger": "опасность",
    "allclear": "отбой",
    "infra": "инфраструктура",
}
THREAT_ONE = {
    "uav": "БПЛА", "fpv": "FPV", "rocket": "ракета",
    "kab": "КАБ", "bek": "БЭК", "aviation": "авиация",
}

SIGNAL_WORDS = {
    "detection": "фиксации бортов",
    "intercept": "перехваты",
    "impact": "взрывы",
    "alarm": "объявленные тревоги",
    "danger": "предупреждения об опасности",
    "allclear": "отбои",
    "infra": "сообщения об инфраструктуре",
}

THREAT_WORDS = {
    "uav": "беспилотники",
    "fpv": "FPV-дроны",
    "rocket": "ракеты",
    "kab": "управляемые бомбы",
    "bek": "безэкипажные катера",
    "aviation": "авиация",
    # Не угрозы, а поводы сообщения: без своих слов оба типа печатались
    # на странице сырыми ключами — «airport — 22», «infra — 5».
    "airport": "ограничения в аэропортах",
    "infra": "объекты инфраструктуры",
}


def plural(count: int, one: str, few: str, many: str) -> str:
    mod100, mod10 = abs(count) % 100, abs(count) % 10
    if 11 <= mod100 <= 14:
        return many
    if mod10 == 1:
        return one
    if 2 <= mod10 <= 4:
        return few
    return many


def inflect(name: str) -> str:
    """Предложный падеж субъекта: «в Курской области», а не «в Курская область».

    Именительный в заголовке читается как машинный перевод и сразу выдаёт
    штампованную страницу. Правила идут от частного к общему: «Еврейская
    автономная область» иначе попадает под «...ая область» и теряет середину.
    """
    rules = [
        # Районы и округа — для городских страниц. Имя после типового слова
        # не склоняется: «в городском округе Краснодар».
        (r"^(.+)ий район$", r"\1ом районе"),
        (r"^(.+)ой район$", r"\1ом районе"),
        (r"^городской округ (?:город )?(.+)$", r"городском округе \1"),
        (r"^(.+)ий городской округ$", r"\1ом городском округе"),
        (r"^(.+)ий округ$", r"\1ом округе"),
        (r"^(.+)ий муниципальный округ$", r"\1ом муниципальном округе"),
        (r"^Республика (.+)$", r"Республике \1"),
        (r"^Чеченская Республика$", "Чеченской Республике"),
        (r"^(.+)ая автономная область$", r"\1ой автономной области"),
        (r"^(.+)ая народная республика$", r"\1ой народной республике"),
        (r"^(.+)ая республика$", r"\1ой республике"),
        (r"^(.+)ая область$", r"\1ой области"),
        (r"^(.+)ий край$", r"\1ом крае"),
        (r"^(.+)ой край$", r"\1ом крае"),
        (r"^(.+)ий автономный округ(.*)$", r"\1ом автономном округе\2"),
        (r"^(.+)ая$", r"\1ой"),
        (r"^(.+)ия$", r"\1ии"),
        (r"^(.+)я$", r"\1е"),
        (r"^(.+)ань$", r"\1ани"),
        (r"^(.+)а$", r"\1е"),
    ]
    for pattern, repl in rules:
        if re.match(pattern, name, re.IGNORECASE):
            return re.sub(pattern, repl, name, flags=re.IGNORECASE)
    if re.search(r"[ьй]$", name, re.IGNORECASE):
        return re.sub(r"[ьй]$", "е", name, flags=re.IGNORECASE)
    return f"{name}е"


def preposition(name: str) -> str:
    return "во" if name.lower().startswith("вл") else "в"


def normalized_name(name: str) -> str:
    return "".join(char for char in name.lower().replace("ё", "е")
                   if char.isalpha())


CITY_NAME_CORRECTIONS = {
    "Алешки": "Алёшки",
    "Артем": "Артём",
    "Артемовский": "Артёмовский",
    "Белозерка": "Белозёрка",
    "Ликино-Дулево": "Ликино-Дулёво",
    "Малоярославетс": "Малоярославец",
    "Орел": "Орёл",
    "Озеры": "Озёры",
    "Тюмен": "Тюмень",
}

CITY_ZONE_NAME_OVERRIDES = {
    "artemovskiy_okrug_donetskaya_narodnaya_respublika": "Бахмут",
    "bogorodskiy_gorodskoy_okrug_moskovskaya_oblast": "Ногинск",
    "lgovskiy_kurskaya_oblast": "Льгов",
}

# Два неверных URL успели попасть в первый IndexNow-пакет. Генератор меняет
# slug этих зон и оставляет на старых адресах noindex/canonical-переходы.
CITY_ZONE_SLUG_OVERRIDES = {
    "artemovskiy_okrug_donetskaya_narodnaya_respublika": "bakhmut",
    "bogorodskiy_gorodskoy_okrug_moskovskaya_oblast": "noginsk",
}
CITY_SLUG_REDIRECTS = {
    "artemovskiy": "bakhmut",
    "bogorodsk": "noginsk",
}

# Только варианты, которые уже встречаются в живых поисковых запросах.
# Не расширять список догадками: alternateName должен помогать реальному
# интенту, а не превращать посадочную в перечень ключевых слов.
PLACE_SEARCH_ALIASES = {
    "Санкт-Петербург": ("СПб", "Питер"),
}


def canonical_city_name(name: str) -> str:
    cleaned = re.sub(r"^г\.\s*", "", name, flags=re.IGNORECASE)
    return CITY_NAME_CORRECTIONS.get(cleaned, cleaned)


def matching_city_name(admin_name: str,
                       places: list[tuple[str, int]]) -> str | None:
    """Найти НП, имя которого соответствует основе административной зоны."""
    base = re.sub(
        r"\s+(?:муниципальный\s+|городской\s+)?округ$", "", admin_name,
        flags=re.IGNORECASE,
    )
    base_normalized = normalized_name(base)
    derived_stems = set()
    for suffix in ("овский", "евский", "инский", "ынский", "ский", "цкий"):
        if base_normalized.endswith(suffix):
            stem = base_normalized[:-len(suffix)]
            if len(stem) >= 4:
                derived_stems.add(stem)

    matches: list[tuple[int, int, int, str]] = []
    for place_name, population in places:
        if population < CITY_MIN_POPULATION:
            continue
        candidate = normalized_name(re.sub(r"^г\.\s*", "", place_name,
                                           flags=re.IGNORECASE))
        if candidate in derived_stems:
            rank, overlap = 4, len(candidate)
        elif candidate == base_normalized:
            rank, overlap = 3, len(candidate)
        else:
            overlaps = [
                len(stem) for stem in derived_stems | {base_normalized}
                if len(stem) >= 5
                and (candidate.startswith(stem) or stem.startswith(candidate))
            ]
            if not overlaps:
                continue
            rank, overlap = 2, max(overlaps)
        matches.append((rank, overlap, population, place_name))
    return canonical_city_name(max(matches)[3]) if matches else None


def city_name_for_area(admin_name: str,
                       places: list[tuple[str, int]],
                       zone_id: str | None = None) -> str | None:
    """Название города для городской/окружной зоны.

    Геометрия и события привязаны к ADM2, а запрос формулируют названием
    города. У явного «городского округа Краснодар» имя надёжно лежит в
    самом ADM2. У «Токмакского округа» оно принимается только когда в зоне
    есть достаточно крупный Токмак; иначе страница осталась бы про
    случайный крупнейший населённый пункт чужого района.
    """
    eligible = [(name, population) for name, population in places
                if population >= CITY_MIN_POPULATION]
    if not eligible:
        return None
    if zone_id in CITY_ZONE_NAME_OVERRIDES:
        return CITY_ZONE_NAME_OVERRIDES[zone_id]

    lowered = admin_name.lower()
    if re.search(r"\b(?:район|улус|кожуун)\b", lowered):
        return None

    explicit = re.sub(r"^городской округ (?:город )?", "", admin_name,
                      flags=re.IGNORECASE)
    if explicit != admin_name:
        # В исходнике встречалось усечённое «городской округ Лесосибирс»;
        # дочерний Лесосибирск надёжнее технического имени ADM2.
        matched = matching_city_name(explicit, places)
        if (matched and len(normalized_name(matched))
                >= len(normalized_name(explicit))):
            return matched
        return canonical_city_name(explicit)

    if "городской округ" in lowered:
        return canonical_city_name(max(eligible, key=lambda item: item[1])[0])

    # В части справочника слово «район» опущено: «Бугульминский» и
    # «Можайский». Возвращать прилагательное как город нельзя — принимаем
    # только совпавший дочерний НП.
    return matching_city_name(admin_name, places)


def city_slug(name: str) -> str:
    """Читаемый URL из проверенного имени, а не технического ID района."""
    return slugify(name).replace("_", "-")


CITY_LOCATIVE = {
    "Алёшки": "Алёшках",
    "Астрахань": "Астрахани", "Казань": "Казани", "Керчь": "Керчи",
    "Пермь": "Перми", "Рязань": "Рязани", "Сызрань": "Сызрани",
    "Тверь": "Твери", "Тюмень": "Тюмени", "Сочи": "Сочи",
    "Тольятти": "Тольятти", "Мытищи": "Мытищах",
    "Набережные Челны": "Набережных Челнах", "Шахты": "Шахтах",
    "Грязи": "Грязях", "Клинцы": "Клинцах", "Ливны": "Ливнах",
    "Луховицы": "Луховицах", "Люберцы": "Люберцах",
    "Озёры": "Озёрах", "Чашкинцы": "Чашкинцах",
    "Чебоксары": "Чебоксарах", "Великие Луки": "Великих Луках",
    "Нижний Новгород": "Нижнем Новгороде",
    "Великий Новгород": "Великом Новгороде",
    "Старый Оскол": "Старом Осколе", "Горячий Ключ": "Горячем Ключе",
    "Ростов-на-Дону": "Ростове-на-Дону",
    "Комсомольск-на-Амуре": "Комсомольске-на-Амуре",
    "Славянск-на-Кубани": "Славянске-на-Кубани",
    "Каменск-Шахтинский": "Каменске-Шахтинском",
    "Гусь-Хрустальный": "Гусь-Хрустальном",
    "Елец": "Ельце", "Малоярославец": "Малоярославце",
    "Орел": "Орле", "Орёл": "Орле",
}


def inflect_city(name: str) -> str:
    """Предложный падеж города без тяжёлой морфологической зависимости."""
    if name in CITY_LOCATIVE:
        return CITY_LOCATIVE[name]
    if name.endswith(("ское", "цкое")):
        return f"{name[:-2]}ом"
    if name.endswith(("ский", "цкий")):
        return f"{name[:-2]}ом"
    if name.endswith("ия"):
        return f"{name[:-1]}и"
    if name.endswith(("ая", "яя")):
        return f"{name[:-2]}{'ой' if name.endswith('ая') else 'ей'}"
    if name.endswith(("ый", "ой")):
        return f"{name[:-2]}ом"
    if name.endswith("ий"):
        return f"{name[:-2]}ем"
    if name.endswith(("а", "я")):
        return f"{name[:-1]}е"
    if name.endswith("ь"):
        return f"{name[:-1]}{'е' if name.endswith(('ль', 'оль')) else 'и'}"
    if name.endswith("й"):
        return f"{name[:-1]}е"
    if name.endswith(("о", "е", "и", "ы", "у", "ю")):
        return name
    return f"{name}е"


def location_phrase(name: str, page_kind: str) -> str:
    inflected = inflect_city(name) if page_kind == "city" else inflect(name)
    return f"{preposition(name)} {inflected}"


def moment(iso: str) -> str:
    """«29 июля, 18:26» по Москве — время на карте всегда московское."""
    stamp = datetime.fromisoformat(iso).astimezone(MSK)
    return f"{stamp.day} {MONTHS[stamp.month - 1]}, {stamp:%H:%M}"


def day_word(iso: str) -> str:
    stamp = datetime.fromisoformat(iso).astimezone(MSK)
    return f"{stamp.day} {MONTHS[stamp.month - 1]}"


def load_geo() -> tuple[list[dict], dict[str, list[str]]]:
    regions = json.loads((DATA / "regions.json").read_text(encoding="utf-8"))
    districts = json.loads((DATA / "districts.json").read_text(encoding="utf-8"))
    by_region: dict[str, list[str]] = {}
    for feature in districts["features"]:
        props = feature.get("properties") or {}
        if not props.get("region") or not props.get("name"):
            continue
        by_region.setdefault(props["region"], []).append(props["name"])
    return regions["features"], by_region


def collect_stats(connection: sqlite3.Connection) -> tuple[
        dict[str, dict], dict[str, dict], dict[str, dict]]:
    """Сводки регионов, городских зон и дней — одним проходом.

    Событие поднимается по всей цепочке зон, поэтому регион ищется в
    zone_path: тревога по посёлку считается и региону тоже.
    """
    current = now_utc()
    current_date = current.astimezone(MSK).date()
    since = (current - WINDOW).isoformat()
    rows = connection.execute(
        """
        SELECT e.zone_path, e.zone_id, e.signal_type, e.threat_type,
               e.first_seen_at, z.level, z.name_ru
        FROM events e LEFT JOIN zones z ON z.id = e.zone_id
        WHERE e.first_seen_at >= ?
        """,
        (since,),
    ).fetchall()

    # Уровни зон — чтобы отличить район/округ в середине пути от региона.
    district_names = {
        row["id"]: row["name_ru"]
        for row in connection.execute(
            "SELECT id, name_ru FROM zones WHERE level = 'district'")
    }

    def blank() -> dict:
        return {
            "events": 0, "days": set(), "last": None,
            "districts": Counter(), "signals": Counter(),
            "threats": Counter(), "hours": Counter(), "recent": [],
            "regions": Counter(),
        }

    stats: dict[str, dict] = {}
    city_stats: dict[str, dict] = {}
    daily_stats: dict[str, dict] = {}
    for row in rows:
        path = json.loads(row["zone_path"] or "[]")
        if not path:
            continue
        # Цепочка идёт снизу вверх: посёлок, район, регион. Регион — последний,
        # и по нему событие засчитывается субъекту целиком.
        region_id = path[-1]
        entry = stats.setdefault(region_id, blank())
        entry["events"] += 1
        stamp = datetime.fromisoformat(row["first_seen_at"]).astimezone(MSK)
        if stamp.date() == current_date:
            entry["today"] = entry.get("today", 0) + 1
        if (current - datetime.fromisoformat(row["first_seen_at"])).total_seconds() < 7200:
            entry["fresh"] = entry.get("fresh", 0) + 1
        entry["days"].add(stamp.date())
        entry["hours"][stamp.hour] += 1
        if entry["last"] is None or row["first_seen_at"] > entry["last"]:
            entry["last"] = row["first_seen_at"]
        entry["signals"][row["signal_type"]] += 1
        if row["threat_type"] and row["threat_type"] != "unknown":
            entry["threats"][row["threat_type"]] += 1
        # Название района берём у самой мелкой зоны события, но саму область
        # в список не пишем: «чаще всего называют Ростовскую область» на
        # странице Ростовской области — не информация.
        # Имя — короткое: «Белгород», а не «городской округ Белгород».
        # Страницу читает человек, а не реестр.
        place_name = short_name(row["name_ru"] or "")
        if row["level"] in ("district", "place") and place_name:
            entry["districts"][place_name] += 1
        entry["recent"].append(
            (row["first_seen_at"], place_name, row["signal_type"],
             row["threat_type"] or "unknown"))
        # Тот же учёт — каждой районной зоне пути: у города и района своя
        # страница со своей сводкой, а не пересказ областной.
        for zone_id in path[:-1]:
            if zone_id not in district_names:
                continue
            centry = city_stats.setdefault(zone_id, blank())
            centry["events"] += 1
            if stamp.date() == current_date:
                centry["today"] = centry.get("today", 0) + 1
            if (current - datetime.fromisoformat(row["first_seen_at"])).total_seconds() < 7200:
                centry["fresh"] = centry.get("fresh", 0) + 1
            centry["days"].add(stamp.date())
            centry["hours"][stamp.hour] += 1
            if centry["last"] is None or row["first_seen_at"] > centry["last"]:
                centry["last"] = row["first_seen_at"]
            centry["signals"][row["signal_type"]] += 1
            if row["threat_type"] and row["threat_type"] != "unknown":
                centry["threats"][row["threat_type"]] += 1
            if (row["level"] == "place" and place_name
                    and row["zone_id"] != zone_id):
                centry["districts"][place_name] += 1

        day_key = stamp.date().isoformat()
        dentry = daily_stats.setdefault(day_key, blank())
        dentry["events"] += 1
        dentry["last"] = max(dentry["last"] or row["first_seen_at"],
                             row["first_seen_at"])
        dentry["days"].add(stamp.date())
        dentry["hours"][stamp.hour] += 1
        dentry["signals"][row["signal_type"]] += 1
        if row["threat_type"] and row["threat_type"] != "unknown":
            dentry["threats"][row["threat_type"]] += 1
        dentry["regions"][region_id] += 1
        dentry["recent"].append(
            (row["first_seen_at"], place_name, row["signal_type"],
             row["threat_type"] or "unknown"))
    # Свежие сверху; страница показывает несколько последних — это и есть
    # то, что отличает её от вчерашней копии и от соседнего региона.
    for entry in stats.values():
        entry["recent"] = sorted(entry["recent"], reverse=True)[:RECENT_EVENTS]
    for entry in city_stats.values():
        entry["recent"] = sorted(entry["recent"], reverse=True)[:RECENT_EVENTS]
    for entry in daily_stats.values():
        entry["recent"] = sorted(
            entry["recent"], reverse=True)[:DIGEST_RECENT_EVENTS]
    return stats, city_stats, daily_stats


def build_city_catalog(connection: sqlite3.Connection, city_stats: dict[str, dict],
                       regions: dict[str, tuple[str, str]]) -> list[dict]:
    """Городские страницы, которые имеют и территорию, и живой материал."""
    places: dict[str, list[tuple[str, int]]] = {}
    for row in connection.execute(
            "SELECT parent_id, name_ru, population FROM zones "
            "WHERE level = 'place' AND parent_id IS NOT NULL"):
        places.setdefault(row["parent_id"], []).append(
            (row["name_ru"], int(row["population"] or 0)))

    candidates = []
    for row in connection.execute(
            "SELECT id, parent_id, name_ru FROM zones WHERE level = 'district'"):
        stats = city_stats.get(row["id"])
        region = regions.get(row["parent_id"])
        if not stats or stats["events"] < CITY_MIN_EVENTS or not region:
            continue
        name = city_name_for_area(
            row["name_ru"], places.get(row["id"], []), row["id"])
        if not name:
            continue
        region_name, region_slug = region
        candidates.append({
            "zone_id": row["id"],
            "name": name,
            "admin_name": row["name_ru"],
            "region_id": row["parent_id"],
            "region_name": region_name,
            "region_slug": region_slug,
            "slug": city_slug(name),
            "stats": stats,
        })

    # Одинаковые названия есть в разных субъектах. URL без региона оставляем
    # только уникальным городам; коллизии получают понятный суффикс субъекта.
    slug_counts = Counter(item["slug"] for item in candidates)
    for item in candidates:
        if slug_counts[item["slug"]] > 1:
            item["slug"] = f'{item["slug"]}-{item["region_slug"]}'
    return sorted(candidates, key=lambda item: (item["name"], item["region_name"]))


def persistent_city_catalog(current: list[dict], city_stats: dict[str, dict],
                            manifest_path: Path) -> list[dict]:
    """Сохранить опубликованные городские URL, даже когда месяц стал тихим.

    Порог активности нужен только для первого появления. После индексации
    страница продолжает обновляться и честно показывает ноль, а не исчезает
    из выдачи из-за скользящего окна.
    """
    fields = (
        "zone_id", "name", "admin_name", "region_id", "region_name",
        "region_slug", "slug",
    )
    required = set(fields)
    previous: list[dict] = []
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            items = (loaded.get("cities")
                     if isinstance(loaded, dict)
                     and loaded.get("version") == CITY_MANIFEST_VERSION
                     else None)
            if isinstance(items, list):
                previous = [item for item in items
                            if isinstance(item, dict) and required <= item.keys()]
            elif isinstance(loaded, list):
                print("SEO: обновляю старый манифест городов")
        except (OSError, json.JSONDecodeError):
            print("SEO: манифест городов повреждён, собираю его заново")

    merged = {item["zone_id"]: dict(item) for item in previous}
    for item in current:
        fresh = dict(item)
        forced_slug = CITY_ZONE_SLUG_OVERRIDES.get(item["zone_id"])
        if forced_slug:
            fresh["slug"] = forced_slug
        # URL уже опубликованной зоны не меняем при обычной правке имени.
        elif item["zone_id"] in merged:
            fresh["slug"] = merged[item["zone_id"]]["slug"]
        merged[item["zone_id"]] = fresh

    # Новый одноимённый город получает региональный суффикс; старый URL при
    # этом остаётся стабильным. Последний числовой суффикс — страховка от
    # двух административных зон с одинаковым именем в одном субъекте.
    used: set[str] = set()
    result = []
    for item in sorted(merged.values(),
                       key=lambda value: (value["name"], value["region_name"])):
        candidate = item.get("slug") or city_slug(item["name"])
        if candidate in used:
            candidate = f'{city_slug(item["name"])}-{item["region_slug"]}'
        serial = 2
        base = candidate
        while candidate in used:
            candidate = f"{base}-{serial}"
            serial += 1
        item["slug"] = candidate
        item["stats"] = city_stats.get(item["zone_id"])
        used.add(candidate)
        result.append(item)

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({
            "version": CITY_MANIFEST_VERSION,
            "cities": [
                {key: item[key] for key in fields}
                for item in result
            ],
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def digest_history(current_days: list[str], daily_stats: dict[str, dict],
                   digest_dir: Path) -> dict[str, dict]:
    """Манифест сохраняет готовые дневные страницы за пределами окна БД."""
    digest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = digest_dir / "manifest.json"
    history: dict[str, dict] = {}
    latest = max(current_days, default="")
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for day_key, item in loaded.items():
                    try:
                        date.fromisoformat(day_key)
                    except ValueError:
                        continue
                    if (isinstance(item, dict)
                            and isinstance(item.get("events"), int)
                            and (item["events"] > 0 or day_key == latest)
                            and (digest_dir / day_key / "index.html").exists()):
                        history[day_key] = {"events": item["events"]}
        except (OSError, json.JSONDecodeError):
            print("SEO: манифест сводок повреждён, собираю его заново")

    for day_key in current_days:
        history[day_key] = {
            "events": int(daily_stats.get(day_key, {}).get("events", 0)),
        }
    manifest_path.write_text(
        json.dumps(history, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return history


def city_redirect_page(target_slug: str) -> str:
    target = f"{SITE}/city/{target_slug}/"
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="UTF-8" />
  <meta name="robots" content="noindex,follow" />
  <link rel="canonical" href="{target}" />
  <meta http-equiv="refresh" content="0;url={target}" />
  <title>Страница перенесена · Тихое небо</title>
</head><body><p><a href="{target}">Открыть актуальную страницу</a></p></body></html>
"""


def city_retired_page() -> str:
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="UTF-8" />
  <meta name="robots" content="noindex,follow" />
  <title>Страница больше не публикуется · Тихое небо</title>
</head><body><p>Актуальные данные доступны на
  <a href="{SITE}/">живой карте «Тихое небо»</a>.</p></body></html>
"""


def city_legacy_page(target_slug: str, active_slugs: set[str]) -> str:
    """Не канонизировать старый URL на цель, снятую тем же порогом."""
    if target_slug in active_slugs:
        return city_redirect_page(target_slug)
    return city_retired_page()


def busiest_window(hours: Counter) -> str | None:
    """Самые шумные шесть часов подряд — «чаще всего с 22 до 4».

    Именно так вопрос и звучит: не «в какой час», а «когда обычно».
    """
    if sum(hours.values()) < 12:
        return None
    best_start, best_sum = 0, -1
    for start in range(24):
        total = sum(hours[(start + offset) % 24] for offset in range(6))
        if total > best_sum:
            best_start, best_sum = start, total
    share = round(best_sum / sum(hours.values()) * 100)
    if share < 35:
        return None
    return f"с {best_start}:00 до {(best_start + 6) % 24}:00 — {share}% всех сообщений"


def summary_block(name: str, stats: dict | None, page_kind: str = "region") -> str:
    """Абзацы сводки. Тихий регион — тоже содержание, и его пишем прямо."""
    where = location_phrase(name, page_kind)
    if not stats or not stats["events"]:
        return (
            f"<h2>Что было за последний месяц</h2>\n"
            f"      <p>За последние 30 дней сообщений об опасности {where} в "
            f"отслеживаемых каналах не было. Карта продолжает следить: как "
            f"только что-то появится, регион на ней подсветится, а событие "
            f"встанет в общую ленту со ссылкой на первоисточник.</p>"
        )

    count = stats["events"]
    days = len(stats["days"])
    parts = [
        f"<h2>Что было за последний месяц</h2>",
        f"      <p>За 30 дней карта отметила {where} "
        f"<strong>{count} {plural(count, 'событие', 'события', 'событий')}</strong> "
        f"— в {days} из тридцати {plural(days, 'дня', 'дней', 'дней')}. "
        f"Последнее — {moment(stats['last'])}.</p>",
    ]

    signals = stats["signals"].most_common(3)
    if signals:
        listed = ", ".join(
            f"{SIGNAL_WORDS.get(key, key)} — {value}" for key, value in signals)
        parts.append(f"      <p>Чаще всего это {listed}.</p>")

    threats = stats["threats"].most_common(2)
    if threats:
        listed = ", ".join(
            f"{THREAT_WORDS.get(key, key)} ({value})" for key, value in threats)
        parts.append(f"      <p>Из названного источниками: {listed}.</p>")

    window = busiest_window(stats["hours"])
    if window:
        parts.append(f"      <p>По времени суток сообщения ложатся неровно: "
                     f"больше всего {window}.</p>")

    districts = stats["districts"].most_common(TOP_DISTRICTS)
    if districts:
        items = "".join(
            f"<li>{escape(place)} — {value} "
            f"{plural(value, 'сообщение', 'сообщения', 'сообщений')}</li>"
            for place, value in districts)
        parts.append(
            "      <h2>Где называют чаще</h2>\n"
            "      <p>Счёт по тому, какое место назвал источник, а не по "
            "тому, где что-то произошло:</p>\n"
            f"      <ul class=\"tops\">{items}</ul>")
    return "\n".join(parts)


def recent_block(name: str, stats: dict | None) -> str:
    """Последние события текстом — то, чего нет на вчерашней копии страницы.

    Список обновляется каждый час вместе со сводкой; для поисковика
    это живой уникальный контент, для человека — ответ «а что было
    последним» без открытия карты.
    """
    if not stats or not stats.get("recent"):
        return ""
    items = []
    previous = None
    for iso, place, signal, threat in stats["recent"]:
        # Имя самого региона в его же списке — не информация; подряд
        # идущие одинаковые строки не добавляют ничего, кроме длины.
        if place == name:
            place = ""
        if (place, signal, threat) == previous:
            continue
        previous = (place, signal, threat)
        what = SIGNAL_ONE.get(signal, signal)
        if signal == "infra" and threat == "airport":
            what = "аэропорт закрыт"
        elif signal == "allclear" and threat == "airport":
            what = "аэропорт открыт"
        tail = (f" · {THREAT_ONE[threat]}"
                if threat in THREAT_ONE else "")
        where = f"{escape(place)} — " if place else ""
        stamp = datetime.fromisoformat(iso).astimezone(MSK)
        items.append(
            f'<li><time datetime="{escape(iso)}">{moment(iso)}</time>: '
            f"{where}{escape(what)}{tail}</li>")
    return ("<h2>Последние события</h2>"
            '      <ul class="tops">' + "".join(items) + "</ul>")


def faq_block(name: str, stats: dict | None,
              page_kind: str = "region") -> tuple[str, str]:
    """FAQ с цифрами самого региона — и HTML, и разметка FAQPage.

    Ответы собираются из сводки, а не из шаблона: три страницы с дословно
    одинаковым FAQ поисковик склеит, а с разными числами — нет.
    """
    where = location_phrase(name, page_kind)
    if stats and stats["events"]:
        count = stats["events"]
        activity = (f"За последние 30 дней карта отметила {where} {count} "
                    f"{plural(count, 'событие', 'события', 'событий')}, "
                    f"последнее — {moment(stats['last'])} по Москве.")
        window = busiest_window(stats["hours"])
        if window:
            activity += f" Чаще всего сообщения приходят {window}."
    else:
        activity = (f"За последние 30 дней сообщений об опасности {where} "
                    f"в отслеживаемых каналах не было.")
    today = stats.get("today", 0) if stats else 0
    fresh = stats.get("fresh", 0) if stats else 0
    if fresh:
        now_answer = (f"За последние два часа карта отметила {where} {fresh} "
                      f"{plural(fresh, 'событие', 'события', 'событий')}. "
                      f"Текущую минуту показывает живая карта — кнопка выше; "
                      f"эта сводка обновляется ежечасно.")
    else:
        now_answer = (f"На момент обновления сводки свежих событий {where} "
                      f"не было. Текущую минуту показывает живая карта — "
                      f"кнопка выше; эта сводка обновляется ежечасно.")
    if today:
        today_answer = (f"Да: сегодня карта отметила {where} {today} "
                        f"{plural(today, 'событие', 'события', 'событий')}, "
                        f"последнее — {moment(stats['last'])} по Москве. "
                        f"Подробности по районам — на карте и в ленте.")
    else:
        today_answer = (f"Сегодня сообщений об опасности {where} в "
                        f"отслеживаемых каналах не было. " + activity)
    qa = [
        (f"Есть ли сейчас тревога {where}?", now_answer),
        (f"Была ли сегодня тревога {where}?", today_answer),
        (f"Что было {where} за последний месяц?",
         activity + " Текущую минуту показывает живая карта — кнопка выше."),
        ("Откуда данные и можно ли им верить?",
         "Карта собирает открытые Telegram-каналы мониторинга и официальные "
         "ленты (МЧС, РСЧС, оперштабы, Росавиация). У каждого события видно, "
         "сколько независимых источников его подтвердили, и открывается "
         "первоисточник. Карта неофициальная: она может опаздывать и "
         "ошибаться, при опасности следуйте указаниям экстренных служб."),
        ("Как получать уведомления о тревогах по своему месту?",
         "На карте выберите район и нажмите колокольчик — уведомления будут "
         "приходить в браузер даже при закрытой вкладке. В Telegram то же "
         "самое делает бот: команда /watch с названием места."),
    ]
    if name == "Санкт-Петербург":
        qa.insert(2, (
            "Где смотреть тревогу в СПб и Питере?",
            "СПб и Питер — распространённые названия Санкт-Петербурга. "
            "Эта страница показывает одну сводку по городу и его районам, "
            "независимо от того, какое название использовал источник.",
        ))
    html = ["<h2>Вопросы и ответы</h2>"]
    for question, answer in qa:
        html.append(f"      <h3>{escape(question)}</h3> "
                    f"<p>{escape(answer)}</p>")
    ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": question,
             "acceptedAnswer": {"@type": "Answer", "text": answer}}
            for question, answer in qa
        ],
    }, ensure_ascii=False)
    return " ".join(html), ld


def page(name: str, slug: str, districts: list[str], stats: dict | None,
         *, path_prefix: str = "region",
         parent: tuple[str, str] | None = None,
         admin_name: str | None = None,
         child_links: list[tuple[str, str]] | None = None,
         map_region_slug: str | None = None,
         neighbours: list[tuple[str, str]], updated: str) -> str:
    page_kind = "city" if path_prefix == "city" else "region"
    where = location_phrase(name, page_kind)
    aliases = PLACE_SEARCH_ALIASES.get(name, ())
    # Формула из реальных запросов Вебмастера: люди спрашивают «тревога в
    # X сейчас», «была ли сегодня», «карта по районам» — все три слова
    # должны быть в заголовке.
    title = (f"Тревога и БПЛА {where} сейчас — карта по районам"
             if path_prefix == "region"
             else f"Тревога и БПЛА {where} сейчас — живая карта")
    count = stats["events"] if stats else 0
    description_subject = (
        f"Тревоги и БПЛА {where} ({', '.join(aliases)})"
        if aliases else f"Воздушная обстановка {where}"
    )
    if count:
        description = (
            f"{description_subject}: {count} "
            f"{plural(count, 'событие', 'события', 'событий')} за 30 дней, "
            f"последнее — {moment(stats['last'])}. Тревоги, сообщения о "
            f"беспилотниках и отбои по районам, по открытым источникам.")
    else:
        description = (
            f"{description_subject}: за 30 дней сообщений об опасности "
            f"не было. Тревоги, сообщения о беспилотниках и отбои по районам, "
            f"по открытым источникам.")
    url = f"{SITE}/{path_prefix}/{slug}/"

    district_list = ""
    if districts:
        items = "".join(f"<li>{escape(item)}</li>" for item in districts)
        district_list = (
            "<h2>Районы и округа</h2>\n"
            "      <p>На карте видно обстановку по каждому из них отдельно:</p>\n"
            f"      <ul>{items}</ul>")

    child_list = ""
    if child_links:
        items = "".join(
            f'<li><a href="{href}">{escape(child_name)}</a></li>'
            for child_name, href in child_links)
        child_list = (
            "<h2>Города с отдельной сводкой</h2>\n"
            f'      <ul class="around">{items}</ul>')

    neighbour_list = "".join(
        f'<li><a href="/{path_prefix}/{other_slug}/">{escape(other_name)}</a></li>'
        for other_name, other_slug in neighbours)
    neighbour_title = ("Другие города и округа"
                       if page_kind == "city" else "Соседние регионы")

    faq_html, faq_ld = faq_block(name, stats, page_kind)

    map_href = (f"/?region={slug}" if path_prefix == "region"
                else f"/?region={map_region_slug}" if map_region_slug else "/")
    scope = ""
    if page_kind == "city" and admin_name:
        scope = (
            "<h2>Какая территория учтена</h2>\n"
            f"      <p>Сводка агрегирует сообщения, которые карта привязала "
            f"к зоне «{escape(admin_name)}», включая входящие населённые "
            f"пункты. Это районная привязка источников, а не утверждение о "
            f"точном месте события.</p>")
    alias_note = ""
    if aliases:
        alias_note = (
            f"<p><strong>{escape(aliases[0])}</strong> и "
            f"<strong>{escape(aliases[1])}</strong> — распространённые "
            f"названия Санкт-Петербурга. Все варианты ведут к одной сводке "
            f"по городу и районам.</p>"
        )
    if parent:
        parent_name, parent_url = parent
        city_crumb = (
            '<a href="/city/">Города</a> → '
            if page_kind == "city" else ""
        )
        crumb_nav = (f'<a href="/">Карта обстановки</a> → {city_crumb}'
                     f'<a href="{parent_url}">{escape(parent_name)}</a> → '
                     f'{escape(name)}')
    else:
        crumb_nav = f'<a href="/">Карта обстановки</a> → {escape(name)}'

    breadcrumbs = [
        {"@type": "ListItem", "position": 1, "name": "Карта обстановки",
         "item": f"{SITE}/"},
    ]
    if page_kind == "city":
        breadcrumbs.append({
            "@type": "ListItem", "position": len(breadcrumbs) + 1,
            "name": "Города", "item": f"{SITE}/city/",
        })
    if parent:
        breadcrumbs.append({
            "@type": "ListItem", "position": len(breadcrumbs) + 1,
            "name": parent[0],
            "item": parent[1],
        })
    breadcrumbs.append({
        "@type": "ListItem", "position": len(breadcrumbs) + 1,
        "name": name, "item": url,
    })
    breadcrumb_ld = json.dumps({
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": breadcrumbs,
    }, ensure_ascii=False)
    contained: dict = {"@type": "Country", "name": "Россия"}
    if parent:
        contained = {
            "@type": "AdministrativeArea", "name": parent[0],
            "url": parent[1],
            "containedInPlace": contained,
        }
    place_ld = {"@type": "Place", "name": name,
                "containedInPlace": contained}
    if aliases:
        place_ld["alternateName"] = list(aliases)
    webpage_ld = json.dumps({
        "@context": "https://schema.org", "@type": "WebPage", "url": url,
        "name": title,
        "about": place_ld,
        "isPartOf": {"@type": "WebSite", "name": "Тихое небо",
                     "url": f"{SITE}/"},
    }, ensure_ascii=False)

    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{escape(title)} · Тихое небо</title>
    <meta name="description" content="{escape(description)}" />
    <link rel="canonical" href="{url}" />
    <meta property="og:type" content="website" />
    <meta property="og:site_name" content="Тихое небо" />
    <meta property="og:locale" content="ru_RU" />
    <meta property="og:url" content="{url}" />
    <meta property="og:title" content="{escape(title)}" />
    <meta property="og:description" content="{escape(description)}" />
    <meta property="og:image" content="{SITE}/preview.png" />
    <meta name="theme-color" content="#0e1211" />
    <script type="application/ld+json">{breadcrumb_ld}</script>
    <script type="application/ld+json">{webpage_ld}</script>
    <script type="application/ld+json">{faq_ld}</script>
    <style>
      body {{ margin:0; background:#0b0f0e; color:#e6ebe6;
             font:16px/1.6 Inter, system-ui, -apple-system, sans-serif; }}
      main {{ max-width:760px; margin:0 auto; padding:40px 20px 80px; }}
      h1 {{ font-size:29px; line-height:1.25; margin:0 0 14px; }}
      h2 {{ font-size:19px; margin:34px 0 10px; color:#eef2ec; }}
      h3 {{ font-size:15px; margin:20px 0 6px; color:#dfe6df; }}
      time {{ color:#dfe6df; }}
      p {{ color:#aab4ad; }}
      strong {{ color:#e6ebe6; }}
      nav.crumbs {{ font-size:13px; color:#7d8a83; margin:0 0 18px; }}
      nav.crumbs a {{ color:#9fd4b0; text-decoration:none; }}
      a.map {{ display:inline-block; margin:22px 0 6px; padding:13px 22px;
              background:#e93e4e; color:#fff; text-decoration:none;
              border-radius:10px; font-weight:600; }}
      ul {{ columns:2; column-gap:28px; padding-left:20px; color:#aab4ad; }}
      ul.tops, ul.around {{ columns:1; }}
      ul.around a {{ color:#9fd4b0; }}
      li {{ margin:3px 0; break-inside:avoid; }}
      footer {{ margin-top:44px; padding-top:18px; font-size:13px; color:#7d8a83;
               border-top:1px solid rgba(255,255,255,.08); }}
      footer a {{ color:#9fd4b0; }}
      @media (max-width:560px) {{ ul {{ columns:1; }} }}
    </style>
  </head>
  <body>
    <main>
      <nav class="crumbs">{crumb_nav}</nav>
      <h1>{escape(title)}</h1>
      <p>{escape(description)}</p>

      {alias_note}

      <a class="map" href="{map_href}">Открыть карту — {escape(name)}</a>

      {summary_block(name, stats, page_kind)}

      {recent_block(name, stats)}

      <h2>Что показывает карта</h2>
      <p>
        Тревоги, предупреждения об опасности и отбои — так, как о них
        сообщили открытые Telegram-каналы. У каждого события видно, сколько
        независимых источников его подтвердили, и можно открыть
        первоисточник.
      </p>

      {district_list}

      {child_list}

      {scope}

      {faq_html}

      <h2>{neighbour_title}</h2>
      <ul class="around">{neighbour_list}</ul>

      <footer>
        Сводка обновлена {updated}. Неофициальная карта: составлена по
        публичным сообщениям, может опаздывать и ошибаться. Не принимайте по
        ней решения о личной безопасности — следуйте указаниям экстренных
        служб.
        <br /><a href="/">Вся карта обстановки по России</a> ·
        <a href="/city/">Сводки по городам</a>
      </footer>
    </main>
  </body>
</html>
"""


def city_index_page(cities: list[dict], updated: str) -> str:
    """HTML-каталог опубликованных городов — один crawl hub вместо 150 сирот."""
    grouped: dict[str, dict] = {}
    for city in cities:
        region = grouped.setdefault(city["region_id"], {
            "name": city["region_name"],
            "slug": city["region_slug"],
            "cities": [],
        })
        region["cities"].append(city)

    sections = []
    for region in sorted(grouped.values(), key=lambda item: item["name"]):
        items = "".join(
            f'<li><a href="/city/{city["slug"]}/">'
            f'{escape(city["name"])}</a></li>'
            for city in sorted(region["cities"], key=lambda item: item["name"])
        )
        sections.append(
            '<section><h2><a href="/region/' + region["slug"] + '/">'
            + escape(region["name"]) + '</a></h2><ul>' + items
            + '</ul></section>'
        )

    title = "Тревога и БПЛА по городам России — карта и сводки"
    description = (
        f"Воздушная обстановка в {len(cities)} городах России: тревоги, "
        "сообщения о БПЛА, последние события и ссылки на живую карту."
    )
    url = f"{SITE}/city/"
    breadcrumb_ld = json.dumps({
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1,
             "name": "Карта обстановки", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2,
             "name": "Города", "item": url},
        ],
    }, ensure_ascii=False)
    collection_ld = json.dumps({
        "@context": "https://schema.org", "@type": "CollectionPage",
        "url": url, "name": title, "description": description,
        "isPartOf": {"@type": "WebSite", "name": "Тихое небо",
                     "url": f"{SITE}/"},
        "mainEntity": {
            "@type": "ItemList", "numberOfItems": len(cities),
            "itemListElement": [
                {"@type": "ListItem", "position": index,
                 "name": city["name"],
                 "url": f'{SITE}/city/{city["slug"]}/'}
                for index, city in enumerate(cities, start=1)
            ],
        },
    }, ensure_ascii=False)

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{escape(title)} · Тихое небо</title>
  <meta name="description" content="{escape(description)}" />
  <link rel="canonical" href="{url}" />
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content="Тихое небо" />
  <meta property="og:locale" content="ru_RU" />
  <meta property="og:url" content="{url}" />
  <meta property="og:title" content="{escape(title)}" />
  <meta property="og:description" content="{escape(description)}" />
  <meta property="og:image" content="{SITE}/preview.png" />
  <meta name="theme-color" content="#0e1211" />
  <script type="application/ld+json">{breadcrumb_ld}</script>
  <script type="application/ld+json">{collection_ld}</script>
  <style>
    body {{ margin:0; background:#0b0f0e; color:#e6ebe6;
           font:16px/1.6 Inter, system-ui, sans-serif; }}
    main {{ max-width:960px; margin:0 auto; padding:40px 20px 80px; }}
    h1 {{ max-width:760px; font-size:29px; line-height:1.25; margin:0 0 14px; }}
    h2 {{ font-size:17px; margin:0 0 9px; }}
    p, li {{ color:#aab4ad; }} a {{ color:#9fd4b0; }}
    nav {{ font-size:13px; margin-bottom:18px; }}
    a.map {{ display:inline-block; margin:18px 0 30px; padding:12px 18px;
            background:#e93e4e; color:#fff; text-decoration:none;
            border-radius:6px; font-weight:600; }}
    .regions {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
                gap:28px 34px; margin-top:28px; }}
    section {{ border-top:1px solid rgba(255,255,255,.12); padding-top:15px; }}
    ul {{ margin:0; padding-left:20px; }} li {{ margin:4px 0; }}
    footer {{ margin-top:46px; padding-top:18px; color:#7d8a83;
             border-top:1px solid rgba(255,255,255,.08); font-size:13px; }}
    @media (max-width:560px) {{ main {{ padding:28px 16px 64px; }}
      .regions {{ grid-template-columns:1fr; gap:24px; }} }}
  </style></head><body><main>
  <nav><a href="/">Карта обстановки</a> → Города</nav>
  <h1>Тревога и БПЛА по городам России</h1>
  <p>{escape(description)}</p>
  <a class="map" href="/">Открыть живую карту</a>
  <div class="regions">{''.join(sections)}</div>
  <footer>Каталог и сводки обновлены {escape(updated)}. Страницы отражают
    сообщения открытых источников и могут опаздывать.
    <a href="/">Карта по России</a></footer>
</main></body></html>
"""


def digest_date(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year} года"


def digest_page(day_key: str, stats: dict, regions: dict[str, tuple[str, str]],
                older: str | None, newer: str | None, updated: str,
                updated_iso: str) -> str:
    day = date.fromisoformat(day_key)
    label = digest_date(day)
    count = stats.get("events", 0)
    region_count = len(stats.get("regions", {}))
    title = f"Сводка тревог и БПЛА за {label}"
    description = (
        f"За {label} карта отметила {count} "
        f"{plural(count, 'событие', 'события', 'событий')} в {region_count} "
        f"{plural(region_count, 'регионе', 'регионах', 'регионах')}. "
        "Хронология тревог, сообщений о БПЛА и отбоев по открытым источникам."
    )
    url = f"{SITE}/svodka/{day_key}/"

    region_items = []
    for zone_id, value in stats.get("regions", Counter()).most_common(10):
        region = regions.get(zone_id)
        if not region:
            continue
        region_name, region_slug = region
        region_items.append(
            f'<li><a href="/region/{region_slug}/">{escape(region_name)}</a> — '
            f"{value} {plural(value, 'событие', 'события', 'событий')}</li>")
    top_regions = (
        "<h2>Где сообщений было больше</h2>\n"
        f'      <ul class="tops">{"".join(region_items)}</ul>'
        if region_items else "")

    signal_items = "".join(
        f"<li>{escape(SIGNAL_WORDS.get(key, key))} — {value}</li>"
        for key, value in stats.get("signals", Counter()).most_common())
    threat_items = "".join(
        f"<li>{escape(THREAT_WORDS.get(key, key))} — {value}</li>"
        for key, value in stats.get("threats", Counter()).most_common())
    breakdown = (
        "<h2>Что сообщали</h2>\n"
        f'      <ul class="tops">{signal_items}{threat_items}</ul>'
        if signal_items or threat_items else "")

    day_links = []
    if older:
        day_links.append(f'<a href="/svodka/{older}/">← {digest_date(date.fromisoformat(older))}</a>')
    if newer:
        day_links.append(f'<a href="/svodka/{newer}/">{digest_date(date.fromisoformat(newer))} →</a>')
    day_nav = " · ".join(day_links)

    article_ld = json.dumps({
        "@context": "https://schema.org", "@type": "Article",
        "headline": title, "description": description, "url": url,
        "datePublished": f"{day_key}T00:00:00+03:00",
        "dateModified": updated_iso,
        "author": {"@type": "Organization", "name": "Тихое небо",
                   "url": f"{SITE}/"},
        "publisher": {"@type": "Organization", "name": "Тихое небо",
                      "url": f"{SITE}/"},
        "mainEntityOfPage": url,
    }, ensure_ascii=False)
    breadcrumb_ld = json.dumps({
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Карта обстановки",
             "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": "Ежедневные сводки",
             "item": f"{SITE}/svodka/"},
            {"@type": "ListItem", "position": 3, "name": label,
             "item": url},
        ],
    }, ensure_ascii=False)

    return f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{escape(title)} · Тихое небо</title>
    <meta name="description" content="{escape(description)}" />
    <link rel="canonical" href="{url}" />
    <meta property="og:type" content="article" />
    <meta property="og:site_name" content="Тихое небо" />
    <meta property="og:url" content="{url}" />
    <meta property="og:title" content="{escape(title)}" />
    <meta property="og:description" content="{escape(description)}" />
    <meta property="og:image" content="{SITE}/preview.png" />
    <script type="application/ld+json">{breadcrumb_ld}</script>
    <script type="application/ld+json">{article_ld}</script>
    <style>
      body {{ margin:0; background:#0b0f0e; color:#e6ebe6;
             font:16px/1.6 Inter, system-ui, -apple-system, sans-serif; }}
      main {{ max-width:760px; margin:0 auto; padding:40px 20px 80px; }}
      h1 {{ font-size:29px; line-height:1.25; margin:0 0 14px; }}
      h2 {{ font-size:19px; margin:34px 0 10px; color:#eef2ec; }}
      p, li {{ color:#aab4ad; }} strong, time {{ color:#e6ebe6; }}
      a {{ color:#9fd4b0; }}
      nav.crumbs {{ font-size:13px; color:#7d8a83; margin:0 0 18px; }}
      a.map {{ display:inline-block; margin:22px 0 6px; padding:13px 22px;
              background:#e93e4e; color:#fff; text-decoration:none;
              border-radius:10px; font-weight:600; }}
      ul {{ padding-left:20px; }} li {{ margin:3px 0; }}
      nav.days {{ margin:32px 0; display:flex; gap:14px; flex-wrap:wrap; }}
      footer {{ margin-top:44px; padding-top:18px; font-size:13px; color:#7d8a83;
               border-top:1px solid rgba(255,255,255,.08); }}
    </style>
  </head>
  <body>
    <main>
      <nav class="crumbs"><a href="/">Карта обстановки</a> →
        <a href="/svodka/">Сводки</a> → {escape(label)}</nav>
      <h1>{escape(title)}</h1>
      <p>{escape(description)}</p>
      <a class="map" href="/">Открыть живую карту</a>
      {top_regions}
      {breakdown}
      {recent_block("Россия", stats)}
      <h2>Как читать сводку</h2>
      <p>Это хронология сообщений открытых источников, а не официальный
        отчёт и не число целей. Повторы объединяются в события; точность
        места зависит от формулировки первоисточника.</p>
      <nav class="days">{day_nav}</nav>
      <footer>Сводка сформирована {updated}. Данные могут опаздывать и
        содержать ошибки; при опасности следуйте указаниям экстренных служб.
        <br /><a href="/svodka/">Все ежедневные сводки</a></footer>
    </main>
  </body>
</html>
"""


def digest_index(days: list[str], daily_stats: dict[str, dict],
                 updated: str) -> str:
    items = []
    for day_key in reversed(days):
        day = date.fromisoformat(day_key)
        count = daily_stats.get(day_key, {}).get("events", 0)
        items.append(
            f'<li><a href="/svodka/{day_key}/">{digest_date(day)}</a> — '
            f"{count} {plural(count, 'событие', 'события', 'событий')}</li>")
    description = ("Ежедневные сводки карты воздушной обстановки: тревоги, "
                   "сообщения о БПЛА, перехваты и отбои по регионам России.")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Ежедневные сводки тревог и БПЛА · Тихое небо</title>
  <meta name="description" content="{description}" />
  <link rel="canonical" href="{SITE}/svodka/" />
  <style>
    body {{ margin:0; background:#0b0f0e; color:#e6ebe6;
           font:16px/1.6 Inter, system-ui, sans-serif; }}
    main {{ max-width:760px; margin:0 auto; padding:40px 20px 80px; }}
    h1 {{ font-size:29px; line-height:1.25; }} p, li {{ color:#aab4ad; }}
    a {{ color:#9fd4b0; }} li {{ margin:7px 0; }}
    nav {{ font-size:13px; margin-bottom:18px; }}
    footer {{ margin-top:44px; color:#7d8a83; font-size:13px; }}
  </style></head><body><main>
  <nav><a href="/">Карта обстановки</a> → Сводки</nav>
  <h1>Ежедневные сводки тревог и БПЛА</h1>
  <p>{description}</p><ul>{''.join(items)}</ul>
  <footer>Обновлено {updated}. <a href="/">Открыть живую карту</a></footer>
</main></body></html>
"""


def fill_prerender(named: list, stats: dict, updated: str,
                   city_count: int) -> bool:
    """Вписать в главную то, что робот без JavaScript иначе не увидит.

    SPA для поисковика — пустой div: разметка ld+json есть, а текста и
    внутренних ссылок нет. Блок между маркерами в dist/index.html живёт
    до монтирования React и виден роботу: сводка за окно, ссылка на каталог
    городов и все посадочные регионов. Маркеры лежат в index.html репозитория.
    """
    index = OUT / "index.html"
    if not index.exists():
        return False
    html = index.read_text(encoding="utf-8")
    start, end = "<!-- prerender:start -->", "<!-- prerender:end -->"
    if start not in html or end not in html:
        print("пререндер: маркеров в dist/index.html нет — блок не вписан")
        return False

    active = sum(1 for _, _, _, zone in named if stats.get(zone))
    total = sum(entry["events"] for entry in stats.values())
    lines = [
        f'<p style="margin:18px 0 6px;color:#9da8a0">За последние 30 дней — '
        f'{total} событий в {active} регионах. Обновлено {escape(updated)}.</p>',
        f'<p style="margin:6px 0 12px"><a href="/city/" '
        f'style="color:#9fd4b0">Сводки по {city_count} городам России</a></p>',
        '<nav aria-label="Регионы"><h2 style="margin:18px 0 8px;font-size:16px">'
        'Обстановка по регионам</h2>',
        '<ul style="margin:0;padding:0;list-style:none;display:flex;'
        'flex-wrap:wrap;gap:6px 14px;max-width:900px">',
    ]
    for name, slug, _, zone in named:
        count = stats.get(zone, {}).get("events", 0)
        suffix = f" · {count}" if count else ""
        lines.append(
            f'<li><a href="/region/{slug}/" style="color:#9da8a0">'
            f'{escape(name)}{suffix}</a></li>')
    lines.append("</ul></nav>")

    head, _, rest = html.partition(start)
    _, _, tail = rest.partition(end)
    index.write_text(head + start + "\n" + "\n".join(lines) + "\n" + end + tail,
                     encoding="utf-8")
    return True


def ping_indexnow(urls: list[str], key: str) -> None:
    """Позвать роботов Яндекса и Bing сразу, не дожидаясь обхода.

    Молодой сайт ждёт первого обхода неделями. IndexNow — тот же способ
    сказать «страница изменилась», только без ручной кнопки в вебмастере.
    Google протокол не поддерживает и придёт сам.
    """
    payload = json.dumps({
        "host": SITE.removeprefix("https://"),
        "key": key,
        "keyLocation": f"{SITE}/{key}.txt",
        "urlList": urls,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://yandex.com/indexnow",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    # До трёх попыток: рукопожатие с yandex.com временами не укладывается
    # в таймаут, а со второго раза проходит сразу. Повторяется только
    # сетевая ошибка — отказ робота повторять бессмысленно.
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                print(f"IndexNow: {response.status}")
            return
        except urllib.error.HTTPError as error:
            # Отказ робота — не повод ронять сборку: страницы уже на месте.
            print(f"IndexNow отказал: {error.code}")
            return
        except OSError as error:
            if attempt == 3:
                print(f"IndexNow недоступен: {error}")
                return
            time.sleep(5)


def main() -> int:
    regions, by_region = load_geo()
    today = now_utc().astimezone(MSK)
    updated = f"{today.day} {MONTHS[today.month - 1]}, {today:%H:%M} МСК"
    lastmod = today.date().isoformat()

    named = []
    for feature in regions:
        props = feature.get("properties") or {}
        # Акватории посадочной страницы не получают: она вся построена
        # вокруг районов субъекта, а у моря их нет. На карте и в ленте
        # оно при этом полноценная зона.
        if props.get("kind") == "sea":
            continue
        if props.get("name") and props.get("zone"):
            named.append((props["name"], str(props["zone"]).replace("_", "-"),
                          props["id"], props["zone"]))
    named.sort(key=lambda item: item[0])

    region_pages = {
        zone_id: (name, slug)
        for name, slug, _, zone_id in named
    }
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        stats, city_stats, daily_stats = collect_stats(connection)
        current_cities = build_city_catalog(connection, city_stats, region_pages)

    cities = persistent_city_catalog(
        current_cities, city_stats, OUT / "city" / "manifest.json")

    cities_by_region: dict[str, list[dict]] = {}
    for city in cities:
        cities_by_region.setdefault(city["region_id"], []).append(city)

    sitemap: list[tuple[str, str, str, str]] = [
        (f"{SITE}/", lastmod, "hourly", "1.0"),
        (f"{SITE}/city/", lastmod, "daily", "0.85"),
        # Правовые страницы генерирует scripts/legal_pages при выкатке —
        # содержимое от данных не зависит. В карту сайта их всё же
        # включаем: страница, которой нет в индексе, не работает.
        (f"{SITE}/privacy/", lastmod, "yearly", "0.2"),
        (f"{SITE}/terms/", lastmod, "yearly", "0.2"),
    ]
    (OUT / "city" / "index.html").write_text(
        city_index_page(cities, updated), encoding="utf-8")
    for index, (name, slug, source_id, zone_id) in enumerate(named):
        districts = sorted(by_region.get(source_id, []))
        # Соседи по алфавиту, кольцом: у последних регионов иначе не было бы
        # ни одной исходящей ссылки.
        neighbours = [(named[(index + step) % len(named)][0],
                       named[(index + step) % len(named)][1])
                      for step in range(1, NEIGHBOURS + 1)]
        child_links = [
            (city["name"], f'/city/{city["slug"]}/')
            for city in cities_by_region.get(zone_id, [])
        ]
        directory = OUT / "region" / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(
            page(name, slug, districts, stats.get(zone_id),
                 neighbours=neighbours, updated=updated,
                 child_links=child_links),
            encoding="utf-8")
        sitemap.append((f"{SITE}/region/{slug}/", lastmod, "hourly", "0.8"))

    for city in cities:
        peers = cities_by_region.get(city["region_id"], cities)
        city_index = peers.index(city)
        neighbour_count = min(NEIGHBOURS, max(0, len(peers) - 1))
        neighbours = [
            (peers[(city_index + step) % len(peers)]["name"],
             peers[(city_index + step) % len(peers)]["slug"])
            for step in range(1, neighbour_count + 1)
        ]
        directory = OUT / "city" / city["slug"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(
            page(
                city["name"], city["slug"], [], city["stats"],
                path_prefix="city",
                parent=(city["region_name"],
                        f'{SITE}/region/{city["region_slug"]}/'),
                admin_name=city["admin_name"],
                map_region_slug=city["region_slug"],
                neighbours=neighbours,
                updated=updated,
            ),
            encoding="utf-8",
        )
        sitemap.append((f'{SITE}/city/{city["slug"]}/', lastmod,
                        "hourly", "0.75"))

    active_city_slugs = {city["slug"] for city in cities}
    for old_slug, target_slug in CITY_SLUG_REDIRECTS.items():
        if old_slug in active_city_slugs:
            continue
        directory = OUT / "city" / old_slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(
            city_legacy_page(target_slug, active_city_slugs), encoding="utf-8")

    retained_slugs = active_city_slugs | set(CITY_SLUG_REDIRECTS)
    for directory in (OUT / "city").iterdir():
        if directory.is_dir() and directory.name not in retained_slugs:
            (directory / "index.html").write_text(
                city_retired_page(), encoding="utf-8")

    current_day_keys = [
        (today.date() - timedelta(days=offset)).isoformat()
        for offset in range(DIGEST_DAYS - 1, -1, -1)
    ]
    current_day_keys = [
        key for key in current_day_keys if key in daily_stats or key == lastmod
    ]
    digest_dir = OUT / "svodka"
    digest_dir.mkdir(parents=True, exist_ok=True)
    history = digest_history(current_day_keys, daily_stats, digest_dir)
    day_keys = sorted(history)

    empty_day = {
        "events": 0, "regions": Counter(), "signals": Counter(),
        "threats": Counter(), "recent": [],
    }
    for day_key in current_day_keys:
        index = day_keys.index(day_key)
        older = day_keys[index - 1] if index > 0 else None
        newer = day_keys[index + 1] if index + 1 < len(day_keys) else None
        directory = digest_dir / day_key
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(
            digest_page(day_key, daily_stats.get(day_key, empty_day),
                        region_pages, older, newer, updated, today.isoformat()),
            encoding="utf-8",
        )

    (digest_dir / "index.html").write_text(
        digest_index(day_keys, history, updated), encoding="utf-8")
    sitemap.append((f"{SITE}/svodka/", lastmod, "daily", "0.7"))
    for day_key in day_keys:
        current = day_key == lastmod
        sitemap.append((f"{SITE}/svodka/{day_key}/",
                        lastmod if current else day_key,
                        "hourly" if current else "monthly",
                        "0.72" if current else "0.6"))

    entries = "\n".join(
        f"  <url><loc>{url}</loc><lastmod>{modified}</lastmod>"
        f"<changefreq>{changefreq}</changefreq>"
        f"<priority>{priority}</priority></url>"
        for url, modified, changefreq, priority in sitemap)
    (OUT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</urlset>\n", encoding="utf-8")

    with_data = sum(1 for _, _, _, zone in named if stats.get(zone))
    filled = fill_prerender(named, stats, updated, len(cities))
    print(f"SEO: регионов {len(named)} ({with_data} со сводкой), "
          f"городов {len(cities)}, дней {len(day_keys)}, "
          f"в sitemap {len(sitemap)} адресов"
          + (", пререндер главной обновлён" if filled else ""))

    if "--ping" in sys.argv:
        keys = [path for path in OUT.glob("*.txt") if path.stem.isalnum()
                and len(path.stem) >= 8]
        if keys:
            ping_indexnow([url for url, _, _, _ in sitemap], keys[0].stem)
        else:
            print("IndexNow: ключа в dist нет, пропускаю")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
