"""Определение сетей каналов по фактическим совпадениям текстов.

Достоверность события считается как объединение независимых свидетельств.
Независимость определялась по шаблону названия («Радар.ру | X область»), и
этого мало: `lpr1_treugolnik` и `radarcrimea` называются по-разному, а текст
публикуют один и тот же — 79 совпадений в корпусе. Перепечатка засчитывалась
как независимое подтверждение, и достоверность завышалась.

Здесь сеть определяется по поведению: если два канала регулярно публикуют
дословно совпадающий текст в пределах короткого окна, это одна редакция.
Каналы связываются в граф, компоненты связности становятся сетями.

    ingest/.venv/bin/python -m pipeline.networks          # пересчитать
    ingest/.venv/bin/python -m pipeline.networks --show   # показать найденное
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from collections import defaultdict

from .db import connect
from .timeutil import parse_utc

# Окно, внутри которого совпадение текста считается перепечаткой, а не
# независимым наблюдением одного события. Разные редакции пишут по-разному
# даже об одном и том же, поэтому дословное совпадение через минуту — это
# копирование, а не совпадение формулировок.
REPOST_WINDOW_SEC = 600

# Сколько совпадений нужно, чтобы связать каналы. Одно-два — случайность:
# короткие сообщения вроде «Отбой» совпадают у кого угодно.
MIN_MATCHES = 5

# Доля совпадений от объёма МЕНЬШЕГО канала в паре. Одного счёта мало:
# за три месяца пять дословных совпадений набирается и у честных каналов,
# цитирующих одну сводку РСЧС, — транзитивное замыкание через такие рёбра
# склеивало 162 канала в одну сеть вместе с губернатором Севастополя и
# Росавиацией. Клоны выдаёт пропорция: у ферм («lpr1_*», «radar_<регион>»
# с двойными буквами) меньший канал совпадает с парой на 20–92%, у
# независимых — на 2–5%. Порог между этими облаками.
MIN_SHARE = 0.2

# Короткие тексты выкидываем: «Отбой», «Опасность БПЛА» совпадают дословно
# у независимых каналов просто потому, что иначе это не написать.
MIN_TEXT_LEN = 25

# Цифры сохраняются намеренно: без них «Ещё 2 БПЛА от Новобелая» и
# «Ещё 5 БПЛА от Новобелая» становятся одним текстом, счёт перепечаток
# завышается, и независимые каналы могут попасть в одну сеть.
NORM_RE = re.compile(r"[^а-яё0-9 ]+")

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_networks (
    source_key TEXT PRIMARY KEY,
    network_id TEXT NOT NULL,
    matches    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_source_networks_net ON source_networks (network_id);
"""


def normalize(text: str) -> str:
    """Ключ сравнения: кириллица и цифры, без пунктуации и эмодзи."""
    return " ".join(NORM_RE.sub(" ", text.lower()).split())


def collect_pairs(connection: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """Пары каналов и число дословных совпадений в пределах окна."""
    buckets: dict[str, list[tuple[str, float]]] = defaultdict(list)

    for row in connection.execute(
        "SELECT source_key, posted_at, text FROM raw_messages ORDER BY posted_at"
    ):
        key = normalize(row["text"])
        if len(key) < MIN_TEXT_LEN:
            continue
        buckets[key].append((row["source_key"], parse_utc(row["posted_at"]).timestamp()))

    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for items in buckets.values():
        if len(items) < 2:
            continue
        items.sort(key=lambda item: item[1])
        for index, (left, left_at) in enumerate(items):
            for right, right_at in items[index + 1:]:
                if right_at - left_at > REPOST_WINDOW_SEC:
                    break
                if left == right:
                    continue
                pairs[tuple(sorted((left, right)))] += 1
    return dict(pairs)


def official_keys() -> set[str]:
    """Каналы с tier=official — губернаторы, МЧС, Росавиация, РСЧС.

    Они в сети не входят никогда: официальный канал пишет своими словами,
    а совпадения с ним — это ленты, цитирующие официальное сообщение.
    Склейка тянула губернатора Севастополя в ферму клонов, и его голос
    переставал считаться отдельным свидетельством.
    """
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))
        from config import sources_from_env
        return {s.key for s in sources_from_env() if s.tier == "official"}
    except Exception:
        # Без конфига (тесты, чужая машина) — просто без белого списка.
        return set()


