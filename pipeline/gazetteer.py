"""Построение справочника зон из public/data.

    ingest/.venv/bin/python -m pipeline.gazetteer

Регион -> район -> населенный пункт. Родитель определяется геометрически:
центроид района внутри полигона региона, точка НП внутри полигона района.
Это закрывает главный пробел прототипа — отсутствие административной иерархии.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from .db import ROOT, connect
from .oktmo import Municipality, Registry, same_place
from .textnorm import name_variants, norm_key, slugify

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


def write_district_facts(path: Path, districts: list[dict]) -> int:
    """Вернуть в файл полигонов исправленное имя и родительский регион.

    Подпись района на карте берётся из districts.json, а не из базы: клиент
    красит и подписывает полигон тем, что приехало с геометрией. Если
    исправить имя только в справочнике, лента скажет «Анапа», а карта под
    курсором по-прежнему «городской округ Новороссий».

    Родитель нужен по той же причине. В тихом районе карта не знает его
    зоны — соответствие полигонов зонам строится из счётчиков обстановки, а
    там только те зоны, где что-то происходит. Без родителя клиенту
    приходилось искать регион геометрией, по точке внутри района, и у
    изогнутых районов эта точка попадала в соседний субъект. Справочник
    родителя знает точно, поэтому пусть он его и запишет.

    Порядок в конвейере поэтому такой: prepare:data собирает геометрию,
    pipeline.gazetteer правит имена и родителей здесь и в базе разом.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    facts = {
        district["source_id"]: (district["name"], district.get("region_source_id"))
        for district in districts
    }
    changed = 0
    for feature in payload["features"]:
        properties = feature.get("properties") or {}
        name, region = facts.get(str(properties.get("id") or ""), (None, None))
        if name and name != properties.get("name"):
            properties["name"] = name
            changed += 1
        if region and region != properties.get("region"):
            properties["region"] = region
            changed += 1
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    return changed


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
    district_tree = STRtree([district["geom"] for district in districts])

    # НП раскладываются по полигонам раньше, чем районы получают имена:
    # именно состав НП и опознаёт район в реестре ОКТМО. Имя должно быть
    # известно до того, как из него построен идентификатор зоны.
    places = json.loads((DATA / "places.json").read_text(encoding="utf-8"))["rows"]
    place_parent: list[int | None] = []
    inside: dict[int, list[str]] = {}
    for row in places:
        point = Point(row[4], row[3])
        hits = [index for index in district_tree.query(point)
                if districts[index]["geom"].contains(point)]
        index = min(hits, key=lambda i: districts[i]["geom"].area) if hits else None
        place_parent.append(index)
        if index is not None:
            inside.setdefault(index, []).append((norm_key(row[1]), row[5] or 0))

    def find_region_by_overlap(geometry) -> str | None:
        """Регион района — тот, с которым район пересекается сильнее всего.

        По точке-представителю район привязывался неверно там, где граница
        региона огрублена: округа Севастополя не попадали в Севастополь, и
        весь город остался без единого района, а его округа висели в Крыму.
        Площадь пересечения такой погрешности не замечает.
        """
        hits = list(region_tree.query(geometry))
        if not hits:
            return find_region(geometry)
        overlaps = []
        for index in hits:
            area = geometry.intersection(regions[index]["geom"]).area
            if area > 0:
                overlaps.append((area, index))
        if not overlaps:
            return find_region(geometry)
        best = max(area for area, _ in overlaps)
        # Город федерального значения лежит внутри области, и пересечение с
        # обоими почти одинаково. Из равных берём меньший — он и есть свой.
        close = [index for area, index in overlaps if area >= best * 0.9]
        return region_ids[min(close, key=lambda index: regions[index]["geom"].area)]

    district_region = [find_region_by_overlap(district["geom"]) for district in districts]
    # Полигон региона рядом с его зоной: клиенту нужен именно source_id,
    # потому что районы и регионы он связывает по идентификаторам полигонов.
    region_polygon = {zone_id: region["source_id"]
                      for zone_id, region in zip(region_ids, regions)}
    for index, district in enumerate(districts):
        district["region_source_id"] = region_polygon.get(district_region[index] or "")

    # Опознание в два прохода. Первый идёт по составу НП и потому надёжен
    # без всяких рамок; он же и показывает, какому коду ОКТМО отвечает наш
    # регион — сопоставлять названия субъектов вручную не приходится.
    # Второй разбирает остаток, и вот ему рамка субъекта необходима: там
    # решает главное место полигона, а Новониколаевок в стране много, и без
    # рамки Запорожская область набрала бы районов Тюменской и Адыгеи.
    registry = Registry.load()
    matched: list[tuple[Municipality, float] | None] = [
        registry.match(inside.get(index, [])) for index in range(len(districts))
    ]

    votes: dict[str, Counter] = defaultdict(Counter)
    for index, found in enumerate(matched):
        if found and district_region[index]:
            votes[district_region[index]][found[0].code[0]] += 1
    region_code = {zone: counter.most_common(1)[0][0] for zone, counter in votes.items()}

    for index, found in enumerate(matched):
        if found is None and district_region[index] in region_code:
            matched[index] = registry.match(
                inside.get(index, []), region=region_code[district_region[index]]
            )

    # Один муниципалитет — один полигон. Без этого правила город и
    # окружающий его район претендуют на одну запись реестра и получают
    # одно имя на двоих: в Запорожской области так родились два
    # «Приазовских округа», в Нижегородской — два «Нижних Новгорода».
    # Спор решает мера совпадения, проигравший остаётся под своим именем.
    claimed: dict[tuple[str, str], int] = {}
    order = sorted(
        (index for index, found in enumerate(matched) if found),
        key=lambda index: matched[index][1],
        reverse=True,
    )
    renamed = 0
    for index in order:
        entry, _score = matched[index]
        if entry.code in claimed:
            continue
        claimed[entry.code] = index
        # Реестровое имя ставится только тогда, когда оно расходится с
        # привезённым. Совпало по существу — оставляем привычную огласовку:
        # «Бежаницкий район» и «Бежаницкий округ» это одно и то же место, и
        # переписывать его без нужды значит менять подписи по всей карте.
        if not same_place(districts[index]["name"], entry.name):
            districts[index]["name"] = entry.name
            renamed += 1

    touched = write_district_facts(DATA / "districts.json", districts)
    print(f"полей в файле полигонов обновлено: {touched}")

    district_ids: list[str] = []
    for index, district in enumerate(districts):
        parent = district_region[index]
        base = slugify(district["name"])
        if parent:
            base = f"{base}_{parent}"
        zone_id = assign_unique(base, taken)
        district_ids.append(zone_id)
        centroid = district["geom"].representative_point()
        add_zone(zone_id, parent, "district", district["name"],
                 centroid.y, centroid.x, None, None, district["source_id"])

    print(f"районов:  {len(districts)}, имя исправлено по ОКТМО у {renamed}")

    # --- Населенные пункты ---------------------------------------------
    for row, index in zip(places, place_parent):
        place_id, name, _ascii, lat, lon, population, code, _label = row
        parent = district_ids[index] if index is not None else find_region(Point(lon, lat))
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
