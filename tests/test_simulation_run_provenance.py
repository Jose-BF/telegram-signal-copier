import copy
import gzip
import hashlib
import importlib
import json
from pathlib import Path

import pytest


def _provenance():
    return importlib.import_module("simulation_run_provenance")


def test_executed_conclusions_require_independent_certification():
    module = _provenance()
    validation = {
        "artifact_integrity_verified": True,
        "primary_universe": "executed_mt5",
        "price_path_mode": "executed_mt5_entries",
        "market_replay_strategy_eligible": True,
        "executed_row_accounting_verified": True,
        "executed_contract_complete": True,
        "money_contract_verified": True,
        "account_currency_money_verified": True,
    }

    assert module._validation_allows_conclusions(validation) is False

    validation["independent_certification_complete"] = True

    assert module._validation_allows_conclusions(validation) is True


def _report(generated_at="2026-07-13T10:00:00+00:00"):
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "from_date": "2026-07-06",
        "to_date": None,
        "executed_trade_count": 2,
        "policy_count": 1,
        "includes_trade_details": False,
        "canonical_scope": {"provider_signals": 2},
        "selection": {
            "selected_policy": None,
            "global_blockers": ["oos_not_validated"],
        },
        "policies": [{
            "policy_id": "follow_actual",
            "metrics": {"net_pnl": 1.0},
        }],
    }


def _evidence_args(root: Path, branch="main"):
    root.mkdir(parents=True, exist_ok=True)
    input_dir = root / "inputs"
    source_dir = root / "source"
    input_dir.mkdir()
    source_dir.mkdir()
    replay = input_dir / "replay.jsonl"
    baseline = input_dir / "baseline.jsonl"
    catalog = input_dir / "catalog.json"
    engine = source_dir / "engine.py"
    replay.write_text(
        '{"sig_id":"canal1_2"}\n{"sig_id":"canal1_1"}\n',
        encoding="utf-8",
    )
    baseline.write_text(
        '{"sig_id":"canal1_1","status":"exact"}\n',
        encoding="utf-8",
    )
    catalog.write_text('{"signals":[]}\n', encoding="utf-8")
    engine.write_text("ENGINE_VERSION = 1\n", encoding="utf-8")
    return {
        "repo_dir": root,
        "report": _report(),
        "parameters": {"from_date": "2026-07-06", "to_date": None},
        "selected_payloads": {
            "replay_trades": [
                {"sig_id": "canal1_2"},
                {"sig_id": "canal1_1"},
            ],
            "effective_baselines": [
                {"sig_id": "canal1_2", "baseline": None},
                {
                    "sig_id": "canal1_1",
                    "baseline": {"status": "exact"},
                },
            ],
        },
        "policies": [{
            "policy_id": "follow_actual",
            "mode": "follow_actual",
        }],
        "input_files": {
            "replay_trades": replay,
            "observed_baseline": baseline,
            "provider_catalog": catalog,
        },
        "source_files": {"engine": engine},
        "required_tick_days": ["2026-07-06"],
        "tick_contracts": {
            "2026-07-06": {
                "day": "2026-07-06",
                "tick_time_contract": "mt5_server_epoch_utc_v3",
                "time_basis": "UTC",
                "source_time_basis": "mt5_server_epoch",
                "utc_offset_seconds": 10_800,
                "offset_detection_method": "fill_anchor",
                "offset_reference": {"signal_id": "canal1_1"},
                "semantic_time_valid": True,
                "anchor_validation": {
                    "valid": True,
                    "anchors_checked": 1,
                    "anchors_matched": 1,
                    "max_time_delta_ms": 0,
                    "max_price_delta": 0.0,
                    "errors": [],
                },
                "symbol": "XAUUSD",
                "coverage": {
                    "source_query_start_utc": (
                        "2026-07-06T00:00:00+00:00"
                    ),
                    "source_query_end_utc": (
                        "2026-07-07T00:00:00+00:00"
                    ),
                    "captured_at_utc": "2026-07-07T00:01:00+00:00",
                    "first_tick_utc": "2026-07-06T00:00:00+00:00",
                    "last_tick_utc": "2026-07-07T00:00:00+00:00",
                    "complete_from_utc": "2026-07-06T00:00:00+00:00",
                    "complete_through_utc": "2026-07-07T00:00:00+00:00",
                    "row_count": 123,
                },
                "source_verification": {
                    "verified": True,
                    "method": "full_day_vs_two_half_days_v1",
                    "content_digest": "time_bid_ask_sequence_sha256_v1",
                    "symbol": "XAUUSD",
                    "primary_row_count": 123,
                    "verification_row_count": 123,
                    "primary_content_sha256": "c" * 64,
                    "verification_content_sha256": "c" * 64,
                    "errors": [],
                },
                "parquet_sha256": "a" * 64,
                "contract_sha256": "d" * 64,
                "size_bytes": 123,
            }
        },
        "market_replay": {
            "selected_trades": 2,
            "exact": 2,
            "blocked": 0,
            "mismatched": 0,
        },
        "runtime": {
            "python": "3.14.2",
            "packages": {
                "pandas": "3.0.2",
                "numpy": "2.4.4",
                "pyarrow": "23.0.1",
            },
        },
        "git": {
            "commit": "1" * 40,
            "branch": branch,
            "dirty": False,
            "errors": [],
        },
    }


