from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

from tools import compare_simulator_forward as comparison


START = comparison._utc("2026-09-09T06:30:00+00:00")
END = START + timedelta(hours=2)
CAPTURE_IDENTITY = {"collector_sha256": "c" * 64, "wrapper_sha256": "d" * 64,
                    "expected_live_commit": "e" * 40, "expected_account_identity_sha256": "a" * 64}


def save(path, value):
    raw = comparison._encode(value)
    path.write_bytes(raw)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def deal(ticket=11, *, position=101, entry=0, at=None, volume=".04", profit="0", comment="c2_1_g55"):
    at = at or START + timedelta(seconds=1 + entry)
    millis = (at + timedelta(hours=3) - comparison.EPOCH) // timedelta(milliseconds=1)
    return {"ticket": ticket, "position_id": position, "order": ticket + 1000,
            "entry": entry, "time": millis // 1000, "time_msc": millis,
            "time_utc": at.isoformat(),
            "symbol": "XAUUSD", "magic": 20260422, "type": 0 if entry == 0 else 1,
            "reason": 3 if entry == 0 else 5, "comment": comment,
            "volume": volume, "price": "100" if entry == 0 else "101",
            "profit": profit, "swap": "0", "commission": "0", "fee": "0"}


def order(row):
    return {"ticket": row["order"], "position_id": row["position_id"],
            "symbol": row["symbol"], "magic": row["magic"], "type": row["type"],
            "reason": row["reason"], "volume_initial": row["volume"], "volume_current": "0",
            "state": 4, "time_done": row["time"], "time_done_msc": row["time_msc"],
            "time_done_utc": row["time_utc"]}


def native_fixture(tmp_path, *, events=None, deals=None):
    from research.causal_replay import RAW_FIELDS
    events = events if events is not None else [{"ev": "telegram_raw", "ts": START.isoformat(),
        "date_utc": START.isoformat(), "channel": "canal2", "message_id": 1,
        "message_revision_id": "revision-1", "text": "BUY GOLD NOW"}]
    deals = deals if deals is not None else [deal(), deal(12, entry=1, profit="4")]
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    dataset_path = tmp_path / "dataset"
    dataset_path.mkdir()
    raw = b"".join(comparison._encode(row) + b"\n" for row in events)
    (capture_dir / "event_delta.jsonl.gz").write_bytes(gzip.compress(raw))
    save(capture_dir / "prior_context.json", {})
    save(capture_dir / "broker_deals.json", {"rows": deals})
    save(capture_dir / "broker_orders.json", {"rows": [order(row) for row in deals]})
    files = {path.name: {"bytes": path.stat().st_size, "sha256": comparison._digest(path)}
             for path in capture_dir.iterdir()}
    symbols = {"XAUUSD": {"point": .01, "digits": 2, "contract_size": 100.0,
                         "stops_level_points": 20, "freeze_level_points": 0,
                         "volume_min": .01, "volume_max": 100.0, "volume_step": .01},
               "EURUSD": {"point": .00001, "digits": 5}}
    native_symbols = deepcopy(symbols)
    for key, alias in (("contract_size", "trade_contract_size"), ("stops_level_points", "trade_stops_level"),
                       ("freeze_level_points", "trade_freeze_level")):
        native_symbols["XAUUSD"][alias] = native_symbols["XAUUSD"].pop(key)
    manifest = {"contract": "morning_causal_capture_v1", "window_start_utc": START,
        "window_end_exclusive_utc": END, "event_cutoff_utc": END,
        "raw_server_epoch_minus_utc_seconds": 10800, "read_only": True,
        "orders_sent_by_collector": 0, "blockers": [],
        "symbol_contract": native_symbols,
        "live_code": {"collector_sha256": "c" * 64, "wrapper_sha256": "d" * 64, "commit": "e" * 40, "clean": True},
        "account": {"currency": "EUR", "terminal_connected": True,
                    "account_identity_sha256": "a" * 64}, "files": files,
        "event_evidence": {"delta_bytes": len(raw), "delta_rows": len(events),
                           "delta_sha256": hashlib.sha256(raw).hexdigest(), "invalid_delta_rows": []},
        "broker_evidence": {"deal_rows": len(deals), "order_rows": len(deals)}}
    manifest_path = capture_dir / "manifest.json"
    save(manifest_path, manifest)
    metadata = save(dataset_path / "metadata.json", {"account_currency": "EUR", "symbols": symbols})
    clock = save(dataset_path / "clock.json", {"broker_epoch_offset_seconds": 10800})
    dataset = {"start_utc": START, "cutoff_utc": END,
               "source_evidence": {"metadata": metadata, "clock": clock}}
    messages = [{key: row.get(key) for key in RAW_FIELDS} for row in events if row.get("ev") == "telegram_raw"]
    save(dataset_path / "raw_messages.json", messages)
    return manifest_path, dataset, dataset_path


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(comparison, "ROOT", tmp_path)


