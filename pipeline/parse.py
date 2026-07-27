"""Разбор текста сообщения в структурированное наблюдение.

Покрывает пять семейств форматов из docs/TARGET_ARCHITECTURE.md §4.
Текст канала — недоверенные данные: разбирается регулярными выражениями,
никогда не исполняется.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .textnorm import norm_key

# --- Футеры подписки: у 6 каналов присутствуют в 98-100% сообщений ----------
FOOTER_RE = re.compile(
    r"(подписаться|подписка|наш\s+канал|@[a-zA-Z_][a-zA-Z0-9_]{3,}"
    r"|локатор\s+россии|мониторинг\s+кубани|кубанский\s+вестник"
    r"|краснодарский\s+дозор|дозор\s+краснодара|в\s+ма[хx]\b)",
    re.IGNORECASE,
)

# --- Нерелевантное: политические и военные новости --------------------------
NEWS_RE = re.compile(
    r"\b(мид|зеленск\w+|путин\w*|минобороны|санкц\w+|саммит\w*|переговор\w+"
    r"|президент\w*|госдум\w+|заявил|сообщил\s+журналист|написал\s+в\s+своем)\b",
    re.IGNORECASE,
)

# --- Сигнал: что произошло. Порядок важен, первое совпадение выигрывает -----
SIGNALS: list[tuple[str, str, int]] = [
    ("allclear",  r"\bотбой\b|угроза\s+мин\w*\s+нет|опасность\s+минова", 0),
    ("retracted", r"погодн\w+\s+услови|без\s+паники|ложн\w+\s+(тревог|сработ)", 0),
    ("impact",    r"\bвзрыв\w*\b|\bприлет\w*|\bгром\b", 9),
    ("intercept", r"\bсбити\w*|\bсбит\b|уничтожен\w*|работа\s+пво|\bпво\b", 8),
    ("alarm",     r"\bтревог\w*|\bсирен\w*|угроз\w+\s+(непосредств|удара|атаки)", 7),
    ("danger",    r"\bопасност\w*|беспилотная\s+опасность", 5),
    ("detection", r"\bфиксац\w*|\bпролет\w*|\bпролёт\w*|\bзамечен", 4),
    ("caution",   r"мер[ыу]\s+(безопасн|предостор)|\bвнимание\b|соблюда\w+\s+мер", 3),
    ("infra",     r"аэропорт|крымск\w+\s+мост|движение\s+автотранспорт", 2),
    # Голое «угроза БЭК», «угроза по области» — тоже оповещение.
    ("danger",    r"\bугроз\w*", 5),
]

# --- Тип угрозы -------------------------------------------------------------
THREATS: list[tuple[str, str]] = [
    ("fpv",      r"\bfpv\b|фпв\b"),
    ("rocket",   r"\bракет\w*|баллистик\w*|\bкалибр\b|искандер"),
    ("kab",      r"\bкаб\b|\bуаб\b|управляем\w+\s+авиабомб|планирующ\w+\s+бомб"),
    ("bek",      r"\bбэк\b|безэкипаж\w*|морск\w+\s+дрон"),
    ("aviation", r"авиац\w*|\bмиг-\d|\bту-\d|\bсу-\d|тактическ\w+\s+авиац"),
    ("uav",      r"\bбпла\b|беспилот\w*|\bдрон\w*|\bгерань|\bшахед"),
]

DIRECTIONS = {
    "север": 0, "северо-восток": 45, "восток": 90, "юго-восток": 135,
    "юг": 180, "юго-запад": 225, "запад": 270, "северо-запад": 315,
}
DIRECTION_FROM_RE = re.compile(
    r"\bс\s+(север[о\-]?восток\w*|юг[о\-]?восток\w*|север[о\-]?запад\w*|юг[о\-]?запад\w*"
    r"|север\w*|юг\w*|восток\w*|запад\w*)", re.IGNORECASE)
DIRECTION_TO_RE = re.compile(r"в\s+(?:направлении|сторону)\s+([А-ЯЁ][\w\-]+)", re.IGNORECASE)

COUNT_RE = re.compile(r"\b(?:ещ[её]\s+)?(\d{1,3})\s*(?:х\s*)?(?:бпла|дрон\w*|цел\w*|борт\w*)", re.IGNORECASE)
MANY_RE = re.compile(r"мног\w+\s+фиксац|групп\w+\s+бпла|масс\w+\s+прол[её]т", re.IGNORECASE)

# Официальная врезка РСЧС — отдельный класс с высоким доверием.
RSCHS_RE = re.compile(r"рсчс|экстренн\w+\s+информац|беспилотная\s+опасность\s+на\s+территории", re.IGNORECASE)

SPLIT_LINE_RE = re.compile(r"[\n;]+")


@dataclass
class Observation:
    """Одно наблюдение, извлеченное из одного сообщения."""

    signal_type: str = "unknown"
    threat_type: str = "unknown"
    severity: int = 1
    place_phrases: list[str] = field(default_factory=list)
    direction_deg: int | None = None
    target_count: int | None = None
    official: bool = False
    relevant: bool = True
    body: str = ""


def strip_footer(text: str) -> str:
    lines = [line for line in text.split("\n") if not FOOTER_RE.search(line)]
    return "\n".join(lines).strip()


def classify_signal(text: str) -> tuple[str, int]:
    for name, pattern, severity in SIGNALS:
        if re.search(pattern, text, re.IGNORECASE):
            return name, severity
    return "unknown", 1


def classify_threat(text: str) -> str:
    for name, pattern in THREATS:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return "unknown"


def extract_direction(text: str) -> int | None:
    match = DIRECTION_FROM_RE.search(text)
    if not match:
        return None
    token = norm_key(match.group(1))
    for name, degrees in DIRECTIONS.items():
        if token.startswith(name.replace("-", "")[:6]) or token.startswith(name[:6]):
            return degrees
    return None


def extract_count(text: str) -> int | None:
    numbers = [int(value) for value in COUNT_RE.findall(text) if 0 < int(value) <= 200]
    if numbers:
        return max(numbers)
    return 10 if MANY_RE.search(text) else None


def candidate_phrases(body: str) -> list[str]:
    """Фрагменты, в которых geocode ищет топонимы.

    Телеграфный формат кладет места на отдельные строки, Локатор и РВК —
    через запятую в одной строке. Режем и по строкам, и по запятым.
    """
    phrases: list[str] = []
    for line in SPLIT_LINE_RE.split(body):
        line = line.strip(" .!?—-•")
        if not line:
            continue
        for chunk in re.split(r"[,·]| - | — ", line):
            chunk = chunk.strip(" .!?—-•​")
            # Отбрасываем куски, состоящие только из служебной лексики.
            if len(chunk) < 3 or len(chunk) > 90:
                continue
            phrases.append(chunk)
    return phrases


def parse(text: str) -> Observation:
    body = strip_footer(text)
    observation = Observation(body=body)

    if not body:
        observation.relevant = False
        return observation

    observation.official = bool(RSCHS_RE.search(body))

    # Новостной текст без признаков обстановки — не наше событие.
    signal, severity = classify_signal(body)
    threat = classify_threat(body)

    # «Ещё 2 БПЛА от Новобелая в сторону Воронежа» — глагола нет, но это
    # фиксация: назван тип угрозы вместе с движением или счётом целей.
    if signal == "unknown" and threat != "unknown" and (
        re.search(DIRECTION_FROM_RE.pattern, body, re.IGNORECASE)
        or DIRECTION_TO_RE.search(body)
        or COUNT_RE.search(body)
        or MANY_RE.search(body)
    ):
        signal, severity = "detection", 4

    if NEWS_RE.search(body) and signal in {"unknown", "caution", "infra"}:
        observation.relevant = False
        return observation
    if signal == "unknown":
        observation.relevant = False
        return observation

    observation.signal_type = signal
    observation.severity = severity
    observation.threat_type = threat
    observation.direction_deg = extract_direction(body)
    observation.target_count = extract_count(body)
    observation.place_phrases = candidate_phrases(body)

    # Официальные врезки и групповые налеты весомее.
    if observation.official:
        observation.severity = min(10, observation.severity + 1)
    if observation.target_count and observation.target_count >= 5:
        observation.severity = min(10, observation.severity + 1)

    return observation
