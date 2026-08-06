"""Иконки сайта: то, по чему сайт узнают в выдаче и в закладках.

Значок в поиске стоит дёшево и ломается тихо: битая ссылка в <head> или
пропавший файл никак не видны на самой карте, а Яндекс с Google обходят
фавиконку неделями — ошибку замечаешь через месяц по пустому квадрату
в выдаче.
"""

from __future__ import annotations

import re
import struct

import pytest
from PIL import Image

from pipeline.db import ROOT

HEAD = (ROOT / "index.html").read_text(encoding="utf-8")
ICON_LINKS = re.findall(r'<link rel="(icon|apple-touch-icon)"[^>]*href="([^"]+)"[^>]*>', HEAD)


def public(href: str):
    return ROOT / "public" / href.lstrip("/")


def test_head_lists_icons():
    """Ссылки на месте — вектор, .ico и иконка для домашнего экрана."""
    hrefs = {href for _, href in ICON_LINKS}
    assert {"/favicon.ico", "/favicon.svg", "/apple-touch-icon.png"} <= hrefs


@pytest.mark.parametrize("href", sorted({href for _, href in ICON_LINKS}))
def test_icon_file_exists(href):
    assert public(href).is_file(), f"{href} нет в public/"


def test_ico_carries_the_sizes_search_engines_ask_for():
    """32×32 — тот размер, который Яндекс берёт в выдачу; 48 просит Google."""
    data = public("/favicon.ico").read_bytes()
    count = struct.unpack("<H", data[4:6])[0]
    sizes = {struct.unpack("<BB", data[6 + index * 16:8 + index * 16])[0] or 256
             for index in range(count)}
    assert {16, 32, 48} <= sizes, f"в .ico только {sorted(sizes)}"


def test_svg_is_self_contained_and_scalable():
    """Вектор без viewBox не масштабируется, а со ссылкой наружу — не грузится."""
    svg = public("/favicon.svg").read_text(encoding="utf-8")
    assert "viewBox" in svg
    assert "http://www.w3.org/2000/svg" in svg
    assert not re.search(r'(?:href|src)\s*=\s*"(?!#)(?:https?:)?//', svg)


@pytest.mark.parametrize("name", ["apple-touch-icon.png", "icon-192.png", "icon-512.png"])
def test_raster_icons_are_square(name):
    """Не квадрат поисковики просто не берут."""
    width, height = Image.open(ROOT / "public" / name).size
    assert width == height, f"{name}: {width}×{height}"
