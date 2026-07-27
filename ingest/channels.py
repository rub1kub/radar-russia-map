"""Проверка источников: существует ли канал, доступен ли он и что в нем сейчас.

    ingest/.venv/bin/python ingest/channels.py
"""

from __future__ import annotations

import asyncio

from pyrogram.errors import RPCError

from config import build_client, require_session, sources_from_env


def preview(text: str | None, limit: int = 110) -> str:
    if not text:
        return "—"
    flat = " ".join(text.split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


async def main() -> None:
    require_session()
    client = build_client()
    sources = sources_from_env()

    async with client:
        for source in sources:
            print(f"\n=== {source.key}  (@{source.username} — {source.label})")
            try:
                chat = await client.get_chat(source.username)
            except RPCError as error:
                print(f"  НЕДОСТУПЕН: {type(error).__name__}: {error}")
                continue

            print(f"  id:        {chat.id}")
            print(f"  title:     {chat.title}")
            print(f"  type:      {chat.type}")
            print(f"  members:   {chat.members_count if chat.members_count is not None else '—'}")

            async for message in client.get_chat_history(chat.id, limit=3):
                stamp = message.date.strftime("%d.%m.%Y %H:%M") if message.date else "—"
                print(f"  [{message.id}] {stamp}  {preview(message.text or message.caption)}")


if __name__ == "__main__":
    asyncio.run(main())
