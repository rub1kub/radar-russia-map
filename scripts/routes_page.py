"""Страница «Маршруты БПЛА»: граф устойчивых коридоров из исторических данных.

    PYTHONPATH=.:ingest ingest/.venv/bin/python -m scripts.routes_page

Карта — не пучок отдельных линий, а граф: все точки маршрутов
кластеризуются в узлы (~10 км), одинаковые плечи складываются в рёбра с
весом. Вместо пятнадцати полупараллельных линий между Джанкоем и
Армянском — одно ребро, у которого видно толщину, направление
(бегущие штрихи) и подпись узлов.

Два источника данных, честно разделённые в подсказке каждого ребра:

  • именованные маршруты — источник сам описал путь («от Анапы через
    Раевскую на Новороссийск»), таблица routes;
  • восстановленные переходы — две точечные фиксации подряд в собственной
    базе событий (вторая в пределах 50 минут и 130 км от первой); каждая
    связка — догадка, в счёт идут только повторившиеся.

Прибрежные рёбра (Туапсе → Сочи) выгибаются над морем: сторона дуги
выбирается проверкой «какая сторона — не суша» по полигонам субъектов.

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
# Ребро графа живёт от трёх повторов, локальных рёбер не больше трёхсот:
# ниже порога начинается сыпь единичных догадок.
MIN_EDGE = 3
MAX_LOCAL_EDGES = 300
# Магистрали — самые тяжёлые рёбра: видны всегда, остальное проявляется
# при приближении.
TRUNK_EDGES = 120
# Подписи узлов: первый ярус виден всегда, второй — при приближении.
LABELS_ALWAYS = 24
LABELS_ZOOMED = 110
# Точки узлов на обзоре — только крупнейшие: сотни сёл-узлов на общем
# плане превращались в сыпь, за которой не видно рёбер.
DOTS_ALWAYS = 80
# Порог зума, с которого проявляются локальные ветки и вторые подписи.
ZOOM_DETAIL = 2.2

MIN_TRANSITION = 5
# Окно склейки двух фиксаций в переход: ближе трёх минут — это одно и то же
# сообщение из двух лент, дальше пятидесяти — уже другой борт.
TRANSITION_MINUTES = (3, 50)
TRANSITION_KM = (8, 130)

# Кластеризация точек в узлы: шаг сетки ~10 км.
NODE_LAT_STEP = 0.09

# Театр событий: западная часть, где живут почти все маршруты.
HERO_BBOX = (42.8, 27.5, 59.5, 55.0)  # lat0, lon0, lat1, lon1
HERO_W, HERO_H = 1120, 840

# Крупные города для ориентировки: точка и подпись, без событийного смысла.
CITY_MIN_POPULATION = 350_000


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
    """Равнопромежуточная проекция в пиксели SVG."""

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
    """Полигоны субъектов: подложка карты и ответ на вопрос «это суша?»."""

    def __init__(self) -> None:
        self.rings: list[tuple[tuple[float, float, float, float], list]] = []
        self.regions: list[tuple[str, float, float]] = []
        self.cities: list[tuple[str, float, float]] = []
        try:
            collection = json.loads(
                (DATA / "regions.json").read_text(encoding="utf-8"))
        except OSError:
            return
        for feature in collection.get("features", []):
            geometry = feature.get("geometry") or {}
            name = (feature.get("properties") or {}).get("name") or ""
            polygons = (geometry.get("coordinates", [])
                        if geometry.get("type") == "MultiPolygon"
                        else [geometry.get("coordinates", [])])
            largest: list | None = None
            for polygon in polygons:
                for ring in polygon:
                    if len(ring) < 4:
                        continue
                    lons = [p[0] for p in ring]
                    lats = [p[1] for p in ring]
                    self.rings.append(
                        ((min(lats), min(lons), max(lats), max(lons)), ring))
                    if largest is None or len(ring) > len(largest):
                        largest = ring
            if name and largest:
                self.regions.append((
                    name,
                    sum(p[1] for p in largest) / len(largest),
                    sum(p[0] for p in largest) / len(largest),
                ))

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
        """Контуры суши в кадре проекции, прорежённые до читаемости.

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
        """Точка над водой сбоку от середины плеча — управляющая для дуги.

        Кандидаты — перпендикуляры в обе стороны; берётся тот, что не на
        суше. Если оба на суше (глубинный коридор) — дуга не нужна.
        """
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
    MIN_TRANSITION раз за всю историю. Пары «город — его же округ»
    отбрасываются: это одна территория на двух уровнях зон.
    """
    rows = connection.execute(
        """SELECT e.zone_id, e.lat, e.lon, e.first_seen_at, z.name_ru
           FROM events e JOIN zones z ON z.id = e.zone_id
           WHERE e.severity >= 8 AND z.level != 'region'
             AND e.lat IS NOT NULL
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

    legs: Counter = Counter()          # (cell_a, cell_b) -> повторы
    named_legs: Counter = Counter()    # из именованных маршрутов
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

    # Пара направлений сливается в одно ребро с преобладающей стороной.
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
        # Рисуется в преобладающую сторону.
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
    # Магистрали + локальные ветки, без хвоста из полусотни одиночек.
    return nodes, result_edges[:TRUNK_EDGES + MAX_LOCAL_EDGES]


