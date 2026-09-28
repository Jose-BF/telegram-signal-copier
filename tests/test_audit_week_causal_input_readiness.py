import gzip
import json

import pytest

from tools.audit_native_money_anchor import digest
from tools.audit_week_causal_input_readiness import audit, coverage


START = "2026-09-14T00:00:00+00:00"
END = "2026-09-19T00:00:00+00:00"


def message(index):
    return {"ev": "telegram_raw", "channel": "canal2", "message_id": index,
            "message_revision_id": f"rev-{index}",
            "date_utc": "2026-09-17T12:00:00+00:00",
            "ts": f"2026-09-17T12:00:0{index}+00:00",
            "text": "Buy Gold Now", "is_edit": False, "edit_date_utc": None,
            "reply_to_msg_id": None, "sticker_id": None}


def sources(tmp_path, *, raw_ids=(1, 2), received_ids=(1, 2)):
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(json.dumps({"contract": "frozen_raw_telegram_slice_v1",
                                    "causal_input_only": True,
                                    "complete_week_claim": False,
                                    "start_utc": START, "end_utc": END,
                                    "source_slice_sha256": "a" * 64,
                                    "start_offset": 100, "end_offset": 200,
                                    "raw_row_count": len(raw_ids),
                                    "rows": [message(index) for index in raw_ids]}),
                        encoding="utf-8")
    shadow_path = tmp_path / "shadow.jsonl.gz"
    with gzip.open(shadow_path, "wt", encoding="utf-8") as stream:
        for index in received_ids:
            stream.write(json.dumps({"ev": "signal_received", "sig": f"canal2_{index}",
                                     "ts": f"2026-09-17T12:00:1{index}+00:00"}) + "\n")
    manifest_path = tmp_path / "shadow.manifest.json"
    manifest_path.write_text(json.dumps({"status": "complete", "output_sha256": digest(shadow_path),
                                         "source_slice_sha256": "a" * 64,
                                         "start_offset": 100, "end_offset": 200}), encoding="utf-8")
    return raw_path, shadow_path, manifest_path


def test_all_raw_signals_survive_even_without_observed_basket(tmp_path):
    report = audit(*sources(tmp_path, raw_ids=(1, 2, 3), received_ids=(1, 2)),
                   start_utc=START, end_utc=END)
    assert report["received_signal_count"] == 2
    assert report["compiled_signal_count"] == 3
    assert report["raw_replay_not_received_by_bot"] == ["canal2_3"]
    assert report["all_received_signals_covered"] is True
    assert report["compiled_signal_ids"] == ["canal2_1", "canal2_2", "canal2_3"]
    assert report["status"] == "diagnostic_only"
    assert report["full_simulator_parity_verified"] is False


def test_missing_raw_signal_is_visible_not_invented(tmp_path):
    report = audit(*sources(tmp_path, raw_ids=(1,), received_ids=(1, 2)),
                   start_utc=START, end_utc=END)
    assert report["received_missing_from_raw_replay"] == ["canal2_2"]
    assert report["all_received_signals_covered"] is False
    assert report["status"] == "blocked"


def test_delayed_telegram_backfill_is_retained_but_not_replayed_as_fresh_entry(tmp_path):
    paths = sources(tmp_path, raw_ids=(1, 2, 3), received_ids=(1, 2))
    raw = json.loads(paths[0].read_text(encoding="utf-8"))
    raw["rows"][2]["date_utc"] = "2026-09-16T12:00:03+00:00"
    paths[0].write_text(json.dumps(raw), encoding="utf-8")

    report = audit(*paths, start_utc=START, end_utc=END)

    assert report["compiled_signal_ids"] == ["canal2_1", "canal2_2"]
    assert report["stale_entry_candidate_ids"] == ["canal2_3"]
    assert report["raw_replay_not_received_by_bot"] == []
    assert report["all_received_signals_covered"] is True


def test_text_only_received_signal_is_visible_as_unresolved_causal_candidate(tmp_path):
    paths = sources(tmp_path, raw_ids=(1,), received_ids=(1,))
    raw = json.loads(paths[0].read_text(encoding="utf-8"))
    text = message(3)
    text.update(channel="canal1", text="BUY GOLD NOW 4366 TP1: 4370 SL: 4355")
    raw["rows"].append(text)
    raw["raw_row_count"] = 2
    paths[0].write_text(json.dumps(raw), encoding="utf-8")
    with gzip.open(paths[1], "at", encoding="utf-8") as stream:
        stream.write(json.dumps({"ev": "signal_received", "sig": "canal1_3",
                                 "ts": "2026-09-17T12:00:13+00:00"}) + "\n")
    manifest = json.loads(paths[2].read_text(encoding="utf-8"))
    manifest["output_sha256"] = digest(paths[1])
    paths[2].write_text(json.dumps(manifest), encoding="utf-8")

    report = audit(*paths, start_utc=START, end_utc=END)

    assert report["compiled_signal_ids"] == ["canal2_1"]
    assert report["text_candidate_signal_ids"] == ["canal1_3"]
    assert report["received_missing_from_raw_replay"] == ["canal1_3"]
    assert report["received_matching_raw_candidates"] == ["canal1_3"]
    assert report["received_unexplained_by_raw"] == []
    assert report["all_received_signals_covered"] is False
    assert report["status"] == "blocked"


def test_duplicate_compiled_ids_and_tampered_sources_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicated"):
        coverage(["canal2_1", "canal2_1"], ["canal2_1"])
    assert coverage([], [])["all_received_signals_covered"] is False
    paths = sources(tmp_path)
    raw = json.loads(paths[0].read_text(encoding="utf-8"))
    raw["rows"][0]["position_id"] = 123
    paths[0].write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="noncausal"):
        audit(*paths, start_utc=START, end_utc=END)
    paths[0].write_text(json.dumps({**raw, "rows": [message(1), message(2)]}), encoding="utf-8")
    manifest = json.loads(paths[2].read_text(encoding="utf-8"))
    manifest["source_slice_sha256"] = "b" * 64
    paths[2].write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="same frozen source slice"):
        audit(*paths, start_utc=START, end_utc=END)
