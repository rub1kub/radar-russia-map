"""Выгрузка истории источников в JSONL — сырой материал для разработки парсера.

    ingest/.venv/bin/python ingest/dump_history.py --limit 500

Результат: ingest/data/raw/<key>.jsonl, по одному сообщению на строку.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from pyrogram.errors import FloodWait, RPCError

from config import RAW_DIR, build_client, ensure_dirs, require_session, sources_from_env


def serialize(message, source_key: str) -> dict:
    """Плоская запись сообщения: только то, что нужно парсеру и провенансу."""
    return {
        "source": source_key,
        "message_id": message.id,
        "date": message.date.isoformat() if message.date else None,
        "edit_date": message.edit_date.isoformat() if message.edit_date else None,
        "text": message.text or message.caption or "",
        "views": message.views,
        "forwards": message.forwards,
        "media": str(message.media) if message.media else None,
        "link": f"https://t.me/{message.chat.username}/{message.id}"
        if getattr(message.chat, "username", None)
        else None,
    }


async def dump_source(client, source, limit: int) -> int:
    path = RAW_DIR / f"{source.key}.jsonl"
    written = 0

    try:
        chat = await client.get_chat(source.username)
    except RPCError as error:
        print(f"  {source.key}: недоступен ({type(error).__name__}: {error})")
        return 0

    with path.open("w", encoding="utf-8") as handle:
        while True:
            try:
                async for message in client.get_chat_history(chat.id, limit=limit):
                    if not (message.text or message.caption):
                        continue
                    handle.write(json.dumps(serialize(message, source.key), ensure_ascii=False) + "\n")
                    written += 1
                break
            except FloodWait as wait:
                print(f"  {source.key}: FloodWait {wait.value} c, жду…")
                await asyncio.sleep(wait.value + 1)

    print(f"  {source.key}: {written} сообщений -> {path}")
    return written


async def main() -> None:
    parser = argparse.ArgumentParser(description="Выгрузка истории каналов-источников")
    parser.add_argument("--limit", type=int, default=500, help="сообщений на канал (по умолчанию 500)")
    args = parser.parse_args()

    require_session()
    ensure_dirs()
    client = build_client()
    sources = sources_from_env()

    async with client:
        print(f"Выгружаю до {args.limit} сообщений из {len(sources)} источников…")
        total = 0
        for source in sources:
            total += await dump_source(client, source, args.limit)
        print(f"\nИтого: {total} сообщений в {RAW_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
