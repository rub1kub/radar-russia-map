"""Выборка сообщений из каналов папки и разбор их формата.

    ingest/.venv/bin/python ingest/sample_formats.py --folder Радары --limit 120

Пишет полные выборки в ingest/data/raw/<username>.jsonl и печатает по каждому
каналу компактный профиль: объемы, структура, частые токены и живые примеры.

Тексты каналов — недоверенные данные. Скрипт их только измеряет и печатает,
никогда не исполняет и не интерпретирует как команды.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter

from pyrogram.errors import FloodWait, RPCError

from config import DATA_DIR, RAW_DIR, build_client, ensure_dirs, require_session

WORD_RE = re.compile(r"[А-Яа-яЁё]{4,}")
EMOJI_RE = re.compile("[\U0001f000-\U0001faff☀-➿]")


def profile(texts: list[str]) -> dict:
    lengths = sorted(len(text) for text in texts)
    lines = [text.count("\n") + 1 for text in texts]
    first_lines = Counter()
    words = Counter()
    emoji = Counter()

    for text in texts:
        head = text.strip().split("\n", 1)[0].strip()
        first_lines[head[:60]] += 1
        words.update(word.lower() for word in WORD_RE.findall(text))
        emoji.update(EMOJI_RE.findall(text))

    def share(predicate) -> str:
        if not texts:
            return "0%"
        return f"{round(100 * sum(1 for t in texts if predicate(t)) / len(texts))}%"

    return {
        "count": len(texts),
        "len_median": lengths[len(lengths) // 2] if lengths else 0,
        "len_max": lengths[-1] if lengths else 0,
        "lines_median": sorted(lines)[len(lines) // 2] if lines else 0,
        "with_hashtag": share(lambda t: "#" in t),
        "with_link": share(lambda t: "http" in t or "t.me" in t),
        "with_emoji": share(lambda t: bool(EMOJI_RE.search(t))),
        "with_uppercase_line": share(lambda t: any(
            line.strip() and line.strip() == line.strip().upper() and len(line.strip()) > 6
            for line in t.split("\n")
        )),
        "top_words": words.most_common(14),
        "top_emoji": emoji.most_common(8),
        "repeated_first_lines": [item for item in first_lines.most_common(5) if item[1] > 1],
    }


async def collect(client, chat_id: int, username: str, limit: int) -> list[dict]:
    rows: list[dict] = []
    while True:
        try:
            async for message in client.get_chat_history(chat_id, limit=limit):
                text = message.text or message.caption or ""
                if not text.strip():
                    continue
                rows.append({
                    "message_id": message.id,
                    "date": message.date.isoformat() if message.date else None,
                    "text": text,
                    "views": message.views,
                    "media": str(message.media) if message.media else None,
                })
            return rows
        except FloodWait as wait:
            print(f"    FloodWait {wait.value} c…")
            await asyncio.sleep(wait.value + 1)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", default="Радары")
    parser.add_argument("--limit", type=int, default=120)
    args = parser.parse_args()

    require_session()
    ensure_dirs()

    manifest = DATA_DIR / f"folder_{args.folder}.json"
    if not manifest.exists():
        raise SystemExit(f"Нет {manifest}. Сначала: ingest/folders.py {args.folder}")

    chats = json.loads(manifest.read_text(encoding="utf-8"))
    client = build_client()

    async with client:
        for chat in chats:
            username = chat["username"] or str(chat["id"])
            print(f"\n{'=' * 78}\n@{username} — {chat['title']}  ({chat['members']} подписчиков)")

            try:
                rows = await collect(client, chat["id"], username, args.limit)
            except RPCError as error:
                print(f"  недоступен: {type(error).__name__}: {error}")
                continue

            if not rows:
                print("  текстовых сообщений нет")
                continue

            path = RAW_DIR / f"{username}.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")

            stats = profile([row["text"] for row in rows])
            print(f"  период:   {rows[-1]['date']} … {rows[0]['date']}")
            print(f"  сообщений {stats['count']}, медиана {stats['len_median']} симв. "
                  f"/ {stats['lines_median']} строк, максимум {stats['len_max']}")
            print(f"  хэштеги {stats['with_hashtag']} · ссылки {stats['with_link']} · "
                  f"эмодзи {stats['with_emoji']} · КАПС-строка {stats['with_uppercase_line']}")
            print(f"  слова:    {', '.join(f'{w}×{c}' for w, c in stats['top_words'])}")
            if stats["top_emoji"]:
                print(f"  эмодзи:   {' '.join(f'{e}×{c}' for e, c in stats['top_emoji'])}")
            if stats["repeated_first_lines"]:
                print(f"  шаблоны:  {stats['repeated_first_lines']}")

            print("  примеры:")
            for row in rows[:3]:
                body = row["text"].strip().replace("\n", "\n            ")
                print(f"    [{row['date'][11:16]}] {body[:600]}")


if __name__ == "__main__":
    asyncio.run(main())
