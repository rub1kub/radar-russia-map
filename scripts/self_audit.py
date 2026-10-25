"""Ночной самоаудит: подозрительные тяжёлые события — владельцу в Telegram.

    PYTHONPATH=.:ingest ingest/.venv/bin/python scripts/self_audit.py          # показать
    PYTHONPATH=.:ingest ingest/.venv/bin/python scripts/self_audit.py --send   # отправить

Каждый класс ложных срабатываний этого проекта находился одним и тем же
движением: взять тяжёлые события, посмотреть на текст первоисточника,
увидеть нестыковку («При сбитиях БПЛА» — инструкция, «слышали взрывы?» —
опрос, «их сбил автомобиль» — ДТП). Движение механическое — значит, его
можно делать каждую ночь без человека.

Отбираются малоподтверждённые гражданские предупреждения, ограничения
и прежняя диагностическая выборка перехватов. Число источников задаёт
очерёдность ручной проверки, но само по себе не доказывает ни истинность,
ни ошибочность сообщения. Отчёт показывает ограниченную выборку публикаций
и не заменяет проверку по первоисточнику.

Пустой отчёт не отправляется: тишина и есть хорошая новость.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))

from pipeline.db import connect
from pipeline.timeutil import MSK, now_utc, parse_utc
from config import sources_from_env

# Владелец. Тот же chat_id, что в бэклоге для алертов.
OWNER_CHAT_ID = 1084693264
# Событий в отчёте. Больше в одно сообщение Telegram не влезает, да и
# смысл отчёта — минутный просмотр, а не чтение простыни.
LIMIT = 12
# Порог «подозрительности» по числу независимых голосов.
MAX_SOURCES = 2
REVIEW_SIGNALS = ("alarm", "danger", "infra", "intercept")
SOURCE_NAMES = {source.key: source.username for source in sources_from_env()}
SIGNAL_LABELS = {"alarm": "Тревога", "danger": "Опасность",
                 "infra": "Ограничения", "intercept": "Перехват"}


def _bounds(now: datetime | None):
    end = now or now_utc()
    return (end - timedelta(days=1)).isoformat(), end.isoformat()


def _clock(value: str) -> str:
    return parse_utc(value, assume=timezone.utc).astimezone(MSK).strftime("%H:%M")


def summary(connection, now: datetime | None = None) -> dict:
    start, end = _bounds(now)
    rows = connection.execute(
        """SELECT signal_type, COUNT(*) AS events,
                  SUM(source_count <= ? AND (signal_type='intercept' OR confidence < 0.7)) AS candidates
           FROM events
           WHERE julianday(first_seen_at) > julianday(?)
             AND julianday(first_seen_at) <= julianday(?)
             AND signal_type IN ('alarm','danger','infra','intercept')
           GROUP BY signal_type""", (MAX_SOURCES, start, end)).fetchall()
    return {row["signal_type"]: {"events": row["events"], "candidates": row["candidates"]}
            for row in rows}


def suspicious(connection, now: datetime | None = None) -> list[dict]:
    start, end = _bounds(now)
    rows = connection.execute(
        """
        SELECT e.id, e.signal_type, e.severity, e.source_count,
               e.first_seen_at, z.name_ru
        FROM events e JOIN zones z ON z.id = e.zone_id
        WHERE e.signal_type IN ('alarm','danger','infra','intercept')
          AND julianday(e.first_seen_at) > julianday(?)
          AND julianday(e.first_seen_at) <= julianday(?)
          AND e.source_count <= ?
          AND (e.signal_type='intercept' OR e.confidence < 0.7)
        ORDER BY julianday(e.first_seen_at) DESC, e.id
        """,
        (start, end, MAX_SOURCES),
    ).fetchall()

    # Импорт здесь: parse тянет весь словарь разбора, а suspicious()
    # зовут и тесты, которым он не нужен... нужен — но пусть грузится
    # один раз на запуск, а не на импорт модуля.
    from pipeline.parse import parse

    out = []
    seen = set()
    parsed = {}
    for row in rows:
        texts = connection.execute(
            """
            SELECT m.id, m.text, m.source_key, m.chat_id, m.message_id FROM event_sources es
            JOIN raw_messages m ON m.id = es.raw_message_id
            WHERE es.event_id = ? AND es.role != 'resolve' ORDER BY es.contributed_at
            """,
            (row["id"],),
        ).fetchall()
        # Показываем сообщение, ДАВШЕЕ значок, а не первое по времени.
        # Событие Алчевска открылось «тревогой», удар в него принесло
        # пятое сообщение «сбито более пяти БПЛА» — и отчёт с первым
        # текстом выглядел ложным срабатыванием, хотя событие честное.
        chosen = texts[0] if texts else None
        for item in texts:
            if item["id"] not in parsed:
                parsed[item["id"]] = parse(item["text"] or "")
            observation = parsed[item["id"]]
            if observation.relevant and observation.signal_type == row["signal_type"]:
                chosen = item
                break
        identity = ((chosen["chat_id"], chosen["message_id"])
                    if chosen is not None and chosen["chat_id"] else
                    ("raw", chosen["id"]) if chosen is not None else ("event", row["id"]))
        if identity in seen:
            continue
        seen.add(identity)
        username = SOURCE_NAMES.get(chosen["source_key"]) if chosen is not None else None
        link = (f"https://t.me/{username}/{int(chosen['message_id'])}" if username else None)
        out.append({
            "zone": row["name_ru"],
            "signal": row["signal_type"],
            "at": _clock(row["first_seen_at"]),
            "sources": row["source_count"],
            "text": " ".join((chosen["text"] or "").split())[:160] if chosen is not None else "",
            "raw_id": chosen["id"] if chosen is not None else None,
            "url": link,
        })
        if len(out) >= LIMIT:
            break
    return out


def mass_clears(connection, now: datetime | None = None) -> list[dict]:
    """Сообщения, закрывшие отбоем сразу несколько событий, — глазами.

    Ложный отбой — самый болезненный класс ошибок разбора: «Ждите отмены
    сигнала опасности» из объявления мэрии о НАЧАЛЕ тревоги закрыло три
    события Краснодара, и подписчик получил три «Отбоя» в 03:18 ночи.
    Настоящий массовый отбой («отбой всех ранее объявленных») выглядит
    так же со стороны счётчика — различает только текст, поэтому он и
    показывается владельцу.
    """
    start, end = _bounds(now)
    rows = connection.execute(
        """
        SELECT r.text, MIN(r.posted_at) AS at,
               COUNT(DISTINCT es.event_id) AS closed
        FROM event_sources es
        JOIN raw_messages r ON r.id = es.raw_message_id
        WHERE es.role = 'resolve'
          AND julianday(r.posted_at) > julianday(?)
          AND julianday(r.posted_at) <= julianday(?)
        GROUP BY es.raw_message_id
        HAVING closed >= 3
        ORDER BY closed DESC
        LIMIT 5
        """, (start, end),
    ).fetchall()
    return [{
        "at": _clock(row["at"]),
        "closed": row["closed"],
        "text": " ".join((row["text"] or "").split())[:160],
    } for row in rows]


def report(items: list[dict], clears: list[dict] | None = None,
           totals: dict | None = None) -> str:
    lines = [f"🔍 Самоаудит за 24 часа: показано {len(items)} исходных сообщений "
             "на 1–2 источниках. Время московское.\n"
             "Это кандидаты для ручной проверки, не доказанные ошибки.\n"]
    if totals:
        lines.append("Событий за сутки: " + ", ".join(
            f"{SIGNAL_LABELS.get(signal, signal)} — {values['events']} "
            f"(кандидатов {values['candidates']})" for signal, values in totals.items()))
    for item in items:
        lines.append(
            f"• {item['at']} <b>{escape(item['zone'])}</b> — {SIGNAL_LABELS.get(item['signal'], item['signal'])}"
            f" ({item['sources']} ист.)\n  <i>{escape(item['text'])}</i>"
            + (f" <a href=\"{escape(item['url'], quote=True)}\">Источник</a>" if item.get("url") else ""))
    if clears:
        lines.append("\n🟢 Массовые отбои за сутки (закрыли 3+ событий) — "
                     "проверь, что каждый текст действительно отбой:")
        for item in clears:
            lines.append(f"• {item['at']} закрыл {item['closed']}: "
                         f"<i>{escape(item['text'])}</i>")
    lines.append("\nЕсли какой-то текст не тянет на свой значок — "
                 "это новый класс для парсера.")
    return "\n".join(lines)


def report_chunks(text: str, limit: int = 3800) -> list[str]:
    """Разделение по строкам не разрывает сформированные HTML-теги."""
    chunks, lines, size = [], [], 0
    for line in text.splitlines():
        if len(line) > limit:
            raise ValueError("строка самоаудита превышает размер сообщения")
        if lines and size + len(line) + 1 > limit:
            chunks.append("\n".join(lines))
            lines, size = [], 0
        lines.append(line)
        size += len(line) + 1
    if lines:
        chunks.append("\n".join(lines))
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser(description="Самоаудит тяжёлых событий")
    parser.add_argument("--send", action="store_true",
                        help="отправить владельцу; без флага — напечатать")
    args = parser.parse_args()

    connection = connect()
    connection.execute("PRAGMA busy_timeout = 5000")
    now = now_utc()
    items = suspicious(connection, now)
    clears = mass_clears(connection, now)

    if not items and not clears:
        print("подозрительных событий нет — отчёт не нужен")
        return 0

    text = report(items, clears, summary(connection, now))
    if not args.send:
        print(text)
        return 0

    # Импорт здесь: api.telegram тянет requests и токен, а для dry-run
    # они не нужны.
    from api.telegram import send
    for chunk in report_chunks(text):
        result = send(OWNER_CHAT_ID, chunk)
        if not result.get("ok"):
            print("не отправлено")
            return 1
    print("отправлено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
