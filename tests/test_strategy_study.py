"""Public M7 contracts using only synthetic exports and quote tapes."""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from research import strategy_study as study
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.protection_contract import ProtectionProfile
from research.telegram_export import prepare_exports, write_admission


BASE = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


@pytest.fixture
def case(tmp_path, monkeypatch):
    # Full-suite collection imports live tests in this interpreter. A fixed
    # study starts without those modules; monkeypatch restores them afterwards.
    for name in study.FORBIDDEN_IMPORTS:
        monkeypatch.delitem(sys.modules, name, raising=False)
    def build(direction="BUY", extra=(), quotes=None, changes=None, message_text=None):
        source = tmp_path / "export.json"
        message = {"id": 1, "type": "message", "date_unixtime": str(int(BASE.timestamp())),
                   "text": message_text or f"{direction} GOLD NOW"}
        write_json(source, {"id": 1642806869, "messages": [message, *extra]})
        admission = tmp_path / "admission"
        write_admission(prepare_exports([source], start=BASE, end=BASE + timedelta(minutes=10)), admission)
        times = pd.date_range(BASE, periods=121, freq="s")
        bids = np.array(quotes if quotes is not None else [100., 100., *([103.] * 119)])
        if direction == "SELL":
            bids = 200 - bids - .2
        market = tmp_path / "market.parquet"
        conversion = tmp_path / "fx.parquet"
        for path, bid, ask in ((market, bids, bids + .2),
                               (conversion, np.ones(121), np.ones(121))):
            pd.DataFrame({"time_utc": times, "source_time_msc": times.as_unit("ns").asi8 // 1_000_000,
                          "bid": bid, "ask": ask}).to_parquet(path)
        genome = StrategyGenome(schema_version=2, entry_mode="signal_market", entry_expiry_min=1,
            leg_count=1, volume_weights=(.01,), target_mode="per_leg_steps", target_steps=(1.,),
            stop_mode="fixed_move", stop_value=5., be_mode="none", time_exit_min=1,
            provider_management_mode="ignore")
        execution = ExecutionAssumptions(protection=ProtectionProfile(.01, 2, 0, 0, 0, 0, 0),
            market=MarketProfile(0, 0, 0, .01, 1., .01))
        config = {
            "schema_version": "own_rule_study_v1", "input_kind": "telegram_export",
            "data_use": "retrospective_development_only",
            "search_candidates": 0,
            "admission": {"path": str(admission), "sha256": digest(admission / "manifest.json")},
            "cohort": {"chat_id": 1642806869, "name": "dubai_historical"},
            "scenario": "publication_initial",
            "period": {"start_utc": BASE.isoformat(), "end_exclusive_utc": (BASE + timedelta(minutes=10)).isoformat()},
            "horizon_seconds": 120, "max_market_gap_ms": 1000, "max_fx_age_ms": 1000,
            "broker_clock": {"utc_offset_seconds": 0, "status": "declared_hypothesis"},
            "sources": {"market": [{"path": str(market), "sha256": digest(market), "symbol": "XAUUSD"}],
                        "conversion": [{"path": str(conversion), "sha256": digest(conversion), "symbol": "EURUSD"}]},
            "strategy": genome.to_dict(), "execution": asdict(execution),
            "money": {"mode": "hypothetical_intraday_zero_cost", "account_currency": "EUR",
                "profit_currency": "USD", "currency_digits": 2, "contract_size": 100,
                "conversion_symbol": "EURUSD", "conversion_orientation": "account_base_profit_quote",
                "commission_per_lot": 0, "swap_per_lot": 0, "rollover_hour_server": 0, "capital_eur": None},
            "budget": {"max_signals": 40, "max_evaluations": 120, "max_wall_seconds": 600, "max_quotes": 2_000_000},
        }
        if changes:
            changes(config)
        config_path = tmp_path / "config.json"
        write_json(config_path, config)
        return config_path, tmp_path / "run", config
    return build


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_known_outcome_three_engines_and_canonical_portfolio(case, direction):
    config, out, _ = case(direction)
    result = study.run_study(config, out)
    row = result["rows"][0]
    assert row["status"] == "simulated"
    assert row["mismatches"] == {"scalar": [], "fast": []}
    for engine in ("scalar", "fast", "oracle"):
        assert row["engines"][engine]["pnl_eur"] == "1.00"
        assert row["engines"][engine]["entries"][0]["entry_price"] == (100.2 if direction == "BUY" else 99.8)
        assert row["engines"][engine]["market_events"]
    assert result["portfolio"]["assessment"]["net_eur"] == "1.00"
    assert result["portfolio"]["canonical_tape"] is True
    assert result["status"] == "hypothetical_only_diagnostic"
    assert result["search_candidates"] == 0
    assert result["money_contract_verified"] is False
    assert result["account_currency_money_verified"] is False
    assert result["selection"]["selected_policy"] is None
    assert result["engine_evaluations"] == 3


