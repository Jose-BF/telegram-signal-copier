import json

import pytest

from live_basket_guard import (
    GuardPolicy,
    GuardState,
    evaluate_guard,
    load_guard_states,
    load_realized_ticket_cache,
    load_signal_ticket_ids,
)


POLICY = GuardPolicy(
    enabled=True,
    channel="canal1",
    loss_cap=-50.0,
    profit_arm=30.0,
    profit_lock=20.0,
)


def test_guard_does_nothing_inside_normal_range():
    decision = evaluate_guard(
        channel="canal1",
        floating_pl=12.0,
        n_open=4,
        state=GuardState(),
        policy=POLICY,
    )

    assert decision.action == "none"
    assert decision.reason is None
    assert decision.state.armed is False
    assert decision.state.peak_pl == 12.0


def test_guard_closes_dubai_basket_at_loss_cap():
    decision = evaluate_guard(
        channel="canal1",
        floating_pl=-50.01,
        n_open=4,
        state=GuardState(),
        policy=POLICY,
    )

    assert decision.action == "close"
    assert decision.reason == "loss_cap"
    assert decision.state.triggered is True


def test_guard_arms_then_closes_at_profit_lock():
    armed = evaluate_guard(
        channel="canal1",
        floating_pl=31.0,
        n_open=4,
        state=GuardState(),
        policy=POLICY,
    )
    held = evaluate_guard(
        channel="canal1",
        floating_pl=25.0,
        n_open=3,
        state=armed.state,
        policy=POLICY,
    )
    closed = evaluate_guard(
        channel="canal1",
        floating_pl=19.99,
        n_open=3,
        state=held.state,
        policy=POLICY,
    )

    assert armed.action == "arm"
    assert held.action == "none"
    assert held.state.peak_pl == 31.0
    assert closed.action == "close"
    assert closed.reason == "profit_lock"


def test_guard_is_channel_scoped_and_requires_open_positions():
    wrong_channel = evaluate_guard(
        channel="canal2",
        floating_pl=-100.0,
        n_open=5,
        state=GuardState(),
        policy=POLICY,
    )
    no_positions = evaluate_guard(
        channel="canal1",
        floating_pl=-100.0,
        n_open=0,
        state=GuardState(),
        policy=POLICY,
    )

    assert wrong_channel.action == "none"
    assert no_positions.action == "none"


def test_triggered_guard_is_idempotent_but_can_recover_interrupted_close():
    triggered = GuardState(
        armed=True,
        triggered=True,
        peak_pl=35.0,
        trigger_reason="profit_lock",
    )
    duplicate = evaluate_guard(
        channel="canal1",
        floating_pl=10.0,
        n_open=2,
        state=triggered,
        policy=POLICY,
    )
    recovery = evaluate_guard(
        channel="canal1",
        floating_pl=10.0,
        n_open=2,
        state=GuardState(
            armed=True,
            triggered=True,
            peak_pl=35.0,
            trigger_reason="profit_lock",
            recovery_pending=True,
        ),
        policy=POLICY,
    )

    assert duplicate.action == "none"
    assert recovery.action == "close"
    assert recovery.reason == "recovery"
    assert recovery.state.recovery_pending is False


def test_policy_rejects_inverted_profit_thresholds():
    with pytest.raises(ValueError, match="profit_arm"):
        GuardPolicy(
            enabled=True,
            channel="canal1",
            loss_cap=-50.0,
            profit_arm=20.0,
            profit_lock=30.0,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("loss_cap", float("nan")),
        ("loss_cap", float("-inf")),
        ("profit_arm", float("inf")),
        ("profit_lock", float("nan")),
    ],
)
def test_policy_rejects_non_finite_thresholds(field, value):
    values = {
        "enabled": True,
        "channel": "canal1",
        "loss_cap": -50.0,
        "profit_arm": 30.0,
        "profit_lock": 20.0,
    }
    values[field] = value

    with pytest.raises(ValueError, match="finite"):
        GuardPolicy(**values)


def test_guard_rejects_non_finite_mt5_profit_sample():
    with pytest.raises(ValueError, match="finite"):
        evaluate_guard(
            channel="canal1",
            floating_pl=float("nan"),
            n_open=4,
            state=GuardState(),
            policy=POLICY,
        )


def test_guard_does_not_arm_or_lock_profit_without_complete_evidence():
    no_arm = evaluate_guard(
        channel="canal1",
        floating_pl=35.0,
        n_open=2,
        state=GuardState(),
        policy=POLICY,
        profit_evidence_complete=False,
    )
    no_lock = evaluate_guard(
        channel="canal1",
        floating_pl=10.0,
        n_open=2,
        state=GuardState(armed=True, peak_pl=35.0),
        policy=POLICY,
        profit_evidence_complete=False,
    )
    loss_close = evaluate_guard(
        channel="canal1",
        floating_pl=-51.0,
        n_open=2,
        state=GuardState(),
        policy=POLICY,
        profit_evidence_complete=False,
    )

    assert no_arm.action == "none"
    assert no_arm.state.peak_pl is None
    assert no_lock.action == "none"
    assert loss_close.reason == "loss_cap"


def test_guard_state_is_recovered_from_journal(tmp_path):
    path = tmp_path / "trade_events.jsonl"
    rows = [
        {
            "sig": "canal1_100",
            "ev": "basket_guard_armed",
            "observed_pl": 31.2,
            "peak_pl": 31.2,
        },
        {
            "sig": "canal1_100",
            "ev": "basket_guard_peak_advanced",
            "observed_pl": 36.7,
            "peak_pl": 36.7,
        },
        {
            "sig": "canal1_100",
            "ev": "basket_guard_triggered",
            "reason": "profit_lock",
            "observed_pl": 19.8,
            "peak_pl": 31.2,
        },
        {
            "sig": "canal2_200",
            "ev": "basket_guard_triggered",
            "reason": "loss_cap",
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    states = load_guard_states(path, {"canal1_100"})

    assert states == {
        "canal1_100": GuardState(
            armed=True,
            triggered=True,
            peak_pl=36.7,
            trigger_reason="profit_lock",
            recovery_pending=True,
        )
    }


def test_realized_ticket_cache_is_recovered_from_journal(tmp_path):
    path = tmp_path / "trade_events.jsonl"
    rows = [
        {
            "sig": "canal1_100",
            "ev": "basket_guard_realized_ticket_confirmed",
            "ticket": 101,
            "realized_pl": 3.83,
        },
        {
            "sig": "canal1_100",
            "ev": "basket_guard_realized_ticket_confirmed",
            "ticket": 102,
            "realized_pl": 7.30,
        },
        {
            "sig": "canal2_200",
            "ev": "basket_guard_realized_ticket_confirmed",
            "ticket": 201,
            "realized_pl": 99.0,
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    assert load_realized_ticket_cache(path, {"canal1_100"}) == {
        "canal1_100": {101: 3.83, 102: 7.30}
    }


def test_signal_ticket_ids_are_recovered_from_fill_events(tmp_path):
    path = tmp_path / "trade_events.jsonl"
    rows = [
        {"sig": "canal1_100", "ev": "market_filled", "ticket": 101},
        {"sig": "canal1_100", "ev": "scale_out_leg_filled", "ticket": 102},
        {"sig": "canal1_100", "ev": "dca_filled", "ticket": 103},
        {"sig": "canal1_100", "ev": "rescue_market_opened", "ticket": 104},
        {"sig": "canal1_100", "ev": "pending_placed", "ticket": 999},
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    assert load_signal_ticket_ids(path, {"canal1_100"}) == {
        "canal1_100": [101, 102, 103, 104]
    }
