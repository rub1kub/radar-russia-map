"""Тормоз на повторные предупреждения без отбоя между ними.

«Опасность» и «тревога» — классы-прогнозы, не подтверждённые наблюдения
(см. CLAUDE.md: «прогноз… — не фиксация»). Вторая волна к тому же месту
и той же угрозе рождает НОВОЕ событие (fuse.SAME_ZONE_WINDOW — 15 минут,
а не «пока не кончится»), и подписчик получал два «Краснодар — объявлена
опасность атаки БПЛА» подряд с разницей в 24 минуты без единого отбоя
между ними — та же новость дважды, а не две новости.

Гасится только точное повторение ТОГО ЖЕ класса («опасность» вслед за
«опасностью»): эскалация до тревоги — другой класс, другая новость, и
проходит всегда, в любую сторону. Наблюдения (фиксация, перехват, взрыв)
тормозом не накрыты: каждое — новый факт. Отбой снимает тормоз —
следующая опасность по тому же месту и угрозе снова придёт как новость.

Общая таблица на боевой базе: бот и веб-пуш — разные подписчики, но одна
картина обстановки, значит один тормоз.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

# Класс-прогноз: предупреждает о возможном, не сообщает увиденное.
FORECAST_SIGNALS = {"alarm", "danger"}

# Тот же интервал, за который событие уровня района гаснет на карте
# (pipeline.fuse.FADE_AFTER) — район борт пересекает примерно за это
# время. Не «запас», а тот же наблюдаемый темп, на который откалиброван
# конвейер: используем готовое число вместо нового произвольного.
COOLDOWN_SEC = int(timedelta(minutes=45).total_seconds())

SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_cooldown (
    subscriber TEXT NOT NULL,
    zone_key   TEXT NOT NULL,
    signal     TEXT NOT NULL,
    sent_at    INTEGER NOT NULL,
    PRIMARY KEY (subscriber, zone_key)
);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def _zone_key(event: dict) -> str:
    # zone_id — самая мелкая зона события; в проде есть всегда, но на
    # случай пробела берём голову zone_path.
    zone = event.get("zone_id") or next(
        iter(event.get("zone_path") or []), "")
    return f"{event.get('threat_type') or 'unknown'}:{zone}"


def should_suppress(connection: sqlite3.Connection, subscriber: str,
                    event: dict, now: int) -> bool:
    """Гасить ли эту опасность/тревогу — тот же класс уже был недавно."""
    signal = event.get("signal_type")
    if event.get("status") == "resolved" or signal not in FORECAST_SIGNALS:
        return False
    row = connection.execute(
        "SELECT signal, sent_at FROM notify_cooldown"
        " WHERE subscriber = ? AND zone_key = ?",
        (subscriber, _zone_key(event))).fetchone()
    if row is None or now - row["sent_at"] > COOLDOWN_SEC:
        return False
    return row["signal"] == signal


def record_sent(connection: sqlite3.Connection, subscriber: str,
                event: dict, now: int) -> None:
    """После настоящей отправки: запомнить прогноз или снять тормоз.

    Отбой и подтверждённое наблюдение удаляют строку — не продлевают
    тормоз, а гасят его: следующая опасность снова будет новостью.
    """
    key = _zone_key(event)
    signal = event.get("signal_type")
    if event.get("status") == "resolved" or signal not in FORECAST_SIGNALS:
        connection.execute(
            "DELETE FROM notify_cooldown WHERE subscriber = ? AND zone_key = ?",
            (subscriber, key))
        return
    connection.execute(
        "INSERT INTO notify_cooldown (subscriber, zone_key, signal, sent_at)"
        " VALUES (?,?,?,?)"
        " ON CONFLICT(subscriber, zone_key) DO UPDATE SET"
        "   signal = excluded.signal, sent_at = excluded.sent_at",
        (subscriber, key, signal, now))
