import re

import causal_trace


def test_message_revision_id_is_stable_and_content_bound():
    first = causal_trace.message_revision_id(
        chat_id=-1003908582492,
        message_id=380,
        revision_token="2026-07-23T15:30:25+00:00",
        text_sha1="a" * 40,
        media_sha256=None,
    )
    same = causal_trace.message_revision_id(
        chat_id=-1003908582492,
        message_id=380,
        revision_token="2026-07-23T15:30:25+00:00",
        text_sha1="a" * 40,
        media_sha256=None,
    )
    edited = causal_trace.message_revision_id(
        chat_id=-1003908582492,
        message_id=380,
        revision_token="2026-07-23T15:31:00+00:00",
        text_sha1="b" * 40,
        media_sha256=None,
    )

    assert first == same
    assert re.fullmatch(r"msgrev_[0-9a-f]{64}", first)
    assert edited != first


def test_message_revision_id_binds_chat_and_media_identity():
    common = {
        "message_id": 380,
        "revision_token": "new",
        "text_sha1": None,
        "media_sha256": "b" * 64,
    }

    first = causal_trace.message_revision_id(
        chat_id=-1003908582492, **common)
    other_chat = causal_trace.message_revision_id(
        chat_id=-1001642806869, **common)
    other_media = causal_trace.message_revision_id(
        chat_id=-1003908582492,
        **{**common, "media_sha256": "c" * 64},
    )

    assert first != other_chat
    assert first != other_media


def test_bound_context_is_visible_then_resets():
    assert causal_trace.current_fields() == {}

    with causal_trace.bind_message_revision(
        "msgrev_a",
        decision_id="decision_b",
    ):
        assert causal_trace.current_fields() == {
            "message_revision_id": "msgrev_a",
            "decision_id": "decision_b",
        }

    assert causal_trace.current_fields() == {}


def test_nested_context_restores_parent():
    with causal_trace.bind_message_revision(
        "msgrev_parent",
        decision_id="decision_parent",
    ):
        with causal_trace.bind_message_revision(
            "msgrev_child",
            decision_id="decision_child",
        ):
            assert causal_trace.current_fields()["message_revision_id"] == (
                "msgrev_child"
            )
        assert causal_trace.current_fields() == {
            "message_revision_id": "msgrev_parent",
            "decision_id": "decision_parent",
        }


def test_runtime_ids_are_prefixed_and_unique():
    factories = {
        "decision": causal_trace.new_decision_id,
        "action": causal_trace.new_action_id,
        "attempt": causal_trace.new_attempt_id,
        "event": causal_trace.new_event_id,
        "session": causal_trace.new_session_id,
    }

    for prefix, factory in factories.items():
        first = factory()
        second = factory()
        assert re.fullmatch(rf"{prefix}_[0-9a-f]{{32}}", first)
        assert first != second


def test_current_or_new_decision_reuses_bound_decision():
    unbound = causal_trace.current_or_new_decision_id()
    assert unbound.startswith("decision_")

    with causal_trace.bind_message_revision(
        "msgrev_a",
        decision_id="decision_fixed",
    ):
        assert causal_trace.current_or_new_decision_id() == "decision_fixed"
        assert causal_trace.current_message_revision_id() == "msgrev_a"

    assert causal_trace.current_message_revision_id() is None
