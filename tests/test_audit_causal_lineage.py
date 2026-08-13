import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import executor
import journal
import listener
from tools import audit_causal_lineage


_RAW_CHAT_ID = -1003908582492
_RAW_MESSAGE_ID = 380
_RAW_REVISION_TOKEN = "new"
_RAW_TEXT = "BUY NOW\nTP1 4059.53\nSL 4047.53"
_RAW_TEXT_SHA1 = hashlib.sha1(_RAW_TEXT.encode("utf-8")).hexdigest()


def _test_message_revision_id(
    *,
    chat_id: int,
    message_id: int,
    revision_token: str,
    text_sha1: str | None,
    media_sha256: str | None,
) -> str:
    canonical = json.dumps(
        {
            "chat_id": chat_id,
            "media_sha256": media_sha256,
            "message_id": message_id,
            "revision_token": revision_token,
            "text_sha1": text_sha1,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"msgrev_{hashlib.sha256(canonical).hexdigest()}"


_MSGREV_1 = _test_message_revision_id(
    chat_id=_RAW_CHAT_ID,
    message_id=_RAW_MESSAGE_ID,
    revision_token=_RAW_REVISION_TOKEN,
    text_sha1=_RAW_TEXT_SHA1,
    media_sha256=None,
)
_MSGREV_2 = _test_message_revision_id(
    chat_id=_RAW_CHAT_ID,
    message_id=_RAW_MESSAGE_ID + 1,
    revision_token=_RAW_REVISION_TOKEN,
    text_sha1=_RAW_TEXT_SHA1,
    media_sha256=None,
)
_MSGREV_MEDIA = _test_message_revision_id(
    chat_id=-1001642806869,
    message_id=20700,
    revision_token="new",
    text_sha1=None,
    media_sha256=None,
)


def _telegram_decision_identity() -> dict:
    return {
        "channel": "canal2",
        "chat_id": _RAW_CHAT_ID,
        "message_id": _RAW_MESSAGE_ID,
        "revision_token": _RAW_REVISION_TOKEN,
        "update_kind": "new",
    }


def _row(ev, event_id, **fields):
    row = {
        "schema_version": 2,
        "event_id": event_id,
        "session_id": "session_1",
        "ts": "2026-07-26T10:00:00.000+00:00",
        "monotonic_ns": 123,
        "code_commit": "a" * 40,
        "sig": "canal2_380",
        "ev": ev,
        **fields,
    }
    return _rehash(row)


def _rehash(row):
    semantic = {
        key: value
        for key, value in row.items()
        if key not in {
            "schema_version",
            "event_id",
            "session_id",
            "ts",
            "monotonic_ns",
            "code_commit",
            "payload_sha256",
        }
    }
    row["payload_sha256"] = hashlib.sha256(json.dumps(
        semantic,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")).hexdigest()
    return row


def _event_row(rows, event_name):
    return next(row for row in rows if row["ev"] == event_name)


def _decision_only_chain():
    rows = _complete_chain()
    return [rows[0], rows[1], rows[5]]


def _complete_chain():
    return [
        _row(
            "telegram_raw",
            "event_raw",
            monotonic_ns=100,
            channel="canal2",
            chat_id=_RAW_CHAT_ID,
            message_id=_RAW_MESSAGE_ID,
            update_kind="new",
            is_edit=False,
            revision_token=_RAW_REVISION_TOKEN,
            message_revision_id=_MSGREV_1,
            has_text=True,
            text=_RAW_TEXT,
            text_sha1=_RAW_TEXT_SHA1,
            has_media=False,
            media_sha256=None,
        ),
        _row(
            "telegram_decision_started",
            "event_decision_started",
            monotonic_ns=110,
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            **_telegram_decision_identity(),
        ),
        _row(
            "mt5_order_requested",
            "event_request",
            monotonic_ns=120,
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_1",
            attempt_id="attempt_1",
            order_kind="market",
            direction="BUY",
            lot=0.01,
            requested_price=4056.53,
            sl=None,
            tp=4059.53,
            magic=20260422,
            comment="c2_380",
            deviation=30,
            action_revision=0,
        ),
        _row(
            "mt5_action_attempt",
            "event_attempt",
            monotonic_ns=130,
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_1",
            attempt_id="attempt_1",
            duration_ns=100,
            operation="OPEN_MARKET",
            attempt_started_utc="2026-07-26T10:00:00.000+00:00",
            attempt_finished_utc="2026-07-26T10:00:00.001+00:00",
            attempt_started_monotonic_ns=100,
            attempt_finished_monotonic_ns=200,
            broker_request_sent=True,
            ticket=None,
            request={
                "action": 1,
                "symbol": "XAUUSD",
                "volume": 0.01,
                "type": 0,
                "price": 4056.53,
                "tp": 4059.53,
                "magic": 20260422,
                "comment": "c2_380",
                "deviation": 30,
                "type_time": 0,
                "type_filling": 1,
            },
            result={
                "retcode": 10009,
                "comment": "Request executed",
                "order": 101,
                "deal": 201,
                "volume": 0.01,
                "price": 4056.53,
                "bid": 4056.49,
                "ask": 4056.53,
                "request_id": 301,
                "retcode_external": 0,
            },
            last_error=None,
            exception=None,
            source_tick={
                "time_msc": 1784820626390,
                "bid": 4056.49,
                "ask": 4056.53,
            },
            validation_tick=None,
            position_before=None,
            order_before=None,
            symbol_contract=None,
            source_tick_lookup_state="found",
            validation_tick_lookup_state="not_queried",
            position_lookup_state="not_queried",
            order_lookup_state="not_queried",
            symbol_info_lookup_state="not_queried",
            terminal_state=None,
            account_state=None,
            expected_magic=None,
            preflight_status=None,
            preflight_effective_sl=None,
            preflight_effective_tp=None,
            preflight_deferred_sl=None,
            preflight_reason=None,
            action_revision=0,
        ),
        _row(
            "mt5_order_result",
            "event_result",
            monotonic_ns=140,
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_1",
            attempt_id="attempt_1",
            order_kind="market",
            retcode=10009,
            action_revision=0,
        ),
        _row(
            "telegram_processed",
            "event_processed",
            monotonic_ns=150,
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            declared_action_ids=["action_1"],
            declared_action_count=1,
            **_telegram_decision_identity(),
        ),
    ]


def _operation_chain(operation: str):
    rows = _complete_chain()
    request = rows[2]
    attempt = rows[3]
    result = rows[4]

    if operation == "PLACE_LIMIT":
        request.update({
            "order_kind": "pending_limit",
            "direction": "SELL",
            "requested_price": 4060.0,
        })
        attempt.update({
            "operation": operation,
            "request": {
                "action": 5,
                "symbol": "XAUUSD",
                "volume": 0.01,
                "type": 3,
                "price": 4060.0,
                "tp": 4059.53,
                "magic": 20260422,
                "comment": "c2_380",
                "deviation": 30,
                "type_time": 0,
                "type_filling": 1,
            },
            "source_tick": None,
            "source_tick_lookup_state": "not_queried",
        })
        result.update({"order_kind": "pending_limit"})
    elif operation == "MODIFY_SLTP":
        request.update({
            "ev": "mt5_modify_requested",
            "ticket": 101,
            "new_sl": 4056.53,
            "new_tp": 4059.53,
            "expected_magic": 20260422,
        })
        attempt.update({
            "operation": operation,
            "ticket": 101,
            "expected_magic": 20260422,
            "preflight_status": "ready",
            "preflight_effective_sl": 4056.53,
            "preflight_effective_tp": 4059.53,
            "preflight_deferred_sl": None,
            "preflight_reason": None,
            "request": {
                "action": 6,
                "position": 101,
                "sl": 4056.53,
                "tp": 4059.53,
            },
            "position_before": {
                "ticket": 101,
                "symbol": "XAUUSD",
                "magic": 20260422,
                "type": 0,
                "volume": 0.01,
                "price_open": 4056.53,
                "price_current": 4056.49,
                "sl": 4047.53,
                "tp": 4059.53,
                "profit": -0.04,
                "comment": "c2_380",
            },
            "position_lookup_state": "found",
            "symbol_contract": {
                "point": 0.01,
                "digits": 2,
                "trade_stops_level": 0,
                "trade_freeze_level": 0,
            },
            "symbol_info_lookup_state": "found",
        })
        result.update({
            "ev": "mt5_modify_confirmed",
            "ticket": 101,
            "new_sl": 4056.53,
            "new_tp": 4059.53,
        })
    elif operation == "CLOSE_POSITION":
        request.update({
            "ev": "mt5_close_requested",
            "ticket": 101,
            "expected_magic": 20260422,
        })
        attempt.update({
            "operation": operation,
            "ticket": 101,
            "expected_magic": 20260422,
            "request": {
                "action": 1,
                "symbol": "XAUUSD",
                "volume": 0.01,
                "type": 1,
                "position": 101,
                "price": 4056.49,
                "deviation": 30,
                "magic": 20260422,
                "comment": "bot_close",
                "type_time": 0,
                "type_filling": 1,
            },
            "position_before": {
                "ticket": 101,
                "symbol": "XAUUSD",
                "magic": 20260422,
                "type": 0,
                "volume": 0.01,
                "price_open": 4056.53,
                "price_current": 4056.49,
                "sl": 4047.53,
                "tp": 4059.53,
                "profit": -0.04,
                "comment": "c2_380",
            },
            "position_lookup_state": "found",
        })
        result.update({
            "ev": "mt5_close_result",
            "ticket": 101,
        })
    elif operation == "CANCEL_PENDING":
        request.update({
            "ev": "mt5_cancel_requested",
            "ticket": 202,
            "expected_magic": 20260422,
        })
        attempt.update({
            "operation": operation,
            "ticket": 202,
            "expected_magic": 20260422,
            "request": {
                "action": 8,
                "order": 202,
            },
            "source_tick": None,
            "source_tick_lookup_state": "not_queried",
            "order_before": {
                "ticket": 202,
                "symbol": "XAUUSD",
                "magic": 20260422,
                "type": 2,
                "volume_initial": 0.01,
                "volume_current": 0.01,
                "price_open": 4050.0,
                "price_current": 4056.49,
                "sl": 4047.53,
                "tp": 4059.53,
                "comment": "c2_380",
            },
            "order_lookup_state": "found",
        })
        result.update({
            "ev": "mt5_cancel_result",
            "ticket": 202,
        })
    else:
        raise AssertionError(f"unsupported operation: {operation}")

    for row in (request, attempt, result):
        _rehash(row)
    return rows


def test_complete_chain_accounts_for_every_relevant_row():
    report = audit_causal_lineage.audit_rows(
        _complete_chain(),
        source_sha256="f" * 64,
    )

    assert report["selection"]["selected_rows"] == 6
    assert report["selection"]["relevant_rows"] == 6
    assert report["summary"]["complete"] == 6
    assert report["summary"]["blocked"] == 0
    assert {row["status"] for row in report["rows"]} == {"complete"}
    assert len(report["fingerprint"]) == 64


def test_decision_start_must_precede_its_action():
    rows = _complete_chain()
    rows[1]["monotonic_ns"] = 125
    _rehash(rows[1])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][1]["status"] == "contradictory_link"
    assert report["rows"][2]["status"] == "contradictory_link"
    assert report["relations"]["temporal_mismatch_decision_ids"] == [
        "decision_1",
    ]
    assert report["relations"]["temporal_mismatch_action_ids"] == [
        "action_1",
    ]
    assert report["relations"]["temporal_mismatch_attempt_ids"] == []


def test_chain_rows_must_keep_one_signal_identity():
    rows = _complete_chain()
    rows[2]["sig"] = "canal1_999"
    _rehash(rows[2])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] == "contradictory_link"


def test_management_decision_may_target_an_older_signal():
    rows = _complete_chain()
    for row in rows[2:5]:
        row["sig"] = "canal2_370"
        _rehash(row)

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["summary"]["blocked"] == 0
    assert {row["status"] for row in report["rows"]} == {"complete"}


def test_action_target_must_match_observed_mt5_position_owner():
    rows = _operation_chain("CLOSE_POSITION")
    for row in rows[2:5]:
        row["sig"] = "canal2_370"
        _rehash(row)

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] == "contradictory_link"
    assert report["rows"][3]["status"] == "contradictory_link"


def test_observed_position_ticket_must_match_the_action_ticket():
    rows = _operation_chain("MODIFY_SLTP")
    rows[3]["position_before"]["ticket"] = 999
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] == "contradictory_link"
    assert report["rows"][3]["status"] == "contradictory_link"


