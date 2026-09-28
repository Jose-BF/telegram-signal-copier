from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from research.gold_iterative.exit_deals import compare_exit_deals
from research.gold_iterative.ledger_evidence import ledger_ticket_evidence


BASE = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _fixture(direction="BUY", *, simultaneous=False):
    side = 0 if direction == "BUY" else 1
    price = Decimal("100.70") if side == 0 else Decimal("99.70")
    deals = []
    values = [(0, BASE, Decimal("100.20"), Decimal("0.04"), Decimal(0))]
    values += [(1, BASE + timedelta(seconds=1), price, Decimal("0.01"), Decimal("0.50")),
               (1, BASE + timedelta(seconds=1 if simultaneous else 2), price, Decimal("0.03"), Decimal("1.50"))]
    for index, (entry, when, fill, volume, profit) in enumerate(values):
        deals.append({
            "ticket": 9001 + index, "position_id": 101, "order": 8101 + index,
            "entry": entry, "type": side if entry == 0 else 1 - side,
            "reason": 3 if entry == 0 else 5, "symbol": "XAUUSD",
            "price": str(fill), "volume": str(volume), "profit": str(profit),
            "swap": "0.00", "fee": "0.00", "commission": "0.00",
            "time": (when - EPOCH) // timedelta(seconds=1),
            "time_msc": (when - EPOCH) // timedelta(milliseconds=1),
            "time_utc": when.isoformat(),
        })
    position = {
        "position_id": 101, "ticket": 9001, "volume": "0.04", "open_price": "100.20",
        "open_dt_utc": BASE.isoformat(), "close_price": str(price),
        "close_dt_utc": deals[1]["time_utc"], "is_closed": True,
        "mt5_time_offset_s": 0, "pnl_net": "2.00",
        "pnl_components": {"profit": "2.00", "swap": "0.00", "fee": "0.00",
                           "commission": "0.00", "net": "2.00"},
        "open_deal": deals[0], "close_deal": deals[1], "deals": deals,
    }
    actual = {"sig_id": "canal2_1", "channel": "canal2", "direction": direction,
              "n_positions": 1, "pnl_real_mt5": "2.00", "reconciled_ok": True,
              "positions": [position]}
    mirror = [{"ticket": "101", "closed_at": deal["time_utc"], "entry_price": "100.20",
               "exit_price": str(price), "volume": deal["volume"],
               "pnl_eur": deal["profit"], "reason": "per_leg_target"}
              for deal in deals[1:]]
    return actual, mirror


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_exact_partial_exit_facts_do_not_certify_decisions_or_requests(direction):
    actual, mirror = _fixture(direction)
    before = deepcopy((actual, mirror))
    result = compare_exit_deals(actual, mirror)
    assert result["status"] == "exact"
    assert result["exit_deal_facts_verified"] is True
    assert result["observed_exit_deals"] == result["mirror_exit_deals"] == 2
    assert result["policy_decision_sequence_verified"] is False
    assert result["broker_request_attempt_confirmation_sequence_verified"] is False
    assert result["rows"][0]["comparisons"][0]["observed"]["deal_ticket"] == "9002"
    assert result["rows"][0]["comparisons"][0]["observed"]["order_ticket"] == "8102"
    assert (actual, mirror) == before


def test_same_time_partial_deals_are_an_unordered_multiset_not_one_exit():
    actual, mirror = _fixture(simultaneous=True)
    mirror.reverse()
    mirror[0]["exit_price"] = "100.70000"
    mirror[0]["volume"] = "0.0300"
    assert compare_exit_deals(actual, mirror)["status"] == "exact"
    mirror[:] = [dict(mirror[0], volume="0.04", pnl_eur="2.00")]
    result = compare_exit_deals(actual, mirror)
    assert result["exit_deal_facts_verified"] is False
    assert "exit_deal_count_mismatch:101" in result["mismatches"]


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_offsetting_partial_money_errors_cannot_pass(direction):
    actual, mirror = _fixture(direction)
    mirror[0]["pnl_eur"], mirror[1]["pnl_eur"] = "0.40", "1.60"
    result = compare_exit_deals(actual, mirror)
    assert result["status"] == "mismatch"
    assert all("net_eur" in row["differences"] for row in result["rows"][0]["comparisons"])


@pytest.mark.parametrize("field,value", [
    ("ticket", "102"), ("closed_at", "2026-09-01T09:00:01.001+00:00"),
    ("entry_price", "100.21"), ("exit_price", "100.71"), ("volume", "0.02"),
    ("pnl_eur", "0.51"), ("reason", "trailing_stop"),
    ("closed_at", None), ("closed_at", "not-a-date"), ("closed_at", "2026-09-01T09:00:01"),
    ("volume", "NaN"), ("volume", "0"), ("volume", "-1"),
    ("pnl_eur", "1e1000"), ("pnl_eur", None), ("exit_price", float("inf")),
    ("entry_price", True), ("reason", "provider_close"), ("ticket", ""),
])
def test_each_exit_fact_is_verified(field, value):
    actual, mirror = _fixture()
    mirror[0][field] = value
    result = compare_exit_deals(actual, mirror)
    assert result["exit_deal_facts_verified"] is False
    assert result["blockers"] or result["mismatches"]


