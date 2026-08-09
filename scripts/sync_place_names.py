"""Синхронизировать канонические имена НП с существующим справочником зон.

Полная пересборка gazetteer удаляет зоны и меняет slug-и, поэтому для живой
базы она неприемлема: события уже ссылаются на эти идентификаторы. Здесь
меняется только name_ru по стабильному GeoNames source_id. Старое имя остаётся
алиасом, новое становится primary — старые формулировки источников не ломаются.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pipeline.db import ROOT, connect
from pipeline.textnorm import name_variants, norm_key

PLACES_PATH = ROOT / "public" / "data" / "places.json"


def canonical_names(path: Path = PLACES_PATH) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"В {path} нет массива rows")

    names: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        source_id, name = str(row[0] or "").strip(), str(row[1] or "").strip()
        if source_id and name:
            names[source_id] = name
    return names


def sync_place_names(
    connection: sqlite3.Connection,
    path: Path = PLACES_PATH,
) -> dict[str, int]:
    names = canonical_names(path)
    zones = connection.execute(
        "SELECT id, source_id, name_ru FROM zones "
        "WHERE level = 'place' AND source_id IS NOT NULL"
    ).fetchall()
    changes = [
        (row["id"], row["name_ru"], names.get(str(row["source_id"])))
        for row in zones
        if names.get(str(row["source_id"]))
        and names[str(row["source_id"])] != row["name_ru"]
    ]

    aliases = []
    for zone_id, _old_name, new_name in changes:
        primary = norm_key(new_name)
        aliases.extend(
            (variant, zone_id, "primary" if variant == primary else "variant")
            for variant in name_variants(new_name, "place")
        )

    with connection:
        connection.execute(
            "CREATE TEMP TABLE place_name_updates ("
            "zone_id TEXT PRIMARY KEY, new_name TEXT NOT NULL) WITHOUT ROWID"
        )
        connection.executemany(
            "INSERT INTO place_name_updates VALUES (?, ?)",
            ((zone_id, new_name) for zone_id, _old_name, new_name in changes),
        )
        connection.execute("""
            UPDATE zones
            SET name_ru = (
                SELECT new_name FROM place_name_updates
                WHERE zone_id = zones.id
            )
            WHERE id IN (SELECT zone_id FROM place_name_updates)
        """)
        connection.execute("""
            UPDATE zone_names
            SET kind = 'variant'
            WHERE kind = 'primary'
              AND EXISTS (
                  SELECT 1 FROM place_name_updates
                  WHERE zone_id = zone_names.zone_id
              )
        """)
        connection.executemany(
            "INSERT INTO zone_names (norm, zone_id, kind) VALUES (?, ?, ?) "
            "ON CONFLICT(norm, zone_id) DO UPDATE SET kind = excluded.kind",
            aliases,
        )
        connection.execute("DROP TABLE place_name_updates")

    return {
        "canonical": len(names),
        "zones": len(zones),
        "changed": len(changes),
    }


def main() -> int:
    stats = sync_place_names(connect())
    print(
        "Имена НП: "
        f"{stats['changed']} исправлено, "
        f"{stats['zones']} зон сверено с {stats['canonical']} каноническими"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