def test_independent_block_prevents_any_capture_read(tmp_path, monkeypatch):
    monkeypatch.setattr(comparison, "_independent", lambda *args: {
        "report": {"status": "blocked", "blockers": ["independent_mismatch"],
                   "eligible_signal_ids": ["canal2_1", "canal2_2"]}})
    monkeypatch.setattr(comparison, "_read", lambda *args, **kwargs: pytest.fail("observed read before admission"))
    report = comparison.compare(tmp_path / "protocol", tmp_path / "dataset", tmp_path / "results",
                                manifests=[tmp_path / "DO_NOT_READ"], out=tmp_path / "out")
    assert report["observed_reads"] is False
    assert report["eligible_signal_ids"] == ["canal2_1", "canal2_2"]
    assert not (tmp_path / "out").exists()


def test_module_import_has_no_observed_or_live_imports():
    root = Path(__file__).resolve().parents[1]
    script = ("import sys; sys.path.insert(0,sys.argv[1]); from tools import compare_simulator_forward; "
              "assert not any(k in sys.modules for k in ('MetaTrader5','executor','gold_555_live_candidate',"
              "'research.gold_iterative.ledger_evidence','tools.audit_causal_lineage'))")
    run = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(root)], capture_output=True)
    assert run.returncode == 0, run.stderr.decode()


def test_native_normalizer_binds_same_causal_messages_and_source_hashes(tmp_path):
    manifest, dataset, data = native_fixture(tmp_path)
    watched = {}
    capture = comparison._capture([manifest], dataset, data, watched)
    assert len(capture["deals"]) == 2
    assert capture["account_currency"] == "EUR" and capture["offset"] == 10800
    assert capture["capture_proofs"][0]["sha256"] == comparison._digest(manifest)
    assert manifest.resolve() in watched


@pytest.mark.parametrize("field,value", [
    ("raw_server_epoch_minus_utc_seconds", 0), ("event_cutoff_utc", START),
    ("orders_sent_by_collector", 1), ("read_only", False),
])
def test_native_contract_drift_blocks(tmp_path, field, value):
    manifest, dataset, data = native_fixture(tmp_path)
    content = comparison._decode(manifest.read_bytes())
    content[field] = value
    save(manifest, content)
    with pytest.raises(ValueError):
        comparison._capture([manifest], dataset, data, {})


def test_capture_currency_does_not_inherit_eur_from_independent_assumption(tmp_path):
    manifest, dataset, data = native_fixture(tmp_path)
    content = comparison._decode(manifest.read_bytes())
    content["account"]["currency"] = "USD"
    save(manifest, content)
    with pytest.raises(ValueError, match="account EUR"):
        comparison._capture([manifest], dataset, data, {})


def test_native_outcomes_cannot_replace_independent_causal_messages(tmp_path):
    manifest, dataset, data = native_fixture(tmp_path)
    save(data / "raw_messages.json", [])
    with pytest.raises(ValueError, match="causal messages differ"):
        comparison._capture([manifest], dataset, data, {})


def test_native_file_mutation_blocks_before_accounting(tmp_path):
    manifest, dataset, data = native_fixture(tmp_path)
    save(manifest.parent / "broker_deals.json", {"rows": []})
    with pytest.raises(ValueError, match="hash mismatch"):
        comparison._capture([manifest], dataset, data, {})


def test_normalized_native_time_cannot_contradict_raw_milliseconds(tmp_path):
    rows = [deal(), deal(12, entry=1)]
    rows[1]["time_utc"] = END.isoformat()
    manifest, dataset, data = native_fixture(tmp_path, deals=rows)
    with pytest.raises(ValueError, match="normalized UTC"):
        comparison._capture([manifest], dataset, data, {})


