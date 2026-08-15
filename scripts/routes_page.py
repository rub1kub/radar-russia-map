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
MAX_EDGES = 700
# Трассы — верх списка по важности (повторы × длина): видны на обзоре,
# остальное движок показывает при приближении.
TRUNK_CHAINS = 60
# Продолжение трассы: следующее плечо не разворачивается больше чем на
# ~75° и весит хотя бы треть текущего — иначе это уже другая трасса.
CHAIN_MAX_TURN = math.pi * 0.42
CHAIN_MIN_RATIO = 0.3
# Подписи путевых точек: первый ярус виден всегда, второй — при приближении.
LABELS_ALWAYS = 26
LABELS_ZOOMED = 120

MIN_TRANSITION = 5
# Окно склейки двух фиксаций в переход: ближе трёх минут — это одно и то же
# сообщение из двух лент, дальше пятидесяти — уже другой борт.
TRANSITION_MINUTES = (3, 50)
TRANSITION_KM = (8, 130)

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


def load_transitions(connection: sqlite3.Connection) -> list[dict]:
    """Переходы, восстановленные из собственной базы фиксаций.

    Каждому точечному тяжёлому событию ищется один ближайший предшественник
    в окне времени и расстояния — «фиксация была там, потом её увидели
    здесь». Одна связка — догадка; в счёт идут только связки, повторившиеся
    MIN_TRANSITION раз за всю историю.
    """
    # Акватории проходят наравне с точечными зонами: «БПЛА над Азовским
    # морем» — такое же наблюдение, только над водой, и без него у
    # приморских трасс обрывался морской конец.
    rows = connection.execute(
        """SELECT e.zone_id, e.lat, e.lon, e.first_seen_at, z.name_ru
           FROM events e JOIN zones z ON z.id = e.zone_id
           WHERE e.severity >= 8 AND e.lat IS NOT NULL
             AND (z.level != 'region' OR z.source_id LIKE '%-sea')
           ORDER BY e.first_seen_at""").fetchall()
    events = [(datetime.fromisoformat(r["first_seen_at"]).timestamp(),
               r["lat"], r["lon"], r["zone_id"], r["name_ru"]) for r in rows]
    times = [e[0] for e in events]
    low_s, high_s = TRANSITION_MINUTES[0] * 60, TRANSITION_MINUTES[1] * 60

    counts: Counter = Counter()
    coords: dict = {}
    for stamp, lat, lon, zone, name in events:
        lo = bisect.bisect_left(times, stamp - high_s)
        hi = bisect.bisect_left(times, stamp - low_s)
        best = None
        for a in events[lo:hi]:
            if a[3] == zone:
                continue
            distance = _km((a[1], a[2]), (lat, lon))
            if not (TRANSITION_KM[0] <= distance <= TRANSITION_KM[1]):
                continue
            score = distance + (stamp - a[0]) / 60 * 0.5
            if best is None or score < best[0]:
                best = (score, a)
        if best is None:
            continue
        a = best[1]
        start, end = short_name(a[4]), short_name(name)
        if start == end:
            continue
        key = (a[3], zone)
        counts[key] += 1
        coords.setdefault(key, ((a[1], a[2]), (lat, lon), start, end))
    return [{
        "a": coords[key][0], "b": coords[key][1],
        "start": coords[key][2], "end": coords[key][3],
        "count": count,
    } for key, count in counts.items() if count >= MIN_TRANSITION]


