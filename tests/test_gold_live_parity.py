from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from research.gold_iterative import live_parity
from research.dubai_iterative.dataset import SignalLeg, SignalPath
from research.gold_iterative.contracts import gold_555_genome
from research.gold_iterative.live_parity import certify_live_logic_mirror
from research.gold_iterative.ledger_evidence import ledger_ticket_evidence


BASE = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _array(values, dtype=float):
    result = np.asarray(values, dtype=dtype)
    result.setflags(write=False)
    return result


def _path(*, signal_id: str = "canal2_100", pnl: str = "2.00") -> SignalPath:
    times = tuple(BASE + timedelta(seconds=index) for index in range(3))
    return SignalPath(
        signal_id=signal_id,
        day="2026-09-01",
        direction="BUY",
        signal_observed_at=BASE,
        opened_at=BASE,
        actual_pnl_eur=Decimal(pnl),
        legs=(
            SignalLeg(
                ticket="101",
                role="market_a",
                volume=0.04,
                opened_at=BASE,
                open_price=100.20,
                closed_at=times[1],
                close_price=100.70,
                close_reason="tp",
                actual_pnl_eur=Decimal(pnl),
                tp_events=(),
                sl_events=(),
            ),
        ),
        provider_events=(),
        times_ns=_array(
            [int(value.timestamp() * 1_000_000_000) for value in times],
            dtype=np.int64,
        ),
        bid=_array([100.20, 100.71, 100.75]),
        ask=_array([100.40, 100.91, 100.95]),
        exit_quotes=_array([100.20, 100.71, 100.75]),
        fx_bid=_array([1.0, 1.0, 1.0]),
        fx_ask=_array([1.0, 1.0, 1.0]),
        fx_age_ms=_array([0, 0, 0], dtype=np.int64),
        fx_valid=_array([True, True, True], dtype=np.bool_),
        contract_size=100.0,
        conversion_orientation="identity",
        currency_digits=2,
        market_evidence=({"symbol": "XAUUSD"},),
        conversion_evidence=({},),
        entry_evidence_kind="actual_mt5",
    )


def _position(*, ticket=101, pnl="2.00", closed_seconds=1):
    opened = {
        "ticket": ticket + 1000, "position_id": ticket, "entry": 0,
        "order": ticket, "reason": 3,
        "type": 0, "symbol": "XAUUSD",
        "time": int(BASE.timestamp()), "time_msc": int(BASE.timestamp()) * 1000,
        "time_utc": BASE.isoformat(), "price": 100.20, "volume": 0.04,
        "profit": "0.00", "swap": "0.00", "commission": "0.00", "fee": "0.00",
    }
    closed = dict(
        opened, ticket=ticket + 2000, order=ticket + 3000, reason=5, entry=1, type=1, profit=str(pnl),
        time=opened["time"] + closed_seconds,
        time_msc=opened["time_msc"] + closed_seconds * 1000,
        time_utc=(BASE + timedelta(seconds=closed_seconds)).isoformat(),
        price=100.70,
    )
    return {
        "position_id": ticket, "ticket": opened["ticket"],
        "mt5_time_offset_s": 0,
        "open_price": 100.20, "open_dt_utc": BASE.isoformat(), "volume": 0.04,
        "close_price": closed["price"], "close_dt_utc": closed["time_utc"],
        "is_closed": True, "pnl_net": str(pnl),
        "pnl_components": {"profit": str(pnl), "swap": "0.00", "commission": "0.00", "fee": "0.00", "net": str(pnl)},
        "open_deal": dict(opened), "close_deal": dict(closed),
        "deals": [opened, closed],
    }


def _actual(*, signal_id: str = "canal2_100", pnl: object = 2.0, entries: int = 1, positions=None):
    if positions is None:
        positions = [_position(pnl=pnl)] if entries else []
    return {
        "sig_id": signal_id,
        "channel": "canal2",
        "direction": "BUY",
        "n_positions": entries,
        "pnl_real_mt5": pnl,
        "positions": positions,
        "reconciled_ok": True,
        "no_position_outcome_verified": entries > 0,
        "strategy_snapshot": {
            "live_strategy_id": "gold_now_555_v1",
            "live_strategy_fingerprint": (
                gold_555_genome().source_strategy_fingerprint
            ),
        },
    }