def components(pairs: dict[tuple[str, str], int],
               totals: dict[str, int] | None = None,
               official: set[str] | None = None) -> list[set[str]]:
    """Компоненты связности графа перепечаток.

    Ребро — только пара, где совпадений и много (MIN_MATCHES), и они
    составляют заметную долю меньшего канала (MIN_SHARE). Замыкание по
    таким рёбрам безопасно: у настоящих клонов доля 20–92%, и цепочка
    ведёт по одной редакции, а не через случайно совпавшую сводку.
    """
    totals = totals or {}
    official = official if official is not None else set()
    adjacency: dict[str, set[str]] = defaultdict(set)
    for (left, right), count in pairs.items():
        if count < MIN_MATCHES:
            continue
        if left in official or right in official:
            continue
        smaller = min(totals.get(left, 0), totals.get(right, 0))
        if smaller and count / smaller < MIN_SHARE:
            continue
        adjacency[left].add(right)
        adjacency[right].add(left)

    seen: set[str] = set()
    groups: list[set[str]] = []
    for node in adjacency:
        if node in seen:
            continue
        stack, group = [node], set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            group.add(current)
            stack.extend(adjacency[current] - seen)
        groups.append(group)
    return groups


def rebuild_networks(connection: sqlite3.Connection) -> dict:
    connection.executescript(SCHEMA)
    pairs = collect_pairs(connection)
    totals = {
        row["source_key"]: row["n"]
        for row in connection.execute(
            "SELECT source_key, COUNT(*) n FROM raw_messages GROUP BY source_key")
    }
    groups = components(pairs, totals, official_keys())

    strength: dict[str, int] = defaultdict(int)
    for (left, right), count in pairs.items():
        if count >= MIN_MATCHES:
            strength[left] += count
            strength[right] += count

    connection.execute("DELETE FROM source_networks")
    rows = []
    for group in groups:
        # Идентификатор сети — алфавитно первый канал: он не меняется от
        # запуска к запуску, пока состав группы тот же.
        network_id = "net:" + min(group)
        for member in group:
            rows.append((member, network_id, strength[member]))
    connection.executemany(
        "INSERT INTO source_networks (source_key, network_id, matches) VALUES (?,?,?)",
        rows,
    )
    connection.commit()

    return {
        "pairs_total": len(pairs),
        "pairs_linked": sum(1 for count in pairs.values() if count >= MIN_MATCHES),
        "networks": len(groups),
        "sources_in_networks": len(rows),
        "largest": max((len(group) for group in groups), default=0),
    }


def load_networks(connection: sqlite3.Connection) -> dict[str, str]:
    """source_key -> network_id. Пусто, если пересчёт ещё не делали."""
    try:
        return {
            row["source_key"]: row["network_id"]
            for row in connection.execute("SELECT source_key, network_id FROM source_networks")
        }
    except sqlite3.OperationalError:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Сети каналов по совпадениям текстов")
    parser.add_argument("--show", action="store_true", help="только показать найденное")
    args = parser.parse_args()

    connection = connect()
    connection.execute("PRAGMA busy_timeout = 5000")

    if not args.show:
        stats = rebuild_networks(connection)
        print("пересчёт:", stats)

    connection.executescript(SCHEMA)
    grouped: dict[str, list[str]] = defaultdict(list)
    for source_key, network_id in load_networks(connection).items():
        grouped[network_id].append(source_key)

    print(f"\nсетей: {len(grouped)}")
    for network_id, members in sorted(grouped.items(), key=lambda item: -len(item[1])):
        print(f"  {network_id} ({len(members)}): {', '.join(sorted(members))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
