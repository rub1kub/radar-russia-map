"""Опрос истории всех источников.

    ingest/.venv/bin/python ingest/poll.py --loop 45

Зачем отдельно от listen.py: push от Telegram приходит только по каналам,
на которые аккаунт подписан. Региональные ленты, найденные разведкой, никто
не читает подпиской, и массово вступать в семь десятков каналов ради этого
не стоит — легко упереться во флуд-лимиты и это заметное действие аккаунта.
Опрос истории работает для любого публичного канала без вступления.

listen.py остаётся для подписанных каналов: там задержка секунды, здесь —
интервал опроса.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyrogram.errors import FloodWait, RPCError
from pyrogram.handlers import MessageHandler

from config import build_client, ensure_dirs, require_session, sources_from_env
from pipeline.db import connect
from pipeline.timeutil import now_utc, utc_iso

# Сколько сообщений тянуть при первом знакомстве с каналом.
FIRST_RUN_LIMIT = 40
# После первого знакомства читаем историю до сохранённого message_id. Нулевой
# limit у Pyrogram означает «без лимита»; цикл ниже сам остановится на seen.
# Фиксированное окно теряло старую часть сообщений после долгого простоя.
CATCH_UP_LIMIT = 0


def ensure_state(connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS poll_state ("
        " source_key TEXT PRIMARY KEY, last_message_id INTEGER NOT NULL)"
    )
    # Числовой идентификатор канала. Имя владелец меняет когда угодно, и
    # опрос по имени в этот момент просто перестаёт что-либо находить —
    # молча, без ошибки, как будто в канале стало тихо. Идентификатор не
    # меняется никогда, поэтому имя нужно ровно один раз: чтобы его узнать.
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(poll_state)")}
    if "chat_id" not in columns:
        connection.execute("ALTER TABLE poll_state ADD COLUMN chat_id INTEGER")
    connection.commit()


def known_chat_id(connection, source_key: str) -> int | None:
    """Идентификатор канала, если он уже известен."""
    row = connection.execute(
        "SELECT chat_id FROM poll_state WHERE source_key = ?", (source_key,)
    ).fetchone()
    if row and row["chat_id"]:
        return row["chat_id"]
    # Канал мог собираться раньше — идентификатор лежит в самих сообщениях.
    row = connection.execute(
        "SELECT chat_id FROM raw_messages WHERE source_key = ? ORDER BY id DESC LIMIT 1",
        (source_key,),
    ).fetchone()
    return row["chat_id"] if row and row["chat_id"] else None


def remember_chat_id(connection, source_key: str, chat_id: int) -> None:
    connection.execute(
        "INSERT INTO poll_state (source_key, last_message_id, chat_id) VALUES (?, 0, ?)"
        " ON CONFLICT(source_key) DO UPDATE SET chat_id = excluded.chat_id",
        (source_key, chat_id),
    )


def last_seen(connection, source_key: str) -> int:
    row = connection.execute(
        "SELECT last_message_id FROM poll_state WHERE source_key = ?", (source_key,)
    ).fetchone()
    if row:
        return row["last_message_id"]
    # Канал мог собираться через listen.py — не начинаем с нуля.
    row = connection.execute(
        "SELECT MAX(message_id) AS m FROM raw_messages WHERE source_key = ?", (source_key,)
    ).fetchone()
    return row["m"] or 0


def remember(connection, source_key: str, message_id: int) -> None:
    connection.execute(
        "INSERT INTO poll_state (source_key, last_message_id) VALUES (?, ?)"
        " ON CONFLICT(source_key) DO UPDATE SET last_message_id = excluded.last_message_id",
        (source_key, message_id),
    )


async def fetch(client, source, peer, seen, limit) -> tuple[list[tuple], int]:
    """Забрать новые сообщения канала. Пустой список — либо тихо, либо не ответил."""
    rows: list[tuple] = []
    highest = seen
    async for message in client.get_chat_history(peer, limit=limit):
        if message.id <= seen:
            break
        highest = max(highest, message.id)
        text = message.text or message.caption or ""
        if not text.strip():
            continue
        rows.append((
            source.key, message.chat.id, message.id,
            utc_iso(message.date) if message.date else now_utc().isoformat(),
            now_utc().isoformat(), text, message.views,
        ))
    return rows, highest


async def poll_source(client, connection, source) -> int | None:
    """Новые сообщения канала; None — канал не ответил (для учёта лежачих)."""
    seen = last_seen(connection, source.key)
    limit = FIRST_RUN_LIMIT if seen == 0 else CATCH_UP_LIMIT

    # Ходим по идентификатору: имя владелец меняет когда угодно, и опрос по
    # имени в этот момент просто перестаёт что-либо находить — молча, без
    # ошибки, как будто в канале стало тихо. Имя остаётся запасным ключом на
    # два случая: первое знакомство и потерянный кеш сессии, в котором
    # идентификатор не разрешается.
    chat_id = known_chat_id(connection, source.key)
    try:
        if chat_id:
            rows, highest = await fetch(client, source, chat_id, seen, limit)
        else:
            rows, highest = await fetch(client, source, source.username, seen, limit)
    except FloodWait as wait:
        # Глобальный лимит аккаунта, а не вина канала: лежачим не считается.
        await asyncio.sleep(wait.value + 1)
        return 0
    except RPCError:
        if not chat_id:
            return None
        # Идентификатор не разрешился — берём имя, и тем же проходом узнаём
        # актуальный идентификатор из самих сообщений.
        try:
            rows, highest = await fetch(client, source, source.username, seen, limit)
        except FloodWait as wait:
            await asyncio.sleep(wait.value + 1)
            return 0
        except RPCError:
            return None

    if rows:
        connection.executemany(
            "INSERT OR IGNORE INTO raw_messages"
            " (source_key, chat_id, message_id, posted_at, received_at, text, views)"
            " VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        remember_chat_id(connection, source.key, rows[0][1])
    if highest > seen:
        remember(connection, source.key, highest)
    connection.commit()
    return len(rows)


# --- Ярусы опроса -----------------------------------------------------------
# 258 источников одним аккаунтом — это ~5 минут на круг и FloodWait от
# Telegram в каждом цикле: опрашивать ВСЁ быстрее нельзя, лимит аккаунта
# уже исчерпан. Зато бюджет запросов можно перераспределить: горячие каналы
# (официальные ленты и те, кто реально пишет) — каждый круг, тихий хвост —
# каждый четвёртый, лежачие — каждый десятый, с автоматическим возвратом
# после первого же успешного ответа.

# Официальные ленты всегда горячие: пишут редко (по активности не пройдут),
# а доставить их надо быстрее всех.
HOT_TIERS = {"official"}
# Самых активных каналов за окно — в горячий ярус.
HOT_ACTIVE = 70
ACTIVITY_HOURS = 48
COLD_EVERY = 4
DEAD_AFTER = 5
DEAD_EVERY = 10


def active_keys(connection, limit: int = HOT_ACTIVE) -> set[str]:
    """Каналы, дававшие больше всего сообщений за окно активности."""
    rows = connection.execute(
        "SELECT source_key, COUNT(*) AS c FROM raw_messages"
        " WHERE posted_at >= datetime('now', ?)"
        " GROUP BY source_key ORDER BY c DESC LIMIT ?",
        (f"-{ACTIVITY_HOURS} hours", limit),
    ).fetchall()
    return {row["source_key"] for row in rows}


def cycle_sources(connection, sources, failures: dict, cycle: int,
                  subscribed: set[str] | None = None) -> list:
    """Кого опрашивать в этом круге.

    Подписанные каналы Telegram доставляет пушем сам (см. live_handler),
    поэтому из горячего яруса они уходят в редкую страховку вместе с
    тихим хвостом: опрос там ловит только пропуски за время рестартов.
    """
    hot = active_keys(connection)
    subscribed = subscribed or set()
    batch = []
    cold_position = 0
    for source in sources:
        if failures.get(source.key, 0) >= DEAD_AFTER:
            if cycle % DEAD_EVERY == 0:
                batch.append(source)
            continue
        if source.key not in subscribed and (
                source.tier in HOT_TIERS or source.key in hot):
            batch.append(source)
            continue
        if cold_position % COLD_EVERY == cycle % COLD_EVERY:
            batch.append(source)
        cold_position += 1
    return batch


def live_handler(connection, key_by_chat: dict[int, str]):
    """Обработчик пуш-сообщений: подписанные каналы пишутся в базу сразу.

    Telegram шлёт апдейты по каналам, где аккаунт состоит, — задержка
    секунды вместо минут круга опроса, и лимиты запросов не тратятся.
    Курсор poll_state здесь не двигается намеренно: обход-страховка сам
    дочитает историю и через INSERT OR IGNORE не создаст дублей.
    """
    async def on_message(_, message) -> None:
        source_key = key_by_chat.get(message.chat.id)
        if not source_key:
            return
        text = message.text or message.caption or ""
        if not text.strip():
            return
        connection.execute(
            "INSERT OR IGNORE INTO raw_messages"
            " (source_key, chat_id, message_id, posted_at, received_at,"
            " text, views)"
            " VALUES (?,?,?,?,?,?,?)",
            (source_key, message.chat.id, message.id,
             utc_iso(message.date) if message.date else now_utc().isoformat(),
             now_utc().isoformat(), text, message.views),
        )
        connection.commit()

    return on_message


async def subscribed_sources(client, sources) -> tuple[set[str], dict[int, str]]:
    """Каналы, где аккаунт состоит: их Telegram доставляет пушем.

    Один проход по диалогам при старте; сопоставление по username и по
    известному chat_id.
    """
    by_username = {source.username.lower(): source for source in sources}
    keys: set[str] = set()
    chat_map: dict[int, str] = {}
    async for dialog in client.get_dialogs():
        username = (getattr(dialog.chat, "username", None) or "").lower()
        source = by_username.get(username)
        if source:
            keys.add(source.key)
            chat_map[dialog.chat.id] = source.key
    return keys, chat_map


async def sweep(client, connection, sources, failures: dict | None = None) -> dict:
    added = 0
    failed = 0
    for source in sources:
        try:
            result = await poll_source(client, connection, source)
        except Exception:  # noqa: BLE001 — один битый канал не должен ронять обход
            result = None
        if result is None:
            failed += 1
            if failures is not None:
                failures[source.key] = failures.get(source.key, 0) + 1
        else:
            added += result
            if failures is not None:
                failures[source.key] = 0
        # Пауза между каналами: семь десятков запросов подряд ведут к флуд-лимиту.
        await asyncio.sleep(0.35)
    return {"added": added, "failed": failed, "sources": len(sources)}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Опрос истории каналов-источников")
    parser.add_argument("--loop", type=float, default=None, metavar="СЕК")
    args = parser.parse_args()

    require_session()
    ensure_dirs()
    connection = connect()
    connection.execute("PRAGMA busy_timeout = 5000")
    ensure_state(connection)

    sources = sources_from_env()
    client = build_client()

    async with client:
        if args.loop is None:
            print(sweep_report(await sweep(client, connection, sources)))
            return

        # Подписанные каналы приходят пушем сразу — в обход круга опроса.
        subscribed, key_by_chat = await subscribed_sources(client, sources)
        client.add_handler(MessageHandler(live_handler(connection, key_by_chat)))
        print(f"опрос {len(sources)} источников ярусами "
              f"(подписаны и слушаются пушем {len(subscribed)}), пауза "
              f"между кругами {args.loop:g} с, Ctrl+C — стоп", flush=True)
        failures: dict[str, int] = {}
        cycle = 0
        while True:
            batch = cycle_sources(connection, sources, failures, cycle,
                                  subscribed)
            stats = await sweep(client, connection, batch, failures)
            dead = sum(1 for value in failures.values() if value >= DEAD_AFTER)
            print(f"[{now_utc().strftime('%H:%M:%S')}] круг {cycle}: "
                  f"{sweep_report(stats)}, лежачих {dead}", flush=True)
            cycle += 1
            await asyncio.sleep(args.loop)


def sweep_report(stats: dict) -> str:
    return (f"новых сообщений {stats['added']}, "
            f"источников {stats['sources']}, недоступных {stats['failed']}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nостановлено")
