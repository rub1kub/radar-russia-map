"""Архив не исчезает вместе с исходными текстами."""

from api import server
from pipeline.db import connect
from pipeline.history import remember_day_sources
from pipeline.rebuild import rebuild
from pipeline.retention import purge


def archived_day(connection, eid="archived", sources=5, day="2026-07-27"):
    at = f"{day}T10:00:00+00:00"
    connection.execute(
        "INSERT OR IGNORE INTO zones(id,level,name_ru) VALUES ('region','region','Тестовая область')")
    connection.execute(
        "INSERT INTO events(id,first_seen_at,last_seen_at,status,signal_type,threat_type,"
        "severity,confidence,source_count,zone_id,zone_path) VALUES (?,?,?,'resolved','danger',"
        "'unknown',5,0.8,?,'region','[\"region\"]')", (eid, at, at, sources))
    for index in range(sources):
        cursor = connection.execute(
            "INSERT INTO raw_messages(source_key,message_id,posted_at,text) VALUES (?,?,?,?)",
            (f"source{index}", len(eid) * 10 + index, at, "Архивное сообщение"))
        connection.execute(
            "INSERT INTO event_sources VALUES (?,?,?,?, 'first')",
            (eid, cursor.lastrowid, f"source{index}", at))
    connection.commit()


def test_purge_and_rebuild_preserve_published_day(tmp_path, monkeypatch):
    path = tmp_path / "history.db"
    connection = connect(path)
    archived_day(connection)
    monkeypatch.setattr(server, "DB_PATH", path)
    before = server.history_days(limit=60)
    assert [d["day"] for d in before["days"]] == ["2026-07-27"]

    purge(connection, "2026-08-01T00:00:00+00:00")
    assert connection.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0] == 0
    assert server.history_days(limit=60) == before

    stats = rebuild(connection)
    assert stats["archived_events_preserved"] == 1
    assert server.history_days(limit=60) == before
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_coverage_does_not_duplicate_sources_or_publish_partial_days(tmp_path, monkeypatch):
    path = tmp_path / "history.db"
    connection = connect(path)
    archived_day(connection, sources=4)
    monkeypatch.setattr(server, "DB_PATH", path)
    assert remember_day_sources(connection) == 4
    assert remember_day_sources(connection) == 0
    connection.commit()
    assert server.history_days(limit=60)["days"] == []

    # Пятый новый источник после очистки суммируется с архивным охватом,
    # а четыре прежних не засчитываются второй раз.
    purge(connection, "2026-08-01T00:00:00+00:00")
    connection.execute("INSERT INTO history_day_sources VALUES ('2026-07-27','source4')")
    connection.commit()
    day = server.history_days(limit=60)["days"][0]
    assert day["sources"] == 5
    assert day["events"] == 1
