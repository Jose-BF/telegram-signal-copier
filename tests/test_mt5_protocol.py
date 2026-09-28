from dataclasses import replace

import pytest

from mt5_protocol import (
    PROTOCOL_VERSION,
    BrokerRequest,
    IntentKey,
    IntentState,
    LookupState,
    classify_broker_result,
    classify_lookup_result,
)


def _key() -> IntentKey:
    return IntentKey(
        account_fingerprint="server/account",
        channel="canal1",
        signal_root="canal1_3086",
        generation=2,
        leg="entry-1",
        operation="OPEN_MARKET",
        revision=3,
    )


def test_intent_identity_is_stable_and_changes_with_every_identity_dimension():
    key = _key()
    assert key.intent_id == _key().intent_id
    assert key.intent_id.startswith("mt5i-v1-")

    for field, value in {
        "account_fingerprint": "other/account",
        "channel": "canal2",
        "signal_root": "canal1_9999",
        "generation": 3,
        "leg": "entry-2",
        "operation": "CLOSE_POSITION",
        "revision": 4,
    }.items():
        assert replace(key, **{field: value}).intent_id != key.intent_id


def test_request_round_trip_preserves_version_identity_and_payload():
    request = BrokerRequest.create(
        _key(),
        {"symbol": "XAUUSD", "volume": 0.01, "sl": 3575.5},
        request_id="request-1",
        attempt_id="attempt-1",
        action_id="action-1",
    )

    assert request.protocol_version == PROTOCOL_VERSION
    assert request.intent_id == request.intent_key.intent_id
    assert BrokerRequest.from_dict(request.to_dict()) == request


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"retcode": 10009}, IntentState.DONE),
        ({"retcode": 10025}, IntentState.DONE),
        ({"retcode": 10008}, IntentState.PLACED),
        ({"retcode": 10010}, IntentState.DONE_PARTIAL),
        ({"retcode": 10011}, IntentState.UNKNOWN),
        ({"retcode": 10012}, IntentState.UNKNOWN),
        (None, IntentState.UNKNOWN),
        ({"retcode": 10014}, IntentState.REJECTED),
    ],
)
def test_result_classification_never_turns_ambiguous_results_into_retry_permission(result, expected):
    assert classify_broker_result(result).state is expected


def test_lookup_distinguishes_failure_from_known_empty_result():
    assert classify_lookup_result(None) is LookupState.UNKNOWN
    assert classify_lookup_result([]) is LookupState.EMPTY
    assert classify_lookup_result([{"ticket": 42}]) is LookupState.FOUND


def test_request_payload_is_deeply_immutable_after_admission():
    request = BrokerRequest.create(_key(), {"volume": 0.01, "nested": {"sl": 3575.5}})

    with pytest.raises(TypeError):
        request.payload["volume"] = 0.5
    detached = request.payload["nested"]
    detached["sl"] = 1.0

    assert request.to_dict()["payload"] == {"nested": {"sl": 3575.5}, "volume": 0.01}


def test_unrecognized_retcode_remains_unknown_and_preserves_raw_result():
    outcome = classify_broker_result({"retcode": 99999, "comment": "future code", "order": 7})

    assert outcome.state is IntentState.UNKNOWN
    assert outcome.raw_result == {"retcode": 99999, "comment": "future code", "order": 7}


@pytest.mark.parametrize("retcode", range(10040, 10047))
def test_documented_terminal_rejection_codes_are_known_rejections(retcode):
    assert classify_broker_result({"retcode": retcode}).state is IntentState.REJECTED
