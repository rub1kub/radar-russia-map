"""Live-приемник: слушает источники и дописывает новые сообщения в JSONL.

    ingest/.venv/bin/python ingest/listen.py

Останов — Ctrl+C. Это заготовка ingest-петли: сюда позже встанет вызов
геопарсера и обновление состояния карты.
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from pyrogram import filters
from pyrogram.errors import RPCError
from pyrogram.handlers import MessageHandler

from config import RAW_DIR, build_client, ensure_dirs, require_session, sources_from_env

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.db import connect  # noqa: E402
from pipeline.timeutil import now_utc, utc_iso  # noqa: E402


def append(path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def store(connection, source_key: str, chat_id: int, message) -> None:
    """Сырое сообщение в raw_messages. Производное пересобирает rebuild.py."""
    connection.execute(
        "INSERT OR IGNORE INTO raw_messages"
        " (source_key, chat_id, message_id, posted_at, received_at, text, views)"
        " VALUES (?,?,?,?,?,?,?)",
        (source_key, chat_id, message.id,
         utc_iso(message.date) if message.date else now_utc().isoformat(),
         now_utc().isoformat(),
         message.text or message.caption or "", message.views),
    )
    connection.commit()


async def main() -> None:
    require_session()
    ensure_dirs()
    client = build_client()
    sources = sources_from_env()

    async with client:
        chat_ids: dict[int, str] = {}
        for source in sources:
            try:
                chat = await client.get_chat(source.username)
            except RPCError as error:
                print(f"{source.key}: недоступен ({type(error).__name__}), пропускаю")
                continue
            chat_ids[chat.id] = source.key
            print(f"{source.key}: слушаю {chat.title} (id={chat.id})")

        if not chat_ids:
            print("Ни один источник не доступен — слушать нечего.")
            return

        live_path = RAW_DIR / "live.jsonl"
        connection = connect()

        async def on_message(_, message) -> None:
            source_key = chat_ids.get(message.chat.id, str(message.chat.id))
            text = message.text or message.caption or ""
            if not text:
                return

            record = {
                "source": source_key,
                "message_id": message.id,
                "date": utc_iso(message.date) if message.date else None,
                "received_at": now_utc().isoformat(),
                "text": text,
            }
            append(live_path, record)
            store(connection, source_key, message.chat.id, message)

            stamp = message.date.strftime("%H:%M:%S") if message.date else "--:--:--"
            print(f"[{stamp}] {source_key}: {' '.join(text.split())[:140]}")

        client.add_handler(MessageHandler(on_message, filters.chat(list(chat_ids))))

        print(f"\nПишу в {live_path}. Ctrl+C — стоп.\n")
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        print("\nОстановлено.")


async def supervise() -> None:
    """Перезапуск при обрыве.

    Один аккаунт Telegram — единственная точка входа данных. Падение по сети
    или FloodWait без перезапуска означает молча остановленный сбор.
    """
    delay = 5
    while True:
        try:
            await main()
            return
        except (KeyboardInterrupt, asyncio.CancelledError):
            return
        except Exception as error:  # noqa: BLE001 — причина не важна, важен перезапуск
            print(f"\nсбор оборвался: {type(error).__name__}: {error}")
            print(f"перезапуск через {delay} c")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


if __name__ == "__main__":
    try:
        asyncio.run(supervise())
    except KeyboardInterrupt:
        print("\nОстановлено.")
