"""OG-превью посадочных страниц: карточка 1200×630 под ссылку в мессенджере.

У всех страниц стояла одна картинка сайта, и репост сводки по Курской
области выглядел как репост главной. Своя карточка с именем места и живой
цифрой месяца заметнее в ленте и честнее описывает, что за ссылкой.

Картинка перерисовывается только когда изменился текст на ней: тот же
регион с теми же цифрами — тот же PNG, диск не дёргаем.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.db import ROOT

OUT = ROOT / "dist" / "og"
SIZE = (1200, 630)
BG = (11, 15, 14)
CARD = (16, 22, 20)
TEXT = (230, 235, 230)
MUTED = (139, 154, 145)
ACCENT = (233, 62, 78)
GREEN = (159, 212, 176)

# Первый существующий шрифт из списка: сервер — Ubuntu с DejaVu, дома —
# macOS со своими путями. Кириллицу покрывают оба.
FONTS_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)
FONTS_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
)


def _font(candidates: tuple[str, ...], size: int):
    from PIL import ImageFont
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _fit(draw, text: str, candidates: tuple[str, ...], start: int,
         max_width: int):
    """Крупный шрифт, ужатый до ширины карточки."""
    size = start
    while size > 28:
        font = _font(candidates, size)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 4
    return _font(candidates, 28)


def render(path: Path, name: str, line: str) -> None:
    from PIL import Image, ImageDraw
    image = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(image)
    # Тонкая красная планка сверху — фирменный акцент карты.
    draw.rectangle((0, 0, SIZE[0], 10), fill=ACCENT)

    kicker = _font(FONTS_REGULAR, 40)
    draw.text((80, 130), "Карта БПЛА и воздушной тревоги",
              font=kicker, fill=MUTED)

    title_font = _fit(draw, name, FONTS_BOLD, 92, SIZE[0] - 160)
    draw.text((80, 210), name, font=title_font, fill=TEXT)

    if line:
        stats_font = _font(FONTS_REGULAR, 36)
        draw.text((80, 360), line, font=stats_font, fill=GREEN)

    brand = _font(FONTS_BOLD, 40)
    draw.text((80, 500), "Тихое небо", font=brand, fill=TEXT)
    site = _font(FONTS_REGULAR, 34)
    offset = draw.textlength("Тихое небо  ", font=brand)
    draw.text((80 + offset, 505), "tihoenebo.com", font=site, fill=MUTED)

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)


def build(cards: list[tuple[str, str, str]]) -> int:
    """cards: (имя файла без расширения, название места, строка цифр).

    Возвращает число перерисованных. Отпечаток текста лежит рядом в
    manifest.json: совпал — файл не трогаем.
    """
    manifest_path = OUT / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    drawn = 0
    for stem, name, line in cards:
        digest = hashlib.md5(f"{name}|{line}".encode("utf-8")).hexdigest()
        target = OUT / f"{stem}.png"
        if manifest.get(stem) == digest and target.exists():
            continue
        render(target, name, line)
        manifest[stem] = digest
        drawn += 1
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return drawn
