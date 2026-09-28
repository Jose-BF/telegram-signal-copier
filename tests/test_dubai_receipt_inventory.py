from copy import deepcopy
import hashlib

import pytest

from research.dubai_receipt_inventory import classify_receipt, build_inventory
from tests.test_dubai_clock_audit import trigger, decision
from tests.test_strategy_study import case


def legacy(mid=1, **changes):
    row = dict(ev="telegram_raw", channel="canal1", message_id=mid, update_kind="new_message",
        date_utc="2026-08-01T10:00:00Z", edit_date_utc=None, ts="2026-08-01T10:00:01Z",
        is_edit=False, is_reply=False, reply_to_msg_id=None, has_text=False, text="",
        text_len=0, text_sha1=None, has_media=True, sticker_id=5)
    row.update(changes)
    return row


def inventory(rows, exports=None, decisions=None):
    return build_inventory(list(enumerate(rows, 1)), start="2026-08-01T00:00:00Z",
        end="2026-08-02T00:00:00Z", sticker_directions={"5": "BUY", "6": "SELL"},
        triggers=[trigger()] if exports is None else exports,
        decisions=[decision(1, 1)] if decisions is None else decisions)


def test_legacy_is_explicit_and_does_not_invent_observed_identifiers():
    row = legacy()
    before = deepcopy(row)
    evidence = classify_receipt(row)
    assert evidence == {"tier": "legacy_channel_tag_only", "reasons": [], "numeric_chat_observed": False,
                        "canonical_revision_observed": False}
    out = inventory([row])
    entry = out["entries"][0]
    assert entry["initial_receipt_supported"] and entry["fresh_initial_receipt_within_5s"]
    assert entry["raw_chat_id"] is None and entry["raw_message_revision_id"] is None
    assert entry["source_evidence_tier"] == "legacy_channel_tag_only"
    assert row == before
    assert not out["engine_dataset_ready"] and not out["money_contract_verified"]


@pytest.mark.parametrize("change", [{"message_revision_id": "forged"}, {"chat_id": -1001642806869},
    {"revision_token": "initial"}, {"has_text": True}, {"text_sha1": "a" * 40},
    {"is_edit": "false"}, {"message_id": True}, {"ts": "2026-08-01T10:00:00"}])
def test_broken_or_partial_modern_evidence_cannot_fall_back_to_legacy(change):
    evidence = classify_receipt(legacy(**change))
    assert evidence["tier"] == "invalid" and evidence["reasons"]


def test_text_integrity_is_checked_without_using_its_trading_result():
    text = "BUY GOLD NOW"
    row = legacy(text=text, has_text=True, text_len=len(text), text_sha1=hashlib.sha1(text.encode()).hexdigest())
    assert classify_receipt(row)["tier"] == "legacy_channel_tag_only"
    row["text"] = "SELL GOLD NOW"
    assert classify_receipt(row)["tier"] == "invalid"


def test_historical_numeric_chat_without_revision_has_its_own_tier():
    row = legacy(chat_id=-1001642806869, date_utc="2026-07-21T13:25:52Z", ts="2026-07-21T13:25:53.004Z")
    evidence = classify_receipt(row)
    assert evidence == {"tier": "legacy_observed_chat_without_revision", "reasons": [],
        "numeric_chat_observed": True, "canonical_revision_observed": False}
    assert "message_revision_id" not in row


@pytest.mark.parametrize("change", [{"message_revision_id": None}, {"revision_token": None},
    {"media_sha256": None}, {"schema_version": None}, {"chat_id": -1009999999}])
def test_claimed_canonical_fields_or_wrong_chat_do_not_enter_intermediate_tier(change):
    row = legacy(chat_id=-1001642806869, date_utc="2026-07-21T13:25:52Z", ts="2026-07-21T13:25:53Z")
    row.update(change)
    evidence = classify_receipt(row)
    assert evidence["tier"] == "invalid" and evidence["reasons"]