def _audit(*, signal_id: str = "canal2_100", tickets: int = 1):
    return {
        "sig_id": signal_id,
        "channel": "canal2",
        "status": "exact",
        "ticket_count": tickets,
        "exact_tickets": tickets,
        "blocked_tickets": 0,
        "mismatch_tickets": 0,
        "blockers": [],
    }


def test_live_logic_mirror_must_match_actual_mt5_to_the_cent() -> None:
    report = certify_live_logic_mirror(
        paths=(_path(),),
        actual_rows=(_actual(),),
        audit_rows=(_audit(),),
        genome=gold_555_genome(),
    )

    assert report["evidence_roles"] == {
        "actual_mt5": "observed_broker_result",
        "live_logic_mirror": "strategy_replay_conditioned_on_actual_mt5_fills",
        "shadow_prediction": "prospective_replay_from_telegram_and_ticks",
    }
    assert report["actual_mt5"]["net_eur"] == "2.00"
    assert report["live_logic_mirror"]["net_eur"] == "2.00"
    assert report["parity"]["status"] == "exact"
    assert report["parity"]["net_delta_eur"] == "0.00"
    assert report["management_replay_allowed"] is True
    assert report["historical_extension_allowed"] is False
    assert report["remaining_end_to_end_gates"] == [
        "prospective_entry_outcome_parity",
        "prospective_entry_trigger_parity",
        "broker_fill_parity",
        "observed_exit_decision_and_deal_sequence_parity",
        "deterministic_terminal_lifecycle_parity",
    ]
    assert report["rows"][0]["engine_agreement"] is True


def test_one_cent_difference_blocks_historical_extension() -> None:
    report = certify_live_logic_mirror(
        paths=(_path(pnl="2.01"),),
        actual_rows=(_actual(pnl=2.01),),
        audit_rows=(_audit(),),
        genome=gold_555_genome(),
    )

    assert report["parity"]["status"] == "mismatch"
    assert report["parity"]["net_delta_eur"] == "-0.01"
    assert report["management_replay_allowed"] is False
    assert report["historical_extension_allowed"] is False
    assert "money_mismatch" in report["rows"][0]["blockers"]


def test_missing_exact_tick_audit_blocks_the_mirror() -> None:
    report = certify_live_logic_mirror(
        paths=(_path(),),
        actual_rows=(_actual(),),
        audit_rows=(),
        genome=gold_555_genome(),
    )

    assert report["parity"]["status"] == "blocked"
    assert report["management_replay_allowed"] is False
    assert report["historical_extension_allowed"] is False
    assert "observed_tick_audit_missing" in report["rows"][0]["blockers"]


def test_verified_no_position_is_an_exact_zero_not_a_missing_signal() -> None:
    actual = _actual(pnl=0, entries=0)
    actual["no_position_outcome_verified"] = True
    report = certify_live_logic_mirror(
        paths=(),
        actual_rows=(actual,),
        audit_rows=(_audit(tickets=0),),
        genome=gold_555_genome(),
    )

    assert report["parity"]["status"] == "exact"
    assert report["actual_mt5"]["signals"] == 1
    assert report["live_logic_mirror"]["exact_signals"] == 1
    assert report["rows"][0]["live_logic_mirror_eur"] == "0.00"


def test_no_position_without_a_recorded_expiry_is_not_assumed_exact() -> None:
    report = certify_live_logic_mirror(
        paths=(),
        actual_rows=(_actual(pnl=0, entries=0),),
        audit_rows=(_audit(tickets=0),),
        genome=gold_555_genome(),
    )

    assert report["parity"]["status"] == "blocked"
    assert "live_no_position_outcome_unverified" in report["rows"][0]["blockers"]


