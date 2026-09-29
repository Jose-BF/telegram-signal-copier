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


def test_executor_marks_orders_in_flight_and_clears_them(tmp_path, monkeypatch):
    import executor
    monkeypatch.setattr(executor.config, "BOT_RUNTIME_HEARTBEAT_FILE", str(tmp_path / "runtime_heartbeat.json"))
    seen = []
    def fake_send(req):
        seen.extend(p.name for p in tmp_path.glob("mt5_request_in_flight.*.json"))
        return None
    monkeypatch.setattr(executor.mt5, "order_send", fake_send)
    monkeypatch.setattr(executor.mt5, "last_error", lambda: (0, "x"))
    monkeypatch.setattr(executor, "_emit_anomaly", lambda *a, **k: None)
    executor._send_safe({"action": 1, "symbol": "XAUUSD"}, "test")
    assert len(seen) == 1 and not list(tmp_path.glob("mt5_request_in_flight.*.json"))


def test_watcher_sees_only_the_running_bots_in_flight_orders(tmp_path):
    import json, os, time
    from tools import run_bot_watch as w
    (tmp_path / "mt5_request_in_flight.111.1.json").write_text(json.dumps({"pid": 111}))
    (tmp_path / "mt5_request_in_flight.222.1.json").write_text(json.dumps({"pid": 222}))
    age = w._mt5_request_in_flight_age_s(111, now=time.time() + 30, folder=tmp_path)
    assert age is not None and 25 < age < 40
    assert w._mt5_request_in_flight_age_s(333, folder=tmp_path) is None
