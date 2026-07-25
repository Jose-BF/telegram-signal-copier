import executed_simulation_contract
import strategy_policies


def _ticket(ticket, *, open_price, volume):
    return {
        "ticket": ticket,
        "open_dt_utc": "2026-07-24T10:00:00+00:00",
        "open_price": open_price,
        "volume": volume,
    }


def _trade():
    return {
        "sig_id": "canal2_380",
        "tickets": [
            _ticket(101, open_price=4056.53, volume=0.01),
            _ticket(102, open_price=4056.56, volume=0.01),
        ],
    }


def _policy(policy_id, *, mode="risk_free_allocation"):
    return strategy_policies.StrategyPolicy(
        policy_id=policy_id,
        mode=mode,
        close_legs=0,
        be_legs=0,
        runner_legs=2,
        base_leg_count=2,
    )


def _row(policy_id, *, strategy_pnl=4.0, actual_pnl=1.0):
    return {
        "sig_id": "canal2_380",
        "status": "simulated",
        "strategy": policy_id,
        "actual_pnl": actual_pnl,
        "strategy_pnl": strategy_pnl,
        "policy": {
            "policy_id": policy_id,
            "entry_policy": "actual_mt5",
        },
        "entry_authority": "mt5_deals",
        "tickets": [
            {
                "ticket": 101,
                "open_time_utc": "2026-07-24T10:00:00+00:00",
                "open_price": 4056.53,
                "volume": 0.01,
                "changed_rules": ["ignored_be_sl"],
            },
            {
                "ticket": 102,
                "open_time_utc": "2026-07-24T10:00:00+00:00",
                "open_price": 4056.56,
                "volume": 0.01,
                "changed_rules": ["ignored_be_sl"],
            },
        ],
    }


def test_complete_mt5_matrix_with_immutable_entries_passes():
    actual = _policy("follow_actual", mode="follow_actual")
    alternative = _policy("no_be")
    actual_row = _row(
        "follow_actual",
        strategy_pnl=1.0,
        actual_pnl=1.0,
    )
    for ticket in actual_row["tickets"]:
        ticket["changed_rules"] = []

    result = executed_simulation_contract.validate_contract(
        [_trade()],
        [actual, alternative],
        {
            "follow_actual": [actual_row],
            "no_be": [_row("no_be")],
        },
    )

    assert result["complete"] is True
    assert result["rows_expected"] == 2
    assert result["rows_emitted"] == 2
    assert result["entry_invariant_failures"] == 0
    assert result["blockers"] == []


def test_missing_trade_policy_row_fails_closed():
    result = executed_simulation_contract.validate_contract(
        [_trade()],
        [_policy("follow_actual", mode="follow_actual"), _policy("no_be")],
        {"follow_actual": [_row("follow_actual")]},
    )

    assert result["complete"] is False
    assert result["rows_expected"] == 2
    assert result["rows_emitted"] == 1
    assert "missing_row:canal2_380:no_be" in result["blockers"]


def test_changed_mt5_entry_fact_fails_closed():
    row = _row("no_be")
    row["tickets"][0]["open_price"] = 9999.0

    result = executed_simulation_contract.validate_contract(
        [_trade()],
        [_policy("no_be")],
        {"no_be": [row]},
    )

    assert result["complete"] is False
    assert result["entry_invariant_failures"] == 1
    assert (
        "entry_mismatch:canal2_380:no_be:101:open_price"
        in result["blockers"]
    )


def test_non_mt5_entry_policy_fails_closed():
    row = _row("virtual_entry")
    row["policy"]["entry_policy"] = "provider_market"

    result = executed_simulation_contract.validate_contract(
        [_trade()],
        [_policy("virtual_entry")],
        {"virtual_entry": [row]},
    )

    assert result["complete"] is False
    assert "non_mt5_entry_policy:virtual_entry" in result["blockers"]


def test_empty_execution_universe_is_not_complete():
    result = executed_simulation_contract.validate_contract(
        [],
        [_policy("no_be")],
        {"no_be": []},
    )

    assert result["complete"] is False
    assert result["blockers"] == ["no_executed_trades"]
