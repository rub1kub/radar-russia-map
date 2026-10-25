"""Перенос гражданского исправления не меняет остальные классы и архив."""

import sqlite3

import pytest

from pipeline.db import connect
from pipeline.incremental import ensure_state, write_watermark
from scripts.install_civil_snapshot import install


def event(connection, eid, signal="danger", source=True):
    connection.execute(
        "INSERT INTO events(id,first_seen_at,last_seen_at,status,signal_type,threat_type,"
        "severity,confidence,source_count,zone_id,zone_path) "
        "VALUES (?,'2026-10-25T10:00:00Z','2026-10-25T10:00:00Z','active',?,'unknown',5,0.5,1,NULL,'[]')",
        (eid, signal))
    if source:
        connection.execute("INSERT INTO event_sources VALUES (?,1,'source','2026-10-25T10:00:00Z','first')", (eid,))
    connection.commit()


def databases(tmp_path):
    paths = [tmp_path / name for name in ("live.db", "shadow.db")]
    connections = [connect(path) for path in paths]
    for connection in connections:
        connection.execute("INSERT INTO raw_messages(id,source_key,message_id,posted_at,text) "
                           "VALUES (1,'source',1,'2026-10-25T10:00:00Z','test')")
        ensure_state(connection)
        write_watermark(connection, 1)
        connection.commit()
    return paths, connections


def test_transfer_preserves_other_classes_archive_and_cross_class_ids(tmp_path):
    paths, (live, shadow) = databases(tmp_path)
    event(live, "civil-old")
    event(live, "protected", "detect")
    event(live, "archive", source=False)
    event(live, "cross-class")
    event(shadow, "civil-new", "alarm")
    event(shadow, "protected")
    event(shadow, "cross-class", "detect")
    result = install(live, paths[1])
    actual = dict(live.execute("SELECT id,signal_type FROM events"))
    assert actual == {"civil-new": "alarm", "protected": "detect", "archive": "danger", "cross-class": "danger"}
    assert result["protected_conflicts"] == result["preserved_cross_class"] == 1
    assert result["raw_unchanged"] and result["protected_unchanged"]
    assert live.execute("SELECT source_key FROM history_day_sources").fetchone()[0] == "source"
    assert live.execute("PRAGMA foreign_key_check").fetchall() == []
    assert install(live, paths[1])["protected_unchanged"]
    assert dict(live.execute("SELECT id,signal_type FROM events")) == actual


def test_watermark_mismatch_rolls_back_without_changes(tmp_path):
    paths, (live, shadow) = databases(tmp_path)
    event(live, "old")
    write_watermark(shadow, 2)
    shadow.commit()
    with pytest.raises(ValueError, match="watermark"):
        install(live, paths[1])
    assert [row[0] for row in live.execute("SELECT id FROM events")] == ["old"]
    assert not live.in_transaction


def test_missing_snapshot_is_not_silently_created(tmp_path):
    paths, (live, shadow) = databases(tmp_path)
    missing = tmp_path / "missing.db"
    with pytest.raises(ValueError, match="не найден"):
        install(live, missing)
    assert not missing.exists()


def test_active_transaction_is_not_implicitly_committed(tmp_path):
    paths, (live, shadow) = databases(tmp_path)
    live.execute("INSERT INTO history_day_sources VALUES ('2026-10-25','pending')")
    with pytest.raises(ValueError, match="транзакцию"):
        install(live, paths[1])
    assert live.in_transaction
    live.rollback()


def test_failure_after_deleting_old_rows_restores_events_and_provenance(tmp_path):
    paths, (live, shadow) = databases(tmp_path)
    event(live, "old")
    event(shadow, "new")
    shadow.execute("INSERT INTO raw_messages(id,source_key,message_id,posted_at,text) "
                   "VALUES (2,'source',2,'2026-10-25T10:01:00Z','missing in live')")
    shadow.execute("UPDATE event_sources SET raw_message_id=2")
    shadow.commit()
    with pytest.raises(sqlite3.IntegrityError):
        install(live, paths[1])
    assert [row[0] for row in live.execute("SELECT id FROM events")] == ["old"]
    assert [row[0] for row in live.execute("SELECT event_id FROM event_sources")] == ["old"]
    assert live.execute("SELECT COUNT(*) FROM history_day_sources").fetchone()[0] == 0
    assert not live.in_transaction
