"""Перечисление папок аккаунта и каналов внутри них.

    ingest/.venv/bin/python ingest/folders.py            # все папки
    ingest/.venv/bin/python ingest/folders.py Радары     # одна папка + JSON

Результат по конкретной папке пишется в ingest/data/folder_<name>.json —
оттуда его берут скрипты выборки и выгрузки.
"""

from __future__ import annotations

import asyncio
import json
import sys

from config import DATA_DIR, build_client, ensure_dirs, require_session


def chat_row(chat) -> dict:
    return {
        "id": chat.id,
        "title": getattr(chat, "title", None) or getattr(chat, "first_name", None),
        "username": getattr(chat, "username", None),
        "type": str(chat.type),
        "members": getattr(chat, "members_count", None),
    }


async def main() -> None:
    require_session()
    ensure_dirs()
    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    client = build_client()

    async with client:
        folders = await client.get_folders()
        if not folders:
            print("Папок нет.")
            return

        for folder in folders:
            chats = list(folder.included_chats or [])
            mark = " <—" if wanted and folder.name == wanted else ""
            print(f"\n[{folder.id}] {folder.name}: {len(chats)} чатов{mark}")

            if wanted and folder.name != wanted:
                continue

            rows = [chat_row(chat) for chat in chats]
            for row in rows:
                handle = f"@{row['username']}" if row["username"] else "—"
                print(f"  {row['id']:>15}  {handle:<24} {row['title']}")

            if wanted:
                path = DATA_DIR / f"folder_{folder.name}.json"
                path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"\n  -> {path}")


if __name__ == "__main__":
    asyncio.run(main())
