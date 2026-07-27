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

import sqlite3

# Ключ региона из ingest/config.py -> название в справочнике зон.
REGION_NAMES: dict[str, str] = {
    "adygea": "Адыгея",
    "astrakhan": "Астраханская область",
    "belgorod": "Белгородская область",
    "bryansk": "Брянская область",
    "crimea": "Республика Крым",
    "dnr": "Донецкая Народная Республика",
    "ivanovo": "Ивановская область",
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
}


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
