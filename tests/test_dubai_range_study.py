from copy import deepcopy
from decimal import Decimal

import pytest

from research import dubai_range_study as study


def signal(direction="BUY", kind="range"):
    return {"signal_id": "s1", "received_utc": "2026-06-05T12:00:00Z",
        "initial_receipt_supported": True, "direction": direction,
        "first_priced": {"kind": "operational_" + kind,
            "parsed": {"range": [100., 104.] if kind == "range" else None,
                "entry_price": 102., "sl": 90. if direction == "BUY" else 114.,
                "tps": [110., 120.] if direction == "BUY" else [94., 84.]}}}


def rule(entry="zone", exit_mode="tp1", kind="range"):
    return {"kind": kind, "entry": entry, "exit": exit_mode, "hold_minutes": 60}


def test_frozen_policy_matrix_has_24_unique_rules():
    rules = study.policies()
    assert len(rules) == len({r["id"] for r in rules}) == 24
    assert sum(r["kind"] == "range" for r in rules) == 16
    assert {r["hold_minutes"] for r in rules} == {60, 240}


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("entry", ["on_levels", "zone", "midpoint", "ladder"])
@pytest.mark.parametrize("exit_mode", ["tp1", "split"])
def test_range_rules_preserve_absolute_levels_and_floor_risk(direction, entry, exit_mode):
    row = signal(direction)
    genome, nominal = study.build_genome(row, rule(entry, exit_mode), 103.)
    assert not genome.validation_errors()
    assert 0 < nominal <= 200
    assert genome.stop_value == row["first_priced"]["parsed"]["sl"]
    assert genome.be_mode == "none"
    assert genome.entry_expiry_min == 5 and genome.time_exit_min == 60
    assert all(Decimal(str(v)) % Decimal(".01") == 0 for v in genome.volume_weights)
    assert set(genome.target_steps) <= set(row["first_priced"]["parsed"]["tps"])
    if entry == "ladder":
        assert genome.volume_weights == (.08, .04, .03)
        assert nominal == 190.
        assert genome.entry_value == 100 and genome.entry_confirmation_value == 104
        assert genome.entry_ladder_mode == "range_levels"
        assert genome.target_steps[:2] == (110., 110.) if direction == "BUY" else genome.target_steps[:2] == (94., 94.)


def test_minimum_lot_is_blocked_not_rounded_up():
    row = signal()
    row["first_priced"]["parsed"]["sl"] = 1.
    with pytest.raises(ValueError, match="below_minimum_lot"):
        study.build_genome(row, rule("ladder"), 103.)


def test_missing_or_reversed_tp2_does_not_invalidate_tp1_or_get_repaired():
    row = signal()
    row["first_priced"]["parsed"]["tps"] = [110., 109.]
    study.build_genome(row, rule(), 103.)
    with pytest.raises(ValueError, match="second_target"):
        study.build_genome(row, rule(exit_mode="split"), 103.)


def test_price_only_uses_a_limit_not_a_synthetic_range():
    genome, _ = study.build_genome(signal(kind="price"), rule("limit", kind="single_price"), 103.)
    assert genome.entry_mode == "published_limit" and genome.entry_value == 102
    assert genome.entry_confirmation_value is None


def test_parity_sample_ignores_future_results_and_takes_first_per_stratum():
    first = signal()
    second = deepcopy(first)
    second["signal_id"] = "s2"
    third = signal("SELL")
    third["signal_id"] = "s3"
    assert study.sample_ids([first, second, third]) == ["s1", "s3"]


def record(sid, policy, status, net=None):
    return {"signal_id": sid, "policy_id": policy, "profile": "reference",
        "received_utc": "2026-06-05T12:00:00Z", "status": status,
        "net_after_cost_eur": net, "nominal_risk_usd": 190,
        "result": {"max_floating_drawdown_eur": "25"}}


def test_blocked_rows_are_unknown_not_zero_and_common_set_is_matched():
    rows = [record("a", "range:zone:tp1:60", "simulated", "20"),
        record("b", "range:zone:tp1:60", "data_blocked"),
        record("a", "range:ladder:tp1:60", "unfilled", "0"),
        record("b", "range:ladder:tp1:60", "simulated", "-30")]
    summary = study.summarize(rows)
    assert summary["matched_all_policies_and_horizons"]["range"]["signal_ids"] == ["a"]
    group = summary["groups"]["range:zone:tp1:60|reference"]
    assert group["known"] == 1 and group["rows"] == 2
    assert group["net_after_cost_eur"] == "20"
    assert study.stats([rows[1]])["net_after_cost_eur"] is None


def test_unfilled_has_no_open_equity_and_missing_filled_equity_stays_unknown():
    unfilled = record("a", "range:zone:tp1:60", "unfilled", "0")
    unfilled["result"]["max_floating_drawdown_eur"] = None
    assert study.stats([unfilled])["max_single_signal_floating_drawdown_eur"] == "0"
    filled = deepcopy(unfilled)
    filled["status"] = "simulated"
    assert study.stats([filled])["max_single_signal_floating_drawdown_eur"] is None
    assert study.stats([filled])["filled_signals_missing_floating_drawdown"] == 1


@pytest.mark.parametrize("changed", [None, "coverage", "genome", "result", "priced_source_sha256"])
def test_reusing_parity_requires_same_inputs_and_fresh_fast_result(changed):
    fields = ("signal_id", "received_utc", "priced_received_utc", "direction", "evidence_tier",
        "priced_source_sha256", "policy_id", "profile", "coverage", "genome", "strategy_fingerprint",
        "nominal_risk_usd", "status", "reasons", "result")
    current = dict.fromkeys(fields)
    current["result"] = {"blockers": [], "unfilled": True}
    prior = deepcopy(current)
    prior["parity"] = {"engines": {name: deepcopy(current["result"]) for name in ("fast", "scalar", "oracle")}}
    current["genome"] = {"volume_weights": (.01, .02)}
    prior["genome"] = {"volume_weights": [.01, .02]}
    if changed:
        prior[changed] = "changed"
        with pytest.raises(ValueError, match="changed"):
            study.retained_parity(current, prior)
    else:
        assert study.retained_parity(current, prior) == prior["parity"]["engines"]