def test_run_identity_ignores_paths_mapping_order_branch_and_timestamp(tmp_path):
    provenance = _provenance()
    left = _evidence_args(tmp_path / "left", branch="main")
    right = _evidence_args(tmp_path / "right", branch="feature/replay")
    right["parameters"] = {"to_date": None, "from_date": "2026-07-06"}
    right["report"]["generated_at"] = "2026-07-13T11:00:00+00:00"

    card_left = provenance.build_run_evidence(**left)
    card_right = provenance.build_run_evidence(**right)

    assert card_left["run_fingerprint"] == card_right["run_fingerprint"]
    assert card_left["result_fingerprint"] == card_right["result_fingerprint"]
    assert card_left["reproducibility"]["git"]["branch"] == "main"
    assert card_right["reproducibility"]["git"]["branch"] == "feature/replay"


def test_replay_order_is_part_of_run_identity(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    first = provenance.build_run_evidence(**args)
    args["selected_payloads"]["replay_trades"].reverse()

    second = provenance.build_run_evidence(**args)

    assert first["run_fingerprint"] != second["run_fingerprint"]


def test_runtime_policy_source_and_tick_changes_change_run_identity(tmp_path):
    provenance = _provenance()
    roots = [tmp_path / name for name in ("runtime", "policy", "source", "tick")]
    cases = [_evidence_args(root) for root in roots]

    original = provenance.build_run_evidence(**cases[0])["run_fingerprint"]
    cases[0]["runtime"]["packages"]["pandas"] = "3.0.3"
    assert provenance.build_run_evidence(**cases[0])["run_fingerprint"] != original

    original = provenance.build_run_evidence(**cases[1])["run_fingerprint"]
    cases[1]["policies"][0]["mode"] = "risk_free_allocation"
    assert provenance.build_run_evidence(**cases[1])["run_fingerprint"] != original

    original = provenance.build_run_evidence(**cases[2])["run_fingerprint"]
    cases[2]["source_files"]["engine"].write_text(
        "ENGINE_VERSION = 2\n",
        encoding="utf-8",
    )
    assert provenance.build_run_evidence(**cases[2])["run_fingerprint"] != original

    original = provenance.build_run_evidence(**cases[3])["run_fingerprint"]
    cases[3]["tick_contracts"]["2026-07-06"]["parquet_sha256"] = "b" * 64
    assert provenance.build_run_evidence(**cases[3])["run_fingerprint"] != original


def _add_provider_first_identity(args):
    args["report"]["provider_scope"] = {
        "formal_signals": 1,
        "policy_count": 1,
        "latency_scenarios_ms": [0, 250],
        "rows_expected": 2,
        "rows_emitted": 2,
        "simulated_rows": 2,
        "blocked_rows": 0,
        "signals_omitted": [],
    }
    args["report"]["validation"] = {
        "price_path_mode": "provider_first",
        "money_mode": "diagnostic_only",
    }
    args["selected_payloads"].update({
        "provider_scope": [{
            "provider_signal_id": "canal1_1",
            "revisions": [{"message_id": 1, "text": "BUY"}],
        }],
        "provider_trade_specs": [{
            "provider_signal_id": "canal1_1",
            "latency_ms": 0,
            "volume_per_leg": 0.01,
        }],
        "provider_latency_scenarios_ms": [0, 250],
        "provider_volume_per_leg": [0.01],
        "provider_policy_results": [{
            "policy_id": "follow_actual",
            "results": [{
                "provider_signal_id": "canal1_1",
                "latency_scenario_ms": 0,
                "status": "simulated_price_path",
                "strategy_value": 1.0,
            }],
        }],
    })
    return args


@pytest.mark.parametrize(
    "mutate",
    [
        lambda args: args["selected_payloads"]["provider_scope"][0][
            "revisions"
        ][0].update(text="SELL"),
        lambda args: args["selected_payloads"][
            "provider_latency_scenarios_ms"
        ].reverse(),
        lambda args: args["selected_payloads"][
            "provider_volume_per_leg"
        ].__setitem__(0, 0.02),
        lambda args: args["selected_payloads"][
            "provider_policy_results"
        ][0]["results"][0].update(strategy_value=2.0),
    ],
    ids=("telegram_revision", "latency_order", "volume", "result"),
)
def test_provider_first_inputs_change_run_identity(tmp_path, mutate):
    provenance = _provenance()
    args = _add_provider_first_identity(_evidence_args(tmp_path))
    original = provenance.build_run_evidence(**args)["run_fingerprint"]

    mutate(args)

    assert provenance.build_run_evidence(**args)["run_fingerprint"] != original


def test_provider_first_money_is_diagnostic_even_with_exact_market_replay(
    tmp_path,
):
    provenance = _provenance()
    args = _add_provider_first_identity(_evidence_args(tmp_path))

    evidence = provenance.build_run_evidence(**args)

    assert evidence["reproducibility"]["verified_now"] is True
    assert evidence["validation"]["provider_row_accounting_verified"] is True
    assert evidence["validation"]["money_contract_verified"] is False
    assert evidence["validation"]["conclusions_allowed"] is False
    assert evidence["validation"]["mode"] == "diagnostic_only"


def test_incomplete_provider_row_accounting_fails_artifact_integrity(tmp_path):
    provenance = _provenance()
    args = _add_provider_first_identity(_evidence_args(tmp_path))
    args["report"]["provider_scope"]["rows_emitted"] = 1

    evidence = provenance.build_run_evidence(**args)

    assert evidence["reproducibility"]["verified_now"] is False
    assert "provider_row_accounting_incomplete" in evidence[
        "reproducibility"
    ]["errors"]
    assert evidence["validation"]["provider_row_accounting_verified"] is False


def test_provider_first_evidence_requires_every_identity_payload(tmp_path):
    provenance = _provenance()
    args = _add_provider_first_identity(_evidence_args(tmp_path))
    args["selected_payloads"].pop("provider_policy_results")

    evidence = provenance.build_run_evidence(**args)

    assert evidence["reproducibility"]["verified_now"] is False
    assert evidence["reproducibility"]["errors"] == [
        "missing_provider_selected_payload:provider_policy_results"
    ]


def _add_executed_mt5_identity(args):
    args["report"]["primary_universe"] = "executed_mt5"
    args["report"]["executed_scope"] = {
        "executed_trades": 2,
        "policy_count": 1,
        "rows_expected": 2,
        "rows_emitted": 2,
        "blocked_rows": 0,
        "entry_invariant_failures": 0,
    }
    args["report"]["executed_replay_contract"] = {
        "universe": "executed_mt5",
        "complete": True,
        "rows_expected": 2,
        "rows_emitted": 2,
        "blocked_rows": 0,
        "entry_invariant_failures": 0,
        "blockers": [],
    }
    args["report"]["validation"] = {
        "primary_universe": "executed_mt5",
        "price_path_mode": "executed_mt5_entries",
        "money_mode": "verified_account_currency",
        "money_contract_verified": True,
        "account_currency_money_verified": True,
        "market_replay_strategy_eligible": True,
        "executed_contract_complete": True,
        "independent_certification_complete": True,
    }
    certificates = [
        {
            "sig_id": "canal1_2",
            "strategy": "follow_actual",
            "status": "certified",
            "certified_tickets": 1,
            "mismatched_tickets": 0,
            "blocked_tickets": 0,
            "blockers": [],
            "proof_sha256": "2" * 64,
        },
        {
            "sig_id": "canal1_1",
            "strategy": "follow_actual",
            "status": "certified",
            "certified_tickets": 1,
            "mismatched_tickets": 0,
            "blocked_tickets": 0,
            "blockers": [],
            "proof_sha256": "1" * 64,
        },
    ]
    proof_records = [
        {
            "sig_id": row["sig_id"],
            "strategy": row["strategy"],
            "status": row["status"],
            "proof_sha256": row["proof_sha256"],
        }
        for row in sorted(
            certificates,
            key=lambda row: (row["sig_id"], row["strategy"]),
        )
    ]
    args["report"]["independent_certification"] = {
        "rows_expected": 2,
        "rows_checked": 2,
        "certified_rows": 2,
        "mismatched_rows": 0,
        "blocked_rows": 0,
        "tickets_expected": 2,
        "certified_tickets": 2,
        "mismatched_tickets": 0,
        "blocked_tickets": 0,
        "proof_sha256": _provenance().sha256_json(proof_records),
        "deterministic": True,
        "complete": True,
        "conclusions_allowed": True,
        "blockers": [],
    }
    args["selected_payloads"]["effective_provider_links"] = [
        {"sig_id": "canal1_2", "provider_signal": {"provider_signal_id": "p2"}},
        {"sig_id": "canal1_1", "provider_signal": {"provider_signal_id": "p1"}},
    ]
    args["selected_payloads"]["independent_certificates"] = certificates
    return args


def test_executed_mt5_mode_requires_complete_contract_and_verified_money(
    tmp_path,
):
    provenance = _provenance()
    args = _add_executed_mt5_identity(_evidence_args(tmp_path))

    evidence = provenance.build_run_evidence(**args)

    assert evidence["reproducibility"]["verified_now"] is True
    assert evidence["validation"]["executed_row_accounting_verified"] is True
    assert evidence["validation"]["money_contract_verified"] is True
    assert evidence["validation"]["account_currency_money_verified"] is True
    assert evidence["validation"]["conclusions_allowed"] is True
    assert evidence["validation"]["mode"] == "verified_simulation"


def test_incomplete_executed_contract_fails_artifact_integrity(tmp_path):
    provenance = _provenance()
    args = _add_executed_mt5_identity(_evidence_args(tmp_path))
    args["report"]["executed_scope"]["rows_emitted"] = 1
    args["report"]["executed_replay_contract"]["complete"] = False

    evidence = provenance.build_run_evidence(**args)

    assert evidence["reproducibility"]["verified_now"] is False
    assert "executed_row_accounting_incomplete" in evidence[
        "reproducibility"
    ]["errors"]
    assert evidence["validation"]["conclusions_allowed"] is False


def test_executed_mt5_metadata_contract_cannot_replace_money_reconciliation(
    tmp_path,
):
    provenance = _provenance()
    args = _add_executed_mt5_identity(_evidence_args(tmp_path))
    args["report"]["validation"].update({
        "money_mode": "account_currency_diagnostic",
        "money_contract_verified": True,
        "account_currency_money_verified": False,
    })

    evidence = provenance.build_run_evidence(**args)

    assert evidence["validation"]["money_contract_verified"] is True
    assert evidence["validation"]["account_currency_money_verified"] is False
    assert evidence["validation"]["conclusions_allowed"] is False
    assert evidence["validation"]["mode"] == "diagnostic_only"


def test_executed_mt5_evidence_requires_provider_trigger_links(tmp_path):
    provenance = _provenance()
    args = _add_executed_mt5_identity(_evidence_args(tmp_path))
    args["selected_payloads"].pop("effective_provider_links")

    evidence = provenance.build_run_evidence(**args)

    assert evidence["reproducibility"]["verified_now"] is False
    assert evidence["reproducibility"]["errors"] == [
        "missing_executed_selected_payload:effective_provider_links"
    ]


def test_executed_mt5_evidence_requires_independent_certificates(tmp_path):
    provenance = _provenance()
    args = _add_executed_mt5_identity(_evidence_args(tmp_path))
    args["selected_payloads"].pop("independent_certificates")

    evidence = provenance.build_run_evidence(**args)

    assert evidence["reproducibility"]["verified_now"] is False
    assert (
        "missing_executed_selected_payload:independent_certificates"
        in evidence["reproducibility"]["errors"]
    )
    assert evidence["validation"][
        "independent_certification_complete"
    ] is False
    assert evidence["validation"]["conclusions_allowed"] is False


def test_executed_mt5_rejects_tampered_independent_certificate(tmp_path):
    provenance = _provenance()
    args = _add_executed_mt5_identity(_evidence_args(tmp_path))
    args["selected_payloads"]["independent_certificates"][0][
        "proof_sha256"
    ] = "f" * 64

    evidence = provenance.build_run_evidence(**args)

    assert "independent_certification_incomplete" in evidence[
        "reproducibility"
    ]["errors"]
    assert evidence["validation"][
        "independent_certification_complete"
    ] is False
    assert evidence["validation"]["conclusions_allowed"] is False


def test_non_v3_tick_contract_is_not_verified_for_publication(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    args["tick_contracts"]["2026-07-06"]["tick_time_contract"] = "mt5_utc_v2"

    evidence = provenance.build_run_evidence(**args)

    assert evidence["reproducibility"]["verified_now"] is False
    assert evidence["reproducibility"]["errors"] == [
        "unverified_tick_contract:2026-07-06"
    ]


def test_tick_contract_without_source_verification_is_not_publishable(
    tmp_path,
):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    args["tick_contracts"]["2026-07-06"].pop("source_verification")

    evidence = provenance.build_run_evidence(**args)

    assert evidence["reproducibility"]["verified_now"] is False
    assert evidence["reproducibility"]["errors"] == [
        "unverified_tick_contract:2026-07-06"
    ]


def test_result_fingerprint_preserves_semantic_policy_order():
    provenance = _provenance()
    report_a = _report(generated_at="2026-07-13T10:00:00+00:00")
    report_b = copy.deepcopy(report_a)
    report_b["generated_at"] = "2026-07-13T11:00:00+00:00"

    assert provenance.result_fingerprint(report_a) == provenance.result_fingerprint(report_b)

    report_b["policies"].append({
        "policy_id": "no_be",
        "metrics": {"net_pnl": 1.0},
    })
    assert provenance.result_fingerprint(report_a) != provenance.result_fingerprint(report_b)


def test_missing_input_or_tick_contract_marks_evidence_incomplete(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    args["input_files"]["provider_catalog"].unlink()
    args["tick_contracts"] = {}

    card = provenance.build_run_evidence(**args)

    assert card["reproducibility"]["verified_now"] is False
    assert card["reproducibility"]["errors"] == [
        "missing_input:provider_catalog",
        "unverified_tick_contract:2026-07-06",
    ]


def test_integrity_does_not_authorize_blocked_market_replay(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    args["market_replay"] = {
        "selected_trades": 2,
        "exact": 0,
        "blocked": 2,
        "mismatched": 0,
    }

    card = provenance.build_run_evidence(**args)

    assert card["reproducibility"]["verified_now"] is True
    assert card["validation"] == {
        "artifact_integrity_verified": True,
        "market_replay_verified": False,
        "conclusions_allowed": False,
        "mode": "diagnostic_only",
        "market_replay": args["market_replay"],
    }


def test_full_input_files_are_diagnostics_not_machine_specific_identity(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)

    card = provenance.build_run_evidence(**args)

    records = {
        row["role"]: row
        for row in card["reproducibility"]["input_artifacts"]
    }
    assert set(records) == {
        "observed_baseline",
        "provider_catalog",
        "replay_trades",
    }
    assert records["replay_trades"]["path"] == "inputs/replay.jsonl"
    assert len(records["replay_trades"]["sha256"]) == 64


def test_card_never_captures_environment_or_secret_values(tmp_path, monkeypatch):
    provenance = _provenance()
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-appear")

    card = provenance.build_run_evidence(**_evidence_args(tmp_path))
    payload = json.dumps(card)

    assert "must-not-appear" not in payload
    assert "GEMINI_API_KEY" not in payload


def _complete_evidence(root: Path):
    provenance = _provenance()
    args = _evidence_args(root)
    report = args["report"]
    return provenance.build_run_evidence(**args), report


def _publish_args(root: Path, evidence: dict, report: dict, *, include_trades=False):
    return {
        "report": report,
        "evidence": evidence,
        "archive_root": root / "runs",
        "output_path": root / "strategy_farm.json",
        "include_trades": include_trades,
        "repo_dir": root,
    }


def test_compact_run_is_published_once_and_idempotent(tmp_path):
    provenance = _provenance()
    evidence, report = _complete_evidence(tmp_path)

    first = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report),
    )
    repeated_report = {**report, "generated_at": "later"}
    second = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, repeated_report),
    )

    assert first.run_dir == second.run_dir
    assert first.idempotent is False
    assert second.idempotent is True
    assert len(list((tmp_path / "runs").glob("[0-9a-f]*"))) == 1
    assert (first.run_dir / "run_card.json").is_file()
    archive = first.run_dir / "strategy_farm.json.gz"
    assert archive.is_file()
    assert not (first.run_dir / "strategy_farm.json").exists()
    canonical = provenance.pretty_json_bytes(first.report)
    assert gzip.decompress(archive.read_bytes()) == canonical
    card = json.loads((first.run_dir / "run_card.json").read_text())
    artifact = card["artifacts"][0]
    assert artifact["path"] == "strategy_farm.json.gz"
    assert artifact["compression"] == "gzip"
    assert artifact["canonical_size_bytes"] == len(canonical)
    assert artifact["canonical_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert artifact["size_bytes"] == archive.stat().st_size
    assert artifact["sha256"] == provenance.sha256_file(archive)


def test_blocked_market_replay_is_archived_as_diagnostic(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    args["market_replay"] = {
        "selected_trades": 2,
        "exact": 0,
        "blocked": 2,
        "mismatched": 0,
    }
    evidence = provenance.build_run_evidence(**args)

    published = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, args["report"]),
    )

    assert published.status == "diagnostic_archived"
    assert published.run_dir is not None
    assert published.report["validation"]["mode"] == "diagnostic_only"
    assert published.report["provenance"]["status"] == "diagnostic_archived"
    card = json.loads(
        (published.run_dir / "run_card.json").read_text(encoding="utf-8")
    )
    assert card["validation"]["conclusions_allowed"] is False


