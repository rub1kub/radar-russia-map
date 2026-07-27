"""Официальные имена муниципалитетов из реестра ОКТМО.

Районы приезжают из geoBoundaries с английскими или обрезанными названиями:
«Anapa Urban Okrug», «городской округ Новороссий», «Ресорт Товн Оф Сочи».
Прежний разбор угадывал русское имя по словарю GeoNames и промахивался —
не в мелочах, а в существе: полигон Анапского округа назывался «городской
округ Новороссий», полигон Калининграда — «Королёв», полигон Тольятти —
«Ставрополь». На карте это выглядит как съехавшая подпись, а в разборе
сообщений уводит событие на 700 км.

Здесь имя не угадывается, а берётся из реестра. ОКТМО (Общероссийский
классификатор территорий муниципальных образований, Росстат) в разделе 1
перечисляет муниципалитеты, в разделе 2 — их поимённый состав населённых
пунктов. Состав и есть ключ: полигон опознаётся по тому, какие НП внутри
него лежат, а не по тому, как его назвал зарубежный поставщик границ.
Совпадение по трём десяткам сёл подделать невозможно.

    from pipeline.oktmo import Registry
    registry = Registry.load()
    registry.match(["анапа", "анапская", "витязево"])   # -> 'Анапа'
"""

from __future__ import annotations

import collections
import csv
import re
from dataclasses import dataclass
from pathlib import Path

from .db import ROOT
from .textnorm import norm_key, strip_unit

CSV_PATH = ROOT / "research" / "data_sources" / "rosstat_oktmo_data_20260601T1406.csv"

# Заголовки групп внутри раздела: «Муниципальные районы Краснодарского края»,
# «Городские поселения Абинского муниципального района/». Это не сами
# муниципалитеты, а рубрики над ними.
GROUP_HEAD_RE = re.compile(
    r"^(Муниципальные|Городские|Сельские|Внутригородские|Населенные пункты|Межселенные)\s",
    re.IGNORECASE,
)

# Тип НП перед названием: «г Анапа», «ст-ца Анапская», «х Большой Разнокол».
PLACE_TYPE_RE = re.compile(
    r"^(?:г|пгт|рп|дп|кп|с|п|д|х|ст-ца|ст|сл|аул|нп|тер|аал|улус|рзд|платф)\.?\s+",
    re.IGNORECASE,
)

# Канцелярские обёртки вокруг имени. Слева — то, что реестр ставит перед
# названием, справа — то, как это место называют в сообщениях.
PREFIX_RE = re.compile(
    r"^(?:муниципальн\w+\s+(?:округ|район)\s+"
    r"|городско\w+\s+округ\s+"
    r"|ЗАТО\s+"
    r"|рабочий\s+поселок\s*\([^)]*\)\s+"
    r"|поселок\s+городского\s+типа\s+"
    r"|город[-\s](?:герой|курорт)\s+"
    r"|город\s+"
    r"|округ\s+)+",
    re.IGNORECASE,
)

SUFFIX_RULES = (
    (re.compile(r"\s+муниципальн\w+\s+район$", re.IGNORECASE), " район"),
    (re.compile(r"\s+муниципальн\w+\s+округ$", re.IGNORECASE), " округ"),
    (re.compile(r"\s+городско\w+\s+округ$", re.IGNORECASE), " округ"),
)

# Слова, по которым видно, что единица уже названа: добавлять ничего не надо.
UNIT_WORDS = ("район", "округ", "улус", "кожуун", "город", "поселение")

ADJECTIVE_RE = re.compile(r"(ский|цкий|ской|ый|ий|ой)$", re.IGNORECASE)

# Мера совпадения — не число сошедшихся НП, а число, взвешенное охватом:
# hits² / всего НП у муниципалитета. Голого счёта не хватает в обе стороны.
# Городской округ большого города записан в реестре одним НП — самим
# городом, — и требование трёх совпадений отвергало Калининград, Пермь,
# Тверь: полигон оставался под чужим именем «Королёв». Охват же без счёта
# ошибается наоборот: под полигоном Антрацитовского округа (20 НП из 56)
# по доле выигрывает соседний Красный Луч (12 из 23), хотя он меньше и
# лежит рядом. Произведение разводит оба случая.
MIN_SCORE = 1.0
MIN_MARGIN = 1.1
# До скольких совпавших НП состав считается слабым и решает главное место.
# Городской округ большого города записан в реестре одним-двумя НП, район —
# десятками, и мерить их одной меркой нельзя.
WEAK_COMPOSITION = 2
# Наименьшее население главного места, при котором ему можно верить.
# Муниципалитеты называют по городам, а не по деревням, и Александровок с
# Первомайскими в стране сотни.
ANCHOR_MIN_POPULATION = 5_000


def tidy(name: str) -> str:
    """Реестровое имя в том виде, в каком место называют люди.

    «Муниципальный округ город-курорт Анапа» -> «Анапа»,
    «Кош-Агачский муниципальный район» -> «Кош-Агачский район»,
    «Владивостокский» -> «Владивостокский округ».
    """
    result = PREFIX_RE.sub("", name.strip()).strip()
    for pattern, replacement in SUFFIX_RULES:
        result = pattern.sub(replacement, result)
    result = result.strip()
    # Голое прилагательное — это округ: реестр так записывает городские и
    # муниципальные округа, у которых имя образовано от города или района.
    # Без слова единицы подпись на карте читалась бы как обрубок.
    if ADJECTIVE_RE.search(result) and not any(word in result.lower() for word in UNIT_WORDS):
        result = f"{result} округ"
    return result


