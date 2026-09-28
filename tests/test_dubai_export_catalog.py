import hashlib
import json
import subprocess
import sys

import pytest

from research.dubai_export_catalog import build_catalog, write_catalog
from research.telegram_export import prepare_exports, write_admission


PUB = 1767358244
BUY = b"fixture BUY GOLD image"
SELL = b"fixture SELL GOLD image"


def message(mid=1, text="", **extra):
    return {"id": mid, "type": "message", "date_unixtime": str(PUB),
            "text": text, **extra}


def sticker(mid=1, **extra):
    return message(mid, **{"media_type": "sticker", "file": "stickers/one.webp", **extra})


def setup(tmp_path, rows, *, second=None, media=BUY, second_media=BUY, chat_id=1642806869):
    sources = []
    for name, messages, data in (("first", rows, media), ("second", second, second_media)):
        if messages is None:
            continue
        root = tmp_path / name
        (root / "stickers").mkdir(parents=True)
        (root / "stickers" / "one.webp").write_bytes(data)
        path = root / "result.json"
        path.write_text(json.dumps({"id": chat_id, "messages": messages}), encoding="utf-8")
        sources.append(path)
    bundle = prepare_exports(sources, start="2026-01-01T00:00:00Z", end="2026-09-14T00:00:00Z")
    admission = tmp_path / "admission"
    write_admission(bundle, admission)
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"schema_version": "reviewed_sticker_labels_v1", "labels": [
        {"sha256": hashlib.sha256(data).hexdigest(), "direction": direction,
         "symbol": "XAUUSD", "evidence": "synthetic fixture, no visual claim"}
        for data, direction in ((BUY, "BUY"), (SELL, "SELL"))]}))
    return admission, labels, sources


def test_catalog_deduplicates_identity_retains_sources_and_publication_without_receipt(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker()], second=[sticker()])
    result = build_catalog(admission, labels)
    assert result["summary"]["gold_sticker_candidates"] == 1
    row = result["sticker_signals"][0]
    assert len(row["snapshots"]) == 2
    assert row["direction"] == "BUY"
    assert row["symbol"] == "XAUUSD"
    assert row["published_utc"] == "2026-01-02T12:50:44Z"
    assert row["received_utc"] is row["trigger_utc"] is None
    assert not row["engine_admitted"]
    assert result["contract"]["temporal_split"] is None
    assert not result["contract"]["engine_dataset_ready"]


def test_edited_snapshot_never_becomes_initial_or_moves_publication(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker(edited_unixtime=str(PUB + 7200))])
    row = build_catalog(admission, labels)["sticker_signals"][0]
    assert row["timing_status"] == "edited_snapshot_only"
    assert not row["has_unedited_directional_snapshot"]
    assert row["published_utc"] == "2026-01-02T12:50:44Z"
    assert row["snapshots"][0]["edited_utc"] == "2026-01-02T14:50:44Z"
    assert row["trigger_utc"] is None


def test_older_unedited_snapshot_remains_available_without_replacing_revision(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker()], second=[sticker(edited_unixtime=str(PUB + 10))])
    row = build_catalog(admission, labels)["sticker_signals"][0]
    assert row["has_unedited_directional_snapshot"]
    assert row["has_edited_snapshot"]
    assert len({item["revision_id"] for item in row["snapshots"]}) == 2


def test_same_descriptor_different_media_bytes_cannot_hide_direction_conflict(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker()], second=[sticker()], second_media=SELL)
    row = build_catalog(admission, labels)["sticker_signals"][0]
    assert len({item["revision_id"] for item in row["snapshots"]}) == 1
    assert row["direction"] is None
    assert "direction_conflict" in row["issues"]
    assert row["timing_status"] == "unresolved"


def test_companion_is_only_a_proposal_and_does_not_add_an_entry(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker(), message(2, "GOLD BUY NOW 4300", date_unixtime=str(PUB + 43))])
    result = build_catalog(admission, labels)
    assert len(result["sticker_signals"]) == 1
    text = result["text_entries"][0]
    assert text["companion_proposal"]["signal_id"].endswith(":1")
    assert text["companion_proposal"]["confirmed"] is False
    assert text["published_utc"] == "2026-01-02T12:51:27Z"
    assert result["summary"]["confirmed_unique_entries"] is None


def test_again_text_is_not_swallowed_even_seconds_after_sticker(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker(),
        message(2, "GOLD BUY NOW 4300", date_unixtime=str(PUB + 43)),
        message(3, "GOLD BUY again NOW 4290", date_unixtime=str(PUB + 60))])
    result = build_catalog(admission, labels)
    repeat = result["text_entries"][1]
    assert repeat["companion_proposal"] is None
    assert "explicit_again_possible_new_entry" in repeat["review_reasons"]
    assert len(result["sticker_signals"][0]["companion_text_ids"]) == 1


def test_contextual_now_phrase_is_kept_for_review_not_linked(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker(), message(2,
        "SL to BE got triggered. We're back in the buy zone now.\nWait patiently for the next clear entry.", date_unixtime=str(PUB + 20))])
    row = build_catalog(admission, labels)["text_entries"][0]
    assert "now_phrase_in_context_not_entry_header" in row["review_reasons"]
    assert row["companion_proposal"] is None


