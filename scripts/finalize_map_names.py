"""Дополнить полигоны каноническими именами через ОКТМО без живой БД.

`pipeline.gazetteer` умеет надёжно опознавать район по геометрии и составу
населённых пунктов, но обычный запуск пересобирает таблицы зон. Для подготовки
статических JSON ему передаётся одноразовая SQLite-база: итоговые имена и
родители попадают в `public/data`, а пользовательские события не затрагиваются.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pipeline.db import ROOT, connect
from pipeline.gazetteer import build


def remove_internal_flags() -> None:
    path = ROOT / "public" / "data" / "districts.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for feature in payload.get("features", []):
        properties = feature.get("properties") or {}
        properties.pop("nameLocked", None)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="radar-map-names-") as directory:
        connection = connect(Path(directory) / "gazetteer.db")
        try:
            stats = build(connection)
        finally:
            connection.close()
    remove_internal_flags()
    print(
        "Финальные имена карты: "
        f"{stats['zones']} зон, {stats['orphans']} районов без родителя"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
