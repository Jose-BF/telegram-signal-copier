"""Independent inputs for pure matching, with no broker/store/runtime fixture."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from entry_history_reconciliation import EntryHistoryBlocked, match_market_entry
from mt5_protocol import BrokerRequest, IntentKey
from mt5_trade_protocol import TradePreparation


def sample(direction="BUY", comment="c2_123_g55"):
    side = 0 if direction == "BUY" else 1
    request = BrokerRequest.create(IntentKey("demo/7", "canal2", "canal2_123", 2,
        "entry-0", "OPEN_MARKET", 0), {
            "symbol": "XAUUSD", "direction": direction, "volume": .03,
            "sl": 70. if side == 0 else 130., "tp": None, "loss_budget": None,
            "protection_policy": "required", "magic": 222, "comment": comment,
            "deviation": 30}, request_id="request", attempt_id="attempt", action_id="action")
    native = {"action": 1, "symbol": "XAUUSD", "type": side, "volume": .03,
              "magic": 222, "comment": comment, "price": 100.}
    preparation = TradePreparation(request.request_id, request.intent_id, request.attempt_id,
        request.action_id, "worker", 123, 12., native,
        {"source_tick": {"time_msc": 1000, "bid": 99.8, "ask": 100.}})
    deal = {"ticket": 81, "order": 41, "position_id": 71, "time_msc": 1050,
            "entry": 0, "type": side, "symbol": "XAUUSD", "magic": 222,
            "comment": comment, "volume": .03, "price": 101.25}
    order = {"ticket": 41, "position_id": 71, "time_setup_msc": 1020,
             "time_done_msc": 1080, "type": side, "state": 4, "symbol": "XAUUSD",
             "magic": 222, "comment": comment, "volume_initial": .03, "volume_current": 0.}
    return dict(request=request, preparation=preparation, deals=[deal], orders=[order],
                date_from_msc=900, date_to_msc=1200, account_fingerprint="demo/7")


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_complete_entry_distinguishes_position_and_order_and_allows_slippage(direction):
    inputs = sample(direction)
    before = deepcopy(inputs)
    result = match_market_entry(**inputs)
    assert result.to_dict() == {"order": 41, "position_id": 71, "deal_ids": [81],
        "volume": .03, "price": 101.25, "first_fill_msc": 1050, "last_fill_msc": 1050}
    assert inputs == before
    with pytest.raises(FrozenInstanceError):
        result.price = 1
    result.to_dict()["deal_ids"].append(999)
    assert result.deal_ids == (81,)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_split_fills_use_weighted_actual_price_and_stable_chronological_ids(direction):
    inputs = sample(direction)
    deal = inputs["deals"][0]
    inputs["deals"] = [dict(deal, ticket=83, volume=.02, price=102., time_msc=1060),
                       dict(deal, ticket=82, volume=.01, price=99., time_msc=1050)]
    result = match_market_entry(**inputs)
    assert result.price == pytest.approx(101.)
    assert result.volume == pytest.approx(.03)
    assert result.deal_ids == (82, 83)
    assert (result.first_fill_msc, result.last_fill_msc) == (1050, 1060)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_native_closure_and_unrelated_account_records_do_not_confirm_or_hide_entry(direction):
    inputs = sample(direction)
    original = deepcopy(inputs)
    deal = inputs["deals"][0]
    inputs["deals"] += [dict(deal, ticket=91, order=51, entry=1, type=1-deal["type"],
                             magic=0, comment="[sl]", time_msc=1150, price=60.),
                        dict(deal, ticket=92, order=0, position_id=0, type=2,
                             magic=0, comment="balance", symbol="", volume=0., price=0.)]
    inputs["orders"] += [dict(inputs["orders"][0], ticket=51, type=1-deal["type"],
                              magic=0, comment="[sl]")]
    assert match_market_entry(**inputs) == match_market_entry(**original)
    inputs["deals"] = inputs["deals"][1:]
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


@pytest.mark.parametrize("target", ["deals", "orders"])
@pytest.mark.parametrize("value", [None, {}, "truncated", [], [None], [{}]])
def test_missing_or_malformed_collections_block(target, value):
    inputs = sample()
    inputs[target] = value
    with pytest.raises(EntryHistoryBlocked) as exc:
        match_market_entry(**inputs)
    assert exc.value.reason


@pytest.mark.parametrize("field", ["request_id", "intent_id", "attempt_id", "action_id"])
def test_preparation_cannot_cross_original_identity(field):
    inputs = sample()
    inputs["preparation"] = replace(inputs["preparation"], **{field: "other"})
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


@pytest.mark.parametrize("field,value", [("action", 5), ("type", 1), ("type", False),
    ("symbol", "OTHER"), ("magic", 223), ("comment", "other"), ("volume", .02),
    ("position", 71), ("price", 0)])
def test_prepared_native_must_represent_the_original_open(field, value):
    inputs = sample()
    native = dict(inputs["preparation"].native_request)
    native[field] = value
    inputs["preparation"] = replace(inputs["preparation"], native_request=native)
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


@pytest.mark.parametrize("field,value", [("date_from_msc", True), ("date_from_msc", 1100),
    ("date_to_msc", 999), ("date_to_msc", 1049), ("account_fingerprint", "demo/8")])
def test_window_and_account_are_explicit(field, value):
    inputs = sample()
    inputs[field] = value
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


@pytest.mark.parametrize("time_msc", [None, 0, -1, True, 1000., 899, 1201])
def test_source_tick_must_be_native_integer_clock_inside_window(time_msc):
    inputs = sample()
    inputs["preparation"] = replace(inputs["preparation"], evidence={"source_tick": {"time_msc": time_msc}})
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


@pytest.mark.parametrize("field,value", [("ticket", True), ("order", 0), ("position_id", -1),
    ("time_msc", 999), ("time_msc", 1201), ("time_msc", 1019), ("time_msc", 1081),
    ("entry", 2), ("entry", 1), ("type", 1), ("symbol", "OTHER"), ("magic", 223),
    ("comment", "changed"), ("volume", .02), ("volume", .04), ("volume", True),
    ("volume", float("nan")), ("price", float("inf")), ("price", 0)])
def test_invalid_or_incompatible_deal_does_not_confirm(field, value):
    inputs = sample()
    inputs["deals"][0][field] = value
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


@pytest.mark.parametrize("field,value", [("ticket", 42), ("position_id", 72), ("state", 3),
    ("state", True), ("type", 1), ("symbol", "OTHER"), ("magic", 223), ("comment", "other"),
    ("volume_initial", .04), ("volume_current", .01), ("volume_current", float("nan")),
    ("time_setup_msc", 999), ("time_done_msc", 1201), ("time_done_msc", 1049),
    ("time_setup_msc", 1060), ("time_done_msc", None)])
def test_historical_order_must_prove_full_execution_and_coherent_clock(field, value):
    inputs = sample()
    inputs["orders"][0][field] = value
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


@pytest.mark.parametrize("target", ["deals", "orders"])
def test_duplicate_ids_are_ambiguous_even_when_identical(target):
    inputs = sample()
    inputs[target].append(dict(inputs[target][0]))
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


@pytest.mark.parametrize("extra_partial", [False, True])
def test_multiple_owned_groups_are_not_ranked_by_price_or_proximity(extra_partial):
    inputs = sample()
    inputs["deals"].append(dict(inputs["deals"][0], ticket=82, order=42, position_id=72,
                                volume=.01 if extra_partial else .03))
    inputs["orders"].append(dict(inputs["orders"][0], ticket=42, position_id=72))
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


def test_unmatched_owned_order_cannot_be_hidden_by_missing_deals():
    inputs = sample()
    inputs["orders"].append(dict(inputs["orders"][0], ticket=42, position_id=72))
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


def test_exact_31_character_comment_without_prefix_matching():
    inputs = sample(comment="x" * 31)
    assert match_market_entry(**inputs).order == 41
    inputs["deals"][0]["comment"] += "x"
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


@pytest.mark.parametrize("target", ["deals", "orders"])
def test_missing_field_on_potential_candidate_is_not_silently_excluded(target):
    inputs = sample()
    inputs[target][0].pop("comment")
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


def test_history_budget_is_bounded():
    inputs = sample()
    inputs["deals"] *= 10001
    with pytest.raises(EntryHistoryBlocked, match="budget"):
        match_market_entry(**inputs)


@pytest.mark.parametrize("target", ["deals", "orders"])
@pytest.mark.parametrize("field", ["ticket", "position_id", "type", "symbol", "magic", "comment"])
def test_truncated_record_does_not_count_as_a_negative_match(target, field):
    inputs = sample()
    inputs[target][0].pop(field)
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


@pytest.mark.parametrize("fault", ["absent", "failed", "no_tick", "wrong_operation", "invalid_key"])
def test_absent_or_invalid_contract_is_explicitly_blocked(fault):
    inputs = sample()
    if fault == "absent":
        inputs["preparation"] = None
    elif fault == "failed":
        inputs["preparation"] = replace(inputs["preparation"], error="unknown", native_request=None)
    elif fault == "no_tick":
        inputs["preparation"] = replace(inputs["preparation"], evidence={})
    elif fault == "invalid_key":
        inputs["request"] = replace(inputs["request"], intent_key=None)
    else:
        key = replace(inputs["request"].intent_key, operation="CLOSE_POSITION")
        inputs["request"] = BrokerRequest.create(key, dict(inputs["request"].payload))
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


def test_same_clock_zero_window_origin_and_sorted_tie_ids():
    inputs = sample()
    inputs["date_from_msc"], inputs["date_to_msc"] = 0, 1000
    inputs["orders"][0].update(time_setup_msc=1000, time_done_msc=1000)
    deal = inputs["deals"][0]
    inputs["deals"] = [dict(deal, ticket=83, volume=.02, time_msc=1000),
                       dict(deal, ticket=82, volume=.01, time_msc=1000)]
    result = match_market_entry(**inputs)
    assert result.deal_ids == (82, 83)
    assert result.first_fill_msc == result.last_fill_msc == 1000


def test_near_full_but_not_float_roundoff_volume_remains_partial():
    inputs = sample()
    inputs["deals"][0]["volume"] -= 1e-14
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


def test_same_order_foreign_deal_cannot_disappear_through_filter():
    inputs = sample()
    inputs["deals"].append(dict(inputs["deals"][0], ticket=82, comment="other"))
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


@pytest.mark.parametrize("field", ["ticket", "order", "position_id"])
@pytest.mark.parametrize("value", [2**63, 2**64 - 1])
def test_native_ids_must_fit_positive_sqlite_integer(field, value):
    inputs = sample()
    inputs["deals"][0][field] = value
    if field == "order":
        inputs["orders"][0]["ticket"] = value
    elif field == "position_id":
        inputs["orders"][0]["position_id"] = value
    with pytest.raises(EntryHistoryBlocked):
        match_market_entry(**inputs)


def test_largest_sqlite_ids_are_preserved_without_float_coercion():
    inputs = sample()
    inputs["deals"][0].update(ticket=2**63-1, order=2**63-2, position_id=2**63-3)
    inputs["orders"][0].update(ticket=2**63-2, position_id=2**63-3)
    result = match_market_entry(**inputs)
    assert result.deal_ids == (2**63-1,)
    assert result.order == 2**63-2 and result.position_id == 2**63-3
