"""Построение справочника зон из public/data.

    ingest/.venv/bin/python -m pipeline.gazetteer

Регион -> район -> населенный пункт. Родитель определяется геометрически:
центроид района внутри полигона региона, точка НП внутри полигона района.
Это закрывает главный пробел прототипа — отсутствие административной иерархии.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from .db import ROOT, connect
from .textnorm import name_variants, slugify

DATA = ROOT / "public" / "data"


def load_polygons(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for feature in payload["features"]:
        try:
            geometry = shape(feature["geometry"])
        except Exception:
            continue
        if geometry.is_empty:
            continue
        rows.append({
            "source_id": str(feature["properties"].get("id") or ""),
            "name": str(feature["properties"].get("name") or "").strip(),
            "geom": geometry if geometry.is_valid else geometry.buffer(0),
        })
    return [row for row in rows if row["name"]]


def assign_unique(slug: str, taken: set[str]) -> str:
    if slug not in taken:
        taken.add(slug)
        return slug
    index = 2
    while f"{slug}_{index}" in taken:
        index += 1
    unique = f"{slug}_{index}"
    taken.add(unique)
    return unique


def build(connection: sqlite3.Connection) -> dict[str, int]:
    connection.execute("DELETE FROM zone_names")
    connection.execute("DELETE FROM zones")

    taken: set[str] = set()
    zone_rows: list[tuple] = []
    name_rows: list[tuple] = []

    def add_zone(zone_id, parent_id, level, name, lat, lon, population, code, source_id):
        zone_rows.append((zone_id, parent_id, level, name, lat, lon, population, code, source_id))
        for variant in name_variants(name, level):
            name_rows.append((variant, zone_id, "primary" if variant == name.lower() else "variant"))

    # --- Регионы -------------------------------------------------------
    regions = load_polygons(DATA / "regions.json")
    region_ids: list[str] = []
    for region in regions:
        zone_id = assign_unique(slugify(region["name"]), taken)
        region_ids.append(zone_id)
        centroid = region["geom"].representative_point()
        add_zone(zone_id, None, "region", region["name"],
                 centroid.y, centroid.x, None, None, region["source_id"])

    region_tree = STRtree([region["geom"] for region in regions])
    print(f"регионов: {len(regions)}")

    def find_region(geometry) -> str | None:
        probe = geometry if geometry.geom_type == "Point" else geometry.representative_point()
        # Города федерального значения — анклавы внутри области, поэтому из всех
        # содержащих полигонов берем наименьший по площади.
        hits = [index for index in region_tree.query(probe) if regions[index]["geom"].contains(probe)]
        if hits:
            best = min(hits, key=lambda index: regions[index]["geom"].area)
            return region_ids[best]
        # Точка вне всех полигонов (море, погрешность границ) — ближайший регион.
        nearest = region_tree.nearest(probe)
        return region_ids[nearest] if nearest is not None else None

    # --- Районы --------------------------------------------------------
    districts = load_polygons(DATA / "districts.json")
    district_ids: list[str] = []
    for district in districts:
        parent = find_region(district["geom"])
        base = slugify(district["name"])
        if parent:
            base = f"{base}_{parent}"
        zone_id = assign_unique(base, taken)
        district_ids.append(zone_id)
        centroid = district["geom"].representative_point()
        add_zone(zone_id, parent, "district", district["name"],
                 centroid.y, centroid.x, None, None, district["source_id"])

    district_tree = STRtree([district["geom"] for district in districts])
    print(f"районов:  {len(districts)}")

    # --- Населенные пункты ---------------------------------------------
    places = json.loads((DATA / "places.json").read_text(encoding="utf-8"))["rows"]
    for row in places:
        place_id, name, _ascii, lat, lon, population, code, _label = row
        point = Point(lon, lat)

        hits = [index for index in district_tree.query(point)
                if districts[index]["geom"].contains(point)]
        parent = district_ids[min(hits, key=lambda i: districts[i]["geom"].area)] if hits else None
        if parent is None:
            parent = find_region(point)

        zone_id = assign_unique(f"{slugify(name)}_{parent or 'ru'}", taken)
        add_zone(zone_id, parent, "place", name, lat, lon, population, code, str(place_id))

    print(f"НП:       {len(places)}")

    connection.executemany(
        "INSERT INTO zones (id, parent_id, level, name_ru, lat, lon, population, feature_code, source_id)"
        " VALUES (?,?,?,?,?,?,?,?,?)", zone_rows)
    connection.executemany(
        "INSERT OR IGNORE INTO zone_names (norm, zone_id, kind) VALUES (?,?,?)", name_rows)
    connection.commit()

    return {
        "zones": len(zone_rows),
        "names": connection.execute("SELECT COUNT(*) AS n FROM zone_names").fetchone()["n"],
        "orphans": connection.execute(
            "SELECT COUNT(*) AS n FROM zones WHERE parent_id IS NULL AND level <> 'region'"
        ).fetchone()["n"],
    }


if __name__ == "__main__":
    connection = connect()
    stats = build(connection)
    print("\nитого:", stats)
    sample = connection.execute("""
        SELECT p.name_ru AS place, d.name_ru AS district, r.name_ru AS region
        FROM zones p
        LEFT JOIN zones d ON d.id = p.parent_id
        LEFT JOIN zones r ON r.id = d.parent_id
        WHERE p.level = 'place' AND p.population > 300000 LIMIT 8
    """).fetchall()
    print("\nпроверка иерархии:")
    for row in sample:
        print(f"  {row['place']} -> {row['district']} -> {row['region']}")
