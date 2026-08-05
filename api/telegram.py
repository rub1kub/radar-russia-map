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
import json
import os
import sqlite3
import time
from contextlib import closing
from typing import Any, Callable

import requests
from fastapi import APIRouter, Header, Request, Response

from pipeline.db import DB_PATH
from pipeline.textnorm import norm_key
from pipeline.timeutil import MSK, now_utc

SITE = "https://tihoenebo.com"
API = "https://api.telegram.org/bot{token}/{method}"

# Как часто рассыльщик проверяет, не появилось ли новых событий по
# подпискам. Тот же такт, что у web push: чаще нет смысла, конвейер сам
# обновляет снимок раз в двадцать секунд.
TICK_SEC = 20
# Сколько помним, что событие уже отправлено. Сутки с запасом перекрывают
# время жизни события, а таблица не растёт бесконечно.
SENT_TTL_SEC = 24 * 3600
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
"""

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


def open_map_button(text: str = "Открыть карту") -> dict:
    """Кнопка, открывающая мини-приложение прямо в Telegram."""
    return {"inline_keyboard": [[{"text": text, "web_app": {"url": SITE}}]]}


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
    return row["name_ru"] if row else zone_id


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

THREAT_WORD = {
    "uav": "БПЛА", "fpv": "FPV", "rocket": "ракета",
    "kab": "КАБ", "bek": "БЭК", "aviation": "авиация",
}


def _moment(iso: str) -> str:
    from datetime import datetime
    return datetime.fromisoformat(iso).astimezone(MSK).strftime("%H:%M")


def _event_line(event: dict) -> str:
    place = event.get("place_name") or event.get("zone_name") or "—"
    signal = SIGNAL_WORD.get(event.get("signal_type", ""), event.get("signal_type", ""))
    threat = THREAT_WORD.get(event.get("threat_type", ""))
    tail = f" · {threat}" if threat else ""
    return f"<b>{place}</b> — {signal}{tail}, {_moment(event['last_seen_at'])}"


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
    head = f"<b>{zone['name_ru']}</b>"
    if not events:
        return (f"{head}\n\nСейчас тихо: активных сообщений по этому месту "
                f"нет.\n\nЧтобы получать уведомления: /watch {zone['name_ru']}")
    lines = [head, ""]
    for event in events[:LIST_LIMIT]:
        lines.append("• " + _event_line(event))
    if len(events) > LIST_LIMIT:
        lines.append(f"…и ещё {len(events) - LIST_LIMIT}")
    lines.append("")
    lines.append(f"Уведомления по этому месту: /watch {zone['name_ru']}")
    return "\n".join(lines)


HELP = (
    "<b>Тихое небо</b> — карта воздушной обстановки по открытым источникам.\n\n"
    "/status — что происходит сейчас\n"
    "/region Белгородская область — обстановка по месту\n"
    "/watch Белгородская область — уведомлять об этом месте\n"
    "/unwatch Белгородская область — перестать\n"
    "/my — мои подписки\n"
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


def _touch(connection: sqlite3.Connection, chat_id: int) -> None:
    stamp = now_utc().isoformat()
    connection.execute(
        """INSERT INTO tg_chats (chat_id, zones, created_at, last_seen)
           VALUES (?, '[]', ?, ?)
           ON CONFLICT(chat_id) DO UPDATE SET last_seen = excluded.last_seen""",
        (chat_id, stamp, stamp))


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

def handle_text(chat_id: int, text: str) -> None:
    raw = (text or "").strip()
    if not raw:
        return
    head, _, argument = raw.partition(" ")
    command = head.split("@")[0].lower()
    argument = argument.strip()

    if command in ("/start",):
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
        await asyncio.to_thread(handle_text, int(chat_id), text)
    return Response(status_code=200)


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
                if connection.execute(
                        "SELECT 1 FROM tg_sent WHERE chat_id = ? AND event_key = ?",
                        (row["chat_id"], key)).fetchone():
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO tg_sent (chat_id, event_key, sent_at)"
                    " VALUES (?,?,?)", (row["chat_id"], key, now))
                head = "🟢 Отбой" if cleared else "🔴 По вашему месту"
                send(int(row["chat_id"]),
                     f"{head}\n\n{_event_line(event)}",
                     open_map_button("Посмотреть на карте"))
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
