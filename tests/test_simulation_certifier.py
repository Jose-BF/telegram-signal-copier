from copy import deepcopy

import simulation_certifier


def _ticket(ticket=101):
    return {
        "ticket": ticket,
        "leg_action": "runner",
        "open_time_utc": "2026-07-06T10:00:00+00:00",
        "open_price": 100.0,
        "volume": 0.01,
        "close_reason": "tp",
        "close_time_utc": "2026-07-06T10:10:00.123+00:00",
        "close_price": 110.0,
        "strategy_pnl": 9.09,
        "profit_currency_pnl": 10.0,
        "touch_side": "bid",
        "touch_side_price": 110.1,
        "money_conversion": {
            "symbol": "EURUSD",
            "side": "ask",
            "price": 1.1,
            "time_utc": "2026-07-06T10:10:00.120+00:00",
            "age_ms": 3,
            "freshness": "within_max_age",
            "quote_interval_ms": None,
            "next_quote_utc": None,
        },
        "money_formula": {
            "directional_delta": 10.0,
            "contract_size": 100.0,
            "volume": 0.01,
            "orientation": "account_base_profit_quote",
            "rounding": "ROUND_HALF_UP",
            "currency_digits": 2,
        },
    }


def _row(ticket=None):
    return {
        "sig_id": "canal2_380",
        "strategy": "no_be",
        "direction": "BUY",
        "entry_authority": "mt5_deals",
        "status": "simulated",
        "strategy_pnl": 9.09,
        "tickets": [ticket or _ticket()],
        "blockers": [],
    }


def _evidence():
    return {
        "market_ticks_sha256": "1" * 64,
        "market_tick_contract_sha256": "2" * 64,
        "conversion_ticks_sha256": "3" * 64,
        "conversion_tick_contract_sha256": "4" * 64,
        "money_contract_sha256": "5" * 64,
        "replay_trade_sha256": "6" * 64,
        "provider_signal_sha256": "7" * 64,
        "policy_sha256": "8" * 64,
    }


def test_exact_candidate_and_oracle_produce_deterministic_ticket_proof():
    first = simulation_certifier.certify_trade(
        candidate=_row(),
        oracle=_row(),
        tick_size=0.01,
        currency_digits=2,
        source_evidence=_evidence(),
    )
    second = simulation_certifier.certify_trade(
        candidate=deepcopy(_row()),
        oracle=deepcopy(_row()),
        tick_size=0.01,
        currency_digits=2,
        source_evidence=dict(reversed(list(_evidence().items()))),
    )

    assert first["status"] == "certified"
    assert first["certified_tickets"] == 1
    assert first["mismatched_tickets"] == 0
    assert first["blocked_tickets"] == 0
    assert first["proof_sha256"] == second["proof_sha256"]
    proof = first["ticket_proofs"][0]
    assert proof["ticket"] == "101"
    assert all(proof["comparisons"].values())


def test_one_millisecond_close_shift_fails_certification():
    candidate = _row()
    candidate["tickets"][0]["close_time_utc"] = (
        "2026-07-06T10:10:00.124+00:00"
    )

    result = simulation_certifier.certify_trade(
        candidate=candidate,
        oracle=_row(),
        tick_size=0.01,
        currency_digits=2,
        source_evidence=_evidence(),
    )

    assert result["status"] == "mismatch"
    assert result["ticket_proofs"][0]["comparisons"]["close_time_utc"] is False
    assert result["blockers"] == [
        "ticket_mismatch:canal2_380:no_be:101:close_time_utc"
    ]


def test_one_tick_close_price_shift_fails_certification():
    candidate = _row()
    candidate["tickets"][0]["close_price"] = 110.01

    result = simulation_certifier.certify_trade(
        candidate=candidate,
        oracle=_row(),
        tick_size=0.01,
        currency_digits=2,
        source_evidence=_evidence(),
    )

    assert result["status"] == "mismatch"
    assert result["ticket_proofs"][0]["comparisons"]["close_price"] is False


def test_one_cent_money_shift_fails_certification():
    candidate = _row()
    candidate["tickets"][0]["strategy_pnl"] = 9.10

    result = simulation_certifier.certify_trade(
        candidate=candidate,
        oracle=_row(),
        tick_size=0.01,
        currency_digits=2,
        source_evidence=_evidence(),
    )

    assert result["status"] == "mismatch"
    assert result["ticket_proofs"][0]["comparisons"]["strategy_pnl"] is False


def test_ticket_set_difference_fails_certification():
    candidate = _row()
    candidate["tickets"].append(_ticket(102))

    result = simulation_certifier.certify_trade(
        candidate=candidate,
        oracle=_row(),
        tick_size=0.01,
        currency_digits=2,
        source_evidence=_evidence(),
    )

    assert result["status"] == "mismatch"
    assert result["blockers"] == [
        "ticket_set_mismatch:canal2_380:no_be"
    ]


def test_duplicate_ticket_id_cannot_be_silently_collapsed():
    candidate = _row()
    duplicate = _ticket()
    duplicate["close_price"] = 109.0
    candidate["tickets"].append(duplicate)

    result = simulation_certifier.certify_trade(
        candidate=candidate,
        oracle=_row(),
        tick_size=0.01,
        currency_digits=2,
        source_evidence=_evidence(),
    )

    assert result["status"] == "mismatch"
    assert result["blockers"] == [
        "duplicate_candidate_ticket:canal2_380:no_be:101"
    ]