@dataclass(frozen=True)
class Municipality:
    code: tuple[str, str]
    name: str
    places: frozenset[str]


class Registry:
    """Муниципалитеты верхнего уровня и их состав."""

    def __init__(self, entries: list[Municipality]) -> None:
        self.entries = entries
        self._by_place: dict[str, list[int]] = collections.defaultdict(list)
        for index, entry in enumerate(entries):
            for place in entry.places:
                self._by_place[place].append(index)

    @classmethod
    def load(cls, path: Path | None = None) -> "Registry":
        source = path or CSV_PATH
        names: dict[tuple[str, str], str] = {}
        places: dict[tuple[str, str], set[str]] = collections.defaultdict(set)

        # Реестр выложен в cp1251, но с отдельными байтами вне таблицы —
        # строгий разбор на них падает, а терять из-за одного символа весь
        # файл незачем: спорные знаки встречаются в примечаниях, не в именах.
        with source.open(encoding="utf-8", errors="replace", newline="") as handle:
            for row in csv.reader(handle, delimiter=";"):
                if len(row) < 7:
                    continue
                region, first, second, third, section, name = (
                    row[0], row[1], row[2], row[3], row[5], row[6].strip()
                )
                if not name or first == "000":
                    continue
                if section == "1" and second == "000" and third == "000":
                    if GROUP_HEAD_RE.match(name) or name.endswith("/"):
                        continue
                    names[(region, first)] = name
                elif section == "2" and third != "000":
                    places[(region, first)].add(norm_key(PLACE_TYPE_RE.sub("", name)))

        entries = [
            Municipality(code=code, name=tidy(name), places=frozenset(places.get(code, ())))
            for code, name in sorted(names.items())
            if places.get(code)
        ]
        return cls(entries)

    def match(
        self,
        places: list[tuple[str, int]],
        region: str | None = None,
    ) -> tuple[Municipality, float] | None:
        """Опознать муниципалитет по составу НП внутри полигона.

        Принимает пары (имя НП, население); имена — уже нормализованные
        Возвращает муниципалитет и меру совпадения — по ней разрешается
        спор двух полигонов за один муниципалитет. region, если задан,
        ограничивает поиск кодом субъекта:
        по составу муниципалитет опознаётся и без него, а вот признак
        главного места без такой рамки ловит тёзок за тысячу километров.
        """
        votes: collections.Counter[int] = collections.Counter()
        for name, _population in places:
            for index in self._by_place.get(name, ()):
                if region is None or self.entries[index].code[0] == region:
                    votes[index] += 1
        if not votes:
            return None

        scored = sorted(
            ((hits * hits / len(self.entries[index].places), hits, index)
             for index, hits in votes.items()),
            reverse=True,
        )
        score, hits, index = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if hits >= 3 and score >= MIN_SCORE and score >= runner_up * MIN_MARGIN:
            return self.entries[index], round(score, 2)

        # Признак главного места работает только в границах субъекта.
        if region is None:
            return None

        # Состав не ответил. Тогда решает главное место полигона: округ
        # назван по своему городу и им же держится, а город достаточно
        # крупен, чтобы не спутаться с тёзкой-деревней.
        #
        # Без этого признака оставались два разных провала. Городской округ
        # записан в реестре одним НП — самим городом, — и у полигона
        # Калининграда мера 1.0 была и у «Калининграда», и у «Первомайского
        # округа», куда попала случайная деревня Первомайский: ничья, отказ,
        # полигон под чужим именем «Королёв». А в Запорожской и Херсонской
        # областях НП записаны по-украински («біленьке», «комиш-зоря»), и
        # состав не сходился ни с чем — зато сам город в справочнике русский.
        anchor, population = max(places, key=lambda item: item[1]) if places else (None, 0)
        if anchor is None or population < ANCHOR_MIN_POPULATION:
            return None
        anchored = [row for row in scored if anchor in self.entries[row[2]].places]
        if not anchored:
            return None

        # Сильный состав главному месту не уступает: полигон Вологодского
        # района содержит саму Вологду, у которой свой округ, но сотня
        # совпавших сёл района весит несравнимо больше, и район не должен
        # становиться городом. Сюда такой случай и не доходит — он ушёл
        # ветвью выше, — но проверка остаётся на случай слабого перевеса.
        if hits > WEAK_COMPOSITION and score >= MIN_SCORE:
            return None

        return self.entries[anchored[0][2]], round(anchored[0][0], 2)


def same_place(left: str, right: str) -> bool:
    """Одно ли это место, если отбросить слово единицы.

    «городской округ Новороссий» и «Новороссийск» — разные: реестр знает
    полное имя, а обрезанное пришло из чужого набора границ.
    """
    return strip_unit(norm_key(left)) == strip_unit(norm_key(right))
