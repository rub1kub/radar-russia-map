"""Инфраструктура API: настройки окружения, TTL-кеш и лимит частоты.

Вынесено из server.py, потому что к предметной области радара не относится и
нужно только при работе не на localhost, где клиентов больше одного.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from typing import Callable, TypeVar

T = TypeVar("T")

# Дефолт — dev-сервер Vite, чтобы локальный запуск работал без переменных.
DEFAULT_CORS_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")
DEFAULT_RATE_LIMIT = 120
# Сколько своих прокси стоит перед сервисом. Ровно столько последних записей
# в X-Forwarded-For дописаны доверенным звеном, всё левее прислал клиент.
DEFAULT_PROXY_DEPTH = 1


def env_int(name: str, default: int) -> int:
    """Мусор в переменной не должен ронять сервер на старте."""
    try:
        value = int(os.getenv(name, "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def cors_origins() -> list[str]:
    """RADAR_CORS_ORIGINS: список через запятую."""
    raw = os.getenv("RADAR_CORS_ORIGINS", "")
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or list(DEFAULT_CORS_ORIGINS)


UNTRUSTED_BUCKET = "untrusted-forwarded"


def client_ip(request, trust_proxy: bool, proxy_depth: int = DEFAULT_PROXY_DEPTH) -> str:
    """Ключ, по которому считается лимит.

    X-Forwarded-For учитывается только за доверенным прокси: иначе любой
    клиент подставит произвольный адрес в заголовок и лимит обойдётся
    одной строкой в curl.
    """
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    real = request.headers.get("x-real-ip", "").strip()

    if trust_proxy:
        chain = [item.strip() for item in forwarded.split(",") if item.strip()]
        if chain:
            # Берём адрес, дописанный ближайшим ДОВЕРЕННЫМ прокси, то есть
            # отсчитываем справа. nginx с $proxy_add_x_forwarded_for приклеивает
            # настоящий адрес в конец, сохраняя всё, что прислал клиент, поэтому
            # левый элемент цепочки подделывается свободно: с ним лимит
            # обходился сменой заголовка на каждый запрос. Если реальных звеньев
            # меньше, чем заявлено в proxy_depth, откатываемся к самому левому —
            # это пере-, а не недоограничение.
            return chain[-min(proxy_depth, len(chain))]
        if real:
            return real
    elif forwarded or real:
        # Прокси не доверяем, но и request.client уже ничего не гарантирует:
        # uvicorn по умолчанию идёт с --proxy-headers и для соединений с
        # 127.0.0.1 сам переписывает адрес значением из заголовка. Поэтому
        # все запросы с этими заголовками делят одну корзину: подделка
        # заголовка перестаёт размножать лимит. Настоящий прокси лечится
        # переменной RADAR_TRUST_PROXY=1.
        return UNTRUSTED_BUCKET

    client = getattr(request, "client", None)
    return client.host if client else "unknown"


class TTLCache:
    """Кеш одного значения на ttl секунд.

    Инвалидация только по времени: базу наполняет отдельный процесс сбора,
    события «данные изменились» у API нет и взяться ему неоткуда.

    Значение отдаётся всем вызывающим как есть, без копии: снимок обстановки
    крупный, а копировать его на каждый запрос — это ровно та работа, ради
    снятия которой кеш и заводился. Поэтому полученный объект менять нельзя.
    """

    def __init__(self, ttl_sec: float) -> None:
        self._ttl = ttl_sec
        self._lock = threading.Lock()
        self._value: object | None = None
        self._expires_at = 0.0

    def get(self, producer: Callable[[], T]) -> T:
        # Лок держится и на время расчёта: иначе десяток клиентов, пришедших
        # в одну секунду, запустит десяток одинаковых пачек SQL-запросов —
        # ровно та нагрузка, ради снятия которой кеш и заводился.
        with self._lock:
            if self._value is not None and time.monotonic() < self._expires_at:
                return self._value  # type: ignore[return-value]
            value = producer()
            self._value = value
            self._expires_at = time.monotonic() + self._ttl
            return value


class RateLimiter:
    """Скользящее окно на минуту, отдельное на каждый ключ (IP).

    In-process и без внешних зависимостей: инстанс uvicorn один, заводить
    Redis ради счётчика запросов не за чем.
    """

    def __init__(self, limit: int, window_sec: float = 60.0) -> None:
        self.limit = limit
        self._window = window_sec
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}
        self._next_sweep = 0.0

    def check(self, key: str) -> int:
        """0 — пропускать; иначе значение Retry-After в секундах."""
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            hits = self._hits.setdefault(key, deque())
            edge = now - self._window
            while hits and hits[0] <= edge:
                hits.popleft()
            if len(hits) >= self.limit:
                # Слот освободится, когда из окна выпадет самый старый запрос.
                return max(1, math.ceil(hits[0] + self._window - now))
            hits.append(now)
            return 0

    def _sweep(self, now: float) -> None:
        """Без уборки словарь растёт на каждый новый адрес и не убывает."""
        if now < self._next_sweep:
            return
        self._next_sweep = now + self._window
        edge = now - self._window
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] <= edge]
        for key in stale:
            del self._hits[key]
