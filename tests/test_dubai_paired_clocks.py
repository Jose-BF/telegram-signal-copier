from copy import deepcopy

import pytest

from research.dubai_clock_audit import build_crosswalk
from research.dubai_paired_clocks import fixed_controls, clock_cases, paired_summary, RunBudget
from research.dubai_iterative.contracts import StrategyGenome
from tests.test_dubai_clock_audit import original, trigger, decision
from tests.test_dubai_annual_dataset import annual_case
from tests.test_dubai_entry_probe import probe_case
from tests.test_strategy_study import case


def test_four_predeclared_rules_keep_risk_size_and_exact_parameters():
    controls = fixed_controls()
    assert len(controls) == 4
    genomes = {name: StrategyGenome.from_dict(row) for name, row in controls.items()}
    assert all(not g.validation_errors() and g.volume_weights == (.01,) and g.entry_expiry_min == 3
        and g.provider_management_mode == "ignore" for g in genomes.values())
    assert (genomes["delay90"].entry_value, genomes["delay90"].stop_value,
        genomes["delay90"].target_steps, genomes["delay90"].time_exit_min) == (90, 8, (12,), 5)
    assert (genomes["recovery2_1"].entry_value, genomes["recovery2_1"].entry_confirmation_value,
        genomes["recovery2_1"].stop_value, genomes["recovery2_1"].target_steps) == (2, 1, 15, (8,))
    assert genomes["delay90"].fingerprint == "0a9fb5c4b7345b9ebb4b7e4a66a07c305b1d48bd4d64dc23b4a6375aa5041fa7"
    assert genomes["recovery2_1"].fingerprint == "8b406291ec7ac83e835c7fea379032d8f9866e27d911201255a5f0d475ddfa47"


def sample():
    return build_crosswalk([original()], [trigger(), trigger(scenario="publication_initial",
        source_is_edit=False, trigger_utc="2026-08-01T10:00:00Z")],
        [decision(1, 1), decision(1, 1, "publication_initial")])


def test_repeated_raw_clock_is_evaluated_once_but_pairs_remain_separate():
    crosswalk = sample()
    before = deepcopy(crosswalk)
    cases = clock_cases(crosswalk)
    assert len(cases) == 3
    assert sum(r["clock_kind"] == "raw_receipt" for r in cases) == 1
    assert crosswalk == before
    assert all(not c["provider_events"] for c in cases)


def test_uncomparable_original_does_not_enter_price_matrix():
    a = original()
    a["retained_revision_is_edit"] = True
    assert clock_cases(build_crosswalk([a], [trigger()], [decision(1, 1)])) == []


def result(case, value, status="simulated"):
    return {"case_id": case["case_id"], "control": "market", "profile": "reference", "status": status,
        "reasons": [] if status == "simulated" else ["missing_fx"],
        "engines": {"oracle": {"pnl_eur": str(value), "unfilled": status == "unfilled"}}}


def test_paired_aggregation_keeps_same_denominator_and_counts_unfilled_zero():
    crosswalk = sample()
    cases = clock_cases(crosswalk)
    rows = [result(c, 3 if c["clock_kind"] == "raw_receipt" else 0,
                   "simulated" if c["clock_kind"] == "raw_receipt" else "unfilled") for c in cases]
    out = paired_summary(crosswalk, cases, rows, controls=("market",), profiles=("reference",))
    main = next(r for r in out["comparisons"] if r["scenario"] == "revision_time" and r["stratum"] == "all_paired")
    assert main["eligible_pairs"] == main["complete_pairs"] == 1
    assert main["raw_pnl_eur"] == "3.00" and main["export_pnl_eur"] == "0.00"
    assert main["raw_minus_export_eur"] == "3.00"


def test_missing_half_is_not_counted_as_zero_pnl():
    crosswalk = sample()
    cases = clock_cases(crosswalk)
    rows = [result(c, 3, "data_blocked" if c["clock_kind"] == "raw_receipt" else "simulated") for c in cases]
    out = paired_summary(crosswalk, cases, rows, controls=("market",), profiles=("reference",))
    main = next(r for r in out["comparisons"] if r["scenario"] == "revision_time" and r["stratum"] == "all_paired")
    assert main["complete_pairs"] == 0 and main["incomplete_pairs"] == 1
    assert main["raw_pnl_eur"] is None and main["export_pnl_eur"] is None


def test_missing_or_duplicate_matrix_row_fails():
    crosswalk, cases = sample(), clock_cases(sample())
    rows = [result(c, 1) for c in cases]
    for bad in (rows[:-1], rows + [rows[0]]):
        with pytest.raises(ValueError):
            paired_summary(crosswalk, cases, bad, controls=("market",), profiles=("reference",))


def test_summary_order_survives_canonical_json_object_key_order():
    crosswalk, cases = sample(), clock_cases(sample())
    controls = {"market": {}, "delay90": {}}
    profiles = {"reference": {}, "adverse_execution": {}}
    rows = [dict(result(c, 1), control=rule, profile=profile)
            for c in cases for rule in controls for profile in profiles]
    before = paired_summary(crosswalk, cases, rows, controls=controls, profiles=profiles)
    after = paired_summary(crosswalk, cases, rows, controls=dict(sorted(controls.items())),
                           profiles=dict(sorted(profiles.items())))
    assert before == after


def test_budget_rejects_before_excess_evaluation():
    b = RunBudget()
    b.quote_visits = 150_000_000
    with pytest.raises(ValueError):
        b.charge(1)
    assert b.evaluations == 0


