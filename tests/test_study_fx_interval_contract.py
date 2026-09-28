"""Synthetic FX contract parity, with no historical engine runs or searches."""

from dataclasses import replace
from datetime import timedelta
import json

import numpy as np
import pandas as pd
import pytest

from research import strategy_study as fixed
from research import strategy_study_dataset as bridge
from research.causal_replay import CausalSignal, make_path, time_ns
from research.dubai_iterative.portfolio import build_portfolio_tape
from tests.test_causal_replay import genome
from tests.test_strategy_study import BASE, case, digest, write_json


def direct_inputs(market_seconds=(0, 30, 60), fx_seconds=(0, 60), *, fx_bid=None):
    market_times = np.array([time_ns(BASE + timedelta(seconds=s)) for s in market_seconds])
    fx_times = np.array([time_ns(BASE + timedelta(seconds=s)) for s in fx_seconds])
    fx_bid = np.array(fx_bid if fx_bid is not None else [1.1] * len(fx_seconds))
    return dict(market=(market_times, np.full(len(market_times), 100.), np.full(len(market_times), 100.2)),
                conversion=(fx_times, fx_bid, fx_bid + .1),
                cutoff=BASE + timedelta(seconds=market_seconds[-1]),
                contract_size=100., currency_digits=2, max_fx_age_ms=5000,
                market_sha256="a" * 64, conversion_sha256="b" * 64)


def signal(direction="BUY"):
    return CausalSignal("canal2_1", "canal2", direction, BASE, BASE, "synthetic_revision")


def coverage(inputs, interval=None):
    config = {"max_market_gap_ms": 60000, "max_fx_age_ms": inputs["max_fx_age_ms"],
              "broker_clock": {"utc_offset_seconds": 0}, "money": {"rollover_hour_server": 0}}
    if interval is not None:
        config["max_fx_interval_ms"] = interval
    return fixed._coverage(signal(), inputs["cutoff"], inputs["market"], inputs["conversion"], config)


def sparse_config(case, *, opt_in, adverse=False, extra=()):
    quotes = [100., 100., *([94. if adverse else 103.] * 119)]
    path, out, payload = case(quotes=quotes, extra=extra)
    proof = payload["sources"]["conversion"][0]
    frame = pd.read_parquet(proof["path"]).iloc[[0, 60, 120]].copy()
    frame["bid"], frame["ask"] = [1.1, 8.1, 9.1], [1.2, 8.2, 9.2]
    frame.to_parquet(proof["path"])
    proof["sha256"] = digest(proof["path"])
    payload["max_fx_age_ms"] = 5000
    if opt_in:
        payload["max_fx_interval_ms"] = 60000
    write_json(path, payload)
    return path, out, payload


@pytest.mark.parametrize("age", [0, 5000, 60000])
def test_default_path_keeps_legacy_arrays_exactly_including_missing_prior_age(age):
    inputs = direct_inputs(market_seconds=(0, 1, 30, 60), fx_seconds=(1, 60), fx_bid=[1.1, 2.1])
    inputs["max_fx_age_ms"] = age
    path = make_path(signal(), genome(), **inputs)
    times, bids, asks = inputs["conversion"]
    indices = np.searchsorted(times, inputs["market"][0], side="right") - 1
    known, safe = indices >= 0, np.maximum(indices, 0)
    ages = (inputs["market"][0] - times[safe]) / 1_000_000
    expected = (np.where(known, bids[safe], np.nan), np.where(known, asks[safe], np.nan),
                ages, known & (ages >= 0) & (ages <= age))
    for actual, prior in zip((path.fx_bid, path.fx_ask, path.fx_age_ms, path.fx_valid), expected):
        np.testing.assert_array_equal(actual, prior)
    explicit = make_path(signal(), genome(), **inputs, max_fx_interval_ms=age)
    for name in ("fx_bid", "fx_ask", "fx_age_ms", "fx_valid"):
        np.testing.assert_array_equal(getattr(path, name), getattr(explicit, name))


