import hashlib
import json
import subprocess
import sys

import pytest

from research.telegram_export import load_admission, prepare_exports, to_causal_signals, write_admission


START = "2026-01-01T00:00:00Z"
END = "2027-01-01T00:00:00Z"
PUB = "1775633418"
EDIT = "1775633624"


def message(identity=1, text="XAU USD BUY NOW", **fields):
    return {"id": identity, "type": "message", "date_unixtime": PUB,
            "date": "2026-04-08T09:30:18", "text": text, **fields}


def source(tmp_path, rows, name="export.json", chat_id=3828356530):
    path = tmp_path / name
    path.write_text(json.dumps({"id": chat_id, "name": "test", "type": "private_channel",
                               "messages": rows}), encoding="utf-8")
    return path


def prepare(paths):
    return prepare_exports(paths, start=START, end=END)


def test_source_inventory_and_utc_never_uses_folder_or_naive_display_date(tmp_path):
    path = source(tmp_path, [message(), message(2, date_unixtime="1688595810"),
                             message(3, date_unixtime=None)], "ChatExport_2099.json")
    result = prepare([path])
    inventory = result["sources"][0]
    assert inventory["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert inventory["period_counts"] == {"inside": 1, "outside": 1, "unknown": 1}
    assert inventory["message_ids"] == [1, 2, 3]
    assert result["identities"][0]["revisions"][0]["published_utc"] == "2026-04-08T07:30:18Z"
    assert result["sources"][0]["exported_at_utc"] is None
    assert len(result["identities"]) == 3


def test_initial_text_only_and_no_provider_levels_required(tmp_path):
    result = prepare([source(tmp_path, [message()])])
    assert len(result["triggers"]["publication_initial"]) == 1
    assert result["triggers"]["receipt"] == []
    row = result["triggers"]["publication_initial"][0]
    assert row["direction"] == "BUY"
    assert row["received_utc"] is None
    assert row["provider_events"] == []
    assert result["summary"]["scenarios"]["receipt"]["blocked"] == 1
    assert "receipt_unknown" in result["identities"][0]["admission"]["receipt"]["reasons"]


def test_final_edit_cannot_rewrite_initial_and_revision_scenario_is_delayed(tmp_path):
    result = prepare([source(tmp_path, [message(edited_unixtime=EDIT, edited="2026-04-08T09:33:44")])])
    assert result["triggers"]["publication_initial"] == []
    trigger = result["triggers"]["revision_time"][0]
    assert trigger["trigger_utc"] == "2026-04-08T07:33:44Z"
    assert trigger["initial_content_known"] is False
    assert "initial_version_missing" in result["identities"][0]["admission"]["publication_initial"]["reasons"]


def test_overlapping_sources_retain_raw_variants_without_double_counting(tmp_path):
    a = message(text=["XAU USD ", {"type": "bold", "text": "BUY NOW"}])
    b = {**a, "reactions": [{"count": 7}]}
    result = prepare([source(tmp_path, [a, message(2)]), source(tmp_path, [b], "b.json")])
    identity = result["identities"][0]
    assert len(result["occurrences"]) == 3
    assert len(result["identities"]) == 2
    assert len(identity["revisions"]) == 1
    assert len(identity["revisions"][0]["occurrence_ids"]) == 2
    assert len(identity["raw_snapshot_hashes"]) == 2
    assert len(result["triggers"]["publication_initial"]) == 2
    assert result["summary"]["duplicate_identity_occurrences"] == 1
    assert result["text_duplicate_groups"][0]["identity_ids"] == [
        "telegram_export:3828356530:1", "telegram_export:3828356530:2"]


def test_initial_and_later_direction_are_distinct_known_revisions(tmp_path):
    paths = [source(tmp_path, [message()]), source(tmp_path, [message(text="XAU USD SELL NOW", edited_unixtime=EDIT)], "b.json")]
    result = prepare(paths)
    assert len(result["identities"][0]["revisions"]) == 2
    for mode in ("publication_initial", "revision_time"):
        assert result["triggers"][mode][0]["direction"] == "BUY"
        assert len(result["triggers"][mode]) == 1


def test_same_revision_time_conflicting_text_fails_closed(tmp_path):
    result = prepare([source(tmp_path, [message()]), source(tmp_path, [message(text="XAU USD SELL NOW")], "b.json")])
    assert result["triggers"]["publication_initial"] == []
    assert "revision_content_conflict" in result["identities"][0]["admission"]["revision_time"]["reasons"]


@pytest.mark.parametrize("fields,reason", [
    ({"date_unixtime": None}, "publication_time_unknown"),
    ({"edited": "2026-04-08T10:00:00"}, "edit_time_unknown"),
    ({"edited_unixtime": "1"}, "edit_before_publication"),
    ({"date_unixtime": True}, "publication_time_unknown"),
    ({"id": None}, "message_identity_missing"),
])
def test_invalid_evidence_remains_in_denominator(tmp_path, fields, reason):
    result = prepare([source(tmp_path, [message(**fields)])])
    assert result["triggers"]["revision_time"] == []
    assert reason in result["identities"][0]["admission"]["revision_time"]["reasons"]
    assert result["summary"]["scenarios"]["revision_time"]["blocked"] == 1


@pytest.mark.parametrize("text,extra,reason", [
    ("", {"media_type": "sticker", "file": "stickers/BUY.webp"}, "unknown_sticker_direction"),
    ("If GOLD BUY NOW then wait", {}, "unresolved_directional_text"),
    ("XAU USD BUY NOW and SELL NOW", {}, "ambiguous_direction"),
    ("BUY ZONE 4300-4305", {}, "zone_plan_not_immediate"),
    ("", {"photo": "not included"}, "media_without_text"),
    ([{"type": "bold"}], {}, "malformed_text"),
])
def test_unresolved_content_never_becomes_a_trigger(tmp_path, text, extra, reason):
    result = prepare([source(tmp_path, [message(text=text, **extra)])])
    assert result["triggers"]["revision_time"] == []
    assert reason in result["identities"][0]["admission"]["revision_time"]["reasons"]


def test_period_is_half_open_and_edit_outside_does_not_backdate(tmp_path):
    result = prepare([source(tmp_path, [message(1, date_unixtime="1798761600"),
                                       message(2, edited_unixtime="1798761600")])])
    assert result["triggers"]["revision_time"] == []
    assert "trigger_outside_period" in result["identities"][1]["admission"]["revision_time"]["reasons"]


def test_cohorts_stay_separate_and_unknown_channel_blocks(tmp_path):
    result = prepare([source(tmp_path, [message()]),
                      source(tmp_path, [message()], "dubai.json", 1642806869),
                      source(tmp_path, [message()], "current.json", 3908582492)])
    assert len(result["identities"]) == 3
    assert len(result["triggers"]["publication_initial"]) == 2
    assert "unsupported_chat_id" in result["identities"][2]["admission"]["publication_initial"]["reasons"]


def test_engine_handoff_requires_named_scenario_and_retains_chat_identity(tmp_path):
    result = prepare([source(tmp_path, [message()])])
    signals = to_causal_signals(result, scenario="publication_initial", chat_id=3828356530)
    assert len(signals) == 1
    assert signals[0].signal_id == "telegram_export:3828356530:1"
    assert signals[0].channel == "canal2"
    assert signals[0].provider_events == ()
    assert signals[0].observed_at.isoformat() == "2026-04-08T07:30:18+00:00"
    with pytest.raises(ValueError):
        to_causal_signals(result, scenario="inferred_receipt", chat_id=3828356530)


def test_artifacts_reproducible_immutable_and_lossless(tmp_path):
    path = source(tmp_path, [message()])
    before = path.read_bytes()
    result = prepare([path])
    out = tmp_path / "admission"
    manifest = write_admission(result, out)
    assert write_admission(prepare([path]), out) == manifest
    restored = load_admission(out)
    assert restored == result
    assert to_causal_signals(restored, scenario="publication_initial", chat_id=3828356530)
    occurrences = [json.loads(line) for line in (out / "occurrences.jsonl").read_text().splitlines()]
    assert occurrences[0]["raw"] == message()
    for name, identity in manifest["artifacts"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == identity["sha256"]
    (out / "summary.json").write_text("conflict")
    with pytest.raises(ValueError, match="hash"):
        load_admission(out)
    with pytest.raises(ValueError, match="immutable"):
        write_admission(result, out)
    assert path.read_bytes() == before


def test_invalid_period_and_top_level_schema_fail_closed(tmp_path):
    path = source(tmp_path, [message()])
    with pytest.raises(ValueError):
        prepare_exports([path], start="2026-01-01", end=END)
    with pytest.raises(ValueError):
        prepare_exports([path], start=END, end=START)
    path.write_text('{"messages": {}}')
    with pytest.raises(ValueError):
        prepare([path])


def test_preparation_import_does_not_load_live_modules():
    code = "import sys; from research.telegram_export import to_causal_signals, SCHEMA_VERSION; to_causal_signals({'contract': {'schema_version': SCHEMA_VERSION}, 'triggers': {'receipt': []}}, scenario='receipt', chat_id=3828356530); assert not {'MetaTrader5', 'listener', 'classifier', 'telethon'} & set(sys.modules)"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_cli_writes_and_verifies_expected_hash(tmp_path):
    path = source(tmp_path, [message()])
    command = [sys.executable, "tools/prepare_telegram_exports.py", "--source", str(path),
               "--start", START, "--end", END, "--output-dir", str(tmp_path / "out")]
    process = subprocess.run(command, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["identities"] == 1
    failed = subprocess.run(command + ["--expected-sha256", "0" * 64], capture_output=True, text=True)
    assert failed.returncode != 0


def test_bridge_reaches_existing_path_builder_without_provider_levels(tmp_path):
    from datetime import timedelta
    import numpy as np
    from research.causal_replay import make_path, time_ns
    from research.dubai_iterative.contracts import StrategyGenome

    result = prepare([source(tmp_path, [message(edited_unixtime=EDIT)])])
    signal = to_causal_signals(result, scenario="revision_time", chat_id=3828356530)[0]
    genome = StrategyGenome(schema_version=2, entry_mode="signal_market", leg_count=1,
        volume_weights=(0.01,), target_mode="none", stop_mode="none", be_mode="none",
        time_exit_min=180, provider_management_mode="exact")
    times = np.array([time_ns(signal.observed_at) - 1_000_000_000,
                      time_ns(signal.observed_at), time_ns(signal.observed_at) + 1_000_000_000])
    path = make_path(signal, genome,
        market=(times, np.array([4300., 4301., 4302.]), np.array([4300.2, 4301.2, 4302.2])),
        conversion=(times, np.ones(3), np.ones(3)), cutoff=signal.observed_at + timedelta(seconds=1),
        contract_size=100., currency_digits=2, max_fx_age_ms=5000,
        market_sha256="a" * 64, conversion_sha256="b" * 64)
    assert path.signal_id == "telegram_export:3828356530:1"
    assert path.times_ns.tolist() == times[1:].tolist()
    assert path.actual_pnl_eur is None
    assert path.provider_events == ()
    assert path.legs[0].sl_events == path.legs[0].tp_events == ()
    assert path.entry_expiry_anchor_at == signal.published_at


def test_same_text_at_later_edit_is_not_an_exact_duplicate_revision(tmp_path):
    result = prepare([source(tmp_path, [message(edited_unixtime=EDIT)]),
                      source(tmp_path, [message(edited_unixtime=str(int(EDIT) + 5))], "b.json")])
    assert len(result["identities"][0]["revisions"]) == 2
    assert result["identities"][0]["distinct_text_contents"] == 1
    assert len(result["triggers"]["revision_time"]) == 1


def test_no_trigger_is_restarted_by_a_later_in_period_revision(tmp_path):
    original = message(date_unixtime="1767225599")
    edited = {**original, "edited_unixtime": PUB}
    result = prepare([source(tmp_path, [original]), source(tmp_path, [edited], "b.json")])
    assert result["triggers"]["revision_time"] == []


def test_json_duplicate_keys_and_nonfinite_constants_are_rejected(tmp_path):
    path = tmp_path / "bad.json"
    for payload in ('{"id":3828356530,"id":1642806869,"messages":[]}',
                    '{"id":3828356530,"messages":[NaN]}'):
        path.write_text(payload)
        with pytest.raises(ValueError):
            prepare([path])


def test_malformed_row_is_retained_and_false_id_does_not_alias_id_one(tmp_path):
    result = prepare([source(tmp_path, [None, message(id=True), message(1)])])
    assert len(result["identities"]) == 3
    assert len(result["occurrences"]) == 3
    assert len(result["triggers"]["publication_initial"]) == 1


def test_publication_conflict_is_visible_not_silently_deduplicated(tmp_path):
    result = prepare([source(tmp_path, [message()]),
                      source(tmp_path, [message(date_unixtime=str(int(PUB) + 1))], "b.json")])
    assert result["triggers"]["revision_time"] == []
    assert "publication_time_conflict" in result["identities"][0]["integrity_issues"]
