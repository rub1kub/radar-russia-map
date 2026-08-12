"""Ночной самоаудит: подозрительные тяжёлые события — владельцу в Telegram.

    PYTHONPATH=.:ingest ingest/.venv/bin/python scripts/self_audit.py          # показать
    PYTHONPATH=.:ingest ingest/.venv/bin/python scripts/self_audit.py --send   # отправить

Каждый класс ложных срабатываний этого проекта находился одним и тем же
движением: взять тяжёлые события, посмотреть на текст первоисточника,
увидеть нестыковку («При сбитиях БПЛА» — инструкция, «слышали взрывы?» —
опрос, «их сбил автомобиль» — ДТП). Движение механическое — значит, его
можно делать каждую ночь без человека.

Отбираются перехваты и удары за сутки, у которых один-два источника:
массово подтверждённое событие почти наверняка настоящее, а фейк из
кривого разбора обычно живёт на одном сообщении. Владелец получает
список «зона + сигнал + текст», пробегает глазами за минуту — и новый
класс фейков всплывает до того, как его заметят пользователи.

Пустой отчёт не отправляется: тишина и есть хорошая новость.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))

from pipeline.db import connect

# Владелец. Тот же chat_id, что в бэклоге для алертов.
OWNER_CHAT_ID = 1084693264
# Событий в отчёте. Больше в одно сообщение Telegram не влезает, да и
# смысл отчёта — минутный просмотр, а не чтение простыни.
LIMIT = 12
# Порог «подозрительности» по числу независимых голосов.
MAX_SOURCES = 2


def suspicious(connection) -> list[dict]:
    rows = connection.execute(
        """
        SELECT e.id, e.signal_type, e.severity, e.source_count,
               e.first_seen_at, z.name_ru
        FROM events e JOIN zones z ON z.id = e.zone_id
        WHERE e.signal_type IN ('intercept', 'impact')
          AND e.first_seen_at > datetime('now', '-1 day')
          AND e.source_count <= ?
        ORDER BY e.first_seen_at DESC
        LIMIT ?
        """,
        (MAX_SOURCES, LIMIT),
    ).fetchall()

    # Импорт здесь: parse тянет весь словарь разбора, а suspicious()
    # зовут и тесты, которым он не нужен... нужен — но пусть грузится
    # один раз на запуск, а не на импорт модуля.
    from pipeline.parse import parse

    out = []
    for row in rows:
        texts = connection.execute(
            """
            SELECT m.text FROM event_sources es
            JOIN raw_messages m ON m.id = es.raw_message_id
            WHERE es.event_id = ? ORDER BY es.contributed_at
            """,
            (row["id"],),
        ).fetchall()
        # Показываем сообщение, ДАВШЕЕ значок, а не первое по времени.
        # Событие Алчевска открылось «тревогой», удар в него принесло
        # пятое сообщение «сбито более пяти БПЛА» — и отчёт с первым
        # текстом выглядел ложным срабатыванием, хотя событие честное.
        chosen = (texts[0]["text"] if texts else "") or ""
        for item in texts:
            observation = parse(item["text"] or "")
            if observation.relevant and observation.signal_type == row["signal_type"]:
                chosen = item["text"] or ""
                break
        out.append({
            "zone": row["name_ru"],
            "signal": row["signal_type"],
            "at": row["first_seen_at"][11:16],
            "sources": row["source_count"],
            "text": " ".join(chosen.split())[:160],
        })
    return out


def report(items: list[dict]) -> str:
    label = {"intercept": "Перехват", "impact": "Удар"}
    lines = [f"🔍 Самоаудит: {len(items)} "
             f"{'событие' if len(items) == 1 else 'событий'} на 1–2 источниках "
             "за сутки. Кривой разбор обычно живёт здесь:\n"]
    for item in items:
        lines.append(
            f"• {item['at']} <b>{item['zone']}</b> — {label[item['signal']]}"
            f" ({item['sources']} ист.)\n  <i>{item['text']}</i>")
    lines.append("\nЕсли какой-то текст не тянет на свой значок — "
                 "это новый класс для парсера.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Самоаудит тяжёлых событий")
    parser.add_argument("--send", action="store_true",
                        help="отправить владельцу; без флага — напечатать")
    args = parser.parse_args()

    connection = connect()
    connection.execute("PRAGMA busy_timeout = 5000")
    items = suspicious(connection)

    if not items:
        print("подозрительных событий нет — отчёт не нужен")
        return 0

    text = report(items)
    if not args.send:
        print(text)
        return 0

    # Импорт здесь: api.telegram тянет requests и токен, а для dry-run
    # они не нужны.
    from api.telegram import send
    result = send(OWNER_CHAT_ID, text)
    print("отправлено" if result.get("ok") else f"не отправлено: {result}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