def test_opt_in_uses_existing_interval_contract_in_path_and_coverage():
    inputs = direct_inputs()
    strict = make_path(signal(), genome(), **inputs)
    assert strict.fx_valid.tolist() == [True, False, True]
    assert coverage(inputs) == ["conversion_gap_or_stale"]
    interval = make_path(signal(), genome(), **inputs, max_fx_interval_ms=60000)
    assert interval.fx_valid.tolist() == [True, True, True]
    assert coverage(inputs, 60000) == []
    np.testing.assert_array_equal(interval.fx_bid, strict.fx_bid)
    np.testing.assert_array_equal(interval.fx_ask, strict.fx_ask)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_altering_future_price_cannot_change_prior_fx_bid_ask_or_validity(direction):
    before = make_path(signal(direction), genome(), **direct_inputs(fx_bid=[1.1, 2.1]), max_fx_interval_ms=60000)
    after = make_path(signal(direction), genome(), **direct_inputs(fx_bid=[1.1, 99.1]), max_fx_interval_ms=60000)
    np.testing.assert_array_equal(before.fx_bid[:2], after.fx_bid[:2])
    np.testing.assert_array_equal(before.fx_ask[:2], after.fx_ask[:2])
    np.testing.assert_array_equal(before.fx_valid, after.fx_valid)
    np.testing.assert_array_equal(before.fx_age_ms, after.fx_age_ms)
    assert before.fx_bid[2] != after.fx_bid[2]


@pytest.mark.parametrize("market_seconds,fx_seconds,expected", [
    ((0, 5, 6), (0,), [True, True, False]),
    ((0, 5, 30), (0, 60.001), [True, True, False]),
    ((0, 1, 30), (1, 60), [False, True, True]),
    ((0, 30, 60), (0, 0, 60), [True, True, True]),
    ((0, 30, 60), (0, 30, 30, 60), [True, True, True]),
])
def test_interval_edges_still_require_prior_quote_and_bounded_evidence(market_seconds, fx_seconds, expected):
    inputs = direct_inputs(market_seconds, fx_seconds, fx_bid=np.arange(len(fx_seconds)) + 1.1)
    path = make_path(signal(), genome(), **inputs, max_fx_interval_ms=60000)
    assert path.fx_valid.tolist() == expected
    assert ("conversion_gap_or_stale" in coverage(inputs, 60000)) == (not all(expected))
    indices = np.searchsorted(inputs["conversion"][0], path.times_ns, side="right") - 1
    for i, index in enumerate(indices):
        if index >= 0:
            assert path.fx_bid[i] == inputs["conversion"][1][index]
        else:
            assert np.isnan(path.fx_bid[i])


@pytest.mark.parametrize("interval", [True, False, "60000", 60000.0, -1, 0, 4999, 60001])
def test_path_rejects_invalid_interval_types_and_ranges(interval):
    with pytest.raises(ValueError, match="FX|fx|interval"):
        make_path(signal(), genome(), **direct_inputs(), max_fx_interval_ms=interval)


@pytest.mark.parametrize("interval", [True, False, "60000", 60000.0, None, -1, 0, 4999, 60001])
def test_config_rejects_invalid_interval_types_and_ranges(case, interval):
    config, _, payload = case()
    payload.update(max_fx_age_ms=5000, max_fx_interval_ms=interval)
    write_json(config, payload)
    with pytest.raises(ValueError, match="FX|fx|interval"):
        fixed.load_config(config)


def test_normalized_default_is_same_age_without_mutating_config_bytes(case):
    config, _, payload = case()
    before = config.read_bytes()
    normalized = fixed.load_config(config)
    assert normalized["max_fx_interval_ms"] == normalized["max_fx_age_ms"] == 1000
    assert "max_fx_interval_ms" not in payload
    assert config.read_bytes() == before


