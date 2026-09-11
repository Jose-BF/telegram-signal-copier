import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import audit_causal_lineage, audit_management_capture as capture


ROOT = Path(__file__).resolve().parents[1]
STATE_FIELDS = (
    "status", "requested_close_reason", "all_filled_tickets", "pending_tickets",
    "candidate_hard_stops", "candidate_entry_prices_by_ticket",
    "basket_guard_armed", "basket_guard_triggered", "basket_guard_peak_pl",
    "basket_guard_trigger_reason", "basket_guard_recovery_pending",
    "basket_guard_close_tickets", "candidate_first_fill_at", "timestamp",
)
GOLD_HASH = "555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0"
DUBAI_HASH = "32cb5c0fe8205ad00a0c655bacd5446c6cc219d1ad7338967212c71781860631"
KINDS = {
    "gold_555_trailing": {"bid": 4050.0, "ask": 4050.1, "tick_time_msc": 1788843600000, "open_tickets": [101]},
    "gold_555_leg_protection": {"ticket": 101, "fill_price": 4050.0, "leg_index": 0},
    "gold_555_basket_guard": {"summary": {"floating_profit": 2.0}, "now_utc": "2026-09-08T05:00:00+00:00"},
    "dubai_basket_guard": {"summary": {"floating_profit": 2.0}, "now_utc": "2026-09-08T05:00:00+00:00"},
}


