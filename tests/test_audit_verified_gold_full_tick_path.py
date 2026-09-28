from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from research.causal_comparison import SequenceEvent
from research.risk_trajectory import RiskQuote, RiskSpec, compare_risk
from tools.audit_verified_gold_full_tick_path import (
    _verified_entry_binding, shadow_sequence, summarize_comparison,
)
from tools.audit_week_shadow_control_path import utc_ms


BASE = datetime(2026, 9, 18, 11, tzinfo=timezone.utc)
BASE_MS = utc_ms(BASE.isoformat())
PAIRING = {"status": "verified_journal_entry_binding",
           "full_entry_identity_verified": True,
           "pairs": [{"leg_index": 0, "native_position_id": 101,
                      "volume": "0.04", "native_entry_price": "100.02",
                      "shadow_entry_price": "100.00"}]}
POSITION = {"leg_index": 0, "status": "closed", "volume": 0.04,
            "entry_price": 100.0, "opened_tick_msc": BASE_MS,
            "closed_tick_msc": BASE_MS + 2000, "close_price": 101.0,
            "target_price": 101.0, "close_reason": "target", "realized_eur": 1.75}


def state_rows(position=POSITION):
    return [
        {"ev": "strategy_shadow_transition", "transition": "virtual_fill",
         "transition_tick_msc": BASE_MS, "transition_details": {"leg_index": 0}},
        {"ev": "strategy_shadow_transition", "transition": "virtual_position_closed",
         "transition_tick_msc": BASE_MS + 2000,
         "transition_details": {"leg_indexes": [0], "close_price": 101.0}},
        {"ev": "strategy_shadow_checkpoint",
         "state": {"signal_id": "canal2_1", "candidate_id": "gold_now_555_v1",
                   "status": "closed", "complete": True, "evidence_blockers": [],
                   "direction": "BUY", "realized_eur": 1.75, "positions": [position]}},
    ]


def test_shadow_sequence_binds_all_entry_and_exit_facts():
    events = shadow_sequence("canal2_1", PAIRING, state_rows())
    assert [row.kind for row in events] == ["entry", "exit"]
    assert [row.slot for row in events] == [1, 1]
    assert [row.money for row in events] == [Decimal("0.00"), Decimal("1.75")]
    assert events[0].at == BASE and events[1].at == BASE + timedelta(seconds=2)


def test_shadow_sequence_rejects_missing_close_or_changed_entry():
    with pytest.raises(ValueError, match="transition missing"):
        shadow_sequence("canal2_1", PAIRING, [state_rows()[0], state_rows()[-1]])
    with pytest.raises(ValueError, match="contradicts"):
        shadow_sequence("canal2_1", PAIRING,
                        state_rows({**POSITION, "entry_price": 100.01}))


def test_comment_bound_entry_is_comparable_but_not_upgraded_to_journal_evidence():
    ordinal = {**PAIRING, "status": "diagnostic_ordinal_pairing",
               "full_entry_identity_verified": False}
    broker = {"signal_id": "canal2_1",
              "status": "verified_broker_deal_comment_leg_binding",
              "prior_pairing_status": "diagnostic_ordinal_pairing",
              "native_leg_identity_verified": True,
              "live_order_request_chain_verified": False,
              "leg_count": 1,
              "legs": [{"leg_index": 0, "native_position_id": 101,
                        "native_entry_price": "100.02", "shadow_entry_price": "100.00",
                        "volume": "0.04"}]}
    assert _verified_entry_binding("canal2_1", ordinal, broker) == broker["status"]
    assert len(shadow_sequence("canal2_1", ordinal, state_rows(), broker_binding=broker)) == 2
    with pytest.raises(ValueError, match="verified entry identities"):
        shadow_sequence("canal2_1", ordinal, state_rows())
    changed = {**broker, "legs": [{**broker["legs"][0], "native_entry_price": "100.03"}]}
    with pytest.raises(ValueError, match="contradicts"):
        shadow_sequence("canal2_1", ordinal, state_rows(), broker_binding=changed)
    inflated = {**broker, "live_order_request_chain_verified": True}
    with pytest.raises(ValueError, match="broker-comment entry"):
        shadow_sequence("canal2_1", ordinal, state_rows(), broker_binding=inflated)


def test_target_touch_quote_can_exceed_settled_fill_but_not_miss_it():
    rows = state_rows()
    rows[1]["transition_details"]["close_price"] = 101.12
    assert shadow_sequence("canal2_1", PAIRING, rows)[1].price == Decimal("101.0")
    rows[1]["transition_details"]["close_price"] = 100.99
    with pytest.raises(ValueError, match="contradicts"):
        shadow_sequence("canal2_1", PAIRING, rows)


