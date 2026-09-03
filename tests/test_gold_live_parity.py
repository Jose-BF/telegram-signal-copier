from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np

from research.dubai_iterative.dataset import SignalLeg, SignalPath
from research.gold_iterative.contracts import gold_555_genome
from research.gold_iterative.live_parity import certify_live_logic_mirror


BASE = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _array(values, dtype=float):
    result = np.asarray(values, dtype=dtype)
    result.setflags(write=False)
    return result


def _path(*, signal_id: str = "canal2_100", pnl: str = "2.00") -> SignalPath:
    times = tuple(BASE + timedelta(seconds=index) for index in range(3))
    return SignalPath(
        signal_id=signal_id,
        day="2026-09-01",
        direction="BUY",
        signal_observed_at=BASE,
        opened_at=BASE,
        actual_pnl_eur=Decimal(pnl),
        legs=(
            SignalLeg(
                ticket="101",
                role="market_a",
                volume=0.04,
                opened_at=BASE,
                open_price=100.20,
                closed_at=times[1],
                close_price=100.70,
                close_reason="tp",
                actual_pnl_eur=Decimal(pnl),
                tp_events=(),
                sl_events=(),
            ),
        ),
        provider_events=(),
        times_ns=_array(
            [int(value.timestamp() * 1_000_000_000) for value in times],
            dtype=np.int64,
        ),
        bid=_array([100.20, 100.71, 100.75]),
        ask=_array([100.40, 100.91, 100.95]),
        exit_quotes=_array([100.20, 100.71, 100.75]),
        fx_bid=_array([1.0, 1.0, 1.0]),
        fx_ask=_array([1.0, 1.0, 1.0]),
        fx_age_ms=_array([0, 0, 0], dtype=np.int64),
        fx_valid=_array([True, True, True], dtype=np.bool_),
        contract_size=100.0,
        conversion_orientation="identity",
        currency_digits=2,
        market_evidence=({},),
        conversion_evidence=({},),
        entry_evidence_kind="actual_mt5",
    )


def _actual(*, signal_id: str = "canal2_100", pnl: object = 2.0, entries: int = 1):
    return {
        "sig_id": signal_id,
        "channel": "canal2",
        "n_positions": entries,
        "pnl_real_mt5": pnl,
        "reconciled_ok": True,
        "no_position_outcome_verified": entries > 0,
        "strategy_snapshot": {
            "live_strategy_id": "gold_now_555_v1",
            "live_strategy_fingerprint": (
                gold_555_genome().source_strategy_fingerprint
            ),
        },
    }


def _audit(*, signal_id: str = "canal2_100", tickets: int = 1):
    return {
        "sig_id": signal_id,
        "channel": "canal2",
        "status": "exact",
        "ticket_count": tickets,
        "exact_tickets": tickets,
        "blocked_tickets": 0,
        "mismatch_tickets": 0,
        "blockers": [],
    }


def test_live_logic_mirror_must_match_actual_mt5_to_the_cent() -> None:
    report = certify_live_logic_mirror(
        paths=(_path(),),
        actual_rows=(_actual(),),
        audit_rows=(_audit(),),
        genome=gold_555_genome(),
    )

    assert report["evidence_roles"] == {
        "actual_mt5": "observed_broker_result",
        "live_logic_mirror": "strategy_replay_conditioned_on_actual_mt5_fills",
        "shadow_prediction": "prospective_replay_from_telegram_and_ticks",
    }
    assert report["actual_mt5"]["net_eur"] == "2.00"
    assert report["live_logic_mirror"]["net_eur"] == "2.00"
    assert report["parity"]["status"] == "exact"
    assert report["parity"]["net_delta_eur"] == "0.00"
    assert report["management_replay_allowed"] is True
    assert report["historical_extension_allowed"] is False
    assert report["remaining_end_to_end_gates"] == [
        "prospective_entry_outcome_parity",
        "prospective_entry_trigger_parity",
        "broker_fill_parity",
        "deterministic_terminal_lifecycle_parity",
    ]
    assert report["rows"][0]["engine_agreement"] is True


def test_one_cent_difference_blocks_historical_extension() -> None:
    report = certify_live_logic_mirror(
        paths=(_path(pnl="2.01"),),
        actual_rows=(_actual(pnl=2.01),),
        audit_rows=(_audit(),),
        genome=gold_555_genome(),
    )

    assert report["parity"]["status"] == "mismatch"
    assert report["parity"]["net_delta_eur"] == "-0.01"
    assert report["management_replay_allowed"] is False
    assert report["historical_extension_allowed"] is False
    assert "money_mismatch" in report["rows"][0]["blockers"]


def test_missing_exact_tick_audit_blocks_the_mirror() -> None:
    report = certify_live_logic_mirror(
        paths=(_path(),),
        actual_rows=(_actual(),),
        audit_rows=(),
        genome=gold_555_genome(),
    )

    assert report["parity"]["status"] == "blocked"
    assert report["management_replay_allowed"] is False
    assert report["historical_extension_allowed"] is False
    assert "observed_tick_audit_missing" in report["rows"][0]["blockers"]


def test_verified_no_position_is_an_exact_zero_not_a_missing_signal() -> None:
    actual = _actual(pnl=0, entries=0)
    actual["no_position_outcome_verified"] = True
    report = certify_live_logic_mirror(
        paths=(),
        actual_rows=(actual,),
        audit_rows=(_audit(tickets=0),),
        genome=gold_555_genome(),
    )

    assert report["parity"]["status"] == "exact"
    assert report["actual_mt5"]["signals"] == 1
    assert report["live_logic_mirror"]["exact_signals"] == 1
    assert report["rows"][0]["live_logic_mirror_eur"] == "0.00"


def test_no_position_without_a_recorded_expiry_is_not_assumed_exact() -> None:
    report = certify_live_logic_mirror(
        paths=(),
        actual_rows=(_actual(pnl=0, entries=0),),
        audit_rows=(_audit(tickets=0),),
        genome=gold_555_genome(),
    )

    assert report["parity"]["status"] == "blocked"
    assert "live_no_position_outcome_unverified" in report["rows"][0]["blockers"]


def test_wrong_live_strategy_identity_is_blocked() -> None:
    actual = _actual()
    actual["strategy_snapshot"]["live_strategy_fingerprint"] = "not-the-555"

    report = certify_live_logic_mirror(
        paths=(_path(),),
        actual_rows=(actual,),
        audit_rows=(_audit(),),
        genome=gold_555_genome(),
    )

    assert report["parity"]["status"] == "blocked"
    assert "live_strategy_fingerprint_mismatch" in report["rows"][0]["blockers"]