def test_wrong_live_strategy_identity_is_blocked() -> None:
    actual = _actual()
    actual["strategy_snapshot"]["live_strategy_fingerprint"] = "not-the-555"

    report = certify_live_logic_mirror(
        paths=(_path(),),
        actual_rows=(actual,),
        audit_rows=(_audit(),),
        genome=gold_555_genome(),
    )

    assert report["parity"]["status"] == "blocked"
    assert "live_strategy_fingerprint_mismatch" in report["rows"][0]["blockers"]


def test_offsetting_ticket_errors_cannot_certify_an_exact_basket() -> None:
    path = _path(pnl="6.00")
    first = replace(
        path.legs[0], actual_pnl_eur=Decimal("1.00"), close_price=100.45,
    )
    second = replace(
        first, ticket="102", role="market_b", closed_at=BASE + timedelta(seconds=2),
        actual_pnl_eur=Decimal("5.00"), close_price=101.45,
    )
    path = replace(
        path, legs=(first, second), bid=_array([100.20, 100.71, 101.21]),
        ask=_array([100.40, 100.91, 101.41]),
        exit_quotes=_array([100.20, 100.71, 101.21]),
    )
    report = certify_live_logic_mirror(
        paths=(path,), actual_rows=(_actual(pnl=6.0, entries=2, positions=[
            _position(pnl="1.00"), _position(ticket=102, pnl="5.00", closed_seconds=2),
        ]),),
        audit_rows=(_audit(tickets=2),), genome=gold_555_genome(),
    )

    assert report["actual_mt5"]["net_eur"] == "6.00"
    assert report["live_logic_mirror"]["net_eur"] == "6.00"
    assert report["parity"]["net_delta_eur"] == "0.00"
    assert report["parity"]["status"] == "mismatch"
    assert report["management_replay_allowed"] is False
    assert "ticket_money_mismatch:101" in report["rows"][0]["blockers"]
    assert "ticket_money_mismatch:102" in report["rows"][0]["blockers"]
    assert [row["net_delta_eur"] for row in report["rows"][0]["ticket_rows"]] == [
        "1.00", "-1.00",
    ]


@pytest.mark.parametrize(
    ("changes", "blocker"),
    [
        ({"actual_pnl_eur": Decimal("3.00")}, "actual_ticket_money_sum_mismatch"),
        ({"actual_pnl_eur": Decimal("NaN")}, "actual_ticket_money_invalid:101"),
        ({"volume": float("nan")}, "actual_ticket_volume_invalid:101"),
        ({"closed_at": None}, "actual_ticket_not_closed:101"),
        ({"ticket": ""}, "actual_ticket_identity_missing"),
    ],
)
def test_invalid_actual_ticket_evidence_is_retained_and_blocked(changes, blocker):
    path = _path()
    path = replace(path, legs=(replace(path.legs[0], **changes),))
    report = certify_live_logic_mirror(
        paths=(path,), actual_rows=(_actual(),), audit_rows=(_audit(),),
        genome=gold_555_genome(),
    )
    assert report["actual_mt5"]["signals"] == 1
    assert report["parity"]["status"] == "blocked"
    assert blocker in report["rows"][0]["blockers"]


def test_duplicate_actual_ticket_cannot_be_hidden_by_entry_count():
    path = _path(pnl="4.00")
    leg = replace(path.legs[0], actual_pnl_eur=Decimal("2.00"))
    path = replace(path, legs=(leg, leg))
    report = certify_live_logic_mirror(
        paths=(path,), actual_rows=(_actual(pnl=4, entries=2, positions=[
            _position(pnl="2.00"), _position(ticket=102, pnl="2.00"),
        ]),),
        audit_rows=(_audit(tickets=2),), genome=gold_555_genome(),
    )
    assert report["parity"]["status"] == "blocked"
    assert "duplicate_actual_ticket:101" in report["rows"][0]["blockers"]


