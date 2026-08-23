"""Регрессии сборщика истории Telegram."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backfill
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


def test_tiered_cycle_polls_hot_always_and_cold_in_rotation(tmp_path):
    """Горячие — каждый круг, тихий хвост — каждый четвёртый, лежачие реже.

    258 источников одним аккаунтом — ~5 минут на круг и FloodWait в каждом
    цикле: быстрее опрашивать всё нельзя. Бюджет перераспределён: активные
    и официальные каналы опрашиваются каждый круг (задержка падает вдвое),
    молчаливый хвост — по очереди.
    """
    from poll import COLD_EVERY, DEAD_AFTER, DEAD_EVERY, cycle_sources

    connection = connect(tmp_path / "radar.db")
    ensure_state(connection)
    # «Активный» канал: свежие сообщения в корпусе.
    connection.execute(
        "INSERT INTO raw_messages (source_key, chat_id, message_id,"
        " posted_at, received_at, text, views) VALUES"
        " ('busy', -1, 1, datetime('now'), datetime('now'), 'тревога', 1)")
    connection.commit()

    sources = [
        Source("official1", "o1", "РСЧС", "official"),
        Source("busy", "b", "Активный", "regional"),
    ] + [Source(f"quiet{i}", f"q{i}", "Тихий", "regional")
         for i in range(8)]

    # Горячие в каждом круге; тихие — по четверти за круг, без пропусков
    # и повторов на полном обороте.
    seen_quiet: list[str] = []
    for cycle in range(COLD_EVERY):
        batch = [s.key for s in cycle_sources(connection, sources, {}, cycle)]
        assert "official1" in batch
        assert "busy" in batch
        seen_quiet += [key for key in batch if key.startswith("quiet")]
    assert sorted(seen_quiet) == sorted(f"quiet{i}" for i in range(8))

    # Лежачий канал уходит в редкий ярус и возвращается после успеха.
    failures = {"quiet0": DEAD_AFTER}
    polled = [cycle for cycle in range(DEAD_EVERY * 2)
              if "quiet0" in {s.key for s in cycle_sources(
                  connection, sources, failures, cycle)}]
    assert polled == [0, DEAD_EVERY]
    failures["quiet0"] = 0
    assert any("quiet0" in {s.key for s in cycle_sources(
        connection, sources, failures, cycle)} for cycle in range(1, COLD_EVERY + 1))


def test_live_handler_stores_pushed_message(tmp_path):
    """Пуш подписанного канала пишется в базу сразу, дубль с опросом не растёт."""
    from poll import live_handler

    connection = connect(tmp_path / "radar.db")
    ensure_state(connection)
    handler = live_handler(connection, {-1001: "source"})

    asyncio.run(handler(None, message(7, "Опасность по БПЛА")))
    stored = connection.execute(
        "SELECT source_key, message_id FROM raw_messages").fetchone()
    assert (stored["source_key"], stored["message_id"]) == ("source", 7)
    # Опрос-страховка того же сообщения дубля не создаёт.
    asyncio.run(handler(None, message(7, "Опасность по БПЛА")))
    assert connection.execute(
        "SELECT COUNT(*) FROM raw_messages").fetchone()[0] == 1
    # Чужой чат и пустой текст не пишутся.
    asyncio.run(handler(None, SimpleNamespace(
        id=8, chat=SimpleNamespace(id=-999), date=None,
        text="что-то", caption=None, views=0)))
    asyncio.run(handler(None, SimpleNamespace(
        id=9, chat=SimpleNamespace(id=-1001), date=None,
        text="", caption=None, views=0)))
    assert connection.execute(
        "SELECT COUNT(*) FROM raw_messages").fetchone()[0] == 1


def test_subscribed_channels_leave_the_hot_tier(tmp_path):
    """Подписанный канал доставляется пушем — из горячего опроса он уходит."""
    from poll import cycle_sources

    connection = connect(tmp_path / "radar.db")
    ensure_state(connection)
    connection.execute(
        "INSERT INTO raw_messages (source_key, chat_id, message_id,"
        " posted_at, received_at, text, views) VALUES"
        " ('busy', -1, 1, datetime('now'), datetime('now'), 'тревога', 1)")
    connection.commit()

    sources = [Source("official1", "o1", "РСЧС", "official"),
               Source("busy", "b", "Активный", "regional")]
    # Без подписки оба в горячем ярусе — в каждом круге.
    for cycle in range(3):
        batch = {s.key for s in cycle_sources(connection, sources, {}, cycle)}
        assert batch == {"official1", "busy"}
    # С подпиской — в редкой страховке, не в каждом круге.
    polled = [cycle for cycle in range(4)
              if "busy" in {s.key for s in cycle_sources(
                  connection, sources, {}, cycle, {"busy", "official1"})}]
    assert len(polled) == 1


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


# --- Защита от запуска догрузки при живом сборщике ---
#
# Telegram-сессия одна на аккаунт: второй клиент разрывает первый, и сбор
# встаёт молча. Признаков живого сборщика три, и каждый порознь ошибается —
# проверяем, что вместе они срабатывают и что честный простой не блокируют.

def corpus(tmp_path, received_at: str | None):
    connection = connect(tmp_path / "radar.db")
    if received_at:
        connection.execute(
            "INSERT INTO raw_messages"
            " (source_key, chat_id, message_id, posted_at, received_at, text)"
            " VALUES (?,?,?,?,?,?)",
            ("astra", -100, 1, received_at, received_at, "Опасность по БПЛА"))
        connection.commit()
    return connection


def test_fresh_corpus_row_means_collector_alive(tmp_path, monkeypatch):
    just_now = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    connection = corpus(tmp_path, just_now)
    monkeypatch.setattr(backfill, "systemd_poll_active", lambda: False)
    monkeypatch.setattr(backfill, "poll_processes", list)

    assert backfill.fresh_corpus_row(connection) == just_now
    assert "сборщик жив" in backfill.collector_reason(connection)


def test_stale_corpus_is_not_a_running_collector(tmp_path, monkeypatch):
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    connection = corpus(tmp_path, long_ago)
    monkeypatch.setattr(backfill, "systemd_poll_active", lambda: False)
    monkeypatch.setattr(backfill, "poll_processes", list)

    assert backfill.fresh_corpus_row(connection) is None
    assert backfill.collector_reason(connection) is None


def test_service_wins_over_quiet_corpus(tmp_path, monkeypatch):
    """Тихий час на каналах не повод считать, что сборщик остановлен."""
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    connection = corpus(tmp_path, long_ago)
    monkeypatch.setattr(backfill, "systemd_poll_active", lambda: True)
    monkeypatch.setattr(backfill, "poll_processes", list)

    assert backfill.collector_reason(connection) == "служба tihoenebo-poll активна"


def test_gap_window_steps_back_from_newest_message(tmp_path):
    """Сборщик мог оборваться посреди прохода — окно берётся с нахлёстом."""
    newest = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    connection = corpus(tmp_path, newest.isoformat())

    assert backfill.gap_start(connection) == newest - backfill.GAP_OVERLAP


def test_gap_needs_a_corpus_to_count_from(tmp_path):
    connection = corpus(tmp_path, None)
    with pytest.raises(SystemExit):
        backfill.gap_start(connection)
