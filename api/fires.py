"""Очаги пожаров по спутниковым данным NASA FIRMS.

    GET /api/v1/fires -> {"points": [[lat, lon, frp], ...], "updated": iso}

Зачем это карте обстановки: последствия прилётов видны с орбиты раньше и
надёжнее, чем в лентах, — НПЗ и склады ГСМ горят сутками. Слой выключен по
умолчанию и живёт в «Слоях карты»: это фон для интересующихся, а не сигнал.

Данные — открытые суточные выгрузки VIIRS (без ключа): Европа и азиатская
часть России. Кэш получаса: спутник всё равно проходит дважды в сутки.
"""

from __future__ import annotations

import csv
import io
import threading
import time
import urllib.request

from fastapi import APIRouter

FEEDS = (
    "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Europe_24h.csv",
    "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Russia_Asia_24h.csv",
)

# Рамка нашей карты: западная граница Калининграда — Урал и чуть дальше.
LON_MIN, LON_MAX = 19.0, 65.0
LAT_MIN, LAT_MAX = 40.0, 72.0

# Порог мощности очага в мегаваттах. Слабые точки — сельхозпалы и трубы
# котельных; без порога слой превращается в веснушки по всей степи.
MIN_FRP = 5.0

CACHE_SEC = 30 * 60

router = APIRouter(prefix="/api/v1", tags=["fires"])

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "points": [], "updated": None}


def _fetch() -> list[list[float]]:
    points: list[list[float]] = []
    for url in FEEDS:
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                text = response.read().decode("utf-8", "replace")
        except Exception:
            continue
        for row in csv.DictReader(io.StringIO(text)):
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
                frp = float(row.get("frp") or 0)
            except (KeyError, ValueError):
                continue
            if not (LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX):
                continue
            if frp < MIN_FRP:
                continue
            points.append([round(lat, 3), round(lon, 3), round(frp, 1)])
    return points


@router.get("/fires")
def fires() -> dict:
    with _lock:
        if time.time() - _cache["at"] > CACHE_SEC:
            _cache["points"] = _fetch()
            _cache["at"] = time.time()
            _cache["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return {"points": _cache["points"], "updated": _cache["updated"]}
