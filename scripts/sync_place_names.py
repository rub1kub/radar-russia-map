"""Синхронизировать канонические имена зон с существующим справочником.

Полная пересборка gazetteer удаляет зоны и меняет slug-и, поэтому для живой
базы она неприемлема: события уже ссылаются на эти идентификаторы. Здесь
меняется только name_ru по стабильному source_id GeoNames/geoBoundaries.
Старое имя остаётся алиасом, новое становится primary — старые формулировки
источников не ломаются.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pipeline.db import ROOT, connect
from pipeline.textnorm import name_variants, norm_key

PLACES_PATH = ROOT / "public" / "data" / "places.json"
DISTRICTS_PATH = ROOT / "public" / "data" / "districts.json"
REGIONS_PATH = ROOT / "public" / "data" / "regions.json"


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


def canonical_polygon_names(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError(f"В {path} нет массива features")

    names: dict[str, str] = {}
    for feature in features:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict):
            continue
        source_id = str(properties.get("id") or "").strip()
        name = str(properties.get("name") or "").strip()
        if source_id and name:
            names[source_id] = name
    return names


def canonical_district_names(path: Path = DISTRICTS_PATH) -> dict[str, str]:
    return canonical_polygon_names(path)


def sync_place_names(
    connection: sqlite3.Connection,
    path: Path = PLACES_PATH,
    districts_path: Path | None = None,
    regions_path: Path | None = None,
) -> dict[str, int]:
    names_by_level = {"place": canonical_names(path)}
    if districts_path is not None:
        names_by_level["district"] = canonical_district_names(districts_path)
    if regions_path is not None:
        names_by_level["region"] = canonical_polygon_names(regions_path)

    placeholders = ",".join("?" for _level in names_by_level)
    zones = connection.execute(
        "SELECT id, level, source_id, name_ru FROM zones "
        f"WHERE level IN ({placeholders}) AND source_id IS NOT NULL",
        tuple(names_by_level),
    ).fetchall()
    changes = [
        (
            row["id"],
            row["level"],
            row["name_ru"],
            names_by_level[row["level"]].get(str(row["source_id"])),
        )
        for row in zones
        if names_by_level[row["level"]].get(str(row["source_id"]))
        and names_by_level[row["level"]][str(row["source_id"])] != row["name_ru"]
    ]

    aliases = []
    for zone_id, level, _old_name, new_name in changes:
        primary = norm_key(new_name)
        aliases.extend(
            (variant, zone_id, "primary" if variant == primary else "variant")
            for variant in name_variants(new_name, level)
        )

    with connection:
        connection.execute(
            "CREATE TEMP TABLE place_name_updates ("
            "zone_id TEXT PRIMARY KEY, new_name TEXT NOT NULL) WITHOUT ROWID"
        )
        connection.executemany(
            "INSERT INTO place_name_updates VALUES (?, ?)",
            ((zone_id, new_name) for zone_id, _level, _old_name, new_name in changes),
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
        "canonical": sum(len(names) for names in names_by_level.values()),
        "zones": len(zones),
        "changed": len(changes),
    }


def main() -> int:
    stats = sync_place_names(
        connect(), PLACES_PATH, DISTRICTS_PATH, REGIONS_PATH
    )
    print(
        "Имена зон: "
        f"{stats['changed']} исправлено, "
        f"{stats['zones']} зон сверено с {stats['canonical']} каноническими"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