def test_observed_pending_order_ticket_must_match_the_action_ticket():
    rows = _operation_chain("CANCEL_PENDING")
    rows[3]["order_before"]["ticket"] = 999
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] == "contradictory_link"
    assert report["rows"][3]["status"] == "contradictory_link"


def test_ready_modify_attempt_must_send_the_preflight_sl_and_tp():
    rows = _operation_chain("MODIFY_SLTP")
    rows[3]["request"]["sl"] = 3999.0
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] == "contradictory_link"
    assert report["rows"][3]["status"] == "contradictory_link"


def test_deferred_sl_attempt_may_apply_only_tp_with_explicit_preflight():
    rows = _operation_chain("MODIFY_SLTP")
    rows[3].update({
        "preflight_status": "apply_tp_defer_sl",
        "preflight_effective_sl": 4047.53,
        "preflight_effective_tp": 4059.53,
        "preflight_deferred_sl": 4056.53,
        "preflight_reason": "requested_sl_waits_for_market",
    })
    rows[3]["request"]["sl"] = 4047.53
    rows[4]["new_sl"] = None
    _rehash(rows[3])
    _rehash(rows[4])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["summary"]["blocked"] == 0


def test_deferred_sl_attempt_may_preserve_a_newer_observed_sl():
    rows = _operation_chain("MODIFY_SLTP")
    rows[3].update({
        "preflight_status": "apply_tp_defer_sl",
        "preflight_effective_sl": 4047.53,
        "preflight_effective_tp": 4059.53,
        "preflight_deferred_sl": 4056.53,
        "preflight_reason": "requested_sl_waits_for_market",
    })
    rows[3]["position_before"]["sl"] = 4048.0
    rows[3]["request"]["sl"] = 4048.0
    rows[4]["new_sl"] = None
    _rehash(rows[3])
    _rehash(rows[4])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["summary"]["blocked"] == 0


def test_deferred_tp_then_later_sl_confirmation_is_one_complete_action():
    rows = _operation_chain("MODIFY_SLTP")
    first_attempt = rows[3]
    first_result = rows[4]
    processed = rows[5]
    first_attempt.update({
        "monotonic_ns": 130,
        "preflight_status": "apply_tp_defer_sl",
        "preflight_effective_sl": 4047.53,
        "preflight_effective_tp": 4059.53,
        "preflight_deferred_sl": 4056.53,
        "preflight_reason": "requested_sl_waits_for_market",
    })
    first_attempt["request"]["sl"] = 4047.53
    first_result.update({
        "monotonic_ns": 135,
        "new_sl": None,
        "new_tp": 4059.53,
    })

    second_attempt = json.loads(json.dumps(first_attempt))
    second_attempt.update({
        "event_id": "event_attempt_2",
        "monotonic_ns": 140,
        "attempt_id": "attempt_2",
        "preflight_status": "ready",
        "preflight_effective_sl": 4056.53,
        "preflight_effective_tp": 4059.53,
        "preflight_deferred_sl": None,
        "preflight_reason": None,
    })
    second_attempt["request"]["sl"] = 4056.53
    second_attempt["position_before"]["tp"] = 4059.53
    second_result = json.loads(json.dumps(first_result))
    second_result.update({
        "event_id": "event_result_2",
        "monotonic_ns": 145,
        "attempt_id": "attempt_2",
        "new_sl": 4056.53,
        "new_tp": 4059.53,
    })
    for row in (
        first_attempt,
        first_result,
        second_attempt,
        second_result,
    ):
        _rehash(row)
    rows[3:5] = [
        first_attempt,
        first_result,
        second_attempt,
        second_result,
    ]

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert processed["declared_action_ids"] == ["action_1"]
    assert report["summary"]["blocked"] == 0


def test_attempt_magic_must_match_the_target_channel():
    rows = _operation_chain("MODIFY_SLTP")
    rows[3]["position_before"]["magic"] = 20260421
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] == "contradictory_link"
    assert report["rows"][3]["status"] == "contradictory_link"


def test_close_request_magic_must_match_the_target_channel():
    rows = _operation_chain("CLOSE_POSITION")
    rows[3]["request"]["magic"] = 20260421
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] == "contradictory_link"
    assert report["rows"][3]["status"] == "contradictory_link"


def test_management_action_magic_must_match_the_target_channel():
    rows = _operation_chain("MODIFY_SLTP")
    rows[2]["expected_magic"] = 20260421
    _rehash(rows[2])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] != "complete"


def test_entry_action_magic_must_match_the_target_channel():
    rows = _complete_chain()
    rows[2]["magic"] = 20260421
    rows[3]["request"]["magic"] = 20260421
    _rehash(rows[2])
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] != "complete"


def test_entry_action_requires_explicit_levels_and_attempt_link():
    for missing_field in ("sl", "attempt_id"):
        rows = _complete_chain()
        rows[2].pop(missing_field)
        _rehash(rows[2])

        report = audit_causal_lineage.audit_rows(
            rows,
            source_sha256="f" * 64,
        )

        request = next(
            row for row in report["rows"]
            if row["event_id"] == "event_request"
        )
        assert request["status"] == "missing_execution_evidence"


def test_attempt_requires_explicit_null_evidence_fields():
    for missing_field in (
        "last_error",
        "exception",
        "position_before",
        "terminal_state",
    ):
        rows = _complete_chain()
        rows[3].pop(missing_field)
        _rehash(rows[3])

        report = audit_causal_lineage.audit_rows(
            rows,
            source_sha256="f" * 64,
        )

        attempt = next(
            row for row in report["rows"]
            if row["event_id"] == "event_attempt"
        )
        assert attempt["status"] == "missing_execution_evidence"


