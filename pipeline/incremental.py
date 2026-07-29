"""Инкрементальный проход: только новые сообщения, ничего не удаляя.

    ingest/.venv/bin/python -m pipeline.incremental            # один проход
    ingest/.venv/bin/python -m pipeline.incremental --loop 15  # каждые 15 секунд

pipeline.rebuild собирает events с нуля через reset_derived(). При живом сборе
это гонка: пока идет пересборка, API видит пустую таблицу, а сообщения,
пришедшие в это время, до следующего запуска не разбираются. Здесь производные
таблицы не чистятся — читаем хвост raw_messages после отметки, доливаем
наблюдения в уже открытые события и пишем результат через UPSERT.

Логика разбора и слияния не дублируется: parse/Geocoder/Fuser те же самые,
что и в rebuild. Отличается только то, откуда берется начальное состояние
Fuser и как результат попадает в базу.

Хвост ленты придерживается на LATE_GRACE: каналы приходят к слушателю не в
порядке публикации, а Fuser не умеет присоединять наблюдение, которое старше
last_seen события. Без задержки опоздавшее на секунду сообщение порождало бы
второе событие вместо подтверждения первого — см. ready_batch.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))

from config import sources_from_env  # noqa: E402

from .db import connect  # noqa: E402
from .fuse import CLEAR_ECHO, Event, Fuser  # noqa: E402
from .geocode import Geocoder, Resolved  # noqa: E402
from .networks import load_networks  # noqa: E402
from .parse import parse, strip_footer  # noqa: E402
from .routes import extract_route, store_route  # noqa: E402
from .source_region import build_fallback  # noqa: E402
from .timeutil import now_utc, parse_utc  # noqa: E402

TIERS = {source.key: source.tier for source in sources_from_env()}
NETWORKS = {source.key: source.network for source in sources_from_env()}


def resolve_networks(connection) -> dict[str, str | None]:
    """Сеть канала: сначала вычисленная по совпадениям, потом из конфига.

    Шаблон названия ловит только явные семейства клонов. Каналы одной
    редакции с разными названиями видно лишь по тому, что они дословно
    перепечатывают друг друга.
    """
    measured = load_networks(connection)
    return {key: measured.get(key) or NETWORKS.get(key) for key in
            set(measured) | set(NETWORKS)}

DEFAULT_TIER = "regional"

# Отметка о последнем разобранном сообщении. Отдельная таблица, а не колонка в
# raw_messages: сырые данные неизменяемы, состояние конвейера к ним не относится.
STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

WATERMARK_KEY = "incremental.last_raw_id"

# Сколько ждать опоздавших, прежде чем считать участок ленты устоявшимся.
# Порядок id (приход к слушателю) не совпадает с порядком posted_at: на живой
# выборке соседние сообщения разных каналов расходились на 1-4 секунды. Fuser
# отбрасывает наблюдение старше last_seen события (Fuser._match, gap < 0),
# поэтому разбор такой пары в разных проходах разрывал событие надвое и терял
# подтверждения: событие с 7 источниками показывалось как одиночное.
#
# Плата за окно — событие появляется на карте на столько же позже, поэтому
# берется не с потолка: на живой выборке разбор совпадает с полным rebuild
# уже при 5 секундах, 30 дают запас в шесть раз. Увеличивать стоит только
# если в ленте появятся каналы с заметной задержкой доставки.
LATE_GRACE = timedelta(seconds=30)

# Открытым считается событие, которое еще может принять подтверждение.
OPEN_EVENTS_SQL = """
SELECT id, first_seen_at, last_seen_at, signal_type, threat_type, severity,
       zone_id, zone_path, lat, lon, accuracy_m, direction_deg, target_count
FROM events
WHERE status IN ('active', 'fading') AND resolved_at IS NULL
-- Порядок повторяет Fuser._open: события лежат в порядке появления. Внутри
-- одной секунды тай-брейк по rowid — это порядок первой записи строки, то
-- есть тот же порядок создания. Сортировка по id развалила бы его на хеши, а
-- _match при совпадении по родительской зоне берет первое подходящее с конца.
ORDER BY first_seen_at, rowid
"""

# Роль resolve означает отбой: такое сообщение закрывает событие, но источником
# подтверждения не считается (см. Fuser.add) — иначе confidence будет завышен.
OPEN_SOURCES_SQL = """
SELECT s.event_id AS event_id, s.source_key AS source_key, m.text AS text
FROM event_sources s
JOIN events e ON e.id = s.event_id
LEFT JOIN raw_messages m ON m.id = s.raw_message_id
WHERE e.status IN ('active', 'fading') AND e.resolved_at IS NULL
  AND s.role <> 'resolve'
