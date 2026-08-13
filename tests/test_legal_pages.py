"""Правовые страницы: что в них обязано быть.

    ingest/.venv/bin/python -m pytest tests/test_legal_pages.py -q

Текст этих страниц — обещание. Если он разойдётся с тем, что сервис
делает на самом деле, документ станет хуже, чем его отсутствие.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.legal_pages import PRIVACY_BODY, TERMS_BODY, shell


def test_privacy_names_every_kind_of_data_we_store():
    """Всё, что лежит в базе и логах, должно быть названо в политике."""
    for promise in ["Идентификатор чата", "отслеживаемых мест",
                    "Журнал обращений", "IP-адрес"]:
        assert promise in PRIVACY_BODY


def test_privacy_promises_a_working_deletion_command():
    """Право на удаление — не декларация: команда должна существовать."""
    from api.telegram import forget  # noqa: F401 — важно, что импортируется
    assert "/stop" in PRIVACY_BODY


def test_privacy_declares_retention_for_logs_and_journal():
    assert "7 суток" in PRIVACY_BODY      # веб-логи
    assert "90 суток" in PRIVACY_BODY     # журнал бота


def test_terms_say_the_three_things_that_matter():
    for claim in ["Не является средством массовой информации",
                  "Не является системой оповещения",
                  "Не является руководством к действию"]:
        assert claim in TERMS_BODY


def test_terms_state_what_the_map_deliberately_omits():
    """Обещание не публиковать прилёты закреплено в конвейере тестами."""
    assert "не публикует места попаданий" in TERMS_BODY
    assert "муниципального района" in TERMS_BODY


@pytest.mark.parametrize("body", [PRIVACY_BODY, TERMS_BODY])
def test_page_renders_with_navigation(body):
    html = shell("Заголовок", "Описание", "/privacy/", body)
    assert "<title>Заголовок · Тихое небо</title>" in html
    assert 'href="/terms/"' in html and 'href="/privacy/"' in html
    assert 'rel="canonical"' in html
