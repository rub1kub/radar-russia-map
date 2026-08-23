"""Подписка на источники и раскладка их по папке.

    ingest/.venv/bin/python ingest/subscribe.py --folder Радары

Вступление в сотню каналов подряд Telegram воспринимает как автоматизацию
и отвечает ограничением на аккаунт, поэтому между вступлениями стоит пауза,
а FloodWait честно пережидается. Прогон можно прерывать и повторять: уже
вступленные каналы пропускаются.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyrogram.errors import FloodWait, RPCError, UserAlreadyParticipant

from config import build_client, require_session, sources_from_env

# Пауза между вступлениями. Меньше — риск ограничения аккаунта.
JOIN_DELAY_SEC = 6.0


async def current_state(client, folder_name: str):
    """Что уже прочитано аккаунтом и что уже лежит в папке."""
    joined: dict[str, int] = {}
    async for dialog in client.get_dialogs():
        username = getattr(dialog.chat, "username", None)
        if username:
            joined[username.lower()] = dialog.chat.id

    folders = await client.get_folders()
    folder = next((item for item in folders if item.name == folder_name), None)
    included = list(folder.included_chats or []) if folder else []
    in_folder = {
        getattr(chat, "username", "").lower()
        for chat in included
        if getattr(chat, "username", None)
    }
    # Идентификаторы всего, что уже лежит в папке, — включая чаты, которых
    # нет в нашем списке источников. Без них состав пришлось бы собирать
    # заново, а всё чужое из папки исчезло бы.
    folder_ids = [chat.id for chat in included if getattr(chat, "id", None)]

    return joined, folder, in_folder, folder_ids


async def main() -> None:
    parser = argparse.ArgumentParser(description="Подписка на каналы-источники")
    parser.add_argument("--folder", default="Радары")
    parser.add_argument("--limit", type=int, default=None,
                        help="ограничить число вступлений за прогон")
    parser.add_argument("--folder-only", action="store_true",
                        help="ничего не делать, только показать состав папки")
    args = parser.parse_args()

    require_session()
    sources = sources_from_env()
    client = build_client()

    async with client:
        joined, folder, in_folder, folder_ids = await current_state(client, args.folder)
        todo = [s for s in sources if s.username.lower() not in joined]
        # Горячие первыми: официальные ленты и самые активные каналы по
        # корпусу. При вступлении порциями (--limit) именно они получают
        # мгновенную пуш-доставку в первую очередь.
        from poll import HOT_TIERS, active_keys
        from pipeline.db import connect as db_connect
        hot = active_keys(db_connect())
        todo.sort(key=lambda s: (s.tier not in HOT_TIERS, s.key not in hot))

        print(f"источников {len(sources)}, уже подписан на {len(sources) - len(todo)}")
        print(f"вступить нужно в {len(todo)}"
              + (f", за этот прогон не больше {args.limit}" if args.limit else ""))

        if args.folder_only:
            todo = []
        elif args.limit:
            todo = todo[: args.limit]

        done, failed = 0, []
        for index, source in enumerate(todo, 1):
            while True:
                try:
                    result = await client.join_chat(source.username)
                    # join_chat возвращает объект результата, а не чат:
                    # у разных версий это либо .chat, либо вовсе без id.
                    peer = getattr(result, "id", None) or getattr(
                        getattr(result, "chat", None), "id", None
                    )
                    if peer is None:
                        peer = (await client.get_chat(source.username)).id
                    joined[source.username.lower()] = peer
                    done += 1
                    print(f"  [{index}/{len(todo)}] + @{source.username}", flush=True)
                    break
                except UserAlreadyParticipant:
                    break
                except FloodWait as wait:
                    print(f"      FloodWait {wait.value} c, жду", flush=True)
                    await asyncio.sleep(wait.value + 2)
                except RPCError as error:
                    failed.append((source.username, type(error).__name__))
                    print(f"  [{index}/{len(todo)}] ! @{source.username}: "
                          f"{type(error).__name__}", flush=True)
                    break
            await asyncio.sleep(JOIN_DELAY_SEC)

        print(f"\nвступил: {done}, не удалось: {len(failed)}")

        # Папку скрипт больше не трогает.
        #
        # Здесь трижды был потерян состав папки, и каждый раз по новой
        # причине. Сначала include_chat() — он состав не дополняет, а
        # перезаписывает, и вызов по одному каналу оставлял ровно последний.
        # Потом полный состав собирался только из наших источников, и всё,
        # что человек положил в папку сам, исчезало. А в третий раз
        # выяснилось, что folder.edit(included_chats=[id, ...]) молча
        # выбрасывает те id, для которых в кеше сессии нет хеша доступа: из
        # сорока просимых доходило восемнадцать. Плюс сам Telegram режет
        # состав по своему пределу — из ста заданных сохранил восемьдесят два.
        #
        # Разбор конвейера папкой не пользуется вовсе: ingest/poll.py ходит по
        # именам каналов из config.py, и членство ему не нужно. Папка — это
        # удобство человека, и раскладывать её должен человек.
        print(f"\nпапка «{args.folder}» не изменялась: сейчас в ней "
              f"{len(folder_ids)} чатов.")
        print("Раскладка по папкам делается вручную — скрипт трижды терял "
              "её состав и больше в неё не пишет.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nостановлено, повторный запуск продолжит с места остановки")