def test_native_symbol_metadata_must_match_prevalidated_independent_profile(tmp_path):
    manifest, dataset, data = native_fixture(tmp_path)
    content = comparison._decode(manifest.read_bytes())
    content["symbol_contract"]["XAUUSD"]["volume_step"] = .02
    save(manifest, content)
    with pytest.raises(ValueError, match="symbol metadata mismatch"):
        comparison._capture([manifest], dataset, data, {})


def test_capture_chain_rejects_missing_or_reordered_anchor(tmp_path):
    manifest, dataset, data = native_fixture(tmp_path)
    with pytest.raises(ValueError, match="not contiguous"):
        comparison._capture([manifest, manifest], dataset, data, {})


def bind_source_capture(manifest, dataset):
    proof = {"path": str(manifest), "sha256": comparison._digest(manifest)}
    dataset["source_evidence"]["capture_manifest"] = proof
    for key in ("metadata", "clock"):
        path = Path(dataset["source_evidence"][key]["path"])
        value = comparison._decode(path.read_bytes())
        value["source_capture_sha256"] = proof["sha256"]
        dataset["source_evidence"][key] = save(path, value)


@pytest.mark.parametrize("key", list(CAPTURE_IDENTITY))
def test_changed_capture_helper_live_commit_or_account_blocks(tmp_path, key):
    manifest, dataset, data = native_fixture(tmp_path)
    bind_source_capture(manifest, dataset)
    contract = dict(CAPTURE_IDENTITY, **{key: "f" * 64})
    with pytest.raises(ValueError, match="frozen collector/live/account"):
        comparison._capture([manifest], dataset, data, {}, capture_contract=contract)


def test_comparator_and_independent_metadata_must_bind_identical_source_capture(tmp_path):
    manifest, dataset, data = native_fixture(tmp_path)
    bind_source_capture(manifest, dataset)
    assert comparison._capture([manifest], dataset, data, {}, capture_contract=CAPTURE_IDENTITY)
    dataset["source_evidence"]["capture_manifest"]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="source_capture"):
        comparison._capture([manifest], dataset, data, {}, capture_contract=CAPTURE_IDENTITY)


def test_final_capture_lag_keeps_inclusive_effective_cutoff_and_excluded_count(tmp_path):
    events = [{"ev": "heartbeat", "ts": END.isoformat()},
              {"ev": "heartbeat", "ts": (END + timedelta(seconds=1)).isoformat()}]
    manifest, dataset, data = native_fixture(tmp_path, events=events, deals=[])
    value = comparison._decode(manifest.read_bytes())
    value["event_cutoff_utc"] = END + timedelta(seconds=2)
    save(manifest, value)
    capture = comparison._capture([manifest], dataset, data, {})
    assert len(capture["events"]) == 1 and comparison._utc(capture["events"][0]["ts"]) == END
    assert capture["source_counts"]["events_after_effective_cutoff"] == 1


def test_context_copy_deduplication_never_drops_duplicate_journal_events(tmp_path):
    event = {"ev": "session_started", "event_id": "event_same", "ts": START.isoformat()}
    manifest, dataset, data = native_fixture(tmp_path, events=[event, event], deals=[])
    save(manifest.parent / "prior_context.json", {"session_started": event})
    content = comparison._decode(manifest.read_bytes())
    context = manifest.parent / "prior_context.json"
    content["files"][context.name] = {"bytes": context.stat().st_size, "sha256": comparison._digest(context)}
    save(manifest, content)
    capture = comparison._capture([manifest], dataset, data, {})
    assert len(capture["events"]) == 2


