"""Страница «Маршруты БПЛА»: данные графа коридоров и посадочная страница.

    PYTHONPATH=.:ingest ingest/.venv/bin/python -m scripts.routes_page

Разделение труда: этот скрипт считает граф и пишет два артефакта —

  • dist/data/corridors.json — узлы, рёбра, веса, направления; его рисует
    OpenLayers-модуль src/marshruty/main.ts прямо на странице, с той же
    тайловой подложкой, что у живой карты. Самодельной SVG-картографии
    здесь больше нет: границы и берега рисует настоящий движок;
  • dist/marshruty/index.html — SEO-текст, галерея коридоров и контейнер
    карты.

Граф: все точки маршрутов и восстановленных переходов кластеризуются в
узлы (~10 км), одинаковые плечи складываются в рёбра с весом и
преобладающим направлением. Источники ребра честно разложены в данных:
сколько пересказано из сообщений, сколько восстановлено по
последовательности фиксаций (вторая в пределах 50 минут и 130 км от
первой). Прибрежные рёбра получают дугу над морем: сторона выбирается
проверкой «какая сторона — не суша» по полигонам субъектов.

Вызывается из scripts.seo_pages при каждой пересборке посадочных —
ежечасно по таймеру и при выкатке.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.db import DB_PATH, ROOT
from pipeline.textnorm import short_name
from pipeline.timeutil import MSK, now_utc

SITE = "https://tihoenebo.com"
OUT = ROOT / "dist"
DATA = ROOT / "public" / "data"

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")

# Коридор попадает в галерею от десяти повторов: единичный маршрут — эпизод,
# десять — закономерность. Карточек не больше шестидесяти.
MIN_CORRIDOR = 10
MAX_CARDS = 60
# Ребро графа живёт от двух повторов: тройка оставляла север пустым,
# хотя запуски там почти ежедневно — просто ленты реже повторяются.
MIN_EDGE = 2
# Потолок — страховка от разрастания файла, а не фильтр: на боевом корпусе
# рёбер вдвое меньше. Прежние 700 резали именно длинный хвост лёгких плеч,
# которыми дальние трассы и сшиваются: снятие потолка дало вдвое больше
# трасс и дальний конец 676 км вместо 401.
MAX_EDGES = 4000
# Трассы — верх списка по важности (повторы × длина): видны на обзоре,
# остальное движок показывает при приближении.
TRUNK_CHAINS = 60
# Продолжение трассы: следующее плечо не разворачивается больше чем на
# ~75°. Порог веса низкий намеренно: борт летит от берега вглубь, и чем
# дальше, тем реже про него пишут — требовать треть от предыдущего плеча
# значило обрубать маршрут ровно там, где он становится интересным.
CHAIN_MAX_TURN = math.pi * 0.42
CHAIN_MIN_RATIO = 0.1
# Подписи путевых точек тремя ярусами: крупные всегда, средние с зума 6.4,
# сёла — с 8. Без третьего яруса при приближении подписей не прибавлялось.
LABELS_ALWAYS = 26
LABELS_ZOOMED = 140
LABELS_CLOSE = 900

# --- Восстановление волн по фиксациям ---------------------------------
# Пороги — от физики украинских дальнобойных БПЛА самолётной схемы
# (Хорнет, Бобр, Дартс, Лютый): крейсерская скорость около 150 км/ч.
# Продолжением фиксации считается следующая, до которой борт мог долететь
# с правдоподобной скоростью и без разворота: 80-260 км/ч и не круче 70°.
# На корпусе это даёт 622 трека медианной длиной 167 км — с настоящими
# цепочками вроде «Штормово → Зеленовка → Тарасовский → … → Новониколаевский
# район»: 762 км за 230 минут, то есть 199 км/ч.
TRACK_SPEED_KMH = (80.0, 260.0)
TRACK_CRUISE_KMH = 150.0
TRACK_GAP_MINUTES = (3, 35)
TRACK_MAX_TURN = math.radians(70)
# Трек короче трёх точек — не волна, а пара совпавших сообщений.
TRACK_MIN_POINTS = 3

# Кластеризация точек в узлы: шаг сетки ~10 км.
NODE_LAT_STEP = 0.09


def plural(count: int, one: str, few: str, many: str) -> str:
    mod100, mod10 = abs(count) % 100, abs(count) % 10
    if 11 <= mod100 <= 14:
        return many
    if mod10 == 1:
        return one
    if 2 <= mod10 <= 4:
        return few
    return many


def day_word(iso: str) -> str:
    stamp = datetime.fromisoformat(iso).astimezone(MSK)
    return f"{stamp.day} {MONTHS[stamp.month - 1]}"


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = (b[1] - a[1]) * 111.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    dy = (b[0] - a[0]) * 111.0
    return math.hypot(dx, dy)


class Projection:
    """Равнопромежуточная проекция в пиксели SVG — для карточек галереи."""

    def __init__(self, bbox: tuple[float, float, float, float],
                 width: int, height: int, pad: float = 0.0,
                 precision: int = 1):
        lat0, lon0, lat1, lon1 = bbox
        self.lat0, self.lon0, self.lat1, self.lon1 = lat0, lon0, lat1, lon1
        self.cos = math.cos(math.radians((lat0 + lat1) / 2))
        span_x = (lon1 - lon0) * self.cos
        span_y = lat1 - lat0
        self.scale = min((width - 2 * pad) / span_x, (height - 2 * pad) / span_y)
        self.width = width
        self.height = height
        self.pad = pad
        self.precision = precision

    def xy(self, lat: float, lon: float) -> tuple[float, float]:
        x = self.pad + (lon - self.lon0) * self.cos * self.scale
        y = self.pad + (self.lat1 - lat) * self.scale
        if self.precision == 0:
            return int(round(x)), int(round(y))
        return round(x, self.precision), round(y, self.precision)

    def inside(self, lat: float, lon: float) -> bool:
        return self.lat0 <= lat <= self.lat1 and self.lon0 <= lon <= self.lon1


class Land:
    """Полигоны субъектов: подложка мини-карт и ответ «это суша?».

    Второе нужно прибрежным рёбрам: «Туапсе — Сочи» борт идёт над морем,
    и дуга должна выгибаться в сторону воды, а не вглубь берега.
    """

    def __init__(self) -> None:
        self.rings: list[tuple[tuple[float, float, float, float], list]] = []
        try:
            collection = json.loads(
                (DATA / "regions.json").read_text(encoding="utf-8"))
        except OSError:
            return
        for feature in collection.get("features", []):
            # Акватории лежат в том же файле — но для вопроса «это суша?»
            # они как раз ответ «нет»: без пропуска Азовское и Чёрное моря
            # считались бы сушей, и прибрежные дуги выгибались бы на берег.
            if (feature.get("properties") or {}).get("kind") == "sea":
                continue
            geometry = feature.get("geometry") or {}
            polygons = (geometry.get("coordinates", [])
                        if geometry.get("type") == "MultiPolygon"
                        else [geometry.get("coordinates", [])])
            for polygon in polygons:
                for ring in polygon:
                    if len(ring) < 4:
                        continue
                    lons = [p[0] for p in ring]
                    lats = [p[1] for p in ring]
                    self.rings.append(
                        ((min(lats), min(lons), max(lats), max(lons)), ring))

    def is_land(self, lat: float, lon: float) -> bool:
        for (lat0, lon0, lat1, lon1), ring in self.rings:
            if not (lat0 <= lat <= lat1 and lon0 <= lon <= lon1):
                continue
            hit = False
            for (ax, ay), (bx, by) in zip(ring, ring[1:]):
                if (ay > lat) != (by > lat):
                    cross = (bx - ax) * (lat - ay) / (by - ay) + ax
                    if lon < cross:
                        hit = not hit
            if hit:
                return True
        return False

    def svg_path(self, projection: Projection, step: float = 3.0) -> str:
        """Контуры суши в кадре мини-карты, прорежённые до читаемости.

        Точки далеко за кадром прижимаются к его границе: форма там не
        видна, а без прижатия контур Краснодарского края тащил в каждую
        мини-карту тысячи невидимых точек.
        """
        margin = 60.0
        paths = []
        for _, ring in self.rings:
            if not any(projection.inside(lat, lon) for lon, lat in ring):
                continue
            previous = None
            parts = []
            for lon, lat in ring:
                x, y = projection.xy(lat, lon)
                x = min(max(x, -margin), projection.width + margin)
                y = min(max(y, -margin), projection.height + margin)
                if previous and abs(x - previous[0]) < step \
                        and abs(y - previous[1]) < step:
                    continue
                parts.append(f"{'M' if previous is None else 'L'}{x} {y}")
                previous = (x, y)
            if len(parts) > 2:
                paths.append("".join(parts) + "Z")
        return " ".join(paths)

    def sea_control(self, a: tuple[float, float],
                    b: tuple[float, float]) -> tuple[float, float] | None:
        """Точка над водой сбоку от середины плеча — управляющая для дуги."""
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        span = _km(a, b)
        if span < 20:
            return None
        dlat = b[0] - a[0]
        dlon = (b[1] - a[1]) * math.cos(math.radians(mid[0]))
        norm = math.hypot(dlat, dlon) or 1.0
        k = 0.18 * (span / 111.0)
        for side in (1, -1):
            candidate = (mid[0] + side * (-dlon) / norm * k,
                         mid[1] + side * dlat / norm * k
                         / math.cos(math.radians(mid[0])))
            if not self.is_land(*candidate):
                return candidate
        return None


def load_routes(connection: sqlite3.Connection) -> list[dict]:
    routes = []
    for row in connection.execute(
            "SELECT points, posted_at, threat_type FROM routes"):
        points = json.loads(row["points"])
        if len(points) < 2:
            continue
        routes.append({
            "points": [(p[0], p[1], p[2]) for p in points],
            "posted_at": row["posted_at"],
            "threat": row["threat_type"] or "unknown",
        })
    return routes


def _bearing_ll(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.atan2((b[1] - a[1])
                      * math.cos(math.radians((a[0] + b[0]) / 2)),
                      b[0] - a[0])


def _turn(a: float, b: float) -> float:
    diff = abs(a - b) % (2 * math.pi)
    return min(diff, 2 * math.pi - diff)


def reconstruct_tracks(connection: sqlite3.Connection) -> list[list[dict]]:
    """Волны, восстановленные из собственной базы фиксаций.

    Налёт идёт волнами: борт видят в одном районе, через двадцать минут в
    соседнем, ещё через двадцать в третьем. Здесь эта цепочка собирается
    обратно — жадно, от каждой ещё не занятой фиксации: продолжением
    считается следующая, до которой борт мог долететь с правдоподобной
    скоростью (80-260 км/ч при крейсерских 150) и без разворота круче 70°.
    Из кандидатов берётся самый «ровный» — ближе к крейсерской скорости и
    с меньшим доворотом. Каждая фиксация принадлежит одному треку.

    Это догадка, а не пересказ источника, — и на карте она помечена как
    вычисленная. Но догадка физическая: пороги взяты от дальнобойных БПЛА
    самолётной схемы, а не подобраны на глаз.

    Акватории идут наравне с точечными зонами: «БПЛА над Азовским морем» —
    такое же наблюдение, только над водой.
    """
    rows = connection.execute(
        """SELECT e.zone_id, e.lat, e.lon, e.first_seen_at, z.name_ru
           FROM events e JOIN zones z ON z.id = e.zone_id
           WHERE e.severity >= 8 AND e.lat IS NOT NULL
             AND (z.level != 'region' OR z.source_id LIKE '%-sea')
           ORDER BY e.first_seen_at""").fetchall()
    events = [{
        "at": datetime.fromisoformat(r["first_seen_at"]).timestamp(),
        "lat": r["lat"], "lon": r["lon"],
        "zone": r["zone_id"], "name": short_name(r["name_ru"]),
    } for r in rows]
    times = [event["at"] for event in events]
    low_s, high_s = TRACK_GAP_MINUTES[0] * 60, TRACK_GAP_MINUTES[1] * 60

    taken = [False] * len(events)
    tracks: list[list[dict]] = []
    for index in range(len(events)):
        if taken[index]:
            continue
        taken[index] = True
        track = [events[index]]
        heading: float | None = None
        while True:
            last = track[-1]
            lo = bisect.bisect_left(times, last["at"] + low_s)
            hi = bisect.bisect_left(times, last["at"] + high_s)
            best = None
            for candidate in range(lo, hi):
                if taken[candidate]:
                    continue
                nxt = events[candidate]
                if nxt["zone"] == last["zone"]:
                    continue
                hours = (nxt["at"] - last["at"]) / 3600
                if hours <= 0:
                    continue
                distance = _km((last["lat"], last["lon"]),
                               (nxt["lat"], nxt["lon"]))
                speed = distance / hours
                if not (TRACK_SPEED_KMH[0] <= speed <= TRACK_SPEED_KMH[1]):
                    continue
                course = _bearing_ll((last["lat"], last["lon"]),
                                     (nxt["lat"], nxt["lon"]))
                doglegs = _turn(heading, course) if heading is not None else 0.0
                if doglegs > TRACK_MAX_TURN:
                    continue
                score = (abs(speed - TRACK_CRUISE_KMH) / TRACK_CRUISE_KMH
                         + doglegs / math.pi * 1.5)
                if best is None or score < best[0]:
                    best = (score, candidate, course)
            if best is None:
                break
            taken[best[1]] = True
            track.append(events[best[1]])
            heading = best[2]
        if len(track) >= TRACK_MIN_POINTS:
            tracks.append(track)
    return tracks


def build_corridors(routes: list[dict], minimum: int = MIN_CORRIDOR,
                    skip_names: frozenset[str] | set[str] = frozenset(),
                    ) -> list[dict]:
    """Коридор — все маршруты с одинаковыми началом и концом.

    skip_names убирает из галереи коридоры, упирающиеся в акваторию:
    карточка «Новороссийск → Чёрное море» показывает не путь, а то, что
    борт ушёл за береговую черту, — на общей карте это видно и без неё.
    """
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for route in routes:
        key = (route["points"][0][2], route["points"][-1][2])
        grouped[key].append(route)

    corridors = []
    for (start, end), items in grouped.items():
        if len(items) < minimum or start == end:
            continue
        if short_name(start) in skip_names or short_name(end) in skip_names:
            continue
        stamps = sorted(item["posted_at"] for item in items)
        hours = Counter(
            datetime.fromisoformat(item["posted_at"]).astimezone(MSK).hour
            for item in items)
        night = sum(v for h, v in hours.items() if h >= 21 or h < 6)
        threats = Counter(item["threat"] for item in items)
        months = sorted({stamp[:7] for stamp in stamps})
        variants = Counter(
            tuple(p[2] for p in item["points"]) for item in items)
        face_names = variants.most_common(1)[0][0]
        face = next(item["points"] for item in items
                    if tuple(p[2] for p in item["points"]) == face_names)
        corridors.append({
            "start": short_name(start), "end": short_name(end),
            "count": len(items),
            "first": stamps[0], "last": stamps[-1],
            "months": months,
            "night_share": round(night * 100 / len(items)),
            "threats": threats,
            "face": face,
            "routes": items,
        })
    corridors.sort(key=lambda c: (-c["count"], c["start"]))
    return corridors


# --- Граф ---------------------------------------------------------------


def _cell(lat: float, lon: float) -> tuple[int, int]:
    lon_step = NODE_LAT_STEP / math.cos(math.radians(lat))
    return (int(lat / NODE_LAT_STEP), int(lon / lon_step))


def build_graph(routes: list[dict], tracks: list[list[dict]]) -> tuple[
        list[dict], list[dict]]:
    """Узлы и рёбра: все плечи, слитые по кластерам точек.

    Узел — ячейка сетки ~10 км: имя берётся у самой частой точки в ней,
    координата — средняя. Ребро A—B хранит оба направления и раскладку
    источников (пересказ / восстановлено); рисуется в преобладающую
    сторону.
    """
    members: dict[tuple[int, int], dict] = {}

    def visit(lat: float, lon: float, name: str) -> tuple[int, int]:
        cell = _cell(lat, lon)
        entry = members.setdefault(
            cell, {"lat": 0.0, "lon": 0.0, "n": 0, "names": Counter()})
        entry["lat"] += lat
        entry["lon"] += lon
        entry["n"] += 1
        entry["names"][short_name(name)] += 1
        return cell

    legs: Counter = Counter()
    named_legs: Counter = Counter()
    for route in routes:
        cells = [visit(lat, lon, name) for lat, lon, name in route["points"]]
        for a, b in zip(cells, cells[1:]):
            if a == b:
                continue
            legs[(a, b)] += 1
            named_legs[(a, b)] += 1
    # Восстановленные волны дают такие же плечи, только помеченные
    # вычисленными: одна волна — один проход цепочки.
    for track in tracks:
        cells = [visit(point["lat"], point["lon"], point["name"])
                 for point in track]
        for a, b in zip(cells, cells[1:]):
            if a != b:
                legs[(a, b)] += 1

    edges: dict[tuple, dict] = {}
    for (a, b), count in legs.items():
        key = (a, b) if a <= b else (b, a)
        edge = edges.setdefault(key, {"ab": 0, "ba": 0,
                                      "named": 0, "computed": 0})
        forward = (a, b) == key
        edge["ab" if forward else "ba"] += count
        named = named_legs.get((a, b), 0)
        edge["named"] += named
        edge["computed"] += count - named

    used_cells: set[tuple[int, int]] = set()
    kept = []
    for (a, b), edge in edges.items():
        total = edge["ab"] + edge["ba"]
        if total < MIN_EDGE:
            continue
        kept.append((a, b, edge, total))
        used_cells.add(a)
        used_cells.add(b)

    node_index: dict[tuple[int, int], int] = {}
    nodes: list[dict] = []
    for cell in used_cells:
        entry = members[cell]
        node_index[cell] = len(nodes)
        nodes.append({
            "lat": entry["lat"] / entry["n"],
            "lon": entry["lon"] / entry["n"],
            "name": entry["names"].most_common(1)[0][0],
            "weight": 0,
        })

    result_edges = []
    for a, b, edge, total in kept:
        ia, ib = node_index[a], node_index[b]
        if edge["ba"] > edge["ab"]:
            ia, ib = ib, ia
            edge["ab"], edge["ba"] = edge["ba"], edge["ab"]
        nodes[ia]["weight"] += total
        nodes[ib]["weight"] += total
        result_edges.append({
            "a": ia, "b": ib, "count": total,
            "forward": edge["ab"], "backward": edge["ba"],
            "named": edge["named"], "computed": edge["computed"],
        })
    result_edges.sort(key=lambda e: -e["count"])
    return nodes, result_edges[:MAX_EDGES]


def smooth_path(points: list[tuple[float, float]],
                steps: int = 12) -> list[list[float]]:
    """Сгладить ломаную по путевым точкам в траекторию полёта.

    Излом на карте — артефакт: борт самолётной схемы на крейсерских
    150 км/ч (41,7 м/с) с креном 25° разворачивается радиусом
    v²/(g·tg φ) ≈ 380 метров — на масштабе карты это неразличимо, то есть
    настоящий путь между районами кривой быть не может, а угловатым тем
    более. Ломаная — лишь способ, которым мы соединили точки, и её надо
    заменить гладкой линией, проходящей через те же точки.

    Кривая — центростремительный Catmull-Rom (α=0,5): проходит ровно через
    путевые точки и, в отличие от равномерного, не даёт петель и заносов
    на резких поворотах, а именно они здесь и встречаются.
    """
    if len(points) < 3:
        return [[round(lat, 4), round(lon, 4)] for lat, lon in points]

    # Дублируем концы: кривой нужны соседи слева и справа от каждого звена.
    padded = [points[0]] + list(points) + [points[-1]]
    result: list[list[float]] = []
    for index in range(len(padded) - 3):
        p0, p1, p2, p3 = padded[index:index + 4]

        def knot(previous: float, a: tuple[float, float],
                 b: tuple[float, float]) -> float:
            # α=0,5 — корень из расстояния: длинное звено не перетягивает
            # кривую на себя.
            return previous + max(math.dist(a, b), 1e-9) ** 0.5

        t0 = 0.0
        t1 = knot(t0, p0, p1)
        t2 = knot(t1, p1, p2)
        t3 = knot(t2, p2, p3)
        for step in range(steps):
            t = t1 + (t2 - t1) * step / steps
            a1 = [((t1 - t) * p0[k] + (t - t0) * p1[k]) / (t1 - t0)
                  for k in (0, 1)]
            a2 = [((t2 - t) * p1[k] + (t - t1) * p2[k]) / (t2 - t1)
                  for k in (0, 1)]
            a3 = [((t3 - t) * p2[k] + (t - t2) * p3[k]) / (t3 - t2)
                  for k in (0, 1)]
            b1 = [((t2 - t) * a1[k] + (t - t0) * a2[k]) / (t2 - t0)
                  for k in (0, 1)]
            b2 = [((t3 - t) * a2[k] + (t - t1) * a3[k]) / (t3 - t1)
                  for k in (0, 1)]
            point = [((t2 - t) * b1[k] + (t - t1) * b2[k]) / (t2 - t1)
                     for k in (0, 1)]
            result.append([round(point[0], 4), round(point[1], 4)])
    result.append([round(points[-1][0], 4), round(points[-1][1], 4)])
    return result


def assemble_chains(nodes: list[dict], edges: list[dict]) -> list[list[dict]]:
    """Склеить рёбра в трассы: много коротких стрелок — одна длинная.

    Жадная сборка от самого тяжёлого свободного ребра: трасса растёт
    вперёд и назад, пока у крайнего узла есть непотраченное продолжение
    без резкого разворота и с сопоставимым весом. Каждое ребро живёт
    ровно в одной трассе — стрелки не дублируются.
    """
    outgoing: dict[int, list[dict]] = defaultdict(list)
    incoming: dict[int, list[dict]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["a"]].append(edge)
        incoming[edge["b"]].append(edge)

    used: set[int] = set()
    chains: list[list[dict]] = []

    def bearing_of(edge: dict) -> float:
        a, b = nodes[edge["a"]], nodes[edge["b"]]
        return _bearing_ll((a["lat"], a["lon"]), (b["lat"], b["lon"]))

    def extend(edge: dict, forward: bool) -> list[dict]:
        tail: list[dict] = []
        current = edge
        while True:
            node = current["b"] if forward else current["a"]
            pool = outgoing[node] if forward else incoming[node]
            best = None
            for candidate in pool:
                if id(candidate) in used:
                    continue
                if candidate["count"] < current["count"] * CHAIN_MIN_RATIO:
                    continue
                turn = _turn(bearing_of(current), bearing_of(candidate))
                if turn > CHAIN_MAX_TURN:
                    continue
                score = candidate["count"] * (1 - turn / math.pi)
                if best is None or score > best[0]:
                    best = (score, candidate)
            if best is None:
                return tail
            current = best[1]
            used.add(id(current))
            tail.append(current)

    for edge in sorted(edges, key=lambda e: -e["count"]):
        if id(edge) in used:
            continue
        used.add(id(edge))
        back = extend(edge, forward=False)
        ahead = extend(edge, forward=True)
        chains.append(list(reversed(back)) + [edge] + ahead)
    return chains


def export_graph(nodes: list[dict], edges: list[dict], land: Land,
                 anchors: dict[tuple[str, str], str], stats: dict) -> dict:
    """corridors.json: готовые трассы — клиенту остаётся нарисовать линии.

    Трасса — цепочка рёбер; её точки собраны с морскими дугами там, где
    плечо идёт вдоль берега. Важность трассы — повторы × длина: по
    чистому счёту в топ пролезали десятикилометровые прыжки между сёлами
    одного горячего района, а Анапа — Сочи с её 250 км уходила вниз.
    """
    chains = assemble_chains(nodes, edges)
    peak = max((e["count"] for e in edges), default=1)

    def chain_km(chain: list[dict]) -> float:
        return sum(_km((nodes[e["a"]]["lat"], nodes[e["a"]]["lon"]),
                       (nodes[e["b"]]["lat"], nodes[e["b"]]["lon"]))
                   for e in chain)

    def importance(chain: list[dict]) -> float:
        top = max(e["count"] for e in chain)
        return top * max(chain_km(chain), 15.0)

    chains.sort(key=importance, reverse=True)

    out_chains = []
    label_weight: Counter = Counter()
    for rank, chain in enumerate(chains):
        # Путевые точки трассы: узлы плюс морская отводка там, где плечо
        # идёт вдоль берега, — и всё это одной гладкой линией.
        waypoints: list[tuple[float, float]] = [
            (nodes[chain[0]["a"]]["lat"], nodes[chain[0]["a"]]["lon"])]
        for edge in chain:
            a, b = nodes[edge["a"]], nodes[edge["b"]]
            control = land.sea_control((a["lat"], a["lon"]),
                                       (b["lat"], b["lon"]))
            if control:
                waypoints.append(control)
            waypoints.append((b["lat"], b["lon"]))
        points = smooth_path(waypoints)
        top = max(e["count"] for e in chain)
        named = sum(e["named"] for e in chain)
        computed = sum(e["computed"] for e in chain)
        backward = sum(e["backward"] for e in chain)
        start_name = nodes[chain[0]["a"]]["name"]
        end_name = nodes[chain[-1]["b"]]["name"]
        via = [nodes[e["a"]]["name"] for e in chain[1:]]
        for e in chain:
            label_weight[e["a"]] += e["count"]
            label_weight[e["b"]] += e["count"]
        out_chains.append({
            "pts": points,
            "from": start_name, "to": end_name,
            "via": via[:6],
            "n": top,
            "nm": named, "cp": computed, "r": backward,
            # Доля восстановленного: по ней клиент красит трассу — жёлтым
            # то, что собрано по нашим волнам, а не пересказано источником.
            "cs": round(computed / max(named + computed, 1), 3),
            "s": round(math.log1p(top) / math.log1p(peak), 3),
            "t": 1 if rank < TRUNK_CHAINS else 0,
            **({"kor": anchors[(start_name, end_name)]}
               if (start_name, end_name) in anchors else {}),
        })

    # Подписи путевых точек: без кружков, только имена — крупные всегда,
    # остальные при приближении.
    ranked = label_weight.most_common(LABELS_CLOSE)
    labels = []
    for position, (index, weight) in enumerate(ranked):
        node = nodes[index]
        labels.append({
            "lat": round(node["lat"], 4), "lon": round(node["lon"], 4),
            "name": node["name"],
            "t": (1 if position < LABELS_ALWAYS
                  else 2 if position < LABELS_ZOOMED else 3),
        })

    return {"generated": now_utc().isoformat(), "stats": stats,
            "chains": out_chains, "labels": labels}


def flow_path(points_xy: list[tuple[float, float]],
              control_xy: tuple[float, float] | None = None) -> str:
    """Гладкий путь для мини-карт галереи — тем же сплайном, что и трассы.

    Углов у настоящей траектории нет (см. smooth_path), поэтому и здесь
    ломаная сглаживается, а морская отводка входит обычной путевой точкой.
    """
    chain = list(points_xy)
    if control_xy is not None and len(chain) == 2:
        chain = [chain[0], control_xy, chain[1]]
    if len(chain) == 2:
        (x0, y0), (x1, y1) = chain
        return f"M{x0} {y0}L{x1} {y1}"
    curve = smooth_path([(y, x) for x, y in chain], steps=10)
    parts = [f"M{round(curve[0][1], 1)} {round(curve[0][0], 1)}"]
    parts.extend(f"L{round(point[1], 1)} {round(point[0], 1)}"
                 for point in curve[1:])
    return "".join(parts)


def arrow_head(tail: tuple[float, float], tip: tuple[float, float],
               size: float, color: str) -> str:
    """Заливной наконечник для карточек галереи."""
    angle = math.atan2(tip[1] - tail[1], tip[0] - tail[0])
    left = (tip[0] - size * math.cos(angle - 0.42),
            tip[1] - size * math.sin(angle - 0.42))
    right = (tip[0] - size * math.cos(angle + 0.42),
             tip[1] - size * math.sin(angle + 0.42))
    return (f'<path d="M{round(left[0], 1)} {round(left[1], 1)} '
            f'L{tip[0]} {tip[1]} L{round(right[0], 1)} {round(right[1], 1)} '
            f'Z" fill="{color}" fill-opacity="0.85" />')


def card_svg(corridor: dict, land: Land) -> str:
    """Мини-карта коридора: бледные варианты, лицо — гладкой стрелкой."""
    lats = [p[0] for r in corridor["routes"] for p in r["points"]]
    lons = [p[1] for r in corridor["routes"] for p in r["points"]]
    spread = max(max(lats) - min(lats), (max(lons) - min(lons)) * 0.65, 0.2)
    pad_deg = spread * 0.24
    bbox = (min(lats) - pad_deg, min(lons) - pad_deg / 0.65,
            max(lats) + pad_deg, max(lons) + pad_deg / 0.65)
    width, height = 280, 170
    projection = Projection(bbox, width, height, pad=12)

    outline = land.svg_path(projection, step=3.0)
    base = (f'<path d="{outline}" fill="#161d1a" stroke="#333f39" '
            f'stroke-width="0.8" />' if outline else "")

    seen: set[str] = set()
    faint = []
    for route in corridor["routes"][:60]:
        xy = [projection.xy(p[0], p[1]) for p in route["points"]]
        path = flow_path(xy)
        if path in seen:
            continue
        seen.add(path)
        faint.append(f'<path d="{path}" />')

    face = corridor["face"]
    chain = [(p[0], p[1]) for p in face]
    control = land.sea_control(chain[0], chain[-1]) if len(chain) == 2 else None
    face_xy = [projection.xy(lat, lon) for lat, lon in chain]
    control_xy = projection.xy(*control) if control else None
    tail_xy = control_xy if control_xy else face_xy[-2]

    labels = []
    for (x, y), name, kind in ((face_xy[0], corridor["start"], "start"),
                               (face_xy[-1], corridor["end"], "end")):
        align = "start" if x < width / 2 else "end"
        ty = y - 8 if y > 26 else y + 16
        labels.append(
            f'<circle cx="{x}" cy="{y}" r="3" '
            f'fill="{"#ff8d97" if kind == "end" else "#9fd4b0"}" />'
            f'<text x="{x}" y="{ty}" text-anchor="{align}" fill="#dfe6df" '
            f'font-size="11">{escape(name)}</text>')

    return (f'<svg viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Коридор {escape(corridor["start"])} — '
            f'{escape(corridor["end"])}" xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="{width}" height="{height}" fill="#0c100f" rx="8" />'
            + base
            + f'<g fill="none" stroke="#e9404f" stroke-opacity="0.13" '
              f'stroke-width="1.6" stroke-linecap="round">{"".join(faint)}</g>'
            + f'<path d="{flow_path(face_xy, control_xy)}" fill="none" '
              f'stroke="#e9404f" stroke-width="2.6" stroke-linecap="round" '
              f'stroke-linejoin="round" />'
            + arrow_head(tail_xy, face_xy[-1], 9, "#ff8d97")
            + "".join(labels) + "</svg>")


THREAT_WORDS = {"uav": "БПЛА", "fpv": "FPV", "rocket": "ракеты",
                "kab": "КАБ", "bek": "БЭК", "aviation": "авиация"}


def card_html(corridor: dict, land: Land, anchor: str) -> str:
    threat_keys = [k for k, _ in corridor["threats"].most_common(2)
                   if k != "unknown"]
    threats = ", ".join(THREAT_WORDS.get(k, k) for k in threat_keys) or "БПЛА"
    months = len(corridor["months"])
    stability = (f"{months} {plural(months, 'месяц', 'месяца', 'месяцев')} подряд"
                 if months > 1 else "в этом месяце")
    return f"""
    <figure class="corridor" id="{anchor}">
      {card_svg(corridor, land)}
      <figcaption>
        <b>{escape(corridor["start"])} → {escape(corridor["end"])}</b>
        <span>{corridor["count"]} {plural(corridor["count"], "маршрут", "маршрута", "маршрутов")}
        · {stability} · последний {day_word(corridor["last"])}
        · {corridor["night_share"]}% ночью · {escape(threats)}</span>
      </figcaption>
    </figure>"""


def build_page(routes: list[dict], tracks: list[list[dict]],
               land: Land, updated: str,
               skip_names: frozenset[str] | set[str] = frozenset(),
               versions: dict[str, str] | None = None) -> str:
    versions = versions or {}
    js_v = f'?v={versions["js"]}' if versions.get("js") else ""
    css_v = f'?v={versions["css"]}' if versions.get("css") else ""
    data_v = versions.get("data", "")
    corridors = build_corridors(routes, skip_names=skip_names)[:MAX_CARDS]

    total = len(routes)
    night = sum(
        1 for r in routes
        if datetime.fromisoformat(r["posted_at"]).astimezone(MSK).hour >= 21
        or datetime.fromisoformat(r["posted_at"]).astimezone(MSK).hour < 6)
    night_share = round(night * 100 / total) if total else 0
    first = min((r["posted_at"] for r in routes), default="")
    span_days = ((now_utc() - datetime.fromisoformat(first)).days
                 if first else 0)
    waves = len(tracks)
    wave_points = sum(len(track) for track in tracks)

    title = "Маршруты БПЛА — повторяющиеся коридоры на карте"
    description = (
        f"{total} маршрутов БПЛА за {span_days} дней из открытых сообщений "
        f"плюс {waves} волн, восстановленных по движению фиксаций: "
        f"интерактивная карта коридоров и галерея самых устойчивых.")
    url = f"{SITE}/marshruty/"
    breadcrumb_ld = json.dumps({
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1,
             "name": "Карта обстановки", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2,
             "name": "Маршруты", "item": url},
        ],
    }, ensure_ascii=False)
    webpage_ld = json.dumps({
        "@context": "https://schema.org", "@type": "WebPage", "url": url,
        "name": title, "description": description,
        "isPartOf": {"@type": "WebSite", "name": "Тихое небо",
                     "url": f"{SITE}/"},
    }, ensure_ascii=False)

    cards = "".join(card_html(c, land, f"kor-{i}")
                    for i, c in enumerate(corridors))

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
    <link rel="stylesheet" href="/assets/marshruty-map.css{css_v}" />
    <style>
      body {{ margin:0; background:#0b0f0e; color:#e6ebe6;
             font:16px/1.6 Inter, system-ui, -apple-system, sans-serif; }}
      main {{ max-width:1120px; margin:0 auto; padding:40px 20px 80px; }}
      h1 {{ font-size:29px; line-height:1.25; margin:0 0 14px; max-width:760px; }}
      h2 {{ font-size:19px; margin:38px 0 10px; }}
      p {{ color:#aab4ad; max-width:760px; }}
      nav.crumbs {{ font-size:13px; color:#7d8a83; margin:0 0 18px; }}
      nav.crumbs a {{ color:#9fd4b0; text-decoration:none; }}
      a.map {{ display:inline-block; margin:16px 0 8px; padding:13px 22px;
              background:#e93e4e; color:#fff; text-decoration:none;
              border-radius:10px; font-weight:600; }}
      #routes-map {{ height:640px; margin:26px 0 8px; border-radius:12px;
                    overflow:hidden; border:1px solid rgba(255,255,255,.07);
                    background:#0c100f; position:relative; }}
      @media (max-width:700px) {{ #routes-map {{ height:440px; }} }}
      .map-note {{ font-size:13px; color:#7d8a83; margin-top:8px; }}
      .gallery {{ display:grid; gap:22px 18px; margin-top:20px;
                 grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); }}
      figure.corridor {{ margin:0; scroll-margin-top:24px; }}
      figure.corridor svg {{ display:block; width:100%; height:auto;
                            border-radius:8px; }}
      figure.corridor:target {{ outline:2px solid #e93e4e;
                               outline-offset:4px; border-radius:8px; }}
      figcaption {{ margin-top:8px; font-size:13px; line-height:1.45; }}
      figcaption b {{ display:block; font-size:15px; color:#eef2ec; }}
      figcaption span {{ color:#8d988f; }}
      footer {{ margin-top:46px; padding-top:18px; font-size:13px;
               color:#7d8a83; border-top:1px solid rgba(255,255,255,.08); }}
      footer a {{ color:#9fd4b0; }}
    </style>
  </head>
  <body>
    <main>
      <nav class="crumbs"><a href="/">Карта обстановки</a> → Маршруты</nav>
      <h1>Маршруты БПЛА: повторяющиеся коридоры</h1>
      <p>За {span_days} {plural(span_days, "день", "дня", "дней")} карта
      записала <strong>{total}</strong>
      {plural(total, "маршрут", "маршрута", "маршрутов")}, названных самими
      источниками, и восстановила <strong>{waves}</strong>
      {plural(waves, "волну", "волны", "волн")} по движению фиксаций:
      налёт идёт волнами, борт видят в одном районе, через двадцать минут
      в соседнем — и эта цепочка собирается обратно в путь
      ({wave_points} {plural(wave_points, "фиксация", "фиксации", "фиксаций")}
      легли в треки). Всё вместе сложено в повторяющиеся трассы.
      {night_share}% маршрутов — ночные.</p>

      <a class="map" href="/">Открыть живую карту</a>

      <div id="routes-map" data-version="{data_v}"></div>
      <p class="map-note">Толщина линии — сколько раз повторилась трасса;
      штрихи бегут по направлению полёта. При приближении проявляются
      локальные ветки и подписи малых мест. Наведите на линию — подсказка
      с числами; клик ведёт к карточке коридора ниже. Прибрежные дуги идут
      над морем: «Туапсе — Сочи» не значит «через города».</p>

      <h2>Устойчивые коридоры</h2>
      <p>Каждая карточка — один коридор между населёнными пунктами:
      бледные линии — все его маршруты, стрелка — самый частый вариант
      пути. Уходы за береговую черту сюда не попадают — их видно на
      карте выше.</p>
      <div class="gallery">{cards}</div>

      <h2>Как это читать</h2>
      <p>Трасса — цепочка плеч между местами, которые называют снова и
      снова; плечо живёт от {MIN_EDGE} повторов и рисуется в преобладающую
      сторону. Линии гладкие не для красоты: борт самолётной схемы на
      крейсерских 150 км/ч разворачивается радиусом около 400 метров — на
      масштабе карты угол физически невозможен, и ломаная была бы враньём
      о траектории.</p>
      <p>Данных два вида, и подсказка всегда говорит, чего сколько.
      Первый — маршруты, которые источник описал сам: «от Анапы через
      Раевскую на Новороссийск». Второй — волны, восстановленные из нашей
      базы: борт видят в одном районе, через 3–35 минут в соседнем, и если
      он мог туда долететь с правдоподобной скоростью (80–260 км/ч при
      крейсерских 150) без разворота круче 70°, фиксации связываются в
      трек. Это догадка — но догадка по физике полёта, а не по
      совпадению имён. Число повторов зависит и от активности каналов
      региона.</p>

      <footer>
        Обновлено {escape(updated)}. Неофициальная сводка: составлена по
        публичным сообщениям, может опаздывать и ошибаться. Не принимайте
        по ней решения о личной безопасности.
        <br /><a href="/">Живая карта</a> ·
        <a href="/city/">Сводки по городам</a> ·
        <a href="/rayon/">Сводки по районам</a> ·
        <a href="/svodka/">Сводки по дням</a>
      </footer>
    </main>
    <script defer src="/assets/marshruty-map.js{js_v}"></script>
  </body>
</html>
"""