def test_denominator_includes_nontriggers_other_cohorts_and_outside_period(case):
    config, out, payload = case(extra=[
        {"id": 2, "type": "message", "date_unixtime": str(int(BASE.timestamp())), "text": "BUY GOLD ZONE 99-100"},
        {"id": 3, "type": "message", "date_unixtime": str(int(BASE.timestamp()) - 1), "text": "SELL GOLD NOW"},
        {"id": 4, "type": "message", "text": "BUY GOLD NOW"},
    ])
    result = study.run_study(config, out)
    assert result["denominator"]["all_archive_identities"] == 4
    assert len(result["rows"]) == 4
    assert {row["message_id"]: row["status"] for row in result["rows"]} == {
        1: "simulated", 2: "admission_blocked", 3: "outside_universe", 4: "admission_blocked"}
    assert all(row["reasons"] for row in result["rows"][1:])


@pytest.mark.parametrize("hole", ["market", "conversion"])
def test_data_holes_preserve_id_without_fake_prices(case, hole):
    config, out, payload = case()
    proof = payload["sources"][hole][0]
    frame = pd.read_parquet(proof["path"]).drop(index=range(20, 100))
    frame.to_parquet(proof["path"])
    proof["sha256"] = digest(proof["path"])
    write_json(config, payload)
    result = study.run_study(config, out)
    assert result["rows"][0]["status"] == "data_blocked"
    assert result["rows"][0]["engines"] == {}
    assert result["engine_evaluations"] == 0
    assert result["portfolio"]["assessment"] is None


def test_idempotent_archive_and_tampered_results_fail_closed(case):
    config, out, payload = case()
    result = study.run_study(config, out)
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    assert study.run_study(config, out) == result
    assert before == {p.name: p.read_bytes() for p in out.iterdir()}
    assert study.verify_study(out)["status"] == "verified_current_hypothetical_archive"
    (out / "results.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash|immutable"):
        study.verify_study(out)
    with pytest.raises(ValueError, match="hash|immutable"):
        study.run_study(config, out)


def test_same_output_changed_config_and_mixed_files_are_rejected(case):
    config, out, payload = case()
    study.run_study(config, out)
    payload["strategy"]["target_steps"] = [2.]
    write_json(config, payload)
    with pytest.raises(ValueError, match="immutable|changed"):
        study.run_study(config, out)
    (out / "unrelated.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact|mixed"):
        study.verify_study(out)


def test_source_hash_mismatch_fails_before_publication(case):
    config, out, payload = case()
    payload["sources"]["market"][0]["sha256"] = "0" * 64
    write_json(config, payload)
    with pytest.raises(ValueError, match="hash"):
        study.run_study(config, out)
    assert not out.exists()


@pytest.mark.parametrize("field,value", [
    ("provider_management_mode", "exact"), ("entry_mode", "actual_mt5"),
    ("stop_mode", "provider"), ("be_mode", "provider"),
    ("target_mode", "provider_per_leg"), ("context_filter_mode", "min_reward_risk"),
])
def test_provider_or_observed_dependencies_never_enter_own_rules(case, field, value):
    config, out, payload = case()
    payload["strategy"][field] = value
    write_json(config, payload)
    with pytest.raises(ValueError, match="own.rule|genome|provider|observed"):
        study.run_study(config, out)
    assert not out.exists()