def test_known_ticket_owner_blocks_cross_signal_management_without_comment():
    rows = _complete_chain()
    close_rows = _operation_chain("CLOSE_POSITION")[2:5]
    for index, row in enumerate(close_rows, start=1):
        row["event_id"] = f"event_close_{index}"
        row["monotonic_ns"] = 140 + index
        row["sig"] = "canal2_370"
        row["action_id"] = "action_2"
        row["attempt_id"] = "attempt_2"
    close_rows[1]["position_before"]["comment"] = "broker generated"
    for row in close_rows:
        _rehash(row)

    rows[5]["declared_action_ids"] = ["action_1", "action_2"]
    rows[5]["declared_action_count"] = 2
    _rehash(rows[5])
    rows[5:5] = close_rows

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    action_rows = {
        row["action_id"]: row
        for row in report["rows"]
        if row["ev"] in {
            "mt5_order_requested",
            "mt5_close_requested",
        }
    }
    assert action_rows["action_1"]["status"] == "invalid_dependency"
    assert action_rows["action_2"]["status"] == "contradictory_link"
    assert report["relations"]["ticket_owner_mismatch_action_ids"] == [
        "action_2",
    ]


def test_close_result_ticket_must_match_action_ticket():
    rows = _operation_chain("CLOSE_POSITION")
    rows[4]["ticket"] = 999
    _rehash(rows[4])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][4]["status"] == "contradictory_link"


def test_modify_result_must_preserve_requested_levels_and_revision():
    for field, replacement in (
        ("new_sl", None),
        ("new_tp", 9999.0),
        ("action_revision", None),
    ):
        rows = _operation_chain("MODIFY_SLTP")
        if replacement is None:
            rows[4].pop(field)
        else:
            rows[4][field] = replacement
        _rehash(rows[4])

        report = audit_causal_lineage.audit_rows(
            rows,
            source_sha256="f" * 64,
        )

        assert report["rows"][4]["status"] == "contradictory_link"


def test_selected_row_is_blocked_by_invalid_external_dependency():
    rows = _complete_chain()
    for row in rows:
        row["ts"] = "2026-07-25T10:00:00.000+00:00"
        _rehash(row)
    rows[2]["code_commit"] = "invalid"
    rows[3]["ts"] = "2026-07-26T10:00:00.000+00:00"
    rows[4]["ts"] = "2026-07-26T10:00:00.001+00:00"
    _rehash(rows[3])
    _rehash(rows[4])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
        since="2026-07-26",
        until="2026-07-26",
    )

    assert report["selection"]["relevant_rows"] == 2
    assert report["summary"]["blocked"] == 2


def test_empty_relevant_selection_is_blocked():
    report = audit_causal_lineage.audit_rows(
        _complete_chain(),
        source_sha256="f" * 64,
        since="2030-01-01",
        until="2030-01-01",
    )

    assert report["selection"]["relevant_rows"] == 0
    assert report["summary"]["empty_selection"] == 1
    assert report["summary"]["blocked"] == 1


def test_final_decision_requires_pre_action_start_event():
    rows = [
        row for row in _complete_chain()
        if row["ev"] != "telegram_decision_started"
    ]

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    final = next(
        row for row in report["rows"]
        if row["ev"] == "telegram_processed"
    )
    assert final["status"] == "missing_decision_start"


def test_every_supported_mt5_operation_has_a_complete_chain():
    for operation in (
        "PLACE_LIMIT",
        "MODIFY_SLTP",
        "CLOSE_POSITION",
        "CANCEL_PENDING",
    ):
        report = audit_causal_lineage.audit_rows(
            _operation_chain(operation),
            source_sha256="f" * 64,
        )

        assert report["summary"]["blocked"] == 0, (
            operation,
            report["rows"],
        )


def test_found_position_requires_replayable_position_fields():
    rows = _operation_chain("CLOSE_POSITION")
    rows[3]["position_before"].pop("volume")
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][3]["status"] == "missing_execution_evidence"


def test_found_symbol_contract_requires_broker_constraints():
    rows = _operation_chain("MODIFY_SLTP")
    rows[3]["symbol_contract"]["point"] = None
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][3]["status"] == "missing_execution_evidence"


def test_found_pending_order_requires_replayable_order_fields():
    rows = _operation_chain("CANCEL_PENDING")
    rows[3]["order_before"].pop("price_open")
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][3]["status"] == "missing_execution_evidence"


def test_sent_request_requires_complete_operation_payload():
    rows = _complete_chain()
    rows[3]["request"].pop("symbol")
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][3]["status"] == "missing_execution_evidence"


def test_broker_response_requires_complete_result_shape():
    rows = _complete_chain()
    rows[3]["result"].pop("price")
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][3]["status"] == "missing_execution_evidence"


def test_close_attempt_type_must_oppose_position_direction():
    rows = _operation_chain("CLOSE_POSITION")
    rows[3]["request"]["type"] = 0
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] == "contradictory_link"
    assert report["rows"][3]["status"] == "contradictory_link"


def test_attempt_without_explicit_lookup_states_is_blocked():
    rows = _complete_chain()
    rows[3].pop("position_lookup_state")
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    attempt = next(
        row for row in report["rows"]
        if row["event_id"] == "event_attempt"
    )
    assert attempt["status"] == "missing_execution_evidence"


def test_sent_market_attempt_requires_complete_source_tick():
    rows = _complete_chain()
    rows[3]["source_tick"] = {
        "time_msc": 1784820626390,
        "bid": None,
        "ask": 4056.53,
    }
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][3]["status"] == "missing_execution_evidence"


def test_non_finite_numeric_evidence_is_blocked():
    rows = _complete_chain()
    rows[3]["result"]["price"] = float("nan")
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][3]["status"] == "invalid_numeric_evidence"


def test_schema_v2_event_timestamp_must_be_explicit_utc():
    rows = _complete_chain()
    rows[0]["ts"] = "2026-07-26T10:00:00"

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][0]["status"] == "missing_timestamp"


def test_attempt_boundaries_must_be_explicit_utc():
    rows = _complete_chain()
    rows[3]["attempt_started_utc"] = "2026-07-26T10:00:00.000"
    rows[3]["attempt_finished_utc"] = "2026-07-26T10:00:00.001"
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][3]["status"] == "missing_execution_evidence"


def test_one_runtime_session_cannot_claim_two_code_commits():
    rows = _complete_chain()
    rows[3]["code_commit"] = "b" * 40

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["relations"]["contradictory_session_ids"] == [
        "session_1"
    ]
    assert {
        row["status"] for row in report["rows"]
    } == {"contradictory_link"}


def test_runtime_identifier_prefixes_are_required():
    rows = _complete_chain()
    rows[0]["event_id"] = "raw"

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][0]["status"] == "missing_envelope"


def test_raw_text_hash_and_revision_identity_must_match_payload():
    rows = _complete_chain()
    rows[0]["text"] = "SELL NOW"
    _rehash(rows[0])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][0]["status"] == "invalid_message_evidence"


def test_raw_revision_token_is_required():
    rows = _complete_chain()
    rows[0].pop("revision_token")
    _rehash(rows[0])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][0]["status"] == "invalid_message_evidence"


def test_processed_decision_identity_must_match_raw_message():
    rows = _complete_chain()
    rows[1].update({
        "channel": "canal1",
        "chat_id": -1001642806869,
        "message_id": 20700,
        "revision_token": "new",
        "update_kind": "new",
    })
    _rehash(rows[1])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][1]["status"] == "contradictory_link"


def test_attempt_operation_must_match_action_root():
    rows = _complete_chain()
    rows[3]["operation"] = "MODIFY_SLTP"
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] == "contradictory_link"
    assert report["rows"][3]["status"] == "contradictory_link"


def test_attempt_request_must_match_action_root():
    rows = _complete_chain()
    rows[3]["request"]["volume"] = 0.02
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] == "contradictory_link"
    assert report["rows"][3]["status"] == "contradictory_link"


def test_market_attempt_direction_must_match_request_type():
    rows = _complete_chain()
    rows[3]["request"]["type"] = 1
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] == "contradictory_link"
    assert report["rows"][3]["status"] == "contradictory_link"


def test_attempt_operation_must_match_mt5_request_action():
    rows = _complete_chain()
    rows[3]["request"]["action"] = 6
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][2]["status"] == "contradictory_link"
    assert report["rows"][3]["status"] == "contradictory_link"


def test_broker_result_kind_must_match_attempt_operation():
    rows = _complete_chain()
    rows[4]["ev"] = "mt5_close_result"
    _rehash(rows[4])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["rows"][3]["status"] == "contradictory_link"
    assert report["rows"][4]["status"] == "contradictory_link"


def test_missing_links_remain_visible_and_blocked():
    rows = _complete_chain()
    rows[5].pop("message_revision_id")
    rows[2].pop("decision_id")
    rows[3].pop("action_id")
    rows[4].pop("attempt_id")
    for row in rows[2:]:
        _rehash(row)

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="e" * 64,
    )
    statuses = {row["event_id"]: row["status"] for row in report["rows"]}

    assert statuses["event_processed"] == "missing_message_revision"
    assert statuses["event_request"] == "missing_decision"
    assert statuses["event_attempt"] == "missing_action"
    assert statuses["event_result"] == "missing_attempt"
    assert statuses["event_raw"] == "missing_decision"
    assert report["summary"]["blocked"] == 6


