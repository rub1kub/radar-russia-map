"""Полный переразбор: raw_messages -> events + event_sources.

    ingest/.venv/bin/python -m pipeline.rebuild

Сырые сообщения не трогаются, производные таблицы пересобираются целиком.
Так и должно быть: парсер будет меняться постоянно.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))

from config import sources_from_env  # noqa: E402

from .db import connect, counts, reset_derived  # noqa: E402
from .fuse import Fuser  # noqa: E402
from .geocode import Geocoder  # noqa: E402
from .parse import parse  # noqa: E402
from .timeutil import now_utc, parse_utc  # noqa: E402

TIERS = {source.key: source.tier for source in sources_from_env()}
USERNAME_TO_KEY = {source.username: source.key for source in sources_from_env()}


def import_jsonl(connection, raw_dir: Path) -> int:
    """Залить выборки ingest/data/raw/*.jsonl в raw_messages."""
    added = 0
    for path in sorted(raw_dir.glob("*.jsonl")):
        fallback_key = USERNAME_TO_KEY.get(path.stem, path.stem)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("text") or not row.get("date"):
                continue
            # live.jsonl содержит сообщения всех каналов вперемешку и несёт
            # собственное поле source. Без него всё живое падало в
            # несуществующий источник "live", задваивая сообщения и ломая
            # и подтверждение между лентами, и аналитику источников.
            source_key = row.get("source") or fallback_key
            cursor = connection.execute(
                "INSERT OR IGNORE INTO raw_messages"
                " (source_key, chat_id, message_id, posted_at, received_at, text, views)"
                " VALUES (?,?,?,?,?,?,?)",
                # Метка нормализуется на входе: в JSONL могут лежать записи
                # старого формата — наивное локальное время.
                (source_key, row.get("chat_id"), row["message_id"],
                 parse_utc(row["date"]).isoformat(),
                 row.get("received_at"), row["text"], row.get("views")),
            )
            added += cursor.rowcount
    connection.commit()
    return added


def rebuild(connection) -> dict:
    reset_derived(connection)
    geocoder = Geocoder(connection)
    fuser = Fuser()

    rows = connection.execute(
        "SELECT id, source_key, posted_at, text FROM raw_messages ORDER BY posted_at"
    ).fetchall()

    stats = {"messages": len(rows), "irrelevant": 0, "ungeocoded": 0, "observations": 0}

    for row in rows:
        observation = parse(row["text"])
        if not observation.relevant:
            stats["irrelevant"] += 1
            continue

        resolved = geocoder.resolve(observation.place_phrases)
        if not resolved:
            stats["ungeocoded"] += 1
            continue

        moment = parse_utc(row["posted_at"])

        # Сообщение может называть несколько независимых мест — каждое
        # становится отдельным наблюдением в своей зоне.
        for item in resolved:
            fuser.add(
                raw_id=row["id"],
                source_key=row["source_key"],
                tier=TIERS.get(row["source_key"], "regional"),
                moment=moment,
                observation=observation,
                zone_path=geocoder.zone_path(item.zone_id),
                lat=item.lat,
                lon=item.lon,
                level=item.level,
            )
            stats["observations"] += 1

    now = now_utc()
    for event in fuser.events:
        connection.execute(
            "INSERT INTO events (id, first_seen_at, last_seen_at, resolved_at, status,"
            " signal_type, threat_type, severity, confidence, source_count, zone_id,"
            " zone_path, lat, lon, accuracy_m, direction_deg, target_count)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event.id, event.first_seen.isoformat(), event.last_seen.isoformat(),
             event.resolved_at.isoformat() if event.resolved_at else None,
             event.status(now), event.signal_type, event.threat_type, event.severity,
             event.confidence, len(event.sources), event.zone_id,
             json.dumps(event.zone_path, ensure_ascii=False), event.lat, event.lon,
             event.accuracy_m, event.direction_deg, event.target_count),
        )
        for raw_id, source_key, role, contributed in event.contributions:
            connection.execute(
                "INSERT OR IGNORE INTO event_sources"
                " (event_id, raw_message_id, source_key, contributed_at, role)"
                " VALUES (?,?,?,?,?)",
                (event.id, raw_id, source_key, contributed.isoformat(), role),
            )
    connection.commit()

    stats["events"] = len(fuser.events)
    stats["multi_source"] = sum(1 for event in fuser.events if len(event.sources) > 1)
    return stats


if __name__ == "__main__":
    connection = connect()
    raw_dir = Path(__file__).resolve().parent.parent / "ingest" / "data" / "raw"
    imported = import_jsonl(connection, raw_dir)
    print(f"импортировано новых сообщений: {imported}")

    stats = rebuild(connection)
    print("\nразбор:", stats)
    print("таблицы:", counts(connection))

    print("\nсобытия с наибольшим подтверждением:")
    for row in connection.execute("""
        SELECT e.severity, e.confidence, e.source_count, e.threat_type, e.signal_type,
               z.name_ru, z.level, e.first_seen_at
        FROM events e JOIN zones z ON z.id = e.zone_id
        ORDER BY e.source_count DESC, e.confidence DESC LIMIT 10
    """):
        print(f"  [{row['source_count']} источн. conf={row['confidence']:.2f} sev={row['severity']}] "
              f"{row['signal_type']}/{row['threat_type']} — {row['name_ru']} ({row['level']}) "
              f"{row['first_seen_at'][11:16]}")