@pytest.mark.parametrize("field,value", [
    ("max_signals", 41), ("max_evaluations", 121), ("max_wall_seconds", 601),
    ("max_quotes", 4_000_001), ("max_signals", True),
])
def test_budget_limits_are_not_search_defaults(case, field, value):
    config, out, payload = case()
    payload["budget"][field] = value
    write_json(config, payload)
    with pytest.raises(ValueError, match="budget"):
        study.run_study(config, out)


@pytest.mark.parametrize("max_quotes", [2_000_000, 3_000_000, 4_000_000])
def test_explicit_quote_budget_validates_without_rewriting_config(case, max_quotes):
    config, _, payload = case()
    payload["budget"]["max_quotes"] = max_quotes
    write_json(config, payload)
    before = config.read_bytes()
    loaded = study.load_config(config)
    assert loaded["budget"] == {"max_signals": 40, "max_evaluations": 120,
                                "max_wall_seconds": 600, "max_quotes": max_quotes}
    assert config.read_bytes() == before


@pytest.mark.parametrize("max_quotes", [2_000_000, 3_000_000, 4_000_000])
def test_declared_quote_budget_plus_one_rejected_before_decode(case, monkeypatch, max_quotes):
    config, out, payload = case()
    payload["budget"]["max_quotes"] = max_quotes
    write_json(config, payload)
    before = config.read_bytes()
    row_counts = iter([max_quotes, 1])
    monkeypatch.setattr(study.pq, "read_metadata", lambda *_: SimpleNamespace(num_rows=next(row_counts)))
    def unexpected(*args, **kwargs):
        pytest.fail("Over-budget source rows must never be decoded or truncated")
    monkeypatch.setattr(study.pd, "read_parquet", unexpected)
    with pytest.raises(ValueError, match="quote budget exceeded before loading Parquet rows"):
        study.run_study(config, out)
    assert config.read_bytes() == before
    assert not out.exists()


@pytest.mark.parametrize("max_quotes", [True, 3_000_000.0, "3000000", 0, 4_000_001])
def test_quote_hard_maximum_requires_bounded_explicit_integer(case, max_quotes):
    config, out, payload = case()
    payload["budget"]["max_quotes"] = max_quotes
    write_json(config, payload)
    with pytest.raises(ValueError, match="budget max_quotes"):
        study.load_config(config)
    assert not out.exists()


def test_offline_import_has_no_live_or_search_modules():
    code = "import sys; import research.strategy_study; print(chr(10).join(sorted(sys.modules)))"
    completed = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    modules = completed.stdout.splitlines()
    assert not any(name in modules for name in ("MetaTrader5", "listener", "executor", "telethon",
        "research.dubai_iterative.search", "research.gold_iterative.search", "classifier"))


@pytest.mark.parametrize("module", [
    "MetaTrader5", "listener", "executor", "telethon", "classifier",
    "research.dubai_iterative.search", "research.gold_iterative.search",
])
def test_fixed_study_still_rejects_imports_inside_its_own_process(case, monkeypatch, module):
    config, output, _ = case()
    monkeypatch.setitem(sys.modules, module, object())
    with pytest.raises(ValueError, match="offline import boundary violated"):
        study.run_study(config, output)
    assert not output.exists()


@pytest.fixture
def control_case(case):
    def build():
        config, out, payload = case()
        source = config.parent / "control"
        source.mkdir()
        raw = {"ev": "telegram_raw", "channel": "canal2", "message_id": 1,
            "message_revision_id": "raw-1", "date_utc": BASE.isoformat(), "ts": BASE.isoformat(),
            "text": "BUY GOLD NOW", "is_edit": False, "edit_date_utc": None,
            "reply_to_msg_id": None, "sticker_id": None}
        rows = [raw, raw | {"message_id": 2, "message_revision_id": "raw-2", "text": "CLOSE ALL",
                            "ts": (BASE + timedelta(seconds=1)).isoformat(), "reply_to_msg_id": 1},
                raw | {"channel": "canal1", "message_id": 1, "message_revision_id": "raw-c1",
                       "text": "", "sticker_id": "known-buy"}]
        write_json(source / "raw_messages.json", rows)
        protocol = {"contract": "raw_message_control_diagnostic_v2",
            "raw_messages_sha256": digest(source / "raw_messages.json"),
            "start_utc": BASE.isoformat(), "cutoff_utc": (BASE + timedelta(minutes=10)).isoformat(),
            "expected_signal_ids": ["canal2_1", "canal1_1"], "sticker_directions": {"known-buy": "BUY"},
            "broker_epoch_offset_seconds": 0, "account_currency": "EUR", "contract_size": 100.,
            "currency_digits": 2, "search_candidate_budget": 0, "mass_search_authorized": False,
            "admission_blockers": ["retrospective_control_not_certified"],
            "tapes": {proof[0]["symbol"]: {key: proof[0][key] for key in ("path", "sha256")}
                      for proof in payload["sources"].values()}}
        write_json(source / "protocol.json", protocol)
        payload.pop("admission")
        payload.update(input_kind="causal_control", source_study={"path": str(source), "sha256": digest(source / "protocol.json")},
            cohort={"chat_id": 3908582492, "name": "gold_current_control", "channel": "canal2"},
            scenario="observed_raw_receipt")
        write_json(config, payload)
        return config, out, payload, source, protocol
    return build


