"""Поиск новых каналов-источников.

    ingest/.venv/bin/python ingest/discover.py

Три способа разом:
  1. упоминания @username и ссылки t.me в уже собранном корпусе — каналы
     постоянно ссылаются друг на друга и на соседние региональные ленты;
  2. пересылки: если сообщение переслано, его источник тоже кандидат;
  3. поиск Telegram по ключевым словам с названиями регионов.

Каждый кандидат проверяется по свежим сообщениям: лента оповещений или нет.
Результат — ingest/data/candidates.json, решение о включении за человеком.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyrogram import raw
from pyrogram.errors import FloodWait, RPCError

from config import DATA_DIR, build_client, ensure_dirs, require_session, sources_from_env
from pipeline.db import connect

MENTION_RE = re.compile(r"@([a-zA-Z][a-zA-Z0-9_]{4,31})")
LINK_RE = re.compile(r"t\.me/(?:s/)?([a-zA-Z][a-zA-Z0-9_]{4,31})")

# Общие запросы по типу ленты.
SEARCH_TERMS = [
    "радар", "радар бпла", "воздушная тревога", "оповещения бпла",
    "мониторинг бпла", "тревога бпла", "радар тревога", "пво тревога",
]

# География: приграничье и новые регионы, где лент больше всего.
REGIONS = [
    "Белгород", "Курск", "Брянск", "Воронеж", "Ростов", "Таганрог",
    "Крым", "Севастополь", "Керчь", "Симферополь",
    "Донецк", "Луганск", "Мариуполь", "Горловка", "Макеевка",
    "Херсон", "Запорожье", "Мелитополь", "Бердянск",
    "Волгоград", "Саратов", "Тамбов", "Липецк", "Орёл", "Смоленск",
    "Тула", "Рязань", "Пенза", "Самара", "Краснодар", "Адыгея", "Калуга",
    # Тыловые субъекты: дальнобойные аппараты доходят и туда, а лент по ним
    # у нас нет ни одной — по этим регионам сообщение сейчас в лучшем случае
    # приезжает из федеральной ленты, без районной детализации.
    "Нижний Новгород", "Казань", "Уфа", "Ижевск", "Пермь", "Киров",
    "Чебоксары", "Ульяновск", "Оренбург", "Челябинск", "Екатеринбург",
    "Тюмень", "Омск", "Новосибирск", "Ярославль", "Кострома", "Иваново",
    "Владимир", "Тверь", "Псков", "Новгород", "Мурманск", "Архангельск",
    "Вологда", "Калининград", "Брянская область", "Курская область",
    "Астрахань", "Ставрополь", "Сочи", "Анапа", "Новороссийск",
    "Энгельс", "Балашов", "Борисоглебск", "Морозовск", "Миллерово",
]
# Два шаблона на регион: канал может называться и так, и так.
REGION_TERMS = [f"{name} тревога" for name in REGIONS] + [f"радар {name}" for name in REGIONS]

# Слова, по которым видно ленту оповещений, а не новостной или магазинный канал.
ALERT_MARKERS = re.compile(
    r"бпла|беспилот|тревог|опасност|отбой|фиксац|ракет|пво|сбит|укрыт|мцд|воздушн",
    re.IGNORECASE,
)
NOISE_MARKERS = re.compile(
    r"реклама|скидк|купить|заказ|промокод|казино|ставк|подписка на курс|инвестиц",
    re.IGNORECASE,
)


def known_usernames() -> set[str]:
    return {source.username.lower() for source in sources_from_env()}


def mine_corpus() -> dict[str, int]:
    """Кандидаты из текстов уже собранных сообщений."""
    connection = connect()
    counter: dict[str, int] = {}
    for row in connection.execute("SELECT text FROM raw_messages"):
        text = row["text"]
        for match in MENTION_RE.findall(text) + LINK_RE.findall(text):
            key = match.lower()
            counter[key] = counter.get(key, 0) + 1
    return counter


async def probe(client, username: str) -> dict | None:
    """Проверить кандидата: существует, публичный, похож на ленту оповещений."""
    try:
        chat = await client.get_chat(username)
    except FloodWait as wait:
        await asyncio.sleep(wait.value + 1)
        return None
    except RPCError:
        return None

    if str(chat.type) not in ("ChatType.CHANNEL", "ChatType.SUPERGROUP"):
        return None

    texts: list[str] = []
    try:
        async for message in client.get_chat_history(chat.id, limit=40):
            body = message.text or message.caption or ""
            if body.strip():
                texts.append(body)
    except RPCError:
        return None

    if len(texts) < 5:
        return None

    alert_hits = sum(1 for text in texts if ALERT_MARKERS.search(text))
    noise_hits = sum(1 for text in texts if NOISE_MARKERS.search(text))
    share = alert_hits / len(texts)

    return {
        "username": chat.username,
        "title": chat.title,
        "id": chat.id,
        "members": chat.members_count,
        "sampled": len(texts),
        "alert_share": round(share, 2),
        "noise_share": round(noise_hits / len(texts), 2),
        "verdict": "подходит" if share >= 0.5 and noise_hits <= len(texts) * 0.2 else "сомнительно",
        "sample": " | ".join(" ".join(text.split())[:70] for text in texts[:3]),
    }


async def main() -> None:
    require_session()
    ensure_dirs()
    known = known_usernames()
    client = build_client()

    mentioned = mine_corpus()
    print(f"упоминаний в корпусе: {len(mentioned)} уникальных имён")

    candidates: dict[str, str] = {}
    for username, count in sorted(mentioned.items(), key=lambda item: -item[1]):
        if username in known:
            continue
        candidates[username] = f"упомянут {count} раз"

    async with client:
        # contacts.Search ищет публичные каналы по username и названию.
        # search_global для этого не годится: он шарит по сообщениям тех
        # чатов, которые аккаунт и так видит, и возвращает уже известное.
        for term in SEARCH_TERMS + REGION_TERMS:
            try:
                found = await client.invoke(raw.functions.contacts.Search(q=term, limit=25))
            except FloodWait as wait:
                await asyncio.sleep(wait.value + 1)
                continue
            except RPCError:
                continue

            for chat in found.chats:
                name = getattr(chat, "username", None)
                members = getattr(chat, "participants_count", None) or 0
                # Совсем крошечные каналы чаще всего заброшены или дубли.
                if name and name.lower() not in known and members >= 300:
                    candidates.setdefault(name.lower(), f"поиск: {term}")
            await asyncio.sleep(0.4)

        print(f"кандидатов к проверке: {len(candidates)}\n")

        results: list[dict] = []
        for index, (username, reason) in enumerate(candidates.items(), 1):
            info = await probe(client, username)
            if not info:
                continue
            info["reason"] = reason
            results.append(info)
            mark = "+" if info["verdict"] == "подходит" else "?"
            print(f"  [{mark}] @{info['username']:<28} {info['members'] or 0:>8} подп. "
                  f"оповещений {info['alert_share']:.0%}  {info['title'][:34]}")
            if index % 12 == 0:
                await asyncio.sleep(2)

    results.sort(key=lambda item: (item["verdict"] != "подходит", -(item["members"] or 0)))
    path = DATA_DIR / "candidates.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    good = [item for item in results if item["verdict"] == "подходит"]
    print(f"\nподходящих: {len(good)} из {len(results)} проверенных")
    print(f"-> {path}")


if __name__ == "__main__":
    asyncio.run(main())
