"""Атомарный перенос проверенного снимка при остановленном pipeline.

Снимок должен быть догнан до watermark живой БД. Сырые сообщения,
маршруты, архив без исходников и события остальных классов не меняются.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from pipeline.history import remember_day_sources
from pipeline.incremental import WATERMARK_KEY

CIVIL = "('alarm','danger','infra')"


def _protected_digest(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for sql in (
        f"SELECT * FROM events WHERE signal_type NOT IN {CIVIL} ORDER BY id",
        f"SELECT s.* FROM event_sources s JOIN events e ON e.id=s.event_id "
        f"WHERE e.signal_type NOT IN {CIVIL} ORDER BY s.event_id,s.raw_message_id",
    ):
        for row in connection.execute(sql):
            digest.update(json.dumps(tuple(row), ensure_ascii=True).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def install(connection: sqlite3.Connection, snapshot: Path) -> dict:
    """Вызов не запускает службы, не создаёт backup и не догоняет сырьё."""
    if connection.in_transaction:
        raise ValueError("перед переносом завершите текущую транзакцию")
    if not snapshot.is_file():
        raise ValueError("серверный снимок не найден")
    connection.execute("ATTACH DATABASE ? AS corrected", (str(snapshot.resolve()),))
    try:
        connection.execute("BEGIN IMMEDIATE")
        watermarks = [connection.execute(
            f"SELECT value FROM {schema}.pipeline_state WHERE key=?", (WATERMARK_KEY,)
        ).fetchone() for schema in ("main", "corrected")]
        if any(row is None for row in watermarks) or int(watermarks[0][0]) != int(watermarks[1][0]):
            raise ValueError("watermark снимка и живой базы различается")
        if connection.execute("PRAGMA corrected.foreign_key_check").fetchone():
            raise ValueError("нарушены FK снимка")
        protected = _protected_digest(connection)
        raw_before = tuple(connection.execute(
            "SELECT COUNT(*),MAX(id) FROM raw_messages").fetchone())
        conflicts = connection.execute(
            f"SELECT COUNT(*) FROM corrected.events c JOIN main.events e ON e.id=c.id "
            f"WHERE c.signal_type IN {CIVIL} AND e.signal_type NOT IN {CIVIL}"
        ).fetchone()[0]
        cross_class = connection.execute(
            f"SELECT COUNT(*) FROM main.events e JOIN corrected.events c ON c.id=e.id "
            f"WHERE e.signal_type IN {CIVIL} AND c.signal_type NOT IN {CIVIL}"
        ).fetchone()[0]
        remember_day_sources(connection)
        connection.execute("INSERT OR IGNORE INTO main.history_day_sources "
                           "SELECT day,source_key FROM corrected.history_day_sources")
        # Межклассовые совпадения и архив без исходников оставляем как есть.
        connection.execute(
            f"CREATE TEMP TABLE civil_replace AS SELECT e.id FROM main.events e "
            f"WHERE e.signal_type IN {CIVIL} "
            "AND EXISTS (SELECT 1 FROM main.event_sources s WHERE s.event_id=e.id) "
            f"AND NOT EXISTS (SELECT 1 FROM corrected.events c WHERE c.id=e.id AND c.signal_type NOT IN {CIVIL})")
        connection.execute(
            f"CREATE TEMP TABLE civil_incoming AS SELECT c.id FROM corrected.events c "
            f"WHERE c.signal_type IN {CIVIL} AND NOT EXISTS ("
            "SELECT 1 FROM main.events e WHERE e.id=c.id "
            "AND e.id NOT IN (SELECT id FROM civil_replace))")
        removed = connection.execute("SELECT COUNT(*) FROM civil_replace").fetchone()[0]
        incoming = connection.execute("SELECT COUNT(*) FROM civil_incoming").fetchone()[0]
        connection.execute("DELETE FROM main.event_sources WHERE event_id IN (SELECT id FROM civil_replace)")
        connection.execute("DELETE FROM main.events WHERE id IN (SELECT id FROM civil_replace)")
        columns = [row["name"] for row in connection.execute("PRAGMA main.table_info(events)")]
        names = ",".join(columns)
        connection.execute(f"INSERT INTO main.events ({names}) SELECT {names} "
                           "FROM corrected.events WHERE id IN (SELECT id FROM civil_incoming)")
        connection.execute("INSERT INTO main.event_sources SELECT s.* FROM corrected.event_sources s "
                           "WHERE s.event_id IN (SELECT id FROM civil_incoming)")
        if connection.execute("PRAGMA main.foreign_key_check").fetchone():
            raise ValueError("нарушены FK после переноса")
        if _protected_digest(connection) != protected:
            raise ValueError("изменились защищённые события или провенанс")
        raw_after = tuple(connection.execute("SELECT COUNT(*),MAX(id) FROM raw_messages").fetchone())
        if raw_after != raw_before:
            raise ValueError("изменились сырые сообщения")
        connection.execute("DROP TABLE civil_incoming")
        connection.execute("DROP TABLE civil_replace")
        connection.commit()
        return {"replaced": removed, "inserted": incoming, "protected_conflicts": conflicts,
                "preserved_cross_class": cross_class, "raw_unchanged": True,
                "protected_unchanged": True, "watermark": int(watermarks[0][0])}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("DETACH DATABASE corrected")
