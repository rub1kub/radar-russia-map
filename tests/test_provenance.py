"""Кто засчитан в подтверждение события.

    ingest/.venv/bin/python -m pytest tests/test_provenance.py -q

Правило живёт в одном месте: и число под заголовком, и список источников
считаются им же. Раньше их считали порознь, и в шапке стояло 20 там, где в
списке набиралось 16 — карту нельзя было проверить.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.provenance import counted, walk

LONG = (
    "Темрюкский район, Краснодарский край — опасность по БПЛА, ударные "
    "беспилотники идут с моря на восток, ожидается работа ПВО, оставайтесь "
    "в укрытиях до сигнала отбоя"
)


def row(source, role="confirm", text="Опасность по БПЛА", at="2026-07-28T21:00:00Z"):
    return {"source_key": source, "role": role, "contributed_at": at, "text": text}


def test_each_channel_counts_once():
    rows = [row("a", "first"), row("b"), row("c")]
    assert counted(rows, {}) == 3


def test_repeat_from_the_same_channel_is_not_a_voice():
    rows = [row("a", "first"), row("a", "repeat"), row("b")]
    assert counted(rows, {}) == 2


def test_verbatim_repost_is_not_a_voice():
    rows = [row("a", "first", LONG), row("b", "confirm", LONG)]
    marked = walk(rows, {})
    assert [item.counted for item in marked] == [True, False]
    assert marked[1].repost is True


def test_same_network_speaks_once():
    rows = [row("a", "first"), row("b"), row("c")]
    marked = walk(rows, {"a": "lpr1", "b": "lpr1"})
    assert [item.counted for item in marked] == [True, False, True]
    assert marked[1].clone is True


def test_repeat_still_claims_the_text():
    """Право первым сказать закрепляется и повтором.

    Иначе первым окажется другой канал, и пометка в списке разойдётся с
    числом под заголовком — ровно та ошибка, ради которой модуль и появился.
    """
    rows = [row("a", "repeat", LONG), row("b", "confirm", LONG)]
    marked = walk(rows, {})
    # Повтор в список не попадает, но текст за собой оставляет.
    assert len(marked) == 1
    assert marked[0].source_key == "b"
    assert marked[0].repost is True
    assert counted(rows, {}) == 0


def test_short_identical_text_is_not_a_repost():
    """«Опасность по БПЛА» две ленты пишут одинаково — иначе не скажешь."""
    rows = [row("a", "first"), row("b")]
    assert counted(rows, {}) == 2


# --- Ссылка на сообщение ----------------------------------------------------

def test_link_points_at_the_original_message():
    """Ник без ссылки — число, которое остаётся принимать на веру."""
    rows = [{"source_key": "locatorru", "role": "first", "contributed_at": "2026-07-28T21:00:00Z",
             "text": "Опасность по БПЛА", "message_id": 8166}]
    marked = walk(rows, {}, {"locatorru": "locatorru"})
    assert marked[0].link == "https://t.me/locatorru/8166"


def test_link_is_absent_when_channel_is_unknown():
    rows = [{"source_key": "ghost", "role": "first", "contributed_at": "2026-07-28T21:00:00Z",
             "text": "Опасность по БПЛА", "message_id": 5}]
    assert walk(rows, {}, {})[0].link is None


# Типовая строка кубанских лент: 95 символов, четырнадцать каналов слово в
# слово. При пороге в 120 символов она не считалась перепечаткой, и событие
# над Сочи показывало «18 источников» там, где независимых голосов четыре.
KUBAN_BOILERPLATE = (
    "От Тамани до Сочи\nтревога по БПЛА сохраняется\n"
    "Соблюдайте меры безопасности\nПри сбитиях БПЛА\nКраснодарский край"
)


def test_short_boilerplate_repeated_verbatim_is_one_voice():
    rows = [row("a", "first", KUBAN_BOILERPLATE),
            row("b", "confirm", KUBAN_BOILERPLATE),
            row("c", "confirm", KUBAN_BOILERPLATE)]
    marked = walk(rows, {})
    assert [item.counted for item in marked] == [True, False, False]
    assert counted(rows, {}) == 1


def test_two_lines_saying_the_same_short_thing_are_still_two_voices():
    """«Опасность БПЛА» иначе и не напишешь — это не перепечатка."""
    rows = [row("a", "first", "Анапа опасность БПЛА"),
            row("b", "confirm", "Анапа опасность БПЛА")]
    assert counted(rows, {}) == 2
