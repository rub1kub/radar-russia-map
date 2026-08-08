"""Регрессии сборщика истории Telegram."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Source
from backfill import read_window
from pipeline.db import connect
from poll import ensure_state, last_seen, poll_source, remember, remember_chat_id


class HistoryClient:
    def __init__(self, messages):
        self.messages = messages
        self.limits = []

    async def get_chat_history(self, _peer, limit):
        self.limits.append(limit)
        for message in self.messages:
            yield message


def message(message_id: int, text: str = "Опасность по БПЛА"):
    return SimpleNamespace(
        id=message_id,
        chat=SimpleNamespace(id=-1001),
        date=datetime(2026, 8, 8, tzinfo=timezone.utc),
        text=text,
        caption=None,
        views=1,
    )


def test_catch_up_reads_past_old_sixty_message_limit(tmp_path):
    connection = connect(tmp_path / "radar.db")
    ensure_state(connection)
    remember(connection, "source", 100)
    remember_chat_id(connection, "source", -1001)
    connection.commit()

    client = HistoryClient([message(value) for value in range(200, 99, -1)])
    source = Source("source", "source_name", "Источник")

    added = asyncio.run(poll_source(client, connection, source))

    assert client.limits == [0]
    assert added == 100
    assert connection.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0] == 100
    assert last_seen(connection, "source") == 200


def test_media_only_message_still_advances_cursor(tmp_path):
    connection = connect(tmp_path / "radar.db")
    ensure_state(connection)
    remember(connection, "source", 100)
    remember_chat_id(connection, "source", -1001)
    connection.commit()

    client = HistoryClient([message(101, "")])
    source = Source("source", "source_name", "Источник")

    assert asyncio.run(poll_source(client, connection, source)) == 0
    assert last_seen(connection, "source") == 101


def test_backfill_respects_both_window_bounds():
    messages = [
        message(3),
        message(2),
        message(1),
    ]
    messages[0].date = datetime(2026, 8, 8, 1, 10, tzinfo=timezone.utc)
    messages[1].date = datetime(2026, 8, 8, 1, 5, tzinfo=timezone.utc)
    messages[2].date = datetime(2026, 8, 8, 0, 59, tzinfo=timezone.utc)
    client = HistoryClient(messages)

    rows, scanned = asyncio.run(read_window(
        client,
        -1001,
        datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 8, 1, 8, tzinfo=timezone.utc),
    ))

    assert [row.id for row in rows] == [2]
    assert scanned == 2