def _agreed_result(monkeypatch, transform):
    path = _path()
    genome = gold_555_genome().with_change(
        entry_mode="actual_mt5", leg_count=1, volume_weights=(0.04,),
        entry_ladder_mode="simultaneous", entry_ladder_step=None,
        entry_value=None, entry_confirmation_value=None, target_steps=(0.5,),
    )
    result = transform(live_parity.simulate(path, genome))
    monkeypatch.setattr(live_parity, "simulate", lambda *args: result)
    monkeypatch.setattr(live_parity, "oracle_simulate", lambda *args: result)
    return certify_live_logic_mirror(
        paths=(path,), actual_rows=(_actual(),), audit_rows=(_audit(),),
        genome=gold_555_genome(), fast_evaluator=lambda *args: result,
    )


@pytest.mark.parametrize(
    ("target", "changes", "blocker"),
    [
        ("entry", {"ticket": "wrong"}, "unexpected_mirror_ticket:wrong"),
        ("entry", {"volume": 0.03}, "ticket_entry_facts_mismatch:101"),
        ("entry", {"entry_price": 100.21}, "ticket_entry_facts_mismatch:101"),
        ("entry", {"opened_at": BASE + timedelta(seconds=1)}, "ticket_entry_facts_mismatch:101"),
        ("exit", {"ticket": "wrong"}, "unexpected_mirror_ticket:wrong"),
        ("exit", {"volume": 0.03}, "ticket_exit_volume_mismatch:101"),
        ("exit", {"pnl_eur": None}, "mirror_ticket_money_missing:101"),
        ("exit", {"volume": float("nan")}, "mirror_ticket_volume_invalid:101"),
    ],
)
def test_engine_agreement_cannot_replace_ticket_evidence(monkeypatch, target, changes, blocker):
    def transform(result):
        field = "entries" if target == "entry" else "exits"
        return replace(result, **{field: (replace(getattr(result, field)[0], **changes),)})

    report = _agreed_result(monkeypatch, transform)
    assert report["rows"][0]["engine_agreement"] is True
    assert report["management_replay_allowed"] is False
    assert blocker in report["rows"][0]["blockers"]


def test_summed_partial_closes_cannot_hide_a_different_observed_deal_count(monkeypatch):
    def transform(result):
        close = result.exits[0]
        return replace(result, exits=(
            replace(close, volume=0.01, pnl_eur=Decimal("0.50")),
            replace(close, volume=0.03, pnl_eur=Decimal("1.50")),
        ))

    report = _agreed_result(monkeypatch, transform)
    assert report["management_replay_allowed"] is False
    row = report["rows"][0]["ticket_rows"][0]
    assert row["mirror_exit_count"] == 2
    assert row["mirror_closed_volume"] == "0.04"
    assert row["mirror_eur"] == "2.00"
    assert "exit_deals:exit_deal_count_mismatch:101" in report["rows"][0]["blockers"]
    assert "observed_exit_decision_and_deal_sequence_parity" in report["remaining_end_to_end_gates"]


def _certify(actual, path=None):
    return certify_live_logic_mirror(
        paths=(_path() if path is None else path,), actual_rows=(actual,),
        audit_rows=(_audit(tickets=actual["n_positions"]),), genome=gold_555_genome(),
    )


def test_ledger_binding_keeps_independent_offsetting_money_regression():
    path = _path(pnl="6.00")
    first = replace(path.legs[0], actual_pnl_eur=Decimal("2.00"))
    second = replace(first, ticket="102", actual_pnl_eur=Decimal("4.00"))
    path = replace(path, legs=(first, second), bid=_array([100.20, 100.71, 101.21]),
                   ask=_array([100.40, 100.91, 101.41]), exit_quotes=_array([100.20, 100.71, 101.21]))
    actual = _actual(pnl=6, entries=2, positions=[
        _position(pnl="1.00"), _position(ticket=102, pnl="5.00", closed_seconds=2),
    ])
    report = _certify(actual, path)
    assert report["management_replay_allowed"] is False
    assert report["actual_mt5"]["signals"] == 1
    assert report["actual_mt5"]["entries"] == 2
    assert report["actual_mt5"]["net_eur"] == "6.00"
    assert "ledger_path_money_mismatch:101" in report["rows"][0]["blockers"]
    assert "ledger_path_money_mismatch:102" in report["rows"][0]["blockers"]
    assert [r["actual_mt5_eur"] for r in report["rows"][0]["ticket_rows"]] == ["1.00", "5.00"]