def test_orphan_duplicate_and_contradictory_ids_are_detected():
    rows = _complete_chain()[:2] + [
        _row(
            "mt5_modify_requested",
            "event_duplicate",
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_conflict",
        ),
        _row(
            "mt5_modify_requested",
            "event_duplicate",
            message_revision_id=_MSGREV_2,
            decision_id="decision_2",
            action_id="action_conflict",
        ),
        _row(
            "mt5_action_attempt",
            "event_orphan",
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_orphan",
            attempt_id="attempt_orphan",
        ),
    ]

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="d" * 64,
    )
    statuses = {row["row_index"]: row["status"] for row in report["rows"]}

    assert statuses[2] == "duplicate_id"
    assert statuses[3] == "duplicate_id"
    assert statuses[4] == "orphan_attempt"
    assert report["relations"]["contradictory_action_ids"] == [
        "action_conflict"
    ]


def test_legacy_rows_before_first_enriched_event_are_not_dropped():
    legacy = {
        "ts": "2026-07-25T10:00:00.000+00:00",
        "sig": "canal2_379",
        "ev": "mt5_order_requested",
    }
    current = _complete_chain()

    report = audit_causal_lineage.audit_rows(
        [legacy, *current],
        source_sha256="c" * 64,
    )

    assert report["selection"]["relevant_rows"] == 7
    assert report["rows"][0]["status"] == "legacy_before_contract"
    assert {
        row["status"] for row in report["rows"][1:]
    } == {"complete"}


def test_cli_output_is_deterministic_and_binds_source_bytes(tmp_path):
    events = tmp_path / "events.jsonl"
    output = tmp_path / "audit.json"
    raw = "".join(
        json.dumps(row, sort_keys=True) + "\n"
        for row in _complete_chain()
    )
    events.write_text(raw, encoding="utf-8")

    args = [
        "--events", str(events),
        "--output", str(output),
        "--since", "2026-07-26",
        "--until", "2026-07-26",
    ]
    assert audit_causal_lineage.main(args) == 0
    first = output.read_bytes()
    assert audit_causal_lineage.main(args) == 0
    second = output.read_bytes()

    assert first == second
    report = json.loads(first)
    assert report["source"]["sha256"] == hashlib.sha256(
        events.read_bytes()
    ).hexdigest()


def test_present_ids_must_reference_the_upstream_causal_chain():
    complete = _complete_chain()
    rows = [complete[0], complete[1], complete[2], complete[5]]
    unknown_revision = f"msgrev_{'f' * 64}"
    rows[1]["message_revision_id"] = unknown_revision
    rows[3]["message_revision_id"] = unknown_revision
    rows[2]["decision_id"] = "decision_unknown"
    for row in rows[1:]:
        _rehash(row)

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="b" * 64,
    )
    statuses = {row["event_id"]: row["status"] for row in report["rows"]}

    assert statuses["event_processed"] == "missing_message_revision"
    assert statuses["event_request"] == "missing_decision"


def test_lifecycle_action_must_reference_an_action_root():
    rows = _complete_chain()
    rows.append(_row(
        "mt5_position_snapshot",
        "event_snapshot",
        action_id="action_unknown",
    ))

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="a" * 64,
    )

    snapshot = next(
        row for row in report["rows"]
        if row["event_id"] == "event_snapshot"
    )
    assert snapshot["status"] == "missing_action"


def test_contract_activation_uses_full_source_before_date_filter():
    rows = _complete_chain()
    for row in rows:
        row["ts"] = "2026-07-25T10:00:00.000+00:00"
    legacy_after_activation = {
        "ts": "2026-07-26T10:00:00.000+00:00",
        "sig": "canal2_381",
        "ev": "mt5_order_requested",
    }

    report = audit_causal_lineage.audit_rows(
        [*rows, legacy_after_activation],
        source_sha256="9" * 64,
        since="2026-07-26",
        until="2026-07-26",
    )

    assert report["selection"]["relevant_rows"] == 1
    assert report["rows"][0]["status"] != "legacy_before_contract"


def test_fingerprint_is_independent_of_local_source_path():
    first = audit_causal_lineage.audit_rows(
        _complete_chain(),
        source_sha256="8" * 64,
        source_path=r"C:\vm\runtime_data\trade_events.jsonl",
    )
    second = audit_causal_lineage.audit_rows(
        _complete_chain(),
        source_sha256="8" * 64,
        source_path=r"C:\local\download\trade_events.jsonl",
    )

    assert first["source"]["path"] != second["source"]["path"]
    assert first["fingerprint"] == second["fingerprint"]


def test_missing_envelope_and_tampered_payload_are_blocked():
    missing_envelope = _complete_chain()[0]
    missing_envelope.pop("session_id")
    tampered = _complete_chain()[0]
    tampered["event_id"] = "event_tampered"
    tampered["sig"] = "canal2_tampered"

    report = audit_causal_lineage.audit_rows(
        [missing_envelope, tampered],
        source_sha256="7" * 64,
    )
    statuses = {row["row_index"]: row["status"] for row in report["rows"]}

    assert statuses[0] == "missing_envelope"
    assert statuses[1] == "payload_hash_mismatch"


def test_coalesced_action_must_reference_two_existing_action_roots():
    rows = _complete_chain()
    rows.extend([
        _row(
            "mt5_modify_requested",
            "event_request_2",
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_2",
        ),
        _row(
            "mt5_action_coalesced",
            "event_coalesced",
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_2",
            coalesced_into_action_id="action_missing",
        ),
    ])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="6" * 64,
    )

    coalesced = next(
        row for row in report["rows"]
        if row["event_id"] == "event_coalesced"
    )
    assert coalesced["status"] == "missing_action"


def _coalesced_modify_rows(
    *,
    first_ticket: int,
    second_ticket: int,
    cycle: bool = False,
):
    rows = _decision_only_chain()
    rows[2]["declared_action_ids"] = ["action_1", "action_2"]
    rows[2]["declared_action_count"] = 2
    _rehash(rows[2])
    action_rows = [
        _row(
            "mt5_modify_requested",
            "event_request_1",
            monotonic_ns=120,
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_1",
            ticket=first_ticket,
            new_sl=4056.53,
            new_tp=4059.53,
            expected_magic=20260422,
            action_revision=0,
        ),
        _row(
            "mt5_modify_requested",
            "event_request_2",
            monotonic_ns=130,
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_2",
            ticket=second_ticket,
            new_sl=4056.53,
            new_tp=4059.53,
            expected_magic=20260422,
            action_revision=0,
        ),
    ]
    if cycle:
        action_rows.extend([
            _row(
                "mt5_action_coalesced",
                "event_relation_1",
                monotonic_ns=140,
                message_revision_id=_MSGREV_1,
                decision_id="decision_1",
                action_id="action_1",
                coalesced_into_action_id="action_2",
                kind="MODIFY_SLTP",
                ticket=first_ticket,
                new_sl=4056.53,
                new_tp=4059.53,
                payload_changed=False,
                label_changed=False,
                persistence_changed=False,
                queue_slots=1,
                expected_magic=20260422,
                action_revision=0,
            ),
            _row(
                "mt5_action_coalesced",
                "event_relation_2",
                monotonic_ns=141,
                message_revision_id=_MSGREV_1,
                decision_id="decision_1",
                action_id="action_2",
                coalesced_into_action_id="action_1",
                kind="MODIFY_SLTP",
                ticket=second_ticket,
                new_sl=4056.53,
                new_tp=4059.53,
                payload_changed=False,
                label_changed=False,
                persistence_changed=False,
                queue_slots=1,
                expected_magic=20260422,
                action_revision=0,
            ),
        ])
    else:
        action_rows.extend([
            _row(
                "mt5_action_failed",
                "event_terminal_1",
                monotonic_ns=140,
                message_revision_id=_MSGREV_1,
                decision_id="decision_1",
                action_id="action_1",
                attempt_id=None,
                kind="MODIFY_SLTP",
                ticket=first_ticket,
                attempts=0,
                last_retcode=None,
                reason="expired before first attempt",
                label="BE",
                new_sl=4056.53,
                new_tp=4059.53,
                age_seconds=60.0,
                expected_magic=20260422,
                action_revision=0,
            ),
            _row(
                "mt5_action_coalesced",
                "event_relation_2",
                monotonic_ns=141,
                message_revision_id=_MSGREV_1,
                decision_id="decision_1",
                action_id="action_2",
                coalesced_into_action_id="action_1",
                kind="MODIFY_SLTP",
                ticket=second_ticket,
                new_sl=4056.53,
                new_tp=4059.53,
                payload_changed=False,
                label_changed=False,
                persistence_changed=False,
                queue_slots=1,
                expected_magic=20260422,
                action_revision=0,
            ),
        ])
    rows[2:2] = action_rows
    return rows


