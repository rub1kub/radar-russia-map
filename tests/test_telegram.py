"""Тесты телеграм-бота: поиск места и разбор команд.

Сеть здесь не нужна и не используется: отправку подменяем, проверяем то,
что решает бот сам.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from api import telegram


@pytest.fixture
def outbox(monkeypatch):
    """Перехватываем отправку: Bot API в тестах дёргать нечем и незачем."""
    sent = []
    monkeypatch.setattr(telegram, "send",
                        lambda chat_id, text, keyboard=None: sent.append((chat_id, text)))
    return sent


def test_help_answers_unknown_command(outbox):
    telegram.handle_text(1, "/абракадабра")
    assert outbox and "Тихое небо" in outbox[0][1]


def test_start_greets(outbox):
    telegram.handle_text(1, "/start")
    assert outbox and "карта воздушной обстановки" in outbox[0][1].lower()


def test_start_deeplink_subscribes(tmp_path, monkeypatch, outbox):
    """t.me/бот?start=w_<зона> подписывает на место одним нажатием."""
    monkeypatch.setattr(telegram, "DB_PATH", tmp_path / "tg.db")
    long_id = "x" * 70
    with telegram.closing(telegram._connect()) as connection:
        connection.execute(
            "CREATE TABLE zones (id TEXT PRIMARY KEY, name_ru TEXT, "
            "level TEXT)")
        connection.execute(
            "INSERT INTO zones VALUES ('kurskaya_oblast', "
            "'Курская область', 'region')")
        connection.execute(
            f"INSERT INTO zones VALUES ('{long_id}', 'Дальнее', 'place')")
        connection.commit()

    telegram.handle_text(5, "/start w_kurskaya_oblast")
    assert "Курская область" in outbox[-1][1]
    assert "/unwatch" in outbox[-1][1]
    with telegram.closing(telegram._connect()) as connection:
        assert telegram._zones_of(connection, 5) == ["kurskaya_oblast"]

    # Длинный id не влезает в 64 знака payload — едет md5-хвостом.
    payload = telegram.zone_start_payload(long_id)
    assert payload.startswith("wh")
    telegram.handle_text(6, f"/start {payload}")
    with telegram.closing(telegram._connect()) as connection:
        assert telegram._zones_of(connection, 6) == [long_id]

    # Неизвестная зона — обычное приветствие, не подписка.
    telegram.handle_text(7, "/start w_vydumannaya_zona")
    assert "карта воздушной обстановки" in outbox[-1][1].lower()
    with telegram.closing(telegram._connect()) as connection:
        assert telegram._zones_of(connection, 7) == []


def test_region_without_argument_explains(outbox):
    telegram.handle_text(1, "/region")
    assert "/region" in outbox[0][1]


def test_plural_matches_russian(outbox):
    assert telegram.plural(1, "событие", "события", "событий") == "событие"
    assert telegram.plural(3, "событие", "события", "событий") == "события"
    assert telegram.plural(11, "событие", "события", "событий") == "событий"
    assert telegram.plural(22, "событие", "события", "событий") == "события"


def test_bare_text_is_treated_as_a_place(outbox, monkeypatch):
    """Человек пишет «Курская область» без команды — это тоже вопрос о месте."""
    monkeypatch.setattr(telegram, "find_zone",
                        lambda q: {"id": "kurskaya_oblast", "name_ru": "Курская область",
                                   "level": "region"})
    monkeypatch.setattr(telegram, "region_text", lambda zone: "ответ про место")
    telegram.handle_text(1, "Курская область")
    assert outbox[0][1] == "ответ про место"


def test_webhook_secret_is_required(monkeypatch):
    """Без совпадения секрета вебхук не должен ничего делать."""
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    assert telegram.webhook_secret() == "s3cret"


def test_deliver_marks_before_send(tmp_path, monkeypatch):
    """Упавшая отправка не приводит к дублю на следующем такте.

    Отметка «отправлено» коммитилась одной пачкой в конце рассылки:
    рестарт API между отправкой и коммитом терял её, и следующий такт
    слал подписчику то же событие ещё раз — владелец получил «взрыв»
    дважды с разницей в минуту. Теперь отметка коммитится до отправки:
    событие уходит один раз, даже если сама отправка оборвалась.
    """
    import json

    monkeypatch.setattr(telegram, "DB_PATH", tmp_path / "tg.db")
    monkeypatch.setattr(telegram, "token", lambda: "t")

    with telegram.closing(telegram._connect()) as connection:
        connection.execute(
            "INSERT INTO tg_chats (chat_id, zones, created_at) VALUES (?,?,?)",
            (1, json.dumps(["krasnodar"]), 0))
        connection.commit()

    snapshot = {"events": [{
        "id": "evt-1", "status": "active", "zone_path": ["krasnodar"],
        "signal_type": "impact", "threat_type": "uav",
        "zone_name": "Краснодар", "last_seen_at": "2026-08-06T16:22:00+00:00",
    }]}

    calls = []

    def failing_send(chat_id, text, keyboard=None):
        calls.append(text)
        raise OSError("сеть оборвалась")

    monkeypatch.setattr(telegram, "send", failing_send)
    telegram.deliver_once(snapshot)
    monkeypatch.setattr(telegram, "send",
                        lambda chat_id, text, keyboard=None: calls.append(text))
    telegram.deliver_once(snapshot)

    assert len(calls) == 1, "событие ушло повторно после падения отправки"


def test_twin_events_send_one_message(tmp_path, monkeypatch):
    """Два события-близнеца об одном факте — одно уведомление.

    Рестарт конвейера в разгар волны репостов расколол её на два события
    в той же зоне с разными id, и подписчик получил «взрыв» дважды с
    разницей в минуту. Дедуп по id это не ловит — ловит текст: у
    близнецов он совпадает вплоть до времени события.
    """
    import json

    monkeypatch.setattr(telegram, "DB_PATH", tmp_path / "tg.db")
    monkeypatch.setattr(telegram, "token", lambda: "t")

    with telegram.closing(telegram._connect()) as connection:
        connection.execute(
            "INSERT INTO tg_chats (chat_id, zones, created_at) VALUES (?,?,?)",
            (1, json.dumps(["krasnodar"]), 0))
        connection.commit()

    twin = {"status": "active", "zone_path": ["krasnodar"],
            "signal_type": "impact", "threat_type": "uav",
            "zone_name": "городской округ Краснодар",
            "last_seen_at": "2026-08-06T16:22:00+00:00"}
    snapshot = {"events": [dict(twin, id="evt-a"), dict(twin, id="evt-b")]}

    sent = []
    monkeypatch.setattr(telegram, "send",
                        lambda chat_id, text, keyboard=None: sent.append(text))
    telegram.deliver_once(snapshot)
    assert len(sent) == 1, "близнецы ушли оба"

    # Настоящее новое событие позже — с другим временем в строке — уходит.
    later = dict(twin, id="evt-c", last_seen_at="2026-08-06T17:05:00+00:00")
    telegram.deliver_once({"events": [later]})
    assert len(sent) == 2


def test_repeat_danger_without_clear_is_throttled(tmp_path, monkeypatch):
    """Вторая «опасность» по тому же месту без отбоя между ними — не новость.

    «Краснодар — объявлена опасность атаки БПЛА» ушло дважды с разницей в
    24 минуты: fuse.SAME_ZONE_WINDOW (15 мин) развело две волны на разные
    события, а подписчик получил одно и то же предупреждение подряд.
    """
    import json

    monkeypatch.setattr(telegram, "DB_PATH", tmp_path / "tg.db")
    monkeypatch.setattr(telegram, "token", lambda: "t")
    with telegram.closing(telegram._connect()) as connection:
        connection.execute(
            "INSERT INTO tg_chats (chat_id, zones, created_at) VALUES (?,?,?)",
            (1, json.dumps(["krasnodar"]), 0))
        connection.commit()

    sent = []
    monkeypatch.setattr(telegram, "send",
                        lambda chat_id, text, keyboard=None: sent.append(text))

    def event(eid, signal, at, status="active"):
        return {"id": eid, "status": status, "zone_id": "krasnodar",
                "zone_path": ["krasnodar"], "signal_type": signal,
                "threat_type": "uav", "severity": 5,
                "zone_name": "Краснодар", "last_seen_at": at}

    telegram.deliver_once({"events": [
        event("e1", "danger", "2026-08-20T21:31:00+00:00")]})
    assert len(sent) == 1

    # Вторая опасность 24 минуты спустя, без отбоя между ними — гасится.
    telegram.deliver_once({"events": [
        event("e2", "danger", "2026-08-20T21:55:00+00:00")]})
    assert len(sent) == 1, "повторная опасность без отбоя не должна уходить"

    # Эскалация до тревоги — другой класс, другая новость, проходит.
    telegram.deliver_once({"events": [
        event("e3", "alarm", "2026-08-20T22:05:00+00:00")]})
    assert len(sent) == 2

    # Отбой снимает тормоз — следующая опасность снова уходит.
    telegram.deliver_once({"events": [
        event("e3", "alarm", "2026-08-20T22:05:00+00:00", status="resolved")]})
    assert len(sent) == 3
    telegram.deliver_once({"events": [
        event("e4", "danger", "2026-08-20T22:40:00+00:00")]})
    assert len(sent) == 4


def test_broader_repeat_of_same_forecast_is_throttled(tmp_path, monkeypatch):
    """«Краснодар — тревога», потом «Краснодарский край — тревога» —
    одна новость.

    Тормоз сравнивал зоны буквально («край» ≠ «город» — другая новость),
    хотя более широкое объявление после узкого не несёт ничего нового.
    Обратный порядок — сужение с края до конкретно твоего города —
    остаётся новостью. Класс в тесте — воздушная тревога: после решения
    владельца от 23.08 с края на подписку города поднимается только она,
    и краевые повторы остальных классов до тормоза уже не доходят.
    """
    import json

    monkeypatch.setattr(telegram, "DB_PATH", tmp_path / "tg.db")
    monkeypatch.setattr(telegram, "token", lambda: "t")
    with telegram.closing(telegram._connect()) as connection:
        connection.execute(
            "CREATE TABLE zones (id TEXT PRIMARY KEY, parent_id TEXT)")
        connection.execute(
            "INSERT INTO zones VALUES ('gorodskoy_okrug_krasnodar', "
            "'krasnodarskiy_kray')")
        connection.execute(
            "INSERT INTO zones VALUES ('krasnodarskiy_kray', NULL)")
        connection.execute(
            "INSERT INTO tg_chats (chat_id, zones, created_at) VALUES (?,?,?)",
            (1, json.dumps(["gorodskoy_okrug_krasnodar"]), 0))
        connection.commit()

    sent = []
    monkeypatch.setattr(telegram, "send",
                        lambda chat_id, text, keyboard=None: sent.append(text))

    def event(eid, zone_id, zone_path, at, status="active"):
        return {"id": eid, "status": status, "zone_id": zone_id,
                "zone_path": zone_path, "signal_type": "alarm",
                "threat_type": "uav", "severity": 7,
                "zone_name": zone_id, "last_seen_at": at}

    city = ["gorodskoy_okrug_krasnodar", "krasnodarskiy_kray"]
    kray = ["krasnodarskiy_kray"]

    telegram.deliver_once({"events": [event(
        "e1", "gorodskoy_okrug_krasnodar", city,
        "2026-08-23T07:40:00+00:00")]})
    assert len(sent) == 1

    # Та же тревога, теперь на весь край, — без отбоя между ними. Гасится.
    telegram.deliver_once({"events": [event(
        "e2", "krasnodarskiy_kray", kray, "2026-08-23T08:22:00+00:00")]})
    assert len(sent) == 1, "краевой повтор той же тревоги ушёл подписчику"

    # Отбой по городу снимает тормоз.
    telegram.deliver_once({"events": [event(
        "e1", "gorodskoy_okrug_krasnodar", city,
        "2026-08-23T08:40:00+00:00", status="resolved")]})
    assert len(sent) == 2

    # Обратный порядок: сначала край, потом конкретно город — сужение до
    # твоего места остаётся новостью.
    telegram.deliver_once({"events": [event(
        "e3", "krasnodarskiy_kray", kray, "2026-08-23T09:00:00+00:00")]})
    assert len(sent) == 3
    telegram.deliver_once({"events": [event(
        "e4", "gorodskoy_okrug_krasnodar", city,
        "2026-08-23T09:10:00+00:00")]})
    assert len(sent) == 4


def test_watching_a_city_still_sees_a_region_wide_alarm(tmp_path, monkeypatch):
    """Подписка на город обязана видеть тревогу по всему краю — и только её.

    Настоящая воздушная тревога по Краснодарскому краю (без названного
    района — источник назвал только регион) не дошла подписчику на
    Краснодар: zone_path региональной тревоги состоит из одной этой зоны
    и не содержит городов внутри неё, а сравнение шло только в эту
    сторону.

    Первая попытка чинить это (пересечение zone_path события с подпиской,
    расширенной вверх до региона) оказалась ШИРЕ, чем нужно: у Анапы и
    Краснодара общий родитель — тот же край, и подписка на город ловила
    вообще все события по всему краю, включая соседние города. Проверять
    нужно zone_id САМОГО события (не всю его цепочку) против цепочки
    родителей подписки.
    """
    import json

    monkeypatch.setattr(telegram, "DB_PATH", tmp_path / "tg.db")
    monkeypatch.setattr(telegram, "token", lambda: "t")
    with telegram.closing(telegram._connect()) as connection:
        connection.execute(
            "CREATE TABLE zones (id TEXT PRIMARY KEY, parent_id TEXT)")
        connection.execute(
            "INSERT INTO zones VALUES ('gorodskoy_okrug_krasnodar', "
            "'krasnodarskiy_kray')")
        connection.execute(
            "INSERT INTO zones VALUES ('anapa', 'krasnodarskiy_kray')")
        connection.execute(
            "INSERT INTO zones VALUES ('krasnodarskiy_kray', NULL)")
        connection.execute(
            "INSERT INTO tg_chats (chat_id, zones, created_at) VALUES (?,?,?)",
            (1, json.dumps(["gorodskoy_okrug_krasnodar"]), 0))
        connection.commit()

    sent = []
    monkeypatch.setattr(telegram, "send",
                        lambda chat_id, text, keyboard=None: sent.append(text))

    def event(eid, zone_id, zone_path, at):
        return {"id": eid, "status": "active", "zone_id": zone_id,
                "zone_path": zone_path, "signal_type": "alarm",
                "threat_type": "uav", "severity": 7,
                "zone_name": zone_id, "last_seen_at": at}

    # Источник назвал только регион — свой zone_id и zone_path из одной
    # этой зоны, района или города внутри неё в сообщении нет. Доходит.
    telegram.deliver_once({"events": [event(
        "e1", "krasnodarskiy_kray", ["krasnodarskiy_kray"],
        "2026-08-20T18:28:00+00:00")]})
    assert len(sent) == 1, "тревога по всему краю не дошла до подписки на город"

    # Тревога конкретно в Анапе — тот же край, но другой город. Не доходит.
    telegram.deliver_once({"events": [event(
        "e2", "anapa", ["anapa", "krasnodarskiy_kray"],
        "2026-08-20T19:00:00+00:00")]})
    assert len(sent) == 1, "тревога по Анапе ушла подписчику на Краснодар"

    # С края на город поднимается ТОЛЬКО воздушная тревога (сирена) —
    # решение владельца 23.08. Ни наблюдения («работает ПВО ·
    # Краснодарский край»), ни даже краевая «опасность» подписчику
    # города не приходят.
    for signal, severity in (("intercept", 8), ("danger", 5),
                             ("detection", 8), ("impact", 9)):
        other = event(f"e-{signal}", "krasnodarskiy_kray",
                      ["krasnodarskiy_kray"], "2026-08-20T20:00:00+00:00")
        other["signal_type"] = signal
        other["severity"] = severity
        telegram.deliver_once({"events": [other]})
        assert len(sent) == 1, f"краевой {signal} ушёл подписчику на город"


def test_notification_colour_follows_map_legend(tmp_path, monkeypatch):
    """Цвет кружка в уведомлении — та же ранжировка, что в легенде карты.

    Красное — борт видят (фиксация, перехват, взрыв), оранжевое — тревога,
    жёлтое — опасность, зелёное — отбой. Раньше всё, кроме отбоя, было
    красным, и «опасность» выглядела так же грозно, как взрыв.
    """
    import json

    monkeypatch.setattr(telegram, "DB_PATH", tmp_path / "tg.db")
    monkeypatch.setattr(telegram, "token", lambda: "t")
    with telegram.closing(telegram._connect()) as connection:
        connection.execute(
            "INSERT INTO tg_chats (chat_id, zones, created_at) VALUES (?,?,?)",
            (1, json.dumps(["z"]), 0))
        connection.commit()

    sent = []
    monkeypatch.setattr(telegram, "send",
                        lambda chat_id, text, keyboard=None: sent.append(text))

    def event(eid, signal, status="active", at="2026-08-06T16:00:00+00:00"):
        return {"id": eid, "status": status, "zone_path": ["z"],
                "signal_type": signal, "threat_type": "uav",
                "zone_name": "Тест", "last_seen_at": at}

    telegram.deliver_once({"events": [
        event("e1", "detection", at="2026-08-06T16:01:00+00:00"),
        event("e2", "alarm", at="2026-08-06T16:02:00+00:00"),
        event("e3", "danger", at="2026-08-06T16:03:00+00:00"),
        event("e4", "allclear", status="resolved",
              at="2026-08-06T16:04:00+00:00"),
    ]})
    heads = [text.split()[0] for text in sent]
    assert heads == ["🔴", "🟠", "🟡", "🟢"]


def test_init_data_signature_is_checked(monkeypatch):
    """Журнал открытий верит только подписи Telegram, а не любому POST."""
    import hashlib
    import hmac as hmac_mod
    from urllib.parse import urlencode

    monkeypatch.setattr(telegram, "token", lambda: "12345:secret")
    fields = {"user": '{"id": 7, "username": "u", "first_name": "n"}',
              "auth_date": "1754500000"}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac_mod.new(b"WebAppData", b"12345:secret", hashlib.sha256).digest()
    good_hash = hmac_mod.new(secret, check.encode(), hashlib.sha256).hexdigest()

    signed = urlencode({**fields, "hash": good_hash})
    assert telegram.validate_init_data(signed) is not None

    forged = urlencode({**fields, "hash": "0" * 64})
    assert telegram.validate_init_data(forged) is None
    assert telegram.validate_init_data("") is None


def test_commands_land_in_activity_log(tmp_path, monkeypatch):
    """Каждая команда — строка в журнале, человек — в tg_chats с именем."""
    monkeypatch.setattr(telegram, "DB_PATH", tmp_path / "tg.db")
    monkeypatch.setattr(telegram, "send",
                        lambda chat_id, text, keyboard=None: None)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "testbot")

    monkeypatch.setattr(telegram, "find_zone", lambda query: None)
    telegram.handle_text(5, "/status", {"username": "dmitry", "first_name": "Д"})
    telegram.handle_text(5, "Курская область")

    with telegram.closing(telegram._connect()) as connection:
        kinds = [row["kind"] for row in
                 connection.execute("SELECT kind FROM tg_activity ORDER BY at")]
        chat = connection.execute(
            "SELECT username, name FROM tg_chats WHERE chat_id = 5").fetchone()
    assert kinds == ["/status", "text"]
    assert chat["username"] == "dmitry" and chat["name"] == "Д"


def test_map_button_is_a_tme_link(monkeypatch):
    """Кнопка — ссылка t.me: переживает репост и открывает мини-приложение."""
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "tihoenebo_bot")
    monkeypatch.setattr(telegram, "_username_cache", None)
    button = telegram.open_map_button()["inline_keyboard"][0][0]
    assert button["url"] == "https://t.me/tihoenebo_bot?startapp"
    assert "web_app" not in button


def test_airport_line_speaks_plainly():
    """«аэропорт закрыт» / «аэропорт открыт» вместо «инфраструктуры»."""
    closed = telegram._event_line({
        "zone_name": "Внуково", "signal_type": "infra", "threat_type": "airport",
        "status": "active", "last_seen_at": "2026-08-08T10:00:00+00:00"})
    assert "аэропорт закрыт" in closed
    opened = telegram._event_line({
        "zone_name": "Внуково", "signal_type": "infra", "threat_type": "airport",
        "status": "resolved", "last_seen_at": "2026-08-08T11:00:00+00:00"})
    assert "аэропорт открыт" in opened


def test_map_watch_subscribes_chat(tmp_path, monkeypatch):
    """Колокольчик в мини-аппе подписывает чат — как команда /watch.

    Подпись initData обязательна: без неё любой подписывал бы чужие чаты
    голым POST-ом. Бот отвечает в чат подтверждением.
    """
    import asyncio
    import hashlib
    import hmac as hmac_mod
    import json
    from urllib.parse import urlencode

    monkeypatch.setattr(telegram, "DB_PATH", tmp_path / "tg.db")
    monkeypatch.setattr(telegram, "token", lambda: "12345:secret")
    with telegram.closing(telegram._connect()) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS zones (id TEXT PRIMARY KEY, name_ru TEXT)")
        connection.execute(
            "INSERT INTO zones (id, name_ru) VALUES ('kursk', 'Курская область')")
        connection.commit()

    fields = {"user": '{"id": 9, "username": "u", "first_name": "n"}',
              "auth_date": "1754500000"}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac_mod.new(b"WebAppData", b"12345:secret", hashlib.sha256).digest()
    good = hmac_mod.new(secret, check.encode(), hashlib.sha256).hexdigest()
    signed = urlencode({**fields, "hash": good})

    sent = []
    monkeypatch.setattr(telegram, "send",
                        lambda chat_id, text, keyboard=None: sent.append(text))

    class FakeRequest:
        def __init__(self, payload):
            self._payload = payload

        async def json(self):
            return self._payload

    ok = asyncio.run(telegram.map_watch(
        FakeRequest({"init_data": signed, "zone_id": "kursk", "on": True})))
    assert ok == {"ok": True}
    with telegram.closing(telegram._connect()) as connection:
        zones = json.loads(connection.execute(
            "SELECT zones FROM tg_chats WHERE chat_id = 9").fetchone()["zones"])
        kinds = [r["kind"] for r in connection.execute(
            "SELECT kind FROM tg_activity")]
    assert zones == ["kursk"]
    assert "watch_map" in kinds
    assert sent and "Слежу" in sent[0]

    # Подделка не проходит и ничего не подписывает.
    forged = urlencode({**fields, "hash": "0" * 64})
    bad = asyncio.run(telegram.map_watch(
        FakeRequest({"init_data": forged, "zone_id": "kursk", "on": True})))
    assert bad == {"ok": False}


def test_notifications_speak_sentences_not_labels():
    """«Краснодар — инфраструктура» никому ничего не говорило.

    Уведомление обязано сказать, что происходит: слова из внутреннего
    словаря сигналов остались в легендах, а человеку идёт предложение.
    """
    stamp = "2026-08-18T10:51:00+00:00"
    line = telegram._event_line({
        "place_name": "Краснодар", "signal_type": "infra",
        "threat_type": "infra", "last_seen_at": stamp})
    assert "инфраструктура" not in line
    assert "перекрыто движение" in line

    seen = telegram._event_line({
        "place_name": "Краснодар", "signal_type": "detection",
        "threat_type": "uav", "last_seen_at": stamp})
    assert "в небе видят БПЛА" in seen

    danger = telegram._event_line({
        "place_name": "Севастополь", "signal_type": "danger",
        "threat_type": "rocket", "last_seen_at": stamp})
    assert "объявлена ракетная опасность" in danger
    # Тип угрозы не дублируется хвостом, когда предложение назвало его само.
    assert "· ракета" not in danger
