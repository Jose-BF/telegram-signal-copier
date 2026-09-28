from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from research.causal_replay import utc
from research.dubai_current_study import aggregate, provider_closes, resize_range, summarize
from research.dubai_range_study import build_genome


def range_genome():
    row = {"direction": "BUY", "first_priced": {"parsed": {"range": [1998, 2000], "sl": 1990, "tps": [2020]}}}
    return build_genome(row, {"entry": "on_levels", "exit": "tp1", "hold_minutes": 60}, 2000)[0]


@pytest.mark.parametrize("direction,quote,stop", [("BUY", 2000, 1990), ("SELL", 2000, 2010)])
def test_equal_risk_uses_loss_fx_bid_and_rounds_down(direction, quote, stop):
    result, usd, eur = resize_range(replace(range_genome(), stop_value=stop), quote, direction, 1.10)
    assert result.volume_weights == (.02,)
    assert Decimal(str(usd)) == 20
    assert Decimal(eur) <= 25
    assert Decimal(eur) == Decimal(20) / Decimal("1.1")


def test_equal_risk_below_minimum_is_an_explicit_no_order():
    result, usd, eur = resize_range(range_genome(), 2100, "BUY", 1.1)
    assert result is None
    assert usd == 0 and eur == "0"


@pytest.mark.parametrize("fx", [0, -1, float("nan")])
def test_equal_risk_rejects_invalid_fx(fx):
    with pytest.raises(ValueError, match="FX"):
        resize_range(range_genome(), 2000, "BUY", fx)


def test_provider_uses_receipts_direct_modality_and_signal_association():
    row = {"signal_id": "one", "received_utc": "2026-06-05T10:00:00Z"}
    base = {"signal_id": "one", "kind": "management", "barrier_reasons": [],
            "received_utc": "2026-06-05T10:01:00Z", "source_line_1based": 5,
            "source_row_sha256": "a" * 64, "parsed": {"modality": "direct", "action": "CLOSE_PARTIAL"}}
    messages = [base]
    for field, value in [("signal_id", "two"), ("received_utc", "2026-06-05T09:59:00Z"),
                         ("received_utc", "2026-06-05T10:03:00Z"), ("barrier_reasons", ["blocked"])]:
        messages.append({**base, field: value})
    for modality in ("conditional", "optional", "informational"):
        messages.append({**base, "parsed": {**base["parsed"], "modality": modality}})
    result = provider_closes(row, messages + [deepcopy(base)], utc("2026-06-05T10:02:00Z"))
    assert len(result) == 1
    assert result[0]["parsed"]["action"] == "CLOSE_PARTIAL"


def record(sid, policy, net, status="simulated"):
    return {"signal_id": sid, "policy_id": policy, "profile": "reference", "received_utc": "2026-06-05T10:00:00Z",
            "status": status, "net_after_cost_eur": net, "result": {"max_floating_drawdown_eur": "4"}}


def test_unknown_is_not_zero_and_abstention_trace_is_not_a_fill():
    result = aggregate([record("a", "r05_native", "5"), record("b", "r05_native", "0", "unfilled"),
                        record("c", "r05_native", None, "engine_blocked")])
    assert result["known"] == 2 and result["filled_signals"] == 1
    assert result["net_after_cost_eur"] == "5"
    assert result["unknown_ids"] == ["c"]
    assert aggregate([record("c", "dubai_current", None, "engine_blocked")])["net_after_cost_eur"] is None


def test_paired_summary_uses_common_known_ids_without_future_r05_filter():
    rows = [record("a", "dubai_current", "10"), record("b", "dubai_current", "-2"),
            record("c", "dubai_current", None, "engine_blocked"),
            record("a", "r05_native", "0", "unfilled"), record("b", "r05_native", "5"),
            record("c", "r05_native", "8")]
    result = summarize(rows)
    pair = result["paired"]["r05_native|reference"]
    assert pair["ids"] == ["a", "b"]
    assert pair["dubai_current"]["net_after_cost_eur"] == "8"
    assert pair["challenger"]["net_after_cost_eur"] == "5"
    assert result["selected_policy"] is None
