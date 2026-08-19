"""Web Push для «Моих мест»: тревога и отбой догоняют закрытую вкладку.

Устройство нарочно без учётных записей: браузер приносит свою push-подписку
и список зон, которые человек отслеживает. Сервер не знает, кто это, — он
знает только «этому endpoint интересны эти зоны». Удаление закладок на
клиенте перезаписывает список; протухший endpoint (404/410 от push-службы)
удаляется сам.

Ключи VAPID генерируются при первом старте и живут в ingest/data/ рядом с
базой — в репозиторий не попадают.
"""

from __future__ import annotations

import asyncio
import json

from .wording import event_sentence
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter
from pydantic import BaseModel, Field
from py_vapid import Vapid, b64urlencode
from pywebpush import WebPushException, webpush

from pipeline.db import DB_PATH

DATA_DIR = DB_PATH.parent
VAPID_PATH = DATA_DIR / "vapid_private.pem"
# Контакт по стандарту обязателен; почтой не делимся.
VAPID_CLAIMS = {"sub": "https://github.com/rub1kub/radar-russia-map"}

# Как часто рассыльщик сверяется с обстановкой.
PUSH_TICK_SEC = 20
# Память об отправленном: не дольше суток — событие столько не живёт.
SENT_TTL_SEC = 24 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint   TEXT PRIMARY KEY,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    zones      TEXT NOT NULL,           -- JSON-массив zone_id
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_sent (
    endpoint  TEXT NOT NULL,
    event_key TEXT NOT NULL,            -- id события или "id:clear"
    sent_at   INTEGER NOT NULL,
    PRIMARY KEY (endpoint, event_key)
);
"""


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.executescript(SCHEMA)
    return connection


def _vapid() -> Vapid:
    if VAPID_PATH.exists():
        return Vapid.from_file(str(VAPID_PATH))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    vapid = Vapid()
    vapid.generate_keys()
    vapid.save_key(str(VAPID_PATH))
    return vapid


def public_key() -> str:
    """Публичный ключ VAPID в base64url — как его ждёт pushManager."""
    vapid = _vapid()
    raw = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return b64urlencode(raw)


router = APIRouter(prefix="/api/v1/push")


class Subscription(BaseModel):
    endpoint: str = Field(min_length=8, max_length=1024)
    keys: dict[str, str]


class SubscribeBody(BaseModel):
    subscription: Subscription
    zones: list[str] = Field(max_length=64)


@router.get("/key")
def key() -> dict:
    return {"key": public_key()}


@router.post("/subscribe")
def subscribe(body: SubscribeBody) -> dict:
    with closing(_connect()) as connection:
        connection.execute(
            "INSERT INTO push_subscriptions (endpoint, p256dh, auth, zones, created_at)"
            " VALUES (?,?,?,?,datetime('now'))"
            " ON CONFLICT(endpoint) DO UPDATE SET"
            "   p256dh = excluded.p256dh, auth = excluded.auth,"
            "   zones = excluded.zones",
            (body.subscription.endpoint,
             body.subscription.keys.get("p256dh", ""),
             body.subscription.keys.get("auth", ""),
             json.dumps(body.zones)),
        )
        connection.commit()
    return {"ok": True}


class UnsubscribeBody(BaseModel):
    endpoint: str


@router.post("/unsubscribe")
def unsubscribe(body: UnsubscribeBody) -> dict:
    with closing(_connect()) as connection:
        connection.execute("DELETE FROM push_subscriptions WHERE endpoint = ?",
                           (body.endpoint,))
        connection.execute("DELETE FROM push_sent WHERE endpoint = ?",
                           (body.endpoint,))
        connection.commit()
    return {"ok": True}


# --- Рассылка -----------------------------------------------------------------

def _drop(connection: sqlite3.Connection, endpoint: str) -> None:
    connection.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    connection.execute("DELETE FROM push_sent WHERE endpoint = ?", (endpoint,))


def _send(connection: sqlite3.Connection, row: sqlite3.Row, payload: dict) -> None:
    try:
        webpush(
            subscription_info={
                "endpoint": row["endpoint"],
                "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
            },
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=str(VAPID_PATH),
            vapid_claims=dict(VAPID_CLAIMS),
            ttl=1800,
        )
    except WebPushException as error:
        status = getattr(error.response, "status_code", None)
        # 404/410 — подписки больше нет; остальное — временное, повторим
        # на следующем событии.
        if status in (404, 410):
            _drop(connection, row["endpoint"])


def deliver_once(snapshot: dict) -> int:
    """Разослать свежие тревоги и отбои по подписанным зонам. Синхронно."""
    events = snapshot.get("events") or []
    if not events:
        return 0

    sent_count = 0
    with closing(_connect()) as connection:
        subscriptions = connection.execute(
            "SELECT endpoint, p256dh, auth, zones FROM push_subscriptions"
        ).fetchall()
        if not subscriptions:
            return 0

        now = int(time.time())
        connection.execute("DELETE FROM push_sent WHERE sent_at < ?",
                           (now - SENT_TTL_SEC,))

        for row in subscriptions:
            try:
                zones = set(json.loads(row["zones"]))
            except ValueError:
                zones = set()
            if not zones:
                continue
            for event in events:
                if not zones.intersection(event.get("zone_path") or []):
                    continue
                cleared = event.get("status") == "resolved"
                key = f"{event['id']}:clear" if cleared else event["id"]
                known = connection.execute(
                    "SELECT 1 FROM push_sent WHERE endpoint = ? AND event_key = ?",
                    (row["endpoint"], key),
                ).fetchone()
                if known:
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO push_sent (endpoint, event_key, sent_at)"
                    " VALUES (?,?,?)", (row["endpoint"], key, now))
                # Заголовок — место, тело — что происходит. Прежний
                # вариант звал любое событие «Тревогой», даже перекрытый
                # мост, а в теле стояло только имя города.
                place = event.get("place_name") or "По вашему месту"
                sentence = event_sentence(event)
                _send(connection, row, {
                    "title": f"{place} — отбой" if cleared else place,
                    "body": sentence[0].upper() + sentence[1:],
                    "tag": event["id"],
                })
                sent_count += 1
        connection.commit()
    return sent_count


async def deliver_loop(snapshot_fn) -> None:
    """Фоновый цикл рассыльщика внутри процесса API."""
    while True:
        try:
            snapshot = await asyncio.to_thread(snapshot_fn)
            await asyncio.to_thread(deliver_once, snapshot)
        except Exception:
            # Рассылка — не повод ронять API; следующий такт попробует снова.
            pass
        await asyncio.sleep(PUSH_TICK_SEC)
