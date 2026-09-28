"""Regression boundaries for the offline shadow/native comparison."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import numpy as np
import pytest

from research.causal_comparison import SequenceEvent
from strategy_shadow_contracts import ShadowSignalState, canonical_hash
from tools.audit_native_week_risk_path import source_msc
from tools.audit_week_shadow_control_path import (controls_and_states, entry_price_effect_at_mark,
                                                  drawdown_context, first_virtual_fill,
                                                  load_binding_reports, mark_native,
                                                  pair_entry_fills, upgrade_pairing_with_journal_binding,
                                                  summarize_transition_lags, utc_ms,
                                                  verified_exposure_at_mark)
from tools.audit_native_money_anchor import digest


def fixture_at_mark(*, fx_age_ms=100, mark_offset_ms=1_000):
    entry = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
    exit_at = entry + timedelta(seconds=10)
    mark = source_msc(entry) + mark_offset_ms
    position = {"position_id": 7, "direction": "BUY", "entry_price": 100.6,
                "volume": 1.0, "entry_msc": source_msc(entry), "exit_msc": source_msc(exit_at)}
    events = {7: [
        SequenceEvent(7, "entry", entry, "BUY", Decimal("100.6"), Decimal("1"), Decimal("0"), "native"),
        SequenceEvent(7, "exit", exit_at, "BUY", Decimal("100.7"), Decimal("1"), Decimal("10"), "native"),
    ]}
    tick = {"time_msc": mark - 10_800_000, "bid": 100.5, "ask": 100.6}
    fx = (np.array([mark - fx_age_ms]), np.array([1.0]), np.array([1.0]))
    contract = {"instrument": {"contract_size": 100},
                "account": {"currency_digits": 2},
                "conversion": {"max_quote_age_ms": 5_000, "orientation": "account_base_profit_quote"}}
    return position, events, tick, fx, contract


def test_native_mark_values_open_position_on_exact_shadow_quote():
    position, events, tick, fx, contract = fixture_at_mark()
    row = mark_native([position], events, tick, fx, contract)
    assert row["status"] == "compared"
    assert row["native_open_volume"] == "1.0"
    assert row["native_realized_eur"] == "0"
    assert row["native_floating_eur"] == "-10.00"
    assert row["native_total_eur"] == "-10.00"


def test_same_millisecond_native_deal_does_not_invent_ordering():
    position, events, tick, fx, contract = fixture_at_mark(mark_offset_ms=0)
    row = mark_native([position], events, tick, fx, contract)
    assert row == {"status": "same_millisecond_native_deal_order_unknown"}


def test_stale_fx_blocks_money_but_preserves_exposure():
    position, events, tick, fx, contract = fixture_at_mark(fx_age_ms=5_001)
    row = mark_native([position], events, tick, fx, contract)
    assert row["status"] == "stale_prior_fx"
    assert row["native_open_volume"] == "1.0"
    assert "native_floating_eur" not in row


def test_no_virtual_fill_is_not_assumed_from_registration():
    assert first_virtual_fill([{"ev": "strategy_shadow_registered"}]) is None
    fill = {"ev": "strategy_shadow_transition", "transition": "virtual_fill"}
    assert first_virtual_fill([{"ev": "strategy_shadow_transition", "transition": "adverse_move_armed"}, fill]) is fill


def test_transition_lag_summary_keeps_price_exit_separate_from_stale_terminal_event():
    rows = [
        {"transition": "virtual_fill", "lag_ms": 1000, "event_id": "fill"},
        {"transition": "virtual_position_closed", "lag_ms": 4484, "event_id": "close"},
        {"transition": "shadow_signal_closed", "lag_ms": 459983, "event_id": "terminal"},
    ]
    result = summarize_transition_lags(rows)
    assert result["virtual_position_closed"]["max_lag_ms"] == 4484
    assert result["virtual_position_closed"]["over_5s_count"] == 0
    assert result["shadow_signal_closed"]["max_lag_ms"] == 459983
    assert result["shadow_signal_closed"]["over_60s_count"] == 1
    assert result["shadow_signal_closed"]["max_event_id"] == "terminal"
    with pytest.raises(ValueError, match="negative transition lag"):
        summarize_transition_lags([{"transition": "virtual_fill", "lag_ms": -1,
                                    "event_id": "bad"}])


def test_shadow_control_hash_chain_is_required():
    policy = {"channel": "canal2", "candidate_id": "gold_now_555_v1", "role": "live_control",
              "strategy_fingerprint": "a" * 64, "execution_fingerprint": "b" * 64}
    manifest = {"schema_version": 1, "policies": [policy]}
    manifest["manifest_hash"] = canonical_hash(manifest)
    state = ShadowSignalState.new(signal_id="canal2_1", source_message_id=1,
                                  candidate_id=policy["candidate_id"], channel="canal2",
                                  direction="BUY", registered_at_utc="2026-09-15T00:00:00+00:00",
                                  registered_tick_msc=None,
                                  strategy_fingerprint=policy["strategy_fingerprint"],
                                  execution_fingerprint=policy["execution_fingerprint"])
    start = {"ev": "strategy_shadow_runtime_started", "controls": {"canal2": policy["candidate_id"]},
             "catalog_manifest": manifest}
    registered = {"ev": "strategy_shadow_registered", "sig": "canal2_1", "channel": "canal2",
                  "candidate_id": policy["candidate_id"], "state": state.to_dict(),
                  "state_hash": state.state_hash, "previous_state_hash": None}
    checkpoint = {**registered, "ev": "strategy_shadow_checkpoint", "previous_state_hash": state.state_hash}
    assert controls_and_states([start, registered, checkpoint])[2] == {("canal2_1", "gold_now_555_v1")}
    with pytest.raises(ValueError, match="chain mismatch"):
        controls_and_states([start, registered, {**checkpoint, "previous_state_hash": "wrong"}])
    with pytest.raises(ValueError, match="state or policy hash mismatch"):
        controls_and_states([start, {**registered, "state_hash": "wrong"}])


def test_emission_clock_is_distinct_from_historical_tick_clock():
    assert utc_ms("2026-09-17T12:31:08.661+00:00") - utc_ms("2026-09-17T12:30:00.192+00:00") == 68_469
    with pytest.raises(ValueError, match="explicit UTC"):
        utc_ms("2026-09-17T14:31:08.661+02:00")


def test_entry_pairing_retains_price_and_time_differences_without_certifying_identity():
    base = 1_789_729_540_000
    native = [
        {"position_id": 1, "direction": "BUY", "entry_msc": base + 10_800_000 + 1375,
         "entry_price": 4381.79, "volume": 0.04},
        {"position_id": 2, "direction": "BUY", "entry_msc": base + 10_800_000 + 4921,
         "entry_price": 4380.17, "volume": 0.03},
    ]
    fills = [
        {"ev": "strategy_shadow_transition", "transition": "virtual_fill",
         "transition_tick_msc": base, "transition_details": {
             "leg_index": 0, "volume": 0.04, "entry_price": 4381.35},
         "state": {"direction": "BUY"}},
        {"ev": "strategy_shadow_transition", "transition": "virtual_fill",
         "transition_tick_msc": base + 5981, "transition_details": {
             "leg_index": 1, "volume": 0.03, "entry_price": 4379.84},
         "state": {"direction": "BUY"}},
    ]
    result = pair_entry_fills(fills, native, contract_size=100, direct_clock=True)
    assert result["status"] == "diagnostic_ordinal_pairing"
    assert result["full_entry_identity_verified"] is False
    assert result["pairs"][0]["native_minus_shadow_ms"] == 1375
    assert result["pairs"][1]["native_minus_shadow_ms"] == -1060
    assert result["cumulative_shadow_minus_native_entry_effect_quote"] == "2.7500"
    proof = {"status": "verified_live_leg_journal_binding",
             "full_entry_identity_verified": True,
             "rows": [{"leg_index": 0, "native_position_id": 1,
                       "entry_price": "4381.79", "volume": "0.04"},
                      {"leg_index": 1, "native_position_id": 2,
                       "entry_price": "4380.17", "volume": "0.03"}]}
    upgraded = upgrade_pairing_with_journal_binding(result, proof, "evidence_hash")
    assert upgraded["status"] == "verified_journal_entry_binding"
    assert upgraded["full_entry_identity_verified"] is True
    assert upgraded["binding_report_sha256"] == "evidence_hash"
    proof["rows"][1]["native_position_id"] = 999
    with pytest.raises(ValueError, match="contradicts"):
        upgrade_pairing_with_journal_binding(result, proof, "evidence_hash")
    assert pair_entry_fills(fills, native, contract_size=100, direct_clock=False)["status"] == "blocked_clock"
    native[1]["volume"] = 0.04
    assert pair_entry_fills(fills, native, contract_size=100, direct_clock=True)["status"] == "blocked_leg_volume"
    native[1]["volume"] = 0.03
    native[1]["position_id"] = 1
    assert pair_entry_fills(fills, native, contract_size=100, direct_clock=True)["status"] == "blocked_entry_facts"


def test_entry_effect_uses_common_native_quote_fx_and_blocks_exposure_mismatch():
    position, events, tick, fx, contract = fixture_at_mark()
    native = mark_native([position], events, tick, fx, contract)
    pairs = {0: {"native_position_id": 7, "shadow_entry_price": 100.0}}
    state = {"positions": [{"leg_index": 0, "entry_price": 100.0, "status": "open"}],
             "realized_eur": 0}
    effect = entry_price_effect_at_mark([position], events, state, tick, fx, contract, native, pairs)
    assert effect == {"status": "attributed", "entry_price_only_model_delta_eur": "60.00"}
    state["positions"][0]["status"] = "closed"
    assert entry_price_effect_at_mark([position], events, state, tick, fx, contract,
                                      native, pairs)["status"] == "blocked_closed_leg_identity"
    state["positions"][0]["status"] = "open"
    state["positions"][0]["leg_index"] = 1
    assert entry_price_effect_at_mark([position], events, state, tick, fx, contract,
                                      native, pairs)["status"] == "blocked_exposure"


def test_entry_effect_after_partial_close_requires_same_closed_leg_and_realized_money():
    first, events, tick, fx, contract = fixture_at_mark(mark_offset_ms=5_000)
    first["exit_msc"] = first["entry_msc"] + 2_000
    events[7][1] = SequenceEvent(7, "exit", datetime(2026, 9, 15, 12, 0, 2,
                                                     tzinfo=timezone.utc), "BUY",
                                 Decimal("100.7"), Decimal("1"), Decimal("10"), "native")
    second = {**first, "position_id": 8, "entry_price": 100.6,
              "exit_msc": first["entry_msc"] + 10_000}
    events[8] = [SequenceEvent(8, "entry", datetime(2026, 9, 15, 12,
                                                   tzinfo=timezone.utc), "BUY",
                               Decimal("100.6"), Decimal("1"), Decimal("0"), "native"),
                 SequenceEvent(8, "exit", datetime(2026, 9, 15, 12, 0, 10,
                                                  tzinfo=timezone.utc), "BUY",
                               Decimal("100.7"), Decimal("1"), Decimal("10"), "native")]
    positions = [first, second]
    native = mark_native(positions, events, tick, fx, contract)
    assert native["native_realized_eur"] == "10"
    pairs = {0: {"native_position_id": 7, "shadow_entry_price": 100.0},
             1: {"native_position_id": 8, "shadow_entry_price": 100.0}}
    state = {"positions": [{"leg_index": 0, "entry_price": 100.0, "status": "closed"},
                           {"leg_index": 1, "entry_price": 100.0, "status": "open"}],
             "realized_eur": 10}
    assert entry_price_effect_at_mark(positions, events, state, tick, fx, contract,
                                      native, pairs) == {
        "status": "attributed", "entry_price_only_model_delta_eur": "60.00"}
    state["realized_eur"] = 9.99
    assert entry_price_effect_at_mark(positions, events, state, tick, fx, contract,
                                      native, pairs)["status"] == "blocked_realized_mismatch"
    state["realized_eur"] = 10
    state["positions"][0]["leg_index"] = 1
    assert entry_price_effect_at_mark(positions, events, state, tick, fx, contract,
                                      native, pairs)["status"] == "blocked_closed_leg_identity"


def test_verified_binding_report_rejects_changed_shared_source(tmp_path):
    source = tmp_path / "native.json"
    source.write_text("{}", encoding="utf-8")
    report_path = tmp_path / "binding.json"
    report_path.write_text(json.dumps({
        "contract": "shadow_live_protection_level_diagnostic_v1",
        "status": "diagnostic_only", "signal_id": "canal2_1",
        "control_manifest_hash": "manifest", "native_clock_direct": True,
        "live_leg_binding": {"status": "verified_live_leg_journal_binding",
                             "full_entry_identity_verified": True, "rows": []},
        "inputs_sha256": {str(source): digest(source)},
    }), encoding="utf-8")
    loaded = load_binding_reports([report_path], [source], "manifest")
    assert loaded["canal2_1"]["inputs_sha256"] == {str(source): digest(source)}
    source.write_text("{\"changed\": true}", encoding="utf-8")
    with pytest.raises(ValueError, match="source hash mismatch"):
        load_binding_reports([report_path], [source], "manifest")


def test_verified_exposure_intervals_distinguish_entry_and_exit_timing():
    base = 1_789_729_540_000
    positions = [{"position_id": 7, "entry_msc": base + 10_800_100,
                  "exit_msc": base + 10_800_500, "volume": 0.04}]
    pairs = {0: {"native_position_id": 7}}
    final = [{"leg_index": 0, "volume": 0.04,
              "opened_tick_msc": base, "closed_tick_msc": base + 400,
              "status": "closed"}]
    opened = {"positions": [{"leg_index": 0, "status": "open"}]}
    closed = {"positions": [{"leg_index": 0, "status": "closed"}]}
    first = verified_exposure_at_mark(positions, opened, final, pairs, base + 50)
    assert first["mismatches"][0]["reason"] == "virtual_entry_precedes_native"
    assert first["volume_delta_from_legs"] == "0.04"
    assert verified_exposure_at_mark(positions, opened, final, pairs, base + 200)["status"] == "same_open_legs"
    last = verified_exposure_at_mark(positions, closed, final, pairs, base + 450)
    assert last["mismatches"][0]["reason"] == "virtual_exit_precedes_native"
    assert last["volume_delta_from_legs"] == "-0.04"
    positions[0]["entry_msc"] = base + 10_800_000 - 100
    early_native = verified_exposure_at_mark(positions, {"positions": []}, final,
                                             pairs, base - 50)
    assert early_native["mismatches"][0]["reason"] == "native_entry_precedes_virtual"
    positions[0]["exit_msc"] = base + 10_800_300
    early_exit = verified_exposure_at_mark(positions, opened, final, pairs, base + 350)
    assert early_exit["mismatches"][0]["reason"] == "native_exit_precedes_virtual"
    positions[0]["volume"] = 0.05
    with pytest.raises(ValueError, match="volume mismatch"):
        verified_exposure_at_mark(positions, opened, final, pairs, base + 200)
    positions[0]["volume"] = 0.04
    with pytest.raises(ValueError, match="chronology"):
        verified_exposure_at_mark(positions, opened, final, pairs, base + 450)


def test_drawdown_context_keeps_prior_peak_and_entry_effect_at_trough():
    marks = [
        {"utc_msc": 1, "shadow_realized_eur": "0", "shadow_floating_eur": "0.77",
         "native_realized_eur": "0", "native_floating_eur": "0"},
        {"utc_msc": 2, "shadow_realized_eur": "0", "shadow_floating_eur": "-17.23",
         "native_realized_eur": "0", "native_floating_eur": "-21.11",
         "entry_price_only_model_delta_eur": "3.88"},
    ]
    shadow = drawdown_context(marks, "shadow_realized_eur", "shadow_floating_eur")
    native = drawdown_context(marks, "native_realized_eur", "native_floating_eur")
    assert shadow == {"max_drawdown_eur": "18.00", "peak_eur": "0.77",
                      "peak_at_utc_msc": 1, "trough_eur": "-17.23",
                      "trough_at_utc_msc": 2, "entry_price_effect_at_trough_eur": "3.88"}
    assert native["max_drawdown_eur"] == "21.11"
    assert native["peak_eur"] == "0"
    assert native["peak_at_utc_msc"] is None
    assert drawdown_context([], "x", "y") is None
