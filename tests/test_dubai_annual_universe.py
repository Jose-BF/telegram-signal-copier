from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from research.dubai_annual_universe import (
    build_universe, load_catalog, resolve_entries, rolling_cohorts, write_universe,
)
from research.dubai_export_catalog import build_catalog, write_catalog
from research.telegram_export import prepare_exports, write_admission


START = "2026-01-01T00:00:00Z"


def row(mid, kind="gold_sticker", seconds=0, edited=None, direction="BUY"):
    pub = (datetime.fromisoformat(START) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
    return {"message_id": mid, "chat_id": 1642806869, "signal_id": f"telegram_export:1642806869:{mid}",
        "kind": kind, "direction": direction, "published_utc": pub, "issues": [],
        "forwarded": False, "forward_origins": [{}], "review_reasons": [], "companion_proposal": None,
        "snapshots": [{"raw_snapshot_sha256": str(mid) * 64, "direction": direction,
                       "has_edit_marker": edited is not None, "edited_utc": edited, "published_utc": pub}]}


def decision(mid, action, target, **extra):
    return {"message_id": mid, "action": action, "target_id": target,
            "reason": "Synthetic review fixture", "evidence_ids": [mid], **extra}


def test_pair_is_one_hypothesis_with_every_identity_retained():
    sticker, text, other = row(1), row(2, "text_now_candidate", 30), row(3, "other", 40, direction=None)
    text["companion_proposal"] = {"signal_id": sticker["signal_id"]}
    entries, assignments = resolve_entries([sticker, text, other], {"decisions": []})
    assert len(entries) == 1
    assert entries[0]["message_ids"] == [1, 2]
    assert entries[0]["initial_at_reference_supported"]
    assert not entries[0]["engine_admitted"]
    assert entries[0]["trigger_utc"] is entries[0]["received_utc"] is None
    assert len(assignments) == 3
    assert assignments[2]["action"] == "retain"


def test_reentry_overrides_nearby_pair_without_dedup_by_text():
    rows = [row(1), row(2, "text_now_candidate", 30), row(3, "text_now_candidate", 90)]
    for text in rows[1:]:
        text["companion_proposal"] = {"signal_id": rows[0]["signal_id"]}
    entries, _ = resolve_entries(rows, {"decisions": [decision(3, "entry", 3)]})
    assert [entry["message_ids"] for entry in entries] == [[1, 2], [3]]


def test_edited_early_text_never_becomes_initial_even_when_grouped_with_sticker():
    text = row(1, "text_now_candidate", edited="2026-01-01T01:00:00Z")
    sticker = row(2, seconds=5)
    entries, _ = resolve_entries([text, sticker], {"decisions": [decision(1, "attach", 2)]})
    entry = entries[0]
    assert entry["publication_reference_utc"] == START
    assert entry["known_unedited_component_utc"] == sticker["published_utc"]
    assert not entry["initial_at_reference_supported"]
    assert entry["trigger_utc"] is None


def test_edited_only_retains_revision_diagnostic_without_admission():
    entries, _ = resolve_entries([row(1, edited="2026-01-01T02:00:00Z")], {"decisions": []})
    assert entries[0]["known_unedited_component_utc"] is None
    assert entries[0]["known_revision_or_initial_component_utc"] == "2026-01-01T02:00:00Z"
    assert not entries[0]["initial_at_reference_supported"]
    assert not entries[0]["engine_admitted"]


def test_older_unedited_snapshot_survives_latest_edit():
    sticker = row(1, edited="2026-01-01T02:00:00Z")
    sticker["snapshots"].append(row(1)["snapshots"][0])
    entries, _ = resolve_entries([sticker], {"decisions": []})
    assert entries[0]["initial_at_reference_supported"]


def test_symbol_less_context_does_not_backdate_gold():
    generic, text = row(1, "unmapped_sticker", direction=None), row(2, "text_now_candidate", 60)
    entries, _ = resolve_entries([generic, text], {"decisions": [
        decision(1, "context", 2), decision(2, "entry", 2)]})
    assert entries[0]["publication_reference_message_id"] == 2
    assert entries[0]["message_ids"] == [1, 2]
    assert entries[0]["directional_message_ids"] == [2]


def test_extra_label_requires_exact_snapshot_binding():
    text = row(1, "directional_text_review", direction=None)
    label = decision(1, "entry", 1, direction="BUY", reviewed_snapshot_sha256=["wrong"])
    with pytest.raises(ValueError, match="snapshot"):
        resolve_entries([text], {"decisions": [label]})
    label["reviewed_snapshot_sha256"] = [text["snapshots"][0]["raw_snapshot_sha256"]]
    entries, _ = resolve_entries([text], {"decisions": [label]})
    assert entries[0]["initial_at_reference_supported"]


@pytest.mark.parametrize("case", ["duplicate", "unreviewed", "chain", "conflict", "evidence", "channel"])
def test_invalid_review_fails_closed(case):
    rows = [row(1), row(2, "text_now_candidate", 30)]
    ledger = {"decisions": [decision(2, "attach", 1)]}
    if case == "duplicate":
        ledger["decisions"].append(decision(2, "attach", 1))
    elif case == "unreviewed":
        ledger["decisions"] = []
    elif case == "chain":
        ledger["decisions"] += [decision(1, "attach", 2)]
    elif case == "conflict":
        rows[1]["direction"] = "SELL"
    elif case == "evidence":
        ledger["decisions"][0]["evidence_ids"] = [999]
    elif case == "channel":
        rows[0]["chat_id"] = 999
    with pytest.raises(ValueError):
        resolve_entries(rows, ledger)


def test_integrity_issue_blocks_clock_but_keeps_denominator():
    sticker = row(1)
    sticker["issues"] = ["direction_conflict"]
    entries, _ = resolve_entries([sticker], {"decisions": []})
    assert len(entries) == 1
    assert entries[0]["known_unedited_component_utc"] is None
    assert not entries[0]["initial_at_reference_supported"]


def test_forward_origins_are_not_erased_by_grouping():
    sticker, text = row(1), row(2, "text_now_candidate", 20)
    text.update(forwarded=True, forward_origins=[{"forwarded_from": "Other source"}])
    entries, _ = resolve_entries([sticker, text], {"decisions": [decision(2, "attach", 1)]})
    assert entries[0]["forwarded"]
    assert len(entries[0]["forward_origins"]) == 2
    assert "mixed_forward_origins" in entries[0]["review_flags"]


def test_rolling_boundaries_purge_and_partial_tail():
    times = [START, "2026-02-25T19:59:59Z", "2026-02-25T20:00:00Z",
             "2026-02-26T00:00:00Z", "2026-03-12T00:00:00Z", "2026-09-13T12:20:45Z"]
    entries = [{"entry_id": str(i), "publication_reference_utc": time} for i, time in enumerate(times)]
    folds, usage = rolling_cohorts(entries, start=START, end_exclusive="2026-09-13T12:20:46Z")
    assert len(folds) == 15
    assert sum(fold["complete_calendar_check"] for fold in folds) == 14
    assert folds[0]["development_entry_ids"] == ["0", "1"]
    assert folds[0]["purged_entry_ids"] == ["2"]
    assert folds[0]["check_entry_ids"] == ["3"]
    assert folds[1]["check_entry_ids"] == ["4"]
    assert folds[-1]["check_entry_ids"] == ["5"]
    assert not folds[-1]["complete_calendar_check"]
    assert all(len(row["check_folds"]) == 1 for row in usage[3:])
    assert all(row["warmup_only"] for row in usage[:3])
    assert all(not set(fold["development_entry_ids"]) & set(fold["check_entry_ids"]) for fold in folds)


@pytest.mark.parametrize("changes", [{"development_days": 0}, {"check_days": True},
                                    {"horizon_seconds": 56 * 86400}, {"start": "2026-01-01T00:00:00"}])
def test_rolling_invalid_contract_rejected(changes):
    kwargs = {"start": START, "end_exclusive": "2026-09-13T12:20:46Z", **changes}
    with pytest.raises(ValueError):
        rolling_cohorts([], **kwargs)


def archive(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"id": 1642806869, "messages": [
        {"id": 1, "type": "message", "date_unixtime": "1767358244", "text": "BUY GOLD NOW 4400"}]}))
    admission = tmp_path / "admission"
    write_admission(prepare_exports([source], start=START, end="2026-09-14T00:00:00Z"), admission)
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"schema_version": "reviewed_sticker_labels_v1", "labels": []}))
    catalog = tmp_path / "catalog"
    manifest = write_catalog(build_catalog(admission, labels), catalog)
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"schema_version": "dubai_annual_review_v1",
        "catalog_identity_sha256": manifest["catalog_identity_sha256"],
        "entry_scope": "Synthetic explicit GOLD entries", "decisions": [decision(1, "entry", 1)],
        "rolling_protocol": {"start": START, "end_exclusive": "2026-09-14T00:00:00Z"}}))
    return catalog, review, source


def test_source_catalog_and_ledger_are_bound_and_output_immutable(tmp_path):
    catalog, review, source = archive(tmp_path)
    result = build_universe(catalog, review)
    output = tmp_path / "universe"
    manifest = write_universe(result, output)
    assert manifest == write_universe(build_universe(catalog, review), output)
    assert result["summary"]["source_messages"] == result["summary"]["entry_hypotheses"] == 1
    changed = deepcopy(result)
    changed["summary"]["entry_hypotheses"] = 2
    with pytest.raises(ValueError, match="immutable"):
        write_universe(changed, output)
    with pytest.raises(ValueError, match="input archive"):
        write_universe(result, catalog / "nested")
    source.write_text("{}")
    with pytest.raises(ValueError, match="evidence mismatch"):
        build_universe(catalog, review)


def test_catalog_tampering_and_stale_ledger_rejected(tmp_path):
    catalog, review, _ = archive(tmp_path)
    data = json.loads(review.read_text())
    data["catalog_identity_sha256"] = "0" * 64
    review.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="another catalog"):
        build_universe(catalog, review)
    (catalog / "messages.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="artifact mismatch"):
        load_catalog(catalog)
