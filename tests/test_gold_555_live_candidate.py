from __future__ import annotations

import pytest

from gold_555_live_candidate import (
    CANDIDATE_FINGERPRINT,
    CANDIDATE_ID,
    Gold555AccountError,
    Gold555GuardState,
    Gold555Policy,
    assert_demo_eur_account,
    evaluate_guard,
    market_comment,
)


def test_policy_matches_frozen_candidate() -> None:
    policy = Gold555Policy()

    assert CANDIDATE_ID == "gold_now_555_v1"
    assert CANDIDATE_FINGERPRINT == (
        "555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0"
    )
    assert policy.entry_volumes == (0.04, 0.03, 0.03, 0.03, 0.03)
    assert policy.max_signal_volume == pytest.approx(0.16)
    assert policy.entry_expiry_minutes == 30


@pytest.mark.parametrize(
    ("direction", "anchor", "expected"),
    [
        ("BUY", 4300.0, (4300.0, 4298.5, 4297.0, 4295.5, 4294.0)),
        ("SELL", 4300.0, (4300.0, 4301.5, 4303.0, 4304.5, 4306.0)),
    ],
)
def test_entry_levels_are_anchored_to_first_real_fill(
    direction: str,
    anchor: float,
    expected: tuple[float, ...],
) -> None:
    assert Gold555Policy().entry_levels(direction, anchor) == expected


@pytest.mark.parametrize(
    ("direction", "fill", "leg_index", "expected"),
    [
        ("BUY", 4298.37, 0, 4298.87),
        ("BUY", 4296.91, 4, 4299.41),
        ("SELL", 4301.24, 0, 4300.74),
        ("SELL", 4306.18, 4, 4303.68),
    ],
)
def test_target_uses_each_leg_real_fill(
    direction: str,
    fill: float,
    leg_index: int,
    expected: float,
) -> None:
    assert Gold555Policy().target_price(direction, fill, leg_index) == pytest.approx(
        expected
    )


@pytest.mark.parametrize(
    ("direction", "fill", "expected"),
    [("BUY", 4300.0, 4270.0), ("SELL", 4300.0, 4330.0)],
)
def test_initial_stop_is_thirty_dollars_from_real_fill(
    direction: str,
    fill: float,
    expected: float,
) -> None:
    assert Gold555Policy().initial_stop(direction, fill) == pytest.approx(expected)


def test_buy_trailing_stop_tightens_but_never_loosens() -> None:
    policy = Gold555Policy()

    assert policy.trailing_stop("BUY", executable_price=4312.0, current_stop=4270.0) == 4282.0
    assert policy.trailing_stop("BUY", executable_price=4308.0, current_stop=4282.0) is None


def test_sell_trailing_stop_tightens_but_never_loosens() -> None:
    policy = Gold555Policy()

    assert policy.trailing_stop("SELL", executable_price=4288.0, current_stop=4330.0) == 4318.0
    assert policy.trailing_stop("SELL", executable_price=4292.0, current_stop=4318.0) is None


def test_profit_lock_arms_and_closes_after_one_euro_giveback() -> None:
    policy = Gold555Policy()
    armed = evaluate_guard(
        policy=policy,
        state=Gold555GuardState(),
        total_pl=30.0,
        n_open=5,
        elapsed_min=45.0,
        money_evidence_complete=True,
    )
    assert armed.action == "arm"
    assert armed.state.peak_pl == 30.0

    higher = evaluate_guard(
        policy=policy,
        state=armed.state,
        total_pl=34.0,
        n_open=5,
        elapsed_min=46.0,
        money_evidence_complete=True,
    )
    close = evaluate_guard(
        policy=policy,
        state=higher.state,
        total_pl=33.0,
        n_open=5,
        elapsed_min=47.0,
        money_evidence_complete=True,
    )
    assert close.action == "close"
    assert close.reason == "profit_lock"


def test_time_exit_closes_only_non_negative_baskets() -> None:
    policy = Gold555Policy()

    negative = evaluate_guard(
        policy=policy,
        state=Gold555GuardState(),
        total_pl=-0.01,
        n_open=2,
        elapsed_min=180.0,
        money_evidence_complete=True,
    )
    non_negative = evaluate_guard(
        policy=policy,
        state=Gold555GuardState(),
        total_pl=0.0,
        n_open=2,
        elapsed_min=180.0,
        money_evidence_complete=True,
    )

    assert negative.action == "none"
    assert non_negative.action == "close"
    assert non_negative.reason == "non_negative_time_exit"


def test_incomplete_money_evidence_never_closes() -> None:
    decision = evaluate_guard(
        policy=Gold555Policy(),
        state=Gold555GuardState(),
        total_pl=31.0,
        n_open=5,
        elapsed_min=200.0,
        money_evidence_complete=False,
    )

    assert decision.action == "evidence_incomplete"
    assert decision.state.triggered is False


def test_account_gate_accepts_only_verified_demo_eur() -> None:
    assert_demo_eur_account(
        {"trade_mode": 0, "trade_mode_name": "demo", "currency": "EUR"}
    )

    for evidence in (
        {"trade_mode": 2, "trade_mode_name": "real", "currency": "EUR"},
        {"trade_mode": 0, "trade_mode_name": "demo", "currency": "USD"},
        None,
    ):
        with pytest.raises(Gold555AccountError):
            assert_demo_eur_account(evidence)


def test_comment_marker_is_distinct_from_c490() -> None:
    assert market_comment(380) == "c2_380_g55"
    assert market_comment(380, 4) == "c2_380_B4_g55"


def test_invalid_direction_or_leg_is_rejected() -> None:
    policy = Gold555Policy()
    with pytest.raises(ValueError):
        policy.entry_levels("HOLD", 4300.0)
    with pytest.raises(IndexError):
        policy.target_price("BUY", 4300.0, 5)
