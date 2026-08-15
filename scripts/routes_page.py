"""Страница «Маршруты БПЛА»: устойчивые коридоры из исторических маршрутов.

    PYTHONPATH=.:ingest ingest/.venv/bin/python -m scripts.routes_page

За три месяца в таблице routes накопились тысячи маршрутов, названных
самими сообщениями («от Анапы через Раевскую на Новороссийск»), и
большинство из них повторяется: коридор Каланчак → Армянск живёт
месяцами. Страница показывает это честно — общую карту всех плеч и
галерею устойчивых коридоров с их числами.

Ничего не вычисляется и не досочиняется: каждый маршрут — пересказ
одного сообщения, прошедший фильтры pipeline/routes.py (длина плеча,
извилистость). Агрегация лишь считает, как часто источники называют
один и тот же путь.

Вызывается из scripts.seo_pages при каждой пересборке посадочных, то
есть ежечасно по таймеру и при выкатке.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
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
# десять — закономерность. Карточек не больше шестидесяти: страница остаётся
# галереей, а не свалкой.
MIN_CORRIDOR = 10
MAX_CARDS = 60
# На общую карту не идут плечи, встреченные один раз: их сотни, и они
# превращают картину в шум. Повторившееся плечо — уже дорога.
MIN_SEGMENT = 2

# Театр событий: западная часть, где живут почти все маршруты. Единичные
# дальние (Урал) в галерее остаются, на общей карте — за кадром.
HERO_BBOX = (42.8, 27.5, 59.5, 55.0)  # lat0, lon0, lat1, lon1
HERO_W, HERO_H = 960, 720


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


class Projection:
    """Равнопромежуточная проекция в пиксели SVG.

    Косинус середины широты выравнивает масштаб по осям: без него юг
    растянут, и Крым выглядит вдвое шире Брянска.
    """

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
        # На большой карте пиксельной точности хватает: ползнака после
        # запятой на полутора тысячах линий — лишние сто килобайт.
        if self.precision == 0:
            return int(round(x)), int(round(y))
        return round(x, self.precision), round(y, self.precision)

    def inside(self, lat: float, lon: float) -> bool:
        return self.lat0 <= lat <= self.lat1 and self.lon0 <= lon <= self.lon1


def load_routes(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT points, posted_at, threat_type FROM routes").fetchall()
    routes = []
    for row in rows:
        points = json.loads(row["points"])
        if len(points) < 2:
            continue
        routes.append({
            "points": [(p[0], p[1], p[2]) for p in points],
            "posted_at": row["posted_at"],
            "threat": row["threat_type"] or "unknown",
        })
    return routes


def build_corridors(routes: list[dict]) -> list[dict]:
    """Коридор — все маршруты с одинаковыми началом и концом.

    Имена концов уже нормализованы геокодером, поэтому группировка по ним
    честнее координатной: «Анапа» из разных сообщений — одна точка.
    """
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for route in routes:
        key = (route["points"][0][2], route["points"][-1][2])
        grouped[key].append(route)

    corridors = []
    for (start, end), items in grouped.items():
        if len(items) < MIN_CORRIDOR or start == end:
            continue
        stamps = sorted(item["posted_at"] for item in items)
        hours = Counter(
            datetime.fromisoformat(item["posted_at"]).astimezone(MSK).hour
            for item in items)
        night = sum(v for h, v in hours.items() if h >= 21 or h < 6)
        threats = Counter(item["threat"] for item in items)
        months = sorted({stamp[:7] for stamp in stamps})
        # Самая частая цепочка точек — лицо коридора на карточке.
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
    return corridors[:MAX_CARDS]


def build_segments(routes: list[dict]) -> list[tuple[float, float, float, float, int]]:
    """Плечи всех маршрутов, слитые по округлённым концам.

    Округление до 0.05° (~4 км) склеивает один и тот же перелёт из разных
    сообщений; счёт повторов управляет толщиной линии.
    """
    counts: Counter = Counter()
    coords: dict = {}
    for route in routes:
        for a, b in zip(route["points"], route["points"][1:]):
            key = (round(a[0] / 0.05), round(a[1] / 0.05),
                   round(b[0] / 0.05), round(b[1] / 0.05))
            counts[key] += 1
            coords.setdefault(key, (a[0], a[1], b[0], b[1]))
    return [(*coords[key], count) for key, count in counts.items()
            if count >= MIN_SEGMENT]


def region_paths(projection: Projection) -> str:
    """Подложка: контуры субъектов в кадре, прорежённые до читаемости."""
    try:
        collection = json.loads(
            (DATA / "regions.json").read_text(encoding="utf-8"))
    except OSError:
        return ""
    paths = []
    for feature in collection.get("features", []):
        geometry = feature.get("geometry") or {}
        polygons = (geometry.get("coordinates", [])
                    if geometry.get("type") == "MultiPolygon"
                    else [geometry.get("coordinates", [])])
        for polygon in polygons:
            for ring in polygon:
                if not ring:
                    continue
                # Кольцо целиком за кадром не рисуется.
                if not any(projection.inside(lat, lon) for lon, lat in ring):
                    continue
                previous = None
                parts = []
                for lon, lat in ring:
                    x, y = projection.xy(lat, lon)
                    # Прореживание: точки ближе трёх пикселей не двигают
                    # контур, а весят как все остальные.
                    if previous and abs(x - previous[0]) < 3 and abs(y - previous[1]) < 3:
                        continue
                    parts.append(f"{'M' if previous is None else 'L'}{x} {y}")
                    previous = (x, y)
                if len(parts) > 2:
                    paths.append("".join(parts) + "Z")
    return (f'<path d="{" ".join(paths)}" fill="#131917" stroke="#2a332e" '
            f'stroke-width="0.6" />') if paths else ""


def hero_svg(routes: list[dict], corridors: list[dict]) -> str:
    """Общая карта: все повторившиеся плечи, поверх — стрелки коридоров."""
    projection = Projection(HERO_BBOX, HERO_W, HERO_H, pad=8, precision=0)
    segments = [s for s in build_segments(routes)
                if projection.inside(s[0], s[1]) and projection.inside(s[2], s[3])]
    peak = max((s[4] for s in segments), default=1)

    lines = []
    for lat0, lon0, lat1, lon1, count in sorted(segments, key=lambda s: s[4]):
        x0, y0 = projection.xy(lat0, lon0)
        x1, y1 = projection.xy(lat1, lon1)
        share = math.log1p(count) / math.log1p(peak)
        width = round(0.8 + 3.2 * share, 1)
        opacity = round(0.25 + 0.65 * share, 2)
        lines.append(
            f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}" '
            f'stroke-width="{width}" stroke-opacity="{opacity}" />')

    # Стрелки — только у самых частых коридоров: направление читается, а
    # карта не зарастает наконечниками.
    heads = []
    for corridor in corridors[:12]:
        tail, head = corridor["face"][-2], corridor["face"][-1]
        if not (projection.inside(head[0], head[1])
                and projection.inside(tail[0], tail[1])):
            continue
        x0, y0 = projection.xy(tail[0], tail[1])
        x1, y1 = projection.xy(head[0], head[1])
        angle = math.atan2(y1 - y0, x1 - x0)
        size = 9.0
        left = (x1 - size * math.cos(angle - 0.45),
                y1 - size * math.sin(angle - 0.45))
        right = (x1 - size * math.cos(angle + 0.45),
                 y1 - size * math.sin(angle + 0.45))
        heads.append(
            f'<path d="M{round(left[0],1)} {round(left[1],1)} L{x1} {y1} '
            f'L{round(right[0],1)} {round(right[1],1)}" stroke="#ff8592" '
            f'stroke-width="2" fill="none" stroke-linejoin="round" />')

    return (f'<svg viewBox="0 0 {HERO_W} {HERO_H}" role="img" '
            f'aria-label="Карта повторяющихся маршрутов БПЛА" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="{HERO_W}" height="{HERO_H}" fill="#0b0f0e" />'
            + region_paths(projection)
            + f'<g stroke="#e93e4e" stroke-linecap="round">{"".join(lines)}</g>'
            + "".join(heads) + "</svg>")


def card_svg(corridor: dict) -> str:
    """Мини-карта коридора: все его маршруты бледно, лицо — стрелкой."""
    lats = [p[0] for r in corridor["routes"] for p in r["points"]]
    lons = [p[1] for r in corridor["routes"] for p in r["points"]]
    spread = max(max(lats) - min(lats), (max(lons) - min(lons)) * 0.65, 0.2)
    pad_deg = spread * 0.22
    bbox = (min(lats) - pad_deg, min(lons) - pad_deg / 0.65,
            max(lats) + pad_deg, max(lons) + pad_deg / 0.65)
    width, height = 280, 170
    projection = Projection(bbox, width, height, pad=10)

    faint = []
    # Одинаковые цепочки дают одинаковые линии — рисуем каждую один раз.
    seen: set[str] = set()
    for route in corridor["routes"][:60]:
        points = " ".join(
            f"{x},{y}" for x, y in
            (projection.xy(p[0], p[1]) for p in route["points"]))
        if points in seen:
            continue
        seen.add(points)
        faint.append(f'<polyline points="{points}" />')

    face = corridor["face"]
    face_xy = [projection.xy(p[0], p[1]) for p in face]
    face_line = " ".join(f"{x},{y}" for x, y in face_xy)
    (x0, y0), (x1, y1) = face_xy[-2], face_xy[-1]
    angle = math.atan2(y1 - y0, x1 - x0)
    size = 8.0
    left = (x1 - size * math.cos(angle - 0.45), y1 - size * math.sin(angle - 0.45))
    right = (x1 - size * math.cos(angle + 0.45), y1 - size * math.sin(angle + 0.45))

    labels = []
    for (x, y), name, anchor in ((face_xy[0], corridor["start"], "start"),
                                 (face_xy[-1], corridor["end"], "end")):
        # Подпись не должна вылезать за кадр — прижимается к своему краю.
        align = "start" if x < width / 2 else "end"
        ty = y - 7 if y > 24 else y + 14
        labels.append(
            f'<circle cx="{x}" cy="{y}" r="3" '
            f'fill="{"#ff8592" if anchor == "end" else "#9fd4b0"}" />'
            f'<text x="{x}" y="{ty}" text-anchor="{align}" fill="#dfe6df" '
            f'font-size="11">{escape(name)}</text>')

    return (f'<svg viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Коридор {escape(corridor["start"])} — '
            f'{escape(corridor["end"])}" xmlns="http://www.w3.org/2000/svg">'
            f'<rect width="{width}" height="{height}" fill="#0e1311" rx="8" />'
            + f'<g fill="none" stroke="#e93e4e" stroke-opacity="0.14" '
              f'stroke-width="1.6" stroke-linecap="round">{"".join(faint)}</g>'
            + f'<polyline points="{face_line}" fill="none" stroke="#e93e4e" '
              f'stroke-width="2.4" stroke-linecap="round" '
              f'stroke-linejoin="round" />'
            + f'<path d="M{round(left[0],1)} {round(left[1],1)} L{x1} {y1} '
              f'L{round(right[0],1)} {round(right[1],1)}" stroke="#ff8592" '
              f'stroke-width="2.2" fill="none" stroke-linejoin="round" />'
            + "".join(labels) + "</svg>")


THREAT_WORDS = {"uav": "БПЛА", "fpv": "FPV", "rocket": "ракеты",
                "kab": "КАБ", "bek": "БЭК", "aviation": "авиация"}


def card_html(corridor: dict) -> str:
    threat_keys = [k for k, _ in corridor["threats"].most_common(2)
                   if k != "unknown"]
    threats = ", ".join(THREAT_WORDS.get(k, k) for k in threat_keys) or "БПЛА"
    months = len(corridor["months"])
    stability = (f"{months} {plural(months, 'месяц', 'месяца', 'месяцев')} подряд"
                 if months > 1 else "в этом месяце")
    return f"""
    <figure class="corridor">
      {card_svg(corridor)}
      <figcaption>
        <b>{escape(corridor["start"])} → {escape(corridor["end"])}</b>
        <span>{corridor["count"]} {plural(corridor["count"], "маршрут", "маршрута", "маршрутов")}
        · {stability} · последний {day_word(corridor["last"])}
        · {corridor["night_share"]}% ночью · {escape(threats)}</span>
      </figcaption>
    </figure>"""


def build_page(routes: list[dict], updated: str) -> str:
    corridors = build_corridors(routes)
    total = len(routes)
    night = sum(
        1 for r in routes
        if datetime.fromisoformat(r["posted_at"]).astimezone(MSK).hour >= 21
        or datetime.fromisoformat(r["posted_at"]).astimezone(MSK).hour < 6)
    night_share = round(night * 100 / total) if total else 0
    first = min((r["posted_at"] for r in routes), default="")
    span_days = 0
    if first:
        span_days = (now_utc() - datetime.fromisoformat(first)).days

    title = "Маршруты БПЛА — повторяющиеся коридоры на карте"
    description = (
        f"{total} маршрутов БПЛА за {span_days} дней из открытых сообщений: "
        f"общая карта коридоров и галерея самых устойчивых — с числом "
        f"повторов и временем активности.")
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

    cards = "".join(card_html(c) for c in corridors)
    hero = hero_svg(routes, corridors)

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
      main {{ max-width:1020px; margin:0 auto; padding:40px 20px 80px; }}
      h1 {{ font-size:29px; line-height:1.25; margin:0 0 14px; max-width:760px; }}
      h2 {{ font-size:19px; margin:38px 0 10px; }}
      p {{ color:#aab4ad; max-width:760px; }}
      nav.crumbs {{ font-size:13px; color:#7d8a83; margin:0 0 18px; }}
      nav.crumbs a {{ color:#9fd4b0; text-decoration:none; }}
      a.map {{ display:inline-block; margin:16px 0 8px; padding:13px 22px;
              background:#e93e4e; color:#fff; text-decoration:none;
              border-radius:10px; font-weight:600; }}
      .hero {{ margin:26px 0 8px; border-radius:12px; overflow:hidden;
              border:1px solid rgba(255,255,255,.07); }}
      .hero svg {{ display:block; width:100%; height:auto; }}
      .hero-note {{ font-size:13px; color:#7d8a83; margin-top:8px; }}
      .gallery {{ display:grid; gap:22px 18px; margin-top:20px;
                 grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); }}
      figure.corridor {{ margin:0; }}
      figure.corridor svg {{ display:block; width:100%; height:auto;
                            border-radius:8px; }}
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
      источниками: «от Анапы через Раевскую на Новороссийск». Большинство
      повторяется из ночи в ночь — {night_share}% маршрутов приходится на
      ночные часы. Ниже — общая карта всех повторившихся плеч и галерея
      самых устойчивых коридоров.</p>

      <a class="map" href="/">Открыть живую карту</a>

      <div class="hero">{hero}</div>
      <p class="hero-note">Толщина линии — сколько раз источники называли
      это плечо. Единичные упоминания не показаны.</p>

      <h2>Устойчивые коридоры</h2>
      <p>Каждая карточка — один коридор: бледные линии — все его маршруты,
      яркая стрелка — самый частый вариант пути.</p>
      <div class="gallery">{cards}</div>

      <h2>Как это читать</h2>
      <p>Маршрут попадает в базу, только когда источник сам описал путь —
      с началом, направлением и промежуточными точками. Карта ничего не
      достраивает и не соединяет события между собой: повтор коридора —
      это повтор формулировок в открытых сообщениях, а не вычисленная
      траектория. Число повторов зависит и от активности каналов
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
  </body>
</html>
"""


def build(connection: sqlite3.Connection) -> int:
    """Собрать dist/marshruty/index.html; вернуть число коридоров."""
    routes = load_routes(connection)
    today = now_utc().astimezone(MSK)
    updated = f"{today.day} {MONTHS[today.month - 1]}, {today:%H:%M} МСК"
    directory = OUT / "marshruty"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.html").write_text(
        build_page(routes, updated), encoding="utf-8")
    return len(build_corridors(routes))


def main() -> int:
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        count = build(connection)
    print(f"маршруты: страница собрана, коридоров в галерее {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
