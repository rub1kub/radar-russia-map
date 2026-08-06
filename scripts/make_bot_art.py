"""Аватарка и баннер телеграм-бота — тот же знак, что у сайта.

    ingest/.venv/bin/python scripts/make_bot_art.py

Размеры диктует BotFather: аватарка — квадрат (Telegram обрежет её в
круг), баннер стартовой страницы — 640×360. Текста на картинках нет
намеренно: под баннером мессенджер сам печатает описание, а подпись
внутри изображения в этом блоке читается как шум.

Знак один и тот же — красная отметка в кольце развёртки, как в
scripts/make_icons.py. Баннер добавляет к нему расходящиеся кольца:
это то же самое место, только видно дальше.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "telegram"

BACKDROP = (11, 13, 13, 255)        # --bg
SURFACE = (21, 26, 24, 255)         # фон знака, как в иконке
MARK = (233, 62, 78, 255)           # отметка
# Кольцо развёртки. В фавиконке оно полупрозрачное и розовеет о белый фон
# вкладки; здесь фон свой, тёмный, и та же альфа дала бы бордовый ободок.
# Берём цвет готовым — тот, каким знак видят в выдаче.
RING = (245, 168, 175, 255)
SWEEP = (245, 168, 175, 46)         # дальние волны
GRID = (238, 242, 236, 16)          # едва различимая сетка

SCALE = 4                           # рисуем крупнее и уменьшаем: края


def circle(draw: ImageDraw.ImageDraw, center, radius: float, **kwargs) -> None:
    x, y = center
    draw.ellipse([x - radius, y - radius, x + radius, y + radius], **kwargs)


def over(image: Image.Image, paint) -> None:
    """Положить полупрозрачное поверх непрозрачного.

    ImageDraw полупрозрачный цвет не смешивает, а замещает им пиксель:
    бледное кольцо развёртки выходило сплошь красным. Рисуем на пустом
    слое и накладываем — только так альфа работает.
    """
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    paint(ImageDraw.Draw(layer))
    image.alpha_composite(layer)


def avatar(side: int = 512) -> Image.Image:
    """Квадрат под круглую обрезку: всё важное держим в середине."""
    size = (side * SCALE, side * SCALE)
    image = Image.new("RGBA", size, SURFACE)
    center = (size[0] / 2, size[1] / 2)
    unit = size[0]

    # Дальнее кольцо у самого края: под круглой обрезкой оно читается
    # ободком, а знак в середине остаётся тем же, что в фавиконке.
    over(image, lambda draw: circle(draw, center, unit * 0.435,
                                    outline=SWEEP, width=round(unit * 0.010)))
    over(image, lambda draw: circle(draw, center, unit * 0.305,
                                    outline=RING, width=round(unit * 0.076)))
    circle(ImageDraw.Draw(image), center, unit * 0.141, fill=MARK)
    return image.resize((side, side), Image.LANCZOS)


def banner(width: int = 640, height: int = 360) -> Image.Image:
    """Баннер стартовой страницы: тот же знак, но видно дальше."""
    size = (width * SCALE, height * SCALE)
    image = Image.new("RGBA", size, BACKDROP)
    center = (size[0] / 2, size[1] / 2)
    unit = height * SCALE          # всё меряем по высоте: она тут короче

    # Сетка карты — четыре линии, ровно чтобы плоскость перестала быть
    # пустой. Больше не нужно: рядом мессенджер печатает описание.
    def grid(draw: ImageDraw.ImageDraw) -> None:
        for step in (0.25, 0.5, 0.75):
            x = size[0] * step
            draw.line([(x, 0), (x, size[1])], fill=GRID, width=SCALE)
        draw.line([(0, center[1]), (size[0], center[1])], fill=GRID, width=SCALE)

    over(image, grid)
    # Отметку видно и за пределами кольца: две волны уходят к краям.
    for radius in (0.86, 0.60):
        over(image, lambda draw, radius=radius: circle(
            draw, center, unit * radius, outline=SWEEP, width=round(unit * 0.005)))
    over(image, lambda draw: circle(draw, center, unit * 0.305,
                                    outline=RING, width=round(unit * 0.076)))
    circle(ImageDraw.Draw(image), center, unit * 0.141, fill=MARK)
    return image.resize((width, height), Image.LANCZOS)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    avatar().convert("RGB").save(OUT / "bot-avatar.png")
    print("assets/telegram/bot-avatar.png — 512×512")
    banner().convert("RGB").save(OUT / "bot-banner.png")
    print("assets/telegram/bot-banner.png — 640×360")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