def flow_path(points_xy: list[tuple[float, float]],
              control_xy: tuple[float, float] | None = None) -> str:
    """Гладкий путь: дуга по управляющей точке или сглаженная ломаная."""
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


def edge_title(edge: dict, nodes: list[dict], anchors: dict) -> str:
    """Подсказка ребра: кто, сколько, в какую сторону, откуда данные."""
    a, b = nodes[edge["a"]], nodes[edge["b"]]
    parts = [f'{a["name"]} → {b["name"]}',
             f'{edge["count"]} {plural(edge["count"], "повтор", "повтора", "повторов")}']
    if edge["backward"]:
        parts.append(f'туда {edge["forward"]}, обратно {edge["backward"]}')
    if edge["computed"]:
        parts.append(f'восстановлено по фиксациям: {edge["computed"]}')
    return " · ".join(parts)


def hero_svg(nodes: list[dict], edges: list[dict], land: Land,
             anchors: dict[tuple[str, str], str]) -> str:
    """Граф коридоров: магистрали всегда, ветки и подписи — при зуме."""
    projection = Projection(HERO_BBOX, HERO_W, HERO_H, pad=10, precision=0)

    outline = land.svg_path(projection)
    base = (f'<path d="{outline}" fill="#182019" stroke="#3a463f" '
            f'stroke-width="1" vector-effect="non-scaling-stroke" />'
            if outline else "")

    region_labels = []
    for name, lat, lon in land.regions:
        if not projection.inside(lat, lon):
            continue
        x, y = projection.xy(lat, lon)
        region_labels.append(
            f'<text x="{x}" y="{y}" text-anchor="middle">'
            f'{escape(short_name(name))}</text>')

    cities = []
    for name, lat, lon in land.cities:
        if not projection.inside(lat, lon):
            continue
        x, y = projection.xy(lat, lon)
        cities.append(
            f'<circle cx="{x}" cy="{y}" r="2.4" data-r="2.4" '
            f'fill="#5b6a62" />'
            f'<text x="{x + 6}" y="{y + 4}">{escape(name)}</text>')

    visible = [e for e in edges
               if projection.inside(nodes[e["a"]]["lat"], nodes[e["a"]]["lon"])
               and projection.inside(nodes[e["b"]]["lat"], nodes[e["b"]]["lon"])]
    peak = max((e["count"] for e in visible), default=1)

    # Магистраль — не просто частое ребро, а частое И длинное: по чистому
    # счёту в топ пролезали десятикилометровые прыжки между сёлами одного
    # горячего района, а Анапа — Сочи с её 250 км уходила в «локальные».
    def importance(edge: dict) -> float:
        a, b = nodes[edge["a"]], nodes[edge["b"]]
        span = _km((a["lat"], a["lon"]), (b["lat"], b["lon"]))
        return edge["count"] * max(span, 15.0)

    visible.sort(key=importance, reverse=True)

    def render_edge(edge: dict, trunk: bool) -> str:
        a, b = nodes[edge["a"]], nodes[edge["b"]]
        start, end = (a["lat"], a["lon"]), (b["lat"], b["lon"])
        control = land.sea_control(start, end)
        xy = [projection.xy(*start), projection.xy(*end)]
        control_xy = projection.xy(*control) if control else None
        path = flow_path(xy, control_xy)
        share = math.log1p(edge["count"]) / math.log1p(peak)
        width = round((2.4 + 4.6 * share) if trunk else (1.0 + 1.5 * share), 1)
        opacity = round(0.7 + 0.3 * share, 2) if trunk else 0.45
        speed = "f" if share > 0.66 else ("m" if share > 0.33 else "s")
        title = f"<title>{escape(edge_title(edge, nodes, anchors))}</title>"
        body = (
            # Тело ребра: постоянная трасса.
            f'<path d="{path}" fill="none" stroke="#d63c4a" '
            f'stroke-width="{width}" stroke-opacity="{opacity}" '
            f'stroke-linecap="round" vector-effect="non-scaling-stroke" />'
            # Бегущие штрихи: направление видно без наконечников.
            f'<path class="ant ant-{speed}" d="{path}" fill="none" '
            f'stroke="#ffc9ce" stroke-width="{round(width * 0.55, 1)}" '
            f'vector-effect="non-scaling-stroke" />'
            # Невидимая широкая накладка — чтобы подсказка ловилась мышью.
            f'<path d="{path}" fill="none" stroke="#fff" stroke-opacity="0" '
            f'stroke-width="9" vector-effect="non-scaling-stroke">{title}</path>'
        )
        anchor = anchors.get((a["name"], b["name"]))
        if anchor:
            body = f'<a href="#{anchor}">{body}</a>'
        return f'<g class="{"trunk" if trunk else "local"}">{body}</g>'

    trunk_svg = [render_edge(e, True) for e in visible[:TRUNK_EDGES]]
    local_svg = [render_edge(e, False) for e in visible[TRUNK_EDGES:]]

    # Узлы: размер по трафику, подписи двумя ярусами. Подписи первого
    # яруса расталкиваются: юг настолько плотный, что двадцать имён без
    # проверки пересечений ложились друг на друга нечитаемой стопкой.
    ranked = sorted(range(len(nodes)), key=lambda i: -nodes[i]["weight"])
    placed_labels: list[tuple[float, float, float]] = []

    def label_fits(x: float, y: float, name: str) -> bool:
        half = len(name) * 3.4
        for px0, px1, py in placed_labels:
            if abs(y - py) < 14 and x - half < px1 and x + half > px0:
                return False
        placed_labels.append((x - half, x + half, y))
        return True

    node_svg = []
    for rank, index in enumerate(ranked):
        node = nodes[index]
        if not node["weight"]:
            continue
        if not projection.inside(node["lat"], node["lon"]):
            continue
        x, y = projection.xy(node["lat"], node["lon"])
        radius = round(2.0 + 2.4 * math.log1p(node["weight"]) /
                       math.log1p(nodes[ranked[0]]["weight"] or 1), 1)
        tier = ("n1" if rank < LABELS_ALWAYS
                else "n2" if rank < LABELS_ZOOMED
                else "nd" if rank < DOTS_ALWAYS else "n3")
        # Не поместившаяся подпись первого яруса уходит во второй:
        # точка остаётся, имя появится при приближении.
        if tier == "n1" and not label_fits(x, y - radius - 4, node["name"]):
            tier = "n2"
        label = (f'<text x="{x}" y="{y - radius - 4}" text-anchor="middle">'
                 f'{escape(node["name"])}</text>'
                 if tier in ("n1", "n2") else "")
        node_svg.append(
            f'<g class="{tier}"><circle cx="{x}" cy="{y}" r="{radius}" '
            f'data-r="{radius}" fill="#ffb3ba" fill-opacity="0.9" '
            f'stroke="#0a0e0d" stroke-width="1" '
            f'vector-effect="non-scaling-stroke">'
            f'<title>{escape(node["name"])} · узел, '
            f'{node["weight"]} {plural(node["weight"], "повтор", "повтора", "повторов")}'
            f'</title></circle>{label}</g>')

    return (f'<svg viewBox="0 0 {HERO_W} {HERO_H}" role="img" '
            f'aria-label="Граф повторяющихся маршрутов БПЛА" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<rect x="-2000" y="-2000" width="5000" height="5000" fill="#0a0e0d" />'
            + base
            + f'<g class="map-labels" fill="#46524b" font-size="12">'
              f'{"".join(region_labels)}</g>'
            + f'<g class="map-cities" fill="#77857c" font-size="12">'
              f'{"".join(cities)}</g>'
            + f'<g class="locals">{"".join(local_svg)}</g>'
            + f'<g class="trunks">{"".join(trunk_svg)}</g>'
            + f'<g class="nodes" fill="#dfe6df" font-size="11">'
              f'{"".join(node_svg)}</g>'
            + "</svg>")


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