@pytest.mark.parametrize("mirror", [None, {}, "text", 1, [None], []])
def test_missing_or_malformed_exit_rows_fail_closed(mirror):
    actual, _ = _fixture()
    result = compare_exit_deals(actual, mirror)
    assert result["exit_deal_facts_verified"] is False
    assert result["observed_exit_deals"] == 2


def test_out_of_order_engine_exits_cannot_be_repaired_by_sorting():
    actual, mirror = _fixture()
    mirror.reverse()
    result = compare_exit_deals(actual, mirror)
    assert "mirror_exit_order_invalid:1" in result["blockers"]
    assert result["exit_deal_facts_verified"] is False


@pytest.mark.parametrize("reason", [None, 0, 1, 2, 3, 6, 99, True])
def test_comments_and_expert_reasons_do_not_prove_a_target_exit(reason):
    actual, mirror = _fixture()
    actual["positions"][0]["deals"][1].update(reason=reason, comment="[tp 100.70]")
    result = compare_exit_deals(actual, mirror)
    assert "observed_exit_cause_requires_decision_trace:101:9002" in result["blockers"]
    assert result["exit_deal_facts_verified"] is False


def test_millisecond_precision_is_required_even_when_second_only_times_match():
    actual, mirror = _fixture()
    actual["positions"][0]["deals"][1].pop("time_msc")
    result = compare_exit_deals(actual, mirror)
    assert "observed_exit_millisecond_time_missing:101:9002" in result["blockers"]


def test_order_identity_cannot_be_replaced_with_position_identity():
    actual, mirror = _fixture()
    actual["positions"][0]["deals"][1].pop("order")
    result = compare_exit_deals(actual, mirror)
    assert "observed_exit_order_identity_missing:101:9002" in result["blockers"]


def test_opening_costs_are_not_silently_spread_over_partial_exits():
    actual, mirror = _fixture()
    position = actual["positions"][0]
    position["deals"][0]["commission"] = "-0.10"
    position["pnl_components"].update(commission="-0.10", net="1.90")
    position["pnl_net"] = actual["pnl_real_mt5"] = "1.90"
    result = compare_exit_deals(actual, mirror)
    assert result["blockers"] == ["opening_cost_allocation_unverified:101"]


def test_verified_empty_position_outcome_is_explicit():
    actual = {"n_positions": 0, "positions": [], "pnl_real_mt5": "0.00",
              "no_position_outcome_verified": True}
    assert compare_exit_deals(actual, [])["status"] == "exact"
    actual.pop("no_position_outcome_verified")
    assert compare_exit_deals(actual, [])["status"] == "blocked"


@pytest.mark.parametrize("field,value", [
    ("time_msc", 0), ("time_utc", "2026-09-01T09:00:02+00:00"),
    ("mt5_time_offset_s", None),
])
def test_exported_exit_precision_requires_a_valid_normalized_time(field, value):
    actual, mirror = _fixture()
    position = actual["positions"][0]
    target = position if field == "mt5_time_offset_s" else position["deals"][1]
    target[field] = value
    evidence, blockers = ledger_ticket_evidence(actual)
    exported = evidence["101"]["exit_deals"][0]
    assert blockers
    assert exported["closed_at"] is None
    assert exported["millisecond_time_verified"] is False
    assert compare_exit_deals(actual, mirror)["exit_deal_facts_verified"] is False


def test_exported_exit_preserves_native_position_when_it_contradicts_summary():
    actual, mirror = _fixture()
    actual["positions"][0]["deals"][1]["position_id"] = 102
    evidence, blockers = ledger_ticket_evidence(actual)
    exported = evidence["101"]["exit_deals"][0]
    assert "ledger_deal_position_mismatch:101" in blockers
    assert exported["position_id"] == "102"
    assert exported["expected_position_id"] == "101"
    assert compare_exit_deals(actual, mirror)["exit_deal_facts_verified"] is False


@pytest.mark.parametrize("currency", ["USD", "", True, 1, {"code": "EUR"}])
def test_explicit_non_eur_currency_cannot_be_compared_as_euros(currency):
    actual, mirror = _fixture()
    actual["account_currency"] = currency
    report = compare_exit_deals(actual, mirror)
    assert report["status"] == "blocked"
    assert "ledger_account_currency_not_eur" in report["blockers"]


def test_explicit_eur_currency_preserves_valid_comparison():
    actual, mirror = _fixture()
    actual["account_currency"] = "EUR"
    assert compare_exit_deals(actual, mirror)["status"] == "exact"
