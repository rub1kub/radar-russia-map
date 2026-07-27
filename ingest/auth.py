"""Интерактивная авторизация Telegram-сессии.

Запускать только вручную в своем терминале: скрипт спросит номер телефона,
код из Telegram и, если включена двухфакторная защита, облачный пароль.
Эти данные вводит владелец аккаунта, они никуда не логируются и не передаются.

    ingest/.venv/bin/python ingest/auth.py
"""

from __future__ import annotations

import asyncio
import sys

from config import build_client, session_file


async def main() -> int:
    if not sys.stdin.isatty():
        print(
            "auth.py требует интерактивный терминал: он запрашивает номер телефона "
            "и код подтверждения.\nЗапустите команду вручную:\n"
            "  ingest/.venv/bin/python ingest/auth.py",
            file=sys.stderr,
        )
        return 2

    path = session_file()
    if path.exists():
        print(f"Сессия уже существует: {path}")
        print("Проверить ее: ingest/.venv/bin/python ingest/whoami.py")
        print("Пересоздать: удалите файл сессии и запустите auth.py снова.")
        return 0

    client = build_client()

    print("Авторизация Telegram. Вводите данные только если запустили скрипт сами.\n")
    async with client:
        me = await client.get_me()
        print("\nГотово. Сессия сохранена:", path)
        print(f"Аккаунт: id={me.id}, username=@{me.username or '—'}")
        print("\nФайл сессии — это полный доступ к аккаунту. Не коммитьте и не пересылайте его.")
        print("Дальше: ingest/.venv/bin/python ingest/channels.py")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