PANZOOM_JS = """
(function () {
  var box = document.querySelector(".hero");
  var svg = box.querySelector("svg");
  var W = %(w)d, H = %(h)d, DETAIL = %(detail)s;
  var vb = [0, 0, W, H];
  var labelGroups = svg.querySelectorAll(".map-labels, .map-cities, .nodes");
  var dots = svg.querySelectorAll("circle[data-r]");
  function apply() {
    svg.setAttribute("viewBox", vb.join(" "));
    // Подписи и точки не раздуваются вместе с картой: их размер в юнитах
    // SVG уменьшается пропорционально приближению.
    var scale = vb[2] / W;
    labelGroups.forEach(function (g) {
      g.setAttribute("font-size",
        ((g.classList.contains("nodes") ? 11 : 12) * scale).toFixed(2));
    });
    dots.forEach(function (dot) {
      dot.setAttribute("r", (dot.dataset.r * scale).toFixed(2));
    });
    // Детализация по зуму: локальные ветки и вторые подписи узлов.
    svg.classList.toggle("zoomed", W / vb[2] >= DETAIL);
  }
  function clamp() {
    vb[2] = Math.min(W, Math.max(W / 14, vb[2]));
    vb[3] = vb[2] * H / W;
    vb[0] = Math.min(W - vb[2], Math.max(0, vb[0]));
    vb[1] = Math.min(H - vb[3], Math.max(0, vb[1]));
  }
  function zoom(factor, cx, cy) {
    var mx = vb[0] + cx * vb[2], my = vb[1] + cy * vb[3];
    vb[2] *= factor; vb[3] *= factor;
    vb[0] = mx - cx * vb[2]; vb[1] = my - cy * vb[3];
    clamp(); apply();
  }
  box.addEventListener("wheel", function (e) {
    e.preventDefault();
    var r = svg.getBoundingClientRect();
    zoom(e.deltaY < 0 ? 0.82 : 1 / 0.82,
         (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
  }, { passive: false });
  var drag = null, moved = false;
  box.addEventListener("pointerdown", function (e) {
    drag = [e.clientX, e.clientY]; moved = false;
    box.setPointerCapture(e.pointerId);
  });
  box.addEventListener("pointermove", function (e) {
    if (!drag) return;
    var r = svg.getBoundingClientRect();
    if (Math.abs(e.clientX - drag[0]) + Math.abs(e.clientY - drag[1]) > 2)
      moved = true;
    vb[0] -= (e.clientX - drag[0]) / r.width * vb[2];
    vb[1] -= (e.clientY - drag[1]) / r.height * vb[3];
    drag = [e.clientX, e.clientY]; clamp(); apply();
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach(function (n) {
    box.addEventListener(n, function () { drag = null; });
  });
  // Случайный клик после перетаскивания не должен прыгать по якорю.
  box.addEventListener("click", function (e) {
    if (moved) { e.preventDefault(); e.stopPropagation(); }
  }, true);
  document.querySelectorAll(".hero-zoom button").forEach(function (b) {
    b.addEventListener("click", function () {
      if (b.dataset.z === "0") { vb = [0, 0, W, H]; apply(); return; }
      zoom(b.dataset.z === "+" ? 0.72 : 1 / 0.72, 0.5, 0.5);
    });
  });
  apply();
})();
"""


