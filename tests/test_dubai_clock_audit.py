from copy import deepcopy

import pytest

from research.dubai_clock_audit import build_crosswalk
from tests.test_strategy_study import case


def original(mid=1, **changes):
    return dict(signal_id=f"canal1_{mid}", message_id=mid, direction="BUY",
                published_utc="2026-08-01T10:00:00Z", received_utc="2026-08-01T10:00:01Z",
                retained_revision_is_edit=False, **changes)


def trigger(mid=1, scenario="revision_time", **changes):
    row = dict(trigger_id=f"dubai_stream:{scenario}:{mid}", trigger_message_id=mid,
               chat_id=1642806869, scenario=scenario, direction="BUY", source_kind="sticker",
               source_is_edit=True, published_utc="2026-08-01T10:00:00Z",
               trigger_utc="2026-08-01T10:01:00Z", clock_is_hypothesis=True)
    row.update(changes)
    return row


def decision(mid, root, scenario="revision_time"):
    return dict(message_id=mid, trigger_id=f"dubai_stream:{scenario}:{root}", scenario=scenario)


def test_same_identity_is_paired_without_replacing_either_clock():
    a, b = original(), trigger()
    before = deepcopy((a, b))
    out = build_crosswalk([a], [b], [decision(1, 1)])
    row = out["pairs"][0]
    assert row["comparison_status"] == "paired_clock_control"
    assert row["export_minus_receipt_seconds"] == 59
    assert row["export_minus_publication_seconds"] == 60
    assert row["raw_original"] == a and row["export_trigger"] == b
    assert (a, b) == before
    assert out["pairs"][1]["comparison_status"] == "not_comparable"


def test_complement_matches_group_but_keeps_distinct_publication_times():
    b = trigger(2, published_utc="2026-08-01T10:00:05Z", source_kind="text")
    out = build_crosswalk([original()], [b], [decision(1, 2), decision(2, 2)])
    row = out["pairs"][0]
    assert row["match_kind"] == "group_member"
    assert row["comparison_status"] == "paired_clock_control"
    assert row["export_member_message_ids"] == [1, 2]


@pytest.mark.parametrize("change,reason", [
    ({"direction": "SELL"}, "direction_differs"),
    ({"published_utc": "2026-08-01T10:00:05Z"}, "same_message_publication_differs"),
])
def test_changed_identity_facts_are_not_silently_paired(change, reason):
    row = build_crosswalk([original()], [trigger(**change)], [decision(1, 1)])["pairs"][0]
    assert reason in row["reasons"] and row["comparison_status"] == "not_comparable"


def test_original_retained_as_edit_is_not_original_entry_evidence():
    a = original()
    a["retained_revision_is_edit"] = True
    row = build_crosswalk([a], [trigger()], [decision(1, 1)])["pairs"][0]
    assert row["reasons"] == ["raw_original_revision_not_observed"]


def test_many_originals_to_one_export_group_remain_visible_and_noncomparable():
    result = build_crosswalk([original(1), original(2)], [trigger()], [decision(1, 1), decision(2, 1)])
    rows = [r for r in result["pairs"] if r["scenario"] == "revision_time"]
    assert len(rows) == 2
    assert all("multiple_raw_signals_share_export_trigger" in r["reasons"] for r in rows)
    assert result["export_inventory"][0]["raw_signal_ids"] == ["canal1_1", "canal1_2"]


def test_one_original_to_multiple_export_triggers_is_not_cherry_picked():
    result = build_crosswalk([original()], [trigger(), trigger(2)], [decision(1, 1), decision(1, 2)])
    row = result["pairs"][0]
    assert row["export_trigger"] is None
    assert row["reasons"] == ["multiple_export_triggers_for_raw_message"]
    assert len(row["possible_export_trigger_ids"]) == 2


def test_missing_export_and_unmatched_export_are_both_counted():
    result = build_crosswalk([original()], [trigger(2)], [decision(2, 2)])
    assert len(result["pairs"]) == 2
    assert all(r["reasons"] == ["raw_message_has_no_export_trigger"] for r in result["pairs"])
    assert result["export_inventory"][0]["raw_signal_ids"] == []


def test_negative_delta_is_retained_not_clamped_to_zero():
    row = build_crosswalk([original()], [trigger(trigger_utc="2026-08-01T10:00:00Z")], [decision(1, 1)])["pairs"][0]
    assert row["comparison_status"] == "paired_clock_control"
    assert row["export_minus_receipt_seconds"] == -1


@pytest.mark.parametrize("bad", ["duplicate_raw", "duplicate_export", "unknown_decision", "wrong_chat"])
def test_identity_corruption_fails_closed(bad):
    a, b, d = [original()], [trigger()], [decision(1, 1)]
    if bad == "duplicate_raw": a *= 2
    elif bad == "duplicate_export": b *= 2
    elif bad == "unknown_decision": d = [decision(1, 99)]
    else: b[0]["chat_id"] = 999
    with pytest.raises(ValueError):
        build_crosswalk(a, b, d)


def test_future_unrelated_messages_do_not_change_existing_pairs():
    before = build_crosswalk([original()], [trigger()], [decision(1, 1)])["pairs"]
    later = trigger(9, trigger_utc="2026-08-31T10:00:00Z", published_utc="2026-08-31T10:00:00Z")
    after = build_crosswalk([original()], [trigger(), later], [decision(1, 1), decision(9, 9)])["pairs"]
    assert before == after


@pytest.mark.parametrize("field,value", [("received_utc", "2026-08-01T10:00:01"),
    ("message_id", True), ("retained_revision_is_edit", "false")])
