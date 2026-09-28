from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json

import pytest

from tools import run_protection_controls as controls


BASE = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)


def test_frozen_profile_protocol_can_be_read_back_without_type_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(controls, "ROOT", tmp_path)
    monkeypatch.setattr(controls, "identity", lambda: {"fixture": "fixed"})
    study = tmp_path / "study"
    study.mkdir()
    messages_hash = controls.save(study / "raw_messages.json", [])
    diagnostics_hash = controls.save(study / "input_diagnostics.json", {})
    controls.save(study / "protocol.json", {
        "contract": "protection_control_diagnostic_v1", "universe": "independent",
        "implementation": controls.identity(), "raw_messages_sha256": messages_hash,
        "conditioned_genomes": {}, "actual_preparation_sources": None,
        "input_diagnostics_sha256": diagnostics_hash, "openings_sha256": None,
        "search_candidate_budget": 0, "mass_search_authorized": False,
        "execution": controls.EXECUTION, "max_wall_seconds": controls.MAX_WALL_SECONDS,
        "max_signals": controls.MAX_SIGNALS, "max_engine_evaluations": controls.MAX_ENGINE_EVALUATIONS,
        "profiles": controls._profile_payloads(),
    })
    loaded = controls._load_run_study(study)
    assert loaded[1]["profiles"] == controls._profile_payloads()
    assert loaded[-2:] == (None, None)