@pytest.mark.parametrize("extra,reason", [
    ({"forwarded_from": "VIP 3.0"}, "nearby_sticker_origin_mismatch"),
    ({"date_unixtime": str(PUB)}, "same_second_order_unresolved"),
    ({"text": "SELL GOLD NOW 4300"}, "nearby_sticker_direction_mismatch"),
])
def test_origin_direction_and_order_mismatches_are_not_merged(tmp_path, extra, reason):
    fields = {"text": "GOLD BUY NOW 4300", "date_unixtime": str(PUB + 20), **extra}
    admission, labels, _ = setup(tmp_path, [sticker(), message(2, **fields)])
    row = build_catalog(admission, labels)["text_entries"][0]
    assert row["companion_proposal"] is None
    assert reason in row["review_reasons"]


def test_newer_sticker_blocks_pairing_with_older_direction(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker(), sticker(2, file="missing.webp", date_unixtime=str(PUB + 10)),
        message(3, "GOLD BUY NOW 4300", date_unixtime=str(PUB + 20))])
    result = build_catalog(admission, labels)
    assert result["text_entries"][0]["companion_proposal"] is None
    assert result["text_entries"][0]["nearby_sticker"]["signal_id"].endswith(":2")


def test_two_nearby_texts_without_again_still_need_review(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker(),
        message(2, "GOLD BUY NOW 4300", date_unixtime=str(PUB + 20)),
        message(3, "GOLD BUY NOW 4301", date_unixtime=str(PUB + 30))])
    result = build_catalog(admission, labels)
    assert "multiple_texts_for_sticker" in result["sticker_signals"][0]["review_reasons"]
    assert all("multiple_texts_for_sticker" in row["review_reasons"] for row in result["text_entries"])


def test_forwarded_publication_keeps_channel_post_time_not_original_time(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker(forwarded_from="VIP 3.0", forwarded_date="2023-01-01T00:00:00")])
    result = build_catalog(admission, labels)
    assert result["summary"]["forwarded_gold_stickers"] == 1
    assert result["sticker_signals"][0]["published_utc"] == "2026-01-02T12:50:44Z"


@pytest.mark.parametrize("path", ["../outside.webp", "C:\\outside.webp", "\\\\server\\share\\secret", "/outside.webp"])
def test_media_path_cannot_escape_source_directory(tmp_path, path):
    admission, labels, _ = setup(tmp_path, [sticker(file=path)])
    row = build_catalog(admission, labels)["messages"][0]
    assert "media_path_unsafe" in row["issues"]
    assert row["direction"] is None


def test_unknown_media_and_ordinary_messages_remain_in_denominator(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker(), message(2, "Good morning")], media=b"unknown")
    result = build_catalog(admission, labels)
    assert result["summary"]["messages"] == 2
    assert result["summary"]["gold_sticker_candidates"] == 0
    assert result["messages"][0]["kind"] == "unmapped_sticker"


def test_service_message_cannot_become_a_directional_sticker(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker(type="service")])
    result = build_catalog(admission, labels)
    assert result["summary"]["messages"] == 1
    assert result["summary"]["gold_sticker_candidates"] == 0
    assert result["messages"][0]["direction"] is None


def test_missing_time_and_outside_period_are_never_silently_admitted(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker(date_unixtime=None),
        sticker(2, date_unixtime="1688595810")])
    result = build_catalog(admission, labels)
    assert result["summary"]["messages"] == 1
    row = result["sticker_signals"][0]
    assert row["published_utc"] is row["trigger_utc"] is None
    assert row["timing_status"] == "unresolved"
    assert "publication_time_unknown" in row["issues"]


def test_changed_source_or_corrupted_archive_fails_closed(tmp_path):
    admission, labels, sources = setup(tmp_path, [sticker()])
    original = sources[0].read_bytes()
    sources[0].write_bytes(original + b" ")
    with pytest.raises(ValueError, match="source hash"):
        build_catalog(admission, labels)
    sources[0].write_bytes(original)
    (admission / "summary.json").write_text("{}")
    with pytest.raises(ValueError, match="hash"):
        build_catalog(admission, labels)


def test_unknown_chat_duplicate_label_and_invalid_window_fail(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker()], chat_id=3828356530)
    with pytest.raises(ValueError, match="Dubai"):
        build_catalog(admission, labels)
    with pytest.raises(ValueError, match="companion_seconds"):
        build_catalog(admission, labels, companion_seconds=True)
    data = json.loads(labels.read_text())
    data["labels"].append(data["labels"][0])
    labels.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="duplicate"):
        build_catalog(admission, labels)


def test_catalog_is_immutable_reproducible_and_does_not_overwrite_inputs(tmp_path):
    admission, labels, sources = setup(tmp_path, [sticker()])
    result = build_catalog(admission, labels)
    out = tmp_path / "catalog"
    manifest = write_catalog(result, out)
    assert write_catalog(build_catalog(admission, labels), out) == manifest
    for name, expected in manifest["artifacts"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == expected["sha256"]
    with pytest.raises(ValueError, match="input evidence"):
        write_catalog(result, admission)
    (out / "summary.json").write_text("conflict")
    with pytest.raises(ValueError, match="immutable"):
        write_catalog(result, out)
    assert sources[0].is_file()


def test_cli_is_offline_and_emits_catalog_summary(tmp_path):
    admission, labels, _ = setup(tmp_path, [sticker()])
    run = subprocess.run([sys.executable, "tools/prepare_dubai_export_catalog.py",
        "--admission-dir", str(admission), "--labels", str(labels), "--output-dir", str(tmp_path / "out")],
        capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["gold_sticker_candidates"] == 1
    subprocess.run([sys.executable, "-c", "import sys; import research.dubai_export_catalog; assert not {'MetaTrader5', 'telethon', 'listener', 'classifier'} & set(sys.modules)"], check=True)