@pytest.mark.parametrize("field,value", [
    ("ticket", "999"), ("volume", 0.03), ("open_price", 100.21),
    ("opened_at", BASE + timedelta(milliseconds=1)),
])
def test_ledger_binding_rejects_path_entry_fact_changes(field, value):
    path = _path()
    report = _certify(_actual(), replace(path, legs=(replace(path.legs[0], **{field: value}),)))
    assert report["management_replay_allowed"] is False
    assert any("ledger_path_" in b for b in report["rows"][0]["blockers"])


@pytest.mark.parametrize("damage", [
    "missing_positions", "empty_positions", "duplicate_position", "open_position",
    "missing_deals", "missing_cost", "duplicate_deal", "wrong_deal_position",
    "wrong_net", "wrong_component", "partial_only", "missing_open_deal",
    "wrong_open_price", "wrong_open_time", "unknown_entry", "missing_identity",
])
def test_ledger_binding_blocks_incomplete_evidence_and_retains_rows(damage):
    actual = _actual()
    p = actual["positions"][0]
    if damage == "missing_positions":
        actual.pop("positions")
    elif damage == "empty_positions":
        actual["positions"] = []
    elif damage == "duplicate_position":
        actual["positions"].append(dict(p))
    elif damage == "open_position":
        p["is_closed"] = False
    elif damage == "missing_deals":
        p.pop("deals")
    elif damage == "missing_cost":
        p["deals"][0].pop("commission")
    elif damage == "duplicate_deal":
        p["deals"].append(dict(p["deals"][1]))
    elif damage == "wrong_deal_position":
        p["deals"][0]["position_id"] = 999
    elif damage == "wrong_net":
        p["pnl_net"] = "3.00"
    elif damage == "wrong_component":
        p["pnl_components"]["commission"] = "-1.00"
    elif damage == "partial_only":
        p["deals"][1]["volume"] = 0.01
        p["close_deal"]["volume"] = 0.01
    elif damage == "missing_open_deal":
        p.pop("open_deal")
    elif damage == "wrong_open_price":
        p["deals"][0]["price"] = 90
        p["open_deal"]["price"] = 90
    elif damage == "wrong_open_time":
        p["open_dt_utc"] = (BASE + timedelta(seconds=1)).isoformat()
    elif damage == "unknown_entry":
        p["deals"][1]["entry"] = 2
    elif damage == "missing_identity":
        p.pop("position_id")
    report = _certify(actual)
    assert report["management_replay_allowed"] is False
    assert report["parity"]["status"] == "blocked"
    assert report["actual_mt5"]["signals"] == 1
    assert report["actual_mt5"]["entries"] == 1
    assert report["rows"][0]["ticket_rows"]
    assert any("ledger_" in b for b in report["rows"][0]["blockers"])


def test_ledger_binding_uses_position_id_and_v4_contract():
    report = _certify(_actual())
    assert report["management_replay_allowed"] is True
    assert report["schema_version"] == 4
    assert report["comparison_contract"] == "ledger_bound_entry_and_exit_deals_v4"
    assert report["rows"][0]["ticket_rows"][0]["ticket"] == "101"