def build_corridors(routes: list[dict],
                    minimum: int = MIN_CORRIDOR) -> list[dict]:
    """Коридор — все маршруты с одинаковыми началом и концом."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for route in routes:
        key = (route["points"][0][2], route["points"][-1][2])
        grouped[key].append(route)

    corridors = []
    for (start, end), items in grouped.items():
        if len(items) < minimum or start == end:
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


def build_graph(routes: list[dict], transitions: list[dict]) -> tuple[
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
    for transition in transitions:
        a = visit(*transition["a"], transition["start"])
        b = visit(*transition["b"], transition["end"])
        if a != b:
            legs[(a, b)] += transition["count"]

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


def _bezier(a: tuple[float, float], control: tuple[float, float],
            b: tuple[float, float], steps: int = 10) -> list[list[float]]:
    """Квадратичная дуга, рассчитанная в точки: клиенту остаётся линия."""
    points = []
    for index in range(steps + 1):
        t = index / steps
        lat = ((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * control[0]
               + t ** 2 * b[0])
        lon = ((1 - t) ** 2 * a[1] + 2 * (1 - t) * t * control[1]
               + t ** 2 * b[1])
        points.append([round(lat, 4), round(lon, 4)])
    return points


def _bearing(a: dict, b: dict) -> float:
    return math.atan2((b["lon"] - a["lon"])
                      * math.cos(math.radians((a["lat"] + b["lat"]) / 2)),
                      b["lat"] - a["lat"])


def _turn(a: float, b: float) -> float:
    diff = abs(a - b) % (2 * math.pi)
    return min(diff, 2 * math.pi - diff)


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
        return _bearing(nodes[edge["a"]], nodes[edge["b"]])

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
        points: list[list[float]] = []
        for index, edge in enumerate(chain):
            a, b = nodes[edge["a"]], nodes[edge["b"]]
            start = (a["lat"], a["lon"])
            end = (b["lat"], b["lon"])
            control = land.sea_control(start, end)
            segment = (_bezier(start, control, end) if control
                       else [[round(start[0], 4), round(start[1], 4)],
                             [round(end[0], 4), round(end[1], 4)]])
            points.extend(segment if index == 0 else segment[1:])
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
            "s": round(math.log1p(top) / math.log1p(peak), 3),
            "t": 1 if rank < TRUNK_CHAINS else 0,
            **({"kor": anchors[(start_name, end_name)]}
               if (start_name, end_name) in anchors else {}),
        })

    # Подписи путевых точек: без кружков, только имена — крупные всегда,
    # остальные при приближении.
    ranked = label_weight.most_common(LABELS_ZOOMED)
    labels = []
    for position, (index, weight) in enumerate(ranked):
        node = nodes[index]
        labels.append({
            "lat": round(node["lat"], 4), "lon": round(node["lon"], 4),
            "name": node["name"],
            "t": 1 if position < LABELS_ALWAYS else 2,
        })

    return {"generated": now_utc().isoformat(), "stats": stats,
            "chains": out_chains, "labels": labels}


def flow_path(points_xy: list[tuple[float, float]],
              control_xy: tuple[float, float] | None = None) -> str:
    """Гладкий путь для мини-карт галереи."""
    (x0, y0) = points_xy[0]
    if control_xy is not None and len(points_xy) == 2:
        (cx, cy), (x1, y1) = control_xy, points_xy[1]
        return f"M{x0} {y0}Q{cx} {cy} {x1} {y1}"
    if len(points_xy) == 2:
        (x1, y1) = points_xy[1]
        return f"M{x0} {y0}L{x1} {y1}"
    parts = [f"M{x0} {y0}"]
    for index in range(1, len(points_xy) - 1):
        (ax, ay), (bx, by) = points_xy[index], points_xy[index + 1]
        mx, my = round((ax + bx) / 2, 1), round((ay + by) / 2, 1)
        parts.append(f"Q{ax} {ay} {mx} {my}")
    (x1, y1) = points_xy[-1]
    parts.append(f"L{x1} {y1}")
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


def build_page(routes: list[dict], transitions: list[dict],
               land: Land, updated: str) -> str:
    corridors = build_corridors(routes)[:MAX_CARDS]

    total = len(routes)
    night = sum(
        1 for r in routes
        if datetime.fromisoformat(r["posted_at"]).astimezone(MSK).hour >= 21
        or datetime.fromisoformat(r["posted_at"]).astimezone(MSK).hour < 6)
    night_share = round(night * 100 / total) if total else 0
    first = min((r["posted_at"] for r in routes), default="")
    span_days = ((now_utc() - datetime.fromisoformat(first)).days
                 if first else 0)
    links = sum(t["count"] for t in transitions)

    title = "Маршруты БПЛА — повторяющиеся коридоры на карте"
    description = (
        f"{total} маршрутов БПЛА за {span_days} дней из открытых сообщений "
        f"плюс переходы, восстановленные по последовательности фиксаций: "
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
    <link rel="stylesheet" href="/assets/marshruty-map.css" />
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
      источниками, и восстановила {links}
      {plural(links, "переход", "перехода", "переходов")} по
      последовательности фиксаций. Всё это слито в граф: узлы — места,
      которые источники называют снова и снова, рёбра — повторяющиеся
      плечи между ними. {night_share}% маршрутов — ночные.</p>

      <a class="map" href="/">Открыть живую карту</a>

      <div id="routes-map"></div>
      <p class="map-note">Толщина линии — сколько раз повторился коридор;
      штрихи бегут по направлению полёта. При приближении проявляются
      локальные ветки и подписи малых узлов. Наведите на линию или узел —
      подсказка с числами; клик по линии ведёт к карточке коридора ниже.
      Прибрежные дуги идут над морем: «Туапсе — Сочи» не значит «через
      города».</p>

      <h2>Устойчивые коридоры</h2>
      <p>Каждая карточка — один коридор: бледные линии — все его маршруты,
      стрелка — самый частый вариант пути.</p>
      <div class="gallery">{cards}</div>

      <h2>Как это читать</h2>
      <p>Узлы графа — кластеры мест (~10 км), которые источники называют
      постоянно; рёбра — плечи между ними, повторившиеся от {MIN_EDGE}
      раз. Ребро рисуется в преобладающую сторону; если летали в обе,
      подсказка показывает счёт туда и обратно. Основа — маршруты, которые
      источник описал сам; к ним добавлены переходы, восстановленные из
      базы: две точечные фиксации подряд, вторая в пределах 50 минут и
      130 км. Доля восстановленного видна в подсказке каждого ребра.
      Число повторов зависит и от активности каналов региона.</p>

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
    <script defer src="/assets/marshruty-map.js"></script>
  </body>
</html>
"""


def build(connection: sqlite3.Connection) -> int:
    """Собрать corridors.json и dist/marshruty/index.html."""
    routes = load_routes(connection)
    transitions = load_transitions(connection)
    land = Land()
    today = now_utc().astimezone(MSK)
    updated = f"{today.day} {MONTHS[today.month - 1]}, {today:%H:%M} МСК"

    corridors = build_corridors(routes)[:MAX_CARDS]
    anchors = {(c["start"], c["end"]): f"kor-{i}"
               for i, c in enumerate(corridors)}
    nodes, edges = build_graph(routes, transitions)
    graph = export_graph(nodes, edges, land, anchors, {
        "routes": len(routes),
        "transitions": sum(t["count"] for t in transitions),
    })
    data_dir = OUT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "corridors.json").write_text(
        json.dumps(graph, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")

    directory = OUT / "marshruty"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.html").write_text(
        build_page(routes, transitions, land, updated), encoding="utf-8")
    return len(corridors)


def main() -> int:
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        count = build(connection)
    print(f"маршруты: страница собрана, коридоров в галерее {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
