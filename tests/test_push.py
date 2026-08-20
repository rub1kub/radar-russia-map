"""Тесты веб-пуша: та же логика тормоза, что у бота — общий модуль."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import push


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
        event("e1", "danger", "2026-08-20T21:31:00+00:00")]})
    assert len(sent) == 1

    push.deliver_once({"events": [
        event("e2", "danger", "2026-08-20T21:55:00+00:00")]})
    assert len(sent) == 1, "повторная опасность без отбоя не должна уходить"

    push.deliver_once({"events": [
        event("e3", "alarm", "2026-08-20T22:05:00+00:00")]})
    assert len(sent) == 2
