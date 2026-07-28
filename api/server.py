"""Публичный API конвейера.

    ingest/.venv/bin/uvicorn api.server:app --port 8000 --reload

Публично отдаются достоверность и число подтвердивших источников, но не имена
каналов и не тексты первичных сообщений (см. docs/TARGET_ARCHITECTURE.md §8).

Переменные окружения (нужны при выкладке дальше localhost):

    RADAR_CORS_ORIGINS  разрешённые origin через запятую; по умолчанию dev-сервер Vite
    RADAR_RATE_LIMIT    запросов в минуту с одного адреса, по умолчанию 120
    RADAR_TRUST_PROXY   1, если перед сервисом стоит свой прокси и X-Forwarded-For
                        можно верить; без этого заголовок игнорируется
    RADAR_PROXY_DEPTH   сколько своих прокси стоит перед сервисом, по умолчанию 1;
                        столько последних записей X-Forwarded-For считаются
                        дописанными доверенным звеном

Без RADAR_TRUST_PROXY=1 за прокси все клиенты попадают в общую корзину лимита:
заголовку не верим, а настоящий адрес до нас не доходит.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.limits import (
    DEFAULT_PROXY_DEPTH,
    DEFAULT_RATE_LIMIT,
    RateLimiter,
    TTLCache,
    client_ip,
    cors_origins,
    env_flag,
    env_int,
)
from api.geometry import router as geo_router
from pipeline.db import DB_PATH
from pipeline.fuse import Fuser
from pipeline.incremental import resolve_networks
from pipeline.provenance import counted, walk
from ingest.config import sources_from_env
from pipeline.parse import strip_footer
from pipeline.timeutil import MSK, now_utc, parse_utc

# Тот же ключ перепоста, что и в слиянии: если считать его здесь по-своему,
# пометка в списке разойдётся с числом под заголовком.
repost_key = Fuser._repost_key


def recount_sources(events: list[dict]) -> None:
    """Проставить событиям число засчитанных источников."""
    if not events:
        return
    ids = [event["id"] for event in events]
    placeholders = ",".join("?" * len(ids))
    rows = query(
        f"""
        SELECT es.event_id, es.source_key, es.role, es.contributed_at, m.text
        FROM event_sources es
        LEFT JOIN raw_messages m ON m.id = es.raw_message_id
        WHERE es.event_id IN ({placeholders})
        ORDER BY es.contributed_at, es.raw_message_id
        """,
        tuple(ids),
    )
    by_event: dict[str, list[dict]] = {}
    for row in rows:
        by_event.setdefault(row["event_id"], []).append(row)

    networks = source_networks()
    for event in events:
        own = by_event.get(event["id"])
        if own:
            event["source_count"] = counted(own, networks)


# Имя канала нужно только для ссылки на сообщение: t.me/<канал>/<id>.
SOURCE_USERNAMES = {source.key: source.username for source in sources_from_env()}


def source_networks() -> dict[str, str | None]:
    """Сеть канала — та же, что видит конвейер при подсчёте голосов."""
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return resolve_networks(connection)

app = FastAPI(title="Radar API", version="1.0")

app.include_router(geo_router)

ACTIVE_WINDOW = timedelta(hours=6)

# Сколько отбой остаётся на виду после закрытия события.
RESOLVED_WINDOW = timedelta(minutes=30)

# Через сколько зона перестаёт гореть, если сообщений больше нет.
#
# Срок не выдуман, а посчитан: борт летит и зону покидает. Считать надо по
# тем аппаратам, которые здесь и летают, — а летают над нашей территорией
# украинские дальнобойные, и ленты их прямо называют: Хорнет (124
# упоминания в корпусе), Бобр (67), Дартс (24), Лютый (23), Рубака.
# Крейсерская у этого класса порядка 150 км/ч — «Лютый» до 190, «Бобр» и
# «Дартс» ближе к 120-150. Берём нижнюю границу: она даёт срок подлиннее,
# и ошибка идёт в сторону осторожности.
#
# Поперечник зон по нашему же справочнику: район в среднем 73 км, регион
# 416 км. Значит район аппарат пересекает за полчаса, регион — часа за
# три. Через полчаса без единого нового сообщения фиксация в районе
# означает не «он здесь», а «он был здесь и ушёл»; красить по ней нечего.
# Сроки чуть длиннее чистого времени пролёта: сообщение приходит с
# задержкой, а волна состоит не из одного борта.
ZONE_FADE_BY_LEVEL = {
    "place": timedelta(minutes=20),
    "district": timedelta(minutes=35),
    "region": timedelta(minutes=165),
}
ZONE_FADE = ZONE_FADE_BY_LEVEL["region"]

# Поправка на скорость цели относительно ударного дрона. «Нептун» и прочие
# крылатые идут около 900 км/ч — вшестеро быстрее, и зону покидают во
# столько же раз раньше; безэкипажный катер ползёт по морю и висит дольше
# всех.
THREAT_SPEED = {
    "rocket": 0.2,
    "aviation": 0.3,
    "kab": 0.4,
    "uav": 1.0,
    "fpv": 1.0,
    "bek": 2.0,
}

# Ниже этого зона не гаснет: событие ещё не закрыто, и стирать его рано.
ZONE_FADE_FLOOR = 0.12
# Меньше этого срок не берём: сообщение и так приходит с задержкой.
ZONE_FADE_MIN = timedelta(minutes=8)


def fade_window(level: str | None, threat: str | None) -> float:
    """За сколько секунд цель успевает покинуть зону такого размера."""
    base = ZONE_FADE_BY_LEVEL.get(level or "district", ZONE_FADE_BY_LEVEL["district"])
    seconds = base.total_seconds() * THREAT_SPEED.get(threat or "uav", 1.0)
    return max(ZONE_FADE_MIN.total_seconds(), seconds)


def zone_fade(last_seen: str, now: datetime,
              level: str | None = None, threat: str | None = None) -> float:
    """Насколько выцвело событие к моменту просмотра. 1.0 — только что.

    Кривая крутая в начале и пологая в конце: человеку важна разница между
    «пять минут назад» и «полчаса назад», а между «час» и «полтора» — уже
    нет. Сам срок берётся из размера зоны и скорости цели.

    Для района и ударного дрона: 5 минут — 0.62, 15 — 0.35, 35 — 0.12.
    """
    age = (now - parse_utc(last_seen)).total_seconds()
    if age <= 0:
        return 1.0
    share = min(1.0, age / fade_window(level, threat))
    return max(ZONE_FADE_FLOOR, 1.0 - share ** 0.5)

# Ответ /state пересобирается не чаще раза в 3 секунды: клиент опрашивает его
# каждые 10 секунд, websocket-цикл — каждые 5, и каждый вызов это несколько
# запросов к SQLite плюс сборка счётчиков по зонам.
STATE_TTL_SEC = 3.0
STATE_CACHE_CONTROL = "public, max-age=5, stale-while-revalidate=30"

_state_cache = TTLCache(STATE_TTL_SEC)
_limiter = RateLimiter(env_int("RADAR_RATE_LIMIT", DEFAULT_RATE_LIMIT))
_trust_proxy = env_flag("RADAR_TRUST_PROXY")
_proxy_depth = env_int("RADAR_PROXY_DEPTH", DEFAULT_PROXY_DEPTH)


# BaseHTTPMiddleware (за ним стоит @app.middleware) видит только http-scope,
# поэтому websocket /api/v1/stream под лимит не попадает — он и не должен:
# одно соединение живёт часами и запросов не генерирует.
@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path.startswith("/api/v1/"):
        retry_after = _limiter.check(client_ip(request, _trust_proxy, _proxy_depth))
        if retry_after:
            return JSONResponse(
                {"detail": "rate limit exceeded", "limit_per_min": _limiter.limit},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
    return await call_next(request)


# CORS добавляется последним, значит оказывается снаружи лимитера: браузер
# должен видеть заголовки и на ответе 429, иначе вместо кода ошибки клиент
# получит невнятный сетевой сбой.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["GET"],
    allow_headers=["*"],
)


def query(sql: str, params: tuple = ()) -> list[dict]:
    with closing(sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params)]


def latest_event_moment() -> datetime | None:
    """Когда конвейер в последний раз доводил сообщение до события.

    Сбор и разбор — разные процессы. Если разбор встал, сообщения продолжают
    копиться, и возраст по raw_messages показывал бы свежесть, которой нет.
    """
    rows = query("SELECT MAX(contributed_at) AS m FROM event_sources")
    stamp = rows[0]["m"] if rows and rows[0]["m"] else None
    return parse_utc(stamp) if stamp else None


def latest_moment() -> datetime:
    """Момент последнего сообщения — НЕ «сейчас».

    Раньше от него отсчитывалась активность, поэтому остановка сбора
    замораживала карту: старые события вечно оставались «активными».
    """
    rows = query("SELECT MAX(posted_at) AS m FROM raw_messages")
    stamp = rows[0]["m"] if rows and rows[0]["m"] else None
    return parse_utc(stamp) if stamp else now_utc()


def event_rows(since: datetime, limit: int = 400) -> list[dict]:
    rows = query(
        """
        SELECT e.id, e.first_seen_at, e.last_seen_at, e.resolved_at, e.status,
               e.signal_type, e.threat_type, e.severity, e.confidence, e.source_count,
               e.zone_id, e.zone_path, e.lat, e.lon, e.accuracy_m,
               e.direction_deg, e.target_count,
               z.name_ru AS place_name, z.level AS zone_level,
               parent.name_ru AS parent_name
        FROM events e JOIN zones z ON z.id = e.zone_id
        LEFT JOIN zones parent ON parent.id = z.parent_id
        WHERE e.last_seen_at >= ?
        ORDER BY e.last_seen_at DESC LIMIT ?
        """,
        (since.isoformat(), limit),
    )
    for row in rows:
        row["zone_path"] = json.loads(row["zone_path"] or "[]")
    return rows


def build_state() -> dict:
    """Текущая обстановка: активные события и счетчики по зонам."""
    now = now_utc()
    last_message = latest_moment()
    last_event = latest_event_moment()
    data_age_sec = max(0, int((now - last_message).total_seconds()))
    pipeline_lag_sec = (
        max(0, int((last_message - last_event).total_seconds())) if last_event else None
    )
    # Отбой человек должен увидеть. Раньше событие с отбоем просто исчезало
    # из выдачи, тревога на карте молча тускнела, и вопрос «можно уже
    # выходить?» оставался без ответа. Закрытые события держим ещё полчаса —
    # лента показывает их как отбой, карту они не красят.
    fresh = event_rows(now - ACTIVE_WINDOW)
    events = []
    # Один отбой на зону и угрозу. Отбой отменяет всё, что в этой зоне по
    # этой угрозе открыто, и закрывает разом несколько событий — в ленте
    # выстраивался ряд одинаковых отбоев, до шести подряд. Показываем самый
    # свежий: отбой это одно утверждение, сколько бы событий он ни закрыл.
    seen_clear: set[tuple[str, str]] = set()
    for row in fresh:
        if row["status"] != "resolved":
            events.append(row)
            continue
        closed = row["resolved_at"] or row["last_seen_at"]
        if not closed or now - parse_utc(closed) > RESOLVED_WINDOW:
            continue
        key = (row["zone_id"], row["threat_type"])
        if key in seen_clear:
            continue
        seen_clear.add(key)
        events.append(row)
    # Число под заголовком считается ровно тем же правилом, что и список
    # источников под ним. Раньше их считали в разных местах, и в шапке
    # стояло 20 там, где в списке набиралось 16.
    recount_sources(events)

    # Уровень зоны нужен до расчёта: от него зависит, как быстро зона гаснет.
    # Район цель пересекает за минуты, регион — за часы.
    levels: dict[str, str] = {}
    chain = {zone_id for event in events for zone_id in event["zone_path"]}
    if chain:
        marks = ",".join("?" * len(chain))
        levels = {row["id"]: row["level"] for row in
                  query(f"SELECT id, level FROM zones WHERE id IN ({marks})", tuple(chain))}

    zone_counts: dict[str, dict] = {}
    for event in events:
        # Закрытое событие остаётся в ленте как отбой, но зону не красит:
        # цвет обязан означать «сейчас», а отбой — это «уже нет».
        if event["status"] == "resolved":
            continue
        # Событие поднимается по всей цепочке родителей — регион светится,
        # если горит любое поселение внутри него.
        for zone_id in event["zone_path"]:
            bucket = zone_counts.setdefault(
                zone_id,
                {"active": 0, "own": 0, "max_severity": 0, "own_severity": 0,
                 "severity": 0, "fade": 1.0, "own_fade": 1.0, "last_active": None})
            bucket["active"] += 1

            # Цвет зоны выбирает не самое страшное событие, а самое весомое
            # сейчас: уровень, умноженный на свежесть. Двухчасовая фиксация
            # (9 x 0.33 = 3.0) уступает свежей тревоге (7 x 1.0 = 7.0), и
            # район перестаёт гореть красным из-за того, что было и прошло.
            # Раньше уровень брался максимумом по всем событиям, а свежесть —
            # по самому позднему из них, и стоило прийти любому новому
            # сообщению, как старая фиксация снова вспыхивала в полную силу.
            fade = zone_fade(event["last_seen_at"], now,
                             levels.get(zone_id), event["threat_type"])
            weight = event["severity"] * fade
            if weight > bucket["severity"] * bucket["fade"]:
                bucket["severity"] = event["severity"]
                bucket["fade"] = round(fade, 3)

            # Собственные события зоны — те, что названы именно ею, а не
            # унаследованы от района внутри. Регион с собственной тревогой
            # закрашивается на любом масштабе, иначе оповещение «по области»
            # оставляло на карте одинокую метку и ни одной закрашенной зоны.
            if zone_id == event["zone_id"]:
                bucket["own"] += 1
                # Свой уровень отдельно от общего: иначе красный район внутри
                # красил бы весь регион, хотя по области объявлена жёлтая
                # опасность, а красное — точечное и уже нарисовано районом.
                if weight > bucket["own_severity"] * bucket["own_fade"]:
                    bucket["own_severity"] = event["severity"]
                    bucket["own_fade"] = round(fade, 3)

            bucket["max_severity"] = max(bucket["max_severity"], event["severity"])
            if not bucket["last_active"] or event["last_seen_at"] > bucket["last_active"]:
                bucket["last_active"] = event["last_seen_at"]

    # source_id связывает зону с полигоном, который уже загружен клиентом:
    # это тот же id, что в public/data/regions.json и districts.json.
    if zone_counts:
        placeholders = ",".join("?" * len(zone_counts))
        meta = query(
            f"SELECT id, level, source_id, name_ru FROM zones WHERE id IN ({placeholders})",
            tuple(zone_counts),
        )
        for row in meta:
            bucket = zone_counts[row["id"]]
            bucket["level"] = row["level"]
            bucket["source_id"] = row["source_id"]
            bucket["name"] = row["name_ru"]

    return {
        "generated_at": now.isoformat(),
        "last_message_at": last_message.isoformat(),
        # Клиент обязан показать, что картинка устарела, если сбор встал.
        "data_age_sec": data_age_sec,
        "last_event_at": last_event.isoformat() if last_event else None,
        # Отставание разбора от сбора. Растёт, если incremental не работает.
        "pipeline_lag_sec": pipeline_lag_sec,
        "stale": data_age_sec > 900 or (pipeline_lag_sec or 0) > 900,
        "events": events,
        "zone_counts": zone_counts,
        "active_events": len(events),
        "active_zones": len(zone_counts),
    }


def state_snapshot() -> dict:
    """Тот же ответ, но не чаще раза в STATE_TTL_SEC.

    Кеш общий для http-запросов (они идут в пуле потоков) и для websocket-цикла,
    поэтому TTLCache берёт лок, а не полагается на GIL.
    """
    return _state_cache.get(build_state)


@app.get("/api/v1/state")
def state(response: Response) -> dict:
    # Те же заголовки ставят radar-map.ru и detector-aero.ru: браузер и CDN
    # держат ответ 5 секунд и ещё 30 отдают устаревший, пока обновляют фоном.
    response.headers["Cache-Control"] = STATE_CACHE_CONTROL
    return state_snapshot()


@app.get("/api/v1/events/{event_id}/sources")
def event_sources(event_id: str):
    """Кто и когда сообщил о событии.

    Прежде источники не раскрывались вовсе: считалось, что это снижает риск
    злоупотреблений. Решение пересмотрено в пользу проверяемости — человек
    должен видеть, на чём основано «подтверждено восемью источниками», иначе
    число приходится принимать на веру.
    """
    rows = query(
        """
        SELECT es.source_key, es.role, es.contributed_at, m.text, m.message_id
        FROM event_sources es
        JOIN raw_messages m ON m.id = es.raw_message_id
        WHERE es.event_id = ?
        ORDER BY es.contributed_at, es.raw_message_id
        """,
        (event_id,),
    )
    if not rows:
        return {"event_id": event_id, "sources": []}

    networks = source_networks()
    items = [
        {
            "source_key": item.source_key,
            "role": item.role,
            "at": item.at,
            "first_from_source": item.first_from_source,
            "repost": item.repost,
            # Канал той же сети, что и уже засчитанный: голос у сети один.
            "clone": item.clone,
            "counted": item.counted,
            "link": item.link,
            "text": item.text,
        }
        for item in walk(rows, networks, SOURCE_USERNAMES)
    ]
    # Список собирается по времени вперёд — иначе перепостом окажется
    # оригинал, — а наружу отдаётся в обратном порядке: свежее сверху.
    items.reverse()
    return {
        "event_id": event_id,
        "sources": items,
        "counted": sum(1 for item in items if item["counted"]),
    }


@app.get("/api/v1/history/days")
def history_days(limit: int = Query(60, ge=1, le=400)):
    """Дни, за которые есть события, с плотностью.

    Плотность нужна интерфейсу: корпус растянут на месяцы, но до недавнего
    времени в нём единицы событий в сутки. Без подсказки человек будет
    перематывать пустоту.
    """
    rows = query(
        """
        SELECT date(datetime(first_seen_at, '+3 hours')) AS day,
               COUNT(*) AS events,
               MAX(severity) AS max_severity,
               SUM(CASE WHEN source_count > 1 THEN 1 ELSE 0 END) AS confirmed
        FROM events
        GROUP BY day
        ORDER BY day DESC
        LIMIT ?
        """,
        (limit,),
    )
    peak = max((row["events"] for row in rows), default=0)
    for row in rows:
        # Доля от самого насыщенного дня — из неё рисуется полоска.
        row["density"] = round(row["events"] / peak, 3) if peak else 0
    return {"days": list(reversed(rows)), "peak": peak}


@app.get("/api/v1/history")
def history(
    hours: int = Query(24, ge=1, le=24 * 30),
    day: str | None = Query(None, description="Сутки по Москве, YYYY-MM-DD"),
):
    """Историческое окно: последние N часов либо конкретные сутки."""
    if day:
        try:
            start_msk = datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return JSONResponse({"detail": "день ожидается как YYYY-MM-DD"}, status_code=400)
        # Сутки считаются московские: человек мыслит своим днём, а не UTC.
        start = start_msk.replace(tzinfo=MSK).astimezone(timezone.utc)
        end = start + timedelta(days=1)
        rows = [
            row for row in event_rows(start, limit=20000)
            if row["first_seen_at"] < end.isoformat()
        ]
        return {"from": start.isoformat(), "to": end.isoformat(), "day": day, "events": rows}

    now = latest_moment()
    since = now - timedelta(hours=hours)
    return {"from": since.isoformat(), "to": now.isoformat(),
            "events": event_rows(since, limit=5000)}


@app.get("/api/v1/analytics/sources")
def analytics_sources():
    """Метрики источников: скорость и подтверждаемость.

    Этого нет ни у одного из существующих сервисов.
    """
    totals = {row["source_key"]: row for row in query(
        "SELECT source_key, COUNT(*) AS messages FROM raw_messages GROUP BY source_key")}

    roles = query(
        "SELECT source_key, role, COUNT(*) AS n FROM event_sources GROUP BY source_key, role")
    confirmed = query("""
        SELECT es.source_key, COUNT(*) AS n
        FROM event_sources es JOIN events e ON e.id = es.event_id
        WHERE e.source_count > 1 GROUP BY es.source_key
    """)
    lonely = query("""
        SELECT es.source_key, COUNT(*) AS n
        FROM event_sources es JOIN events e ON e.id = es.event_id
        WHERE e.source_count = 1 GROUP BY es.source_key
    """)

    # Задержка относительно первого сообщения о событии.
    lag_rows = query("""
        SELECT es.source_key,
               (julianday(es.contributed_at) - julianday(e.first_seen_at)) * 86400.0 AS lag_sec
        FROM event_sources es JOIN events e ON e.id = es.event_id
        WHERE e.source_count > 1
    """)
    lags: dict[str, list[float]] = {}
    for row in lag_rows:
        lags.setdefault(row["source_key"], []).append(row["lag_sec"])

    out = []
    for key, base in totals.items():
        by_role = {row["role"]: row["n"] for row in roles if row["source_key"] == key}
        got_confirmed = next((row["n"] for row in confirmed if row["source_key"] == key), 0)
        got_lonely = next((row["n"] for row in lonely if row["source_key"] == key), 0)
        values = sorted(lags.get(key, []))
        median = values[len(values) // 2] if values else None
        contributions = got_confirmed + got_lonely

        out.append({
            "source_key": key,
            "messages": base["messages"],
            "contributions": contributions,
            "first_reports": by_role.get("first", 0),
            "confirmations": by_role.get("confirm", 0),
            "confirmed_share": round(got_confirmed / contributions, 3) if contributions else 0.0,
            "unconfirmed_share": round(got_lonely / contributions, 3) if contributions else 0.0,
            "median_lag_sec": round(median) if median is not None else None,
        })

    out.sort(key=lambda row: -row["first_reports"])
    span = query("SELECT MIN(first_seen_at) AS a, MAX(last_seen_at) AS b FROM events")
    tiers = {source.key: source.tier for source in sources_from_env()}
    for row in out:
        # Официальный источник и анонимный радар в таблице выглядели
        # одинаково, и колонка «подтв. 0%» у МЧС читалась как «верить
        # нельзя», хотя означает лишь, что его сообщения редко дублируют.
        row["tier"] = tiers.get(row["source_key"], "regional")
    return {
        "sources": out,
        # Период у таблицы не был указан вовсе, и числа читались как
        # «вообще», хотя это весь собранный корпус целиком.
        "since": span[0]["a"] if span else None,
        "until": span[0]["b"] if span else None,
    }


@app.get("/api/v1/analytics/zones")
def analytics_zones(hours: int = Query(168, ge=1, le=24 * 90), limit: int = 25):
    """Плотность и длительность по зонам за произвольный период."""
    since = (latest_moment() - timedelta(hours=hours)).isoformat()
    rows = query("""
        SELECT z.name_ru, z.level, e.zone_id,
               COUNT(*) AS events,
               MAX(e.severity) AS max_severity,
               AVG(e.confidence) AS avg_confidence,
               AVG((julianday(e.last_seen_at) - julianday(e.first_seen_at)) * 86400.0) AS avg_duration_sec
        FROM events e JOIN zones z ON z.id = e.zone_id
        WHERE e.first_seen_at >= ?
        GROUP BY e.zone_id ORDER BY events DESC LIMIT ?
    """, (since, limit))
    for row in rows:
        row["avg_confidence"] = round(row["avg_confidence"] or 0, 3)
        row["avg_duration_sec"] = round(row["avg_duration_sec"] or 0)

    by_hour = query("""
        SELECT substr(first_seen_at, 12, 2) AS hour, COUNT(*) AS n
        FROM events WHERE first_seen_at >= ? GROUP BY hour ORDER BY hour
    """, (since,))
    by_threat = query("""
        SELECT threat_type, COUNT(*) AS n FROM events
        WHERE first_seen_at >= ? GROUP BY threat_type ORDER BY n DESC
    """, (since,))

    return {"top_zones": rows, "by_hour": by_hour, "by_threat": by_threat}


@app.get("/api/v1/search")
def search(q: str = Query(..., min_length=2), limit: int = Query(12, ge=1, le=40)):
    """Поиск по справочнику на сервере.

    Раньше клиент держал в памяти каталог из 212 тысяч строк и фильтровал его
    на каждое нажатие клавиши. Теперь это индексированный запрос к SQLite.
    """
    needle = q.strip().lower().replace("ё", "е")
    if len(needle) < 2:
        return {"items": []}

    rows = query(
        """
        SELECT z.id, z.name_ru, z.level, z.lat, z.lon, z.population, z.source_id,
               p.name_ru  AS parent_name,
               gp.name_ru AS grandparent_name,
               MIN(LENGTH(n.norm)) AS match_len
        FROM zone_names n
        JOIN zones z       ON z.id = n.zone_id
        LEFT JOIN zones p  ON p.id = z.parent_id
        LEFT JOIN zones gp ON gp.id = p.parent_id
        WHERE n.norm LIKE ? ESCAPE '\\'
        GROUP BY z.id
        ORDER BY
            CASE z.level WHEN 'region' THEN 0 WHEN 'district' THEN 1 ELSE 2 END,
            COALESCE(z.population, 0) DESC,
            match_len
        LIMIT ?
        """,
        (needle.replace("%", "\\%").replace("_", "\\_") + "%", limit),
    )

    items = []
    for row in rows:
        # Одноимённых районов больше сотни — показываем родителя,
        # иначе выбрать нужный невозможно.
        context = row["parent_name"] if row["level"] != "region" else None
        if row["level"] == "place" and row["grandparent_name"]:
            context = f"{row['parent_name']} · {row['grandparent_name']}"
        items.append({
            "zone_id": row["id"],
            "name": row["name_ru"],
            "level": row["level"],
            "context": context,
            "lat": row["lat"],
            "lon": row["lon"],
            "population": row["population"],
            "source_id": row["source_id"],
        })
    return {"items": items}


@app.get("/api/v1/summary")
def summary():
    counts = query("""
        SELECT (SELECT COUNT(*) FROM raw_messages) AS raw_messages,
               (SELECT COUNT(*) FROM events) AS events,
               (SELECT COUNT(*) FROM events WHERE source_count > 1) AS multi_source_events,
               (SELECT COUNT(*) FROM zones) AS zones,
               (SELECT COUNT(DISTINCT source_key) FROM raw_messages) AS sources
    """)[0]
    counts["generated_at"] = latest_moment().isoformat()
    return counts


@app.websocket("/api/v1/stream")
async def stream(socket: WebSocket):
    """Push-обновления. Без пейволла — в отличие от RadarMap."""
    await socket.accept()
    last_sent: str | None = None
    try:
        while True:
            # Снимок собирается в отдельном потоке: SQL синхронный, а на event
            # loop висят все остальные соединения.
            snapshot = await asyncio.to_thread(state_snapshot)
            marker = f"{snapshot['last_message_at']}|{snapshot['active_events']}"
            if marker != last_sent:
                await socket.send_json({"type": "state", **snapshot})
                last_sent = marker
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        return