def test_ledger_binding_sums_partial_close_by_and_all_deal_costs():
    actual = _actual()
    p = actual["positions"][0]
    p["deals"][0]["commission"] = "-0.10"
    p["open_deal"]["commission"] = "-0.10"
    p["deals"][1].update(volume=0.01, profit="0.60", swap="-0.05", fee="-0.05", entry=3)
    p["close_deal"] = dict(p["deals"][1])
    p["deals"].append(dict(p["deals"][1], ticket=3001, volume=0.03, profit="1.60", swap="0.00", fee="0.00"))
    p["pnl_components"].update(profit="2.20", commission="-0.10", swap="-0.05", fee="-0.05")
    report = _certify(actual)
    assert report["management_replay_allowed"] is False
    assert "exit_deals:opening_cost_allocation_unverified:101" in report["rows"][0]["blockers"]
    row = report["rows"][0]["ticket_rows"][0]
    assert row["ledger_closed_volume"] == "0.04"
    assert row["ledger_deal_count"] == 3
    assert row["actual_mt5_eur"] == "2.00"


def test_ledger_binding_rejects_false_no_position_with_fill_path():
    actual = _actual(entries=0, pnl=0)
    actual["no_position_outcome_verified"] = True
    report = _certify(actual)
    assert report["management_replay_allowed"] is False
    assert report["rows"][0]["ticket_rows"]


def test_ledger_evidence_preserves_exact_deal_milliseconds_and_broker_offset():
    actual = _actual()
    p = actual["positions"][0]
    p["mt5_time_offset_s"] = 7200
    for deal in p["deals"]:
        deal["time"] += 7200
        deal["time_msc"] += 7200000 + 347
        deal["time_utc"] = datetime.fromtimestamp(deal["time"], timezone.utc).isoformat()
    p["open_deal"] = dict(p["deals"][0])
    p["close_deal"] = dict(p["deals"][1])
    original = deepcopy(actual)
    evidence, blockers = ledger_ticket_evidence(actual)
    assert blockers == ()
    assert evidence["101"]["opened_at"] == BASE + timedelta(milliseconds=347)
    assert evidence["101"]["closed_at"] == BASE + timedelta(seconds=1, milliseconds=347)
    assert evidence["101"]["net_eur"] == Decimal("2.00")
    assert actual == original


def test_ledger_evidence_keeps_first_exit_summary_but_checks_final_partial():
    actual = _actual()
    p = actual["positions"][0]
    p["deals"][1].update(volume=0.01, profit="0.50")
    p["close_deal"] = dict(p["deals"][1])
    final = dict(p["deals"][1], ticket=3001, volume=0.03, profit="1.50")
    final.update(time=final["time"] + 1, time_msc=final["time_msc"] + 1000,
                 time_utc=(BASE + timedelta(seconds=2)).isoformat())
    p["deals"].append(final)
    evidence, blockers = ledger_ticket_evidence(actual)
    assert blockers == ()
    assert evidence["101"]["closed_at"] == BASE + timedelta(seconds=2)
    assert evidence["101"]["closed_volume"] == Decimal("0.04")


@pytest.mark.parametrize("damage", [
    "second_entry", "out_before_in", "overclose", "duplicate_across_positions",
    "naive_summary", "bad_milliseconds", "bad_time_text", "nan_cost", "missing_component",
])
def test_ledger_evidence_rejects_ambiguous_deal_contracts(damage):
    actual = _actual()
    p = actual["positions"][0]
    if damage == "second_entry":
        p["deals"].append(dict(p["deals"][0], ticket=4001))
    elif damage == "out_before_in":
        closing = p["deals"][1]
        closing.update(time=closing["time"] - 2, time_msc=closing["time_msc"] - 2000,
                       time_utc=(BASE - timedelta(seconds=1)).isoformat())
        p["close_deal"] = dict(closing)
        p["close_dt_utc"] = closing["time_utc"]
    elif damage == "overclose":
        p["deals"][1]["volume"] = 0.05
        p["close_deal"]["volume"] = 0.05
    elif damage == "duplicate_across_positions":
        other = _position(ticket=102)
        other["deals"][1]["ticket"] = p["deals"][1]["ticket"]
        other["close_deal"] = dict(other["deals"][1])
        actual.update(n_positions=2, pnl_real_mt5=4, positions=[p, other])
    elif damage == "naive_summary":
        p["open_dt_utc"] = BASE.replace(tzinfo=None).isoformat()
    elif damage == "bad_milliseconds":
        p["deals"][0]["time_msc"] += 1000
        p["open_deal"] = dict(p["deals"][0])
    elif damage == "bad_time_text":
        p["deals"][0]["time_utc"] = "not a date"
        p["open_deal"] = dict(p["deals"][0])
    elif damage == "nan_cost":
        p["deals"][0]["fee"] = "NaN"
        p["open_deal"] = dict(p["deals"][0])
    elif damage == "missing_component":
        p["pnl_components"].pop("swap")
    evidence, blockers = ledger_ticket_evidence(actual)
    assert blockers
    assert "101" in evidence


