from datetime import datetime, timezone
import hashlib
import json

import pytest

import provider_signal_catalog
from tools import prepare_gold_study as preparation


CHAT = preparation.CURRENT_GOLD_CHAT_ID
CUTOFF = datetime(2026, 7, 23, tzinfo=timezone.utc)


def raw(message_id=100, text="Buy Gold Now", ts="2026-07-22T10:00:01+00:00", **overrides):
    row = {"ev": "telegram_raw", "channel": "canal2", "chat_id": CHAT,
           "message_id": message_id, "ts": ts, "date_utc": "2026-07-22T10:00:00+00:00",
           "text": text, "update_kind": "new", "is_edit": False, "is_reply": False}
    row.update(overrides)
    return row


def manifest(ids=(100,)):
    return {"proposed_period": {"start_date_utc": "2026-07-22", "end_date_utc": "2026-07-22"},
            "telegram_raw_coverage": {"messages": [{"message_id": i, "chat_id": CHAT,
                "unedited_revision_observed": True, "causal_source_blockers": []} for i in ids]},
            "tick_source_selection": {"selected_records": [], "conflicting_consistent_duplicate_groups": []}}


def report(events, ids=(100,)):
    selected, history, _ = preparation.select_observations(events, chat_id=CHAT, cutoff=CUTOFF)
    catalog = provider_signal_catalog.build_catalog_report(selected, [])
    return preparation.build_readiness(catalog, history, manifest(ids), cutoff=CUTOFF)


def test_chat_generation_collision_cannot_rewrite_current_direction():
    data = report([raw(), raw(text="Sell Gold Now", chat_id=-1003828356530)])
    assert data["summary"]["now_signals"] == 1
    assert data["signals"][0]["trigger_direction"] == "BUY"
    assert data["summary"]["inventory_receipts_total"] == 1


def test_post_cutoff_edit_is_retained_in_denominator_but_not_catalog():
    data = report([raw(), raw(text="Sell Gold Now", ts="2026-07-23T00:00:00+00:00",
                             update_kind="edit", is_edit=True)])
    assert data["signals"][0]["final_catalog_direction"] == "BUY"
    assert data["messages"][0]["raw_receipts_total"] == 2
    assert data["messages"][0]["receipts_before_cutoff"] == 1


def test_later_direction_does_not_change_reported_entry_direction():
    data = report([raw(), raw(text="Sell Gold Now", ts="2026-07-22T10:05:00+00:00",
                             update_kind="edit", is_edit=True)])
    signal = data["signals"][0]
    assert signal["trigger_direction"] == "BUY"
    assert signal["final_catalog_direction"] == "SELL"
    assert "final_direction_differs_from_causal_entry_direction" in signal["source_warnings"]


def test_now_word_in_later_revision_does_not_reclassify_priced_trigger():
    data = report([raw(text="Buy Gold @4100 TP1 4105 SL 4095"),
                   raw(text="Buy Gold Now", ts="2026-07-22T10:05:00+00:00", update_kind="edit", is_edit=True)])
    assert data["summary"]["now_signals"] == 0
    assert data["signals"][0]["scope"] == "non_now_entry_with_later_now"
    assert data["messages"][0]["raw_now_seen_before_cutoff"] is True


def test_uncatalogued_reply_stays_in_message_denominator():
    data = report([raw(), raw(101, "Thanks", reply_to_msg_id=999, is_reply=True)], (100, 101))
    assert data["summary"]["inventory_messages"] == 2
    assert data["messages"][1]["disposition"] == "uncatalogued_reply_or_context_retained"
    assert data["signals"][0]["execution_observed"] is None


def test_invalid_receipt_clock_is_retained_but_cannot_trigger_entry():
    data = report([raw(ts="2026-07-22T10:00:01")])
    assert data["summary"]["now_signals"] == 0
    assert data["messages"][0]["raw_receipts_total"] == 1
    assert data["messages"][0]["disposition"] == "no_usable_receipt_before_cutoff"


def test_screening_includes_next_day_and_preserves_missing_sources():
    result = preparation.coverage_requirements(datetime(2026, 7, 22, 23, tzinfo=timezone.utc), 240, {}, set())
    assert len(result["requirements"]) == 4
    assert {row["day_utc"] for row in result["requirements"]} == {"2026-07-22", "2026-07-23"}
    assert result["status"] == "blocked"
    assert all(row["blockers"] == ["missing_tick_source"] for row in result["requirements"])


def test_immutable_output_rejects_different_content(tmp_path):
    target = tmp_path / "report.json"
    preparation.write_immutable(target, b"original")
    preparation.write_immutable(target, b"original")
    with pytest.raises(ValueError, match="immutable"):
        preparation.write_immutable(target, b"different")
    assert target.read_bytes() == b"original"


def test_mixed_generation_manifest_is_rejected():
    data = manifest()
    data["telegram_raw_coverage"]["messages"].append({"message_id": 1, "chat_id": -1003828356530})
    with pytest.raises(ValueError, match="current Gold"):
        preparation.gold_chat_id(data)


def test_catchup_keeps_actual_availability_and_separate_provider_age():
    data = report([raw(date_utc="2026-07-20T10:00:00+00:00", update_kind="startup_catchup_new")])
    timing = data["signals"][0]["trigger_timing"]
    assert timing["provider_to_receipt_seconds"] == 172801
    assert timing["publication_to_receipt_seconds"] == 172801
    assert timing["update_kinds"] == ["startup_catchup_new"]
    assert data["signals"][0]["trigger_observed_utc"] == "2026-07-22T10:00:01+00:00"
    assert data["summary"]["now_received_on_later_utc_date"] == 1


def test_duplicate_inventory_message_is_not_silently_collapsed():
    with pytest.raises(ValueError, match="duplicate.*message"):
        report([raw()], (100, 100))