def test_causal_control_keeps_identity_denominator_and_ignores_provider_management(control_case):
    config, out, payload, source, original = control_case()
    report = study.run_study(config, out)
    assert len(report["rows"]) == 3
    row = report["rows"][0]
    assert row["source_signal_id"] == "canal2_1"
    assert row["signal_id"].startswith("causal_control:")
    assert row["chat_id"] == 3908582492
    assert row["chat_identity_basis"] == "declared_channel_mapping_not_raw_chat_evidence"
    assert row["engines"]["scalar"]["pnl_eur"] == "1.00"
    assert row["trigger"]["received_utc"] == BASE.isoformat()
    assert row["trigger"]["clock_is_hypothesis"] is False
    assert report["source_admission_blockers"] == original["admission_blockers"]
    assert report["rows"][2]["status"] == "outside_universe"
    assert report["rows"][2]["chat_id"] is None
    assert report["engine_evaluations"] == 3
    assert study.verify_study(out)["status"] == "verified_current_hypothetical_archive"


@pytest.mark.parametrize("corruption", ["raw_hash", "expected_ids", "source_tape", "chat"])
def test_causal_control_source_contract_must_match(control_case, corruption):
    config, out, payload, source, protocol = control_case()
    if corruption == "raw_hash":
        protocol["raw_messages_sha256"] = "0" * 64
    elif corruption == "expected_ids":
        protocol["expected_signal_ids"] = ["canal2_1"]
    elif corruption == "source_tape":
        protocol["tapes"]["XAUUSD"]["sha256"] = "0" * 64
    else:
        payload["cohort"]["chat_id"] = 3828356530
    write_json(source / "protocol.json", protocol)
    payload["source_study"]["sha256"] = digest(source / "protocol.json")
    write_json(config, payload)
    with pytest.raises(ValueError):
        study.run_study(config, out)
    assert not out.exists()


@pytest.fixture
def sharded_control_case(control_case):
    def build():
        config, out, payload, source, protocol = control_case()
        for role, symbol in (("market", "XAUUSD"), ("conversion", "EURUSD")):
            original = payload["sources"][role][0]
            frame = pd.read_parquet(original["path"])
            shards = []
            for index, positions in enumerate((slice(0, 40), slice(40, 80), slice(80, None))):
                path = config.parent / f"{role}_{index}.parquet"
                frame.iloc[positions].to_parquet(path)
                shards.append({"path": str(path), "sha256": digest(path)})
            protocol["tapes"][symbol] = shards
            payload["sources"][role] = [proof | {"symbol": symbol} for proof in shards]
        write_json(source / "protocol.json", protocol)
        payload["source_study"]["sha256"] = digest(source / "protocol.json")
        write_json(config, payload)
        return config, out, payload, source, protocol
    return build


def test_three_shards_match_exactly_without_losing_quotes_or_raw_ids(sharded_control_case):
    config, out, payload, _, _ = sharded_control_case()
    report = study.run_study(config, out)
    assert report["quotes_loaded"] == 242
    assert len(report["rows"]) == 3
    assert report["engine_evaluations"] == 3
    assert report["rows"][0]["engines"]["scalar"]["pnl_eur"] == "1.00"
    assert payload["budget"]["max_quotes"] == 2_000_000