ORDER BY s.contributed_at, s.raw_message_id
"""

UPSERT_EVENT_SQL = """
INSERT INTO events (id, first_seen_at, last_seen_at, resolved_at, status,
                    signal_type, threat_type, severity, confidence, source_count,
                    zone_id, zone_path, lat, lon, accuracy_m, direction_deg,
                    target_count)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(id) DO UPDATE SET
    -- Монотонные поля берутся с запасом: событие может только расширяться во
    -- времени и по числу подтверждений, сужаться ему нечем.
    first_seen_at = min(events.first_seen_at, excluded.first_seen_at),
    last_seen_at  = max(events.last_seen_at,  excluded.last_seen_at),
    severity      = max(events.severity,      excluded.severity),
    source_count  = max(events.source_count,  excluded.source_count),
    confidence    = max(events.confidence,    excluded.confidence),
    -- Однажды объявленный отбой не отменяется поздним подтверждением.
    resolved_at   = COALESCE(events.resolved_at, excluded.resolved_at),
    status        = CASE
                        WHEN COALESCE(events.resolved_at, excluded.resolved_at)
                             IS NOT NULL THEN 'resolved'
                        ELSE excluded.status
                    END,
    signal_type   = excluded.signal_type,
    threat_type   = excluded.threat_type,
    zone_id       = excluded.zone_id,
    zone_path     = excluded.zone_path,
    lat           = excluded.lat,
    lon           = excluded.lon,
    accuracy_m    = excluded.accuracy_m,
    direction_deg = excluded.direction_deg,
    target_count  = excluded.target_count
