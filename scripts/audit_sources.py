"""Проверка каналов-источников по признакам, требующим маркировки.

    PYTHONPATH=.:ingest ingest/.venv/bin/python -m scripts.audit_sources

Републикация материалов иноагента без маркировки и любое цитирование
нежелательной организации — отдельные основания придраться, никак не
связанные с существом карты. Реестры Минюста ведутся по людям и
организациям, а не по адресам Telegram-каналов, поэтому автоматически
сопоставить их с нашим списком нельзя: скрипт не выносит вердиктов, он
сужает поле для просмотра глазами.

Что делает:

  • отбирает каналы, чьё имя похоже на СМИ или НКО, — именно среди них
    теоретически возможны designated-организации, а не среди «Радаров»
    и «Куполов»;
  • сверяет названия с реестром, если его файл скачан вручную
    (--registry путь): сам реестр Минюста отдаётся страницей-приложением,
    и надёжно выкачать его скриптом нельзя — пробовал;
  • печатает полный список источников для просмотра глазами.

Вердикт по каждому каналу принимает владелец. Реестры:

    иноагенты     https://minjust.gov.ru/ru/pages/reestr-inostryannykh-agentov/
    нежелательные https://minjust.gov.ru/ru/activity/directions/941/
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))

from config import sources_from_env

# Страницы реестров — для человека, не для скрипта: содержимое
# подгружается приложением, в HTML его нет. Проверено 13 августа.
REGISTRY_PAGES = {
    "иноагенты": "https://minjust.gov.ru/ru/pages/reestr-inostryannykh-agentov/",
    "нежелательные": "https://minjust.gov.ru/ru/activity/directions/941/",
}

# Слова, по которым канал похож на медиа или НКО, а не на ленту
# мониторинга. Среди «Радар Курск» designated-организаций не бывает,
# среди «Вестника» и «Новостей» — теоретически возможны.
MEDIA_HINT_RE = re.compile(
    r"новост|вестник|медиа|газет|журнал|издани|инфо|news|press|пресс"
    r"|телеканал|тв\b|радио|агентств|фонд|правозащит|комитет",
    re.IGNORECASE,
)

# Ленты мониторинга: по названию видно, что это оповещения, а не редакция.
MONITORING_RE = re.compile(
    r"радар|radar|тревог|trevog|бпла|bpla|пво|мониторинг|monitoring"
    r"|сирен|siren|небо|nebo|дозор|dozor|локатор|locator|рсчс|мчс"
    r"|alarm|alert|купол|kupol",
    re.IGNORECASE,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Аудит источников по реестрам")
    parser.add_argument("--registry", type=Path, action="append", default=[],
                        help="файл скачанного реестра (txt/csv); можно несколько")
    args = parser.parse_args()

    sources = sources_from_env()
    print(f"источников всего: {len(sources)}\n")

    haystack = ""
    for path in args.registry:
        try:
            haystack += path.read_text(encoding="utf-8", errors="replace").lower()
            print(f"реестр загружен из файла: {path}")
        except OSError as error:
            print(f"не прочитать {path}: {error}")
    if not args.registry:
        print("Файл реестра не передан (--registry). Сверять не с чем — "
              "скачайте вручную:")
        for name, url in REGISTRY_PAGES.items():
            print(f"  {name:14} {url}")
    print()

    if haystack:
        hits = [s for s in sources
                if len(s.label) >= 5 and s.label.lower() in haystack]
        if hits:
            print("СОВПАДЕНИЯ С РЕЕСТРОМ — проверить вручную:")
            for source in hits:
                print(f"  {source.key} — {source.label} (@{source.username})")
        else:
            print("Совпадений названий с реестром нет.")
        print()

    suspicious = [s for s in sources
                  if MEDIA_HINT_RE.search(s.label)
                  and not MONITORING_RE.search(s.label)]
    print(f"похожи на медиа или НКО, а не на ленту мониторинга: {len(suspicious)}"
          f" из {len(sources)}")
    print("(именно здесь возможны designated-организации — смотреть глазами)")
    for source in sorted(suspicious, key=lambda s: s.label):
        print(f"  {source.tier:9} {source.label}  (@{source.username})")
    print()

    print("Полный список источников:")
    for source in sorted(sources, key=lambda s: (s.tier, s.label)):
        print(f"  {source.tier:9} {source.label}  (@{source.username})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
