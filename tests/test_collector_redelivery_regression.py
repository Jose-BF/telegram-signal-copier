"""Public, offline delta regressions with synthetic Telegram observations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import time

import pytest

from tools import collect_simulator_window as collector


WINDOW = collector.CaptureWindow("2026-09-10T13:00:00+00:00", "2026-09-10T15:00:00+00:00")
CONTENT_MUTATIONS = {
    "channel": "canal1", "message_id": 102,
    "date_utc": "2026-09-10T12:59:01+00:00",
    "edit_date_utc": "2026-09-10T13:00:01+00:00",
    "text": "SELL GOLD NOW", "sticker_id": 999, "reply_to_msg_id": 100,
}


def observation(**changes):
    row = {
        "ev": "telegram_raw", "channel": "canal2", "chat_id": -100123,
        "message_id": 101, "message_revision_id": "canal2:101:original",
        "revision_token": "original", "date_utc": "2026-09-10T12:59:00+00:00",
        "ts": "2026-09-10T13:00:00+00:00", "edit_date_utc": None,
        "text": "BUY GOLD NOW", "text_sha1": hashlib.sha1(b"BUY GOLD NOW").hexdigest(),
        "has_text": True, "has_media": False, "sticker_id": None,
        "has_photo": False, "has_document": False, "reply_to_msg_id": None,
        "is_reply": False, "media_sha256": None,
        "is_edit": False, "update_kind": "new_message", "event_id": "observation-1",
    }
    return {**row, **changes}


def redelivery(**changes):
    return observation(ts="2026-09-10T13:01:00+00:00", is_edit=True,
                       update_kind="message_edited", event_id="observation-2", **changes)


def project(row):
    return {key: row.get(key) for key in collector.RAW_FIELDS}


def capture(tmp_path, events, *, prior_raw=(), availability=None):
    prefix = b" " * (collector.BOUNDARY_BYTES - 1) + b"\n"
    delta = b"".join(json.dumps(row).encode() + b"\n" for row in events)
    source = tmp_path / "source.jsonl"
    source.write_bytes(prefix + delta)
    before = source.read_bytes()
    anchor = {
        "prefix_bytes": len(prefix), "boundary_sha256": hashlib.sha256(prefix).hexdigest(),
        "source_chain_sha256": "a" * 64, "manifest_sha256": "b" * 64,
        "inherited_whole_prefix_sha256": hashlib.sha256(prefix).hexdigest(),
        "manifest": {
            "contract": collector.CONTRACT, "window_start_utc": WINDOW.start.isoformat(),
            "window_end_exclusive_utc": WINDOW.end.isoformat(),
            "event_evidence": {"raw_message_causal_availability": availability or {}},
        },
    }
    prior_raw = list(prior_raw)
    originals = deepcopy((anchor, prior_raw))
    output = tmp_path / "delta.jsonl.gz"
    evidence, context, rows = collector.capture_event_delta(
        source, anchor, len(before), output, max_rows=100, max_seconds=10,
        prior_context={}, prior_raw=prior_raw, started=time.monotonic(),
        window=WINDOW, cutoff=datetime(2026, 9, 10, 13, 14, tzinfo=timezone.utc),
    )
    assert source.read_bytes() == before
    assert (anchor, prior_raw) == originals
    assert gzip.decompress(output.read_bytes()) == delta
    assert evidence["delta_sha256"] == hashlib.sha256(delta).hexdigest()
    assert evidence["delta_gzip_sha256"] == collector.sha256_file(output)
    assert evidence["inherited_whole_prefix_sha256"] == anchor["inherited_whole_prefix_sha256"]
    assert evidence["anchor_manifest_sha256"] == anchor["manifest_sha256"]
    assert evidence["delta_rows"] == len(events)
    assert rows == prior_raw + [project(row) for row in events]
    assert evidence["raw_message_causal_availability"]["window_rows_cumulative"] == len(rows)
    assert evidence["raw_message_causal_availability"]["new_rows"] == len(events)
    return evidence["raw_message_causal_availability"]


@pytest.mark.parametrize("split", ["same_batch", "prior_to_new", "all_prior"])
def test_transport_redelivery_preserves_every_observation_without_conflicts(tmp_path, split):
    first, second = observation(), redelivery()
    prior = [] if split == "same_batch" else [project(first)]
    events = [first, second] if split == "same_batch" else [second]
    if split == "all_prior":
        prior.append(project(second))
        events = []
    availability = capture(tmp_path, events, prior_raw=prior)
    assert availability["revision_conflicts"] == []
    assert availability["missing_fields"] == {}


@pytest.mark.parametrize("field,value", CONTENT_MUTATIONS.items())
@pytest.mark.parametrize("split", ["same_batch", "prior_to_new", "all_prior"])
def test_content_changes_still_conflict_including_retained_prior_rows(tmp_path, field, value, split):
    first, second = observation(), redelivery(**{field: value})
    prior = [] if split == "same_batch" else [project(first)]
    events = [first, second] if split == "same_batch" else [second]
    if split == "all_prior":
        prior.append(project(second))
        events = []
    availability = capture(tmp_path, events, prior_raw=prior)
    assert observation()["message_revision_id"] in availability["revision_conflicts"]


@pytest.mark.parametrize("field", [key for key in collector.RAW_FIELDS if key not in {"ev", "ts"}])
def test_missing_fields_remain_blocking(tmp_path, field):
    row = observation()
    row.pop(field)
    availability = capture(tmp_path, [row])
    assert availability["missing_fields"][f"missing_key:{field}"] == 1


def test_old_manifest_conflicts_and_missing_evidence_are_not_reinterpreted(tmp_path):
    old = {"revision_conflicts": [observation()["message_revision_id"], "older-conflict"],
           "missing_fields": {"missing_key:reply_to_msg_id": 2}}
    availability = capture(tmp_path, [redelivery()], prior_raw=[project(observation())], availability=old)
    assert availability["revision_conflicts"] == old["revision_conflicts"]
    assert availability["missing_fields"] == old["missing_fields"]


def test_missing_prior_fields_cannot_be_hidden_by_complete_redelivery(tmp_path):
    prior = project(observation())
    prior.pop("reply_to_msg_id")
    availability = capture(tmp_path, [redelivery()], prior_raw=[prior])
    assert availability["missing_fields"]["missing_key:reply_to_msg_id"] == 1


@pytest.mark.parametrize("field", [key for key in collector.RAW_NONNULL if key != "ts"])
def test_null_identity_fields_remain_blocking(tmp_path, field):
    availability = capture(tmp_path, [observation(**{field: None})])
    assert availability["missing_fields"][f"null:{field}"] == 1


def test_prior_missing_counts_are_not_added_twice(tmp_path):
    prior = project(observation())
    prior.pop("reply_to_msg_id")
    availability = capture(tmp_path, [redelivery()], prior_raw=[prior], availability={
        "missing_fields": {"missing_key:reply_to_msg_id": 1}, "revision_conflicts": [],
    })
    assert availability["missing_fields"] == {"missing_key:reply_to_msg_id": 1}


@pytest.mark.parametrize("field,value,blocker", [
    ("revision_conflicts", ["inherited-old-conflict"], "native_capture_revision_conflicts"),
    ("missing_fields", {"missing_key:reply_to_msg_id": 1}, "native_capture_raw_fields_incomplete"),
])
def test_consumer_keeps_collector_evidence_blockers(tmp_path, monkeypatch, field, value, blocker):
    from tests.test_collect_simulator_window import explicit_bridge_fixture
    from tools import run_simulator_forward as runner

    fixture = explicit_bridge_fixture(tmp_path, monkeypatch)
    fixture.manifest["event_evidence"]["raw_message_causal_availability"][field] = value
    fixture.manifest_path.write_text(json.dumps(fixture.manifest), encoding="utf-8")
    result = runner._capture_inputs(fixture.capture, None, fixture.end + timedelta(seconds=10), fixture.protocol, {})
    assert blocker in result["capture"]["blockers"]


def test_collector_content_contract_is_auditor_projection_without_schema_expansion():
    from tools.audit_raw_observations import CONTENT_FIELDS, OBSERVATION_FIELDS
    from research.causal_replay import RAW_FIELDS

    assert set(collector.RAW_FIELDS) == set(RAW_FIELDS)
    assert set(collector.RAW_CONTENT_FIELDS) == set(CONTENT_FIELDS) & set(RAW_FIELDS)
    assert not set(collector.RAW_CONTENT_FIELDS) & set(OBSERVATION_FIELDS)
    assert "sticker_id" in collector.RAW_CONTENT_FIELDS