def test_ledger_binding_matches_the_existing_replay_adapter_contract():
    from build_replay_trades import _normalise_ticket
    from research.dubai_iterative.dataset import _build_legs

    actual = _actual()
    ticket = _normalise_ticket(actual["positions"][0], {}, {}, {})
    legs = _build_legs({"tickets": [ticket]})
    assert legs[0].ticket == "101"
    assert actual["positions"][0]["ticket"] == 1101
    report = _certify(actual, replace(_path(), legs=legs))
    assert report["management_replay_allowed"] is True


@pytest.mark.parametrize("damage", [
    "unknown_clock", "conflicting_clock", "wrong_open_side", "wrong_close_side",
    "wrong_ledger_direction", "wrong_position_symbol", "wrong_close_symbol",
    "missing_symbol", "wrong_path_direction", "wrong_path_symbol", "missing_path_symbol",
])
def test_ledger_binding_rejects_unknown_clock_side_and_symbol(damage):
    actual = _actual()
    p = actual["positions"][0]
    path = _path()
    if damage == "unknown_clock":
        p.pop("mt5_time_offset_s")
    elif damage == "conflicting_clock":
        actual["mt5_time_offset_s"] = 7200
    elif damage == "wrong_open_side":
        p["deals"][0]["type"] = 1
        p["open_deal"] = dict(p["deals"][0])
    elif damage == "wrong_close_side":
        p["deals"][1]["type"] = 0
        p["close_deal"] = dict(p["deals"][1])
    elif damage == "wrong_ledger_direction":
        actual["direction"] = "SELL"
    elif damage == "wrong_position_symbol":
        p["symbol"] = "EURUSD"
    elif damage == "wrong_close_symbol":
        p["deals"][1]["symbol"] = "EURUSD"
        p["close_deal"] = dict(p["deals"][1])
    elif damage == "missing_symbol":
        for d in p["deals"]:
            d.pop("symbol")
        p["open_deal"] = dict(p["deals"][0])
        p["close_deal"] = dict(p["deals"][1])
    elif damage == "wrong_path_direction":
        path = replace(path, direction="SELL")
    elif damage == "wrong_path_symbol":
        path = replace(path, market_evidence=({"symbol": "EURUSD"},))
    elif damage == "missing_path_symbol":
        path = replace(path, market_evidence=({},))
    report = _certify(actual, path)
    assert report["management_replay_allowed"] is False
    assert any("ledger_" in b for b in report["rows"][0]["blockers"])


@pytest.mark.parametrize("changes", [
    {"closed_at": BASE + timedelta(seconds=1, milliseconds=1)},
    {"exit_price": 100.71},
    {"entry_price": 100.21},
    {"reason": "trailing_stop"},
])
def test_matching_ticket_money_cannot_hide_incompatible_exit_facts(monkeypatch, changes):
    report = _agreed_result(monkeypatch, lambda result: replace(
        result, exits=(replace(result.exits[0], **changes),),
    ))
    assert report["parity"]["net_delta_eur"] == "0.00"
    assert report["rows"][0]["engine_agreement"] is True
    assert report["management_replay_allowed"] is False
