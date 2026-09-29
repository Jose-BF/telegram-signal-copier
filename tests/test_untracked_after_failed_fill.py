"""A failed entry whose order the broker executed anyway must be detected and alerted (28/09/2026)."""
import asyncio

import listener


def test_executed_position_after_failed_fill_is_detected(monkeypatch):
    events, alerts = [], []
    monkeypatch.setattr(listener, "UNTRACKED_POSITION_CHECK_DELAYS_SEC", (0.0, 0.0))
    async def fake_run(fn, *a, **k):
        return {"canal1_23201": {"market": 2103957181}}
    async def fake_notify(text):
        alerts.append(text); return True
    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(listener, "notify", fake_notify)
    monkeypatch.setattr(listener.journal, "event", lambda sig, ev, **f: events.append((sig, ev)))
    monkeypatch.setattr(listener.journal, "anomaly", lambda *a, **k: events.append(("anomaly", a[1])))
    assert asyncio.run(listener._check_untracked_after_failed_fill("canal1_23201")) is True
    assert ("canal1_23201", "untracked_position_detected") in events and alerts


def test_no_position_means_no_alert(monkeypatch):
    alerts = []
    monkeypatch.setattr(listener, "UNTRACKED_POSITION_CHECK_DELAYS_SEC", (0.0, 0.0))
    async def fake_run(fn, *a, **k):
        return {}
    async def fake_notify(text):
        alerts.append(text); return True
    monkeypatch.setattr(listener, "_run", fake_run)
    monkeypatch.setattr(listener, "notify", fake_notify)
    assert asyncio.run(listener._check_untracked_after_failed_fill("canal1_1")) is False and not alerts
