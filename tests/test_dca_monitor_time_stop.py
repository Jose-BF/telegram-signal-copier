"""
Regresiones del time-stop notify-only.

El time-stop actual NO debe cerrar automaticamente; solo notifica y deja
evidencia estructurada para analisis posterior en reconcile.py.
"""

import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

import dca_monitor
from state import Signal


@pytest.mark.asyncio
async def test_notify_time_stop_emits_outcome_anomaly_without_closing(
        monkeypatch):
    events = []
    anomalies = []
    notifications = []

    async def fake_notify(text):
        notifications.append(text)

    monkeypatch.setitem(sys.modules, "listener",
                        SimpleNamespace(notify=fake_notify))
    monkeypatch.setattr(
        dca_monitor, "_floating_pl_summary",
        lambda _sig: {
            "pl": -41.9,
            "n_open": 4,
            "lots_total": 0.04,
            "current_price": 4529.25,
            "avg_entry": 4538.4,
        })
    monkeypatch.setattr(dca_monitor, "_next_tp_for_signal",
                        lambda _sig: 4548.0)
    monkeypatch.setattr(dca_monitor, "_build_time_stop_recommendation",
                        lambda *_args: "mark for analysis")

    import journal
    monkeypatch.setattr(journal, "get_trade",
                        lambda _sig_id: {"mfe_usd": 7.5, "mae_usd": -44.0})
    monkeypatch.setattr(
        journal, "event",
        lambda sig, ev, **fields: events.append((sig, ev, fields)))
    monkeypatch.setattr(
        journal, "anomaly",
        lambda sig, category, severity, detail, **ctx:
        anomalies.append((sig, category, severity, detail, ctx)))

    sig = Signal(channel="canal1", message_id=19822, direction="BUY")
    sig.time_stop_at = datetime.utcnow()

    await dca_monitor._notify_time_stop(sig, elapsed_min=60.0)

    assert sig.status == "open"
    assert sig.time_stop_at is None
    assert len(notifications) == 1
    assert events[0][0:2] == ("canal1_19822", "time_stop_notified")
    assert anomalies[0][0:3] == ("canal1_19822", "outcome", "warning")
    assert anomalies[0][4]["pl"] == -41.9
