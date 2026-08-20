"""Телеграм-бот и мини-приложение «Тихое небо».

Зачем отдельный канал, когда на сайте уже есть web push: до половины
аудитории сидит в Telegram и там же читает те самые ленты мониторинга. На
iOS браузерные уведомления работают только для сайта, добавленного на
домашний экран, — бот доходит до человека там, где он и так есть.

Что здесь:

* вебхук Bot API (`/api/v1/tg/webhook`) — приём команд;
* команды: старт, обстановка, регион, подписка и её отмена;
* доставка тревог и отбоев подписчикам — той же логикой, что и web push:
  событие считается доставленным один раз, отбой отдельным сообщением;
* мини-приложение — это сам сайт, открытый в Telegram. Кнопка меню бота и
  кнопка в приветствии ведут туда же, куда и обычная ссылка.

Токен читается из окружения (`TELEGRAM_BOT_TOKEN`) и в репозиторий не
попадает: он даёт полный доступ к боту. Пока переменной нет, роутер
отвечает как обычно, но наружу ничего не шлёт — так локальная разработка
не трогает живого бота.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import time
from contextlib import closing
from typing import Any, Callable

import requests
from fastapi import APIRouter, Header, Request, Response

from pipeline.db import DB_PATH
from pipeline.textnorm import norm_key, short_name
from pipeline.timeutil import MSK, now_utc

from . import notify_throttle
from .wording import event_sentence

SITE = "https://tihoenebo.com"
API = "https://api.telegram.org/bot{token}/{method}"

# Как часто рассыльщик проверяет, не появилось ли новых событий по
# подпискам. Тот же такт, что у web push: чаще нет смысла, конвейер сам
# обновляет снимок раз в двадцать секунд.
TICK_SEC = 20
# Сколько помним, что событие уже отправлено. Сутки с запасом перекрывают
# время жизни события, а таблица не растёт бесконечно.
SENT_TTL_SEC = 24 * 3600

# Окно, в котором одинаковый ТЕКСТ уведомления не отправляется повторно.
# Дедуп по id события не спасает, когда слияние раскололо одну волну на
# два события в той же зоне: рестарт конвейера в разгар волны дал два
# «взрыва» с разными id, и подписчик получил одно и то же дважды с
# разницей в минуту. Полчаса — двойное окно слияния: настоящий второй
# удар по тому же месту позже него уже различим временем в тексте.
SAME_LINE_TTL_SEC = 30 * 60
# Сколько мест показываем в ответе на команду.
LIST_LIMIT = 8
# Предел подписок на один чат: список должен помещаться в сообщение, а
# человеку больше и не нужно.
MAX_ZONES = 12

SCHEMA = """
CREATE TABLE IF NOT EXISTS tg_chats (
    chat_id    INTEGER PRIMARY KEY,
    zones      TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    last_seen  TEXT
);
CREATE TABLE IF NOT EXISTS tg_sent (
    chat_id   INTEGER NOT NULL,
    event_key TEXT NOT NULL,
    sent_at   INTEGER NOT NULL,
    PRIMARY KEY (chat_id, event_key)
);
-- Журнал действий: кто написал команду, кто открыл карту из Telegram.
-- Отдельно от tg_chats: чат хранит состояние, журнал хранит историю.
CREATE TABLE IF NOT EXISTS tg_activity (
    chat_id INTEGER NOT NULL,
    kind    TEXT NOT NULL,
    at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tg_activity_at ON tg_activity (at);
"""

# Колонки, дописанные позже создания таблицы: CREATE IF NOT EXISTS старую
# форму не меняет, поэтому доливаем через ALTER и глотаем «уже есть».
SCHEMA_PATCHES = (
    "ALTER TABLE tg_chats ADD COLUMN username TEXT",
    "ALTER TABLE tg_chats ADD COLUMN name TEXT",
)

router = APIRouter(prefix="/api/v1/tg", tags=["telegram"])

# Снимок обстановки берётся у server.py. Через переменную, а не импортом:
# сервер уже импортирует этот модуль, и обратный импорт замкнул бы круг.
_snapshot_fn: Callable[[], dict] | None = None


def use_snapshot(fn: Callable[[], dict]) -> None:
    global _snapshot_fn
    _snapshot_fn = fn


def token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def webhook_secret() -> str:
    """Пароль вебхука. Telegram присылает его заголовком в каждом запросе.

    Без него эндпоинт открыт всему интернету: кто угодно мог бы прислать
    поддельное обновление и заставить бота отвечать от нашего имени.
    """
    return os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.executescript(SCHEMA)
    notify_throttle.ensure_schema(connection)
    for patch in SCHEMA_PATCHES:
        try:
            connection.execute(patch)
        except sqlite3.OperationalError:
            pass  # колонка уже долита
    return connection


# --- Вызовы Bot API ---------------------------------------------------------

def call(method: str, **payload: Any) -> dict:
    """Синхронный вызов Bot API. Ошибку не поднимаем: бот не должен ронять
    ни вебхук, ни рассыльщик — в худшем случае сообщение не уйдёт."""
    if not token():
        return {"ok": False, "description": "нет TELEGRAM_BOT_TOKEN"}
    try:
        response = requests.post(
            API.format(token=token(), method=method), json=payload, timeout=20)
        return response.json()
    except Exception as error:  # noqa: BLE001 — сеть, ронять нечего
        return {"ok": False, "description": str(error)}


def send(chat_id: int, text: str, keyboard: dict | None = None) -> dict:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = keyboard
    return call("sendMessage", **payload)


_username_cache: str | None = None


def bot_username() -> str | None:
    """Имя бота для t.me-ссылок: из окружения или один раз через getMe."""
    global _username_cache
    if _username_cache is None:
        _username_cache = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip()
        if not _username_cache and token():
            result = call("getMe").get("result") or {}
            _username_cache = result.get("username") or ""
    return _username_cache or None


def open_map_button(text: str = "Открыть карту") -> dict:
    """Кнопка со ссылкой t.me — мини-приложение, переживающее репост.

    Кнопка web_app при пересылке пропадает: Telegram не даёт чужим чатам
    открывать мини-приложение бота напрямую. Ссылка t.me/бот?startapp
    остаётся у репоста и открывает карту в самом Telegram, а при первом
    открытии мессенджер спрашивает разрешение боту писать человеку.
    Требует включённого Main Mini App у BotFather; без имени бота кнопка
    ведёт на сайт.
    """
    username = bot_username()
    url = f"https://t.me/{username}?startapp" if username else SITE
    return {"inline_keyboard": [[{"text": text, "url": url}]]}


# --- Поиск зоны по названию -------------------------------------------------

def find_zone(query: str) -> sqlite3.Row | None:
    """Регион, район или город по человеческому вводу.

    Ищем по таблице zone_names — там лежат нормализованные имена и их
    варианты («тульская» рядом с «тульская область»), тем же индексом
    пользуется геокодер. Собственный lower() тут не годится: в SQLite он
    работает только с латиницей и на кириллице молча возвращает исходное.

    Сначала точное совпадение, потом начало строки. Внутри — от области к
    деревне и по населению: «белгородская» должна давать область, а не
    одноимённый хутор.
    """
    key = norm_key(query)
    if len(key) < 3:
        return None
    order = ("""ORDER BY CASE z.level WHEN 'region' THEN 0
                                      WHEN 'district' THEN 1 ELSE 2 END,
                        COALESCE(z.population, 0) DESC
               LIMIT 1""")
    with closing(_connect()) as connection:
        exact = connection.execute(
            f"""SELECT z.id, z.name_ru, z.level FROM zone_names n
                JOIN zones z ON z.id = n.zone_id
                WHERE n.norm = ? {order}""", (key,)).fetchone()
        if exact:
            return exact
        return connection.execute(
            f"""SELECT z.id, z.name_ru, z.level FROM zone_names n
                JOIN zones z ON z.id = n.zone_id
                WHERE n.norm LIKE ? || '%' {order}""", (key,)).fetchone()


def zone_name(zone_id: str) -> str:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT name_ru FROM zones WHERE id = ?", (zone_id,)).fetchone()
    return short_name(row["name_ru"]) if row else zone_id


def forget(chat_id: int) -> str:
    """Удалить всё, что сервис хранит об этом человеке."""
    with closing(_connect()) as connection:
        connection.execute("DELETE FROM tg_chats WHERE chat_id = ?", (chat_id,))
        connection.execute("DELETE FROM tg_sent WHERE chat_id = ?", (chat_id,))
        connection.execute("DELETE FROM tg_activity WHERE chat_id = ?", (chat_id,))
        connection.commit()
    return ("Готово: подписки и все связанные с вами данные удалены.\n\n"
            "Картой можно пользоваться и без бота — она ничего о вас не "
            "хранит. Чтобы снова получать уведомления, напишите /watch и "
            "название места.")


# --- Тексты ответов ---------------------------------------------------------

def plural(count: int, one: str, few: str, many: str) -> str:
    mod100, mod10 = abs(count) % 100, abs(count) % 10
    if 11 <= mod100 <= 14:
        return many
    if mod10 == 1:
        return one
    if 2 <= mod10 <= 4:
        return few
    return many


SIGNAL_WORD = {
    "detection": "фиксация",
    "intercept": "перехват",
    "impact": "взрыв",
    "alarm": "тревога",
    "danger": "опасность",
    "allclear": "отбой",
    "infra": "инфраструктура",
}

# Те же полосы, что в легенде карты: красное — борт видят (фиксация,
# перехват, взрыв), оранжевое — тревога, жёлтое — опасность, зелёное —
# отбой. Служебное (инфраструктура) — без цвета угрозы.
SIGNAL_DOT = {
    "detection": "🔴", "intercept": "🔴", "impact": "🔴",
    "alarm": "🟠",
    "danger": "🟡",
    "allclear": "🟢",
    "infra": "⚪",
}


def _dot(event: dict) -> str:
    return SIGNAL_DOT.get(event.get("signal_type", ""), "⚪")

THREAT_WORD = {
    "uav": "БПЛА", "fpv": "FPV", "rocket": "ракета",
    "kab": "КАБ", "bek": "БЭК", "aviation": "авиация",
    # infra намеренно без слова: сигнал «инфраструктура» уже сказал всё.
}


def _moment(iso: str) -> str:
    from datetime import datetime
    return datetime.fromisoformat(iso).astimezone(MSK).strftime("%H:%M")


def _event_line(event: dict) -> str:
    """«Краснодар — в небе видят БПЛА, 13:51» — предложение, а не ярлык.

    Раньше здесь стояло слово из внутреннего словаря сигналов, и человек
    получал «Краснодар — инфраструктура»: правда, но непонятная. Слова
    сигналов остались в подсказках и легендах; уведомление говорит, что
    происходит.
    """
    place = event.get("place_name") or event.get("zone_name") or "—"
    sentence = event_sentence(event)
    # Хвост с типом угрозы — там, где предложение её не назвало само.
    threat = THREAT_WORD.get(event.get("threat_type", ""))
    # По основе слова: «ракетная опасность» уже назвала ракету, хотя
    # словарное «ракета» в неё дословно не входит.
    named = bool(threat) and threat.lower()[:5] in sentence.lower()
    tail = f" · {threat}" if threat and not named else ""
    return f"<b>{place}</b> — {sentence}{tail}, {_moment(event['last_seen_at'])}"


def status_text() -> str:
    """Что происходит по стране прямо сейчас."""
    snapshot = _snapshot_fn() if _snapshot_fn else {}
    events = snapshot.get("events") or []
    if not events:
        return ("Сейчас в отслеживаемых лентах тихо: активных сообщений нет.\n\n"
                "Карта продолжает следить — как только что-то появится, оно "
                "будет здесь.")

    from collections import Counter
    by_region: Counter = Counter()
    for event in events:
        path = event.get("zone_path") or []
        if path:
            by_region[path[-1]] += 1

    lines = [f"<b>Сейчас в эфире {len(events)} "
             f"{plural(len(events), 'событие', 'события', 'событий')}</b>", ""]
    for zone_id, count in by_region.most_common(6):
        lines.append(f"• {zone_name(zone_id)} — {count}")
    lines.append("")
    lines.append("Подробности и карта — по кнопке ниже.")
    if snapshot.get("stale"):
        lines.insert(1, "⚠️ Сбор сообщений отстаёт, показанное может "
                        "не отражать текущую обстановку.\n")
    return "\n".join(lines)


def region_text(zone: sqlite3.Row) -> str:
    """Обстановка по конкретной зоне."""
    snapshot = _snapshot_fn() if _snapshot_fn else {}
    events = [e for e in (snapshot.get("events") or [])
              if zone["id"] in (e.get("zone_path") or [])]
    # Официальное «городской округ Белгород» человеку не показываем — ни
    # в заголовке, ни в подсказке команды: /watch он наберёт руками.
    name = short_name(zone["name_ru"])
    head = f"<b>{name}</b>"
    if not events:
        return (f"{head}\n\nСейчас тихо: активных сообщений по этому месту "
                f"нет.\n\nЧтобы получать уведомления: /watch {name}")
    lines = [head, ""]
    for event in events[:LIST_LIMIT]:
        lines.append(f"{_dot(event)} " + _event_line(event))
    if len(events) > LIST_LIMIT:
        lines.append(f"…и ещё {len(events) - LIST_LIMIT}")
    lines.append("")
    lines.append(f"Уведомления по этому месту: /watch {name}")
    return "\n".join(lines)


HELP = (
    "<b>Тихое небо</b> — карта воздушной обстановки по открытым источникам.\n\n"
    "/status — что происходит сейчас\n"
    "/region Белгородская область — обстановка по месту\n"
    "/watch Белгородская область — уведомлять об этом месте\n"
    "/unwatch Белгородская область — перестать\n"
    "/my — мои подписки\n"
    "/stop — удалить все мои данные\n"
    "/help — эта справка\n\n"
    "Карта показывает только то, что сообщили ленты мониторинга. Это не "
    "официальное оповещение: следуйте указаниям экстренных служб."
)


# --- Подписки ---------------------------------------------------------------

def _zones_of(connection: sqlite3.Connection, chat_id: int) -> list[str]:
    row = connection.execute(
        "SELECT zones FROM tg_chats WHERE chat_id = ?", (chat_id,)).fetchone()
    if not row:
        return []
    try:
        return list(json.loads(row["zones"]))
    except ValueError:
        return []


def _touch(connection: sqlite3.Connection, chat_id: int,
           username: str | None = None, name: str | None = None) -> None:
    stamp = now_utc().isoformat()
    connection.execute(
        """INSERT INTO tg_chats (chat_id, zones, created_at, last_seen, username, name)
           VALUES (?, '[]', ?, ?, ?, ?)
           ON CONFLICT(chat_id) DO UPDATE SET
               last_seen = excluded.last_seen,
               username  = COALESCE(excluded.username, tg_chats.username),
               name      = COALESCE(excluded.name, tg_chats.name)""",
        (chat_id, stamp, stamp, username, name))


def record_activity(chat_id: int, kind: str,
                    username: str | None = None, name: str | None = None) -> None:
    """Каждое действие человека — строка в журнале.

    Кто написал команду, кто открыл карту из Telegram: без журнала о
    людях известно только «подписан / не подписан», а владельцу нужно
    видеть, живёт ли бот вообще.
    """
    with closing(_connect()) as connection:
        _touch(connection, chat_id, username, name)
        connection.execute(
            "INSERT INTO tg_activity (chat_id, kind, at) VALUES (?,?,?)",
            (chat_id, kind, now_utc().isoformat()))
        connection.commit()


def watch(chat_id: int, zone_id: str) -> str:
    with closing(_connect()) as connection:
        _touch(connection, chat_id)
        zones = _zones_of(connection, chat_id)
        if zone_id in zones:
            connection.commit()
            return "Уже отслеживается."
        if len(zones) >= MAX_ZONES:
            connection.commit()
            return (f"Больше {MAX_ZONES} мест сразу не отслеживаем — список "
                    f"перестаёт читаться. Уберите лишнее: /my")
        zones.append(zone_id)
        connection.execute("UPDATE tg_chats SET zones = ? WHERE chat_id = ?",
                           (json.dumps(zones), chat_id))
        connection.commit()
    return f"Слежу за этим местом: <b>{zone_name(zone_id)}</b>."


def zone_start_payload(zone_id: str) -> str:
    """Payload диплинка t.me/бот?start=… — Telegram пускает максимум 64 знака.

    Короткие id идут как есть, читаемыми; редкие длинные (глубокие сёла)
    сворачиваются в md5-хвост, который deeplink_zone разворачивает обратно
    перебором таблицы зон.
    """
    if len(zone_id) <= 62:
        return "w_" + zone_id
    return "wh" + hashlib.md5(zone_id.encode("utf-8")).hexdigest()[:12]


def deeplink_zone(payload: str) -> str | None:
    """Зона из start-payload диплинка; None — payload не про подписку."""
    if payload.startswith("w_"):
        zone_id = payload[2:]
        with closing(_connect()) as connection:
            row = connection.execute(
                "SELECT id FROM zones WHERE id = ?", (zone_id,)).fetchone()
        return row["id"] if row else None
    if payload.startswith("wh") and len(payload) == 14:
        tail = payload[2:]
        with closing(_connect()) as connection:
            for (zone_id,) in connection.execute(
                    "SELECT id FROM zones WHERE length(id) > 62"):
                if hashlib.md5(
                        zone_id.encode("utf-8")).hexdigest()[:12] == tail:
                    return zone_id
    return None


def unwatch(chat_id: int, zone_id: str) -> str:
    with closing(_connect()) as connection:
        _touch(connection, chat_id)
        zones = _zones_of(connection, chat_id)
        if zone_id not in zones:
            connection.commit()
            return "Этого места и не было в списке."
        zones.remove(zone_id)
        connection.execute("UPDATE tg_chats SET zones = ? WHERE chat_id = ?",
                           (json.dumps(zones), chat_id))
        connection.commit()
    return f"Больше не слежу: {zone_name(zone_id)}."


def my_text(chat_id: int) -> str:
    with closing(_connect()) as connection:
        zones = _zones_of(connection, chat_id)
    if not zones:
        return ("Пока ничего не отслеживаете.\n\n"
                "Добавить: /watch Белгородская область")
    lines = ["<b>Ваши места</b>", ""]
    lines += [f"• {zone_name(zone_id)}" for zone_id in zones]
    lines.append("")
    lines.append("Убрать: /unwatch и название места.")
    return "\n".join(lines)


# --- Разбор команд ----------------------------------------------------------

def handle_text(chat_id: int, text: str,
                user: dict | None = None) -> None:
    raw = (text or "").strip()
    if not raw:
        return
    head, _, argument = raw.partition(" ")
    command = head.split("@")[0].lower()
    argument = argument.strip()

    # Каждое обращение — в журнал: команду целиком, свободный текст — меткой.
    record_activity(chat_id, command if command.startswith("/") else "text",
                    (user or {}).get("username"),
                    (user or {}).get("first_name"))

    if command in ("/start",):
        # Диплинк с сайта: t.me/бот?start=w_<зона> — человек нажал
        # «Получать уведомления» на странице места, подписываем сразу.
        zone_id = deeplink_zone(argument) if argument else None
        if zone_id:
            record_activity(chat_id, "/start:deeplink")
            reply = watch(chat_id, zone_id)
            send(chat_id,
                 f"{reply}\n\nКогда здесь объявят тревогу, заметят БПЛА или "
                 f"дадут отбой — придёт сообщение. Отписаться: "
                 f"/unwatch {zone_name(zone_id)}",
                 open_map_button())
            return
        send(chat_id,
             "Это <b>Тихое небо</b> — карта воздушной обстановки по открытым "
             "Telegram-каналам.\n\nОткройте карту кнопкой ниже или спросите "
             "командой: /status, /region Курская область.\n\n"
             "Чтобы получать уведомления о своём месте — /watch и название.",
             open_map_button())
        return

    if command in ("/help", "/помощь"):
        send(chat_id, HELP, open_map_button())
        return

    if command == "/status":
        send(chat_id, status_text(), open_map_button())
        return

    if command in ("/region", "/регион"):
        if not argument:
            send(chat_id, "Напишите место: <code>/region Курская область</code>")
            return
        zone = find_zone(argument)
        if not zone:
            send(chat_id, f"Не нашёл место «{argument}». Попробуйте назвать "
                          f"область или район целиком.")
            return
        send(chat_id, region_text(zone), open_map_button())
        return

    if command in ("/watch", "/следить"):
        if not argument:
            send(chat_id, "Напишите место: <code>/watch Курская область</code>")
            return
        zone = find_zone(argument)
        if not zone:
            send(chat_id, f"Не нашёл место «{argument}».")
            return
        send(chat_id, watch(chat_id, zone["id"]))
        return

    if command in ("/unwatch", "/отписаться"):
        if not argument:
            send(chat_id, "Напишите место: <code>/unwatch Курская область</code>")
            return
        zone = find_zone(argument)
        if not zone:
            send(chat_id, f"Не нашёл место «{argument}».")
            return
        send(chat_id, unwatch(chat_id, zone["id"]))
        return

    if command in ("/my", "/мои"):
        send(chat_id, my_text(chat_id))
        return

    # Право на удаление, обещанное политикой конфиденциальности. Сносим всё,
    # что о человеке известно: подписки, состояние чата, журнал обращений и
    # отметки об отправленных уведомлениях.
    if command in ("/stop", "/удалить"):
        send(chat_id, forget(chat_id))
        return

    # Не команда — считаем, что человек назвал место: так проще, чем
    # объяснять синтаксис.
    zone = find_zone(raw)
    if zone:
        send(chat_id, region_text(zone), open_map_button())
    else:
        send(chat_id, HELP, open_map_button())


# --- Вебхук -----------------------------------------------------------------

@router.post("/webhook")
async def webhook(
    request: Request,
    secret: str = Header("", alias="X-Telegram-Bot-Api-Secret-Token"),
) -> Response:
    expected = webhook_secret()
    if expected and secret != expected:
        # Молча: отвечать подробностями тому, кто стучится не тем ключом,
        # незачем.
        return Response(status_code=403)

    update = await request.json()
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")
    if chat_id and text:
        # Отвечаем в потоке: Bot API синхронный, а держать вебхук открытым
        # дольше нужного нельзя — Telegram повторит обновление.
        await asyncio.to_thread(handle_text, int(chat_id), text,
                                message.get("from") or {})
    return Response(status_code=200)


def validate_init_data(init_data: str) -> dict | None:
    """Подпись initData мини-приложения — по алгоритму Telegram.

    Секрет — HMAC от токена бота с ключом «WebAppData»; им подписывается
    отсортированная строка пар. Без проверки эндпоинт открыт всему
    интернету, и журнал открытий можно было бы накрутить curl-ом.
    """
    if not init_data or not token():
        return None
    from urllib.parse import parse_qsl

    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    their_hash = fields.pop("hash", "")
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token().encode(), hashlib.sha256).digest()
    ours = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not their_hash or not hmac.compare_digest(ours, their_hash):
        return None
    return fields


@router.post("/watch")
async def map_watch(request: Request) -> dict:
    """Колокольчик в мини-аппе — та же подписка, что команда /watch.

    Web Push внутри Telegram не живёт, а бот живёт: нажатие на карте
    уходит сюда с подписанными initData, и уведомления о месте приходят
    в чат. Бот отвечает подтверждением — человек сразу видит, что связь
    установлена. Подпись обязательна: без неё любой мог бы подписывать
    чужие чаты голым POST-ом.
    """
    try:
        payload = await request.json()
    except ValueError:
        return {"ok": False}
    fields = validate_init_data(str(payload.get("init_data") or ""))
    if fields is None:
        return {"ok": False}
    try:
        user = json.loads(fields.get("user") or "{}")
    except ValueError:
        user = {}
    if not user.get("id"):
        return {"ok": False}
    zone_id = str(payload.get("zone_id") or "")
    with closing(_connect()) as connection:
        known = connection.execute(
            "SELECT 1 FROM zones WHERE id = ?", (zone_id,)).fetchone()
    if not known:
        return {"ok": False}
    chat_id = int(user["id"])
    turned_on = bool(payload.get("on"))
    await asyncio.to_thread(
        record_activity, chat_id,
        "watch_map" if turned_on else "unwatch_map",
        user.get("username"), user.get("first_name"))
    reply = await asyncio.to_thread(
        watch if turned_on else unwatch, chat_id, zone_id)
    await asyncio.to_thread(send, chat_id, reply)
    return {"ok": True}


@router.post("/opened")
async def map_opened(request: Request) -> dict:
    """Карта открыта из Telegram — записываем, кто пришёл."""
    try:
        payload = await request.json()
    except ValueError:
        return {"ok": False}
    fields = validate_init_data(str(payload.get("init_data") or ""))
    if fields is None:
        return {"ok": False}
    try:
        user = json.loads(fields.get("user") or "{}")
    except ValueError:
        user = {}
    if not user.get("id"):
        return {"ok": False}
    await asyncio.to_thread(
        record_activity, int(user["id"]), "open_map",
        user.get("username"), user.get("first_name"))
    return {"ok": True}


@router.get("/health")
def health() -> dict:
    """Виден ли боту его токен и сколько людей подписано."""
    with closing(_connect()) as connection:
        chats = connection.execute("SELECT COUNT(*) FROM tg_chats").fetchone()[0]
        watching = connection.execute(
            "SELECT COUNT(*) FROM tg_chats WHERE zones != '[]'").fetchone()[0]
    return {"token": bool(token()), "chats": chats, "watching": watching}


# --- Рассылка ---------------------------------------------------------------

def deliver_once(snapshot: dict) -> int:
    """Разослать свежие события подписчикам. Синхронно.

    Правила те же, что у web push: одно событие — одно сообщение, отбой
    отдельно. Иначе при каждом такте человек получал бы одно и то же.
    """
    events = snapshot.get("events") or []
    if not events or not token():
        return 0

    sent = 0
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT chat_id, zones FROM tg_chats WHERE zones != '[]'").fetchall()
        if not rows:
            return 0
        now = int(time.time())
        connection.execute("DELETE FROM tg_sent WHERE sent_at < ?",
                           (now - SENT_TTL_SEC,))

        for row in rows:
            try:
                zones = set(json.loads(row["zones"]))
            except ValueError:
                continue
            for event in events:
                if not zones.intersection(event.get("zone_path") or []):
                    continue
                cleared = event.get("status") == "resolved"
                key = f"{event['id']}:clear" if cleared else event["id"]
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO tg_sent (chat_id, event_key, sent_at)"
                    " VALUES (?,?,?)", (row["chat_id"], key, now)).rowcount
                if not inserted:
                    continue
                # Опасность/тревога без отбоя между двумя волнами — та же
                # новость дважды: см. notify_throttle.
                subscriber = f"tg:{row['chat_id']}"
                if notify_throttle.should_suppress(
                        connection, subscriber, event, now):
                    connection.commit()
                    continue
                # Второй рубеж — сам текст: время события входит в строку,
                # так что настоящий новый удар от повтора отличим.
                head = ("🟢 Отбой" if cleared
                        else f"{_dot(event)} По вашему месту")
                text = f"{head}\n\n{_event_line(event)}"
                line_key = "line:" + hashlib.sha1(text.encode()).hexdigest()
                if connection.execute(
                        "SELECT 1 FROM tg_sent WHERE chat_id = ? AND event_key = ?"
                        " AND sent_at > ?",
                        (row["chat_id"], line_key,
                         now - SAME_LINE_TTL_SEC)).fetchone():
                    connection.commit()
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO tg_sent (chat_id, event_key, sent_at)"
                    " VALUES (?,?,?)", (row["chat_id"], line_key, now))
                notify_throttle.record_sent(connection, subscriber, event, now)
                # Отметка коммитится ДО отправки. Раньше коммит был один на
                # всю пачку в конце: рестарт API между отправкой и коммитом
                # терял отметки, и следующий такт рассылал всё то же самое
                # ещё раз. Обратная цена — упади процесс в зазоре между
                # коммитом и отправкой, уведомление пропадёт, — но дубль
                # тревоги подрывает доверие сильнее, чем редкий пропуск.
                connection.commit()
                try:
                    send(int(row["chat_id"]), text,
                         open_map_button("Посмотреть на карте"))
                except Exception:  # noqa: BLE001 — один чат не рушит рассылку
                    continue
                sent += 1
        connection.commit()
    return sent


async def deliver_loop(snapshot_fn) -> None:
    """Фоновый цикл рассыльщика внутри процесса API."""
    while True:
        try:
            snapshot = await asyncio.to_thread(snapshot_fn)
            await asyncio.to_thread(deliver_once, snapshot)
        except Exception:  # noqa: BLE001 — рассылка не роняет API
            pass
        await asyncio.sleep(TICK_SEC)