def _superseded_modify_rows(*, terminal_before_attempt: bool = False):
    rows = _operation_chain("MODIFY_SLTP")
    source_root = rows[2]
    attempt = rows[3]
    result = rows[4]
    processed = rows[5]

    source_root.update({
        "action_id": "action_2",
        "new_tp": None,
        "action_revision": 1,
    })
    source_root.pop("attempt_id", None)
    attempt.update({
        "action_id": "action_2",
        "action_revision": 1,
    })
    result.update({
        "action_id": "action_2",
        "action_revision": 1,
    })
    processed.update({
        "declared_action_ids": ["action_1", "action_2"],
        "declared_action_count": 2,
    })
    for row in (source_root, attempt, result, processed):
        _rehash(row)

    target_root = _row(
        "mt5_modify_requested",
        "event_request_1",
        monotonic_ns=115,
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        action_id="action_1",
        ticket=101,
        new_sl=4047.53,
        new_tp=4059.53,
        expected_magic=20260422,
        action_revision=0,
    )
    relation = _row(
        "mt5_action_coalesced",
        "event_relation_2",
        monotonic_ns=125,
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        action_id="action_2",
        supersedes_action_id="action_1",
        kind="MODIFY_SLTP",
        ticket=101,
        new_sl=4056.53,
        new_tp=4059.53,
        payload_changed=True,
        label_changed=False,
        persistence_changed=False,
        queue_slots=1,
        expected_magic=20260422,
        action_revision=1,
    )
    rows.insert(2, target_root)
    rows.insert(4, relation)
    if terminal_before_attempt:
        terminal = _row(
            "mt5_action_failed",
            "event_terminal_2",
            monotonic_ns=130,
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_2",
            attempt_id=None,
            kind="MODIFY_SLTP",
            ticket=101,
            attempts=0,
            last_retcode=None,
            reason="expired before first attempt",
            label="BE",
            new_sl=4056.53,
            new_tp=4059.53,
            expected_magic=20260422,
            age_seconds=60.0,
            action_revision=1,
        )
        rows[5:7] = [terminal]
    return rows


def _twice_superseded_modify_rows():
    rows = _superseded_modify_rows()
    second_root = next(
        row for row in rows
        if row["event_id"] == "event_request"
    )
    second_relation = next(
        row for row in rows
        if row["event_id"] == "event_relation_2"
    )
    attempt = next(row for row in rows if row["ev"] == "mt5_action_attempt")
    result = next(row for row in rows if row["ev"] == "mt5_modify_confirmed")
    processed = next(
        row for row in rows
        if row["ev"] == "telegram_processed"
    )

    second_root["new_sl"] = 4055.0
    second_relation["new_sl"] = 4055.0
    third_root = _row(
        "mt5_modify_requested",
        "event_request_3",
        monotonic_ns=126,
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        action_id="action_3",
        ticket=101,
        new_sl=4056.53,
        new_tp=None,
        expected_magic=20260422,
        action_revision=2,
    )
    third_relation = _row(
        "mt5_action_coalesced",
        "event_relation_3",
        monotonic_ns=127,
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        action_id="action_3",
        supersedes_action_id="action_2",
        kind="MODIFY_SLTP",
        ticket=101,
        new_sl=4056.53,
        new_tp=4059.53,
        payload_changed=True,
        label_changed=False,
        persistence_changed=False,
        queue_slots=1,
        expected_magic=20260422,
        action_revision=2,
    )
    attempt.update({
        "action_id": "action_3",
        "action_revision": 2,
    })
    result.update({
        "action_id": "action_3",
        "action_revision": 2,
    })
    processed.update({
        "declared_action_ids": ["action_1", "action_2", "action_3"],
        "declared_action_count": 3,
    })
    for row in (second_root, second_relation, attempt, result, processed):
        _rehash(row)
    insert_at = rows.index(second_relation) + 1
    rows[insert_at:insert_at] = [third_root, third_relation]
    return rows


def test_coalesced_actions_must_target_the_same_ticket_and_operation():
    report = audit_causal_lineage.audit_rows(
        _coalesced_modify_rows(
            first_ticket=101,
            second_ticket=202,
        ),
        source_sha256="6" * 64,
    )

    assert report["relations"]["coalescence_mismatch_action_ids"] == [
        "action_2",
    ]
    relation = next(
        row for row in report["rows"]
        if row["event_id"] == "event_relation_2"
    )
    assert relation["status"] == "contradictory_link"


def test_compatible_coalesced_action_is_a_complete_terminal_relation():
    report = audit_causal_lineage.audit_rows(
        _coalesced_modify_rows(
            first_ticket=101,
            second_ticket=101,
        ),
        source_sha256="6" * 64,
    )

    assert report["summary"]["blocked"] == 0


def test_coalesced_relation_levels_must_match_its_source_action():
    rows = _coalesced_modify_rows(
        first_ticket=101,
        second_ticket=101,
    )
    relation = next(
        row for row in rows
        if row["event_id"] == "event_relation_2"
    )
    relation["new_sl"] = 9999.0
    _rehash(relation)

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="6" * 64,
    )

    assert report["relations"]["coalescence_mismatch_action_ids"] == [
        "action_2",
    ]


def test_coalesced_relation_magic_must_match_its_actions():
    rows = _coalesced_modify_rows(
        first_ticket=101,
        second_ticket=101,
    )
    relation = next(
        row for row in rows
        if row["event_id"] == "event_relation_2"
    )
    relation["expected_magic"] = 20260421
    _rehash(relation)

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="6" * 64,
    )

    assert report["relations"]["coalescence_mismatch_action_ids"] == [
        "action_2",
    ]


def test_superseded_action_executes_the_merged_payload():
    report = audit_causal_lineage.audit_rows(
        _superseded_modify_rows(),
        source_sha256="6" * 64,
    )

    assert report["summary"]["blocked"] == 0


def test_superseded_action_terminal_uses_the_merged_payload():
    report = audit_causal_lineage.audit_rows(
        _superseded_modify_rows(terminal_before_attempt=True),
        source_sha256="6" * 64,
    )

    assert report["summary"]["blocked"] == 0


def test_superseded_action_inherits_payload_across_the_full_chain():
    report = audit_causal_lineage.audit_rows(
        _twice_superseded_modify_rows(),
        source_sha256="6" * 64,
    )

    assert report["relations"]["coalescence_mismatch_action_ids"] == []
    assert report["summary"]["blocked"] == 0


def test_coalesced_action_graph_cannot_contain_cycles():
    report = audit_causal_lineage.audit_rows(
        _coalesced_modify_rows(
            first_ticket=101,
            second_ticket=101,
            cycle=True,
        ),
        source_sha256="6" * 64,
    )

    assert report["relations"]["cyclic_action_relation_ids"] == [
        "action_1",
        "action_2",
    ]
    assert {
        row["status"]
        for row in report["rows"]
        if row["action_id"] in {"action_1", "action_2"}
    } == {"contradictory_link"}


def test_attempt_id_cannot_link_two_logical_actions():
    rows = _complete_chain()
    rows.extend([
        _row(
            "mt5_modify_requested",
            "event_request_2",
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_2",
        ),
        _row(
            "mt5_action_attempt",
            "event_attempt_2",
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_2",
            attempt_id="attempt_1",
        ),
    ])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="5" * 64,
    )

    assert report["relations"]["contradictory_attempt_ids"] == [
        "attempt_1"
    ]
    assert any(
        row["status"] == "contradictory_link"
        for row in report["rows"]
        if row["attempt_id"] == "attempt_1"
    )


def test_declared_attempt_and_result_require_their_action_chain():
    complete = _complete_chain()
    missing_attempt = [
        complete[0],
        complete[1],
        complete[2],
        complete[5],
    ]
    decision_only = _decision_only_chain()
    decision_only[2]["declared_action_ids"] = []
    decision_only[2]["declared_action_count"] = 0
    _rehash(decision_only[2])
    orphan_result = [
        *decision_only,
        _row(
            "mt5_action_attempt",
            "event_orphan_attempt",
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_missing",
            attempt_id="attempt_missing_action",
        ),
        _row(
            "mt5_order_result",
            "event_orphan_result",
            message_revision_id=_MSGREV_1,
            decision_id="decision_1",
            action_id="action_missing",
            attempt_id="attempt_missing_action",
        ),
    ]

    request_report = audit_causal_lineage.audit_rows(
        missing_attempt,
        source_sha256="4" * 64,
    )
    request = next(
        row for row in request_report["rows"]
        if row["event_id"] == "event_request"
    )
    assert request["status"] == "missing_attempt"

    result_report = audit_causal_lineage.audit_rows(
        orphan_result,
        source_sha256="3" * 64,
    )
    result = next(
        row for row in result_report["rows"]
        if row["event_id"] == "event_orphan_result"
    )
    assert result["status"] == "missing_action"


def test_broker_result_cannot_contradict_attempt_evidence():
    rows = _complete_chain()
    rows[4]["retcode"] = 10016
    _rehash(rows[4])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="a" * 64,
    )

    result = next(
        row for row in report["rows"]
        if row["event_id"] == "event_result"
    )
    assert result["status"] == "contradictory_result"


