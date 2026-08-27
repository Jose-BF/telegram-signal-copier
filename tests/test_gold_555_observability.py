from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import config
import gold_555_live_candidate
import main
import position_lifecycle_monitor as monitor
from state import Signal


def _enable_only_gold_555(monkeypatch) -> None:
    monkeypatch.setattr(config, "STRATEGY_C1_BALANCED_V1_ENABLED", False)
    monkeypatch.setattr(config, "STRATEGY_C2_GOLD_NOW_C490_ENABLED", False)
    monkeypatch.setattr(config, "STRATEGY_C2_GOLD_NOW_555_ENABLED", True)
    monkeypatch.setattr(config, "GOLD_NOW_LIVE_POLICY", "555")
    monkeypatch.setattr(
        config,
        "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL",
        0.05,
    )
    monkeypatch.setattr(
        config,
        "GOLD_555_MAX_PLANNED_LOTS_PER_SIGNAL",
        0.16,
    )


def _signal() -> Signal:
    first_fill_at = datetime(2026, 8, 27, 8, 0, 0)
    return Signal(
        channel="canal2",
        message_id=380,
        direction="BUY",
        timestamp=first_fill_at,
        market_ticket=1000,
        market_fill_price=4300.0,
        live_strategy_id=gold_555_live_candidate.CANDIDATE_ID,
        live_strategy_fingerprint=(
            gold_555_live_candidate.CANDIDATE_FINGERPRINT
        ),
        candidate_first_fill_at=first_fill_at,
    )


def test_live_contract_discloses_selector_account_gate_and_trial_status(
    monkeypatch,
) -> None:
    _enable_only_gold_555(monkeypatch)

    contract = main._live_strategy_contract()

    assert contract["gold"]["selector"] == "555"
    assert contract["gold"]["account_gate"] == {
        "trade_mode": "demo",
        "currency": "EUR",
        "revalidated_before_first_fill": True,
    }
    assert contract["gold"]["evidence_status"] == (
        "in_sample_candidate_forward_trial"
    )
    assert contract["gold"]["independent_forward_validation"] is False
    assert contract["risk"]["max_planned_lots_per_signal"] == 0.16
    assert contract["risk"]["legacy_max_planned_lots_per_signal"] == 0.05


def test_gold_555_guard_event_retains_broker_tick_and_policy(monkeypatch) -> None:
    signal = _signal()
    events = []
    monkeypatch.setattr(
        monitor,
        "_journal_event",
        lambda sig, event, **fields: events.append((sig, event, fields)),
    )

    monitor._apply_live_basket_guard(
        signal,
        {
            "positions_complete": True,
            "floating_pl": 30.0,
            "realized_pl": 0.0,
            "realized_complete": True,
            "total_pl": 30.0,
            "n_open": 1,
            "open_tickets": [1000],
            "source_tick_time_msc": 1787817600123,
        },
        now=signal.candidate_first_fill_at + timedelta(minutes=10),
    )

    _, event, fields = events[-1]
    assert event == "basket_guard_armed"
    assert fields["strategy_id"] == gold_555_live_candidate.CANDIDATE_ID
    assert fields["strategy_fingerprint"] == (
        gold_555_live_candidate.CANDIDATE_FINGERPRINT
    )
    assert fields["source_tick_time_msc"] == 1787817600123
    assert fields["observed_at_utc"] == "2026-08-27T08:10:00.000"


def test_prolonged_negative_exposure_alerts_once_with_decision_inputs(
    monkeypatch,
) -> None:
    signal = _signal()
    events = []
    anomalies = []
    monkeypatch.setattr(
        monitor,
        "_journal_event",
        lambda sig, event, **fields: events.append((sig, event, fields)),
    )
    monkeypatch.setattr(
        monitor,
        "_journal_anomaly",
        lambda sig, category, severity, detail, **fields: anomalies.append(
            (sig, category, severity, detail, fields)
        ),
    )
    summary = {
        "positions_complete": True,
        "floating_pl": -42.5,
        "realized_pl": 0.0,
        "realized_complete": True,
        "total_pl": -42.5,
        "n_open": 2,
        "open_tickets": [1000, 1001],
        "source_tick_time_msc": 1787828400123,
    }
    now = signal.candidate_first_fill_at + timedelta(minutes=181)

    first = monitor._maybe_alert_gold_555_prolonged_exposure(
        signal,
        summary,
        now=now,
    )
    second = monitor._maybe_alert_gold_555_prolonged_exposure(
        signal,
        summary,
        now=now + timedelta(seconds=1),
    )

    assert first is True
    assert second is False
    assert signal.candidate_prolonged_exposure_alerted is True
    assert [row[1] for row in events] == [
        "gold_555_prolonged_exposure_alerted"
    ]
    assert len(anomalies) == 1
    assert anomalies[0][1] == "outcome"
    fields = anomalies[0][4]
    assert fields["code"] == "gold_555_prolonged_negative_exposure"
    assert fields["total_pl"] == -42.5
    assert fields["elapsed_min"] == 181.0
    assert fields["source_tick_time_msc"] == 1787828400123
    assert fields["strategy_fingerprint"] == (
        gold_555_live_candidate.CANDIDATE_FINGERPRINT
    )


def test_trailing_does_not_compete_with_a_requested_basket_close(
    monkeypatch,
) -> None:
    signal = _signal()
    signal.candidate_hard_stops = {1000: 4270.0}
    signal.requested_close_reason = "PROVIDER_CLOSE"
    requests = []
    monkeypatch.setattr(
        monitor.pending_actions,
        "enqueue_modify_sl",
        lambda *args, **kwargs: requests.append((args, kwargs)),
    )

    tightened = __import__("asyncio").run(
        monitor._apply_gold_555_trailing_stops(
            signal,
            SimpleNamespace(bid=4310.0, ask=4310.2, time_msc=1),
        )
    )

    assert tightened == 0
    assert requests == []
