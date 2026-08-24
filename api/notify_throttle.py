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
from datetime import datetime, timedelta

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


# Свежесть уведомления. После простоя сбор дочитывает историю, конвейер
# проигрывает её в ускоренной перемотке, и рассыльщик 24.08 доставил
# «опасность 11:23» и её же «отбой 11:44» пачкой в 12:13 — как живые.
# Уведомление имеет смысл только про «прямо сейчас»: событие старше этого
# порога — догонка, а не эфир. В живом режиме события моложе минуты, и
# порог ни одно настоящее уведомление не задевает.
NOTIFY_FRESH_SEC = 15 * 60


def is_stale(event: dict, now: int) -> bool:
    """Событие из догонки истории, а не из живого эфира."""
    stamp = (event.get("resolved_at") if event.get("status") == "resolved"
             else event.get("last_seen_at")) or event.get("last_seen_at")
    if not stamp:
        return False
    try:
        moment = datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return False
    return now - moment > NOTIFY_FRESH_SEC


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


def matches_watch(connection: sqlite3.Connection, zones: set[str],
                  event: dict) -> bool:
    """Событие относится к подписке: строго вниз, никогда вверх.

    Подписка на регион видит событие в конкретном районе внутри него —
    пересечение подписки с zone_path события (цепочкой ОТ события до
    региона). Обратного НЕТ: подписка на город не получает событий с
    региональной привязкой ни для какого класса — решение владельца
    23.08 после двух неудачных «улучшений». Сирены объявляются по
    городам, а не по краям; событие «тревога» с одной лишь краевой
    привязкой — небрежная атрибуция источника или огрех геокодинга, и
    чинить его надо в разборе (класть на названный город), а не
    расширением подписок.

    Аргумент connection не используется, но сохранён: сигнатура общая
    для бота и веб-пуша, а решение о границах живёт в одном месте.
    """
    del connection
    return bool(zones.intersection(event.get("zone_path") or []))


def _zone_key(event: dict) -> str:
    # zone_id — самая мелкая зона события; в проде есть всегда, но на
    # случай пробела берём голову zone_path.
    zone = event.get("zone_id") or next(
        iter(event.get("zone_path") or []), "")
    return f"{event.get('threat_type') or 'unknown'}:{zone}"


# Пара «закрыто — открыто»: аэропорты и мост. Одна новость «аэропорт
# Краснодар (Пашковский) закрыт» рождает события и на посёлке-аэропорте,
# и на городском округе — каналы называют его то так, то так, — и
# подписчик получал «закрыт» дважды за 13 минут. Здесь родство зон
# гасит в ОБЕ стороны (это один и тот же аэропорт), а открытие — не
# сброс тормоза, а собственное сообщение со своим тормозом: иначе
# «открыт» приходил бы той же парой дублей.
PAIRED_SIGNALS = {"infra"}


def _paired_marker(event: dict) -> str:
    return ("infra:open" if event.get("status") == "resolved" else "infra")


def _related(connection: sqlite3.Connection, one: str, other: str) -> bool:
    """Зоны об одном месте: совпадают или одна — родитель другой."""
    if not one or not other:
        return False
    return (one == other or other in _ancestors(connection, one)
            or one in _ancestors(connection, other))


def should_suppress(connection: sqlite3.Connection, subscriber: str,
                    event: dict, now: int) -> bool:
    """Гасить ли это уведомление — та же новость уже уходила недавно.

    Прогнозы (опасность, тревога): сверка не только по точному совпадению
    зоны — «Краснодар — опасность атаки БПЛА» в 10:40 и «Краснодарский
    край — опасность атаки БПЛА» в 11:22 — одна новость. Более широкое
    объявление после уже присланного узкого гасится; обратный порядок
    (край, потом конкретно его город) проходит: сужение до твоего места —
    новость.

    Пары «закрыто — открыто» (аэропорты, мост): родство зон гасит в обе
    стороны — «Пашковский закрыт» и «Краснодар закрыт» это один аэропорт,
    в каком бы порядке ленты его ни назвали. Открытие гасится отдельно от
    закрытия: у него свой маркер.
    """
    signal = event.get("signal_type")
    threat = event.get("threat_type") or "unknown"
    event_zone = event.get("zone_id") or next(
        iter(event.get("zone_path") or []), "")

    if signal in PAIRED_SIGNALS:
        marker = _paired_marker(event)
        rows = connection.execute(
            "SELECT zone_key, signal, sent_at FROM notify_cooldown"
            " WHERE subscriber = ?", (subscriber,)).fetchall()
        for row in rows:
            if now - row["sent_at"] > COOLDOWN_SEC or row["signal"] != marker:
                continue
            sent_threat, _, sent_zone = row["zone_key"].partition(":")
            if sent_threat != threat:
                continue
            if _related(connection, sent_zone, event_zone):
                return True
        return False

    if event.get("status") == "resolved" or signal not in FORECAST_SIGNALS:
        return False
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
    Пары «закрыто — открыто» вместо удаления пишут собственный маркер:
    открытие аэропорта — само сообщение с дублями, которые надо гасить.
    """
    key = _zone_key(event)
    signal = event.get("signal_type")
    if signal in PAIRED_SIGNALS:
        connection.execute(
            "INSERT INTO notify_cooldown (subscriber, zone_key, signal, sent_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(subscriber, zone_key) DO UPDATE SET"
            "   signal = excluded.signal, sent_at = excluded.sent_at",
            (subscriber, key, _paired_marker(event), now))
        return
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
