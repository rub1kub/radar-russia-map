"""Слияние наблюдений разных источников в события.

Ключ кластеризации — не текст, а тройка (зона, тип угрозы, временное окно).
Совпадение текста без времени бесполезно: «Краснодарский край, опасность по
БПЛА» повторяется месяцами, а настоящее подтверждение приходит из 4 лент за 44 с.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Вес источника в расчете достоверности (см. tier в ingest/config.py).
# official — МЧС, РСЧС и оперштабы: официальное оповещение весит больше
# народной ленты, потому что за ним стоит проверка, а не пересказ.
TIER_WEIGHT = {"official": 0.72, "federal": 0.55, "regional": 0.4, "mixed": 0.25}

# Окно, в котором сообщение считается тем же событием. Пятнадцать минут —
# не запас, а наблюдаемый темп: ленты подтверждают друг друга и через десять
# минут после первой. Пока действовало правило родственных зон, оно же
# держало и одну зону в этом окне; после его отмены остались бы пять минут,
# и шесть лент про одну областную тревогу распались бы на три события.
SAME_ZONE_WINDOW = timedelta(minutes=15)
# Сколько после отбоя сообщения о той же угрозе считаются опоздавшими.
# Медленные ленты присылают свою тревогу через несколько минут после того,
# как быстрые уже дали отбой.
CLEAR_ECHO = timedelta(minutes=10)
FADE_AFTER = timedelta(minutes=45)
CLOSE_AFTER = timedelta(hours=3)

# Радиус неопределенности по уровню зоны.
ACCURACY_M = {"place": 4_000, "district": 12_000, "region": 40_000}

RESOLVING = {"allclear", "retracted"}

# Типы угрозы, живущие парой «закрыто — открыто» со своим отбоем:
# аэропорты и прочая инфраструктура. Они проставляются разбором сами,
# когда текст угрозу не назвал, поэтому в общих правилах слияния ведут
# себя иначе: не мостятся через «unknown» и закрываются только своим
# отбоем — «сняты ограничения» в аэропорту не гасят тревогу по мосту.
PAIRED_THREATS = {"infra", "airport"}

# Длина, начиная с которой дословное совпадение текста — это copy-paste, а не
# совпадение слов. Короткое «Опасность БПЛА» две ленты пишут одинаково просто
# потому, что иначе не скажешь, и считать их одним голосом нельзя.
#
# Было 120 символов — и порог не ловил почти ничего: по корпусу за трое суток
# дословных тел длиной 120+ размножено по трём и более каналам всего 7%, а
# вся копипаста живёт в диапазоне 55–119 символов (29–38%). Именно там
# лежит типовая строка кубанских лент «От Тамани до Сочи / тревога по БПЛА
# сохраняется / Соблюдайте меры безопасности» — 95 символов, четырнадцать
# каналов слово в слово, и каждый считался отдельным свидетелем: событие
# над Сочи показывало «18 источников» там, где независимых голосов четыре.
#
# Пятьдесят пять символов — это уже «место + регион + сигнал», фраза с
# конкретикой. Ниже начинаются шаблоны вроде «Отбой» и «Опасность БПЛА»,
# которые две ленты пишут одинаково не сговариваясь. Ошибка порога здесь
# несимметрична: занизить число голосов честнее, чем завысить.
REPOST_MIN_LEN = 55

# Насколько исход конкретнее наблюдения — при одинаковом уровне опасности.
# «Сбитие» и «фиксация» весят одинаково (8), а слияние меняло подпись только
# при строго большем уровне: событие открывалось фиксацией, и пришедшее следом
# сбитие в неё молча вливалось. За неделю так пропало 90 перехватов — на карте
# они значились как «борт видят». Взрыв и без того весит больше, но стоит
# здесь же, чтобы порядок читался целиком.
SIGNAL_RANK = {"impact": 3, "intercept": 2, "detection": 1}


@dataclass
class Event:
    id: str
    zone_id: str
    zone_path: list[str]
    threat_type: str
    signal_type: str
    severity: int
    first_seen: datetime
    last_seen: datetime
    resolved_at: datetime | None = None
    lat: float | None = None
    lon: float | None = None
    accuracy_m: int = 12_000
    direction_deg: int | None = None
    target_count: int | None = None
    # Хотя бы один источник назвал налёт групповым, не назвав числа бортов.
    massive: bool = False
    sources: dict[str, str] = field(default_factory=dict)   # source_key -> tier
    # network_id -> tier. Клоны одной сети дают один голос: десяток лент вида
    # "Радар.ру | X область" ведёт один оператор, и считать их независимыми
    # подтверждениями значит выдумывать достоверность.
    networks: dict[str, str] = field(default_factory=dict)
    # Голоса, которые реально считаются: сеть (или одиночный канал), но
    # дословный перепост чужого текста своего голоса не добавляет.
    voices: dict[str, str] = field(default_factory=dict)
    # Хеш текста -> голос, который сказал это первым.
    texts: dict[str, str] = field(default_factory=dict)
    contributions: list[tuple[int, str, str, datetime]] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """Вероятностное объединение независимых свидетельств.

        Один federal-источник дает 0.55, два — 0.80, три — 0.91.
        Считается по сетям, а не по каналам: иначе пять клонов одного
        оператора выглядели бы как пять независимых подтверждений.
        """
        miss = 1.0
        for tier in (self.voices or self.networks or self.sources).values():
            miss *= 1.0 - TIER_WEIGHT.get(tier, 0.25)
        return round(1.0 - miss, 3)

    @property
    def independent_sources(self) -> int:
        """Сколько независимых голосов стоит за событием."""
        return len(self.voices or self.networks or self.sources)

    def status(self, now: datetime) -> str:
        if self.resolved_at:
            return "resolved"
        if now - self.last_seen > CLOSE_AFTER:
            return "resolved"
        if now - self.last_seen > FADE_AFTER:
            return "fading"
        return "active"


def make_id(zone_id: str, threat: str, moment: datetime) -> str:
    seed = f"{zone_id}|{threat}|{moment.isoformat()}"
    return hashlib.sha1(seed.encode()).hexdigest()[:16]


class Fuser:
    @staticmethod
    def _repost_key(text: str) -> str | None:
        """Ключ дословного перепоста, если текст достаточно длинный.

        Считается по тому же тексту, что видит разбор, но без пробелов и
        регистра: перепост часто отличается только переносами строк и своей
        подписью в конце — подпись к этому моменту уже снята.
        """
        squeezed = "".join(text.split()).lower()
        if len(squeezed) < REPOST_MIN_LEN:
            return None
        return hashlib.sha1(squeezed.encode()).hexdigest()[:16]

    def _voice(self, event: Event, key: str, tier: str, text: str) -> bool:
        """Учесть голос, если это не пересказ чужими словами.

        Сеть перепостов ловится заранее по графу (networks.py), но утренняя
        сводка Минобороны расходится по лентам, между которыми постоянной
        связи нет, — граф её не видит. Здесь ловится сам факт: тот же текст,
        который уже принесли, независимым свидетельством не является.

        Возвращает, принёс ли вклад что-то новое: перепост не только не
        добавляет голоса, но и не должен обновлять время события.
        """
        stamp = self._repost_key(text)
        if stamp is not None:
            owner = event.texts.setdefault(stamp, key)
            if owner != key:
                return False
        fresh = key not in event.voices
        event.voices.setdefault(key, tier)
        return fresh

    def __init__(self) -> None:
        self.events: list[Event] = []
        self._open: list[Event] = []
        # Уже выданные идентификаторы. make_id — хеш (зона|угроза|секунда
        # начала), и в живом потоке он не сталкивался годами. Догонка после
        # простоя 24.08 вставила сообщения не в хронологическом порядке
        # rowid, и «закрытие — отбой — закрытие» в одну секунду дали два
        # РАЗНЫХ события с одинаковым id: rebuild падал на UNIQUE и
        # оставлял таблицу событий пустой.
        self._ids: set[str] = set()
        # Недавно закрытые события: (зона, угроза) -> событие. Нужны, чтобы
        # опоздавшие сообщения об уже отменённой тревоге не заводили новое
        # событие. Без этого получалась карусель: отбой закрывает событие,
        # медленная лента присылает свою тревогу, та заводит новое, его
        # закрывает следующая копия отбоя — и в ленте четыре одинаковых
        # отбоя подряд по одной зоне, а бывало и шесть.
        self._cleared: dict[tuple[str, str], Event] = {}

    def _match(self, zone_path: list[str], threat: str, moment: datetime) -> Event | None:
        """Событие для наблюдения — только по той же самой зоне.

        Родственные зоны раньше сливались, и это ломало карту хуже всего
        остального. Событие начиналось областной тревогой («Приазовье
        Краснодарского края, тревога по БПЛА»), потом два часа впитывало
        сообщения про один Новороссийск, а «уточняем характер взрыва»
        поднимало весь край до взрыва девятого уровня: сорок районов
        закрашивались красным из-за одного города.

        Ничего при этом не терялось бы и от раздельного счёта: у города своё
        событие, у области своё, а карта поднимает город по цепочке зон и
        показывает оба. Сейчас так и сделано — событие всегда про одну зону.
        """
        zone_id = zone_path[0]
        for event in reversed(self._open):
            if event.resolved_at:
                continue
            if event.threat_type != threat:
                # «unknown» — мостик между разными формулировками одной и
                # той же угрозы («сбит» вслед за «работа ПВО» без слова
                # «БПЛА»). Для infra он не работает: этот тип угрозы сам
                # проставляется, только когда текст угрозу НЕ назвал —
                # мостик пускал бы через него любое несвязанное «unknown»
                # наблюдение, и «Приготовиться к фиксациям» без слова
                # «БПЛА» подряд с закрытием аэропорта сливалось в одно
                # событие, наследуя ложную метку «аэропорт».
                bridge = "unknown" in (event.threat_type, threat)
                paired = (event.threat_type in PAIRED_THREATS
                          or threat in PAIRED_THREATS)
                if not bridge or paired:
                    continue

            # Наблюдение может прийти к слушателю позже более свежих: каналы
            # доставляются не в порядке публикации, и опоздание бывает больше
            # LATE_GRACE. Раньше такое отбрасывалось (gap < 0), и волна РСЧС
            # рождала событие-близнеца в той же зоне через секунды — подписчик
            # получал две «опасности» подряд. Опоздавшее присоединяется, если
            # попадает в окно жизни события; время события оно не двигает —
            # присоединение берёт max(last_seen, moment).
            if moment - event.last_seen > SAME_ZONE_WINDOW:
                continue
            if moment < event.first_seen - SAME_ZONE_WINDOW:
                continue

            if event.zone_id == zone_id:
                return event
        return None

    def _prune(self, now: datetime) -> None:
        self._open = [
            event for event in self._open
            if not event.resolved_at and now - event.last_seen <= CLOSE_AFTER
        ]
        self._cleared = {
            key: event for key, event in self._cleared.items()
            if event.resolved_at and now - event.resolved_at <= CLEAR_ECHO
        }

    def add(self, *, raw_id: int, source_key: str, tier: str, moment: datetime,
            observation, zone_path: list[str], lat, lon, level: str,
            network: str | None = None) -> Event | None:
        """Добавить наблюдение. Возвращает затронутое событие."""
        self._prune(moment)
        zone_id = zone_path[0]

        # Отбой закрывает открытые события в этой зоне и ниже — но только по
        # той угрозе, которая названа. Коррекция ``retracted`` строже: она
        # относится только к точно названной зоне. «Наша авиация над ЛНР» —
        # объяснение конкретной региональной отметки, а не отбой каждого
        # районного события внутри ЛНР; прежнее правило закрывало таким
        # сообщением больше сотни независимых событий.
        if observation.signal_type in RESOLVING:
            cleared = observation.threat_type
            closed = None
            for event in self._open:
                if event.resolved_at:
                    continue
                # Отбой закрывает только то, что началось до него. Каналы
                # доставляются не в порядке публикации, и опоздавший отбой
                # прежней волны закрывал событие, родившееся позже него:
                # у события выходило resolved_at раньше first_seen, а
                # подписчик получал отбой опасности, которой не видел.
                if event.first_seen > moment:
                    continue
                if observation.signal_type == "retracted":
                    if event.zone_id != zone_id:
                        continue
                elif event.zone_id != zone_id and zone_id not in event.zone_path:
                    continue
                # Отбой без названной угрозы снимает всё: «отбой всех ранее
                # объявленных», «отбой тревоги».
                if cleared != "unknown" and event.threat_type not in (cleared, "unknown"):
                    continue
                # Пары «закрыто — открыто» закрываются только своим
                # отбоем: «отбой тревоги» аэропорт не открывает, «сняты
                # ограничения» не гасят дроновую тревогу по городу, а
                # мост и аэропорт не закрывают друг друга.
                if ((event.threat_type in PAIRED_THREATS or cleared in PAIRED_THREATS)
                        and event.threat_type != cleared):
                    continue
                event.resolved_at = moment
                event.last_seen = max(event.last_seen, moment)
                event.contributions.append((raw_id, source_key, "resolve", moment))
                self._cleared[(event.zone_id, event.threat_type)] = event
                closed = event
            return closed

        # Эхо отменённой тревоги: сообщение о том же и не сильнее того, что
        # уже отменено. Считаем его опоздавшим и приписываем к закрытому
        # событию, не открывая нового.
        echo = self._cleared.get((zone_id, observation.threat_type))
        if (
            echo is not None
            and echo.resolved_at is not None
            and moment - echo.resolved_at <= CLEAR_ECHO
            and observation.severity <= echo.severity
        ):
            echo.contributions.append((raw_id, source_key, "late", moment))
            return echo

        existing = self._match(zone_path, observation.threat_type, moment)
        if existing:
            # Перепост чужого текста и вторая ветка той же сети не приносят
            # ничего нового, а значит и время события двигать не должны:
            # иначе сообщение, пересказанное через семь минут, делало
            # событие «свежим» и оставляло зону гореть на карте.
            said = self._voice(existing, network or source_key, tier,
                               getattr(observation, "body", ""))
            if said:
                existing.last_seen = max(existing.last_seen, moment)
            # Подпись должна соответствовать цвету. Слияние брало максимум
            # severity, но оставляло тип сигнала от первого сообщения: событие
            # показывалось как «Опасность» и красилось красным, потому что
            # внутрь попала фиксация. Растёт уровень — растёт и подпись.
            harder = observation.severity > existing.severity
            # При равном уровне побеждает более определённый исход: сбитие
            # говорит о борте больше, чем «видим», и подпись должна об этом
            # сказать.
            same_but_sharper = (
                observation.severity == existing.severity
                and SIGNAL_RANK.get(observation.signal_type, 0)
                > SIGNAL_RANK.get(existing.signal_type, 0))
            if harder or same_but_sharper:
                existing.severity = observation.severity
                existing.signal_type = observation.signal_type
            if observation.threat_type != "unknown":
                existing.threat_type = observation.threat_type
            if observation.direction_deg is not None:
                existing.direction_deg = observation.direction_deg
            if observation.target_count:
                existing.target_count = max(existing.target_count or 0, observation.target_count)
            if getattr(observation, "massive", False):
                existing.massive = True
            role = "confirm" if source_key not in existing.sources else "repeat"
            existing.sources.setdefault(source_key, tier)
            existing.networks.setdefault(network or source_key, tier)
            existing.contributions.append((raw_id, source_key, role, moment))
            return existing

        # Свободный id: при столкновении детерминированно дохешируем — тот
        # же корпус даёт те же идентификаторы от запуска к запуску.
        event_id = make_id(zone_id, observation.threat_type, moment)
        while event_id in self._ids:
            event_id = hashlib.sha1(
                (event_id + "|next").encode()).hexdigest()[:16]
        self._ids.add(event_id)
        event = Event(
            id=event_id,
            zone_id=zone_id,
            zone_path=zone_path,
            threat_type=observation.threat_type,
            signal_type=observation.signal_type,
            severity=observation.severity,
            first_seen=moment,
            last_seen=moment,
            lat=lat,
            lon=lon,
            accuracy_m=ACCURACY_M.get(level, 12_000),
            direction_deg=observation.direction_deg,
            target_count=observation.target_count,
            massive=getattr(observation, "massive", False),
            sources={source_key: tier},
            networks={(network or source_key): tier},
            contributions=[(raw_id, source_key, "first", moment)],
        )
        self._voice(event, network or source_key, tier, getattr(observation, "body", ""))
        self.events.append(event)
        self._open.append(event)
        return event
