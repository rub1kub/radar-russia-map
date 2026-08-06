"""Добавить акватории в справочник зон, не пересобирая его целиком.

Полная пересборка (`python -m pipeline.gazetteer`) начинается с удаления
всех зон, а на них ссылаются события — на живой базе она упирается во
внешние ключи, и правильно делает. Здесь добавляется только недостающее.

Само по себе море попадает в справочник и при полной пересборке: оно лежит
в regions.json с пометкой kind=sea. Этот скрипт нужен для баз, собранных
раньше.

    python scripts/add_sea_zones.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.db import DB_PATH, ROOT
from pipeline.textnorm import norm_key, slugify

REGIONS = ROOT / "public" / "data" / "regions.json"


def centroid(coordinates: list) -> tuple[float, float]:
    """Середина по вершинам. Точности хватает: центр нужен, только чтобы
    карта знала, куда лететь при выборе зоны."""
    points: list[tuple[float, float]] = []

    def walk(node) -> None:
        if not node:
            return
        if isinstance(node[0], (int, float)):
            points.append((float(node[0]), float(node[1])))
            return
        for item in node:
            walk(item)

    walk(coordinates)
    lon = sum(point[0] for point in points) / len(points)
    lat = sum(point[1] for point in points) / len(points)
    return lat, lon


def variants(name: str) -> list[str]:
    """Как это место называют в лентах.

    Только полное имя: «с Азовского моря», «в акватории Азовского моря» —
    падежи снимает стеммер. Сокращать до «Азовское» нельзя: так называются
    село в Крыму и посёлок под Калининградом, а ещё бывает «Азовское
    побережье» и «Азовское шоссе» — всё это суша.
    """
    return [norm_key(name)]


def main() -> int:
    payload = json.loads(REGIONS.read_text(encoding="utf-8"))
    seas = [feature for feature in payload["features"]
            if (feature.get("properties") or {}).get("kind") == "sea"]
    if not seas:
        print("акваторий в regions.json нет — нечего добавлять")
        return 0

    added = 0
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        for feature in seas:
            properties = feature["properties"]
            name = properties["name"]
            zone_id = slugify(name)
            source_id = str(properties.get("id") or zone_id)

            exists = connection.execute(
                "SELECT 1 FROM zones WHERE id = ?", (zone_id,)).fetchone()
            if exists:
                print(f"уже есть: {name} ({zone_id})")
            else:
                lat, lon = centroid(feature["geometry"]["coordinates"])
                connection.execute(
                    "INSERT INTO zones (id, parent_id, level, name_ru, lat, lon,"
                    " population, feature_code, source_id) VALUES (?,?,?,?,?,?,?,?,?)",
                    (zone_id, None, "region", name, lat, lon, None, None, source_id))
                added += 1
                print(f"добавлено: {name} ({zone_id}) — {lat:.3f}, {lon:.3f}")

            for index, variant in enumerate(variants(name)):
                connection.execute(
                    "INSERT OR IGNORE INTO zone_names (norm, zone_id, kind)"
                    " VALUES (?,?,?)",
                    (variant, zone_id, "primary" if index == 0 else "variant"))

            # Полигон должен знать свою зону — по этому полю карта красит
            # акваторию и открывает её из адреса.
            properties["zone"] = zone_id

        connection.commit()

    REGIONS.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
    print(f"итого добавлено зон: {added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