def build_page(routes: list[dict], transitions: list[dict],
               land: Land, updated: str) -> str:
    corridors = build_corridors(routes)[:MAX_CARDS]
    anchors = {(c["start"], c["end"]): f"kor-{i}"
               for i, c in enumerate(corridors)}
    nodes, edges = build_graph(routes, transitions)

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
        f"граф коридоров с приближением и галерея самых устойчивых.")
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
    hero = hero_svg(nodes, edges, land, anchors)
    trunk_count = min(TRUNK_EDGES, len(edges))

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
      .hero-wrap {{ position:relative; margin:26px 0 8px; }}
      .hero {{ border-radius:12px; overflow:hidden; cursor:grab;
              border:1px solid rgba(255,255,255,.07); touch-action:none;
              user-select:none; -webkit-user-select:none; }}
      .hero:active {{ cursor:grabbing; }}
      .hero svg {{ display:block; width:100%; height:auto; }}
      .hero text {{ pointer-events:none; }}
      /* Локальные ветки, мелкие узлы и вторые подписи — при приближении. */
      .hero .locals, .hero .n2 text, .hero .n3 {{ display:none; }}
      .hero svg.zoomed .locals, .hero svg.zoomed .n2 text,
      .hero svg.zoomed .n3 {{ display:initial; }}
      /* Бегущие штрихи: направление потока без единого наконечника. */
      .ant {{ stroke-dasharray:3 14; animation:ants 1.4s linear infinite; }}
      .ant-m {{ animation-duration:2.1s; }}
      .ant-s {{ animation-duration:3.2s; }}
      @keyframes ants {{ to {{ stroke-dashoffset:-17; }} }}
      @media (prefers-reduced-motion: reduce) {{
        .ant {{ animation:none; stroke-dasharray:none; opacity:0; }} }}
      .hero a {{ cursor:pointer; }}
      .hero-zoom {{ position:absolute; right:12px; bottom:12px;
                   display:flex; flex-direction:column; gap:6px; }}
      .hero-zoom button {{ width:36px; height:36px; border-radius:9px;
                          border:1px solid rgba(255,255,255,.14);
                          background:#141a17; color:#dfe6df; font-size:17px;
                          cursor:pointer; }}
      .hero-legend {{ position:absolute; right:12px; top:12px;
                     background:rgba(12,16,15,.88); padding:10px 14px;
                     border:1px solid #28322c; border-radius:9px;
                     display:flex; flex-direction:column; gap:6px;
                     font-size:13px; color:#aab4ad; pointer-events:none; }}
      .hero-legend i {{ display:inline-block; width:34px; height:0;
                       vertical-align:middle; margin-right:8px;
                       border-top:4px solid #d63c4a; border-radius:2px; }}
      .hero-legend i.thin {{ border-top-width:1.5px; }}
      .hero-legend i.dot {{ width:9px; height:9px; border:0; margin-left:12px;
                           margin-right:20px; border-radius:50%%;
                           background:#ffb3ba; }}
      @media (max-width:560px) {{ .hero-legend {{ font-size:11px;
        padding:8px 10px; }} .hero-legend i {{ width:22px; }} }}
      .gallery {{ display:grid; gap:22px 18px; margin-top:20px;
                 grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); }}
      figure.corridor {{ margin:0; scroll-margin-top:24px; }}
      figure.corridor svg {{ display:block; width:100%%; height:auto;
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
      плечи между ними. Бегущие штрихи показывают направление.
      {night_share}% маршрутов — ночные.</p>

      <a class="map" href="/">Открыть живую карту</a>

      <div class="hero-wrap">
        <div class="hero">{hero}</div>
        <div class="hero-legend">
          <span><i></i> магистраль ({trunk_count} самых частых)</span>
          <span><i class="thin"></i> локальная ветка — видна при приближении</span>
          <span><i class="dot"></i> узел: чем крупнее, тем чаще называют</span>
        </div>
        <div class="hero-zoom">
          <button type="button" data-z="+" aria-label="Приблизить">+</button>
          <button type="button" data-z="-" aria-label="Отдалить">−</button>
          <button type="button" data-z="0" aria-label="Сброс">⌂</button>
        </div>
      </div>
      <p class="hero-note">Колесо и кнопки приближают, карта тянется мышью
      и пальцем. При приближении проявляются локальные ветки и подписи
      малых узлов. Наведите на ребро или узел — подсказка с числами; клик
      по ребру ведёт к его карточке ниже. Прибрежные дуги идут над морем:
      «Туапсе — Сочи» не значит «через города».</p>

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
    <script>{PANZOOM_JS % {"w": HERO_W, "h": HERO_H, "detail": ZOOM_DETAIL}}</script>
  </body>
</html>
"""


def load_cities(connection: sqlite3.Connection) -> list[tuple[str, float, float]]:
    best: dict[str, tuple[str, float, float, int]] = {}
    for row in connection.execute(
            """SELECT name_ru, lat, lon, population FROM zones
               WHERE level = 'place' AND population >= ?
                 AND lat IS NOT NULL""", (CITY_MIN_POPULATION,)):
        current = best.get(row["name_ru"])
        if current is None or row["population"] > current[3]:
            best[row["name_ru"]] = (row["name_ru"], row["lat"], row["lon"],
                                    row["population"])
    return [(name, lat, lon) for name, lat, lon, _ in best.values()]


def build(connection: sqlite3.Connection) -> int:
    """Собрать dist/marshruty/index.html; вернуть число коридоров галереи."""
    routes = load_routes(connection)
    transitions = load_transitions(connection)
    land = Land()
    land.cities = load_cities(connection)
    today = now_utc().astimezone(MSK)
    updated = f"{today.day} {MONTHS[today.month - 1]}, {today:%H:%M} МСК"
    directory = OUT / "marshruty"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.html").write_text(
        build_page(routes, transitions, land, updated), encoding="utf-8")
    return len(build_corridors(routes)[:MAX_CARDS])


def main() -> int:
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        count = build(connection)
    print(f"маршруты: страница собрана, коридоров в галерее {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
