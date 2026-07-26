import re
import json

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


def test_context_bound_call_captures_current_causal_context():
    with causal_trace.bind_message_revision(
        "msgrev_worker",
        decision_id="decision_worker",
    ):
        worker_call = causal_trace.context_bound_call(
            causal_trace.current_fields
        )

    assert causal_trace.current_fields() == {}
    assert worker_call() == {
        "message_revision_id": "msgrev_worker",
        "decision_id": "decision_worker",
    }


def test_message_context_collects_actions_created_in_worker_calls():
    with causal_trace.bind_message_revision(
        "msgrev_manifest",
        decision_id="decision_manifest",
    ) as context:
        direct = causal_trace.new_action_id()
        worker = causal_trace.context_bound_call(causal_trace.new_action_id)
        threaded = worker()

        assert causal_trace.declared_action_ids() == [direct, threaded]
        assert causal_trace.declared_action_ids(context) == [
            direct,
            threaded,
        ]

    assert causal_trace.declared_action_ids() == []


def test_detached_context_does_not_inherit_telegram_decision():
    with causal_trace.bind_message_revision(
        "msgrev_parent",
        decision_id="decision_parent",
    ):
        with causal_trace.detached_context():
            assert causal_trace.current_fields() == {}
            detached_action = causal_trace.new_action_id()
            assert causal_trace.declared_action_ids() == []

        assert causal_trace.current_fields() == {
            "message_revision_id": "msgrev_parent",
            "decision_id": "decision_parent",
        }
        assert detached_action not in causal_trace.declared_action_ids()


def test_internal_decision_has_fresh_identity_and_parent():
    with causal_trace.bind_internal_decision(
        message_revision_id="msgrev_origin",
        parent_decision_id="decision_origin",
        reason="position_lifecycle_be",
    ) as context:
        action_id = causal_trace.new_action_id()

        assert context.decision_id != "decision_origin"
        assert context.decision_kind == "internal"
        assert context.parent_decision_id == "decision_origin"
        assert context.decision_reason == "position_lifecycle_be"
        assert causal_trace.declared_action_ids() == [action_id]


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


def test_signal_origin_index_accepts_one_consistent_market_origin():
    rows = [
        {
            "sig": "canal2_380",
            "ev": "mt5_order_requested",
            "order_kind": "market",
            "comment": "c2_380",
            "message_revision_id": "msgrev_origin",
            "decision_id": "decision_origin",
            "action_id": "action_primary",
        },
        {
            "sig": "canal2_380",
            "ev": "mt5_order_requested",
            "order_kind": "market",
            "comment": "c2_380",
            "message_revision_id": "msgrev_origin",
            "decision_id": "decision_origin",
            "action_id": "action_primary",
        },
    ]

    origins, conflicts = causal_trace.signal_origin_index(rows)

    assert origins == {
        "canal2_380": {
            "message_revision_id": "msgrev_origin",
            "decision_id": "decision_origin",
        }
    }
    assert conflicts == {}


def test_signal_origin_index_fails_closed_on_conflicting_origins():
    rows = [
        {
            "sig": "canal1_20700",
            "ev": "mt5_order_requested",
            "order_kind": "market",
            "comment": "c1_20700",
            "message_revision_id": "msgrev_a",
            "decision_id": "decision_a",
            "action_id": "action_a",
        },
        {
            "sig": "canal1_20700",
            "ev": "mt5_order_requested",
            "order_kind": "market",
            "comment": "c1_20700",
            "message_revision_id": "msgrev_b",
            "decision_id": "decision_b",
            "action_id": "action_b",
        },
    ]

    origins, conflicts = causal_trace.signal_origin_index(rows)

    assert "canal1_20700" not in origins
    assert conflicts == {
        "canal1_20700": [
            {
                "message_revision_id": "msgrev_a",
                "decision_id": "decision_a",
            },
            {
                "message_revision_id": "msgrev_b",
                "decision_id": "decision_b",
            },
        ]
    }


def test_load_signal_origin_index_streams_valid_rows_and_reports_bad_lines(
        tmp_path):
    events = tmp_path / "trade_events.jsonl"
    events.write_text(
        json.dumps({
            "sig": "canal2_380",
            "ev": "mt5_order_requested",
            "order_kind": "market",
            "comment": "c2_380",
            "message_revision_id": "msgrev_origin",
            "decision_id": "decision_origin",
            "action_id": "action_primary",
        }) + "\n"
        + "{not json}\n"
        + "[]\n",
        encoding="utf-8",
    )

    origins, conflicts, invalid_lines = (
        causal_trace.load_signal_origin_index(events)
    )

    assert origins["canal2_380"]["decision_id"] == "decision_origin"
    assert conflicts == {}
    assert invalid_lines == [2, 3]