def asset_version(path: Path) -> str:
    """Восемь знаков от содержимого файла — метка версии для ссылки.

    Имена бандла и данных фиксированы (страницу собирает сервер, и хешей
    манифеста ему взять негде), а Apache отдаёт их с недельным кэшем.
    Без метки браузер неделю показывает старую карту: правки уезжали на
    боевой, а владелец видел прежнюю версию.
    """
    try:
        return hashlib.md5(path.read_bytes()).hexdigest()[:8]
    except OSError:
        return ""


def sea_names(connection: sqlite3.Connection) -> set[str]:
    return {row["name_ru"] for row in connection.execute(
        "SELECT name_ru FROM zones WHERE source_id LIKE '%-sea'")}


def build(connection: sqlite3.Connection) -> int:
    """Собрать corridors.json и dist/marshruty/index.html."""
    routes = load_routes(connection)
    tracks = reconstruct_tracks(connection)
    land = Land()
    today = now_utc().astimezone(MSK)
    updated = f"{today.day} {MONTHS[today.month - 1]}, {today:%H:%M} МСК"

    corridors = build_corridors(
        routes, skip_names=sea_names(connection))[:MAX_CARDS]
    anchors = {(c["start"], c["end"]): f"kor-{i}"
               for i, c in enumerate(corridors)}
    nodes, edges = build_graph(routes, tracks)
    graph = export_graph(nodes, edges, land, anchors, {
        "routes": len(routes),
        "tracks": len(tracks),
    })
    data_dir = OUT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "corridors.json").write_text(
        json.dumps(graph, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")

    directory = OUT / "marshruty"
    directory.mkdir(parents=True, exist_ok=True)
    versions = {
        "js": asset_version(OUT / "assets" / "marshruty-map.js"),
        "css": asset_version(OUT / "assets" / "marshruty-map.css"),
        "data": asset_version(data_dir / "corridors.json"),
    }
    (directory / "index.html").write_text(
        build_page(routes, tracks, land, updated, sea_names(connection),
                   versions),
        encoding="utf-8")
    return len(corridors)


def main() -> int:
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        count = build(connection)
    print(f"маршруты: страница собрана, коридоров в галерее {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
