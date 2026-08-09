import json

import scripts.finalize_map_names as finalizer


def write_map(path, source_id, zone_id, locked=False):
    properties = {"id": source_id, "name": "Тест", "zone": zone_id}
    if locked:
        properties["nameLocked"] = True
    path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": properties,
            "geometry": None,
        }],
    }, ensure_ascii=False), encoding="utf-8")


def test_finalizer_preserves_published_zone_ids(tmp_path, monkeypatch):
    regions = tmp_path / "regions.json"
    districts = tmp_path / "districts.json"
    write_map(regions, "region-source", "stable-region")
    write_map(districts, "district-source", "stable-district", locked=True)
    monkeypatch.setattr(finalizer, "MAP_PATHS", (regions, districts))

    stable = finalizer.stable_zone_ids()
    write_map(regions, "region-source", "regenerated-region")
    write_map(districts, "district-source", "regenerated-district", locked=True)
    finalizer.restore_stable_zone_ids(stable)

    region = json.loads(regions.read_text(encoding="utf-8"))["features"][0]["properties"]
    district = json.loads(districts.read_text(encoding="utf-8"))["features"][0]["properties"]
    assert region["zone"] == "stable-region"
    assert district["zone"] == "stable-district"
    assert "nameLocked" not in district