"""


# --- Отметка о прогрессе ----------------------------------------------------

def ensure_state(connection: sqlite3.Connection) -> None:
    # Именно execute, а не executescript: последний перед запуском делает
    # неявный COMMIT и разорвал бы транзакцию прохода.
    connection.execute(STATE_SCHEMA)


def read_watermark(connection: sqlite3.Connection) -> int:
    """Id последнего разобранного сообщения.

    Отметки нет — значит, инкрементальный проход запускается впервые поверх
    базы, собранной rebuild. Начинать с нуля нельзя: повторный разбор всей
    истории при живых открытых событиях плодит дубликаты. Точка отсчета —
    последнее сообщение, давшее вклад в событие. Все, что после него, либо
    пришло уже после пересборки, либо было отброшено как нерелевантное, и
    повторный разбор такого сообщения ничего не меняет.

    Та же граница работает и как нижний предел для сохраненной отметки.
    reset_derived() чистит events и event_sources, но не pipeline_state,
    поэтому после rebuild в таблице остается отметка, отставшая от разобранного.
    Взяли бы ее как есть — разобрали хвост повторно, и подтверждение, чье
    событие уже успело закрыться, легло бы отдельным дублем.
    """
    seeded = connection.execute(
        "SELECT COALESCE(MAX(raw_message_id), 0) AS last FROM event_sources"
    ).fetchone()["last"]

    row = connection.execute(
        "SELECT value FROM pipeline_state WHERE key = ?", (WATERMARK_KEY,)
    ).fetchone()
    stored = int(row["value"]) if row is not None else 0
    return max(stored, int(seeded))


def write_watermark(connection: sqlite3.Connection, value: int) -> None:
    connection.execute(
        "INSERT INTO pipeline_state (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (WATERMARK_KEY, str(value)),
    )


# --- Отбор устоявшегося участка ленты ---------------------------------------

def ready_batch(
    rows: list[sqlite3.Row], now: datetime, grace: timedelta = LATE_GRACE
) -> tuple[list[sqlite3.Row], int | None]:
    """Отделить устоявшийся хвост ленты от еще догоняющих сообщений.

    Возвращает пачку к разбору и новую отметку (None — двигать нечего).

    Разбирается только непрерывный по id префикс: сообщение считается
    устоявшимся, если с момента его публикации прошло больше grace. Как только
    встретилось неустоявшееся, все следующие по id придерживаются тоже, даже
    если сами уже старые — иначе отметка перескочила бы через них и сообщение
    пропало бы навсегда. Внутри пачки порядок остается по posted_at, так что
    пришедшие не по порядку соседи попадают в один проход и сливаются как при
    полном rebuild.

    Отсчет ведется от большего из now и самой свежей публикации: при заливке
    старой истории now далеко впереди и вся пачка сразу считается устоявшейся,
    а при часах, отставших от ленты, горизонт задает сама лента.
    """
    if not rows:
        return [], None

    horizon = max([parse_utc(row["posted_at"]) for row in rows] + [now])
    cutoff = horizon - grace

    late = [row["id"] for row in rows if parse_utc(row["posted_at"]) > cutoff]
    if not late:
        return list(rows), max(row["id"] for row in rows)

    stop = min(late)
    return [row for row in rows if row["id"] < stop], stop - 1


# --- Восстановление состояния слияния ---------------------------------------

def load_open_events(
    connection: sqlite3.Connection,
    tiers: dict[str, str] | None = None,
    networks: dict[str, str | None] | None = None,
) -> list[Event]:
    """Поднять открытые события из базы обратно в структуры Fuser.

    Без этого шага новое сообщение не находит, к чему присоединиться, и
    создает второе событие в той же зоне. Особенно важны sources: confidence
    считается как объединение независимых свидетельств, и событие, потерявшее
    список подтвердивших каналов, после первого же дозаписанного наблюдения
    откатилось бы к достоверности одиночного источника.
    """
    tiers = TIERS if tiers is None else tiers
    networks = NETWORKS if networks is None else networks

    events: dict[str, Event] = {}
    order: list[Event] = []
    for row in connection.execute(OPEN_EVENTS_SQL):
        zone_path = json.loads(row["zone_path"] or "[]") or [row["zone_id"]]
        event = Event(
            id=row["id"],
            zone_id=row["zone_id"],
            zone_path=list(zone_path),
            threat_type=row["threat_type"],
            signal_type=row["signal_type"],
            severity=row["severity"],
            first_seen=parse_utc(row["first_seen_at"]),
            last_seen=parse_utc(row["last_seen_at"]),
            resolved_at=None,
            lat=row["lat"],
            lon=row["lon"],
            accuracy_m=row["accuracy_m"] or 12_000,
            direction_deg=row["direction_deg"],
            target_count=row["target_count"],
        )
        events[event.id] = event
        order.append(event)

    if not events:
        return []

    for row in connection.execute(OPEN_SOURCES_SQL):
        event = events.get(row["event_id"])
        if event is not None:
            key = row["source_key"]
            tier = tiers.get(key, DEFAULT_TIER)
            event.sources.setdefault(key, tier)
            # Сети восстанавливаются вместе с источниками: иначе у поднятого
            # события окажется пустой networks, и первое же новое наблюдение
            # оставит в нём один голос вместо всех накопленных.
            voice = networks.get(key) or key
            event.networks.setdefault(voice, tier)
            # Вместе с голосами поднимаются и тексты: без них дословный
            # перепост, пришедший в следующем проходе, снова считался бы
            # самостоятельным свидетельством. Порядок строк — по времени
            # вклада, поэтому автором текста остаётся тот, кто сказал первым.
            # Подпись канала снимается так же, как при слиянии: перепост
            # отличается от оригинала ровно ею, и хеш по сырому тексту не
            # совпал бы сам с собой между проходами.
            stamp = Fuser._repost_key(strip_footer(row["text"] or ""))
            if stamp is not None and event.texts.setdefault(stamp, voice) != voice:
                continue
            event.voices.setdefault(voice, tier)

    # contributions намеренно остаются пустыми: уже записанные строки
    # event_sources переписывать незачем, в базу уйдут только новые.
    return order


CLEARED_SQL = """
SELECT e.id, e.zone_id, e.zone_path, e.threat_type, e.signal_type, e.severity,
       e.first_seen_at, e.last_seen_at, e.resolved_at, e.lat, e.lon,
       e.accuracy_m, e.direction_deg, e.target_count