def test_provider_first_exact_price_path_still_archives_as_diagnostic(tmp_path):
    provenance = _provenance()
    args = _add_provider_first_identity(_evidence_args(tmp_path))
    evidence = provenance.build_run_evidence(**args)

    published = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, args["report"]),
    )

    assert published.status == "diagnostic_archived"
    assert published.run_dir is not None
    assert published.report["validation"]["market_replay_verified"] is True
    assert published.report["validation"]["money_contract_verified"] is False
    assert published.report["provenance"]["status"] == "diagnostic_archived"


def test_executed_mt5_external_intervention_is_published_when_accounted(
    tmp_path,
):
    provenance = _provenance()
    args = _add_executed_mt5_identity(_evidence_args(tmp_path))
    args["market_replay"] = {
        "selected_trades": 2,
        "exact": 1,
        "external_interventions": 1,
        "blocked": 0,
        "mismatched": 0,
    }
    evidence = provenance.build_run_evidence(**args)

    assert evidence["validation"]["market_replay_verified"] is False
    assert evidence["validation"]["market_replay_strategy_eligible"] is True
    assert evidence["validation"]["conclusions_allowed"] is True

    published = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, args["report"]),
    )

    assert published.status == "archived"
    assert published.report["validation"]["mode"] == "verified_simulation"
    assert published.report["provenance"]["status"] == "archived"


