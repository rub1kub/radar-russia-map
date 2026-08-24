"""Тесты веб-пуша: та же логика тормоза, что у бота — общий модуль."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import push


def _fresh(minutes_ago: float = 0) -> str:
    """Свежая метка события: рассылка игнорирует всё старше 15 минут."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes_ago)).isoformat()


def test_repeat_danger_without_clear_is_throttled(tmp_path, monkeypatch):
    """Веб-пуш использует тот же тормоз, что и бот — не дублирует его логику."""
    import json

    monkeypatch.setattr(push, "DB_PATH", tmp_path / "push.db")
    sent = []
    monkeypatch.setattr(push, "_send",
                        lambda connection, row, payload: sent.append(payload))

    with push.closing(push._connect()) as connection:
        connection.execute(
            "INSERT INTO push_subscriptions"
            " (endpoint, p256dh, auth, zones, created_at)"
            " VALUES (?,?,?,?,datetime('now'))",
            ("https://push.example/1", "key", "auth",
             json.dumps(["krasnodar"])))
        connection.commit()

    def event(eid, signal, at, status="active"):
        return {"id": eid, "status": status, "zone_id": "krasnodar",
                "zone_path": ["krasnodar"], "signal_type": signal,
                "threat_type": "uav", "severity": 5,
                "place_name": "Краснодар", "last_seen_at": at}

    push.deliver_once({"events": [
        event("e1", "danger", _fresh(9.7))]})
    assert len(sent) == 1

    push.deliver_once({"events": [
        event("e2", "danger", _fresh(9.4))]})
    assert len(sent) == 1, "повторная опасность без отбоя не должна уходить"

    push.deliver_once({"events": [
        event("e3", "alarm", _fresh(9.1))]})
    assert len(sent) == 2


def test_stale_backlog_event_is_not_delivered(tmp_path, monkeypatch):
    """Догонка истории после простоя — не живой эфир.

    24.08 сбор пролежал ночь; после рестарта конвейер проиграл утро в
    ускоренной перемотке, и подписчик получил «опасность 11:23» и её же
    «отбой 11:44» пачкой в 12:13. События старше 15 минут не рассылаются.
    """
    import json

    monkeypatch.setattr(push, "DB_PATH", tmp_path / "push.db")
    sent = []
    monkeypatch.setattr(push, "_send",
                        lambda connection, row, payload: sent.append(payload))
    with push.closing(push._connect()) as connection:
        connection.execute(
            "INSERT INTO push_subscriptions"
            " (endpoint, p256dh, auth, zones, created_at)"
            " VALUES (?,?,?,?,datetime('now'))",
            ("https://push.example/1", "key", "auth",
             json.dumps(["krasnodar"])))
        connection.commit()

    def event(eid, at, status="active", resolved_at=None):
        return {"id": eid, "status": status, "zone_id": "krasnodar",
                "zone_path": ["krasnodar"], "signal_type": "danger",
                "threat_type": "uav", "severity": 5,
                "place_name": "Краснодар", "last_seen_at": at,
                "resolved_at": resolved_at}

    # Событие 50-минутной давности и его отбой получасовой — молчание.
    push.deliver_once({"events": [
        event("old", _fresh(50)),
        event("old", _fresh(50), status="resolved",
              resolved_at=_fresh(29))]})
    assert sent == []

    # Свежее — уходит.
    push.deliver_once({"events": [event("new", _fresh(0.5))]})
    assert len(sent) == 1
