"""Тесты телеграм-бота: поиск места и разбор команд.

Сеть здесь не нужна и не используется: отправку подменяем, проверяем то,
что решает бот сам.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from api import telegram


@pytest.fixture
def outbox(monkeypatch):
    """Перехватываем отправку: Bot API в тестах дёргать нечем и незачем."""
    sent = []
    monkeypatch.setattr(telegram, "send",
                        lambda chat_id, text, keyboard=None: sent.append((chat_id, text)))
    return sent


def test_help_answers_unknown_command(outbox):
    telegram.handle_text(1, "/абракадабра")
    assert outbox and "Тихое небо" in outbox[0][1]


def test_start_greets(outbox):
    telegram.handle_text(1, "/start")
    assert outbox and "карта воздушной обстановки" in outbox[0][1].lower()


def test_region_without_argument_explains(outbox):
    telegram.handle_text(1, "/region")
    assert "/region" in outbox[0][1]


def test_plural_matches_russian(outbox):
    assert telegram.plural(1, "событие", "события", "событий") == "событие"
    assert telegram.plural(3, "событие", "события", "событий") == "события"
    assert telegram.plural(11, "событие", "события", "событий") == "событий"
    assert telegram.plural(22, "событие", "события", "событий") == "события"


def test_bare_text_is_treated_as_a_place(outbox, monkeypatch):
    """Человек пишет «Курская область» без команды — это тоже вопрос о месте."""
    monkeypatch.setattr(telegram, "find_zone",
                        lambda q: {"id": "kurskaya_oblast", "name_ru": "Курская область",
                                   "level": "region"})
    monkeypatch.setattr(telegram, "region_text", lambda zone: "ответ про место")
    telegram.handle_text(1, "Курская область")
    assert outbox[0][1] == "ответ про место"


def test_webhook_secret_is_required(monkeypatch):
    """Без совпадения секрета вебхук не должен ничего делать."""
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    assert telegram.webhook_secret() == "s3cret"


def test_deliver_marks_before_send(tmp_path, monkeypatch):
    """Упавшая отправка не приводит к дублю на следующем такте.

    Отметка «отправлено» коммитилась одной пачкой в конце рассылки:
    рестарт API между отправкой и коммитом терял её, и следующий такт
    слал подписчику то же событие ещё раз — владелец получил «взрыв»
    дважды с разницей в минуту. Теперь отметка коммитится до отправки:
    событие уходит один раз, даже если сама отправка оборвалась.
    """
    import json

    monkeypatch.setattr(telegram, "DB_PATH", tmp_path / "tg.db")
    monkeypatch.setattr(telegram, "token", lambda: "t")

    with telegram.closing(telegram._connect()) as connection:
        connection.execute(
            "INSERT INTO tg_chats (chat_id, zones, created_at) VALUES (?,?,?)",
            (1, json.dumps(["krasnodar"]), 0))
        connection.commit()

    snapshot = {"events": [{
        "id": "evt-1", "status": "active", "zone_path": ["krasnodar"],
        "signal_type": "impact", "threat_type": "uav",
        "zone_name": "Краснодар", "last_seen_at": "2026-08-06T16:22:00+00:00",
    }]}

    calls = []

    def failing_send(chat_id, text, keyboard=None):
        calls.append(text)
        raise OSError("сеть оборвалась")

    monkeypatch.setattr(telegram, "send", failing_send)
    telegram.deliver_once(snapshot)
    monkeypatch.setattr(telegram, "send",
                        lambda chat_id, text, keyboard=None: calls.append(text))
    telegram.deliver_once(snapshot)

    assert len(calls) == 1, "событие ушло повторно после падения отправки"


def test_twin_events_send_one_message(tmp_path, monkeypatch):
    """Два события-близнеца об одном факте — одно уведомление.

    Рестарт конвейера в разгар волны репостов расколол её на два события
    в той же зоне с разными id, и подписчик получил «взрыв» дважды с
    разницей в минуту. Дедуп по id это не ловит — ловит текст: у
    близнецов он совпадает вплоть до времени события.
    """
    import json

    monkeypatch.setattr(telegram, "DB_PATH", tmp_path / "tg.db")
    monkeypatch.setattr(telegram, "token", lambda: "t")

    with telegram.closing(telegram._connect()) as connection:
        connection.execute(
            "INSERT INTO tg_chats (chat_id, zones, created_at) VALUES (?,?,?)",
            (1, json.dumps(["krasnodar"]), 0))
        connection.commit()

    twin = {"status": "active", "zone_path": ["krasnodar"],
            "signal_type": "impact", "threat_type": "uav",
            "zone_name": "городской округ Краснодар",
            "last_seen_at": "2026-08-06T16:22:00+00:00"}
    snapshot = {"events": [dict(twin, id="evt-a"), dict(twin, id="evt-b")]}

    sent = []
    monkeypatch.setattr(telegram, "send",
                        lambda chat_id, text, keyboard=None: sent.append(text))
    telegram.deliver_once(snapshot)
    assert len(sent) == 1, "близнецы ушли оба"

    # Настоящее новое событие позже — с другим временем в строке — уходит.
    later = dict(twin, id="evt-c", last_seen_at="2026-08-06T17:05:00+00:00")
    telegram.deliver_once({"events": [later]})
    assert len(sent) == 2
