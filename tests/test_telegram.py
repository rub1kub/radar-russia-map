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