def freeze_manifest(path, payload):
    unsigned = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    payload["manifest_identity_sha256"] = hashlib.sha256(unsigned).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_full_package_preserves_raw_and_detects_source_mutation(tmp_path):
    source = tmp_path / "events.jsonl"
    source.write_text(json.dumps(raw()) + "\n", encoding="utf-8")
    payload = manifest()
    payload["input_sources"] = {"trade_events": preparation.file_identity(source)}
    source_manifest = freeze_manifest(tmp_path / "manifest.json", payload)
    output = tmp_path / "package"
    result = preparation.run(source_manifest, output, (240,))
    assert result["summary"]["now_signals"] == 1
    assert json.loads((output / "scoped_telegram_raw.jsonl").read_text()) == raw()
    assert preparation.run(source_manifest, output, (240,)) == result
    source.write_text(json.dumps(raw(text="Sell Gold Now")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen source"):
        preparation.run(source_manifest, tmp_path / "mutated", (240,))
    assert not (tmp_path / "mutated").exists()


def tick_proof_fixture(tmp_path, relationship="exact_ordered_semantic_equal"):
    candidates = [{"path": f"source_{index}", "sha256": f"ticks_{index}", "metadata_sha256": f"sidecar_{index}"}
                  for index in range(2)]
    data = manifest()
    data["tick_source_selection"] = {
        "selected_records": [{**candidates[1], "day": "2026-07-22", "symbol": "EURUSD"}],
        "conflicting_consistent_duplicate_groups": [{"day": "2026-07-22", "symbol": "EURUSD", "candidates": candidates}]}
    source_manifest = freeze_manifest(tmp_path / "manifest.json", data)
    proof = {"input_manifest": {"observed_file_sha256": preparation.file_identity(source_manifest)["sha256"]},
             "per_day_results": [{"day": "2026-07-22", "symbol": "EURUSD",
                 "candidate_results": [{"manifest_record_original": candidate,
                     "observed": {"parquet_sha256": candidate["sha256"], "sidecar_sha256": candidate["metadata_sha256"]},
                     "admission": {"admissible_with_own_parquet_and_own_sidecar": index == 1}}
                     for index, candidate in enumerate(candidates)],
                 "resolution": {"exact_full_sequence_equal": True},
                 "pairwise_comparisons": [{"left_candidate_index": 0, "right_candidate_index": 1,
                     "semantic": {"relationship": relationship}, "all_columns_values_and_dtypes_equal": True}]}]}
    return data, source_manifest, proof


def write_tick_proof(path, proof):
    encoded = json.dumps(proof, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    proof["report_identity_sha256"] = hashlib.sha256(encoded).hexdigest()
    path.write_text(json.dumps(proof), encoding="utf-8")
    return path


def test_tick_proof_resolves_actual_selected_capture_not_first_candidate(tmp_path):
    data, source, proof = tick_proof_fixture(tmp_path)
    path = write_tick_proof(tmp_path / "proof.json", proof)
    assert preparation.resolved_tick_conflicts(path, data, source) == {("2026-07-22", "EURUSD")}


def test_tick_proof_never_resolves_divergent_prices(tmp_path):
    data, source, proof = tick_proof_fixture(tmp_path, "divergent_ordered_semantics")
    path = write_tick_proof(tmp_path / "proof.json", proof)
    assert preparation.resolved_tick_conflicts(path, data, source) == set()


def test_tick_proof_requires_each_candidates_own_sidecar(tmp_path):
    data, source, proof = tick_proof_fixture(tmp_path)
    proof["per_day_results"][0]["candidate_results"][1]["observed"]["sidecar_sha256"] = "sidecar_0"
    path = write_tick_proof(tmp_path / "proof.json", proof)
    with pytest.raises(ValueError, match="candidate identity mismatch"):
        preparation.resolved_tick_conflicts(path, data, source)


def test_tick_proof_rejects_modified_report(tmp_path):
    data, source, proof = tick_proof_fixture(tmp_path)
    path = write_tick_proof(tmp_path / "proof.json", proof)
    proof["per_day_results"] = []
    path.write_text(json.dumps(proof), encoding="utf-8")
    with pytest.raises(ValueError, match="diagnostics identity mismatch"):
        preparation.resolved_tick_conflicts(path, data, source)


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_directions_at_identical_trigger_time_are_blocked(reverse):
    rows = [raw(), raw(text="Sell Gold Now", update_kind="edit", is_edit=True)]
    data = report(list(reversed(rows)) if reverse else rows)
    assert data["summary"]["now_signals"] == 1
    assert data["summary"]["now_with_causal_blockers"] == 1
    assert "ambiguous_trigger_revision_same_timestamp" in data["signals"][0]["causal_blockers"]


def test_post_cutoff_original_does_not_certify_earlier_edited_receipt():
    data = report([raw(is_edit=True, edit_date_utc="2026-07-22T10:00:00+00:00"),
                   raw(ts="2026-07-23T10:00:00+00:00")])
    assert data["messages"][0]["inventory_unedited_original_observed_anytime"] is True
    assert data["messages"][0]["unedited_original_observed"] is False
    assert data["signals"][0]["unedited_original_observed_by_trigger"] is False
    assert "unedited_original_not_observed" in data["signals"][0]["source_warnings"]


def test_original_after_trigger_before_cutoff_is_not_available_at_entry():
    data = report([raw(is_edit=True, edit_date_utc="2026-07-22T10:00:00+00:00"),
                   raw(ts="2026-07-22T11:00:00+00:00")])
    assert data["messages"][0]["unedited_original_observed"] is True
    assert data["signals"][0]["unedited_original_observed_by_trigger"] is False
