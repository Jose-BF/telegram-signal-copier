from dataclasses import fields
from datetime import timedelta
import json
import subprocess
import sys

import pandas as pd
import pytest

from tools import prepare_simulator_forward as forward
from tools import run_protection_controls as controls


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(forward, "ROOT", tmp_path)
    monkeypatch.setattr(controls, "ROOT", tmp_path)
    # Other test modules import live code during collection; the real guard is tested in a clean process.
    monkeypatch.setattr(controls, "FORBIDDEN_IMPORTS", {"forward_forbidden_fixture"})
    monkeypatch.setattr(forward, "_now", lambda: forward.START - timedelta(minutes=15))
    market_sources = dict.fromkeys(forward.MARKET_SOURCES, "b" * 64)
    implementation = {"controls": {"iterative": {"sha256": "a" * 64, "source_sha256": market_sources}},
                      "forward_sources": {"tool": "b" * 64, "tools/prepare_simulator_forward.py": "b" * 64,
                                          **market_sources}}
    monkeypatch.setattr(forward, "identity", lambda: implementation)
    verification = tmp_path / "verification.json"
    sha = controls.save(verification, {
        "status": "local_suite_verified", "implementation_sha256": "a" * 64,
        "exit_code": 0, "same_implementation": True, "changed_sources": [], "changed_wrappers": [],
        "suite": {"tests": 7, "failures": 0, "errors": 0, "skipped": 0},
        "completed_at_utc": (forward.START - timedelta(hours=1)).isoformat(),
    })
    profile = tmp_path / "profile.json"
    producer = tmp_path / "source_only_adapter.py"
    producer.write_text("# Synthetic source-only producer identity fixture.\n")
    runner = tmp_path / forward.RUNNER_SOURCE
    runner.parent.mkdir()
    runner.write_text("# Synthetic runner identity fixture.\n")
    controls.save(profile, {
        "contract": "simulator_forward_profile_v1",
        "execution": {"latency_ms": 0, "entry_fill_latency_ms": 0,
                      "protection": dict(controls.PROFILE_SPECS[0]),
                      "market": {"entry_acknowledgement_delay_ms": 250,
                                 "close_processing_delay_ms": 1000,
                                 "close_acknowledgement_delay_ms": 250,
                                 "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01}},
        "capabilities": dict.fromkeys(forward.CAPABILITIES, "verified"),
        "verification": {"path": str(verification), "sha256": sha},
        "additional_sources": [{"path": str(producer), "sha256": controls.digest(producer)},
                               {"path": str(runner), "sha256": controls.digest(runner)}],
    })
    return profile, tmp_path / "frozen", implementation


def freeze(setup):
    profile, out, _ = setup
    result = forward.prepare(profile, out=out)
    assert result["status"] == "frozen_waiting_for_cohort"
    return out / "protocol.json", result["protocol_sha256"]


def dataset(tmp_path, protocol_path, protocol_sha, *, cutoff=None, count=2):
    protocol = controls.read(protocol_path)
    start = forward.utc(protocol["start_utc"])
    cutoff = cutoff or forward.utc(protocol["end_utc"])
    out = tmp_path / "dataset"
    out.mkdir()
    messages = [{
        "ev": "telegram_raw", "channel": "canal2", "message_id": index + 1,
        "message_revision_id": f"revision-{index}", "date_utc": start.isoformat(),
        "ts": start.isoformat(), "text": "BUY GOLD NOW",
    } for index in range(count)]
    messages_sha = controls.save(out / "raw_messages.json", messages)
    tapes = {}
    for symbol, bid, ask in (("XAUUSD", 100.0, 100.2), ("EURUSD", 1.1, 1.11)):
        path = out / f"{symbol}.parquet"
        stamps = pd.to_datetime([start, cutoff], utc=True)
        source_ms = stamps.astype("datetime64[ms, UTC]").astype("int64") + 10800000
        pd.DataFrame({"time_utc": stamps, "source_time_msc": source_ms,
                      "bid": [bid, bid], "ask": [ask, ask]}).to_parquet(path)
        tapes[symbol] = {"path": str(path), "sha256": controls.digest(path)}
    execution = protocol["execution"]
    source_evidence = {}
    for kind, value in {
        "clock": {"contract": "simulator_forward_clock_evidence_v1", "captured_at_utc": cutoff,
                  "clock_domain": "UTC", "broker_epoch_offset_seconds": 10800,
                  "monotonic_order_verified": True},
        "metadata": {"contract": "simulator_forward_metadata_evidence_v1", "captured_at_utc": cutoff,
                     "account_currency": "EUR", "symbols": {
                         "XAUUSD": {"contract_size": 100.,
                                    **{name: execution["protection"][name] for name in (
                                        "point", "digits", "stops_level_points", "freeze_level_points")},
                                    **{name: execution["market"][name] for name in (
                                        "volume_min", "volume_max", "volume_step")}},
                         "EURUSD": {"point": .00001, "digits": 5}}},
    }.items():
        evidence_path = out / f"{kind}_evidence.json"
        evidence_sha = controls.save(evidence_path, value)
        source_evidence[kind] = {"path": str(evidence_path), "sha256": evidence_sha,
                                 "source_path": str(evidence_path), "source_sha256": evidence_sha}
    payload = {
        "forward_protocol_sha256": protocol_sha, "universe": "independent",
        "search_candidate_budget": 0, "mass_search_authorized": False,
        "execution": protocol["execution"], "genome_fingerprint": protocol["genome_fingerprint"],
        "producer": protocol["profile_payload"]["additional_sources"][0],
        "start_utc": start.isoformat(), "cutoff_utc": cutoff.isoformat(),
        "contract_size": 100.0, "currency_digits": 2, "fx_max_age_ms": 5000,
        "broker_epoch_offset_seconds": 10800, "raw_messages_sha256": messages_sha,
        "expected_signal_ids": [f"canal2_{index + 1}" for index in range(count)], "tapes": tapes,
        "source_evidence": source_evidence,
    }
    controls.save(out / "protocol.json", payload)
    return out


def test_freeze_roundtrip_is_immutable_and_preserves_policy(setup):
    profile, out, _ = setup
    assert forward.prepare(profile)["status"] == "ready_to_freeze"
    path, _ = freeze(setup)
    protocol = controls.read(path)
    assert protocol["contract"] == "simulator_forward_protocol_v1"
    assert forward.utc(protocol["start_utc"]) == forward.START
    assert forward.utc(protocol["end_utc"]) == forward.END
    assert protocol["genome"]["profit_lock_arm"] == 30
    assert protocol["search_candidate_budget"] == 0
    assert protocol["full_live_parity_verified"] is False
    assert protocol["engine_state"] == "admitted_for_bounded_control"
    assert protocol["execution"]["market"]["entry_acknowledgement_delay_ms"] == 250
    before = path.read_bytes()
    with pytest.raises(ValueError, match="never overwrite"):
        forward.prepare(profile, out=out)
    assert path.read_bytes() == before


@pytest.mark.parametrize("mode", ["ready", "freeze"])
def test_cli_explicit_window_requires_both_bounds(setup, capsys, mode):
    profile, out, _ = setup
    args = [mode, "--profile", str(profile), "--start-utc", "2026-09-10T13:00:00Z"]
    if mode == "freeze":
        args.extend(["--out", str(out)])
    assert forward.main(args) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked"
    assert "together" in report["blockers"][0]
    assert not out.exists()


@pytest.mark.parametrize("mode", ["ready", "freeze"])
def test_cli_explicit_window_is_not_a_global_date_edit(setup, capsys, mode):
    profile, out, _ = setup
    args = [mode, "--profile", str(profile), "--start-utc", "2026-09-10T13:00:00Z",
            "--end-utc", "2026-09-10T15:00:00+00:00"]
    if mode == "freeze":
        args.extend(["--out", str(out)])
    assert forward.main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert forward.utc(report["start_utc"]) == forward.utc("2026-09-10T13:00:00Z")
    assert forward.utc(report["end_utc"]) == forward.utc("2026-09-10T15:00:00Z")
    assert out.exists() is (mode == "freeze")
    assert forward.START == forward.utc("2026-09-09T06:30:00Z")


@pytest.mark.parametrize("capability", forward.CAPABILITIES)
def test_blocked_capability_cannot_publish_a_frozen_protocol(setup, capability):
    profile, out, _ = setup
    value = controls.read(profile)
    value["capabilities"][capability] = "blocked"
    profile.write_text(json.dumps(value))
    result = forward.prepare(profile, out=out)
    assert result["status"] == "blocked"
    assert result["engine_state"] == "engine_incomplete"
    assert f"capability_unverified:{capability}" in result["blockers"]
    assert not out.exists()


def test_late_freeze_is_not_retrospectively_called_forward(setup):
    profile, out, _ = setup
    result = forward.prepare(profile, out=out, now=forward.START)
    assert "forward_freeze_deadline_missed" in result["blockers"]
    assert not out.exists()


def test_ready_rejects_old_engine_verification(setup):
    profile, _, implementation = setup
    implementation["controls"]["iterative"]["sha256"] = "c" * 64
    result = forward.prepare(profile)
    assert "current_engine_verification_missing_or_failed" in result["blockers"]
    assert result["engine_state"] == "engine_incomplete"


def test_missing_market_profile_is_diagnostic_and_cannot_freeze(setup):
    profile, out, _ = setup
    value = controls.read(profile)
    value["execution"].pop("market")
    profile.write_text(json.dumps(value))
    result = forward.prepare(profile, out=out)
    assert result["status"] == "blocked"
    assert result["engine_state"] == "engine_incomplete"
    assert "market_profile_missing" in result["blockers"]
    assert not out.exists()


def test_engine_verification_identity_must_cover_market_sources(setup):
    profile, out, implementation = setup
    implementation["controls"]["iterative"]["source_sha256"].pop(forward.MARKET_SOURCES[0])
    report = forward.prepare(profile, out=out)
    assert report["engine_state"] == "engine_incomplete"
    assert "engine_verification_identity_missing_market_sources" in report["blockers"]
    assert not out.exists()


def test_freeze_rechecks_deadline_after_final_hashes(setup, monkeypatch):
    profile, out, _ = setup
    original = forward._finish
    checks = 0
    def finish(*args):
        nonlocal checks
        original(*args)
        checks += 1
        if checks == 2:
            monkeypatch.setattr(forward, "_now", lambda: forward.START)
    monkeypatch.setattr(forward, "_finish", finish)
    with pytest.raises(ValueError, match="deadline"):
        forward.prepare(profile, out=out)
    assert not out.exists()


def test_future_inputs_can_be_admitted_without_reading_outcomes(setup, tmp_path, monkeypatch):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    original = forward._read
    seen = []
    def guarded(path, watched):
        seen.append(str(path))
        assert not any(word in str(path) for word in ("ledger", "attempt", "comparison", "results"))
        return original(path, watched)
    monkeypatch.setattr(forward, "_read", guarded)
    result = forward.check(protocol, data, now=forward.END)
    assert result["status"] == "evidence_ready_for_review"
    assert result["denominator"] == 2
    assert result["full_live_parity_verified"] is False
    assert seen


def test_empty_partial_cohort_stays_incomplete(setup, tmp_path):
    protocol, sha = freeze(setup)
    cutoff = forward.START + timedelta(minutes=10)
    data = dataset(tmp_path, protocol, sha, cutoff=cutoff, count=0)
    result = forward.check(protocol, data, now=cutoff)
    assert result["status"] == "incomplete"
    assert result["denominator"] == 0
    assert set(result["incomplete"]) == {"observation_window_incomplete", "insufficient_natural_signals"}


def test_source_changes_invalidate_the_frozen_protocol(setup, tmp_path):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    setup[2]["forward_sources"]["tool"] = "changed"
    with pytest.raises(ValueError, match="frozen implementation"):
        forward.check(protocol, data, now=forward.END)


@pytest.mark.parametrize("change", ["past", "future", "observed", "missing_signal"])
def test_inadmissible_dataset_is_rejected(setup, tmp_path, change):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    value = controls.read(data / "protocol.json")
    if change == "past":
        value["start_utc"] = "2026-09-08T06:30:00Z"
    elif change == "future":
        value["cutoff_utc"] = (forward.END + timedelta(seconds=1)).isoformat()
    elif change == "observed":
        value["openings_sha256"] = "observed-openings"
    else:
        value["expected_signal_ids"] = ["canal2_1"]
    (data / "protocol.json").write_text(json.dumps(value))
    with pytest.raises(ValueError):
        forward.check(protocol, data, now=forward.END)


def test_independent_stage_rejects_observed_comparison_argument(setup, tmp_path):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    with pytest.raises(ValueError, match="stage boundary"):
        forward.check(protocol, data, stage="independent", comparison=tmp_path / "never-read.json")


def test_changed_bytes_during_check_are_not_admitted(setup, tmp_path, monkeypatch):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    original = controls.load_tape
    def mutate(*args):
        tape = original(*args)
        (data / "raw_messages.json").write_text("[]")
        return tape
    monkeypatch.setattr(controls, "load_tape", mutate)
    with pytest.raises(ValueError, match="changed during execution"):
        forward.check(protocol, data, now=forward.END)


def engine_results(tmp_path, protocol_path, data, monkeypatch, *, blocker=None, unfilled=False):
    from research.dubai_iterative.engine import SimulationResult
    from research.dubai_iterative.oracle import OracleResult
    protocol = controls.read(protocol_path)
    dataset_protocol = controls.read(data / "protocol.json")
    rows = []
    for signal_id in dataset_protocol["expected_signal_ids"]:
        row = {"signal_id": signal_id}
        for name, schema in (("scalar", SimulationResult), ("fast", SimulationResult), ("oracle", OracleResult)):
            record = dict.fromkeys((field.name for field in fields(schema)), None)
            record.update({
                "signal_id": signal_id, "strategy_fingerprint": protocol["genome_fingerprint"],
                "entries": [] if unfilled else [{"ticket": "sim_1", "source": "causal_signal_market"}],
                "exits": [] if unfilled else [{"ticket": "sim_1"}],
                "protection_events": [], "blockers": [blocker] if blocker else [],
            })
            row[name] = record
        rows.append(row)
    path = tmp_path / "engine_results.json"
    controls.save(path, {
        "contract": "simulator_forward_run_v1", "runner": forward._runner(protocol),
        "implementation": protocol["implementation"], "execution": protocol["execution"],
        "forward_protocol_sha256": controls.digest(protocol_path),
        "protocol_sha256": controls.digest(data / "protocol.json"),
        "universe": "independent", "search_candidates": 0, "mass_search_authorized": False,
        "engine_evaluations": len(rows) * 3, "results": rows,
    })
    monkeypatch.setattr(forward, "_simulate", lambda *args: rows)
    return path


def test_matching_engines_with_shared_blocker_do_not_pass(setup, tmp_path, monkeypatch):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    results = engine_results(tmp_path, protocol, data, monkeypatch, blocker="protection_market_close_latency_unmodeled")
    report = forward.check(protocol, data, stage="independent", results=results, now=forward.END)
    assert report["status"] == "blocked"
    assert report["denominator"] == 2
    assert len(report["blockers"]) == 6


def test_unfilled_signals_are_not_first_trade_validation(setup, tmp_path, monkeypatch):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    results = engine_results(tmp_path, protocol, data, monkeypatch, unfilled=True)
    report = forward.check(protocol, data, stage="independent", results=results, now=forward.END)
    assert report["status"] == "incomplete"
    assert "insufficient_completed_simulated_trades" in report["incomplete"]


def test_engine_disagreement_is_recomputed_from_records(setup, tmp_path, monkeypatch):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    results = engine_results(tmp_path, protocol, data, monkeypatch)
    value = controls.read(results)
    value["results"][0]["fast"]["exit_reason"] = "different"
    results.write_text(json.dumps(value))
    report = forward.check(protocol, data, stage="independent", results=results, now=forward.END)
    assert "canal2_1:engine_disagreement:fast" in report["blockers"]


def test_identically_truncated_engine_records_cannot_pass(setup, tmp_path, monkeypatch):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    results = engine_results(tmp_path, protocol, data, monkeypatch)
    value = controls.read(results)
    for engine in ("scalar", "fast", "oracle"):
        value["results"][0][engine].pop("protection_events")
    results.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="complete dataclass record"):
        forward.check(protocol, data, stage="independent", results=results, now=forward.END)


def test_exact_prices_do_not_bypass_missing_installation_evidence(setup, tmp_path, monkeypatch):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    results = engine_results(tmp_path, protocol, data, monkeypatch)
    comparison = tmp_path / "comparison.json"
    controls.save(comparison, {
        "forward_protocol_sha256": sha, "independent_results_sha256": controls.digest(results),
        "producer": controls.read(protocol)["profile_payload"]["additional_sources"][0],
        "search_candidates": 0, "mass_search_authorized": False,
        "rows": [{"signal_id": f"canal2_{i}", "status": "exact", "blockers": []} for i in (1, 2)],
    })
    report = forward.check(protocol, data, stage="observed", results=results,
                           comparison=comparison, now=forward.END)
    assert report["status"] == "blocked"
    assert "canal2_1:lifecycle_unverified:installed_protection" in report["blockers"]
    assert report["full_live_parity_verified"] is False


def test_unfrozen_producer_and_changed_producer_cannot_label_forward_data(setup, tmp_path):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    value = controls.read(data / "protocol.json")
    producer = tmp_path / "late_producer.py"
    producer.write_text("# Late source fixture.\n")
    value["producer"] = {"path": str(producer), "sha256": controls.digest(producer)}
    (data / "protocol.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="producer was not frozen"):
        forward.check(protocol, data, now=forward.END)
    original_producer = tmp_path / "source_only_adapter.py"
    original_producer.write_text("# Changed source fixture.\n")
    with pytest.raises(ValueError, match="evidence hash mismatch"):
        forward.check(protocol, data, now=forward.END)


def test_actual_offline_import_boundary_in_clean_process():
    script = """
import sys
import time
from tools import prepare_simulator_forward as forward
from research.dubai_iterative import engine, oracle
expected = forward.identity()
started = time.monotonic()
forward._finish({}, expected, started)
for name in forward.protection.FORBIDDEN_IMPORTS:
    sys.modules[name] = object()
    try:
        forward._finish({}, expected, started)
    except ValueError as exc:
        assert 'offline boundary' in str(exc), str(exc)
    else:
        raise AssertionError(name)
    finally:
        del sys.modules[name]
print('offline boundary verified')
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=forward.ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "offline boundary verified"


def test_cli_missing_evidence_preserves_engine_incomplete(setup, capsys):
    profile, _, _ = setup
    value = controls.read(profile)
    value.pop("verification")
    profile.write_text(json.dumps(value))
    assert forward.main(["ready", "--profile", str(profile)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["engine_state"] == "engine_incomplete"
    assert report["full_live_parity_verified"] is False


def test_cli_json_is_not_changed_by_process_global_print_wrapper(setup, capsys, monkeypatch):
    import builtins
    original = builtins.print
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: original("[clock]", *args, **kwargs))
    profile, _, _ = setup
    value = controls.read(profile)
    value.pop("verification")
    profile.write_text(json.dumps(value))
    assert forward.main(["ready", "--profile", str(profile)]) == 2
    assert json.loads(capsys.readouterr().out)["engine_state"] == "engine_incomplete"


def test_late_signal_without_quotes_retains_whole_denominator(setup, tmp_path):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    messages = controls.read(data / "raw_messages.json")
    messages[1].update(ts=(forward.END - timedelta(seconds=1)).isoformat(),
                       date_utc=(forward.END - timedelta(seconds=1)).isoformat())
    (data / "raw_messages.json").write_text(json.dumps(messages))
    meta = controls.read(data / "protocol.json")
    meta["raw_messages_sha256"] = controls.digest(data / "raw_messages.json")
    for proof in meta["tapes"].values():
        frame = pd.read_parquet(proof["path"])
        frame.loc[1, "time_utc"] = forward.END - timedelta(seconds=2)
        frame.loc[1, "source_time_msc"] -= 2000
        frame.to_parquet(proof["path"])
        proof["sha256"] = controls.digest(proof["path"])
    (data / "protocol.json").write_text(json.dumps(meta))
    inputs = forward.check(protocol, data, now=forward.END)
    assert inputs["status"] == "incomplete"
    assert "signal_without_market_quote:canal2_2" in inputs["incomplete"]
    result = forward.run(protocol, data, out=tmp_path / "late-run", now=forward.END)
    rows = controls.read(tmp_path / "late-run" / "independent_results.json")["results"]
    assert [row["signal_id"] for row in rows] == ["canal2_1", "canal2_2"]
    assert rows[1]["preparation_blockers"]
    assert result["engine_evaluations"] == 3
    assert result["denominator"] == 2 and result["status"] == "blocked"
    assert rows[1]["scalar"]["unfilled"] is False and rows[1]["scalar"]["pnl_eur"] is None


def test_excluded_channel_parser_diagnostic_does_not_block_gold(setup, tmp_path):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    messages = controls.read(data / "raw_messages.json")
    messages.append({"ev": "telegram_raw", "channel": "canal1", "message_id": 99,
                     "message_revision_id": "unknown-sticker", "date_utc": forward.START.isoformat(),
                     "ts": forward.START.isoformat(), "text": "", "sticker_id": "unknown-new-sticker"})
    (data / "raw_messages.json").write_text(json.dumps(messages))
    meta = controls.read(data / "protocol.json")
    meta["raw_messages_sha256"] = controls.digest(data / "raw_messages.json")
    (data / "protocol.json").write_text(json.dumps(meta))
    result = forward.check(protocol, data, now=forward.END)
    assert result["status"] == "evidence_ready_for_review" and result["denominator"] == 2
    assert result["excluded_input_diagnostics"][0]["reason"] == "unknown_sticker_direction"


def comparison_report(tmp_path, protocol, results):
    frozen = controls.read(protocol)
    evidence = tmp_path / "synthetic_native_evidence.json"
    proof = {"path": str(evidence), "sha256": controls.save(evidence, {"synthetic_fixture": True})}
    comparison = tmp_path / "comparison.json"
    controls.save(comparison, {
        "forward_protocol_sha256": controls.digest(protocol),
        "independent_results_sha256": controls.digest(results),
        "producer": frozen["profile_payload"]["additional_sources"][0],
        "search_candidates": 0, "mass_search_authorized": False,
        "rows": [{"signal_id": f"canal2_{i}", "status": "differences_for_review", "blockers": [],
                  "observed_entries": 1, "observed_exits": 1,
                  "differences": [{"field": "entry_price", "simulated": 100, "observed": 100.3,
                                   "hypothesis": "processing quote unobserved"}],
                  "lifecycle_checks": {name: {
                      "status": "exact" if name in forward.EXACT_NATIVE_CHECKS else "verified", "evidence": [proof],
                      **({"scope": "observed_state", "server_install_time": None}
                         if name == "installed_protection" else {})
                  } for name in forward.LIFECYCLE_CHECKS}} for i in (1, 2)],
    })
    return comparison


def test_observed_differences_are_review_evidence_not_a_millimetric_gate(setup, tmp_path, monkeypatch):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    results = engine_results(tmp_path, protocol, data, monkeypatch)
    comparison = comparison_report(tmp_path, protocol, results)
    report = forward.check(protocol, data, stage="observed", results=results,
                           comparison=comparison, now=forward.END)
    assert report["status"] == "evidence_ready_for_review"
    assert report["observed_hypothesis_reviews"][0]["differences"][0]["field"] == "entry_price"
    assert report["quantitative_agreement_admitted"] is False
    assert report["full_live_parity_verified"] is False


@pytest.mark.parametrize("state", ["not_observed", "contradiction", "invented_install_time"])
def test_installed_state_coverage_is_distinct_from_server_install_time(setup, tmp_path, monkeypatch, state):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    results = engine_results(tmp_path, protocol, data, monkeypatch)
    comparison = comparison_report(tmp_path, protocol, results)
    value = controls.read(comparison)
    check = value["rows"][0]["lifecycle_checks"]["installed_protection"]
    if state == "invented_install_time":
        check["server_install_time"] = forward.START.isoformat()
    else:
        check["status"] = "not_observed" if state == "not_observed" else "blocked"
    comparison.write_text(json.dumps(value))
    report = forward.check(protocol, data, stage="observed", results=results,
                           comparison=comparison, now=forward.END)
    if state == "not_observed":
        assert report["status"] == "incomplete" and report["blockers"] == []
        assert report["observed_coverage_gaps"] == [{"signal_id": "canal2_1",
            "capability": "installed_protection", "reason": "native_state_snapshot_not_observed"}]
        assert report["observed_hypothesis_reviews"][0]["differences"]
    else:
        assert report["status"] == "blocked"
    assert report["full_live_parity_verified"] is False


@pytest.mark.parametrize("name", forward.EXACT_NATIVE_CHECKS)
def test_native_accounting_and_request_binding_must_still_be_exact(setup, tmp_path, monkeypatch, name):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    results = engine_results(tmp_path, protocol, data, monkeypatch)
    comparison = comparison_report(tmp_path, protocol, results)
    value = controls.read(comparison)
    value["rows"][0]["lifecycle_checks"][name]["status"] = "differences_for_review"
    comparison.write_text(json.dumps(value))
    report = forward.check(protocol, data, stage="observed", results=results,
                           comparison=comparison, now=forward.END)
    assert f"canal2_1:lifecycle_unverified:{name}" in report["blockers"]


def test_fabricated_matching_results_do_not_allow_observed_input(setup, tmp_path, monkeypatch):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    results = engine_results(tmp_path, protocol, data, monkeypatch)
    value = controls.read(results)
    for engine in ("scalar", "fast", "oracle"):
        value["results"][0][engine]["pnl_eur"] = "999999.00"
    results.write_text(json.dumps(value))
    def never_read(*args):
        raise AssertionError("observed comparison read before independent replay agrees")
    monkeypatch.setattr(forward, "_observed", never_read)
    report = forward.check(protocol, data, stage="observed", results=results,
                           comparison=tmp_path / "never-read.json", now=forward.END)
    assert "independent_results_differ_from_local_replay" in report["blockers"]
    assert "observed_comparison_deferred_independent_blocked" in report["incomplete"]


def test_repeated_read_cannot_rebase_a_changed_input_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(forward, "ROOT", tmp_path)
    path = tmp_path / "input.json"
    controls.save(path, [1])
    watched = {}
    forward._read(path, watched)
    path.write_text("[2]")
    with pytest.raises(ValueError, match="changed during execution"):
        forward._read(path, watched)


@pytest.mark.parametrize("duplicate", [False, True])
def test_sidecar_runner_must_be_frozen_before_cohort(setup, duplicate):
    profile, out, _ = setup
    value = controls.read(profile)
    if duplicate:
        value["additional_sources"].append(value["additional_sources"][1])
    else:
        value["additional_sources"] = value["additional_sources"][:1]
    profile.write_text(json.dumps(value))
    report = forward.prepare(profile, out=out)
    expected = "forward_runner_source_duplicated" if duplicate else "forward_runner_source_missing"
    assert expected in report["blockers"]
    assert not out.exists()


def test_run_executes_real_causal_market_engines_without_observed_inputs(setup, tmp_path, monkeypatch):
    protocol, sha = freeze(setup)
    data = dataset(tmp_path, protocol, sha)
    value = controls.read(data / "protocol.json")
    stamps = pd.to_datetime([forward.START + timedelta(seconds=i) for i in range(8)] + [forward.END], utc=True)
    bids = [100., 98., 99.5, 99.5, 99.5, 100.5, 100.5, 100.5, 100.5]
    for symbol in ("XAUUSD", "EURUSD"):
        path = data / f"{symbol}.parquet"
        bid = bids if symbol == "XAUUSD" else [1.1] * len(stamps)
        ask = [price + .2 for price in bid] if symbol == "XAUUSD" else [1.11] * len(stamps)
        pd.DataFrame({"time_utc": stamps,
                      "source_time_msc": stamps.astype("datetime64[ms, UTC]").astype("int64") + 10800000,
                      "bid": bid, "ask": ask}).to_parquet(path)
        value["tapes"][symbol]["sha256"] = controls.digest(path)
    (data / "protocol.json").write_text(json.dumps(value))
    original = forward._read
    def no_observed(path, watched):
        assert not any(word in str(path) for word in ("ledger", "attempt", "comparison"))
        return original(path, watched)
    monkeypatch.setattr(forward, "_read", no_observed)
    out = tmp_path / "real_local_run"
    report = forward.run(protocol, data, out=out, now=forward.END)
    result = controls.read(out / "independent_results.json")
    assert result["engine_evaluations"] == 6
    assert result["runner"]["path"].endswith("run_simulator_forward.py")
    for row in result["results"]:
        for engine in ("scalar", "fast", "oracle"):
            assert row[engine]["entries"]
            assert row[engine]["market_events"]
            assert all(entry["source"] != "observed_mt5_fill" for entry in row[engine]["entries"])
    assert report["full_live_parity_verified"] is False
    replay = forward.check(protocol, data, stage="independent", results=out / "independent_results.json",
                           now=forward.END)
    assert "independent_results_differ_from_local_replay" not in replay["blockers"]
    assert replay["counts"] == report["counts"]
    before = (out / "independent_results.json").read_bytes()
    with pytest.raises(ValueError, match="never overwrite"):
        forward.run(protocol, data, out=out, now=forward.END)
    assert (out / "independent_results.json").read_bytes() == before