def test_attempt_duration_must_match_monotonic_boundaries():
    rows = _complete_chain()
    rows[3]["duration_ns"] = 999
    _rehash(rows[3])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="a" * 64,
    )

    attempt = next(
        row for row in report["rows"]
        if row["event_id"] == "event_attempt"
    )
    assert attempt["status"] == "missing_execution_evidence"


def test_duplicate_attempt_evidence_is_contradictory():
    rows = _complete_chain()
    duplicate = dict(rows[3])
    duplicate["event_id"] = "event_attempt_duplicate"
    duplicate["result"] = {"retcode": 10016}
    _rehash(duplicate)
    rows.append(duplicate)

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="a" * 64,
    )

    attempts = [
        row for row in report["rows"]
        if row["attempt_id"] == "attempt_1"
    ]
    assert all(
        row["status"] == "contradictory_link"
        for row in attempts
    )


def test_action_without_attempt_or_terminal_outcome_is_blocked():
    complete = _complete_chain()
    rows = [complete[0], complete[1], complete[2], complete[5]]
    rows[2].pop("attempt_id")
    rows[2]["payload_sha256"] = _row(
        rows[2]["ev"],
        rows[2]["event_id"],
        **{
            key: value
            for key, value in rows[2].items()
            if key not in {
                "schema_version",
                "event_id",
                "session_id",
                "ts",
                "monotonic_ns",
                "code_commit",
                "payload_sha256",
                "sig",
                "ev",
            }
        },
    )["payload_sha256"]

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="2" * 64,
    )

    request = next(
        row for row in report["rows"]
        if row["event_id"] == "event_request"
    )
    assert request["status"] == "missing_execution_evidence"


def test_position_gone_terminal_without_attempt_is_blocked():
    complete = _complete_chain()
    rows = [complete[0], complete[1], complete[2]]
    rows[2].pop("attempt_id")
    rows[2] = _row(
        "mt5_modify_requested",
        "event_request",
        monotonic_ns=120,
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        action_id="action_1",
    )
    rows.append(_row(
        "mt5_modify_skipped_position_gone",
        "event_terminal",
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        action_id="action_1",
        attempt_id=None,
        retcode=10036,
        monotonic_ns=130,
    ))
    rows.append(complete[5])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="1" * 64,
    )

    request = next(
        row for row in report["rows"]
        if row["event_id"] == "event_request"
    )
    assert request["status"] == "missing_execution_evidence"


def test_explicit_pre_attempt_failure_can_close_action_with_evidence():
    complete = _complete_chain()
    rows = [complete[0], complete[1]]
    rows.append(_row(
        "mt5_modify_requested",
        "event_request",
        monotonic_ns=120,
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        action_id="action_1",
        ticket=101,
        new_sl=4056.53,
        new_tp=4059.53,
        expected_magic=20260422,
        action_revision=0,
    ))
    rows.append(_row(
        "mt5_action_failed",
        "event_terminal",
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        action_id="action_1",
        attempt_id=None,
        kind="MODIFY_SLTP",
        ticket=101,
        attempts=0,
        last_retcode=None,
        reason="expired before first attempt",
        label="BE #101",
        new_sl=4056.53,
        new_tp=4059.53,
        age_seconds=60.0,
        expected_magic=20260422,
        action_revision=0,
        monotonic_ns=130,
    ))
    rows.append(complete[5])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="1" * 64,
    )

    assert report["summary"]["blocked"] == 0


def test_position_gone_preflight_can_finish_without_executor_attempt():
    complete = _complete_chain()
    rows = [complete[0], complete[1]]
    rows.append(_row(
        "mt5_modify_requested",
        "event_request",
        monotonic_ns=120,
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        action_id="action_1",
        ticket=101,
        new_sl=4056.53,
        new_tp=4059.53,
        expected_magic=20260422,
        action_revision=0,
    ))
    rows.append(_row(
        "mt5_modify_skipped_position_gone",
        "event_terminal",
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        action_id="action_1",
        attempt_id=None,
        ticket=101,
        attempts=0,
        retcode=10036,
        label="BE #101",
        new_sl=4056.53,
        new_tp=4059.53,
        expected_magic=20260422,
        preflight_status="position_gone",
        preflight_reason="ticket_not_found",
        preflight_effective_sl=None,
        preflight_effective_tp=None,
        preflight_deferred_sl=None,
        action_revision=0,
        monotonic_ns=130,
    ))
    rows.append(complete[5])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="1" * 64,
    )

    assert report["summary"]["blocked"] == 0


def _invalid_magic_preflight_rows():
    complete = _complete_chain()
    rows = [complete[0], complete[1]]
    rows.append(_row(
        "mt5_modify_requested",
        "event_request",
        monotonic_ns=120,
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        action_id="action_1",
        ticket=101,
        new_sl=4056.53,
        new_tp=4059.53,
        expected_magic=20260422,
        action_revision=0,
    ))
    rows.append(_row(
        "mt5_action_failed",
        "event_terminal",
        monotonic_ns=130,
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        action_id="action_1",
        attempt_id=None,
        kind="MODIFY_SLTP",
        ticket=101,
        attempts=0,
        last_retcode=10013,
        reason="permanent_error_retcode_10013",
        label="BE #101",
        new_sl=4056.53,
        new_tp=4059.53,
        age_seconds=0.1,
        expected_magic=20260422,
        preflight_status="invalid_magic",
        preflight_reason="magic_mismatch",
        preflight_effective_sl=None,
        preflight_effective_tp=None,
        preflight_deferred_sl=None,
        preflight_observed_ticket=101,
        preflight_observed_magic=20260421,
        preflight_observed_kind="position",
        action_revision=0,
    ))
    rows.append(complete[5])
    return rows


def test_invalid_magic_preflight_can_finish_with_observed_owner_evidence():
    rows = _invalid_magic_preflight_rows()

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="1" * 64,
    )

    assert report["summary"]["blocked"] == 0


def test_invalid_magic_preflight_must_observe_a_conflicting_magic():
    rows = _invalid_magic_preflight_rows()
    terminal = next(
        row for row in rows
        if row["event_id"] == "event_terminal"
    )
    terminal["preflight_observed_magic"] = 20260422
    _rehash(terminal)

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="1" * 64,
    )

    assert report["summary"]["blocked"] > 0
    assert next(
        row for row in report["rows"]
        if row["event_id"] == "event_terminal"
    )["status"] == "missing_execution_evidence"


def test_attempt_without_request_or_outcome_is_blocked():
    complete = _complete_chain()
    rows = [
        complete[0],
        complete[1],
        complete[2],
        complete[3],
        complete[5],
    ]
    attempt = rows[3]
    attempt["request"] = None
    attempt["result"] = {"retcode": None}
    attempt["last_error"] = None
    attempt["exception"] = None
    attempt["payload_sha256"] = _row(
        attempt["ev"],
        attempt["event_id"],
        **{
            key: value
            for key, value in attempt.items()
            if key not in {
                "schema_version",
                "event_id",
                "session_id",
                "ts",
                "monotonic_ns",
                "code_commit",
                "payload_sha256",
                "sig",
                "ev",
            }
        },
    )["payload_sha256"]

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="0" * 64,
    )

    audited = next(
        row for row in report["rows"]
        if row["event_id"] == "event_attempt"
    )
    assert audited["status"] == "missing_execution_evidence"


def test_processed_manifest_detects_completely_missing_action():
    rows = _decision_only_chain()

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="a" * 64,
    )

    processed = next(
        row for row in report["rows"]
        if row["event_id"] == "event_processed"
    )
    assert processed["status"] == "missing_action"


def test_processed_decision_requires_explicit_action_manifest():
    rows = _decision_only_chain()
    rows[2] = _row(
        "telegram_processed",
        "event_processed",
        monotonic_ns=150,
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        **_telegram_decision_identity(),
    )

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="b" * 64,
    )

    assert _event_row(
        report["rows"],
        "telegram_processed",
    )["status"] == "missing_action_manifest"


def test_action_not_declared_by_its_decision_is_blocked():
    rows = _complete_chain()
    rows[5] = _row(
        "telegram_processed",
        "event_processed",
        monotonic_ns=150,
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        declared_action_ids=[],
        declared_action_count=0,
        **_telegram_decision_identity(),
    )

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="c" * 64,
    )

    request = next(
        row for row in report["rows"]
        if row["event_id"] == "event_request"
    )
    assert request["status"] == "missing_action_manifest"


def test_media_message_without_content_hash_is_blocked():
    raw = _row(
        "telegram_raw",
        "event_media",
        channel="canal1",
        chat_id=-1001642806869,
        message_id=20700,
        update_kind="new",
        is_edit=False,
        revision_token="new",
        message_revision_id=_MSGREV_MEDIA,
        has_text=False,
        text="",
        text_sha1=None,
        has_media=True,
        media_sha256=None,
    )

    report = audit_causal_lineage.audit_rows(
        [raw],
        source_sha256="d" * 64,
    )

    assert report["rows"][0]["status"] == "missing_media_evidence"


