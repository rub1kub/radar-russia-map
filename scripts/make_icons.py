"""Знак карты — отметка на радаре — во всех размерах, какие спрашивают.

    ingest/.venv/bin/python scripts/make_icons.py

Раньше иконки были нарисованы разово и руками: перерисовать их значило
подбирать радиусы заново. Здесь одна геометрия на всех — векторная
favicon.svg и растровые копии с неё же.

Что кому нужно:
  · Яндекс берёт SVG в первую очередь, а из растровых предпочитает 32×32;
  · Google просит квадрат больше 48×48 и умеет тот же SVG;
  · iOS кладёт на домашний экран apple-touch-icon 180×180;
  · старые браузеры знают только favicon.ico — оставляем четыре размера.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"

# Геометрия в долях от стороны. Тёмный круг во весь квадрат, бледное
# кольцо развёртки, красная отметка в центре — тот же знак, что в шапке.
SHELL = (21, 26, 24, 255)
MARK = (233, 62, 78, 255)
RING = (232, 62, 77, 115)      # тот же красный сквозь тёмный фон
MARK_RADIUS = 0.1406           # 72/512
RING_INNER = 0.2676            # 137/512
RING_OUTER = 0.3438            # 176/512

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <title>Тихое небо</title>
  <circle cx="256" cy="256" r="256" fill="#151a18"/>
  <circle cx="256" cy="256" r="156" fill="none" stroke="#e83e4d"
          stroke-opacity="0.45" stroke-width="39"/>
  <circle cx="256" cy="256" r="72" fill="#e93e4e"/>
</svg>
"""


def render(size: int) -> Image.Image:
    """Рисуем вчетверо крупнее и уменьшаем: у кругов иначе рваный край."""
    scale = 4
    canvas = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    side = size * scale
    center = side / 2

    def circle(radius: float, **kwargs) -> None:
        draw.ellipse([center - radius, center - radius,
                      center + radius, center + radius], **kwargs)

    circle(center, fill=SHELL)
    ring_width = (RING_OUTER - RING_INNER) * side
    circle((RING_INNER + RING_OUTER) / 2 * side, outline=RING,
           width=max(1, round(ring_width)))
    circle(MARK_RADIUS * side, fill=MARK)
    return canvas.resize((size, size), Image.LANCZOS)


def main() -> int:
    (PUBLIC / "favicon.svg").write_text(SVG, encoding="utf-8")
    print("favicon.svg")

    # Внутри .ico те размеры, что спрашивают браузеры и поисковики.
    icon_sizes = [16, 32, 48, 64]
    render(64).save(PUBLIC / "favicon.ico", format="ICO",
                    sizes=[(size, size) for size in icon_sizes])
    print(f"favicon.ico — {', '.join(f'{s}×{s}' for s in icon_sizes)}")

    for name, size in (("apple-touch-icon.png", 180),
                       ("icon-192.png", 192),
                       ("icon-512.png", 512)):
        render(size).save(PUBLIC / name)
        print(f"{name} — {size}×{size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
