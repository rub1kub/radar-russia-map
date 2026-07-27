"""Проверка сессии: подключается и печатает минимум об аккаунте."""

from __future__ import annotations

import asyncio

from config import build_client, require_session


async def main() -> None:
    require_session()
    client = build_client()

    async with client:
        me = await client.get_me()
        print("Сессия рабочая.")
        print(f"  id:        {me.id}")
        print(f"  username:  @{me.username}" if me.username else "  username:  —")
        print(f"  premium:   {'да' if me.is_premium else 'нет'}")


if __name__ == "__main__":
    asyncio.run(main())
