"""Регион источника как запасная привязка события.

Часть каналов не называет место вовсе: «ОРЁЛ ТРЕВОГА» шлёт «РАКЕТНАЯ
ОПАСНОСТЬ!» без единого топонима, потому что регион зашит в название канала.
Раньше такой текст цеплялся за топоним из футера подписи, но футеры пришлось
срезать — они приклеивали чужой город к каждому сообщению соседних лент.

Отсюда правило: если сообщение разобрано как оповещение, но ни одной зоны
не нашлось, событие относится к региону самого источника. Это заведомо
грубее, чем название района в тексте, поэтому применяется только как запасной
вариант и только к региональным лентам с известной географией.
"""

from __future__ import annotations

import re
import sqlite3


REGION_WIDE_CLEAR_RE = re.compile(
    r"\b(?:на|по\s+всей)\s+территори\w*\b[^.!?\n]{0,100}?"
    r"(?:отбой|отмен\w+|снят\w+|минова\w*)"
    r"|(?:отбой|отмен\w+|снят\w+|минова\w*)[^.!?\n]{0,100}?"
    r"\b(?:на|по\s+всей)\s+территори\w*\b",
    re.IGNORECASE,
)

# Ключ региона из ingest/config.py -> название в справочнике зон.
REGION_NAMES: dict[str, str] = {
    "adygea": "Адыгея",
    "astrakhan": "Астраханская область",
    "bashkortostan": "Республика Башкортостан",
    "belgorod": "Белгородская область",
    "bryansk": "Брянская область",
    "chelyabinsk": "Челябинская область",
    "chuvashia": "Чувашия",
    "crimea": "Республика Крым",
    "dagestan": "Дагестан",
    "dnr": "Донецкая Народная Республика",
    "ivanovo": "Ивановская область",
    "kirov": "Кировская область",
    "khmao": "Ханты-Мансийский автономный округ — Югра",
    "komi": "Республика Коми",
    "kostroma": "Костромская область",
    "leningrad": "Ленинградская область",
    "mari_el": "Марий Эл",
    "mordovia": "Мордовия",
    "moscow_oblast": "Московская область",
    "novgorod": "Новгородская область",
    "omsk": "Омская область",
    "orenburg": "Оренбургская область",
    "perm": "Пермский край",
    "tyumen": "Тюменская область",
    "ulyanovsk": "Ульяновская область",
    "udmurtia": "Удмуртия",
    "vologda": "Вологодская область",
    "izhevsk": "Удмуртия",
    "kaluga": "Калужская область",
    "kazan": "Татарстан",
    "kherson": "Херсонская область",
    "krasnodar": "Краснодарский край",
    "kursk": "Курская область",
    "lipetsk": "Липецкая область",
    "lnr": "Луганская Народная Республика",
    "moscow": "Москва",
    "nnovgorod": "Нижегородская область",
    "orel": "Орловская область",
    "penza": "Пензенская область",
    "pskov": "Псковская область",
    "rostov": "Ростовская область",
    "ryazan": "Рязанская область",
    "samara": "Самарская область",
    "saratov": "Саратовская область",
    "sevastopol": "Севастополь",
    "smolensk": "Смоленская область",
    "sochi": "Краснодарский край",
    "spb": "Санкт-Петербург",
    "stavropol": "Ставропольский край",
    "sverdlovsk": "Свердловская область",
    "tambov": "Тамбовская область",
    "tver": "Тверская область",
    "tula": "Тульская область",
    "vladimir": "Владимирская область",
    "volgograd": "Волгоградская область",
    "voronezh": "Воронежская область",
    "yaroslavl": "Ярославская область",
    "zaporizhzhia": "Запорожская область",
    # Второе написание живёт в конфиге исторически; из-за него Токмак
    # молча оставался без фолбэка.
    "zaporozhye": "Запорожская область",
}


def explicit_home_region(observation, resolved, home: str | None):
    """Регион источника, когда отбой явно объявлен на всей его территории.

    В одном сообщении РСЧС могут снять и региональную тревогу, и режим
    «Ковёр» в аэропорту. Обычный ``drop_covered`` оставляет аэропорт как
    более точную зону и выбрасывает регион, поэтому общий отбой не закрывает
    областное событие. Локальная формулировка без «на территории» сюда не
    попадает и по-прежнему гасит только названный город или район.
    """
    if (
        not home
        or observation.signal_type != "allclear"
        or not REGION_WIDE_CLEAR_RE.search(observation.body)
    ):
        return None
    return next(
        (
            item
            for item in resolved
            if item.zone_id == home and item.level == "region"
        ),
        None,
    )


def resolve_observation_zones(geocoder, observation, home: str | None):
    """Разрешить зоны с безопасной семантикой отбоя.

    Возвращает ``(zones, used_source_region)``. Коррекция ``retracted`` без
    явно названного места никогда не наследует весь регион канала: «наша
    авиация» без топонима раньше закрывала сотни дочерних событий. То же
    относится к смешанному отбою, где все найденные зоны перечислены после
    «опасность сохраняется» или «кроме» — отсутствие адресата лучше
    ложного массового отбоя.
    """
    from .geocode import Resolved, preserved_zone_ids

    candidates = geocoder.resolve(observation.place_phrases, home=home)
    protected = (
        preserved_zone_ids(
            geocoder, observation.place_phrases, home
        )
        if observation.signal_type in {"allclear", "retracted"}
        else set()
    )
    if protected:
        candidates = [
            item for item in candidates if item.zone_id not in protected
        ]

    regional_clear = explicit_home_region(observation, candidates, home)
    resolved = (
        [regional_clear]
        if regional_clear is not None
        else geocoder.drop_covered(candidates)
    )
    if resolved:
        return resolved, False

    if (not home or protected
            or observation.signal_type == "retracted"):
        return [], False

    zone = geocoder.zones[home]
    return [Resolved(home, "region", zone["name_ru"],
                     zone["lat"], zone["lon"], "источник")], True


def build_fallback(connection: sqlite3.Connection, sources) -> dict[str, str]:
    """Отображение source_key -> zone_id региона источника.

    Федеральные ленты сюда не попадают намеренно: у них география вся страна,
    и приписывать их сообщения одному региону было бы прямой ошибкой.
    """
    by_name: dict[str, str] = {}
    for row in connection.execute(
        "SELECT id, name_ru FROM zones WHERE level = 'region'"
    ):
        by_name[row["name_ru"].strip().lower()] = row["id"]

    fallback: dict[str, str] = {}
    for source in sources:
        if source.tier == "federal":
            continue
        name = REGION_NAMES.get(getattr(source, "region", "other"))
        if not name:
            continue
        zone_id = by_name.get(name.lower())
        if zone_id:
            fallback[source.key] = zone_id
    return fallback


def unmatched_regions(connection: sqlite3.Connection) -> list[str]:
    """Названия из REGION_NAMES, которых нет в справочнике.

    Нужна при смене источника границ: молчаливо потерянная привязка выглядит
    как «канал просто не геокодируется», и найти причину потом тяжело.
    """
    known = {
        row["name_ru"].strip().lower()
        for row in connection.execute("SELECT name_ru FROM zones WHERE level = 'region'")
    }
    return sorted({name for name in REGION_NAMES.values() if name.lower() not in known})
