"""Конфигурация ingest-слоя: креденшелы Telegram и список источников."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

INGEST_DIR = Path(__file__).resolve().parent
DATA_DIR = INGEST_DIR / "data"
SESSION_DIR = DATA_DIR / "sessions"
RAW_DIR = DATA_DIR / "raw"

load_dotenv(INGEST_DIR / ".env")

SESSION_NAME = os.getenv("TG_SESSION_NAME", "radar")


@dataclass(frozen=True)
class Source:
    """Публичный канал-источник оповещений.

    tier:
      federal  — широкое покрытие, телеграфный формат, низкий шум;
      regional — узкая география (в основном Кубань), низкий шум;
      mixed    — оповещения вперемешку с новостями, нужен фильтр релевантности.
    """

    key: str
    username: str
    label: str
    tier: str = "regional"
    subscribers: int = 0


# Все 12 каналов папки "Радары", проверены через folders.py 27.07.2026.
SOURCES: list[Source] = [
    Source("lpr1_treugolnik", "lpr1_treugolnik", "Lpr 1", "federal", 924_432),
    Source("vrv_radar", "vrv_radar", "Радар ВРВ", "federal", 269_737),
    Source("locatorru", "locatorru", "Локатор России", "federal", 197_377),
    Source("radar_rvk", "radar_rvk", "Радар РВК", "federal", 129_580),
    Source("lpr1_krasnodar", "lpr1_Krasnodar_alarm", "Краснодарский край оповещения", "regional", 95_761),
    Source("pra_vo_zn", "PRA_VO_ZN", "ПРАВО ЗНАТЬ", "mixed", 49_602),
    Source("rschs_krd_adygea", "radar_rschs_krd_adygea", "ЧП Кубань и Адыгея", "mixed", 17_884),
    Source("kubanoidici", "kubanoidici24838", "Кубанский Вестник", "regional", 9_909),
    Source("krasnodarskiy_dozor", "krasnodarskiy_dozor_radar", "Краснодарский Дозор", "regional", 9_641),
    Source("montkub", "montkub", "Мониторинг Кубани", "regional", 5_679),
    Source("kubtrevoga93", "kubtrevoga93", "Оповещения Кубани", "regional", 5_669),
    Source("krasnodar_dozor", "krasnodar_dozor_radar", "Дозор Краснодара", "regional", 756),
]


def sources_from_env() -> list[Source]:
    """Переопределение списка через TG_SOURCES="key:username:label,..."."""
    raw = os.getenv("TG_SOURCES", "").strip()
    if not raw:
        return SOURCES

    parsed: list[Source] = []
    for chunk in raw.split(","):
        parts = [part.strip() for part in chunk.split(":")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        label = parts[2] if len(parts) > 2 and parts[2] else parts[1]
        parsed.append(Source(key=parts[0], username=parts[1].lstrip("@"), label=label))

    return parsed or SOURCES


def require_credentials() -> tuple[int, str]:
    """Читает api_id/api_hash из .env. Значения никогда не логируются."""
    api_id = os.getenv("TG_API_ID", "").strip()
    api_hash = os.getenv("TG_API_HASH", "").strip()

    if not api_id or not api_hash:
        sys.exit(
            "Не заданы TG_API_ID / TG_API_HASH.\n"
            f"Скопируйте {INGEST_DIR / '.env.example'} в {INGEST_DIR / '.env'} "
            "и впишите значения с https://my.telegram.org/apps"
        )

    if not api_id.isdigit():
        sys.exit("TG_API_ID должен быть числом.")

    return int(api_id), api_hash


def ensure_dirs() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)


def build_client():
    """Клиент Kurigram с сессией в ingest/data/sessions."""
    from pyrogram import Client

    api_id, api_hash = require_credentials()
    ensure_dirs()

    return Client(
        name=SESSION_NAME,
        api_id=api_id,
        api_hash=api_hash,
        workdir=str(SESSION_DIR),
        app_version="Radar Ingest 0.1",
        device_model="radar-ingest",
        system_version="macOS",
    )


def session_file() -> Path:
    return SESSION_DIR / f"{SESSION_NAME}.session"


def require_session() -> None:
    if not session_file().exists():
        sys.exit(
            "Сессия не найдена. Сначала авторизуйтесь вручную:\n"
            "  ingest/.venv/bin/python ingest/auth.py"
        )