def test_full_tick_summary_counts_exposure_and_money_path_not_just_final():
    observed = [
        SequenceEvent(1, "entry", BASE, "BUY", Decimal("100"), Decimal("0.04"),
                      Decimal("0.00"), "native"),
        SequenceEvent(1, "exit", BASE + timedelta(seconds=2), "BUY", Decimal("101"),
                      Decimal("0.04"), Decimal("1.75"), "native"),
    ]
    virtual = [
        SequenceEvent(1, "entry", BASE + timedelta(seconds=1), "BUY", Decimal("99"),
                      Decimal("0.04"), Decimal("0.00"), "virtual"),
        SequenceEvent(1, "exit", BASE + timedelta(seconds=2), "BUY", Decimal("101"),
                      Decimal("0.04"), Decimal("1.75"), "virtual"),
    ]
    quotes = [RiskQuote(BASE + timedelta(seconds=i), Decimal(str(bid)),
                        Decimal(str(bid + 0.2)))
              for i, bid in enumerate((99.8, 98.8, 100.8))]
    spec = RiskSpec("EUR", 2, Decimal(100), "identity", 5000, 5000)
    report = summarize_comparison(compare_risk(observed, virtual, quotes, spec=spec))
    assert report["status"] == "mismatch"
    assert report["exposure_difference_marks"] > 0
    assert report["floating_difference_marks"] > 0
    assert report["native"]["known_sample_metrics"]["final_net"] == "1.75"
    assert report["shadow"]["known_sample_metrics"]["final_net"] == "1.75"


def test_stale_causal_fx_preserves_partial_evidence_without_drawdown_claim():
    observed = [
        SequenceEvent(1, "entry", BASE, "BUY", Decimal("100"), Decimal("0.04"),
                      Decimal("0.00"), "native"),
        SequenceEvent(1, "exit", BASE + timedelta(seconds=2), "BUY", Decimal("101"),
                      Decimal("0.04"), Decimal("1.75"), "native"),
    ]
    virtual = [
        SequenceEvent(1, "entry", BASE + timedelta(seconds=1), "BUY", Decimal("99"),
                      Decimal("0.04"), Decimal("0.00"), "virtual"),
        SequenceEvent(1, "exit", BASE + timedelta(seconds=2), "BUY", Decimal("101"),
                      Decimal("0.04"), Decimal("1.75"), "virtual"),
    ]
    quotes = [RiskQuote(BASE + timedelta(seconds=i), Decimal(str(bid)),
                        Decimal(str(bid + 0.2)), BASE, Decimal("1.10"),
                        Decimal("1.11"))
              for i, bid in enumerate((99.8, 98.8, 100.8))]
    spec = RiskSpec("EUR", 2, Decimal(100), "account_base_profit_quote", 500, 5000)
    report = summarize_comparison(compare_risk(observed, virtual, quotes, spec=spec))
    assert report["status"] == "blocked"
    assert "stale_conversion_quote" in report["blockers"]
    assert report["full_risk_path_comparable"] is False
    assert report["exposure_difference_marks"] > 0
    assert report["realized_difference_marks"] == 0
    assert report["drawdown_delta_eur"] is None
    assert report["max_abs_total_difference_scope"] == "known_marks_only"


def test_full_tick_summary_separates_entry_price_from_exposure_timing():
    observed = [
        SequenceEvent(1, "entry", BASE, "BUY", Decimal("100"), Decimal("0.04"),
                      Decimal("0.00"), "native"),
        SequenceEvent(1, "exit", BASE + timedelta(seconds=2), "BUY", Decimal("101"),
                      Decimal("0.04"), Decimal("1.75"), "native"),
    ]
    virtual = [
        SequenceEvent(1, "entry", BASE, "BUY", Decimal("99"), Decimal("0.04"),
                      Decimal("0.00"), "virtual"),
        SequenceEvent(1, "exit", BASE + timedelta(seconds=2), "BUY", Decimal("101"),
                      Decimal("0.04"), Decimal("1.75"), "virtual"),
    ]
    quotes = [RiskQuote(BASE + timedelta(seconds=i), Decimal(str(bid)),
                        Decimal(str(bid + 0.2)))
              for i, bid in enumerate((99.8, 98.8, 100.8))]
    spec = RiskSpec("EUR", 2, Decimal(100), "identity", 5000, 5000)
    report = summarize_comparison(compare_risk(observed, virtual, quotes, spec=spec))
    assert report["exposure_difference_marks"] == 0
    assert report["same_exposure_floating_difference_marks"] > 0
    assert report["same_position_state_floating_difference_marks"] > 0
    assert report["native"]["known_sample_metrics"]["final_net"] == "1.75"
    assert report["shadow"]["known_sample_metrics"]["final_net"] == "1.75"