def test_executed_mt5_delayed_close_is_published_when_accounted(tmp_path):
    provenance = _provenance()
    args = _add_executed_mt5_identity(_evidence_args(tmp_path))
    args["market_replay"] = {
        "selected_trades": 2,
        "exact": 1,
        "external_interventions": 0,
        "delayed_close_observations": 1,
        "blocked": 0,
        "mismatched": 0,
    }
    evidence = provenance.build_run_evidence(**args)

    assert evidence["validation"]["market_replay_verified"] is False
    assert evidence["validation"]["market_replay_strategy_eligible"] is True
    assert evidence["validation"]["conclusions_allowed"] is True

    published = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, args["report"]),
    )

    assert published.status == "archived"
    assert published.report["validation"]["mode"] == "verified_simulation"


def test_same_identity_with_different_result_fails_closed(tmp_path):
    provenance = _provenance()
    evidence, report = _complete_evidence(tmp_path)
    provenance.publish_run_archive(**_publish_args(tmp_path, evidence, report))
    changed = copy.deepcopy(evidence)
    changed["result_fingerprint"] = "f" * 64

    with pytest.raises(provenance.ProvenanceConflictError):
        provenance.publish_run_archive(
            **_publish_args(tmp_path, changed, report),
        )


def test_corrupt_retained_artifact_fails_closed(tmp_path):
    provenance = _provenance()
    evidence, report = _complete_evidence(tmp_path)
    first = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report),
    )
    (first.run_dir / "strategy_farm.json.gz").write_bytes(b"corrupt\n")

    with pytest.raises(provenance.ProvenanceConflictError):
        provenance.publish_run_archive(
            **_publish_args(tmp_path, evidence, report),
        )