def test_same_canonical_prices_support_both_clock_paths_and_three_engines(annual_case):
    from research.dubai_annual_dataset import AnnualInputs
    from research.dubai_paired_clocks import _load_path
    from research.dubai_shared_lab import _evaluate, execution_profiles
    from research.dubai_iterative.fast_engine import FastEvaluator
    from research.execution_profile import execution_from_mapping
    inputs = AnnualInputs(*annual_case()[:3])
    base = StrategyGenome.from_dict(fixed_controls()["market"])
    cases = [dict(case_id="main", clock_kind="revision_time", trigger_utc="2026-01-05T12:00:00Z",
                  published_utc="2026-01-05T12:00:00Z", direction="BUY", revision_id="revision1")]
    cases.append(dict(cases[0], case_id="receipt:canal1_1", clock_kind="raw_receipt",
                      trigger_utc="2026-01-05T12:00:01Z"))
    budget = RunBudget()
    for case in cases:
        path, coverage = _load_path(case, inputs, base)
        assert path is not None and coverage["status"] == "data_ready"
        assert path.provider_events == ()
        assert coverage["opportunity"]["hindsight_not_executable_profit"]
        for profile in execution_profiles(inputs.reference["execution"]).values():
            profile["protection"]["request_quote_binding"] = "timestamp_and_ordinal"
            execution = execution_from_mapping(profile)
            fast = FastEvaluator(execution=execution)
            for genome in fixed_controls().values():
                result, scalar = _evaluate(path, StrategyGenome.from_dict(genome), execution, fast, budget)
                assert result["status"] in {"simulated", "unfilled"}
                assert not any(result["mismatches"].values())
    assert budget.evaluations == 48
    inputs.verify_sources()


def test_export_coverage_mismatch_fails_before_engine(annual_case):
    from research.dubai_annual_dataset import AnnualInputs
    from research.dubai_paired_clocks import _load_path
    inputs = AnnualInputs(*annual_case(wrong_coverage=True)[:3])
    case = dict(case_id="main", clock_kind="revision_time", trigger_utc="2026-01-05T12:00:00Z",
                published_utc="2026-01-05T12:00:00Z", direction="BUY", revision_id="revision1")
    with pytest.raises(ValueError, match="coverage differs"):
        _load_path(case, inputs, StrategyGenome.from_dict(fixed_controls()["market"]))


def paired_fixture(annual_case, tmp_path):
    from research.dubai_entry_probe import _rows
    from research.dubai_clock_audit import run_audit
    from research.strategy_study import _read, _encode, _digest, _sha
    from tests.test_dubai_clock_audit import audit_fixture
    stream, coverage, raw_prices, unused = annual_case()
    triggers = _rows(stream / "triggers.jsonl")
    mapping, decisions = {}, []
    for r in triggers:
        key = f"dubai_stream:{r['scenario']}:1"
        mapping[r["trigger_id"]] = key
        r.update(trigger_id=key, trigger_message_id=1, source_is_edit=False, source_kind="sticker")
        decisions.append(dict(message_id=1, trigger_id=key, scenario=r["scenario"]))
    coverage_rows = _rows(stream / "coverage.jsonl")
    for r in coverage_rows:
        r["trigger_id"] = mapping[r["trigger_id"]]
    payloads = {"triggers.jsonl": triggers, "decisions.jsonl": decisions, "coverage.jsonl": coverage_rows}
    for name, rows in payloads.items():
        (stream / name).write_bytes(b"".join(_encode(r) for r in rows))
    manifest = _read(stream / "manifest.json")
    for name in payloads:
        manifest["artifacts"][name] = {"sha256": _digest(stream / name)}
    del manifest["stream_identity_sha256"]
    manifest["stream_identity_sha256"] = _sha(manifest)
    (stream / "manifest.json").write_bytes(_encode(manifest))
    originals, other_stream, audit, source = audit_fixture(tmp_path / "originals", stamp="2026-01-05T12:00:00Z")
    run_audit(originals, stream, audit)
    return audit, coverage, raw_prices, tmp_path / "paired"


def test_full_original_export_prices_engines_and_archive_reopen(annual_case, tmp_path):
    from research.dubai_paired_clocks import run_controls, verify_controls
    args = paired_fixture(annual_case, tmp_path)
    summary = run_controls(*args)
    assert summary["evaluation_rows"] == 24
    assert summary["usage"]["evaluations"] == 72
    assert verify_controls(args[-1])["status"] == "verified_fixed_paired_controls_only"
    with pytest.raises(ValueError, match="immutable"):
        run_controls(*args)


@pytest.mark.parametrize("issue", ["source_change", "missing_row", "money_tampered", "missing_manifest"])
def test_paired_archive_and_bound_evidence_fail_closed(annual_case, tmp_path, issue):
    from research.dubai_paired_clocks import run_controls, verify_controls
    from research.dubai_entry_probe import _rows
    from research.strategy_study import _read, _encode, _digest, _sha
    args = paired_fixture(annual_case, tmp_path)
    run_controls(*args)
    out = args[-1]
    if issue == "source_change":
        args[2].write_bytes(args[2].read_bytes() + b"\n")
    elif issue == "missing_manifest":
        (out / "manifest.json").unlink()
    else:
        rows = _rows(out / "results.jsonl")
        if issue == "missing_row":
            rows.pop()
        else:
            rows[0]["engines"]["fast"]["pnl_eur"] = "999.00"
        target = out / "results.jsonl"
        target.write_bytes(b"".join(_encode(r) for r in rows))
        manifest = _read(out / "manifest.json")
        manifest["artifacts"][target.name]["sha256"] = _digest(target)
        del manifest["paired_identity_sha256"]
        manifest["paired_identity_sha256"] = _sha(manifest)
        (out / "manifest.json").write_bytes(_encode(manifest))
    with pytest.raises(ValueError):
        verify_controls(out)