def test_later_stored_media_capture_completes_raw_message_evidence():
    raw = _row(
        "telegram_raw",
        "event_media_raw",
        monotonic_ns=100,
        channel="canal1",
        chat_id=-1001642806869,
        message_id=20700,
        update_kind="new",
        is_edit=False,
        revision_token="new",
        message_revision_id=_MSGREV_MEDIA,
        has_text=False,
        text="",
        text_sha1=None,
        has_media=True,
        media_sha256=None,
    )
    stored = _row(
        "telegram_media_capture_stored",
        "event_media_stored",
        monotonic_ns=110,
        channel="canal1",
        message_id=20700,
        update_kind="new",
        message_revision_id=_MSGREV_MEDIA,
        media_sha256="f" * 64,
        size_bytes=512,
        storage_path="telegram_media/canal1/media.webp",
        archive_stream="telegram_media.jsonl",
        archive_appended=True,
        attempts=1,
    )
    later_poll_copy = dict(raw)
    later_poll_copy.update({
        "event_id": "event_media_raw_poll_copy",
        "monotonic_ns": 115,
        "update_kind": "poll_edit",
        "is_edit": True,
    })
    _rehash(later_poll_copy)
    started = _row(
        "telegram_decision_started",
        "event_media_started",
        monotonic_ns=120,
        channel="canal1",
        chat_id=-1001642806869,
        message_id=20700,
        update_kind="new",
        revision_token="new",
        message_revision_id=_MSGREV_MEDIA,
        decision_id="decision_media",
    )
    processed = _row(
        "telegram_processed",
        "event_media_processed",
        monotonic_ns=130,
        channel="canal1",
        chat_id=-1001642806869,
        message_id=20700,
        update_kind="new",
        revision_token="new",
        message_revision_id=_MSGREV_MEDIA,
        decision_id="decision_media",
        declared_action_ids=[],
        declared_action_count=0,
    )

    report = audit_causal_lineage.audit_rows(
        [raw, stored, later_poll_copy, started, processed],
        source_sha256="d" * 64,
    )

    assert {row["status"] for row in report["rows"]} == {"complete"}


def test_same_revision_from_edit_and_poller_is_one_immutable_message():
    rows = _complete_chain()
    duplicate_transport = dict(rows[0])
    duplicate_transport.update({
        "event_id": "event_raw_poll_transport",
        "monotonic_ns": 105,
        "update_kind": "poll_new",
        "is_edit": False,
    })
    rows[0]["update_kind"] = "edit"
    rows[0]["is_edit"] = True
    rows[1]["update_kind"] = "edit"
    rows[5]["update_kind"] = "edit"
    _rehash(rows[0])
    _rehash(rows[1])
    _rehash(rows[5])
    _rehash(duplicate_transport)
    rows.insert(1, duplicate_transport)

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="d" * 64,
    )

    assert report["relations"]["contradictory_message_revision_ids"] == []
    assert {row["status"] for row in report["rows"]} == {"complete"}


def test_schema_two_envelope_requires_verified_code_commit():
    raw = _complete_chain()[0]
    raw["code_commit"] = None

    report = audit_causal_lineage.audit_rows(
        [raw],
        source_sha256="e" * 64,
    )

    assert report["rows"][0]["status"] == "missing_envelope"


def test_raw_message_requires_explicit_media_hash_even_without_media():
    raw = _complete_chain()[0]
    raw.pop("media_sha256")
    _rehash(raw)

    report = audit_causal_lineage.audit_rows(
        [raw],
        source_sha256="e" * 64,
    )

    assert report["rows"][0]["status"] == "invalid_message_evidence"


def test_internal_decision_must_link_to_original_decision_and_action():
    rows = _decision_only_chain()
    rows[2]["declared_action_ids"] = []
    rows[2]["declared_action_count"] = 0
    _rehash(rows[2])
    rows.extend([
        _row(
            "bot_internal_decision_started",
            "event_internal_started",
            monotonic_ns=160,
            message_revision_id=_MSGREV_1,
            decision_id="decision_internal",
            parent_decision_id="decision_1",
            decision_reason="position_lifecycle_be",
        ),
        _row(
            "mt5_modify_requested",
            "event_internal_request",
            monotonic_ns=170,
            message_revision_id=_MSGREV_1,
            decision_id="decision_internal",
            action_id="action_internal",
            ticket=101,
            new_sl=4056.53,
            new_tp=4059.53,
            expected_magic=20260422,
            action_revision=0,
        ),
        _row(
            "mt5_action_failed",
            "event_internal_terminal",
            monotonic_ns=180,
            message_revision_id=_MSGREV_1,
            decision_id="decision_internal",
            action_id="action_internal",
            attempt_id=None,
            kind="MODIFY_SLTP",
            ticket=101,
            attempts=0,
            last_retcode=None,
            reason="expired before first attempt",
            label="BE",
            new_sl=4056.53,
            new_tp=4059.53,
            expected_magic=20260422,
            age_seconds=60.0,
            action_revision=0,
        ),
        _row(
            "bot_internal_decision",
            "event_internal",
            monotonic_ns=190,
            message_revision_id=_MSGREV_1,
            decision_id="decision_internal",
            parent_decision_id="decision_1",
            decision_reason="position_lifecycle_be",
            declared_action_ids=["action_internal"],
            declared_action_count=1,
        ),
    ])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )

    assert report["summary"]["blocked"] == 0

    rows[3] = _row(
        "bot_internal_decision_started",
        "event_internal_started",
        monotonic_ns=160,
        message_revision_id=_MSGREV_1,
        decision_id="decision_internal",
        parent_decision_id="decision_missing",
        decision_reason="position_lifecycle_be",
    )
    rows[6] = _row(
        "bot_internal_decision",
        "event_internal",
        monotonic_ns=190,
        message_revision_id=_MSGREV_1,
        decision_id="decision_internal",
        parent_decision_id="decision_missing",
        decision_reason="position_lifecycle_be",
        declared_action_ids=["action_internal"],
        declared_action_count=1,
    )
    blocked = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )
    assert _event_row(
        blocked["rows"],
        "bot_internal_decision",
    )["status"] == "missing_decision"

    rows[3] = _row(
        "bot_internal_decision_started",
        "event_internal_started",
        monotonic_ns=160,
        message_revision_id=_MSGREV_1,
        decision_id="decision_internal",
        parent_decision_id="decision_1",
        decision_reason="position_lifecycle_be",
    )
    rows[6] = _row(
        "bot_internal_decision",
        "event_internal",
        monotonic_ns=190,
        message_revision_id=_MSGREV_1,
        decision_id="decision_internal",
        parent_decision_id="decision_1",
        declared_action_ids=["action_internal"],
        declared_action_count=1,
    )
    blocked = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="f" * 64,
    )
    assert _event_row(
        blocked["rows"],
        "bot_internal_decision",
    )["status"] == (
        "missing_decision_evidence"
    )


def test_internal_decision_start_must_use_parent_message_revision():
    rows = _decision_only_chain()
    rows[2]["declared_action_ids"] = []
    rows[2]["declared_action_count"] = 0
    _rehash(rows[2])
    rows.extend([
        _row(
            "telegram_raw",
            "event_raw_2",
            monotonic_ns=160,
            channel="canal2",
            chat_id=_RAW_CHAT_ID,
            message_id=_RAW_MESSAGE_ID + 1,
            update_kind="new",
            is_edit=False,
            revision_token=_RAW_REVISION_TOKEN,
            message_revision_id=_MSGREV_2,
            has_text=True,
            text=_RAW_TEXT,
            text_sha1=_RAW_TEXT_SHA1,
            has_media=False,
            media_sha256=None,
        ),
        _row(
            "telegram_decision_started",
            "event_decision_2_started",
            monotonic_ns=170,
            channel="canal2",
            chat_id=_RAW_CHAT_ID,
            message_id=_RAW_MESSAGE_ID + 1,
            revision_token=_RAW_REVISION_TOKEN,
            update_kind="new",
            message_revision_id=_MSGREV_2,
            decision_id="decision_2",
        ),
        _row(
            "telegram_processed",
            "event_decision_2",
            monotonic_ns=180,
            channel="canal2",
            chat_id=_RAW_CHAT_ID,
            message_id=_RAW_MESSAGE_ID + 1,
            revision_token=_RAW_REVISION_TOKEN,
            update_kind="new",
            message_revision_id=_MSGREV_2,
            decision_id="decision_2",
            declared_action_ids=[],
            declared_action_count=0,
        ),
        _row(
            "bot_internal_decision_started",
            "event_internal_started",
            monotonic_ns=190,
            message_revision_id=_MSGREV_2,
            decision_id="decision_internal",
            parent_decision_id="decision_1",
            decision_reason="position_lifecycle_be",
        ),
    ])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="e" * 64,
    )

    assert _event_row(
        report["rows"],
        "bot_internal_decision_started",
    )["status"] == "contradictory_link"