def test_empty_ticket_sets_cannot_certify_a_trade():
    candidate = _row()
    oracle = _row()
    candidate["tickets"] = []
    oracle["tickets"] = []

    result = simulation_certifier.certify_trade(
        candidate=candidate,
        oracle=oracle,
        tick_size=0.01,
        currency_digits=2,
        source_evidence=_evidence(),
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == [
        "empty_ticket_set:canal2_380:no_be"
    ]


def test_changed_entry_or_policy_action_fails_certification():
    candidate = _row()
    candidate["tickets"][0]["open_price"] = 100.01
    candidate["tickets"][0]["leg_action"] = "move_to_be"

    result = simulation_certifier.certify_trade(
        candidate=candidate,
        oracle=_row(),
        tick_size=0.01,
        currency_digits=2,
        source_evidence=_evidence(),
    )

    assert result["status"] == "mismatch"
    comparisons = result["ticket_proofs"][0]["comparisons"]
    assert comparisons["open_price"] is False
    assert comparisons["leg_action"] is False


def test_direction_flip_fails_certification():
    candidate = _row()
    candidate["direction"] = "SELL"

    result = simulation_certifier.certify_trade(
        candidate=candidate,
        oracle=_row(),
        tick_size=0.01,
        currency_digits=2,
        source_evidence=_evidence(),
    )

    assert result["status"] == "mismatch"
    assert result["trade_comparisons"]["direction"] is False
    assert result["blockers"] == [
        "trade_mismatch:canal2_380:no_be:direction"
    ]


def test_ticket_volume_change_fails_certification():
    candidate = _row()
    candidate["tickets"][0]["volume"] = 0.02

    result = simulation_certifier.certify_trade(
        candidate=candidate,
        oracle=_row(),
        tick_size=0.01,
        currency_digits=2,
        source_evidence=_evidence(),
    )

    assert result["status"] == "mismatch"
    assert result["ticket_proofs"][0]["comparisons"]["volume"] is False
    assert result["blockers"] == [
        "ticket_mismatch:canal2_380:no_be:101:volume"
    ]


def test_touch_side_and_conversion_evidence_must_match():
    candidate = _row()
    candidate["tickets"][0]["touch_side"] = "ask"
    candidate["tickets"][0]["money_conversion"]["price"] = 1.2

    result = simulation_certifier.certify_trade(
        candidate=candidate,
        oracle=_row(),
        tick_size=0.01,
        currency_digits=2,
        source_evidence=_evidence(),
    )

    assert result["status"] == "mismatch"
    comparisons = result["ticket_proofs"][0]["comparisons"]
    assert comparisons["touch_side"] is False
    assert comparisons["money_conversion"] is False


def test_blocked_oracle_can_never_certify_candidate():
    oracle = _row()
    oracle["status"] = "blocked"
    oracle["strategy_pnl"] = None
    oracle["tickets"] = []
    oracle["blockers"] = ["ambiguous_duplicate_tick_outcome"]

    result = simulation_certifier.certify_trade(
        candidate=_row(),
        oracle=oracle,
        tick_size=0.01,
        currency_digits=2,
        source_evidence=_evidence(),
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == [
        "oracle_blocked:canal2_380:no_be:"
        "ambiguous_duplicate_tick_outcome"
    ]


def test_invalid_source_hash_blocks_certificate():
    evidence = _evidence()
    evidence["market_ticks_sha256"] = "changed"

    result = simulation_certifier.certify_trade(
        candidate=_row(),
        oracle=_row(),
        tick_size=0.01,
        currency_digits=2,
        source_evidence=evidence,
    )

    assert result["status"] == "blocked"
    assert result["blockers"] == [
        "invalid_source_fingerprint:market_ticks_sha256"
    ]


def test_run_certificate_requires_every_expected_policy_trade_pair():
    exact = simulation_certifier.certify_trade(
        candidate=_row(),
        oracle=_row(),
        tick_size=0.01,
        currency_digits=2,
        source_evidence=_evidence(),
    )

    result = simulation_certifier.summarize_run(
        certificates=[exact],
        expected_pairs={
            ("canal2_380", "no_be"),
            ("canal2_380", "follow_actual"),
        },
    )

    assert result["complete"] is False
    assert result["conclusions_allowed"] is False
    assert result["blockers"] == [
        "missing_certificate:canal2_380:follow_actual"
    ]


def test_source_evidence_is_order_independent_and_binds_every_artifact():
    market = [
        {
            "day": "2026-07-07",
            "parquet_sha256": "a" * 64,
            "contract_sha256": "b" * 64,
        },
        {
            "day": "2026-07-06",
            "parquet_sha256": "c" * 64,
            "contract_sha256": "d" * 64,
        },
    ]
    conversion = [{
        "day": "2026-07-06",
        "parquet_sha256": "e" * 64,
        "contract_sha256": "f" * 64,
    }]
    kwargs = {
        "trade": {"sig_id": "canal2_380", "tickets": [{"ticket": 101}]},
        "provider_signal": {"provider_signal_id": "canal2_380"},
        "policy": {"policy_id": "no_be"},
        "market_tick_evidence": market,
        "conversion_tick_evidence": conversion,
        "money_contract_sha256": "9" * 64,
    }

    first = simulation_certifier.build_source_evidence(**kwargs)
    second = simulation_certifier.build_source_evidence(
        **{
            **kwargs,
            "market_tick_evidence": list(reversed(market)),
        }
    )
    changed = simulation_certifier.build_source_evidence(
        **{
            **kwargs,
            "trade": {
                "sig_id": "canal2_380",
                "tickets": [{"ticket": 101, "volume": 0.02}],
            },
        }
    )

    assert first == second
    assert first["replay_trade_sha256"] != changed["replay_trade_sha256"]
    assert set(first) == simulation_certifier.REQUIRED_SOURCE_FINGERPRINTS