def test_later_export_direction_and_time_cannot_change_raw_trigger_eligibility():
    a = inventory([legacy()])["entries"][0]
    b = inventory([legacy()], exports=[trigger(direction="SELL")])["entries"][0]
    assert a["initial_receipt_supported"] == b["initial_receipt_supported"] is True
    assert b["export_crosswalk"][0]["reasons"] == ["direction_differs"]
    assert b["received_utc"] == a["received_utc"]


def test_missing_export_does_not_remove_or_backdate_legacy_entry():
    row = inventory([legacy()], exports=[], decisions=[])["entries"][0]
    assert row["initial_receipt_supported"]
    assert all(r["reasons"] == ["raw_message_has_no_export_trigger"] for r in row["export_crosswalk"])


def test_redelivery_preserves_first_receipt_not_latest_transport():
    first = legacy()
    later = legacy(ts="2026-08-01T11:00:00Z", update_kind="poll_new")
    out = inventory([later, first])["entries"][0]
    assert out["received_utc"] == "2026-08-01T10:00:01+00:00"
    assert out["first_source_line_1based"] == 2 and out["receipt_count"] == 2
    assert out["fresh_initial_receipt_within_5s"]


def test_later_edit_does_not_replace_original_or_become_a_new_trade():
    first = legacy()
    later = legacy(sticker_id=6, ts="2026-08-01T10:01:01Z", edit_date_utc="2026-08-01T10:01:00Z",
                   is_edit=True, update_kind="edit")
    out = inventory([first, later])
    assert len(out["entries"]) == 1
    entry = out["entries"][0]
    assert entry["direction"] == "BUY" and entry["initial_receipt_supported"]
    assert "later_direction_differs" in entry["history_diagnostics"]


def test_first_retained_edit_is_not_proof_of_original_direction():
    row = inventory([legacy(edit_date_utc="2026-08-01T10:00:00Z")])["entries"][0]
    assert not row["initial_receipt_supported"]
    assert "first_directional_revision_is_edit" in row["entry_reasons"]


def test_clock_conflict_is_visible_not_corrected():
    row = inventory([legacy(ts="2026-08-01T09:59:59Z")])["entries"][0]
    assert not row["initial_receipt_supported"]
    assert "publication_after_receipt" in row["entry_reasons"]
    assert row["receipt_minus_publication_seconds"] == -1


def test_late_unedited_receipt_keeps_separate_currentness_status():
    row = inventory([legacy(ts="2026-08-01T11:00:00Z")])["entries"][0]
    assert row["initial_receipt_supported"] and not row["fresh_initial_receipt_within_5s"]
    assert row["receipt_minus_publication_seconds"] == 3600


def test_same_time_conflicting_content_is_not_ordered_by_file_line():
    row = inventory([legacy(), legacy(sticker_id=6)])["entries"][0]
    assert not row["initial_receipt_supported"]
    assert "ambiguous_first_directional_receipt" in row["entry_reasons"]


def test_context_before_directional_version_prevents_original_content_claim():
    row = inventory([legacy(sticker_id=None), legacy(ts="2026-08-01T10:00:02Z")])["entries"][0]
    assert not row["initial_receipt_supported"]
    assert "earlier_nondirectional_receipt" in row["entry_reasons"]


def test_unknown_stickers_and_invalid_rows_are_retained_outside_directional_entries():
    out = inventory([legacy(sticker_id=999), legacy(2, message_id=False)], exports=[], decisions=[])
    assert not out["entries"]
    assert len(out["unknown_sticker_messages"]) == 1
    assert len(out["invalid_receipts"]) == 1


def test_new_unrelated_message_cannot_change_initial_entry_facts():
    a = inventory([legacy()])["entries"][0]
    b = inventory([legacy(), legacy(2, ts="2026-08-01T12:00:00Z")])["entries"][0]
    assert a == b