@pytest.mark.parametrize("corruption", ["missing", "extra", "order", "hash", "type", "empty", "too_many"])
def test_control_shard_proofs_require_full_ordered_equality(sharded_control_case, corruption, monkeypatch):
    config, out, payload, source, protocol = sharded_control_case()
    proofs = protocol["tapes"]["XAUUSD"]
    if corruption == "missing":
        proofs.pop()
    elif corruption == "extra":
        proofs.append(deepcopy(proofs[-1]))
    elif corruption == "order":
        proofs.reverse()
    elif corruption == "hash":
        proofs[1]["sha256"] = "0" * 64
    elif corruption == "type":
        proofs[1] = "not-a-proof"
    elif corruption == "empty":
        proofs.clear()
    else:
        proofs[:] = [deepcopy(proofs[0]) for _ in range(41)]
    write_json(source / "protocol.json", protocol)
    payload["source_study"]["sha256"] = digest(source / "protocol.json")
    write_json(config, payload)
    def unexpected(*args, **kwargs):
        pytest.fail("Mismatched protocol must fail before loading any quote source")
    monkeypatch.setattr(study, "_load_tapes", unexpected)
    with pytest.raises(ValueError, match="control source tape"):
        study.run_study(config, out)
    assert not out.exists()


@pytest.mark.parametrize("boundary", ["overlap", "reverse"])
def test_shard_order_and_overlap_still_fail_in_existing_tape_loader(sharded_control_case, boundary):
    config, out, payload, source, protocol = sharded_control_case()
    proofs = protocol["tapes"]["XAUUSD"]
    if boundary == "reverse":
        proofs.reverse()
    else:
        first = pd.read_parquet(proofs[0]["path"])
        second = pd.read_parquet(proofs[1]["path"])
        pd.concat([first.tail(1), second], ignore_index=True).to_parquet(proofs[1]["path"])
        proofs[1]["sha256"] = digest(proofs[1]["path"])
    payload["sources"]["market"] = [proof | {"symbol": "XAUUSD"} for proof in proofs]
    write_json(source / "protocol.json", protocol)
    payload["source_study"]["sha256"] = digest(source / "protocol.json")
    write_json(config, payload)
    with pytest.raises(ValueError, match="overlapping or unordered Parquet shards"):
        study.run_study(config, out)
    assert not out.exists()


def test_partial_ladder_fill_keeps_rejected_leg_and_known_money(case):
    config, out, payload = case(quotes=[100., 100., 100., *([103.] * 118)])
    payload["strategy"].update(leg_count=2, volume_weights=[.02, .01], target_steps=[1., 1.])
    payload["execution"]["market"]["volume_min"] = .02
    write_json(config, payload)
    report = study.run_study(config, out)
    row = report["rows"][0]
    for engine in row["engines"].values():
        assert engine["filled_volume"] == .02
        assert engine["pnl_eur"] == "2.00"
        assert any("reject" in event["kind"] for event in engine["market_events"])
    assert not any(row["mismatches"].values())


def test_unsupported_partial_policy_is_not_silently_sent_to_legacy(case):
    config, out, payload = case()
    payload["strategy"].update(target_mode="partial_runner", target_steps=[], target_value=1.,
                               partial_fraction=.5, runner_target=2., volume_weights=[.02])
    write_json(config, payload)
    report = study.run_study(config, out)
    row = report["rows"][0]
    assert row["status"] == "engine_blocked"
    assert "protection_policy_unsupported" in row["reasons"]
    assert report["portfolio"]["assessment"] is None
    assert report["engine_evaluations"] == 3


def test_source_mutation_inside_engine_fails_without_completion(case, monkeypatch):
    config, out, payload = case()
    original = study.simulate
    def mutate(*args, **kwargs):
        value = original(*args, **kwargs)
        Path(payload["sources"]["conversion"][0]["path"]).write_bytes(b"mutated source")
        return value
    monkeypatch.setattr(study, "simulate", mutate)
    with pytest.raises(ValueError, match="source changed"):
        study.run_study(config, out)
    assert not (out / "manifest.json").exists()