def rehash(row):
    semantic = {k: v for k, v in row.items() if k not in audit_causal_lineage.ENVELOPE_FIELDS}
    row["payload_sha256"] = hashlib.sha256(json.dumps(
        semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    return row


def chain(kind="gold_555_trailing", *, action=False):
    channel = "canal1" if kind == "dubai_basket_guard" else "canal2"
    identity = dict(channel=channel, chat_id=-10001, message_id=380, revision_token="new", update_kind="new")
    raw_text = "BUY NOW"
    text_hash = hashlib.sha1(raw_text.encode()).hexdigest()
    revision = "msgrev_" + hashlib.sha256(json.dumps({
        "chat_id": -10001, "message_id": 380, "revision_token": "new",
        "text_sha1": text_hash, "media_sha256": None,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    rows = []

    def add(ev, **fields):
        row = dict(schema_version=2, event_id=f"event_{len(rows)}", session_id="session_test",
                   ts=f"2026-09-08T05:00:00.{len(rows):03d}+00:00", monotonic_ns=100 + len(rows),
                   code_commit="a" * 40, sig=f"{channel}_380", ev=ev, message_revision_id=revision, **fields)
        rows.append(rehash(row))

    add("telegram_raw", **identity, is_edit=False, has_text=True, text=raw_text,
        text_sha1=text_hash, has_media=False, media_sha256=None)
    add("telegram_decision_started", **identity, decision_id="decision_parent")
    add("telegram_processed", **identity, decision_id="decision_parent", declared_action_ids=[], declared_action_count=0)
    common = dict(management_contract="management_decision_inputs_v1", management_kind=kind,
                  strategy_id="dubai_balanced_v1" if channel == "canal1" else "gold_now_555_v1",
                  strategy_fingerprint=DUBAI_HASH if channel == "canal1" else GOLD_HASH,
                  direction="BUY", decision_id="decision_management", parent_decision_id="decision_parent", decision_reason=kind)
    state = dict.fromkeys(STATE_FIELDS)
    add("bot_internal_decision_started", **common, decision_inputs=copy.deepcopy(KINDS[kind]),
        state_before=state.copy(), observed_at_utc="2026-09-08T05:00:00.003+00:00")
    if action:
        details = dict(decision_id="decision_management", action_id="action_modify", action_revision=0,
                       ticket=101, new_sl=4040.0, new_tp=4059.0, expected_magic=20260421 if channel == "canal1" else 20260422)
        add("mt5_modify_requested", **details)
        add("mt5_action_failed", **details, attempt_id=None, kind="MODIFY_SLTP", attempts=0,
            last_retcode=None, reason="expired before first attempt", label="SL", age_seconds=60.0)
    add("bot_internal_decision", **common, decision_status="completed", error_type=None,
        decision_result=None, state_after=state.copy(), declared_action_ids=["action_modify"] if action else [],
        declared_action_count=int(action))
    return rows


def test_fixture_has_complete_global_lineage():
    for action in (False, True):
        report = audit_causal_lineage.audit_rows(chain(action=action), source_sha256="a" * 64)
        assert report["summary"]["blocked"] == 0, report["rows"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("action", [False, True])
def test_complete_capture_and_noop(kind, action):
    rows = chain(kind, action=action)
    original = copy.deepcopy(rows)
    report = capture.audit_capture(rows)
    assert report["status"] == "capture_complete"
    assert report["full_live_parity_verified"] is False
    assert report["summary"]["decisions"] == 1
    assert report["summary"]["action_denominator"] == int(action)
    assert report["summary"]["noop_decisions"] == int(not action)
    assert rows == original


def test_no_decisions_is_not_certification():
    assert capture.audit_capture([])["status"] == "no_decisions_yet"
    assert capture.audit_capture(chain()[:3])["status"] == "no_decisions_yet"


@pytest.mark.parametrize("index", [3, -1, 4])
def test_drop_keeps_incomplete_decisions_and_pending_actions(index):
    rows = chain(action=True)
    rows.pop(index)
    report = capture.audit_capture(rows)
    assert report["status"] == "blocked"
    assert report["summary"]["decisions"] == 1
    assert report["summary"]["action_denominator"] == 1


@pytest.mark.parametrize("index", [3, -1, 4])
def test_duplicate_start_completion_or_request_blocks(index):
    rows = chain(action=True)
    rows.append(copy.deepcopy(rows[index]))
    assert capture.audit_capture(rows)["status"] == "blocked"


@pytest.mark.parametrize("field,value", [
    ("decision_id", "decision_wrong"), ("strategy_id", "wrong"),
    ("strategy_fingerprint", "0" * 64), ("management_contract", "v0"),
    ("management_kind", "unknown"), ("direction", "SELL"),
    ("parent_decision_id", "decision_wrong"), ("message_revision_id", "msgrev_wrong"),
    ("declared_action_ids", []), ("declared_action_ids", ["action_modify", "action_modify"]),
    ("declared_action_count", True), ("declared_action_count", 2),
    ("state_after", []), ("decision_status", "error"), ("error_type", "ValueError"),
    ("monotonic_ns", 0), ("ts", "2026-09-08T04:00:00+00:00"),
])
def test_inconsistent_completion_blocks(field, value):
    rows = chain(action=True)
    rows[-1][field] = value
    rehash(rows[-1])
    assert capture.audit_capture(rows)["status"] == "blocked"


@pytest.mark.parametrize("field", ["declared_action_ids", "declared_action_count", "decision_result", "error_type"])
def test_missing_completion_fields_block(field):
    rows = chain()
    del rows[-1][field]
    rehash(rows[-1])
    assert capture.audit_capture(rows)["status"] == "blocked"


@pytest.mark.parametrize("kind", KINDS)
def test_missing_required_input_and_state_block(kind):
    rows = chain(kind)
    rows[3]["decision_inputs"].pop(next(iter(KINDS[kind])))
    rehash(rows[3])
    assert capture.audit_capture(rows)["status"] == "blocked"
    rows = chain(kind)
    del rows[3]["state_before"]["status"]
    rehash(rows[3])
    assert capture.audit_capture(rows)["status"] == "blocked"


@pytest.mark.parametrize("malformed", [None, [], {"ev": []}, {"event_id": []}, {"ev": "bot_internal_decision", "decision_id": []}])
def test_malformed_rows_block_without_crashing(malformed):
    assert capture.audit_capture([malformed])["status"] == "blocked"


def test_cutoff_retains_parent_and_pre_cutoff_errors():
    rows = chain()
    report = capture.audit_capture(rows, since="2026-09-08T05:00:00.003Z")
    assert report["status"] == "capture_complete"
    assert report["journal_integrity"]["selection"]["parsed_rows"] == len(rows)
    rows[0]["payload_sha256"] = "0" * 64
    assert capture.audit_capture(rows, since="2026-09-08T05:00:00.003Z")["status"] == "blocked"


def test_coalescence_is_retained_and_cannot_replace_request():
    rows = chain(action=True)
    relation = copy.deepcopy(rows[4])
    relation.update(ev="mt5_action_coalesced", event_id="event_relation", coalesced_into_action_id="action_missing")
    rows.insert(5, rehash(relation))
    report = capture.audit_capture(rows)
    assert report["status"] == "blocked"
    assert report["summary"]["coalescence_rows"] == 1
    assert report["summary"]["action_denominator"] == 1


def test_exception_keeps_declared_missing_action_denominator():
    rows = chain()
    rows[-1].update(decision_status="error", error_type="RuntimeError", declared_action_ids=["action_lost"], declared_action_count=1)
    rehash(rows[-1])
    report = capture.audit_capture(rows)
    assert report["status"] == "blocked"
    assert report["summary"]["action_denominator"] == 1
    assert report["summary"]["pending_declared_actions"] == 1


def test_valid_coalescence_preserves_both_declared_requests():
    rows = chain(action=True)
    second = copy.deepcopy(rows[4])
    second.update(event_id="event_second", action_id="action_second")
    relation = copy.deepcopy(second)
    relation.update(ev="mt5_action_coalesced", event_id="event_coalescence",
                    coalesced_into_action_id="action_modify", kind="MODIFY_SLTP",
                    payload_changed=False, label_changed=False, persistence_changed=False, queue_slots=1)
    rows[5:5] = [second, relation]
    rows[-1].update(declared_action_ids=["action_modify", "action_second"], declared_action_count=2)
    for index, row in enumerate(rows):
        row.update(monotonic_ns=100 + index, ts=f"2026-09-08T05:00:00.{index:03d}+00:00")
        rehash(row)
    report = capture.audit_capture(rows)
    assert report["status"] == "capture_complete", report["journal_integrity"]
    assert report["summary"]["action_denominator"] == 2
    assert report["summary"]["coalescence_rows"] == 1


def test_policy_cannot_be_relabelled_on_both_endpoints():
    rows = chain()
    for row in (rows[3], rows[-1]):
        row["strategy_fingerprint"] = "0" * 64
        rehash(row)
    assert capture.audit_capture(rows)["status"] == "blocked"


@pytest.mark.parametrize("value", [None, [], {"bid": float("nan")}])
def test_malformed_inputs_block(value):
    rows = chain()
    rows[3]["decision_inputs"] = value
    assert capture.audit_capture(rows)["status"] == "blocked"


@pytest.mark.parametrize("row_index,state_key", [(3, "state_before"), (-1, "state_after")])
@pytest.mark.parametrize("field", ["timestamp", "candidate_first_fill_at"])
@pytest.mark.parametrize("value", ["invalid", "2026-09-08T05:00:00.123456", "2026-09-08T05:00:00+02:00"])
def test_state_dates_reject_malformed_or_non_utc_values(row_index, state_key, field, value):
    rows = chain()
    rows[row_index][state_key][field] = value
    rehash(rows[row_index])
    assert capture.audit_capture(rows)["status"] == "blocked"


def test_state_preserves_full_utc_microseconds():
    rows = chain()
    for row_index, state_key in ((3, "state_before"), (-1, "state_after")):
        rows[row_index][state_key].update(timestamp="2026-09-08T04:59:59.123456+00:00",
                                         candidate_first_fill_at="2026-09-08T04:59:59.654321+00:00")
        rehash(rows[row_index])
    before = copy.deepcopy(rows)
    assert capture.audit_capture(rows)["status"] == "capture_complete"
    assert rows == before


def test_cli_detects_source_change_during_audit(tmp_path, monkeypatch):
    source, output = tmp_path / "events.jsonl", tmp_path / "report.json"
    source.write_text("\n".join(json.dumps(r) for r in chain()) + "\n", encoding="utf-8")
    original_audit = capture.audit_capture

    def changing_source(rows, **kwargs):
        report = original_audit(rows, **kwargs)
        # Model an independent journal writer; the validator itself never writes it.
        with source.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        return report

    monkeypatch.setattr(capture, "audit_capture", changing_source)
    assert capture.main(["--events", str(source), "--output", str(output)]) == 2
    report = json.loads(output.read_text())
    assert report["status"] == "blocked"
    assert report["source"]["unchanged"] is False


def test_module_has_no_live_imports():
    result = subprocess.run([sys.executable, "-B", "-c",
        "import sys; from tools import audit_management_capture; "
        "assert not {'journal', 'executor', 'MetaTrader5', 'listener', 'management_decision_evidence'} & set(sys.modules)"],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def run_cli(source, output, *extra):
    return subprocess.run([sys.executable, "-B", str(ROOT / "tools/audit_management_capture.py"),
                           "--events", str(source), "--output", str(output), *extra],
                          cwd=ROOT, capture_output=True, text=True, timeout=30)


def test_cli_preserves_source_refuses_overwrite_and_records_hashes(tmp_path):
    source, output = tmp_path / "events.jsonl", tmp_path / "report.json"
    source.write_text("\n".join(json.dumps(r) for r in chain()) + "\n", encoding="utf-8")
    before = source.read_bytes()
    result = run_cli(source, output, "--since", "2026-09-08T05:00:00.003Z")
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text())
    digest = hashlib.sha256(before).hexdigest()
    assert report["source"]["sha256_before"] == report["source"]["sha256_after"] == digest
    assert report["source"]["unchanged"] is True
    assert len(report["code"]["sha256"]) == 64
    output_before = output.read_bytes()
    assert run_cli(source, output).returncode != 0
    assert run_cli(source, source).returncode != 0
    assert source.read_bytes() == before
    assert output.read_bytes() == output_before


def test_cli_invalid_line_is_in_denominator(tmp_path):
    source, output = tmp_path / "events.jsonl", tmp_path / "report.json"
    source.write_bytes(b'{broken\nnull\n')
    before = source.read_bytes()
    result = run_cli(source, output)
    assert result.returncode == 2
    report = json.loads(output.read_text())
    assert report["status"] == "blocked"
    assert report["summary"]["malformed_rows"] == 2
    assert source.read_bytes() == before


@pytest.mark.parametrize("since", ["2026-09-08", "2026-09-08T05:00:00", "2026-09-08T05:00:00+02:00"])
def test_since_requires_explicit_utc(since):
    with pytest.raises(ValueError):
        capture.audit_capture([], since=since)
