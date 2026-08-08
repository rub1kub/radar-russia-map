"""Идемпотентная догрузка истории Telegram за заданный интервал.

Штатный сборщик должен быть остановлен на время запуска: Telegram-сессия
одна. Без ``--apply`` команда только показывает, сколько строк отсутствует.

    .venv/bin/python ingest/backfill.py \
        --since 2026-08-07T23:55:00+00:00 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyrogram.errors import FloodWait, RPCError

from config import build_client, ensure_dirs, require_session, sources_from_env
from pipeline.db import connect
from pipeline.timeutil import now_utc, parse_utc, to_utc, utc_iso
from poll import known_chat_id


async def read_window(
    client, peer, since: datetime, until: datetime | None = None,
) -> tuple[list, int]:
    """Прочитать сообщения внутри закрытого временного окна."""
    messages = []
    scanned = 0
    async for message in client.get_chat_history(peer, limit=0):
        posted_at = to_utc(message.date) if message.date else None
        if posted_at and posted_at < since:
            break
        scanned += 1
        if until and posted_at and posted_at > until:
            continue
        if (message.text or message.caption or "").strip():
            messages.append(message)
    return messages, scanned


async def scan_source(
    client, connection, source, since: datetime, until: datetime | None = None,
) -> tuple[list[tuple], int]:
    existing = {
        row["message_id"]
        for row in connection.execute(
            "SELECT message_id FROM raw_messages WHERE source_key = ? AND posted_at >= ?",
            (source.key, since.isoformat()),
        )
    }
    chat_id = known_chat_id(connection, source.key)
    peer = chat_id or source.username

    try:
        messages, scanned = await read_window(client, peer, since, until)
    except FloodWait:
        raise
    except RPCError:
        if not chat_id:
            raise
        messages, scanned = await read_window(client, source.username, since, until)

    received_at = now_utc().isoformat()
    rows = []
    for message in messages:
        if message.id in existing:
            continue
        rows.append((
            source.key,
            message.chat.id,
            message.id,
            utc_iso(message.date) if message.date else received_at,
            received_at,
            message.text or message.caption or "",
            message.views,
        ))
    return rows, scanned


async def run(
    since: datetime, until: datetime | None, apply: bool,
) -> dict[str, int]:
    require_session()
    ensure_dirs()
    connection = connect()
    connection.execute("PRAGMA busy_timeout = 5000")
    sources = sources_from_env()
    client = build_client()
    stats = {"sources": len(sources), "scanned": 0, "missing": 0,
             "inserted": 0, "failed": 0}

    async with client:
        for source in sources:
            try:
                while True:
                    try:
                        rows, scanned = await scan_source(
                            client, connection, source, since, until)
                        break
                    except FloodWait as wait:
                        await asyncio.sleep(wait.value + 1)
                stats["scanned"] += scanned
                stats["missing"] += len(rows)
                if rows:
                    print(f"{source.key}: отсутствует {len(rows)} из {scanned}")
                if apply and rows:
                    cursor = connection.executemany(
                        "INSERT OR IGNORE INTO raw_messages"
                        " (source_key, chat_id, message_id, posted_at, received_at, text, views)"
                        " VALUES (?,?,?,?,?,?,?)",
                        rows,
                    )
                    connection.commit()
                    stats["inserted"] += cursor.rowcount
            except RPCError as error:
                stats["failed"] += 1
                print(f"{source.key}: недоступен ({type(error).__name__})")
            await asyncio.sleep(0.35)

    connection.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Догрузка Telegram за временное окно")
    parser.add_argument("--since", required=True, help="начало окна в ISO 8601")
    parser.add_argument("--until", help="конец окна в ISO 8601; по умолчанию сейчас")
    parser.add_argument("--apply", action="store_true", help="записать отсутствующие строки")
    args = parser.parse_args()
    since = parse_utc(args.since)
    until = parse_utc(args.until) if args.until else None
    if until and until < since:
        parser.error("--until должен быть не раньше --since")
    stats = asyncio.run(run(since, until, args.apply))
    mode = "записано" if args.apply else "dry-run"
    print(f"{mode}: {stats}")


if __name__ == "__main__":
    main()