def test_default_sparse_fx_keeps_both_trigger_ids_blocked_and_no_engines_run(case):
    extra = [{"id": 2, "type": "message", "date_unixtime": str(int(BASE.timestamp())), "text": "SELL GOLD NOW"},
             {"id": 3, "type": "message", "date_unixtime": str(int(BASE.timestamp())), "text": "Commentary"}]
    config, out, _ = sparse_config(case, opt_in=False, extra=extra)
    bundle = bridge.load_study_dataset(config)
    result = fixed.run_study(config, out)
    assert result["engine_evaluations"] == 0
    assert result["denominator"] == {"all_archive_identities": 3, "selected_trigger_identities": 2,
                                    "status_counts": {"admission_blocked": 1, "data_blocked": 2}}
    assert not bundle.dataset.paths and len(bundle.dataset.eligible_signal_ids) == 2
    assert bundle.inventory["denominator"] == result["denominator"]
    assert bundle.max_fx_interval_ms == bundle.max_fx_age_ms == 5000


@pytest.mark.parametrize("adverse", [False, True])
def test_opt_in_runner_bundle_and_canonical_portfolio_share_contract(case, adverse):
    config, out, _ = sparse_config(case, opt_in=True, adverse=adverse)
    bundle = bridge.load_study_dataset(config)
    assert bundle.dataset.coverage_complete and len(bundle.dataset.paths) == 1
    assert bundle.max_fx_age_ms == 5000 and bundle.max_fx_interval_ms == 60000
    tape = build_portfolio_tape(bundle.dataset.paths, market_tick_source=bundle.market_tick_source,
        conversion_tick_source=bundle.conversion_tick_source, max_conversion_age_ms=bundle.max_fx_age_ms,
        max_conversion_interval_ms=bundle.max_fx_interval_ms)
    assert not tape.blockers and tape.fx_valid.all()
    assert tape.max_conversion_interval_ms == 60000
    path = bundle.dataset.paths[0]
    np.testing.assert_array_equal(tape.times_ns, path.times_ns)
    np.testing.assert_array_equal(tape.fx_valid, path.fx_valid)
    result = fixed.run_study(config, out)
    row = result["rows"][0]
    assert row["status"] == "simulated" and not any(row["mismatches"].values())
    assert result["portfolio"]["canonical_tape"] and not result["portfolio"]["blockers"]
    assert result["engine_evaluations"] == 3
    for engine in ("scalar", "fast", "oracle"):
        assert row["engines"][engine]["pnl_eur"] == result["portfolio"]["assessment"]["net_eur"]
    assert bundle.inventory["fx_validation"] == result["fx_validation"] == bundle.identity["fx_validation"]
    assert result["fx_validation"]["max_fx_age_ms"] == 5000
    assert result["fx_validation"]["max_fx_interval_ms"] == 60000
    assert result["fx_validation"]["mode"] == "historical_bracketed_prior_quote"
    assert result["fx_validation"]["status"] == "declared_hypothesis"
    for report in (result, bundle.inventory, bundle.identity):
        assert report["money_contract_verified"] is False
        assert report["account_currency_money_verified"] is False
    bundle.verify_sources()
    protocol = json.loads((out / "protocol.json").read_text())
    assert protocol["fx_validation"] == result["fx_validation"]


def test_opt_in_does_not_bypass_quote_clock_proof(case):
    config, _, payload = sparse_config(case, opt_in=True)
    proof = payload["sources"]["conversion"][0]
    frame = pd.read_parquet(proof["path"])
    frame.loc[frame.index[1], "source_time_msc"] += 1
    frame.to_parquet(proof["path"])
    proof["sha256"] = digest(proof["path"])
    write_json(config, payload)
    with pytest.raises(ValueError, match="clock mismatch"):
        bridge.load_study_dataset(config)


def test_bundle_cannot_silently_replace_its_fx_interval_contract(case):
    config, _, _ = sparse_config(case, opt_in=True)
    bundle = bridge.load_study_dataset(config)
    changed = replace(bundle, max_fx_interval_ms=5000)
    with pytest.raises(ValueError, match="contract|changed"):
        changed.verify_sources()