def test_legacy_uncompressed_archive_remains_readable(tmp_path):
    provenance = _provenance()
    evidence, report = _complete_evidence(tmp_path)
    first = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report),
    )
    archive = first.run_dir / "strategy_farm.json.gz"
    canonical = gzip.decompress(archive.read_bytes())
    legacy = first.run_dir / "strategy_farm.json"
    legacy.write_bytes(canonical)
    archive.unlink()
    card_path = first.run_dir / "run_card.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    artifact = card["artifacts"][0]
    artifact.clear()
    artifact.update({
        "path": "strategy_farm.json",
        "size_bytes": len(canonical),
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "retained": True,
    })
    card_path.write_text(json.dumps(card), encoding="utf-8")

    repeated = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, {**report, "generated_at": "later"}),
    )

    assert repeated.idempotent is True
    assert repeated.run_dir == first.run_dir


def test_incomplete_evidence_marks_latest_report_without_archive(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    args["tick_contracts"] = {}
    report = args["report"]
    evidence = provenance.build_run_evidence(**args)

    result = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report),
    )

    assert result.status == "incomplete"
    assert result.run_dir is None
    assert result.report["provenance"]["status"] == "incomplete"
    assert not (tmp_path / "runs").exists()


def test_detailed_result_is_referenced_but_not_copied(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    report = args["report"]
    report["includes_trade_details"] = True
    evidence = provenance.build_run_evidence(**args)

    result = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report, include_trades=True),
    )

    card = json.loads((result.run_dir / "run_card.json").read_text())
    assert not (result.run_dir / "strategy_farm.json").exists()
    assert not (result.run_dir / "strategy_farm.json.gz").exists()
    assert card["artifacts"][0]["retained"] is False


