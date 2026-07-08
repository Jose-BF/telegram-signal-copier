import json

import accounting_replay_validator


def _ticket(ticket=101, pnl=1.25, **overrides):
    base = {
        "ticket": ticket,
        "position_ticket": ticket,
        "role": "market_a",
        "volume": 0.01,
        "open_dt_utc": "2026-07-01T09:00:00+00:00",
        "open_price": 4200.0,
        "close_dt_utc": "2026-07-01T09:10:00+00:00",
        "close_price": 4201.25,
        "close_reason": "tp",
        "is_closed": True,
        "pnl_net": pnl,
    }
    base.update(overrides)
    return base


def _trade(**overrides):
    base = {
        "sig_id": "canal1_20700",
        "channel": "canal1",
        "direction": "BUY",
        "signal_dt_utc": "2026-07-01T08:59:58+00:00",
        "open_dt_utc": "2026-07-01T09:00:00+00:00",
        "close_dt_utc": "2026-07-01T09:10:00+00:00",
        "status": "closed",
        "pnl_real_mt5": 3.00,
        "pnl_journal": 3.00,
        "pnl_discrepancy": 0.0,
        "reconciled_ok": True,
        "pnl_mt5_complete": True,
        "journal_has_signal_closed": True,
        "health": "ok",
        "gaps": [],
        "audit_blockers": [],
        "tickets": [_ticket(101, 1.25), _ticket(102, 1.75)],
    }
    base.update(overrides)
    return base


def test_exact_trade_matches_mt5_to_the_cent():
    audit = accounting_replay_validator.validate_trade(_trade())

    assert audit["sig_id"] == "canal1_20700"
    assert audit["stage"] == "accounting_replay"
    assert audit["real_pnl_mt5"] == 3.00
    assert audit["replayed_pnl"] == 3.00
    assert audit["diff"] == 0.00
    assert audit["status"] == "exact"
    assert audit["confidence"] == "high"
    assert audit["optimization_bucket"] == "strict"
    assert audit["assumptions"] == []
    assert audit["blockers"] == []


def test_mt5_closure_event_source_is_reconstructed_not_exact():
    audit = accounting_replay_validator.validate_trade(
        _trade(pnl_real_mt5_source="positions_closed_by_mt5")
    )

    assert audit["status"] == "reconstructed"
    assert audit["confidence"] == "medium"
    assert audit["optimization_bucket"] == "review"
    assert "mt5_closure_event_fallback" in audit["assumptions"]


def test_missing_signal_closed_is_reconstructed_from_mt5_tickets():
    audit = accounting_replay_validator.validate_trade(
        _trade(
            pnl_real_mt5=3.00,
            pnl_journal=None,
            pnl_discrepancy=None,
            reconciled_ok=None,
            journal_has_signal_closed=False,
            gaps=["missing_signal_closed"],
            audit_blockers=["missing_signal_closed"],
            health="degraded",
        )
    )

    assert audit["status"] == "reconstructed"
    assert audit["confidence"] == "medium"
    assert audit["optimization_bucket"] == "review"
    assert audit["diff"] == 0.00
    assert "journal_missing_signal_closed" in audit["assumptions"]
    assert "journal_pnl_missing" in audit["assumptions"]
    assert audit["blockers"] == []


def test_mismatch_when_ticket_sum_does_not_match_real_mt5_pnl():
    audit = accounting_replay_validator.validate_trade(
        _trade(
            pnl_real_mt5=3.01,
            tickets=[_ticket(101, 1.25), _ticket(102, 1.75)],
        )
    )

    assert audit["status"] == "mismatch"
    assert audit["confidence"] == "low"
    assert audit["optimization_bucket"] == "review"
    assert audit["replayed_pnl"] == 3.00
    assert audit["diff"] == 0.01


def test_missing_ticket_pnl_blocks_responsible_reconstruction():
    audit = accounting_replay_validator.validate_trade(
        _trade(tickets=[_ticket(101, None)])
    )

    assert audit["status"] == "blocked"
    assert audit["confidence"] == "none"
    assert audit["optimization_bucket"] == "blocked"
    assert audit["replayed_pnl"] is None
    assert "missing_ticket_pnl:101" in audit["blockers"]


def test_price_based_estimate_is_marked_as_estimated():
    audit = accounting_replay_validator.validate_trade(
        _trade(
            pnl_real_mt5=1.00,
            tickets=[
                _ticket(
                    101,
                    None,
                    open_price=4200.0,
                    close_price=4201.0,
                    volume=0.01,
                )
            ],
        ),
        allow_price_estimates=True,
    )

    assert audit["status"] == "estimated"
    assert audit["confidence"] == "low"
    assert audit["optimization_bucket"] == "exploratory"
    assert audit["replayed_pnl"] == 1.00
    assert audit["diff"] == 0.00
    assert "price_formula_pnl_estimate:101" in audit["assumptions"]


def test_cli_writes_one_audit_row_per_input_trade(tmp_path):
    input_path = tmp_path / "replay_trades.jsonl"
    output_path = tmp_path / "accounting_replay_audit.jsonl"
    rows = [
        _trade(sig_id="canal1_20700"),
        _trade(
            sig_id="canal2_2787",
            journal_has_signal_closed=False,
            pnl_journal=None,
            pnl_discrepancy=None,
            reconciled_ok=None,
            gaps=["missing_signal_closed"],
        ),
    ]
    input_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    exit_code = accounting_replay_validator.main([
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--quiet",
    ])

    written = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert exit_code == 0
    assert [row["sig_id"] for row in written] == ["canal1_20700", "canal2_2787"]
    assert [row["status"] for row in written] == ["exact", "reconstructed"]
