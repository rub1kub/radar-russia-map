"""Количественный разбор лексики событий по собранным выборкам.

    ingest/.venv/bin/python ingest/analyze_lexicon.py

Читает ingest/data/raw/*.jsonl и считает: типы событий, типы угроз, маркеры
направления и счета целей, объем футеров, долю нерелевантных сообщений и
пересечения между каналами (одно событие в нескольких лентах).
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime

from config import RAW_DIR

SIGNAL = {
    "тревога": r"\bтревог",
    "опасность": r"\bопасност",
    "фиксация": r"\bфиксац",
    "сбитие": r"\bсбит|\bуничтож",
    "работа ПВО": r"работа\s+пво|\bпво\b",
    "отбой": r"\bотбой",
    "меры безопасности": r"мер[ыу]\s+(безопасн|предостор)",
    "внимание": r"\bвнимание\b",
    "пролет": r"\bпролёт|\bпролет\b",
    "взрыв/гром": r"\bвзрыв|\bгром\b",
    "сирена": r"\bсирен",
    "угроза удара": r"угроз[аы]\s+(непосредств|удара|атаки)",
}

THREAT = {
    "БПЛА": r"\bбпла\b|беспилот",
    "FPV": r"\bfpv\b",
    "ракета": r"\bракет",
    "КАБ/УАБ": r"\bкаб\b|\bуаб\b|управляем\w+\s+авиабомб",
    "авиация": r"авиац|\bмиг-|\bту-\d",
    "БЭК": r"\bбэк\b|безэкипаж",
    "аэропорт": r"аэропорт",
    "Крымский мост": r"крымск\w+\s+мост",
}

DIRECTION = r"в\s+направлени|в\s+сторону|с\s+(севера|юга|запада|востока)|\bсеверо-|\bюго-"
COUNT = r"\bещё\s+\d+|\bеще\s+\d+|\d+\s*бпла|много\s+фиксац|групп[аы]\s+бпла"
FOOTER = re.compile(r"(подписаться|@[a-z_0-9]+|подписка|наш канал|в\s+max\b|в\s+мах\b)", re.I)
RSCHS = r"рсчс|экстренная информация|беспилотная опасность на территории"

# Нерелевантное: политические/военные новости без локальной обстановки.
NEWS = r"\b(мид|зеленск|путин|минобороны|заявил|сообщил|написал|переговор|санкц|саммит)\b"


def load() -> dict[str, list[dict]]:
    data: dict[str, list[dict]] = {}
    for path in sorted(RAW_DIR.glob("*.jsonl")):
        if path.stem in {"live"}:
            continue
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if rows:
            data[path.stem] = rows
    return data


def strip_footer(text: str) -> str:
    lines = [line for line in text.strip().split("\n") if not FOOTER.search(line)]
    return "\n".join(lines).strip()


def hits(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text, re.I))


def main() -> None:
    data = load()
    if not data:
        raise SystemExit(f"Нет выборок в {RAW_DIR}. Сначала: ingest/sample_formats.py")

    print(f"Каналов: {len(data)}, сообщений: {sum(len(v) for v in data.values())}\n")

    print(f"{'канал':<28}{'сообщ':>6}{'футер':>7}{'новости':>8}{'РСЧС':>6}{'напр':>6}{'счет':>6}")
    print("-" * 67)
    for name, rows in data.items():
        texts = [row["text"] for row in rows]
        bodies = [strip_footer(t) for t in texts]
        n = len(texts)
        pct = lambda c: f"{round(100 * c / n)}%"
        print(
            f"{name:<28}{n:>6}"
            f"{pct(sum(1 for t in texts if FOOTER.search(t))):>7}"
            f"{pct(sum(1 for t in bodies if hits(NEWS, t))):>8}"
            f"{pct(sum(1 for t in bodies if hits(RSCHS, t))):>6}"
            f"{pct(sum(1 for t in bodies if hits(DIRECTION, t))):>6}"
            f"{pct(sum(1 for t in bodies if hits(COUNT, t))):>6}"
        )

    all_bodies = [strip_footer(row["text"]) for rows in data.values() for row in rows]
    total = len(all_bodies)

    print(f"\nТипы событий (доля сообщений, N={total}):")
    for label, pattern in sorted(SIGNAL.items(), key=lambda kv: -sum(hits(kv[1], t) for t in all_bodies)):
        count = sum(1 for t in all_bodies if hits(pattern, t))
        if count:
            print(f"  {label:<22}{count:>5}  {round(100 * count / total):>3}%")

    print("\nТипы угроз:")
    for label, pattern in sorted(THREAT.items(), key=lambda kv: -sum(hits(kv[1], t) for t in all_bodies)):
        count = sum(1 for t in all_bodies if hits(pattern, t))
        if count:
            print(f"  {label:<22}{count:>5}  {round(100 * count / total):>3}%")

    # Пересечение источников: одинаковое нормализованное тело в разных каналах.
    by_body: dict[str, set[str]] = defaultdict(set)
    when: dict[str, list[datetime]] = defaultdict(list)
    for name, rows in data.items():
        for row in rows:
            key = re.sub(r"[^а-яё ]", "", strip_footer(row["text"]).lower())
            key = " ".join(key.split())
            if len(key) < 12:
                continue
            by_body[key].add(name)
            when[key].append(datetime.fromisoformat(row["date"]))

    shared = {k: v for k, v in by_body.items() if len(v) > 1}
    print(f"\nДубли между каналами: {len(shared)} текстов встречаются в 2+ лентах")
    spread = Counter(len(v) for v in shared.values())
    for channels, count in sorted(spread.items()):
        print(f"  в {channels} каналах: {count}")

    print("\nПримеры кросс-источникового подтверждения:")
    for key, channels in sorted(shared.items(), key=lambda kv: -len(kv[1]))[:5]:
        times = sorted(when[key])
        lag = (times[-1] - times[0]).total_seconds()
        print(f"  [{len(channels)} источн., разброс {int(lag)} c] {key[:70]}")
        print(f"     {', '.join(sorted(channels))}")


if __name__ == "__main__":
    main()
