"""Срок хранения сырых сообщений и журнала бота.

    ingest/.venv/bin/python -m pipeline.retention                # показать
    ingest/.venv/bin/python -m pipeline.retention --apply        # удалить
    ingest/.venv/bin/python -m pipeline.retention --days 30 --apply

Зачем это нужно. raw_messages хранит дословные тексты чужих каналов, и
хранились они бессрочно просто потому, что никто не решил иначе. Это и
правовой вопрос (чужой контент), и практический: корпус растёт линейно, а
пользы от сообщения полугодовой давности для карты обстановки нет.

Что удаляется и что нет. Удаляются только сырые сообщения старше срока.
События и их провенанс остаются: они уже обезличены до «зона, тип, время,
сколько источников подтвердило», и именно на них построена история.
Ссылка event_sources -> raw_messages при этом повиснет, поэтому строки
провенанса для удалённых сообщений снимаются тоже — событие сохраняет
счётчик источников, но перестаёт указывать на конкретный текст.

Побочное следствие, которое надо понимать: после удаления полный
переразбор pipeline.rebuild восстановит только события свежее срока.
Более старые события останутся в базе такими, какими их посчитал разбор
на момент сбора, и переразбором уже не обновятся.

Заодно подрезается tg_activity — журнал команд бота и открытий мини-аппа.
Срок у него свой (--activity-days) и с корпусом не связан: там чужие
тексты, а тут наша собственная статистика, и укорачивать её до тридцати
суток заодно с разовой чисткой корпуса незачем.
"""

from __future__ import annotations

import argparse
from datetime import timedelta

from .db import connect
from .timeutil import now_utc

# Срок по умолчанию. Девяносто суток: перекрывает сезонность и любые разборы
# постфактум, но не превращает базу в бессрочный архив чужих текстов.
DEFAULT_DAYS = 90
# Журнал бота. Тот же горизонт, но отдельным числом: см. шапку модуля.
DEFAULT_ACTIVITY_DAYS = 90


def has_table(connection, name: str) -> bool:
    """Таблицы бота создаёт api/telegram.py при первом запросе.

    В базе, где бот ни разу не поднимался (и в тестах конвейера), их
    просто нет — чистка не должна на этом падать.
    """
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,)).fetchone() is not None


def activity_stats(connection, cutoff_iso: str) -> dict:
    if not has_table(connection, "tg_activity"):
        return {"activity_total": 0, "activity_older": 0}
    return {
        "activity_total": connection.execute(
            "SELECT COUNT(*) n FROM tg_activity").fetchone()["n"],
        "activity_older": connection.execute(
            "SELECT COUNT(*) n FROM tg_activity WHERE at < ?",
            (cutoff_iso,)).fetchone()["n"],
    }


def purge_activity(connection, cutoff_iso: str) -> int:
    """Снять старые записи журнала. Возвращает сколько удалено."""
    if not has_table(connection, "tg_activity"):
        return 0
    removed = connection.execute(
        "DELETE FROM tg_activity WHERE at < ?", (cutoff_iso,)).rowcount
    connection.commit()
    return removed


def stats(connection, cutoff_iso: str) -> dict:
    total = connection.execute("SELECT COUNT(*) n FROM raw_messages").fetchone()["n"]
    old = connection.execute(
        "SELECT COUNT(*) n FROM raw_messages WHERE posted_at < ?", (cutoff_iso,)
    ).fetchone()["n"]
    provenance = connection.execute(
        "SELECT COUNT(*) n FROM event_sources WHERE raw_message_id IN"
        " (SELECT id FROM raw_messages WHERE posted_at < ?)",
        (cutoff_iso,),
    ).fetchone()["n"]
    oldest = connection.execute("SELECT MIN(posted_at) m FROM raw_messages").fetchone()["m"]
    return {
        "messages_total": total,
        "messages_older": old,
        "provenance_rows": provenance,
        "oldest": oldest,
    }


def purge(connection, cutoff_iso: str) -> dict:
    before = stats(connection, cutoff_iso)
    # Провенанс и маршруты снимаются первыми: внешний ключ не даст удалить
    # сообщение, на которое ещё ссылаются.
    connection.execute(
        "DELETE FROM event_sources WHERE raw_message_id IN"
        " (SELECT id FROM raw_messages WHERE posted_at < ?)",
        (cutoff_iso,),
    )
    connection.execute("DELETE FROM routes WHERE posted_at < ?", (cutoff_iso,))
    connection.execute("DELETE FROM raw_messages WHERE posted_at < ?", (cutoff_iso,))
    connection.commit()
    connection.execute("VACUUM")
    return before


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Очистка сырых сообщений и журнала бота по сроку")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--activity-days", type=int, default=DEFAULT_ACTIVITY_DAYS,
                        help="срок журнала бота tg_activity, по умолчанию 90")
    parser.add_argument("--apply", action="store_true",
                        help="удалить; без флага только показывает объём")
    args = parser.parse_args()

    if args.days < 7:
        parser.error("срок меньше недели ломает разбор: события живут до трёх часов, "
                     "но переразбор корпуса нужен на большем окне")
    if args.activity_days < 7:
        parser.error("журнал короче недели бесполезен: по нему смотрят, "
                     "живёт ли бот вообще")

    connection = connect()
    connection.execute("PRAGMA busy_timeout = 5000")
    now = now_utc()
    cutoff = (now - timedelta(days=args.days)).isoformat()
    activity_cutoff = (now - timedelta(days=args.activity_days)).isoformat()

    numbers = stats(connection, cutoff)
    journal = activity_stats(connection, activity_cutoff)
    print(f"срок хранения: {args.days} суток, граница {cutoff[:16]}")
    print(f"  сообщений всего:        {numbers['messages_total']}")
    print(f"  старше срока:           {numbers['messages_older']}")
    print(f"  строк провенанса с ними:{numbers['provenance_rows']:>5}")
    print(f"  самое старое:           {(numbers['oldest'] or '—')[:16]}")
    print(f"журнал бота: {args.activity_days} суток, граница {activity_cutoff[:16]}")
    print(f"  записей всего:          {journal['activity_total']}")
    print(f"  старше срока:           {journal['activity_older']}")

    if not args.apply:
        print("\nничего не удалено: запустите с --apply")
        return 0

    # Журнал первым: корпусная чистка заканчивается VACUUM, и место,
    # освобождённое обоими удалениями, возвращается файлу за один проход.
    removed_activity = purge_activity(connection, activity_cutoff)

    if not numbers["messages_older"]:
        print(f"\nкорпус чистить нечего; журнал: удалено {removed_activity}")
        return 0

    purge(connection, cutoff)
    after = connection.execute("SELECT COUNT(*) n FROM raw_messages").fetchone()["n"]
    print(f"\nудалено {numbers['messages_older']} сообщений, осталось {after}")
    print(f"журнал: удалено {removed_activity}, "
          f"осталось {journal['activity_total'] - removed_activity}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
