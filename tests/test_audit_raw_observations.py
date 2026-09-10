import copy
import hashlib
import json

import pytest

from causal_trace import message_revision_id
from tools.audit_raw_observations import audit_file, audit_rows


def raw():
    text = "SELL GOLD NOW"
    sha = hashlib.sha1(text.encode()).hexdigest()
    return {
        "ev": "telegram_raw", "channel": "canal2", "chat_id": -100123,
        "message_id": 2662, "revision_token": "2026-09-09T09:45:25+00:00",
        "message_revision_id": message_revision_id(chat_id=-100123, message_id=2662,
             revision_token="2026-09-09T09:45:25+00:00", text_sha1=sha, media_sha256=None),
        "text": text, "text_sha1": sha, "has_text": True, "has_media": False,
        "sticker_id": None, "has_photo": False, "has_document": False,
        "reply_to_msg_id": None, "is_reply": False, "media_sha256": None,
        "date_utc": "2026-09-09T09:45:20+00:00", "edit_date_utc": "2026-09-09T09:45:25+00:00",
        "ts": "2026-09-09T09:45:25.546+00:00", "update_kind": "edit", "is_edit": True,
    }


def test_redelivery_is_not_a_content_conflict_and_preserves_both_observations():
    first = raw()
    later = first | {"ts": "2026-09-09T09:45:29.901+00:00", "is_edit": False, "update_kind": "poll_new"}
    original = copy.deepcopy([first, later])
    report = audit_rows([later, first])
    assert report["status"] == "raw_observations_consistent"
    assert report["summary"]["compatible_redeliveries"] == 1
    assert report["summary"]["raw_observations"] == 2
    assert report["summary"]["unique_revisions"] == 1
    assert report["revisions"][0]["first_observed_at_utc"] == first["ts"]
    assert len({row["observation_sha256"] for row in report["observations"]}) == 2
    assert len({row["content_sha256"] for row in report["observations"]}) == 1
    assert [first, later] == original
    assert report["full_causal_or_prospective_validation"] is False


@pytest.mark.parametrize("field,value", [
    ("text", "BUY GOLD NOW"), ("reply_to_msg_id", 1), ("sticker_id", 1234),
    ("edit_date_utc", "2026-09-09T09:45:26+00:00"), ("date_utc", "2026-09-09T09:45:19+00:00"),
    ("channel", "canal1"), ("message_id", 2663), ("has_photo", True),
])
def test_changed_content_keeps_the_conflict_instead_of_deduplicating(field, value):
    first = raw()
    report = audit_rows([first, first | {field: value, "ts": "2026-09-09T09:45:30+00:00"}])
    assert report["status"] == "blocked"
    assert report["summary"]["content_conflicts"] == 1
    assert report["summary"]["compatible_redeliveries"] == 0
    assert field in report["content_conflicts"][0]["changed_content_fields"]


def test_transport_flag_still_has_to_agree_with_its_own_delivery_kind():
    report = audit_rows([raw(), raw() | {"is_edit": False}])
    assert report["status"] == "blocked"
    assert report["summary"]["invalid_observations"] == 1
    assert report["summary"]["compatible_redeliveries"] == 0


@pytest.mark.parametrize("field", ["reply_to_msg_id", "edit_date_utc", "has_media"])
def test_missing_fields_are_not_accepted_as_equivalent_nulls(field):
    original = raw()
    missing = original.copy()
    del missing[field]
    report = audit_rows([original, missing])
    assert report["status"] == "blocked"
    assert report["summary"]["invalid_observations"] == 1


@pytest.mark.parametrize("stamp", ["2026-09-09T09:45:25", "2026-09-09T09:45:19+00:00"])
def test_invalid_or_prepublication_receipt_is_retained_as_blocked(stamp):
    report = audit_rows([raw() | {"ts": stamp}])
    assert report["status"] == "blocked"
    assert report["summary"]["raw_observations"] == 1


def test_file_audit_is_bounded_and_rejects_partial_json_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    line = json.dumps(raw()) + "\n"
    path.write_text(line, encoding="utf-8")
    assert audit_file(path)["summary"]["raw_observations"] == 1
    with pytest.raises(ValueError, match="budget"):
        audit_file(path, max_bytes=10)
    path.write_text(line.rstrip(), encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        audit_file(path)


@pytest.mark.parametrize("line", ['{"ev":"one","ev":"two"}\n', '{"ev":"other","price":NaN}\n'])
def test_invalid_json_is_not_hidden_by_the_raw_event_filter(tmp_path, line):
    path = tmp_path / "events.jsonl"
    path.write_text(line, encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        audit_file(path)
