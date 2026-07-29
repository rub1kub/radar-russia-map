"""Фолбэк на регион источника не имеет права умирать молча.

    ingest/.venv/bin/python -m pytest tests/test_source_region.py -q

Севастопольские ленты месяц теряли сообщения без топонима только потому,
что ключа «sevastopol» не было в REGION_NAMES: build_fallback тихо
пропускал их, и «РАКЕТНАЯ ОПАСНОСТЬ!» из Севастополя не ложилась никуда.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))

import pytest

from config import sources_from_env
from pipeline.db import DB_PATH
from pipeline.source_region import REGION_NAMES, build_fallback


def test_every_config_region_is_mapped():
    """Каждый регион нефедерального канала обязан быть в REGION_NAMES.

    «other» — явное «фолбэка нет», это честно. Любой другой ключ без
    отображения — молчаливо потерянная привязка.
    """
    used = {
        source.region
        for source in sources_from_env()
        if source.tier != "federal" and source.region and source.region != "other"
    }
    missing = sorted(region for region in used if region not in REGION_NAMES)
    assert missing == [], f"регионы без отображения: {missing}"


def test_fallback_reaches_a_real_zone():
    """И само отображение обязано попадать в зону справочника."""
    if not DB_PATH.exists():
        pytest.skip("базы нет")
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    if connection.execute("SELECT COUNT(*) n FROM zones").fetchone()["n"] == 0:
        pytest.skip("справочник не построен")

    fallback = build_fallback(connection, sources_from_env())
    for source in sources_from_env():
        if source.tier == "federal" or (source.region or "other") == "other":
            continue
        assert source.key in fallback, (
            f"{source.key}: region={source.region!r} не дал зоны"
        )