def test_raw_clock_and_identity_types_are_explicit(field, value):
    row = original()
    row[field] = value
    with pytest.raises(ValueError):
        build_crosswalk([row], [trigger()], [decision(1, 1)])


def test_publication_after_receipt_is_preserved_but_not_paired():
    row = original()
    row["received_utc"] = "2026-08-01T09:59:59Z"
    out = build_crosswalk([row], [trigger()], [decision(1, 1)])
    assert out["pairs"][0]["reasons"] == ["raw_publication_after_receipt"]


def test_duplicate_membership_evidence_does_not_duplicate_a_signal():
    a = build_crosswalk([original()], [trigger()], [decision(1, 1)])
    b = build_crosswalk([original()], [trigger()], [decision(1, 1)] * 2)
    assert a == b


def audit_fixture(tmp_path, *, stamp="2026-08-01T10:00:00Z"):
    import hashlib
    import json
    from dataclasses import asdict
    from datetime import timedelta
    from research.causal_replay import compile_signals, utc
    from research.dubai_clock_audit import _signal_record, _write
    from research.strategy_study import _digest, _sha

    tmp_path.mkdir(parents=True, exist_ok=True)
    date = stamp[:10]
    raw_dir, stream_dir = tmp_path / "raw", tmp_path / "export"
    raw_dir.mkdir()
    stream_dir.mkdir()
    raw = dict(ev="telegram_raw", channel="canal1", chat_id=-1001642806869, message_id=1,
        update_kind="new_message", is_edit=False, revision_token="initial", text="", text_sha1=None,
        has_text=False, has_media=True, media_sha256=None, date_utc=stamp,
        edit_date_utc=None, ts=(utc(stamp) + timedelta(seconds=1)).isoformat(), sticker_id="5", reply_to_msg_id=None)
    canonical = {key: raw[key] for key in ("chat_id", "media_sha256", "message_id", "revision_token", "text_sha1")}
    raw["message_revision_id"] = "msgrev_" + hashlib.sha256(json.dumps(canonical,
        ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    source = tmp_path / "source.jsonl"
    _write(source, raw)
    protocol = {"contract": "canal1_recursive_data_freeze_v2",
        "development": {"from": date}, "challenge": {"through": date},
        "sticker_directions": {"5": "BUY"},
        "raw_source": {"path": str(source), "sha256": _digest(source), "bytes": source.stat().st_size}}
    _write(raw_dir / "data_protocol.json", protocol)
    _write(raw_dir / "admission_summary.json", {"data_protocol_sha256": _digest(raw_dir / "data_protocol.json")})
    signals, diagnostics = compile_signals([raw], start=utc(date + "T00:00:00Z"),
        cutoff=utc(date + "T00:00:00Z") + timedelta(days=1), sticker_directions={"5": "BUY"})
    _write(raw_dir / "global_trigger_identity.json", {"signals": [_signal_record(asdict(s)) for s in signals],
        "diagnostics": list(diagnostics), "selected_window_trigger_count": 1})
    _write(stream_dir / "triggers.jsonl", trigger(published_utc=stamp,
        trigger_utc=(utc(stamp) + timedelta(seconds=60)).isoformat()))
    _write(stream_dir / "decisions.jsonl", decision(1, 1))
    manifest = {"schema_version": "dubai_entry_stream_v1", "inputs": {"watched_files": {}},
        "artifacts": {name: {"sha256": _digest(stream_dir / name)} for name in ("triggers.jsonl", "decisions.jsonl")}}
    manifest["stream_identity_sha256"] = _sha(manifest)
    _write(stream_dir / "manifest.json", manifest)
    return raw_dir, stream_dir, tmp_path / "out", source


def test_real_compiler_source_hashes_archive_and_fresh_verifier(tmp_path, case):
    from research.dubai_clock_audit import run_audit, verify_audit
    raw, stream, out, source = audit_fixture(tmp_path)
    summary = run_audit(raw, stream, out)
    assert summary["scenarios"]["revision_time"]["paired"] == 1
    assert verify_audit(out)["status"] == "verified_identity_audit_only"
    with pytest.raises(ValueError, match="immutable"):
        run_audit(raw, stream, out)
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="hash|size"):
        verify_audit(out)


@pytest.mark.parametrize("corrupt", ["delete", "edit", "reseal_wrong_count"])
def test_incomplete_or_changed_archive_cannot_pass(tmp_path, corrupt, case):
    from research.dubai_clock_audit import run_audit, verify_audit
    from research.strategy_study import _read, _encode, _digest, _sha
    raw, stream, out, source = audit_fixture(tmp_path)
    run_audit(raw, stream, out)
    target = out / "summary.json"
    if corrupt == "delete":
        target.unlink()
    else:
        row = _read(target)
        row["scenarios"]["revision_time"]["paired"] = 99
        target.write_bytes(_encode(row))
        if corrupt == "reseal_wrong_count":
            manifest = _read(out / "manifest.json")
            manifest["artifacts"]["summary.json"]["sha256"] = _digest(target)
            del manifest["audit_identity_sha256"]
            manifest["audit_identity_sha256"] = _sha(manifest)
            (out / "manifest.json").write_bytes(_encode(manifest))
    with pytest.raises(ValueError):
        verify_audit(out)


def test_audit_still_blocks_live_module_in_a_clean_study_process(tmp_path, case, monkeypatch):
    import sys
    from types import SimpleNamespace
    from research.dubai_clock_audit import run_audit
    raw, stream, out, source = audit_fixture(tmp_path)
    monkeypatch.setitem(sys.modules, "MetaTrader5", SimpleNamespace())
    with pytest.raises(ValueError, match="offline import boundary"):
        run_audit(raw, stream, out)
    assert not out.exists()
