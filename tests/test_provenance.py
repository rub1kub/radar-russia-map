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
