"""Нормализация русских топонимов: slug, ключ сопоставления, варианты имени."""

from __future__ import annotations

import re

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Типовые части названий административных единиц и населенных пунктов.
UNIT_WORDS = (
    "муниципальный округ", "городской округ", "городское поселение",
    "сельское поселение", "муниципальный район", "городской район",
    "автономный округ", "автономная область", "народная республика",
    "район", "округ", "область", "край", "республика", "поселение",
    "г\\.", "город", "пгт", "станица", "ст-ца", "поселок", "посёлок",
    "село", "деревня", "хутор", "аул", "слобода", "рп",
)
UNIT_RE = re.compile(r"\b(" + "|".join(UNIT_WORDS) + r")\b", re.IGNORECASE)
PUNCT_RE = re.compile(r"[^\w\s\-]", re.UNICODE)
SPACE_RE = re.compile(r"\s+")


def slugify(text: str) -> str:
    """Латинский slug для стабильного zone_id."""
    lowered = text.lower().strip()
    out = []
    for char in lowered:
        if char in TRANSLIT:
            out.append(TRANSLIT[char])
        elif char.isalnum():
            out.append(char)
        elif char in " -_/":
            out.append("_")
    slug = "".join(out)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "zone"


def norm_key(text: str) -> str:
    """Ключ сопоставления: без пунктуации, ё->е, схлопнутые пробелы."""
    lowered = text.lower().replace("ё", "е").replace("​", " ")
    lowered = PUNCT_RE.sub(" ", lowered)
    return SPACE_RE.sub(" ", lowered).strip()


def strip_unit(text: str) -> str:
    """Убрать типовое слово: 'Азовский район' -> 'азовский'."""
    return SPACE_RE.sub(" ", UNIT_RE.sub(" ", norm_key(text))).strip()


def name_variants(name: str, level: str) -> set[str]:
    """Набор ключей, по которым зону можно найти в тексте сообщения."""
    variants = {norm_key(name)}

    core = strip_unit(name)
    if core and len(core) >= 3:
        variants.add(core)

    # Дефис часто пишут по-разному: Ростов-на-Дону / Ростов на Дону.
    for variant in list(variants):
        if "-" in variant:
            variants.add(variant.replace("-", " "))

    return {v for v in variants if len(v) >= 3}