def test_frozen_inputs_are_hashed_from_the_bytes_that_are_parsed(tmp_path):
    path = tmp_path / "protocol.json"
    expected = controls.save(path, {"version": 1})

    value, actual = controls.read_frozen(path)

    assert value == {"version": 1}
    assert actual == expected
    path.write_text('{"version": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="frozen input changed during execution"):
        controls.verify_frozen(path, expected)


def test_actual_opening_binding_emits_only_opening_facts():
    observed = {
        "signals": [{
            "sig_id": "canal2_1",
            "channel": "canal2",
            "direction": "BUY",
            "symbol": "XAUUSD",
            "n_positions": 1,
            "pnl_real_mt5": "999.99",
            "positions": [{
                "position_id": 101,
                "symbol": "XAUUSD",
                "direction": "BUY",
                "opened_at_precise": BASE.isoformat(),
                "open_price": 100.2,
                "volume": 0.04,
                "closed_at_precise": "2026-09-08T10:01:00+00:00",
                "close_price": 100.7,
                "pnl_net": "999.99",
                "open_deal": {
                    "ticket": 201,
                    "order": 101,
                    "position_id": 101,
                    "comment": "c2_1_g55",
                    "symbol": "XAUUSD",
                    "type": 0,
                    "price": 100.2,
                    "volume": 0.04,
                    "entry": 0,
                },
                "close_deal": {"ticket": 202, "reason": 5},
            }],
        }]
    }
    attempts = [{
        "attempt_id": "open-1",
        "sig": "canal2_1",
        "operation": "OPEN_MARKET",
        "broker_request_sent": True,
        "position_before": {"sl": 88.0, "tp": 120.0},
        "request": {
            "comment": "c2_1_g55",
            "symbol": "XAUUSD",
            "type": 0,
            "price": 100.3,
            "volume": 0.04,
            "sl": 70.3,
            "tp": 100.7,
        },
        "result": {
            "deal": 201,
            "order": 101,
            "price": 100.2,
            "volume": 0.04,
            "retcode": 10009,
        },
        "source_tick": {"bid": 100.1, "ask": 100.3},
    }]

    output = controls.bind_actual_openings(
        observed, attempts, {"canal2_1": "BUY"}
    )

    assert output["signals"][0]["openings"][0] == {
        "slot": 0,
        "role": "root",
        "ticket": "101",
        "position_id": "101",
        "open_deal": "201",
        "opening_attempt_id": "open-1",
        "comment": "c2_1_g55",
        "symbol": "XAUUSD",
        "direction": "BUY",
        "opened_at": BASE.isoformat(),
        "open_price": 100.2,
        "volume": 0.04,
        "initial_sl": 70.3,
        "initial_tp": 100.7,
        "initial_protection_source": "observed_successful_open_request",
    }
    encoded = json.dumps(output, sort_keys=True)
    for forbidden in (
        "close_deal", "close_price", "closed_at", "pnl", "retcode",
        "position_before", "source_tick",
    ):
        assert forbidden not in encoded


def test_engine_comparison_ignores_only_scalar_behavior_digest():
    oracle = {"entries": [], "exits": [], "blockers": [], "protection_events": []}
    scalar = dict(oracle, behavior_digest="scalar-only")
    assert controls.engine_mismatch_fields(scalar, oracle) == []

    scalar["blockers"] = ["changed"]
    assert controls.engine_mismatch_fields(scalar, oracle) == ["blockers"]

    del scalar["protection_events"]
    oracle["protection_events"] = None
    assert controls.engine_mismatch_fields(scalar, oracle) == ["blockers", "protection_events"]


def test_attempt_evidence_must_match_full_rows_in_reconstructed_stream(tmp_path):
    attempt = {"ev": "mt5_action_attempt", "attempt_id": "attempt-1", "request": {"sl": 90.0}}
    prefix_rows = [{"ev": "heartbeat"}, attempt]
    delta_rows = [{"ev": "telegram_raw"}]
    prefix = tmp_path / "source_prefix.jsonl"
    delta = tmp_path / "event_delta.jsonl.gz"
    prefix_bytes = b"".join(json.dumps(row, sort_keys=True).encode() + b"\n" for row in prefix_rows)
    delta_bytes = b"".join(json.dumps(row, sort_keys=True).encode() + b"\n" for row in delta_rows)
    prefix.write_bytes(prefix_bytes)
    with gzip.open(delta, "wb") as stream:
        stream.write(delta_bytes)
    reconstructed = hashlib.sha256(prefix_bytes + delta_bytes).hexdigest()

    proof = controls.verify_attempt_evidence(prefix, delta, [attempt], reconstructed)

    assert proof["causal_attempt_rows_verified"] == 1
    changed = dict(attempt, request={"sl": 91.0})
    with pytest.raises(ValueError, match="differs from causal stream"):
        controls.verify_attempt_evidence(prefix, delta, [changed], reconstructed)
    changed_type = dict(attempt, request={"sl": 90})
    with pytest.raises(ValueError, match="differs from causal stream"):
        controls.verify_attempt_evidence(prefix, delta, [changed_type], reconstructed)


def test_comparison_retains_every_signal_profile_pair_when_results_are_missing():
    protocol = {
        "expected_signal_ids": ["canal2_1", "canal2_2"],
        "profiles": [{"name": "instant"}],
        "universe": "actual_entries",
    }
    results = {
        "results": [{
            "signal_id": "canal2_1",
            "profile": "instant",
            "engine_mismatches": {"scalar": [], "fast": []},
            "scalar": {
                "entries": [],
                "exits": [],
                "blockers": ["protection_market_close_latency_unmodeled"],
                "protection_events": [],
            },
        }]
    }
    observed = {
        "signals": [
            {"sig_id": "canal2_1", "channel": "canal2", "direction": "BUY", "positions": []},
            {"sig_id": "canal2_2", "channel": "canal2", "direction": "SELL", "positions": []},
        ]
    }

    report = controls.build_comparison_report(protocol, results, observed, [])

    assert report["denominator"] == {"signals": 2, "profiles": 1, "controls": 2}
    assert [(row["signal_id"], row["profile"]) for row in report["rows"]] == [
        ("canal2_1", "instant"),
        ("canal2_2", "instant"),
    ]
    assert report["statuses"] == {"blocked": 2}
    assert report["missing_controls"] == [{"signal_id": "canal2_2", "profile": "instant"}]


def test_identical_native_entry_and_sltp_exit_is_exact_without_zero_gaps():
    closed = BASE + timedelta(seconds=5)
    protocol = {
        "expected_signal_ids": ["canal2_1"],
        "profiles": [{"name": "instant"}],
        "universe": "actual_entries",
    }
    results = {"results": [{
        "signal_id": "canal2_1",
        "profile": "instant",
        "engine_mismatches": {"scalar": [], "fast": []},
        "scalar": {
            "entries": [{
                "ticket": "101", "opened_at": BASE.isoformat(),
                "entry_price": 100.2, "volume": 0.04,
            }],
            "exits": [{
                "ticket": "101", "closed_at": closed.isoformat(),
                "exit_price": 100.7, "reason": "per_leg_target",
            }],
            "blockers": [],
            "protection_events": [],
        },
    }]}
    observed = {"signals": [{
        "sig_id": "canal2_1",
        "channel": "canal2",
        "direction": "BUY",
        "positions": [{
            "position_id": 101,
            "opened_at_precise": BASE.isoformat(),
            "open_price": 100.2,
            "volume": 0.04,
            "closed_at_precise": closed.isoformat(),
            "close_price": 100.7,
            "close_deal": {"reason": 5},
        }],
    }]}

    report = controls.build_comparison_report(protocol, results, observed, [])

    assert report["statuses"] == {"exact": 1}
    assert report["rows"][0]["comparison"]["gaps"] == {}
    assert report["rows"][0]["comparison"]["exact"] is True


def test_duplicate_simulated_exit_ticket_is_explicitly_blocked():
    closed = BASE + timedelta(seconds=5)
    protocol = {
        "expected_signal_ids": ["canal2_1"],
        "profiles": [{"name": "instant"}],
        "universe": "actual_entries",
    }
    exit_row = {
        "ticket": "101", "closed_at": closed.isoformat(),
        "exit_price": 100.7, "reason": "per_leg_target",
    }
    results = {"results": [{
        "signal_id": "canal2_1",
        "profile": "instant",
        "engine_mismatches": {"scalar": [], "fast": []},
        "scalar": {
            "entries": [{
                "ticket": "101", "opened_at": BASE.isoformat(),
                "entry_price": 100.2, "volume": 0.04,
            }],
            "exits": [exit_row, dict(exit_row)],
            "blockers": [],
            "protection_events": [],
        },
    }]}
    observed = {"signals": [{
        "sig_id": "canal2_1",
        "channel": "canal2",
        "direction": "BUY",
        "positions": [{
            "position_id": 101,
            "opened_at_precise": BASE.isoformat(),
            "open_price": 100.2,
            "volume": 0.04,
            "closed_at_precise": closed.isoformat(),
            "close_price": 100.7,
            "close_deal": {"reason": 5},
        }],
    }]}

    report = controls.build_comparison_report(protocol, results, observed, [])

    assert report["statuses"] == {"blocked": 1}
    assert "duplicate_simulated_exit_ticket:101" in report["rows"][0]["blockers"]
    assert report["rows"][0]["comparison"]["gaps"] == {"extra_simulated_exits": 1}
