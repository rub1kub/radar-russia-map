"""Идемпотентная догрузка истории Telegram за заданный интервал.

Штатный сборщик должен быть остановлен на время запуска: Telegram-сессия
одна на аккаунт, и второй клиент разрывает первый. Команда проверяет это
сама и отказывается работать при живом сборщике — обойти можно ``--force``,
но обычно это значит, что сборщик забыли остановить.

Без ``--apply`` команда только показывает, сколько строк отсутствует.

    # окно задано руками
    .venv/bin/python ingest/backfill.py \
        --since 2026-08-07T23:55:00+00:00 --apply

    # после простоя: окно считается по самой свежей строке корпуса
    systemctl stop tihoenebo-poll
    .venv/bin/python ingest/backfill.py --gap --apply
    systemctl start tihoenebo-poll
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyrogram.errors import FloodWait, RPCError

from config import build_client, ensure_dirs, require_session, sources_from_env
from pipeline.db import connect
from pipeline.timeutil import now_utc, parse_utc, to_utc, utc_iso
from poll import known_chat_id

# Нахлёст окна в режиме --gap. Сборщик мог оборваться посреди прохода:
# последняя записанная строка не значит, что всё до неё на месте.
GAP_OVERLAP = timedelta(minutes=15)
# Насколько свежая строка в корпусе считается признаком живого сборщика.
# Проход у него 45 секунд, но каналы бывают тихими — берём с запасом.
LIVE_WINDOW = timedelta(minutes=4)


def systemd_poll_active() -> bool:
    """Работает ли сборщик как служба. На машине без systemd — нет."""
    if not shutil.which("systemctl"):
        return False
    try:
        done = subprocess.run(
            ["systemctl", "is-active", "--quiet", "tihoenebo-poll"],
            timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def poll_processes() -> list[str]:
    """Чужие процессы poll.py. Свой pid исключён — иначе поймали бы себя."""
    if not shutil.which("pgrep"):
        return []
    try:
        done = subprocess.run(["pgrep", "-af", "poll.py"],
                              capture_output=True, text=True,
                              timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    mine = str(os.getpid())
    lines = []
    for line in done.stdout.splitlines():
        pid, _, command = line.partition(" ")
        # pgrep -f видит и строку запуска самого backfill, если в ней
        # случайно есть «poll.py» — например при запуске через обёртку.
        if pid != mine and "backfill" not in command:
            lines.append(line.strip())
    return lines


def fresh_corpus_row(connection) -> str | None:
    """Метка последней записи, если она свежее LIVE_WINDOW."""
    row = connection.execute(
        "SELECT MAX(received_at) m FROM raw_messages").fetchone()
    if not row or not row["m"]:
        return None
    try:
        received = parse_utc(row["m"])
    except (TypeError, ValueError):
        return None
    return row["m"] if now_utc() - received < LIVE_WINDOW else None


def collector_reason(connection) -> str | None:
    """Почему запускать нельзя, или None если сборщик точно не работает.

    Три независимых признака: служба, процесс и свежесть корпуса. Порознь
    каждый ошибается — служба видна только на сервере, процесс только на
    своей машине, а тихий час на каналах выглядит как остановка, — но
    вместе они закрывают все способы, которыми сборщик оказывается живым.
    """
    if systemd_poll_active():
        return "служба tihoenebo-poll активна"
    processes = poll_processes()
    if processes:
        return "запущен процесс сборщика: " + "; ".join(processes)
    fresh = fresh_corpus_row(connection)
    if fresh:
        return f"в корпус только что писали ({fresh[:16]}) — сборщик жив"
    return None


def gap_start(connection) -> datetime:
    """Начало окна «после простоя»: свежайшее сообщение минус нахлёст."""
    row = connection.execute(
        "SELECT MAX(posted_at) m FROM raw_messages").fetchone()
    if not row or not row["m"]:
        raise SystemExit("корпус пуст: режим --gap не от чего отсчитывать, "
                         "задайте --since")
    return parse_utc(row["m"]) - GAP_OVERLAP


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
    window = parser.add_mutually_exclusive_group(required=True)
    window.add_argument("--since", help="начало окна в ISO 8601")
    window.add_argument("--gap", action="store_true",
                        help="после простоя: окно от последней строки корпуса")
    parser.add_argument("--until", help="конец окна в ISO 8601; по умолчанию сейчас")
    parser.add_argument("--apply", action="store_true", help="записать отсутствующие строки")
    parser.add_argument("--force", action="store_true",
                        help="не проверять, остановлен ли сборщик")
    args = parser.parse_args()

    # Отдельное короткое соединение: окно и проверка сборщика считаются до
    # того, как поднимется клиент Telegram, — иначе сессию рвёт сам запуск.
    connection = connect()
    try:
        reason = None if args.force else collector_reason(connection)
        if reason:
            print(f"сборщик работает: {reason}", file=sys.stderr)
            print("остановите его и повторите:\n"
                  "  systemctl stop tihoenebo-poll\n"
                  "  … backfill …\n"
                  "  systemctl start tihoenebo-poll\n"
                  "или запустите с --force, если уверены.", file=sys.stderr)
            raise SystemExit(2)
        since = gap_start(connection) if args.gap else parse_utc(args.since)
    finally:
        connection.close()

    until = parse_utc(args.until) if args.until else None
    if until and until < since:
        parser.error("--until должен быть не раньше --since")
    if args.gap:
        print(f"окно после простоя: с {since.isoformat()[:16]}")

    stats = asyncio.run(run(since, until, args.apply))
    mode = "записано" if args.apply else "dry-run"
    print(f"{mode}: {stats}")


if __name__ == "__main__":
    main()