def test_native_subprocess_timeout_is_an_explicit_block(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("native", kwargs["timeout"])
    monkeypatch.setattr(comparison.subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        comparison._native_process({}, [], {}, remaining=.001)


def test_real_native_subprocess_preserves_unverified_rows(monkeypatch):
    monkeypatch.setattr(comparison, "ROOT", Path(__file__).resolve().parents[1])
    capture = {"events": [], "deals": [], "orders": [], "offset": 10800, "blockers": []}
    results = {"results": [{"signal_id": "canal2_1", "scalar": {}}]}
    evidence = comparison._native_process(capture, ["canal2_1"], results, remaining=30)
    row = evidence["rows"][0]
    assert row["signal_id"] == "canal2_1" and row["observed_net_eur"] is None
    assert row["lifecycle_checks"]["installed_protection"]["status"] == "not_observed"
    assert evidence["journal"]["summary"]["blocked"]


def test_cutoff_is_inclusive_for_native_deals(tmp_path):
    deals = [deal(), deal(12, entry=1, at=END), deal(13, position=102, at=END + timedelta(milliseconds=1))]
    manifest, dataset, data = native_fixture(tmp_path, deals=deals)
    capture = comparison._capture([manifest], dataset, data, {})
    assert [row["ticket"] for row in capture["deals"]] == [11, 12]
    assert capture["source_counts"]["deals"] == 3


def test_ledger_normalization_reconciles_partial_costs_per_ticket_and_retains_zero():
    rows = [deal(), deal(12, entry=1, volume=".02", profit="2"),
            deal(13, entry=1, volume=".02", profit="-2")]
    before = deepcopy(rows)
    ledgers, owners, blockers, excluded = comparison._ledgers(rows, ["canal2_1", "canal2_2"], 10800)
    assert not blockers and not excluded
    assert owners == {"101": "canal2_1"}
    assert not ledgers["canal2_1"]["blockers"]
    assert ledgers["canal2_1"]["actual"]["pnl_real_mt5"] == Decimal("0.00")
    assert len(ledgers["canal2_1"]["ticket_evidence"]["101"]["exit_deals"]) == 2
    assert set(ledgers) == {"canal2_1", "canal2_2"}
    assert rows == before


def test_missing_fee_is_unknown_not_zero():
    rows = [deal(), deal(12, entry=1, profit="4")]
    del rows[1]["fee"]
    ledgers, _, _, _ = comparison._ledgers(rows, ["canal2_1"], 10800)
    assert ledgers["canal2_1"]["actual"]["pnl_real_mt5"] is None
    assert any("cost_missing:fee" in reason for reason in ledgers["canal2_1"]["blockers"])


def test_position_still_open_is_retained_and_blocked():
    ledgers, owners, _, _ = comparison._ledgers([deal()], ["canal2_1"], 10800)
    assert owners == {"101": "canal2_1"}
    assert len(ledgers["canal2_1"]["actual"]["positions"]) == 1
    assert "ledger_position_not_closed:101" in ledgers["canal2_1"]["blockers"]


def test_duplicate_native_identity_is_not_silently_dropped():
    rows = [deal(), deal(12, entry=1), deal(12, entry=1)]
    ledgers, _, blockers, _ = comparison._ledgers(rows, ["canal2_1"], 10800)
    assert "native_deal_identity_invalid_or_duplicate" in blockers
    assert ledgers["canal2_1"]["blockers"]


def opening_attempt(opening):
    at = comparison._broker_time(opening, 10800)
    return {"ev": "mt5_action_attempt", "operation": "OPEN_MARKET", "sig": "canal2_1",
            "attempt_id": "attempt_a", "broker_request_sent": True,
            "broker_request_started_utc": at - timedelta(milliseconds=10),
            "broker_response_received_utc": at + timedelta(milliseconds=10),
            "ts": at + timedelta(milliseconds=11),
            "request": {"action": 1, "symbol": "XAUUSD", "magic": 20260422, "type": 0, "volume": ".04"},
            "result": {"retcode": 10009, "deal": opening["ticket"], "order": opening["order"],
                       "price": opening["price"], "volume": ".04"}}


def test_open_native_binding_requires_price_volume_clock_and_identity():
    opening = deal()
    event = opening_attempt(opening)
    args = ([event], [opening], [order(opening)], {"101": "canal2_1"}, 10800)
    assert comparison._open_bindings(*args)[0]["blockers"] == []
    event["result"]["price"] = "101"
    assert comparison._open_bindings(*args)[0]["blockers"]


def test_empty_and_unknown_install_keep_all_selected_ids_and_null_money(tmp_path):
    capture = {"events": [], "deals": [], "orders": [], "offset": 10800, "blockers": []}
    results = {"results": [{"signal_id": sig, "scalar": {}} for sig in ("canal2_1", "canal2_2")]}
    report = comparison._observed(capture, ["canal2_1", "canal2_2"], results)
    assert [row["signal_id"] for row in report["rows"]] == ["canal2_1", "canal2_2"]
    for row in report["rows"]:
        assert row["observed_entries"] == row["observed_exits"] == 0
        assert row["observed_net_eur"] is None
        assert row["lifecycle_checks"]["installed_protection"]["status"] == "not_observed"
        assert "observed_server_installation_unverified" not in row["blockers"]
        assert row["coverage_gaps"]


def test_native_preparation_failure_keeps_complete_unknown_denominator():
    evidence = comparison._blocked_native(["canal2_1", "canal2_2"], "native_hash_mismatch")
    assert [row["signal_id"] for row in evidence["rows"]] == ["canal2_1", "canal2_2"]
    for row in evidence["rows"]:
        assert row["observed_net_eur"] is None and not row["observed_counts_complete"]
        assert row["blockers"] == ["native_hash_mismatch"]
        assert all(check["status"] == "blocked" for check in row["lifecycle_checks"].values())


def installed_fixture(*, deferred=False):
    ledger, _, _, _ = comparison._ledgers([deal(), deal(12, entry=1)], ["canal2_1"], 10800)
    common = {"sig": "canal2_1", "ticket": 101, "action_id": "action_a", "decision_id": "decision_a",
              "message_revision_id": "msgrev_a", "action_revision": 0}
    wanted_sl = 99 if deferred else 98
    root = dict(common, ev="mt5_modify_requested", new_sl=wanted_sl, new_tp=105,
                ts=(START + timedelta(seconds=1.1)).isoformat())
    attempt = dict(common, ev="mt5_action_attempt", operation="MODIFY_SLTP", attempt_id="attempt_a",
        broker_request_sent=True, result={"retcode": 10009},
        request={"action": 6, "position": 101, "sl": 98, "tp": 105},
        preflight_effective_sl=None if deferred else 98, preflight_effective_tp=105,
        preflight_deferred_sl=99 if deferred else None,
        broker_response_received_utc=(START + timedelta(seconds=1.3)).isoformat())
    snapshot = dict(common, ev="mt5_position_snapshot", after_action="MODIFY_SLTP", attempt_id="attempt_a",
        retcode=10009, requested_sl=wanted_sl, requested_tp=105, position_exists=True,
        symbol="XAUUSD", magic=20260422, position_type=0, volume=.04, price_open=100,
        sl=98, tp=105, ts=(START + timedelta(seconds=1.4)).isoformat())
    return [root, attempt, snapshot], ledger["canal2_1"]["ticket_evidence"]


@pytest.mark.parametrize("deferred", [False, True])
def test_installed_protection_native_snapshot_verifies_state_not_server_time(deferred):
    events, evidence = installed_fixture(deferred=deferred)
    result = comparison._installed_state(events, "canal2_1", evidence, lineage_ok=True)
    assert result["status"] == "verified" and result["scope"] == "observed_state"
    assert result["server_install_time"] is None and result["coverage_gaps"] == []
    assert result["observations"][0]["sl"] == 98


@pytest.mark.parametrize("field,value", [("ticket", 102), ("sl", 99), ("tp", 104),
    ("action_revision", 1), ("action_id", "action_wrong"), ("requested_sl", None),
    ("symbol", "EURUSD"), ("volume", .03), ("price_open", 99), ("retcode", 10016)])
def test_contradictory_snapshot_blocks_instead_of_becoming_missing_coverage(field, value):
    events, evidence = installed_fixture()
    events[-1][field] = value
    result = comparison._installed_state(events, "canal2_1", evidence, lineage_ok=True)
    assert result["status"] == "blocked" and result["blockers"]


def test_missing_snapshot_is_coverage_gap_not_installation_failure():
    events, evidence = installed_fixture()
    result = comparison._installed_state(events[:-1], "canal2_1", evidence, lineage_ok=True)
    assert result["status"] == "not_observed" and result["blockers"] == []
    assert result["scope"] == "observed_state" and result["server_install_time"] is None
    assert result["coverage_gaps"] == ["installed_protection_snapshot_missing:101"]


@pytest.mark.parametrize("exit_at", [1.3, 1.35, 1.4])
def test_exit_inside_snapshot_read_log_interval_is_coverage_gap_not_contradiction(exit_at):
    events, evidence = installed_fixture()
    close_at = (START + timedelta(seconds=exit_at)).isoformat()
    evidence["101"]["closed_at"] = close_at
    evidence["101"]["exit_deals"][0]["closed_at"] = close_at
    result = comparison._installed_state(events, "canal2_1", evidence, lineage_ok=True)
    assert result["status"] == "not_observed" and result["blockers"] == []
    assert "snapshot_time_ambiguous" in result["coverage_gaps"]
    assert result["server_install_time"] is None


def test_snapshot_of_position_closed_before_response_interval_is_contradiction():
    events, evidence = installed_fixture()
    evidence["101"]["closed_at"] = (START + timedelta(seconds=1.2)).isoformat()
    result = comparison._installed_state(events, "canal2_1", evidence, lineage_ok=True)
    assert result["status"] == "blocked"


def test_ambiguous_snapshot_time_cannot_hide_level_contradiction():
    events, evidence = installed_fixture()
    evidence["101"]["exit_deals"][0]["closed_at"] = (START + timedelta(seconds=1.35)).isoformat()
    events[-1]["sl"] = 90
    result = comparison._installed_state(events, "canal2_1", evidence, lineage_ok=True)
    assert result["status"] == "blocked"


def test_pre_window_opening_context_identifies_unrelated_exit_without_global_gold_block(tmp_path):
    earlier = deal(21, position=202, at=START - timedelta(hours=1), comment="c1_99_dv1")
    foreign_exit = deal(22, position=202, entry=1, comment="[sl]")
    earlier["magic"] = foreign_exit["magic"] = 20260421
    manifest, dataset, data = native_fixture(tmp_path, deals=[earlier, foreign_exit, deal(), deal(12, entry=1)])
    capture = comparison._capture([manifest], dataset, data, {})
    assert earlier in capture["deals"]
    ledgers, owners, blockers, excluded = comparison._ledgers(capture["deals"], ["canal2_1"], 10800)
    assert blockers == [] and owners == {"101": "canal2_1"}
    assert len(excluded) == 1 and excluded[0]["position_id"] == "202"
    assert ledgers["canal2_1"]["blockers"] == []


def test_cli_json_ignores_global_print_timestamp_patch(monkeypatch, capsys):
    import builtins
    monkeypatch.setattr(builtins, "print", lambda *args, **kwargs: pytest.fail("public JSON used patched print"))
    monkeypatch.setattr(comparison, "compare", lambda *args, **kwargs: {"status": "incomplete"})
    assert comparison.main(["--protocol", "p", "--dataset", "d", "--results", "r",
                            "--capture-manifest", "c", "--out", "o"]) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "incomplete"}


def test_hypothesis_difference_does_not_relabel_native_money_as_inexact():
    rows = [deal(), deal(12, entry=1, profit="4")]
    capture = {"events": [], "deals": rows, "orders": [order(row) for row in rows],
               "offset": 10800, "blockers": []}
    scalar = {"blockers": [], "entries": [{"ticket": "sim_1", "source": "signal_market",
        "opened_at": START + timedelta(seconds=1), "entry_price": 100.5, "volume": .04}],
        "exits": [{"ticket": "sim_1", "closed_at": START + timedelta(seconds=2),
                   "exit_price": 102, "volume": .04, "pnl_eur": 5, "reason": "fixed_sl"}]}
    report = comparison._observed(capture, ["canal2_1"], {"results": [{"signal_id": "canal2_1", "scalar": scalar}]})
    row = report["rows"][0]
    assert row["status"] == "differences_for_review"
    assert row["differences"]
    assert row["lifecycle_checks"]["native_accounting"]["status"] == "exact"
    assert row["lifecycle_checks"]["request_native_binding"]["status"] == "blocked"
    assert row["observed_net_eur"] == Decimal("4.00")


def test_duplicate_json_and_nonfinite_values_fail_closed():
    for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
        with pytest.raises(ValueError):
            comparison._decode(raw)


def test_transitive_comparison_sources_are_explicit_and_bounded():
    assert len(comparison.COMPARISON_SOURCES) < 30
    assert len(set(comparison.COMPARISON_SOURCES)) == len(comparison.COMPARISON_SOURCES)
    for source in ("research/gold_iterative/ledger_evidence.py", "research/gold_iterative/exit_execution.py",
                   "tools/audit_causal_lineage.py", "tools/audit_management_capture.py",
                   "mt5_deal_reason.py", "research/causal_comparison.py"):
        assert source in comparison.COMPARISON_SOURCES


def test_real_independent_subprocess_then_native_output_consumed_by_forward_contract(monkeypatch):
    """Synthetic future clock/data only; real three-engine execution and child guard."""
    from tools import prepare_simulator_forward as forward
    from tools import run_protection_controls as controls
    from tools import run_simulator_forward as runner
    from tests.test_run_simulator_forward import _inputs
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(comparison, "ROOT", root)
    monkeypatch.setattr(forward, "_now", lambda: forward.START - timedelta(hours=1))
    # Collection in this process has imported observed auditors. The child uses
    # the untouched real FORBIDDEN_IMPORTS and must remain clean.
    monkeypatch.setattr(controls, "FORBIDDEN_IMPORTS", {"test_only_forbidden_sentinel"})
    original_run = subprocess.run
    def synthetic_clock(args, **kwargs):
        if "-c" in args and "report = f.check" in args[args.index("-c") + 1]:
            args = list(args)
            index = args.index("-c") + 1
            args[index] = args[index].replace("report = f.check", "f._now = lambda: f.END\nreport = f.check")
        return original_run(args, **kwargs)
    monkeypatch.setattr(comparison.subprocess, "run", synthetic_clock)
    with tempfile.TemporaryDirectory(prefix="test-forward-native-", dir=root / "runtime_data") as temporary:
        work = Path(temporary)
        identity = forward.identity()
        verification = save(work / "synthetic-verification.json", {
            "status": "local_suite_verified", "implementation_sha256": identity["controls"]["iterative"]["sha256"],
            "exit_code": 0, "same_implementation": True, "changed_sources": [], "changed_wrappers": [],
            "suite": {"tests": 1, "failures": 0, "errors": 0, "skipped": 0},
            "completed_at_utc": forward.START - timedelta(hours=2),
            "fixture_only": True,
        })
        sources = tuple(dict.fromkeys((*comparison.COMPARISON_SOURCES, forward.RUNNER_SOURCE, runner.COLLECTOR_SOURCE)))
        capture_identity = dict(CAPTURE_IDENTITY, collector_sha256=comparison._digest(root / runner.COLLECTOR_SOURCE))
        profile = work / "profile.json"
        save(profile, {"contract": "simulator_forward_profile_v1",
            "execution": {"latency_ms": 0, "entry_fill_latency_ms": 0,
                          "protection": dict(controls.PROFILE_SPECS[0]),
                          "market": {"entry_acknowledgement_delay_ms": 0, "close_processing_delay_ms": 0,
                                     "close_acknowledgement_delay_ms": 0, "volume_min": .01,
                                     "volume_max": 100.0, "volume_step": .01}},
            "capabilities": dict.fromkeys(forward.CAPABILITIES, "verified"), "verification": verification,
            "capture_contract": capture_identity,
            "additional_sources": [{"path": str(root / source), "sha256": comparison._digest(root / source)}
                                   for source in sources]})
        prepared = forward.prepare(profile, out=work / "frozen", now=forward.START - timedelta(hours=1))
        assert prepared["status"] == "frozen_waiting_for_cohort"
        protocol = Path(prepared["protocol"])
        events, tapes, clock, metadata = _inputs(work)
        native_work = work / "native"
        native_work.mkdir()
        event_rows = [json.loads(line) for line in events.read_text().splitlines()
                      if json.loads(line).get("ev") == "telegram_raw"]
        manifest, _, _ = native_fixture(native_work, events=event_rows, deals=[])
        manifest_value = comparison._decode(manifest.read_bytes())
        manifest_value["live_code"].update(clean=True, collector_sha256=capture_identity["collector_sha256"])
        manifest_value.update(status="captured", normal_operations_only=True,
                              completed_at_utc=forward.END, tick_evidence={})
        for symbol, contract in manifest_value["symbol_contract"].items():
            defaults = {"volume_min": .01, "volume_max": 100., "volume_step": .01,
                        "trade_stops_level": 0, "trade_freeze_level": 0,
                        "trade_contract_size": 100000., "currency_base": "EUR",
                        "currency_profit": "USD", "currency_margin": "EUR", "filling_mode": 2, "order_mode": 127}
            for key, value in defaults.items():
                contract.setdefault(key, value)
            destination = manifest.parent / f"{symbol}.parquet"
            shutil.copyfile(tapes[symbol], destination)
            manifest_value["tick_evidence"][symbol] = {"status": "captured", "rows": 12,
                "parquet_sha256": comparison._digest(destination), "window_end_exclusive_utc": forward.END}
        save(manifest.parent / "symbol_metadata.json", manifest_value["symbol_contract"])
        save(manifest.parent / "raw_messages_window.json",
             [{key: row.get(key) for key in runner.RAW_FIELDS} for row in event_rows])
        millis = (forward.END + timedelta(hours=3) - comparison.EPOCH) // timedelta(milliseconds=1)
        manifest_value["clock_samples"] = [{"sample": index, "symbol": symbol, "observed_utc": forward.END,
            "offset_seconds": 10800, "raw_time_msc": millis, "residual_seconds": 0, "bid": 100, "ask": 100.2}
            for index in range(3) for symbol in ("XAUUSD", "EURUSD")]
        manifest_value["event_evidence"].update(raw_messages_file="raw_messages_window.json",
            raw_messages_sha256=comparison._digest(manifest.parent / "raw_messages_window.json"),
            raw_message_causal_availability={"window_rows_cumulative": len(event_rows),
                                            "missing_fields": {}, "revision_conflicts": []})
        manifest_value["files"] = {path.name: {"bytes": path.stat().st_size, "sha256": comparison._digest(path)}
            for path in manifest.parent.iterdir() if path.name != "manifest.json"}
        save(manifest, manifest_value)
        produced = runner.produce(protocol, capture=manifest, cutoff=forward.END,
                                  out=work / "dataset", now=forward.END)
        assert produced["status"] == "dataset_ready"
        execution = forward.run(protocol, work / "dataset", out=work / "run", now=forward.END)
        result_path = Path(execution["results"])
        gate = comparison._independent(protocol, work / "dataset", result_path)
        assert not gate["report"]["blockers"]
        assert gate["report"]["eligible_signal_ids"] == ["canal2_1", "canal2_2"]
        assert str(protocol.resolve()) in gate["inputs"]
        # Native evidence has no deals: every selected signal remains a blocked
        # zero-count row, never invented P/L or a handcrafted matching result.
        compared = comparison.compare(protocol, work / "dataset", result_path,
                                      manifests=[manifest], out=work / "comparison")
        assert compared["status"] == "blocked" and compared["denominator"] == 2
        report = comparison._decode(Path(compared["comparison"]).read_bytes())
        assert report["approved_tolerances"] is None
        assert report["full_live_parity_verified"] is False
        assert report["quantitative_agreement_admitted"] is False
        assert report["native_factual_review_status"] == "incomplete"
        assert "server_protection_installation_instant_unobserved" in report["capability_gaps"]
        native_output = comparison._decode((work / "comparison" / "native_evidence.json").read_bytes())
        assert "journal" in native_output, native_output
        bound_protocol, protocol_sha = controls.read_frozen(protocol)
        blockers, counts, reviews, gaps = forward._observed(compared["comparison"], bound_protocol,
            protocol_sha, comparison._digest(result_path), ["canal2_1", "canal2_2"], {})
        assert counts == {"observed_entries": 0, "observed_exits": 0}
        assert len(reviews) == 2 and blockers
        final = forward.check(protocol, work / "dataset", stage="observed", results=result_path,
                              comparison=compared["comparison"], now=forward.END)
        assert final["eligible_signal_ids"] == ["canal2_1", "canal2_2"]
        assert final["status"] == "blocked" and final["counts"]["observed_entries"] == 0
        assert not final["quantitative_agreement_admitted"] and not final["full_live_parity_verified"]
        original_report = Path(compared["comparison"]).read_bytes()
        with pytest.raises(ValueError, match="never overwrite"):
            comparison.compare(protocol, work / "dataset", result_path,
                               manifests=[manifest], out=work / "comparison")
        assert Path(compared["comparison"]).read_bytes() == original_report
        # The runner/replayer is not merely accepting matching supplied JSON.
        payload = comparison._decode(result_path.read_bytes())
        payload["results"][0]["scalar"]["pnl_eur"] = 999
        save(result_path, payload)
        bad = comparison._independent(protocol, work / "dataset", result_path)
        assert "independent_results_differ_from_local_replay" in bad["report"]["blockers"]
        deferred = comparison.compare(protocol, work / "dataset", result_path,
            manifests=[work / "DO_NOT_READ_MISSING_CAPTURE"], out=work / "bad-comparison")
        assert deferred["observed_reads"] is False
        assert not (work / "bad-comparison").exists()