def test_idempotence_ignores_git_and_unselected_file_diagnostics(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    first_evidence = provenance.build_run_evidence(**args)
    first = provenance.publish_run_archive(
        **_publish_args(tmp_path, first_evidence, args["report"]),
    )
    args["git"] = {
        "commit": "2" * 40,
        "branch": "main-after-data-commit",
        "dirty": True,
        "errors": [],
    }
    with args["input_files"]["replay_trades"].open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write('{"sig_id":"outside_selected_window"}\n')
    second_evidence = provenance.build_run_evidence(**args)

    second = provenance.publish_run_archive(
        **_publish_args(tmp_path, second_evidence, args["report"]),
    )

    assert second_evidence["run_fingerprint"] == first_evidence["run_fingerprint"]
    assert second.run_dir == first.run_dir
    assert second.idempotent is True


def test_existing_card_rejects_tampered_computational_identity(tmp_path):
    provenance = _provenance()
    evidence, report = _complete_evidence(tmp_path)
    published = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report),
    )
    card_path = published.run_dir / "run_card.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["reproducibility"]["parameters"]["from_date"] = "2026-07-07"
    card_path.write_text(json.dumps(card), encoding="utf-8")

    with pytest.raises(provenance.ProvenanceConflictError):
        provenance.publish_run_archive(
            **_publish_args(tmp_path, evidence, report),
        )


