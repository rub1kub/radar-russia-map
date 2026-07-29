"""Маршрут борта, названный самим сообщением.

«От Анапы через Раевскую на Новороссийск» — источник сам описал путь, и
такую линию можно рисовать честно: мы её пересказываем, а не вычисляем.

Маршруты, СОБРАННЫЕ из разных сообщений, сюда намеренно не пишутся: в
массовый налёт летит десяток бортов, и на живой выборке у половины
точечных событий нашлось несколько правдоподобных «продолжений» сразу.
Такая склейка — догадка, и её место разве что в архиве с пометкой
«приблизительно» (см. src/lib/trails.ts), но не в таблице фактов.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3

# Обороты, которыми ленты описывают путь. Одного перечисления мест мало:
# «Азов, Батайск — опасность БПЛА» называет два адресата предупреждения,
# а не траекторию.
ROUTE_MARKER_RE = re.compile(
    r"\bчерез\b|в\s+сторону\b|в\s+направлени\w*|курс\w*\s+на\b"
    r"|прош[лаиё]\w*\s+на\b|ид[её]т\s+на\b|лет[ия]т\s+на\b",
    re.IGNORECASE,
)

# Больше шести точек в одном сообщении — это уже сводка, а не маршрут.
MAX_POINTS = 6
# Плечо длиннее — почти наверняка ошибка геокодера: настоящие маршруты в
# корпусе идут по соседним районам, плечи 14-60 км. На пороге в 300 км
# хутор-тёзка «Большой» рисовал линию через Чёрное море в Сочи.
MAX_LEG_KM = 120.0
# Короче — точки слились в одно место, линия выродилась.
MIN_TOTAL_KM = 5.0
# Путь длиннее прямой более чем в полтора раза — это не полёт, а
# перечисление районов-адресатов в порядке списка: «Армавир, Белоглинский,
# Новопокровский... в направлении X» рисовало зигзаг с извилистостью до 8.
# У настоящих маршрутов корпуса она 1.0-1.1.
MAX_SINUOSITY = 1.6


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = (b[1] - a[1]) * 111.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    dy = (b[0] - a[0]) * 111.0
    return math.hypot(dx, dy)


def extract_route(text: str, observation, resolved) -> list[tuple[float, float, str]] | None:
    """Точки маршрута в порядке текста — или None, если маршрута нет.

    resolved — зоны сообщения после drop_covered: порядок совпадает с
    порядком упоминания. Регионы не берутся: «в сторону Белгородской
    области» — направление, а не точка на линии.
    """
    if not observation.relevant:
        return None
    # Отбой и опровержение говорят, что борта нет, — рисовать им нечего.
    if observation.signal_type in ("allclear", "retracted"):
        return None
    if not ROUTE_MARKER_RE.search(text or ""):
        return None

    points: list[tuple[float, float, str]] = []
    for item in resolved:
        if item.level == "region" or item.lat is None or item.lon is None:
            continue
        if points and _km((points[-1][0], points[-1][1]), (item.lat, item.lon)) < 1:
            continue
        points.append((item.lat, item.lon, item.name))
        if len(points) == MAX_POINTS:
            break

    if len(points) < 2:
        return None

    total = 0.0
    for a, b in zip(points, points[1:]):
        leg = _km((a[0], a[1]), (b[0], b[1]))
        if leg > MAX_LEG_KM:
            return None
        total += leg
    if total < MIN_TOTAL_KM:
        return None
    direct = _km((points[0][0], points[0][1]), (points[-1][0], points[-1][1]))
    if direct < MIN_TOTAL_KM or total > direct * MAX_SINUOSITY:
        return None
    return points


def store_route(connection: sqlite3.Connection, raw_id: int, source_key: str,
                posted_at: str, observation, points) -> None:
    """Одно сообщение — один маршрут; повторный разбор перезаписывает."""
    connection.execute(
        "INSERT OR REPLACE INTO routes"
        " (raw_message_id, source_key, posted_at, threat_type, severity, points)"
        " VALUES (?,?,?,?,?,?)",
        (raw_id, source_key, posted_at, observation.threat_type,
         observation.severity, json.dumps(points, ensure_ascii=False)),
    )


def backfill(connection: sqlite3.Connection, days: int = 90) -> int:
    """Достроить маршруты по уже собранным сообщениям.

    События не трогаются: routes — отдельная таблица, и её можно
    пересчитывать хоть каждый день. Нужен после появления таблицы и после
    любой правки ROUTE_MARKER_RE.
    """
    from datetime import timedelta

    from .geocode import Geocoder
    from .parse import parse
    from .timeutil import now_utc

    geocoder = Geocoder(connection)
    since = (now_utc() - timedelta(days=days)).isoformat()
    found = 0
    for row in connection.execute(
        "SELECT id, source_key, posted_at, text FROM raw_messages"
        " WHERE posted_at >= ? ORDER BY posted_at", (since,)
    ).fetchall():
        if not ROUTE_MARKER_RE.search(row["text"] or ""):
            continue
        observation = parse(row["text"])
        if not observation.relevant:
            continue
        resolved = geocoder.drop_covered(geocoder.resolve(observation.place_phrases))
        route = extract_route(row["text"], observation, resolved)
        if route:
            store_route(connection, row["id"], row["source_key"],
                        row["posted_at"], observation, route)
            found += 1
    connection.commit()
    return found


if __name__ == "__main__":
    import argparse as _argparse

    from .db import connect

    parser = _argparse.ArgumentParser(description="Достроить маршруты по корпусу")
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()
    connection = connect()
    connection.execute("PRAGMA busy_timeout = 10000")
    print("маршрутов записано:", backfill(connection, args.days))
