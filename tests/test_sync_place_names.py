import json
import sqlite3

from scripts.sync_place_names import sync_place_names


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE zones (
            id TEXT PRIMARY KEY,
            level TEXT NOT NULL,
            name_ru TEXT NOT NULL,
            source_id TEXT
        );
        CREATE TABLE zone_names (
            norm TEXT NOT NULL,
            zone_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            PRIMARY KEY (norm, zone_id)
        );
        CREATE TABLE events (id TEXT PRIMARY KEY, zone_id TEXT);
    """)
    connection.execute(
        "INSERT INTO zones VALUES (?, 'place', ?, ?)",
        ("ilskiy_severskiy", "Илский", "556951"),
    )
    connection.execute(
        "INSERT INTO zone_names VALUES (?, ?, 'primary')",
        ("илский", "ilskiy_severskiy"),
    )
    connection.execute(
        "INSERT INTO events VALUES ('event-1', 'ilskiy_severskiy')"
    )
    connection.commit()
    return connection


def test_sync_updates_name_without_changing_zone_or_event_links(tmp_path):
    places = tmp_path / "places.json"
    places.write_text(json.dumps({
        "fields": ["id", "name"],
        "rows": [["556951", "Ильский"]],
    }, ensure_ascii=False), encoding="utf-8")
    connection = database()

    stats = sync_place_names(connection, places)

    assert stats == {"canonical": 1, "zones": 1, "changed": 1}
    zone = connection.execute(
        "SELECT id, name_ru FROM zones WHERE source_id = '556951'"
    ).fetchone()
    assert dict(zone) == {"id": "ilskiy_severskiy", "name_ru": "Ильский"}
    assert connection.execute(
        "SELECT zone_id FROM events WHERE id = 'event-1'"
    ).fetchone()["zone_id"] == "ilskiy_severskiy"
    assert {
        tuple(row) for row in connection.execute(
            "SELECT norm, kind FROM zone_names WHERE zone_id = 'ilskiy_severskiy'"
        )
    } == {("илский", "variant"), ("ильский", "primary")}


def test_sync_is_idempotent(tmp_path):
    places = tmp_path / "places.json"
    places.write_text(json.dumps({
        "rows": [["556951", "Ильский"]],
    }, ensure_ascii=False), encoding="utf-8")
    connection = database()

    sync_place_names(connection, places)

    assert sync_place_names(connection, places)["changed"] == 0
