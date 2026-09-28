from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from research.gold_iterative.exit_execution import bind_close_attempts_to_deals


BASE = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _fixture():
    common = {"ticket": 101, "sig": "canal2_1", "action_id": "action_a",
              "decision_id": "decision_a", "action_revision": 0}
    root = dict(common, ev="mt5_close_requested", ts=(BASE - timedelta(milliseconds=1)).isoformat())
    request = {"action": 1, "position": 101, "symbol": "XAUUSD", "magic": 7,
               "type": 1, "volume": "0.04", "price": "100.70"}
    response = {"retcode": 10009, "deal": 9002, "order": 8102, "volume": "0.04", "price": "100.69"}
    attempt = dict(common, ev="mt5_action_attempt", operation="CLOSE_POSITION",
                   attempt_id="attempt_a", broker_request_sent=True,
                   broker_request_started_utc=BASE.isoformat(),
                   broker_response_received_utc=(BASE + timedelta(milliseconds=200)).isoformat(),
                   ts=(BASE + timedelta(milliseconds=201)).isoformat(), request=request, result=response,
                   position_before={"ticket": 101, "symbol": "XAUUSD", "magic": 7,
                                    "type": 0, "volume": "0.04"})
    end = dict(common, ev="mt5_close_result", attempt_id="attempt_a", retcode=10009,
               ts=(BASE + timedelta(milliseconds=202)).isoformat())
    raw = BASE + timedelta(hours=3, milliseconds=123)
    millis = (raw - EPOCH) // timedelta(milliseconds=1)
    deal = {"ticket": 9002, "order": 8102, "position_id": 101, "entry": 1,
            "symbol": "XAUUSD", "magic": 7, "type": 1, "reason": 3,
            "volume": "0.04", "price": "100.69", "time": millis // 1000, "time_msc": millis}
    order = {"ticket": 8102, "position_id": 101, "symbol": "XAUUSD", "magic": 7,
             "type": 1, "reason": 3, "state": 4, "volume_initial": "0.04", "volume_current": "0",
             "time_done": millis // 1000, "time_done_msc": millis}
    return {"events": [root, attempt, end], "broker_deals": [deal], "broker_orders": [order],
            "position_signal": {"101": "canal2_1"}, "mt5_time_offset_s": 10800}


def test_close_fill_binds_native_deal_order_position_without_claiming_policy():
    fixture = _fixture()
    before = deepcopy(fixture)
    report = bind_close_attempts_to_deals(**fixture)
    assert report["status"] == "attribution_checked"
    assert report["bound_close_deals"] == 1
    assert report["attempts"][0]["status"] == "fill_bound"
    assert report["attempts"][0]["closed_at"] == "2026-09-07T12:00:00.123000+00:00"
    assert report["full_policy_and_causal_sequence_verified"] is False
    assert fixture == before


@pytest.mark.parametrize("path,value", [
    (("events", 1, "result", "retcode"), "10009"),
    (("events", 1, "result", "deal"), 9999),
    (("events", 1, "result", "order"), 101),
    (("events", 1, "result", "volume"), "0.05"),
    (("events", 1, "result", "price"), "100.70"),
    (("events", 1, "request", "position"), 102),
    (("events", 1, "request", "volume"), "0.05"),
    (("events", 1, "position_before", "volume"), "0.03"),
    (("events", 1, "request", "type"), 0),
    (("events", 1, "request", "magic"), 8),
    (("events", 1, "broker_request_sent"), False),
    (("events", 1, "broker_request_started_utc"), "2026-09-07T12:00:00.124+00:00"),
    (("events", 1, "broker_response_received_utc"), "2026-09-07T12:00:00.122+00:00"),
    (("events", 1, "broker_response_received_utc"), "2026-09-07T12:00:00"),
    (("events", 2, "retcode"), 10036), (("events", 2, "attempt_id"), "attempt_wrong"),
    (("events", 2, "action_id"), "action_wrong"), (("events", 0, "decision_id"), "decision_wrong"),
    (("events", 1, "action_revision"), None),
    (("broker_deals", 0, "position_id"), 102),
    (("broker_deals", 0, "reason"), 5),
    (("broker_orders", 0, "position_id"), 102),
    (("broker_orders", 0, "volume_current"), "0.01"),
    (("broker_orders", 0, "state"), 3),
    (("mt5_time_offset_s",), 0), (("mt5_time_offset_s",), None),
    (("mt5_time_offset_s",), True),
])
def test_inconsistent_close_evidence_does_not_bind(path, value):
    fixture = _fixture()
    target = fixture
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    report = bind_close_attempts_to_deals(**fixture)
    assert report["status"] == "blocked"
    assert report["bound_close_deals"] == 0


def test_missing_close_confirmation_is_not_filled_in_by_attempt_success():
    fixture = _fixture()
    fixture["events"].pop()
    report = bind_close_attempts_to_deals(**fixture)
    assert report["bound_close_deals"] == 0
    assert any("confirmation_missing" in reason for reason in report["blockers"])


def test_native_fill_cannot_be_discarded_only_because_retcode_is_not_done():
    fixture = _fixture()
    fixture["events"][1]["result"]["retcode"] = fixture["events"][2]["retcode"] = 10013
    report = bind_close_attempts_to_deals(**fixture)
    assert report["bound_close_deals"] == 1
    assert report["attempts"][0]["status"] == "fill_bound_with_non_done_retcode"


def test_partial_retcode_requires_an_explicit_partial_fill_contract():
    fixture = _fixture()
    fixture["events"][1]["result"]["retcode"] = fixture["events"][2]["retcode"] = 10010
    report = bind_close_attempts_to_deals(**fixture)
    assert report["bound_close_deals"] == 0
    assert any("partial_return_code" in reason for reason in report["blockers"])


@pytest.mark.parametrize("reason", [4, 5])
def test_redundant_close_attempt_does_not_claim_a_broker_stop_or_target(reason):
    fixture = _fixture()
    fixture["events"][1]["result"].update(retcode=10036, deal=0, order=0, volume=0, price=0)
    fixture["events"][1]["broker_request_sent"] = False
    fixture["events"][2]["retcode"] = 10036
    fixture["broker_deals"][0]["reason"] = fixture["broker_orders"][0]["reason"] = reason
    report = bind_close_attempts_to_deals(**fixture)
    assert report["status"] == "attribution_checked"
    assert report["bound_close_deals"] == 0
    assert report["attempts"][0]["status"] == "no_fill_bound"
    assert report["exit_rows"][0]["status"] == "passive_broker_exit"
    fixture["broker_orders"].clear()
    assert bind_close_attempts_to_deals(**fixture)["status"] == "blocked"


@pytest.mark.parametrize("field", ["broker_deals", "broker_orders"])
def test_duplicate_native_identities_block_the_report(field):
    fixture = _fixture()
    fixture[field].append(deepcopy(fixture[field][0]))
    assert bind_close_attempts_to_deals(**fixture)["status"] == "blocked"


def test_retry_cannot_count_the_same_deal_twice():
    fixture = _fixture()
    fixture["events"].append(deepcopy(fixture["events"][1]))
    report = bind_close_attempts_to_deals(**fixture)
    assert report["status"] == "blocked"
    assert report["bound_close_deals"] == 1


def test_empty_deal_history_cannot_verify_selected_positions():
    fixture = _fixture()
    fixture["events"].clear()
    fixture["broker_deals"].clear()
    report = bind_close_attempts_to_deals(**fixture)
    assert report["status"] == "blocked"
    assert "selected_position_exit_missing:101" in report["blockers"]


@pytest.mark.parametrize("entry,position", [(0, 101), (2, 101), (1, 102), (1, 101)])
def test_review_single_deal_contract_uses_every_deal_of_the_order(entry, position):
    fixture = _fixture()
    fixture["broker_deals"].append(dict(
        fixture["broker_deals"][0], ticket=9003, entry=entry,
        position_id=position, volume="0.01",
    ))

    report = bind_close_attempts_to_deals(**fixture)

    assert report["status"] == "blocked"
    assert report["bound_close_deals"] == 0
    assert any("single_deal_close_order_unverified" in b for b in report["blockers"])
    assert report["input_counts"]["broker_deals"] == 2


def _passive_fixture(reason=5):
    fixture = _fixture()
    fixture["events"] = []
    fixture["broker_deals"][0]["reason"] = reason
    fixture["broker_orders"][0]["reason"] = reason
    return fixture


@pytest.mark.parametrize("reason", [4, 5])
def test_review_passive_exit_needs_no_bot_request(reason):
    report = bind_close_attempts_to_deals(**_passive_fixture(reason))

    assert report["status"] == "attribution_checked"
    assert report["close_attempts"] == report["bound_close_deals"] == 0
    assert report["exit_rows"][0]["status"] == "passive_broker_exit"
    assert report["full_policy_and_causal_sequence_verified"] is False


@pytest.mark.parametrize("price", [None, "NaN", float("inf"), -float("inf"), 0, -1, True, False])
def test_review_passive_exit_requires_a_positive_finite_price(price):
    fixture = _passive_fixture()
    fixture["broker_deals"][0]["price"] = price

    report = bind_close_attempts_to_deals(**fixture)

    assert report["status"] == "blocked"
    assert report["observed_exit_deals"] == 1
    assert report["exit_rows"][0]["status"] == "blocked"
    assert report["full_policy_and_causal_sequence_verified"] is False


@pytest.mark.parametrize("reason", [4, 5])
def test_review_passive_close_by_needs_a_separate_contract(reason):
    fixture = _passive_fixture(reason)
    fixture["broker_deals"][0]["entry"] = 3

    report = bind_close_attempts_to_deals(**fixture)

    assert report["status"] == "blocked"
    assert report["observed_exit_deals"] == 1
    assert report["exit_rows"][0]["status"] == "blocked"


@pytest.mark.parametrize("source", ["events", "broker_deals", "broker_orders"])
@pytest.mark.parametrize("value", [None, {}, "text", 1])
def test_review_malformed_sources_are_blocked_not_assumed_empty(source, value):
    fixture = _fixture()
    fixture[source] = value

    report = bind_close_attempts_to_deals(**fixture)

    assert report["status"] == "blocked"
    assert report["input_counts"][source] is None
    assert source in report["invalid_sources"]
    assert report["full_policy_and_causal_sequence_verified"] is False


@pytest.mark.parametrize("source", ["events", "broker_deals", "broker_orders"])
@pytest.mark.parametrize("value", [None, [], "text", True])
def test_review_invalid_rows_remain_in_source_denominators(source, value):
    fixture = _fixture()
    original_count = len(fixture[source])
    fixture[source].append(value)

    report = bind_close_attempts_to_deals(**fixture)

    assert report["status"] == "blocked"
    assert report["input_counts"][source] == original_count + 1
    assert any(row["source"] == source and row["row_index"] == original_count
               for row in report["invalid_rows"])
    assert report["close_attempts"] == 1
    assert report["observed_exit_deals"] == 1


@pytest.mark.parametrize("index,field", [
    (0, "action_id"), (0, "decision_id"), (0, "sig"),
    (1, "action_id"), (1, "decision_id"), (1, "sig"), (1, "attempt_id"),
    (2, "action_id"), (2, "decision_id"), (2, "sig"), (2, "attempt_id"),
])
@pytest.mark.parametrize("value", [None, "", " ", 1, True, [], {}])
def test_review_causal_ids_must_be_nonempty_strings(index, field, value):
    fixture = _fixture()
    fixture["events"][index][field] = value

    report = bind_close_attempts_to_deals(**fixture)

    assert report["status"] == "blocked"
    assert report["bound_close_deals"] == 0
    assert report["input_counts"]["events"] == 3
    assert report["close_attempts"] == 1
    assert any(row["source"] == "events" and row["row_index"] == index
               for row in report["invalid_rows"])


@pytest.mark.parametrize("mapping", [
    None, [], True, "text", {}, {101: "canal2_1"},
    {"101": None}, {"101": 1}, {"101": []}, {"101": {}},
    {"101": ""}, {"101": " "}, {"": "canal2_1"},
    {" ": "canal2_1"}, {"bad": "canal2_1"},
    {"101": "canal2_1", "0101": "canal2_2"},
])
def test_review_selected_mapping_requires_valid_string_pairs(mapping):
    fixture = _passive_fixture()
    fixture["position_signal"] = mapping

    report = bind_close_attempts_to_deals(**fixture)

    assert report["status"] == "blocked"
    assert report["input_counts"]["position_signal"] == (
        len(mapping) if isinstance(mapping, dict) else None
    )
    assert report["bound_close_deals"] == 0


def test_review_bad_attempt_id_remains_visible_even_without_a_fill():
    fixture = _passive_fixture()
    event = _fixture()["events"][1]
    event.update(attempt_id=[], broker_request_sent=False, request=None)
    event["result"].update(retcode=10036, deal=0, order=0, volume=0, price=0)
    fixture["events"] = [event]

    report = bind_close_attempts_to_deals(**fixture)

    assert report["status"] == "blocked"
    assert report["close_attempts"] == 1
    assert report["attempts"][0]["status"] == "blocked"
    assert report["attempts"][0]["event_index"] == 0


@pytest.mark.parametrize("source", ["broker_deals", "broker_orders"])
@pytest.mark.parametrize("ticket", [None, True, [], {}, "bad"])
def test_review_invalid_native_ticket_is_retained_as_an_invalid_source_row(source, ticket):
    fixture = _fixture()
    fixture[source][0]["ticket"] = ticket

    report = bind_close_attempts_to_deals(**fixture)

    assert report["status"] == "blocked"
    assert report["input_counts"][source] == 1
    assert any(row["source"] == source and row["row_index"] == 0
               for row in report["invalid_rows"])


def test_review_valid_generators_preserve_counts_and_event_indices():
    fixture = _fixture()
    for source in ("events", "broker_deals", "broker_orders"):
        fixture[source] = iter(fixture[source])

    report = bind_close_attempts_to_deals(**fixture)

    assert report["status"] == "attribution_checked"
    assert report["input_counts"] == {
        "events": 3, "broker_deals": 1, "broker_orders": 1, "position_signal": 1,
    }
    assert report["invalid_rows"] == report["invalid_sources"] == []
    assert report["attempts"][0]["event_index"] == 1