def test_current_identity_change_invalidates_archive_without_rewriting(case, monkeypatch):
    config, out, payload = case()
    study.run_study(config, out)
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    original = study.current_identity
    def changed():
        value = deepcopy(original())
        value["runner_sources"]["research/strategy_study.py"] = "0" * 64
        return value
    monkeypatch.setattr(study, "current_identity", changed)
    with pytest.raises(ValueError, match="changed"):
        study.verify_study(out)
    assert before == {p.name: p.read_bytes() for p in out.iterdir()}


def test_budget_keeps_entire_cohort_instead_of_first_n(case):
    config, out, payload = case(extra=[{"id": 2, "type": "message", "date_unixtime": str(int(BASE.timestamp())),
                                      "text": "SELL GOLD NOW"}])
    payload["budget"]["max_signals"] = 1
    write_json(config, payload)
    report = study.run_study(config, out)
    assert report["engine_evaluations"] == 0
    assert len(report["rows"]) == 2
    assert all(row["status"] == "budget_blocked" for row in report["rows"])


def test_cli_run_and_verify_are_offline_and_reproducible(case):
    config, out, payload = case()
    command = [sys.executable, str(ROOT / "tools/run_strategy_study.py")]
    completed = subprocess.run([*command, "run", "--config", str(config), "--output-dir", str(out)],
                               cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["search_candidates"] == 0
    verified = subprocess.run([*command, "verify", "--output-dir", str(out)], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)
    assert verified.returncode == 0, verified.stderr


def test_provider_sl_tp_are_not_own_rule_levels(case):
    config, out, payload = case(message_text="BUY GOLD NOW\nSL 120\nTP 80")
    report = study.run_study(config, out)
    assert report["rows"][0]["engines"]["scalar"]["pnl_eur"] == "1.00"


def test_canonical_portfolio_counts_overlapping_signals(case):
    config, out, payload = case(extra=[{"id": 2, "type": "message", "date_unixtime": str(int(BASE.timestamp())),
                                      "text": "BUY GOLD NOW"}])
    report = study.run_study(config, out)
    assessment = report["portfolio"]["assessment"]
    assert assessment["net_eur"] == "2.00"
    assert assessment["max_concurrent_signals"] == 2
    assert assessment["max_concurrent_volume"] == .02
    assert report["engine_evaluations"] == 6


def test_open_position_is_blocked_not_fabricated_closed(case):
    config, out, payload = case(quotes=[100.] * 121)
    report = study.run_study(config, out)
    row = report["rows"][0]
    assert row["status"] == "engine_blocked"
    for result in row["engines"].values():
        assert result["entries"]
        assert not result["exits"]
        assert result["pnl_eur"] is None
    assert report["portfolio"]["assessment"] is None


def test_no_entry_is_explicit_zero_participation_not_removed(case):
    config, out, payload = case()
    payload["strategy"]["entry_mode"] = "no_entry"
    write_json(config, payload)
    report = study.run_study(config, out)
    assert len(report["rows"]) == 1
    assert report["rows"][0]["status"] == "unfilled"
    assert report["rows"][0]["engines"]["scalar"]["pnl_eur"] == "0.00"


@pytest.mark.parametrize("field,value", [("account_currency", "USD"), ("currency_digits", 3),
    ("commission_per_lot", 1), ("swap_per_lot", 1), ("conversion_orientation", "identity")])
def test_unsupported_money_is_explicitly_rejected(case, field, value):
    config, out, payload = case()
    payload["money"][field] = value
    write_json(config, payload)
    with pytest.raises(ValueError, match="money"):
        study.run_study(config, out)
    assert not out.exists()


@pytest.mark.parametrize("value", ["2026-01-05T12:00:00", "2026-01-05T13:00:00+01:00"])
def test_period_requires_explicit_utc_not_local_time(case, value):
    config, out, payload = case()
    payload["period"]["start_utc"] = value
    write_json(config, payload)
    with pytest.raises(ValueError, match="explicit UTC"):
        study.run_study(config, out)


@pytest.mark.parametrize("corruption", ["duplicate", "nan", "unknown", "oos", "search"])
def test_config_cannot_hide_ambiguous_or_unapproved_options(case, corruption):
    config, out, payload = case()
    if corruption == "duplicate":
        config.write_text(config.read_text(encoding="utf-8").replace('"search_candidates": 0',
            '"search_candidates": 1, "search_candidates": 0'), encoding="utf-8")
    elif corruption == "nan":
        payload["strategy"]["stop_value"] = float("nan")
        write_json(config, payload)
    else:
        if corruption == "unknown":
            payload["observed_fills"] = [123]
        elif corruption == "oos":
            payload["data_use"] = "reserved_oos"
        else:
            payload["search_candidates"] = 1
        write_json(config, payload)
    with pytest.raises(ValueError):
        study.run_study(config, out)
    assert not out.exists()


def test_injected_initial_broker_protection_is_rejected(case):
    config, out, payload = case()
    payload["execution"]["protection"]["initial_protections"] = [
        {"ticket": "fake-mt5", "sl": 98., "tp": 102., "source": "observed"}]
    write_json(config, payload)
    with pytest.raises(ValueError, match="injection"):
        study.run_study(config, out)


def test_protocol_preserves_nested_current_execution_profile(case):
    config, out, payload = case()
    payload["execution"].update(entry_fill_latency_ms=500, latency_ms=250)
    payload["execution"]["market"].update(entry_acknowledgement_delay_ms=750)
    payload["execution"]["protection"].update(processing_delay_ms=750, acknowledgement_delay_ms=250)
    write_json(config, payload)
    report = study.run_study(config, out)
    assert json.loads((out / "protocol.json").read_text())["config"]["execution"] == json.loads(json.dumps(payload["execution"]))
    assert not any(report["rows"][0]["mismatches"].values())


def test_deadline_during_engine_never_publishes_complete_archive(case, monkeypatch):
    config, out, payload = case()
    clock = [0.]
    monkeypatch.setattr(study.time, "monotonic", lambda: clock[0])
    original = study.simulate
    def expire(*args, **kwargs):
        value = original(*args, **kwargs)
        clock[0] = 601.
        return value
    monkeypatch.setattr(study, "simulate", expire)
    with pytest.raises(TimeoutError, match="budget"):
        study.run_study(config, out)
    assert (out / "protocol.json").exists()
    assert not (out / "manifest.json").exists()
    with pytest.raises(ValueError, match="incomplete"):
        study.verify_study(out)


def test_quote_budget_is_checked_before_rows_are_loaded(case, monkeypatch):
    config, out, payload = case()
    payload["budget"]["max_quotes"] = 200
    write_json(config, payload)
    def unexpected(*args, **kwargs):
        pytest.fail("Parquet rows must not be loaded over budget")
    monkeypatch.setattr(study.pd, "read_parquet", unexpected)
    with pytest.raises(ValueError, match="quote budget"):
        study.run_study(config, out)
    assert not out.exists()


def test_overnight_horizon_is_outside_zero_swap_hypothesis(case):
    config, out, payload = case()
    payload["broker_clock"]["utc_offset_seconds"] = -15 * 60
    payload["money"]["rollover_hour_server"] = 12
    payload["horizon_seconds"] = 1800
    for proofs in payload["sources"].values():
        proof = proofs[0]
        frame = pd.read_parquet(proof["path"])
        frame["source_time_msc"] -= 15 * 60 * 1000
        frame.to_parquet(proof["path"])
        proof["sha256"] = digest(proof["path"])
    write_json(config, payload)
    report = study.run_study(config, out)
    assert "overnight_outside_money_universe" in report["rows"][0]["reasons"]
    assert report["engine_evaluations"] == 0


def test_missing_engine_field_is_a_disagreement(case, monkeypatch):
    config, out, payload = case()
    original = study.asdict
    def missing(value):
        result = original(value)
        if hasattr(value, "confidence_layer"):
            if not hasattr(missing, "seen"):
                missing.seen = True
                result.pop("behavior_digest")
                result.pop("pnl_eur")
        return result
    monkeypatch.setattr(study, "asdict", missing)
    report = study.run_study(config, out)
    row = report["rows"][0]
    assert row["status"] == "engine_disagreement"
    assert row["mismatches"]["scalar"] == ["pnl_eur"]
    assert report["portfolio"]["assessment"] is None
