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


def _ancestors(connection: sqlite3.Connection, zone_id: str) -> set[str]:
    """Зона и цепочка её родителей вверх до региона."""
    chain: set[str] = set()
    current = zone_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        chain.add(current)
        try:
            row = connection.execute(
                "SELECT parent_id FROM zones WHERE id = ?",
                (current,)).fetchone()
        except sqlite3.OperationalError:
            # Таблицы зон нет (тестовая база без справочника) —
            # дальше подниматься некуда.
            break
        current = row["parent_id"] if row else None
    return chain


# Классы, которые ОБЪЯВЛЯЮТСЯ на территорию: тревога и опасность по краю
# касаются каждого города в нём, и их отбои — тоже. Наблюдения (перехват,
# фиксация, взрыв, инфраструктура) точечны: «работает ПВО» с одной лишь
# региональной привязкой — не «у тебя над головой», см. правило проекта
# «точечное событие не красит весь регион».
ANNOUNCED_SIGNALS = {"alarm", "danger", "allclear"}


def matches_watch(connection: sqlite3.Connection, zones: set[str],
                  event: dict) -> bool:
    """Событие относится к подписке — в любую из двух сторон.

    Вниз: подписка на регион видит событие в конкретном районе внутри
    него — исходная проверка, пересечение подписки с zone_path события
    (цепочкой ОТ события до региона).

    Вверх: подписка на город видит тревогу, объявленную сразу на весь
    край без названного района, — её zone_path состоит из одной этой
    региональной зоны и городов внутри себя не содержит. Проверяем
    отдельно: попадает ли зона САМОГО события (не вся его цепочка) в
    цепочку родителей отслеживаемой зоны. Вверх поднимаются только
    объявления (тревога, опасность, их отбои): наблюдение, привязанное
    к целому региону, — не событие в твоём городе, и «работает ПВО ·
    Краснодарский край» не должно приходить подписчику на Краснодар.

    Общее пересечение «подписка вверх ∩ zone_path события» этого не
    заменяет: у Анапы и Краснодара общий родитель — тот же край, но
    тревога в Анапе Краснодар не касается. Проверять нужно zone_id
    события отдельно, а не всю его цепочку целиком.
    """
    if zones.intersection(event.get("zone_path") or []):
        return True
    if event.get("signal_type") not in ANNOUNCED_SIGNALS:
        return False
    zone_id = event.get("zone_id")
    if not zone_id:
        return False
    return any(zone_id in _ancestors(connection, watched)
               for watched in zones)


def _zone_key(event: dict) -> str:
    # zone_id — самая мелкая зона события; в проде есть всегда, но на
    # случай пробела берём голову zone_path.
    zone = event.get("zone_id") or next(
        iter(event.get("zone_path") or []), "")
    return f"{event.get('threat_type') or 'unknown'}:{zone}"


def should_suppress(connection: sqlite3.Connection, subscriber: str,
                    event: dict, now: int) -> bool:
    """Гасить ли эту опасность/тревогу — тот же класс уже был недавно.

    Сверка не только по точному совпадению зоны: «Краснодар — опасность
    атаки БПЛА» в 10:40 и «Краснодарский край — опасность атаки БПЛА» в
    11:22 — одна новость, а не две. Более широкое объявление после уже
    присланного узкого не несёт подписчику ничего нового — гасится, если
    зона события лежит в цепочке родителей недавно отправленной. Обратный
    порядок (край, потом конкретно его город) проходит: сужение до
    твоего места — новость.
    """
    signal = event.get("signal_type")
    if event.get("status") == "resolved" or signal not in FORECAST_SIGNALS:
        return False
    threat = event.get("threat_type") or "unknown"
    event_zone = event.get("zone_id") or next(
        iter(event.get("zone_path") or []), "")
    rows = connection.execute(
        "SELECT zone_key, signal, sent_at FROM notify_cooldown"
        " WHERE subscriber = ?", (subscriber,)).fetchall()
    for row in rows:
        if now - row["sent_at"] > COOLDOWN_SEC or row["signal"] != signal:
            continue
        sent_threat, _, sent_zone = row["zone_key"].partition(":")
        if sent_threat != threat:
            continue
        if sent_zone == event_zone:
            return True
        if event_zone and event_zone in _ancestors(connection, sent_zone):
            return True
    return False


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
