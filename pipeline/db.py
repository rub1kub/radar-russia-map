"""Хранилище конвейера.

SQLite повторяет целевую схему PostGIS из docs/TARGET_ARCHITECTURE.md:
сырые сообщения неизменяемы, все производное пересобирается из них.
Переезд на Postgres меняет только этот модуль.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "ingest" / "data" / "radar.db"

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Неизменяемый вход. Единственный источник истины.
CREATE TABLE IF NOT EXISTS raw_messages (
    id           INTEGER PRIMARY KEY,
    source_key   TEXT    NOT NULL,
    chat_id      INTEGER,
    message_id   INTEGER NOT NULL,
    posted_at    TEXT    NOT NULL,
    received_at  TEXT,
    text         TEXT    NOT NULL,
    views        INTEGER,
    UNIQUE (source_key, message_id)
);
CREATE INDEX IF NOT EXISTS idx_raw_posted ON raw_messages (posted_at);

-- Справочник зон: регион -> район -> населенный пункт.
CREATE TABLE IF NOT EXISTS zones (
    id          TEXT PRIMARY KEY,
    parent_id   TEXT REFERENCES zones (id),
    level       TEXT NOT NULL,          -- region | district | place
    name_ru     TEXT NOT NULL,
    lat         REAL,
    lon         REAL,
    population  INTEGER,
    feature_code TEXT,
    source_id   TEXT
);
CREATE INDEX IF NOT EXISTS idx_zones_parent ON zones (parent_id);
CREATE INDEX IF NOT EXISTS idx_zones_level  ON zones (level);

-- Нормализованные имена для сопоставления топонимов.
CREATE TABLE IF NOT EXISTS zone_names (
    norm     TEXT NOT NULL,
    zone_id  TEXT NOT NULL REFERENCES zones (id),
    kind     TEXT NOT NULL,             -- primary | variant
    PRIMARY KEY (norm, zone_id)
);
CREATE INDEX IF NOT EXISTS idx_zone_names_norm ON zone_names (norm);

-- Событие после слияния источников.
CREATE TABLE IF NOT EXISTS events (
    id            TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    resolved_at   TEXT,
    status        TEXT NOT NULL,        -- active | fading | resolved
    signal_type   TEXT NOT NULL,
    threat_type   TEXT NOT NULL,
    severity      INTEGER NOT NULL,
    confidence    REAL    NOT NULL,
    source_count  INTEGER NOT NULL,
    zone_id       TEXT REFERENCES zones (id),
    zone_path     TEXT,                 -- JSON-массив цепочки родителей
    lat           REAL,
    lon           REAL,
    accuracy_m    INTEGER,
    direction_deg INTEGER,
    target_count  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_seen   ON events (last_seen_at);
CREATE INDEX IF NOT EXISTS idx_events_zone   ON events (zone_id);
CREATE INDEX IF NOT EXISTS idx_events_status ON events (status);

-- Провенанс: какое сообщение какого источника дало это событие.
CREATE TABLE IF NOT EXISTS event_sources (
    event_id       TEXT NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    raw_message_id INTEGER NOT NULL REFERENCES raw_messages (id),
    source_key     TEXT NOT NULL,
    contributed_at TEXT NOT NULL,
    role           TEXT NOT NULL,       -- first | confirm | resolve
    PRIMARY KEY (event_id, raw_message_id)
);
CREATE INDEX IF NOT EXISTS idx_evsrc_source ON event_sources (source_key);
"""

# Производные таблицы, которые пересобираются целиком при переразборе.
DERIVED_TABLES = ("event_sources", "events")


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def reset_derived(connection: sqlite3.Connection) -> None:
    """Очистить только производное. raw_messages и zones не трогаются."""
    for table in DERIVED_TABLES:
        connection.execute(f"DELETE FROM {table}")
    connection.commit()


def counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = ("raw_messages", "zones", "zone_names", "events", "event_sources")
    return {
        table: connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for table in tables
    }
