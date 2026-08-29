"""Дополнить полигоны каноническими именами через ОКТМО без живой БД.

`pipeline.gazetteer` умеет надёжно опознавать район по геометрии и составу
населённых пунктов, но обычный запуск пересобирает таблицы зон. Для подготовки
статических JSON ему передаётся одноразовая SQLite-база: итоговые имена и
родители попадают в `public/data`, опубликованные zone ID восстанавливаются
после прохода, а пользовательские события не затрагиваются.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pipeline.db import ROOT, connect
from pipeline.gazetteer import build


MAP_PATHS = (
    ROOT / "public" / "data" / "regions.json",
    ROOT / "public" / "data" / "districts.json",
)


def stable_zone_ids() -> dict[str, str]:
    result: dict[str, str] = {}
    for path in MAP_PATHS:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for feature in payload.get("features", []):
            properties = feature.get("properties") or {}
            source_id = str(properties.get("id") or "")
            zone_id = str(properties.get("zone") or "")
            if source_id and zone_id:
                result[source_id] = zone_id
    return result


def restore_stable_zone_ids(zone_ids: dict[str, str]) -> None:
    for path in MAP_PATHS:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for feature in payload.get("features", []):
            properties = feature.get("properties") or {}
            stable_id = zone_ids.get(str(properties.get("id") or ""))
            if stable_id:
                properties["zone"] = stable_id
        path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )


def main() -> int:
    zone_ids = stable_zone_ids()
    with tempfile.TemporaryDirectory(prefix="radar-map-names-") as directory:
        connection = connect(Path(directory) / "gazetteer.db")
        try:
            stats = build(connection)
        finally:
            connection.close()
    restore_stable_zone_ids(zone_ids)
    print(
        "Финальные имена карты: "
        f"{stats['zones']} зон, {stats['orphans']} районов без родителя"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
