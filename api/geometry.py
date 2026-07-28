"""Геометрия зон по запросу.

Клиент грузил public/data/districts.json целиком: 14.4 МБ на 2327 полигонов,
из которых в любой момент подсвечены единицы. Здесь то же самое отдаётся
адресно — обзорные регионы в огрублённом виде, районы только активные, —
и стартовая загрузка карты перестаёт зависеть от размера справочника.

Роутер самодостаточен, подключается одной строкой:

    from api.geometry import router as geometry_router
    app.include_router(geometry_router)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import closing
from datetime import timedelta
from typing import Any, Iterable

from fastapi import APIRouter, HTTPException, Query, Response

from pipeline.db import DB_PATH, ROOT
from pipeline.timeutil import now_utc

DATA_DIR = ROOT / "public" / "data"

# Файл-источник для каждого уровня зон. Уровень 'place' полигонов не имеет —
# населённые пункты живут точками и сюда не попадают.
LAYER_FILES = {"region": "regions.json", "district": "districts.json"}

# Окно активности и лимит выборки повторяют api.server.state(): подсветка и
# геометрия обязаны сходиться, иначе карта закрасит зону, полигон которой не
# приехал, или наоборот.
ACTIVE_WINDOW = timedelta(hours=6)
EVENT_LIMIT = 400

# Разрядность координат. 2 знака — примерно 1 км, для обзорного масштаба
# границы региона этого хватает; районы разглядывают ближе, им нужно 3.
REGION_PRECISION = 2
DISTRICT_PRECISION = 3

# Набор активных зон меняется не чаще, чем приходят события, а /geo/active
# дёргается на каждое обновление состояния.
ACTIVE_TTL_SEC = 10.0

router = APIRouter(prefix="/api/v1/geo", tags=["geo"])


# --- Индекс полигонов -------------------------------------------------------

_layers: dict[str, dict[str, dict]] = {}
_layers_lock = threading.Lock()


def _load_layer(level: str) -> dict[str, dict]:
    path = DATA_DIR / LAYER_FILES[level]
    with path.open(encoding="utf-8") as handle:
        collection = json.load(handle)

    index: dict[str, dict] = {}
    for feature in collection.get("features", ()):
        properties = feature.get("properties") or {}
        # properties.id — это и есть zones.source_id, по нему сходятся база и
        # исходные GeoJSON-файлы. feature.id держим как запасной ключ.
        source_id = properties.get("id") or feature.get("id")
        if source_id:
            index[str(source_id)] = feature
    return index


def _layer(level: str) -> dict[str, dict]:
    """Индекс source_id -> feature, один раз на процесс.

    Разбор districts.json стоит секунды и сотни мегабайт, поэтому слой
    поднимается лениво: если эндпойнты геометрии не трогали, платить не за что.
    """
    cached = _layers.get(level)
    if cached is not None:
        return cached
    with _layers_lock:
        # Повторная проверка: пока ждали блокировку, слой мог поднять сосед по
        # пулу потоков, а второй разбор того же файла — чистая потеря памяти.
        cached = _layers.get(level)
        if cached is None:
            cached = _load_layer(level)
            _layers[level] = cached
    return cached


def warmup(levels: Iterable[str] = ("region",)) -> dict[str, int]:
    """Поднять слои заранее, если первый запрос не должен ждать разбора."""
    return {level: len(_layer(level)) for level in levels if level in LAYER_FILES}


# --- Прореживание координат -------------------------------------------------

def _round_position(position: list, precision: int) -> list:
    # +0.0 убирает -0.0, который иначе уезжает в ответ лишним символом.
    return [round(float(value), precision) + 0.0 for value in position]


def _drop_redundant(points: list[list]) -> None:
    """Выбросить вершины, лежащие ровно на отрезке между соседями.

    После снятия на грубую сетку длинные участки границы становятся строго
    прямыми, и половина вершин на них не несёт формы: на регионах это ещё
    четверть веса ответа сверх самого округления. Заодно уходят иглы вида
    A-B-A, из которых берётся часть самопересечений.

    Нижней границы у длины нет намеренно. Кольцо, где лишними оказались все
    вершины, — это кольцо нулевой площади: после округления так схлопываются
    мелкие острова. Останавливаться на трёх вершинах значило бы отдать клиенту
    вырожденный треугольник вместо честного «геометрии не осталось».

    Список принимается незамкнутым и правится на месте.
    """
    index = 0
    while index < len(points):
        before = points[index - 1]
        current = points[index]
        after = points[(index + 1) % len(points)]
        cross = ((current[0] - before[0]) * (after[1] - before[1])
                 - (current[1] - before[1]) * (after[0] - before[0]))
        if cross == 0.0:
            del points[index]
            # Соседи сомкнулись — предыдущая вершина могла тоже стать лишней.
            if index:
                index -= 1
        else:
            index += 1


def _round_ring(ring: list, precision: int) -> list | None:
    """Кольцо полигона с выброшенными точками, слипшимися после округления.

    Без этого шага соседние вершины ближе шага сетки дают дубли, кольцо
    вырождается в отрезок, и разбор такой геометрии падает. None означает,
    что от кольца ничего не осталось.
    """
    out: list[list] = []
    for position in ring:
        rounded = _round_position(position, precision)
        if not out or rounded != out[-1]:
            out.append(rounded)

    # Дальше работаем с незамкнутым списком: иначе первая и последняя вершины
    # считаются разными и стык кольца не чистится.
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    if len(out) < 3:
        return None

    _drop_redundant(out)
    if len(out) < 3:
        return None
    out.append(list(out[0]))
    return out


def _round_polygon(rings: list, precision: int) -> list:
    out = []
    for index, ring in enumerate(rings):
        cleaned = _round_ring(ring, precision)
        if cleaned is None:
            # Без внешнего кольца полигона нет; выродившаяся дырка просто
            # исчезает — на обзорном масштабе она всё равно неразличима.
            if index == 0:
                return []
            continue
        out.append(cleaned)
    return out


def _round_coordinates(node: Any, precision: int) -> Any:
    """Запасной путь для типов вне Polygon/MultiPolygon: рекурсия по спискам."""
    if isinstance(node, (int, float)):
        return round(float(node), precision) + 0.0
    return [_round_coordinates(item, precision) for item in node]


def round_geometry(geometry: dict, precision: int) -> dict | None:
    """Геометрия с прорежёнными координатами или None, если она выродилась.

    Отдать сломанный полигон хуже, чем не отдать никакого: клиент на нём
    спотыкается молча.
    """
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if coordinates is None:
        return None

    if kind == "Polygon":
        rings = _round_polygon(coordinates, precision)
        return {"type": "Polygon", "coordinates": rings} if rings else None

    if kind == "MultiPolygon":
        parts = [part for part in
                 (_round_polygon(polygon, precision) for polygon in coordinates) if part]
        return {"type": "MultiPolygon", "coordinates": parts} if parts else None

    return {"type": kind, "coordinates": _round_coordinates(coordinates, precision)}


def _slim_feature(feature: dict, precision: int, level: str | None) -> dict | None:
    """Только то, чем клиент красит и подписывает полигон."""
    geometry = round_geometry(feature.get("geometry") or {}, precision)
    if geometry is None:
        return None

    properties = feature.get("properties") or {}
    source_id = str(properties.get("id") or feature.get("id") or "")
    slim = {"id": source_id, "name": properties.get("name")}
    # Родительский регион едет вместе с районом: по нему лента находит, что
    # показать, когда в самом районе тихо, а закрашен он областной тревогой.
    if properties.get("region"):
        slim["region"] = properties["region"]
    if level:
        # При levels=district,region иначе не разобрать, что чем красить.
        slim["level"] = level
    return {"type": "Feature", "id": source_id, "properties": slim, "geometry": geometry}


def _collection(features: list[dict]) -> bytes:
    # Компактные разделители — минус около 5 процентов от ответа даром.
    payload = {"type": "FeatureCollection", "features": features}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# --- Активные зоны ----------------------------------------------------------

def _query(sql: str, params: tuple = ()) -> list[dict]:
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params)]


def active_source_ids(levels: tuple[str, ...]) -> dict[str, list[str]]:
    """source_id активных зон по уровням — тем же путём, что и /api/v1/state.

    Событие поднимается по всей цепочке родителей: горит посёлок — горит и
    район, и регион над ним, поэтому берём zone_path целиком.
    """
    since = (now_utc() - ACTIVE_WINDOW).isoformat()
    rows = _query(
        """
        SELECT e.zone_path, e.status
        FROM events e JOIN zones z ON z.id = e.zone_id
        WHERE e.last_seen_at >= ?
        ORDER BY e.last_seen_at DESC LIMIT ?
        """,
        (since, EVENT_LIMIT),
    )

    zone_ids: set[str] = set()
    for row in rows:
        if row["status"] == "resolved":
            continue
        zone_ids.update(json.loads(row["zone_path"] or "[]"))

    result: dict[str, list[str]] = {level: [] for level in levels}
    if not zone_ids:
        return result

    zone_marks = ",".join("?" * len(zone_ids))
    level_marks = ",".join("?" * len(levels))
    rows = _query(
        f"""
        SELECT level, source_id FROM zones
        WHERE id IN ({zone_marks}) AND level IN ({level_marks}) AND source_id IS NOT NULL
        """,
        tuple(zone_ids) + tuple(levels),
    )
    for row in rows:
        result[row["level"]].append(str(row["source_id"]))
    return result


_active_cache: dict[tuple[str, ...], tuple[float, bytes]] = {}
_active_lock = threading.Lock()


def _build_active(levels: tuple[str, ...]) -> bytes:
    by_level = active_source_ids(levels)
    features: list[dict] = []
    for level in levels:
        source_ids = by_level.get(level, ())
        if not source_ids:
            # Разбор districts.json стоит 200 МБ и держится до конца жизни
            # процесса. В тихие часы активных районов нет вовсе, и платить
            # за пустой ответ нечем: слой поднимаем, только когда есть что искать.
            continue
        precision = REGION_PRECISION if level == "region" else DISTRICT_PRECISION
        index = _layer(level)
        for source_id in source_ids:
            feature = index.get(source_id)
            if feature is None:
                # Зона есть в базе, полигона под неё в справочнике нет.
                # Пропускаем молча: подписи и точки события клиент уже получил.
                continue
            slim = _slim_feature(feature, precision, level)
            if slim is not None:
                features.append(slim)
    return _collection(features)


def _cached_active(levels: tuple[str, ...]) -> bytes:
    now = time.monotonic()
    with _active_lock:
        entry = _active_cache.get(levels)
        if entry and entry[0] > now:
            return entry[1]
    # Сборку держим вне блокировки: запрос к базе не должен стопорить чтение
    # кеша другими потоками. Гонка тут безобидна — оба потока дадут один ответ.
    payload = _build_active(levels)
    with _active_lock:
        _active_cache[levels] = (time.monotonic() + ACTIVE_TTL_SEC, payload)
    return payload


def _parse_levels(raw: str) -> tuple[str, ...]:
    levels = [item.strip().lower() for item in raw.split(",") if item.strip()]
    unknown = [level for level in levels if level not in LAYER_FILES]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown levels: {','.join(unknown)}; expected {'|'.join(LAYER_FILES)}",
        )
    # dict.fromkeys вместо set: порядок уровней задаёт порядок отрисовки.
    return tuple(dict.fromkeys(levels)) or ("district",)


# --- Эндпойнты --------------------------------------------------------------

_regions_payload: bytes | None = None
_regions_lock = threading.Lock()


@router.get("/regions.geojson")
def regions_geojson() -> Response:
    """Все 89 регионов разом: их мало и они нужны сразу, при первом кадре."""
    global _regions_payload
    if _regions_payload is None:
        with _regions_lock:
            if _regions_payload is None:
                features = [
                    slim for slim in
                    (_slim_feature(feature, REGION_PRECISION, None)
                     for feature in _layer("region").values())
                    if slim is not None
                ]
                _regions_payload = _collection(features)
    return Response(
        content=_regions_payload,
        media_type="application/json",
        # Границы регионов не меняются в течение жизни процесса, а файл весит
        # мегабайты — пусть браузер не перекачивает их на каждую перезагрузку.
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/active")
def active_geojson(levels: str = Query("district")) -> Response:
    """Полигоны только тех зон, где сейчас что-то происходит."""
    payload = _cached_active(_parse_levels(levels))
    return Response(
        content=payload,
        media_type="application/json",
        # Свежесть здесь важнее экономии: TTL уже ограничил нагрузку на базу.
        headers={"Cache-Control": "no-store"},
    )
