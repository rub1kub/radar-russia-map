"""Полный переразбор: raw_messages -> events + event_sources.

    ingest/.venv/bin/python -m pipeline.rebuild

Сырые сообщения не трогаются, производные таблицы пересобираются целиком.
Так и должно быть: парсер будет меняться постоянно.
"""

from __future__ import annotations

from dataclasses import replace

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))

from config import sources_from_env  # noqa: E402

from .db import connect, counts, reset_derived  # noqa: E402
from .fuse import Fuser  # noqa: E402
from .geocode import (Geocoder, Resolved, coarsen_intercept,  # noqa: E402
                      destination_zone_ids, forecast_zone_ids)
from .networks import load_networks  # noqa: E402
from .parse import (MAX_RESOLVED_ZONES, block_signals, parse,  # noqa: E402
                    phrase_signals, signal_for_place)
from .routes import extract_route, store_route  # noqa: E402
from .source_policy import accepts_observation  # noqa: E402
from .timeutil import now_utc, parse_utc  # noqa: E402
from .source_region import build_fallback, explicit_home_region  # noqa: E402

TIERS = {source.key: source.tier for source in sources_from_env()}
NETWORKS = {source.key: source.network for source in sources_from_env()}
USERNAME_TO_KEY = {source.username: source.key for source in sources_from_env()}
STRICT_ALERTS = {source.key for source in sources_from_env() if source.strict_alerts}


def resolve_networks(connection) -> dict[str, str | None]:
    """Сеть канала: сначала вычисленная по совпадениям, потом из конфига.

    Шаблон названия ловит только явные семейства клонов. Каналы одной
    редакции с разными названиями видно лишь по тому, что они дословно
    перепечатывают друг друга.
    """
    measured = load_networks(connection)
    return {key: measured.get(key) or NETWORKS.get(key) for key in
            set(measured) | set(NETWORKS)}


def import_jsonl(connection, raw_dir: Path) -> int:
    """Залить выборки ingest/data/raw/*.jsonl в raw_messages."""
    added = 0
    for path in sorted(raw_dir.glob("*.jsonl")):
        fallback_key = USERNAME_TO_KEY.get(path.stem, path.stem)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("text") or not row.get("date"):
                continue
            # live.jsonl содержит сообщения всех каналов вперемешку и несёт
            # собственное поле source. Без него всё живое падало в
            # несуществующий источник "live", задваивая сообщения и ломая
            # и подтверждение между лентами, и аналитику источников.
            source_key = row.get("source") or fallback_key
            cursor = connection.execute(
                "INSERT OR IGNORE INTO raw_messages"
                " (source_key, chat_id, message_id, posted_at, received_at, text, views)"
                " VALUES (?,?,?,?,?,?,?)",
                # Метка нормализуется на входе: в JSONL могут лежать записи
                # старого формата — наивное локальное время.
                (source_key, row.get("chat_id"), row["message_id"],
                 parse_utc(row["date"]).isoformat(),
                 row.get("received_at"), row["text"], row.get("views")),
            )
            added += cursor.rowcount
    connection.commit()
    return added