def test_detailed_idempotent_report_matches_first_published_artifact(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    args["report"]["includes_trade_details"] = True
    first_evidence = provenance.build_run_evidence(**args)
    first = provenance.publish_run_archive(
        **_publish_args(
            tmp_path,
            first_evidence,
            args["report"],
            include_trades=True,
        ),
    )
    args["report"]["generated_at"] = "2026-07-13T12:00:00+00:00"
    second_evidence = provenance.build_run_evidence(**args)

    second = provenance.publish_run_archive(
        **_publish_args(
            tmp_path,
            second_evidence,
            args["report"],
            include_trades=True,
        ),
    )
    card = json.loads((first.run_dir / "run_card.json").read_text())
    expected_hash = card["artifacts"][0]["sha256"]

    assert second.idempotent is True
    assert hashlib.sha256(
        provenance.pretty_json_bytes(second.report)
    ).hexdigest() == expected_hash


def test_malformed_card_identity_fails_with_controlled_conflict(tmp_path):
    provenance = _provenance()
    evidence, report = _complete_evidence(tmp_path)
    published = provenance.publish_run_archive(
        **_publish_args(tmp_path, evidence, report),
    )
    card_path = published.run_dir / "run_card.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["reproducibility"]["source_files"] = [{}]
    card_path.write_text(json.dumps(card), encoding="utf-8")

    with pytest.raises(provenance.ProvenanceConflictError):
        provenance.publish_run_archive(
            **_publish_args(tmp_path, evidence, report),
        )


def test_malformed_detailed_artifact_fails_with_controlled_conflict(tmp_path):
    provenance = _provenance()
    args = _evidence_args(tmp_path)
    args["report"]["includes_trade_details"] = True
    evidence = provenance.build_run_evidence(**args)
    published = provenance.publish_run_archive(
        **_publish_args(
            tmp_path,
            evidence,
            args["report"],
            include_trades=True,
        ),
    )
    card_path = published.run_dir / "run_card.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["artifacts"][0]["size_bytes"] = "not-an-integer"
    card_path.write_text(json.dumps(card), encoding="utf-8")

    with pytest.raises(provenance.ProvenanceConflictError):
        provenance.publish_run_archive(
            **_publish_args(
                tmp_path,
                evidence,
                args["report"],
                include_trades=True,
            ),
        )
