"""Постоянные соединения не удерживают процессы прокси бессрочно."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastapi import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import server


class Socket:
    def __init__(self, disconnect=False):
        self.accepted = False
        self.messages = []
        self.closed = None
        self.disconnect = disconnect

    async def accept(self):
        self.accepted = True

    async def send_json(self, value):
        if self.disconnect:
            raise WebSocketDisconnect()
        self.messages.append(value)

    async def close(self, code):
        self.closed = code


def test_legacy_stream_delivers_state_and_releases_proxy_worker(monkeypatch):
    monkeypatch.setattr(server, "state_snapshot", lambda: {
        "last_message_at": "2026-10-25T00:00:00+00:00", "active_events": 2,
    })
    socket = Socket()

    asyncio.run(server.stream(socket))

    assert socket.accepted
    assert len(socket.messages) == 1
    assert socket.messages[0]["type"] == "state"
    assert socket.closed == 1001


def test_stream_releases_disconnected_client(monkeypatch):
    monkeypatch.setattr(server, "state_snapshot", lambda: {
        "last_message_at": "2026-10-25T00:00:00+00:00", "active_events": 2,
    })
    socket = Socket(disconnect=True)

    asyncio.run(server.stream(socket))

    assert socket.closed == 1001


def test_stream_releases_connection_when_snapshot_fails(monkeypatch):
    def fail():
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr(server, "state_snapshot", fail)
    socket = Socket()

    asyncio.run(server.stream(socket))

    assert socket.messages == []
    assert socket.closed == 1001