def test_internal_decision_parent_graph_cannot_contain_cycles():
    rows = _decision_only_chain()
    rows[2]["declared_action_ids"] = []
    rows[2]["declared_action_count"] = 0
    _rehash(rows[2])
    rows.extend([
        _row(
            "bot_internal_decision_started",
            "event_internal_a_started",
            monotonic_ns=160,
            message_revision_id=_MSGREV_1,
            decision_id="decision_internal_a",
            parent_decision_id="decision_internal_b",
            decision_reason="cycle_a",
        ),
        _row(
            "bot_internal_decision",
            "event_internal_a",
            monotonic_ns=170,
            message_revision_id=_MSGREV_1,
            decision_id="decision_internal_a",
            parent_decision_id="decision_internal_b",
            decision_reason="cycle_a",
            declared_action_ids=[],
            declared_action_count=0,
        ),
        _row(
            "bot_internal_decision_started",
            "event_internal_b_started",
            monotonic_ns=180,
            message_revision_id=_MSGREV_1,
            decision_id="decision_internal_b",
            parent_decision_id="decision_internal_a",
            decision_reason="cycle_b",
        ),
        _row(
            "bot_internal_decision",
            "event_internal_b",
            monotonic_ns=190,
            message_revision_id=_MSGREV_1,
            decision_id="decision_internal_b",
            parent_decision_id="decision_internal_a",
            decision_reason="cycle_b",
            declared_action_ids=[],
            declared_action_count=0,
        ),
    ])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="e" * 64,
    )

    assert report["relations"]["cyclic_decision_ids"] == [
        "decision_internal_a",
        "decision_internal_b",
    ]
    assert {
        row["status"]
        for row in report["rows"]
        if row["decision_id"] in {
            "decision_internal_a",
            "decision_internal_b",
        }
    } == {"contradictory_link"}


def test_raw_message_without_processed_decision_is_blocked():
    report = audit_causal_lineage.audit_rows(
        [_complete_chain()[0]],
        source_sha256="9" * 64,
    )

    assert report["rows"][0]["status"] == "missing_decision"


def test_failed_handler_decision_keeps_actions_attributed_before_retry():
    rows = _complete_chain()
    rows[5] = _row(
        "telegram_processing_failed",
        "event_failed_decision",
        monotonic_ns=150,
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        declared_action_ids=["action_1"],
        declared_action_count=1,
        exception_type="RuntimeError",
        exception_message="handler failed",
        **_telegram_decision_identity(),
    )
    rows.extend([
        _row(
            "telegram_decision_started",
            "event_retry_started",
            monotonic_ns=160,
            message_revision_id=_MSGREV_1,
            decision_id="decision_retry",
            **_telegram_decision_identity(),
        ),
        _row(
            "telegram_processed",
            "event_retry_decision",
            monotonic_ns=170,
            message_revision_id=_MSGREV_1,
            decision_id="decision_retry",
            declared_action_ids=[],
            declared_action_count=0,
            **_telegram_decision_identity(),
        ),
    ])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="8" * 64,
    )

    assert report["selection"]["relevant_rows"] == 8
    assert report["summary"]["complete"] == 8
    assert report["summary"]["blocked"] == 0


def test_failed_handler_decision_requires_exception_evidence():
    rows = _complete_chain()
    rows[5] = _row(
        "telegram_processing_failed",
        "event_failed_decision",
        monotonic_ns=150,
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        declared_action_ids=["action_1"],
        declared_action_count=1,
        **_telegram_decision_identity(),
    )
    rows.extend([
        _row(
            "telegram_decision_started",
            "event_retry_started",
            monotonic_ns=160,
            message_revision_id=_MSGREV_1,
            decision_id="decision_retry",
            **_telegram_decision_identity(),
        ),
        _row(
            "telegram_processed",
            "event_retry_decision",
            monotonic_ns=170,
            message_revision_id=_MSGREV_1,
            decision_id="decision_retry",
            declared_action_ids=[],
            declared_action_count=0,
            **_telegram_decision_identity(),
        ),
    ])

    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256="8" * 64,
    )

    failed = next(
        row for row in report["rows"]
        if row["event_id"] == "event_failed_decision"
    )
    assert failed["status"] == "missing_decision_evidence"


def test_cli_returns_nonzero_when_certification_is_blocked(tmp_path):
    events = tmp_path / "events.jsonl"
    output = tmp_path / "audit.json"
    rows = _complete_chain()[:3]
    rows[2] = _row(
        "mt5_order_requested",
        "event_request",
        message_revision_id=_MSGREV_1,
        decision_id="decision_1",
        action_id="action_1",
    )
    events.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    exit_code = audit_causal_lineage.main([
        "--events", str(events),
        "--output", str(output),
    ])

    assert exit_code == 2
    assert json.loads(output.read_text(encoding="utf-8"))[
        "summary"
    ]["blocked"] > 0


def test_cli_reports_invalid_utf8_source_line_instead_of_crashing(tmp_path):
    events = tmp_path / "events.jsonl"
    output = tmp_path / "audit.json"
    valid = b"".join(
        (json.dumps(row) + "\n").encode("utf-8")
        for row in _complete_chain()
    )
    events.write_bytes(valid + b'{"ev":"telegram_raw","raw":"\xff"}\n')

    exit_code = audit_causal_lineage.main([
        "--events", str(events),
        "--output", str(output),
    ])

    assert exit_code == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["invalid_source_line"] == 1
    assert report["summary"]["blocked"] == 1
    assert report["selection"]["source_lines"] == 7
    assert report["selection"]["parsed_rows"] == 6
    assert report["source_integrity"]["invalid_lines"] == [{
        "line_number": 7,
        "reason": "invalid_utf8",
    }]


def test_cli_rejects_non_finite_json_constants(tmp_path):
    events = tmp_path / "events.jsonl"
    output = tmp_path / "audit.json"
    valid = b"".join(
        (json.dumps(row) + "\n").encode("utf-8")
        for row in _complete_chain()
    )
    events.write_bytes(valid + b'{"ev":"telegram_raw","price":NaN}\n')

    exit_code = audit_causal_lineage.main([
        "--events", str(events),
        "--output", str(output),
    ])

    assert exit_code == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["invalid_source_line"] == 1
    assert report["summary"]["blocked"] == 1
    assert report["source_integrity"]["invalid_lines"] == [{
        "line_number": 7,
        "reason": "invalid_json",
    }]


async def test_real_dispatch_executor_journal_chain_passes_audit(
        tmp_path, monkeypatch):
    events_path = tmp_path / "events.jsonl"
    monkeypatch.setattr(journal, "EVENTS_FILE", events_path)
    monkeypatch.setenv("BOT_WATCHER_VERIFIED_HEAD", "a" * 40)
    listener._seen_new_msg_ids.clear()
    listener._seen_new_msgs_order.clear()
    listener._dispatch_inflight_revisions.clear()
    listener._dispatch_completed_revisions.clear()
    listener._dispatch_completed_order.clear()

    tick = SimpleNamespace(
        time=1784820626,
        time_msc=1784820626390,
        bid=4056.49,
        ask=4056.53,
        last=4056.51,
        volume=12,
        flags=6,
        volume_real=12.0,
    )
    position = SimpleNamespace(
        ticket=8001,
        symbol="XAUUSD",
        magic=20260422,
        type=executor.mt5.ORDER_TYPE_BUY,
        volume=0.01,
        price_open=4056.53,
        price_current=4056.53,
        sl=0.0,
        tp=4059.53,
        profit=0.0,
        comment="c2_380",
    )
    result = SimpleNamespace(
        retcode=executor.mt5.TRADE_RETCODE_DONE,
        deal=7001,
        order=8001,
        volume=0.01,
        price=4056.53,
        bid=4056.49,
        ask=4056.53,
        comment="Request executed",
        request_id=91,
        retcode_external=0,
    )
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info_tick",
        lambda symbol: tick,
    )
    monkeypatch.setattr(
        executor.mt5,
        "order_send",
        lambda request: result,
    )
    monkeypatch.setattr(
        executor.mt5,
        "positions_get",
        lambda ticket=None: [position],
    )
    monkeypatch.setattr(executor, "_emit_anomaly", lambda *args, **kw: None)

    async def process(msg, label="Canal2", dedup=True):
        listener._new_msg_already_seen("canal2", msg.id)
        opened = await listener._run(
            executor.open_market_with_fill,
            "BUY",
            0.01,
            tp=4059.53,
            comment="c2_380",
            magic=20260422,
        )
        assert opened == (8001, 4056.53)

    monkeypatch.setattr(listener, "_process_canal2_new", process)
    message = SimpleNamespace(
        id=380,
        chat_id=-1003908582492,
        text="BUY NOW",
        message="BUY NOW",
        date=datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc),
        edit_date=None,
        sticker=None,
        photo=None,
        document=None,
        reply_to=None,
    )

    raw_receipt = listener._msg_diag(message, "canal2", "new")
    assert await listener._dispatch_telegram_message(
        message,
        "canal2",
        "new",
        raw_receipt=raw_receipt,
    ) is True
    assert journal.flush_events(timeout=1.0) is True

    source = events_path.read_bytes()
    rows = [
        json.loads(line)
        for line in source.decode("utf-8").splitlines()
    ]
    report = audit_causal_lineage.audit_rows(
        rows,
        source_sha256=hashlib.sha256(source).hexdigest(),
    )

    assert report["selection"]["relevant_rows"] == 6
    assert report["summary"]["complete"] == 6
    assert report["summary"]["blocked"] == 0