def test_load_signal_origin_index_survives_invalid_utf8_and_keeps_valid_rows(
        tmp_path):
    events = tmp_path / "trade_events.jsonl"
    valid = json.dumps({
        "sig": "canal2_380",
        "ev": "mt5_order_requested",
        "order_kind": "market",
        "comment": "c2_380",
        "message_revision_id": "msgrev_origin",
        "decision_id": "decision_origin",
        "action_id": "action_primary",
    }).encode("utf-8")
    events.write_bytes(valid + b"\n\xff\xfe\n")

    origins, conflicts, invalid_lines = (
        causal_trace.load_signal_origin_index(events)
    )

    assert origins["canal2_380"]["decision_id"] == "decision_origin"
    assert conflicts == {}
    assert invalid_lines == [2]


def test_load_signal_origin_index_rejects_invalid_utf8_inside_json_string(
        tmp_path):
    events = tmp_path / "trade_events.jsonl"
    events.write_bytes(
        b'{"sig":"canal2_380","ev":"mt5_order_requested",'
        b'"order_kind":"market","comment":"c2_380",'
        b'"message_revision_id":"msgrev_origin",'
        b'"decision_id":"decision_\xff","action_id":"action_primary"}\n'
    )

    origins, conflicts, invalid_lines = (
        causal_trace.load_signal_origin_index(events)
    )

    assert origins == {}
    assert conflicts == {}
    assert invalid_lines == [1]


def test_signal_origin_ignores_later_dca_and_scale_out_decisions():
    rows = [
        {
            "sig": "canal2_380",
            "ev": "mt5_order_requested",
            "order_kind": "market",
            "comment": "c2_380",
            "message_revision_id": "msgrev_origin",
            "decision_id": "decision_origin",
            "action_id": "action_primary",
        },
        {
            "sig": "canal2_380",
            "ev": "mt5_order_requested",
            "order_kind": "market",
            "comment": "c2_380_B1",
            "message_revision_id": "msgrev_origin",
            "decision_id": "decision_origin",
            "action_id": "action_scale",
        },
        {
            "sig": "canal2_380",
            "ev": "mt5_order_requested",
            "order_kind": "market",
            "comment": "DCA_c2_380_4051.0",
            "message_revision_id": "msgrev_origin",
            "decision_id": "decision_internal",
            "action_id": "action_dca",
        },
    ]

    origins, conflicts = causal_trace.signal_origin_index(rows)

    assert origins == {
        "canal2_380": {
            "message_revision_id": "msgrev_origin",
            "decision_id": "decision_origin",
        }
    }
    assert conflicts == {}


def test_signal_origin_prefers_confirmed_primary_request_over_failed_retry():
    rows = [
        {
            "sig": "canal1_20700",
            "ev": "mt5_order_requested",
            "order_kind": "market",
            "comment": "c1_20700",
            "message_revision_id": "msgrev_failed",
            "decision_id": "decision_failed",
            "action_id": "action_failed",
        },
        {
            "sig": "canal1_20700",
            "ev": "mt5_order_result",
            "action_id": "action_failed",
            "retcode": 10018,
        },
        {
            "sig": "canal1_20700",
            "ev": "mt5_order_requested",
            "order_kind": "market",
            "comment": "c1_20700",
            "message_revision_id": "msgrev_success",
            "decision_id": "decision_success",
            "action_id": "action_success",
        },
        {
            "sig": "canal1_20700",
            "ev": "mt5_order_result",
            "action_id": "action_success",
            "retcode": 10009,
        },
    ]

    origins, conflicts = causal_trace.signal_origin_index(rows)

    assert origins == {
        "canal1_20700": {
            "message_revision_id": "msgrev_success",
            "decision_id": "decision_success",
        }
    }
    assert conflicts == {}


def test_load_signal_origin_index_reads_result_rows_for_confirmation(tmp_path):
    events = tmp_path / "trade_events.jsonl"
    rows = [
        {
            "sig": "canal1_20700",
            "ev": "mt5_order_requested",
            "order_kind": "market",
            "comment": "c1_20700",
            "message_revision_id": "msgrev_failed",
            "decision_id": "decision_failed",
            "action_id": "action_failed",
        },
        {
            "sig": "canal1_20700",
            "ev": "mt5_order_requested",
            "order_kind": "market",
            "comment": "c1_20700",
            "message_revision_id": "msgrev_success",
            "decision_id": "decision_success",
            "action_id": "action_success",
        },
        {
            "sig": "canal1_20700",
            "ev": "mt5_order_result",
            "action_id": "action_success",
            "retcode": 10009,
        },
    ]
    events.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    origins, conflicts, invalid_lines = (
        causal_trace.load_signal_origin_index(events)
    )

    assert origins["canal1_20700"]["decision_id"] == "decision_success"
    assert conflicts == {}
    assert invalid_lines == []