FROM events e
WHERE e.resolved_at IS NOT NULL AND e.resolved_at >= ?
"""


def load_cleared(connection, now) -> dict[tuple[str, str], Event]:
    """Недавно закрытые события — память об отбое между проходами.

    Без неё опоздавшее сообщение об уже отменённой тревоге заводило новое
    событие, следующая копия отбоя его закрывала, и в ленте выстраивался
    ряд одинаковых отбоев по одной зоне.
    """
    since = (now - CLEAR_ECHO).isoformat()
    out: dict[tuple[str, str], Event] = {}
    for row in connection.execute(CLEARED_SQL, (since,)):
        zone_path = json.loads(row["zone_path"] or "[]") or [row["zone_id"]]
        out[(row["zone_id"], row["threat_type"])] = Event(
            id=row["id"],
            zone_id=row["zone_id"],
            zone_path=list(zone_path),
            threat_type=row["threat_type"],
            signal_type=row["signal_type"],
            severity=row["severity"],
            first_seen=parse_utc(row["first_seen_at"]),
            last_seen=parse_utc(row["last_seen_at"]),
            resolved_at=parse_utc(row["resolved_at"]),
            lat=row["lat"],
            lon=row["lon"],
            accuracy_m=row["accuracy_m"] or 12_000,
            direction_deg=row["direction_deg"],
            target_count=row["target_count"],
        )
    return out


def restore_fuser(
    connection: sqlite3.Connection,
    tiers: dict[str, str] | None = None,
    networks: dict[str, str | None] | None = None,
) -> tuple[Fuser, list[Event]]:
    """Fuser с предзаполненным окном открытых событий."""
    restored = load_open_events(connection, tiers, networks)
    fuser = Fuser()
    # Обращение к приватному полю осознанное: Fuser не имеет публичного способа
    # принять готовое состояние, а менять fuse.py ради одного вызова дороже,
    # чем зафиксировать связь здесь. events оставляем пустым — туда попадут
    # только вновь созданные события, по ним считается статистика прохода.
    fuser._open = list(restored)
    fuser._cleared = load_cleared(connection, now_utc())
    return fuser, restored


def merge_for_write(restored: list[Event], created: list[Event]) -> list[Event]:
    """Свести восстановленные и новые события к одной записи на id.

    Идентификатор строится из (зона, угроза, момент), поэтому опоздавшее
    сообщение может получить id уже существующего события, не приклеившись к
    нему: окно совпадения смотрит только вперед по времени. Без слияния такая
    запись затерла бы список источников меньшим набором.
    """
    merged: dict[str, Event] = {event.id: event for event in restored}
    result: list[Event] = list(restored)

    for event in created:
        target = merged.get(event.id)
        if target is None:
            merged[event.id] = event
            result.append(event)
            continue
        target.first_seen = min(target.first_seen, event.first_seen)
        target.last_seen = max(target.last_seen, event.last_seen)
        target.severity = max(target.severity, event.severity)
        target.resolved_at = target.resolved_at or event.resolved_at
        if event.target_count:
            target.target_count = max(target.target_count or 0, event.target_count)
        for key, tier in event.sources.items():
            target.sources.setdefault(key, tier)
        target.contributions.extend(event.contributions)

    return result


# --- Запись -----------------------------------------------------------------

def store_event(connection: sqlite3.Connection, event: Event, now: datetime) -> None:
    connection.execute(
        UPSERT_EVENT_SQL,
        (event.id, event.first_seen.isoformat(), event.last_seen.isoformat(),
         event.resolved_at.isoformat() if event.resolved_at else None,
         event.status(now), event.signal_type, event.threat_type, event.severity,
         event.confidence, event.independent_sources, event.zone_id,
         json.dumps(event.zone_path, ensure_ascii=False), event.lat, event.lon,
         event.accuracy_m, event.direction_deg, event.target_count),
    )
    for raw_id, source_key, role, contributed in event.contributions:
        connection.execute(
            "INSERT OR IGNORE INTO event_sources"
            " (event_id, raw_message_id, source_key, contributed_at, role)"
            " VALUES (?,?,?,?,?)",
            (event.id, raw_id, source_key, contributed.isoformat(), role),
        )


# --- Проход -----------------------------------------------------------------

def run_once(
    connection: sqlite3.Connection,
    *,
    geocoder: Geocoder | None = None,
    tiers: dict[str, str] | None = None,
    now: datetime | None = None,
    grace: timedelta = LATE_GRACE,
) -> dict:
    """Разобрать сообщения, появившиеся после отметки, и дописать события.

    Повторный вызов без новых сообщений не создает событий и не меняет
    счетчики: отметка сдвигается только на уже прочитанный хвост, событие
    пишется по своему id, строки провенанса — с INSERT OR IGNORE.
    """
    ensure_state(connection)
    tiers = TIERS if tiers is None else tiers
    geocoder = geocoder or Geocoder(connection)
    now = now or now_utc()

    last_id = read_watermark(connection)
    # Порядок разбора — по времени публикации, как в rebuild: окна слияния
    # смотрят вперед, и перемешанная лента дала бы другие события.
    pending = connection.execute(
        "SELECT id, source_key, posted_at, text FROM raw_messages"
        " WHERE id > ? ORDER BY posted_at, id",
        (last_id,),
    ).fetchall()

    rows, watermark = ready_batch(pending, now, grace)

    fuser, restored = restore_fuser(connection, tiers)
    # Регион источника: разводит тёзок при разборе и подхватывает сообщения
    # без единого топонима. rebuild оба правила знает давно, а живой разбор
    # молча терял «РАКЕТНАЯ ОПАСНОСТЬ!» от лент, у которых регион зашит в
    # название канала.
    fallback = build_fallback(connection, sources_from_env())

    stats = {
        "scanned": len(rows),
        "held": len(pending) - len(rows),
        "irrelevant": 0,
        "ungeocoded": 0,
        "observations": 0,
        "restored_open": len(restored),
    }

    for row in rows:
        observation = parse(row["text"])
        if not observation.relevant:
            stats["irrelevant"] += 1
            continue

        home = fallback.get(row["source_key"])
        resolved = geocoder.drop_covered(
            geocoder.resolve(observation.place_phrases, home=home))
        if not resolved:
            if home:
                zone = geocoder.zones[home]
                resolved = [Resolved(home, "region", zone["name_ru"],
                                     zone["lat"], zone["lon"], "источник")]
                stats["by_source_region"] = stats.get("by_source_region", 0) + 1
            else:
                stats["ungeocoded"] += 1
                continue

        # Маршрут, описанный самим сообщением, — отдельная строка рядом с
        # событиями: линию утверждает источник, и она живёт своей жизнью.
        route = extract_route(row["text"], observation, resolved)
        if route:
            store_route(connection, row["id"], row["source_key"],
                        row["posted_at"], observation, route)

        moment = parse_utc(row["posted_at"])

        for item in resolved:
            fuser.add(
                raw_id=row["id"],
                source_key=row["source_key"],
                tier=tiers.get(row["source_key"], DEFAULT_TIER),
                network=NETWORKS.get(row["source_key"]),
                moment=moment,
                observation=observation,
                zone_path=geocoder.zone_path(item.zone_id),
                lat=item.lat,
                lon=item.lon,
                level=item.level,
            )
            stats["observations"] += 1

    stats["new_events"] = len(fuser.events)

    # Восстановленные события переписываются даже нетронутыми: только так
    # active вовремя превращается в fading и resolved без полной пересборки.
    written = merge_for_write(restored, fuser.events)
    for event in written:
        store_event(connection, event, now)
    stats["written_events"] = len(written)

    # Отметку двигает ready_batch: она встает перед первым придержанным
    # сообщением, а не на максимум разобранных id.
    stats["last_raw_id"] = last_id if watermark is None else max(last_id, watermark)
    write_watermark(connection, stats["last_raw_id"])
    connection.commit()
    return stats


def run_loop(
    connection: sqlite3.Connection,
    interval: float,
    *,
    geocoder: Geocoder | None = None,
) -> None:
    """Проходы по расписанию. Прерывается по Ctrl+C."""
    geocoder = geocoder or Geocoder(connection)
    while True:
        try:
            stats = run_once(connection, geocoder=geocoder)
        except sqlite3.OperationalError as error:
            # Живой сбор держит запись: пропускаем такт, состояние в базе цело.
            connection.rollback()
            print(f"база занята, повтор через {interval:g} с: {error}")
        else:
            if stats["scanned"]:
                # Придержанные не показываем отдельной строкой: они появятся в
                # следующем такте как разобранные, и лог не забивается.
                print(
                    f"[{now_utc().isoformat(timespec='seconds')}] "
                    f"сообщений {stats['scanned']}, новых событий "
                    f"{stats['new_events']}, обновлено {stats['written_events']}, "
                    f"придержано {stats['held']}, отметка {stats['last_raw_id']}"
                )
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pipeline.incremental",
        description="Инкрементальный разбор новых сообщений без сброса events.",
    )
    parser.add_argument(
        "--loop",
        type=float,
        metavar="СЕК",
        default=None,
        help="повторять проход каждые СЕК секунд (по умолчанию один проход)",
    )
    args = parser.parse_args(argv)

    if args.loop is not None and args.loop <= 0:
        parser.error("--loop ожидает положительное число секунд")

    connection = connect()
    # Живой сбор пишет в ту же базу: ждем снятия блокировки вместо падения.
    connection.execute("PRAGMA busy_timeout = 5000")

    # Справочник зон загружается один раз на процесс: 200 тысяч строк на такт
    # в режиме цикла съели бы весь интервал.
    geocoder = Geocoder(connection)

    if args.loop is None:
        stats = run_once(connection, geocoder=geocoder)
        print("проход:", stats)
        return 0

    print(f"цикл каждые {args.loop:g} с, Ctrl+C для остановки")
    try:
        run_loop(connection, args.loop, geocoder=geocoder)
    except KeyboardInterrupt:
        print("\nостановлено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