def extended_fixture(tmp_path):
    import json
    from research.strategy_study import _read, _encode, _digest
    from research.dubai_clock_audit import run_audit
    from tests.test_dubai_clock_audit import audit_fixture
    raw_dir, export, prior, source = audit_fixture(tmp_path / "original")
    raw = json.loads(source.read_text())
    extra = dict(raw, message_id=2, date_utc="2026-09-04T10:00:00Z", ts="2026-09-04T10:00:01Z")
    canonical = {k: extra[k] for k in ("chat_id", "media_sha256", "message_id", "revision_token", "text_sha1")}
    extra["message_revision_id"] = "msgrev_" + hashlib.sha256(json.dumps(canonical,
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    old = legacy(3, date_utc="2026-06-06T10:00:00Z", ts="2026-06-06T10:00:01Z")
    intermediate = legacy(4, chat_id=-1001642806869, date_utc="2026-07-21T13:25:52Z", ts="2026-07-21T13:25:53Z")
    source.write_bytes(_encode(raw) + _encode(extra) + _encode(old) + _encode(intermediate))
    protocol = _read(raw_dir / "data_protocol.json")
    protocol["raw_source"].update(sha256=_digest(source), bytes=source.stat().st_size)
    (raw_dir / "data_protocol.json").write_bytes(_encode(protocol))
    (raw_dir / "admission_summary.json").write_bytes(_encode({"data_protocol_sha256": _digest(raw_dir / "data_protocol.json")}))
    run_audit(raw_dir, export, prior)
    return prior, tmp_path / "extended", source


def test_complete_source_to_inventory_and_fresh_verifier(tmp_path, case):
    from research.dubai_receipt_inventory import run_inventory, verify_inventory
    from research.strategy_study import _read
    prior, output, source = extended_fixture(tmp_path)
    summary = run_inventory(prior, output)
    assert summary["canonical_compiled_signals"] == 2
    assert summary["canonical_added_signal_ids"] == ["canal1_2"]
    assert summary["groups"]["legacy_channel_tag_only"]["fresh_initial_receipt_within_5s"] == 1
    assert summary["groups"]["legacy_observed_chat_without_revision"]["fresh_initial_receipt_within_5s"] == 1
    assert summary["canonical_prefix_matches_prior"]
    inventory = _read(output / "inventory.json")
    old = next(r for r in inventory["entries"] if r["message_id"] == 3)
    assert old["raw_chat_id"] is old["raw_message_revision_id"] is None
    assert verify_inventory(output)["status"] == "verified_receipt_inventory_only"
    with pytest.raises(ValueError, match="immutable"):
        run_inventory(prior, output)


@pytest.mark.parametrize("issue", ["missing", "source_changed", "summary_resealed"])
def test_inventory_tamper_or_missing_evidence_never_verifies(tmp_path, case, issue):
    from research.dubai_receipt_inventory import run_inventory, verify_inventory
    from research.strategy_study import _read, _encode, _digest, _sha
    prior, output, source = extended_fixture(tmp_path)
    run_inventory(prior, output)
    if issue == "missing":
        (output / "canonical_signals.json").unlink()
    elif issue == "source_changed":
        source.write_bytes(source.read_bytes() + b"\n")
    else:
        target = output / "summary.json"
        value = _read(target)
        value["canonical_compiled_signals"] = 99
        target.write_bytes(_encode(value))
        manifest = _read(output / "manifest.json")
        manifest["artifacts"][target.name]["sha256"] = _digest(target)
        del manifest["receipt_inventory_identity_sha256"]
        manifest["receipt_inventory_identity_sha256"] = _sha(manifest)
        (output / "manifest.json").write_bytes(_encode(manifest))
    with pytest.raises(ValueError):
        verify_inventory(output)


def test_duplicate_source_lines_fail_before_identity_selection():
    with pytest.raises(ValueError, match="source line"):
        build_inventory([(1, legacy()), (1, legacy(2))], start="2026-08-01T00:00:00Z",
            end="2026-08-02T00:00:00Z", sticker_directions={"5": "BUY"}, triggers=[], decisions=[])


def test_existing_offline_barrier_is_not_disabled(tmp_path, case, monkeypatch):
    import sys
    from types import SimpleNamespace
    from research.dubai_receipt_inventory import run_inventory
    prior, output, source = extended_fixture(tmp_path)
    monkeypatch.setitem(sys.modules, "MetaTrader5", SimpleNamespace())
    with pytest.raises(ValueError, match="offline import boundary"):
        run_inventory(prior, output)
    assert not output.exists()
