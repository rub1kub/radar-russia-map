"""Публичный API конвейера.

    ingest/.venv/bin/uvicorn api.server:app --port 8000 --reload

Публично отдаются достоверность и число подтвердивших источников, но не имена
каналов и не тексты первичных сообщений (см. docs/TARGET_ARCHITECTURE.md §8).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from pipeline.db import DB_PATH
from pipeline.timeutil import now_utc, parse_utc

app = FastAPI(title="Radar API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

ACTIVE_WINDOW = timedelta(hours=6)


def query(sql: str, params: tuple = ()) -> list[dict]:
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params)]


def latest_moment() -> datetime:
    """Момент последнего сообщения — НЕ «сейчас».

    Раньше от него отсчитывалась активность, поэтому остановка сбора
    замораживала карту: старые события вечно оставались «активными».
    """
    rows = query("SELECT MAX(posted_at) AS m FROM raw_messages")
    stamp = rows[0]["m"] if rows and rows[0]["m"] else None
    return parse_utc(stamp) if stamp else now_utc()


def event_rows(since: datetime, limit: int = 400) -> list[dict]:
    rows = query(
        """
        SELECT e.id, e.first_seen_at, e.last_seen_at, e.resolved_at, e.status,
               e.signal_type, e.threat_type, e.severity, e.confidence, e.source_count,
               e.zone_id, e.zone_path, e.lat, e.lon, e.accuracy_m,
               e.direction_deg, e.target_count,
               z.name_ru AS place_name, z.level AS zone_level
        FROM events e JOIN zones z ON z.id = e.zone_id
        WHERE e.last_seen_at >= ?
        ORDER BY e.last_seen_at DESC LIMIT ?
        """,
        (since.isoformat(), limit),
    )
    for row in rows:
        row["zone_path"] = json.loads(row["zone_path"] or "[]")
    return rows


@app.get("/api/v1/state")
def state():
    """Текущая обстановка: активные события и счетчики по зонам."""
    now = now_utc()
    last_message = latest_moment()
    data_age_sec = max(0, int((now - last_message).total_seconds()))
    events = [row for row in event_rows(now - ACTIVE_WINDOW) if row["status"] != "resolved"]

    zone_counts: dict[str, dict] = {}
    for event in events:
        # Событие поднимается по всей цепочке родителей — регион светится,
        # если горит любое поселение внутри него.
        for zone_id in event["zone_path"]:
            bucket = zone_counts.setdefault(
                zone_id, {"active": 0, "max_severity": 0, "last_active": None})
            bucket["active"] += 1
            bucket["max_severity"] = max(bucket["max_severity"], event["severity"])
            if not bucket["last_active"] or event["last_seen_at"] > bucket["last_active"]:
                bucket["last_active"] = event["last_seen_at"]

    # source_id связывает зону с полигоном, который уже загружен клиентом:
    # это тот же id, что в public/data/regions.json и districts.json.
    if zone_counts:
        placeholders = ",".join("?" * len(zone_counts))
        meta = query(
            f"SELECT id, level, source_id, name_ru FROM zones WHERE id IN ({placeholders})",
            tuple(zone_counts),
        )
        for row in meta:
            bucket = zone_counts[row["id"]]
            bucket["level"] = row["level"]
            bucket["source_id"] = row["source_id"]
            bucket["name"] = row["name_ru"]

    return {
        "generated_at": now.isoformat(),
        "last_message_at": last_message.isoformat(),
        # Клиент обязан показать, что картинка устарела, если сбор встал.
        "data_age_sec": data_age_sec,
        "stale": data_age_sec > 900,
        "events": events,
        "zone_counts": zone_counts,
        "active_events": len(events),
        "active_zones": len(zone_counts),
    }


@app.get("/api/v1/history")
def history(hours: int = Query(24, ge=1, le=24 * 30)):
    """Произвольное историческое окно, а не только сутки."""
    now = latest_moment()
    since = now - timedelta(hours=hours)
    return {"from": since.isoformat(), "to": now.isoformat(),
            "events": event_rows(since, limit=5000)}


@app.get("/api/v1/analytics/sources")
def analytics_sources():
    """Метрики источников: скорость и подтверждаемость.

    Этого нет ни у одного из существующих сервисов.
    """
    totals = {row["source_key"]: row for row in query(
        "SELECT source_key, COUNT(*) AS messages FROM raw_messages GROUP BY source_key")}

    roles = query(
        "SELECT source_key, role, COUNT(*) AS n FROM event_sources GROUP BY source_key, role")
    confirmed = query("""
        SELECT es.source_key, COUNT(*) AS n
        FROM event_sources es JOIN events e ON e.id = es.event_id
        WHERE e.source_count > 1 GROUP BY es.source_key
    """)
    lonely = query("""
        SELECT es.source_key, COUNT(*) AS n
        FROM event_sources es JOIN events e ON e.id = es.event_id
        WHERE e.source_count = 1 GROUP BY es.source_key
    """)

    # Задержка относительно первого сообщения о событии.
    lag_rows = query("""
        SELECT es.source_key,
               (julianday(es.contributed_at) - julianday(e.first_seen_at)) * 86400.0 AS lag_sec
        FROM event_sources es JOIN events e ON e.id = es.event_id
        WHERE e.source_count > 1
    """)
    lags: dict[str, list[float]] = {}
    for row in lag_rows:
        lags.setdefault(row["source_key"], []).append(row["lag_sec"])

    out = []
    for key, base in totals.items():
        by_role = {row["role"]: row["n"] for row in roles if row["source_key"] == key}
        got_confirmed = next((row["n"] for row in confirmed if row["source_key"] == key), 0)
        got_lonely = next((row["n"] for row in lonely if row["source_key"] == key), 0)
        values = sorted(lags.get(key, []))
        median = values[len(values) // 2] if values else None
        contributions = got_confirmed + got_lonely

        out.append({
            "source_key": key,
            "messages": base["messages"],
            "contributions": contributions,
            "first_reports": by_role.get("first", 0),
            "confirmations": by_role.get("confirm", 0),
            "confirmed_share": round(got_confirmed / contributions, 3) if contributions else 0.0,
            "unconfirmed_share": round(got_lonely / contributions, 3) if contributions else 0.0,
            "median_lag_sec": round(median) if median is not None else None,
        })

    out.sort(key=lambda row: -row["first_reports"])
    return {"sources": out}


@app.get("/api/v1/analytics/zones")
def analytics_zones(hours: int = Query(168, ge=1, le=24 * 90), limit: int = 25):
    """Плотность и длительность по зонам за произвольный период."""
    since = (latest_moment() - timedelta(hours=hours)).isoformat()
    rows = query("""
        SELECT z.name_ru, z.level, e.zone_id,
               COUNT(*) AS events,
               MAX(e.severity) AS max_severity,
               AVG(e.confidence) AS avg_confidence,
               AVG((julianday(e.last_seen_at) - julianday(e.first_seen_at)) * 86400.0) AS avg_duration_sec
        FROM events e JOIN zones z ON z.id = e.zone_id
        WHERE e.first_seen_at >= ?
        GROUP BY e.zone_id ORDER BY events DESC LIMIT ?
    """, (since, limit))
    for row in rows:
        row["avg_confidence"] = round(row["avg_confidence"] or 0, 3)
        row["avg_duration_sec"] = round(row["avg_duration_sec"] or 0)

    by_hour = query("""
        SELECT substr(first_seen_at, 12, 2) AS hour, COUNT(*) AS n
        FROM events WHERE first_seen_at >= ? GROUP BY hour ORDER BY hour
    """, (since,))
    by_threat = query("""
        SELECT threat_type, COUNT(*) AS n FROM events
        WHERE first_seen_at >= ? GROUP BY threat_type ORDER BY n DESC
    """, (since,))

    return {"top_zones": rows, "by_hour": by_hour, "by_threat": by_threat}


@app.get("/api/v1/search")
def search(q: str = Query(..., min_length=2), limit: int = Query(12, ge=1, le=40)):
    """Поиск по справочнику на сервере.

    Раньше клиент держал в памяти каталог из 212 тысяч строк и фильтровал его
    на каждое нажатие клавиши. Теперь это индексированный запрос к SQLite.
    """
    needle = q.strip().lower().replace("ё", "е")
    if len(needle) < 2:
        return {"items": []}

    rows = query(
        """
        SELECT z.id, z.name_ru, z.level, z.lat, z.lon, z.population, z.source_id,
               p.name_ru  AS parent_name,
               gp.name_ru AS grandparent_name,
               MIN(LENGTH(n.norm)) AS match_len
        FROM zone_names n
        JOIN zones z       ON z.id = n.zone_id
        LEFT JOIN zones p  ON p.id = z.parent_id
        LEFT JOIN zones gp ON gp.id = p.parent_id
        WHERE n.norm LIKE ? ESCAPE '\\'
        GROUP BY z.id
        ORDER BY
            CASE z.level WHEN 'region' THEN 0 WHEN 'district' THEN 1 ELSE 2 END,
            COALESCE(z.population, 0) DESC,
            match_len
        LIMIT ?
        """,
        (needle.replace("%", "\\%").replace("_", "\\_") + "%", limit),
    )

    items = []
    for row in rows:
        # Одноимённых районов больше сотни — показываем родителя,
        # иначе выбрать нужный невозможно.
        context = row["parent_name"] if row["level"] != "region" else None
        if row["level"] == "place" and row["grandparent_name"]:
            context = f"{row['parent_name']} · {row['grandparent_name']}"
        items.append({
            "zone_id": row["id"],
            "name": row["name_ru"],
            "level": row["level"],
            "context": context,
            "lat": row["lat"],
            "lon": row["lon"],
            "population": row["population"],
            "source_id": row["source_id"],
        })
    return {"items": items}


@app.get("/api/v1/summary")
def summary():
    counts = query("""
        SELECT (SELECT COUNT(*) FROM raw_messages) AS raw_messages,
               (SELECT COUNT(*) FROM events) AS events,
               (SELECT COUNT(*) FROM events WHERE source_count > 1) AS multi_source_events,
               (SELECT COUNT(*) FROM zones) AS zones,
               (SELECT COUNT(DISTINCT source_key) FROM raw_messages) AS sources
    """)[0]
    counts["generated_at"] = latest_moment().isoformat()
    return counts


@app.websocket("/api/v1/stream")
async def stream(socket: WebSocket):
    """Push-обновления. Без пейволла — в отличие от RadarMap."""
    await socket.accept()
    last_sent: str | None = None
    try:
        while True:
            snapshot = state()
            marker = f"{snapshot['last_message_at']}|{snapshot['active_events']}"
            if marker != last_sent:
                await socket.send_json({"type": "state", **snapshot})
                last_sent = marker
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        return
