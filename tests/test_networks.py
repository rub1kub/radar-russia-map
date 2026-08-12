"""Тесты определения сетей по фактическим перепечаткам.

Достоверность события строится на независимости источников. Если два канала
одной редакции считать независимыми, число становится выдуманным, поэтому
поведение этого модуля стоит зафиксировать.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pipeline.db import SCHEMA
from pipeline.networks import (MIN_MATCHES, collect_pairs, components,
                               normalize, rebuild_networks)

BASE = "2026-07-27T10:%02d:%02dZ"
LONG = "Опасность по БПЛА в Тимашёвском районе Краснодарского края, "


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def post(connection, source, minute, second, text):
    connection.execute(
        "INSERT INTO raw_messages (source_key, message_id, posted_at, text)"
        " VALUES (?,?,?,?)",
        (source, hash((source, minute, second)) % 10**6, BASE % (minute, second), text),
    )


def test_normalize_strips_emoji_and_punctuation():
    assert normalize("❗️Краснодар — ОПАСНОСТЬ!!!") == "краснодар опасность"


def test_short_texts_do_not_link_channels(db):
    # «Отбой» независимые каналы пишут одинаково просто потому, что иначе никак.
    for minute in range(20):
        post(db, "alpha", minute, 0, "Отбой")
        post(db, "beta", minute, 5, "Отбой")
    assert collect_pairs(db) == {}


def test_verbatim_repost_links_channels(db):
    for minute in range(MIN_MATCHES + 2):
        text = LONG + f"сообщение {minute}"
        post(db, "alpha", minute, 0, text)
        post(db, "beta", minute, 30, text)
    pairs = collect_pairs(db)
    assert pairs[("alpha", "beta")] >= MIN_MATCHES


def test_same_text_far_apart_is_not_a_repost(db):
    # Через час это уже не перепечатка, а совпадение дежурной формулировки.
    for minute in range(MIN_MATCHES + 2):
        text = LONG + f"сообщение {minute}"
        post(db, "alpha", minute, 0, text)
        post(db, "beta", minute + 30, 0, text)
    assert collect_pairs(db).get(("alpha", "beta"), 0) == 0


def test_rare_coincidence_does_not_link(db):
    for minute in range(MIN_MATCHES - 2):
        text = LONG + f"сообщение {minute}"
        post(db, "alpha", minute, 0, text)
        post(db, "beta", minute, 10, text)
    assert components(collect_pairs(db)) == []


def test_components_merge_transitively():
    pairs = {("a", "b"): 10, ("b", "c"): 10, ("d", "e"): 10}
    groups = components(pairs)
    assert {frozenset(group) for group in groups} == {
        frozenset({"a", "b", "c"}),
        frozenset({"d", "e"}),
    }


def test_rebuild_stores_stable_network_id(db):
    for minute in range(MIN_MATCHES + 2):
        text = LONG + f"сообщение {minute}"
        post(db, "zeta", minute, 0, text)
        post(db, "alpha", minute, 20, text)
    stats = rebuild_networks(db)
    assert stats["networks"] == 1

    rows = {r["source_key"]: r["network_id"] for r in
            db.execute("SELECT source_key, network_id FROM source_networks")}
    # Идентификатор берётся по алфавитно первому каналу, чтобы не менялся
    # от запуска к запуску.
    assert set(rows) == {"alpha", "zeta"}
    assert set(rows.values()) == {"net:alpha"}


def test_rebuild_is_idempotent(db):
    for minute in range(MIN_MATCHES + 2):
        text = LONG + f"сообщение {minute}"
        post(db, "alpha", minute, 0, text)
        post(db, "beta", minute, 20, text)
    first = rebuild_networks(db)
    second = rebuild_networks(db)
    assert first == second
    assert db.execute("SELECT COUNT(*) n FROM source_networks").fetchone()["n"] == 2


def test_low_share_pair_is_not_a_network():
    """Пять совпадений за три месяца — случайность, если каналы большие.

    Через такие рёбра транзитивное замыкание склеивало 162 канала в одну
    сеть. Клоны выдаёт доля: у ферм меньший канал совпадает на 20–92%.
    """
    pairs = {("a", "b"): 10}
    totals = {"a": 1000, "b": 1000}   # 10/1000 = 1% — совпадение, не сеть
    assert components(pairs, totals) == []


def test_high_share_pair_is_a_network():
    pairs = {("a", "b"): 10}
    totals = {"a": 1000, "b": 20}     # 10/20 = 50% — половина канала
    assert {frozenset(g) for g in components(pairs, totals)} == {frozenset({"a", "b"})}


def test_official_channel_never_joins_a_network():
    """Совпадение с губернатором — это цитата его сообщения, а не клон."""
    pairs = {("gov", "lenta"): 50, ("lenta", "lenta2"): 50}
    totals = {"gov": 60, "lenta": 60, "lenta2": 60}
    groups = components(pairs, totals, official={"gov"})
    assert {frozenset(g) for g in groups} == {frozenset({"lenta", "lenta2"})}