def rebuild(connection) -> dict:
    reset_derived(connection)
    geocoder = Geocoder(connection)
    fallback = build_fallback(connection, sources_from_env())
    networks = resolve_networks(connection)
    fuser = Fuser()

    rows = connection.execute(
        "SELECT id, source_key, posted_at, text FROM raw_messages ORDER BY posted_at"
    ).fetchall()

    stats = {"messages": len(rows), "irrelevant": 0, "ungeocoded": 0, "observations": 0}

    for row in rows:
        observation = parse(row["text"])
        if not accepts_observation(
            observation, strict=row["source_key"] in STRICT_ALERTS
        ):
            stats["irrelevant"] += 1
            continue

        # Регион источника передаётся и в сам разбор: он разводит тёзок,
        # когда сообщение своего региона не назвало.
        home = fallback.get(row["source_key"])
        candidates = geocoder.resolve(observation.place_phrases, home=home)
        regional_clear = explicit_home_region(observation, candidates, home)
        resolved = (
            [regional_clear]
            if regional_clear is not None
            else geocoder.drop_covered(candidates)
        )
        # Каталог городов, а не оповещение — см. такой же страж в incremental.
        if len(resolved) > MAX_RESOLVED_ZONES:
            stats["catalog"] = stats.get("catalog", 0) + 1
            continue
        if not resolved:
            # Часть каналов не называет место: регион зашит в имя канала.
            # Такое событие кладём на регион источника — грубее, чем район
            # из текста, но лучше, чем потерять оповещение целиком.
            zone_id = home
            if not zone_id:
                stats["ungeocoded"] += 1
                continue
            zone = geocoder.zones[zone_id]
            resolved = [Resolved(zone_id, "region", zone["name_ru"],
                                 zone["lat"], zone["lon"], "источник")]
            stats["by_source_region"] = stats.get("by_source_region", 0) + 1

        # Маршрут, описанный самим сообщением: линию утверждает источник.
        route = extract_route(row["text"], observation, resolved,
                              geocoder.sea_ids)
        if route:
            store_route(connection, row["id"], row["source_key"],
                        row["posted_at"], observation, route)

        moment = parse_utc(row["posted_at"])

        # Зона-адресат («далее в направлении X») борт ещё не видит:
        # ей достаётся «опасность», а не сигнал сообщения.
        targets = (destination_zone_ids(geocoder, observation.place_phrases, home)
                   if observation.severity > 5 else set())
        # Места из оговорки о возможном («в случае прорыва ожидаются цели
        # от…») — тоже адресаты предупреждения, но по другому признаку:
        # там речь про условие, а не про направление.
        ahead = (forecast_zone_ids(geocoder, observation.place_phrases, home)
                 if observation.severity > 5 else set())

        # Сообщение может называть несколько независимых мест — каждое
        # становится отдельным наблюдением в своей зоне.
        # Лента-дайджест: у каждой фразы свой сигнал, иначе перехват из
        # одного куска красит все места сообщения. Телеграфный формат
        # делится не запятыми, а пустой строкой — тогда блоками.
        segments = (phrase_signals(observation.place_phrases)
                    or block_signals(observation.body))

        for item in resolved:
            local = observation
            own = signal_for_place(segments, item.phrase)
            # «Внимание!» адресатам — класс, который на карту не идёт:
            # решение владельца, см. правила проекта.
            if own and own[0] == "caution":
                stats["as_attention"] = stats.get("as_attention", 0) + 1
                continue
            if own and own[0] != observation.signal_type:
                local = replace(observation, signal_type=own[0],
                                severity=own[1])
                stats["as_segment"] = stats.get("as_segment", 0) + 1
            # Работа ПВО публикуется районом, не точкой — см. coarsen_intercept.
            if local.signal_type == "intercept":
                item = coarsen_intercept(geocoder, item)
            # Место, названное только внутри оговорки о возможном, получает
            # предупреждение, а не сигнал сообщения: «уничтожена группа над
            # Сочи… в случае прорыва ожидаются цели от Анапы» — над Анапой
            # ничего не сбивали.
            if item.zone_id in ahead:
                local = replace(observation, signal_type="danger", severity=5)
                stats["as_forecast"] = stats.get("as_forecast", 0) + 1
            elif item.zone_id in targets:
                local = replace(observation, signal_type="danger", severity=5)
                stats["as_destination"] = stats.get("as_destination", 0) + 1
            fuser.add(
                raw_id=row["id"],
                source_key=row["source_key"],
                tier=TIERS.get(row["source_key"], "regional"),
                network=networks.get(row["source_key"]),
                moment=moment,
                observation=local,
                zone_path=geocoder.zone_path(item.zone_id),
                lat=item.lat,
                lon=item.lon,
                level=item.level,
            )
            stats["observations"] += 1

    now = now_utc()
    for event in fuser.events:
        connection.execute(
            "INSERT INTO events (id, first_seen_at, last_seen_at, resolved_at, status,"
            " signal_type, threat_type, severity, confidence, source_count, zone_id,"
            " zone_path, lat, lon, accuracy_m, direction_deg, target_count, massive)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event.id, event.first_seen.isoformat(), event.last_seen.isoformat(),
             event.resolved_at.isoformat() if event.resolved_at else None,
             event.status(now), event.signal_type, event.threat_type, event.severity,
             event.confidence, event.independent_sources, event.zone_id,
             json.dumps(event.zone_path, ensure_ascii=False), event.lat, event.lon,
             event.accuracy_m, event.direction_deg, event.target_count,
             int(event.massive)),
        )
        for raw_id, source_key, role, contributed in event.contributions:
            connection.execute(
                "INSERT OR IGNORE INTO event_sources"
                " (event_id, raw_message_id, source_key, contributed_at, role)"
                " VALUES (?,?,?,?,?)",
                (event.id, raw_id, source_key, contributed.isoformat(), role),
            )
    connection.commit()

    stats["events"] = len(fuser.events)
    stats["multi_source"] = sum(1 for event in fuser.events if len(event.sources) > 1)
    return stats


if __name__ == "__main__":
    connection = connect()
    raw_dir = Path(__file__).resolve().parent.parent / "ingest" / "data" / "raw"
    imported = import_jsonl(connection, raw_dir)
    print(f"импортировано новых сообщений: {imported}")

    stats = rebuild(connection)
    print("\nразбор:", stats)
    print("таблицы:", counts(connection))

    print("\nсобытия с наибольшим подтверждением:")
    for row in connection.execute("""
        SELECT e.severity, e.confidence, e.source_count, e.threat_type, e.signal_type,
               z.name_ru, z.level, e.first_seen_at
        FROM events e JOIN zones z ON z.id = e.zone_id
        ORDER BY e.source_count DESC, e.confidence DESC LIMIT 10
    """):
        print(f"  [{row['source_count']} источн. conf={row['confidence']:.2f} sev={row['severity']}] "
              f"{row['signal_type']}/{row['threat_type']} — {row['name_ru']} ({row['level']}) "
              f"{row['first_seen_at'][11:16]}")
