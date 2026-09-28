from datetime import datetime

import numpy as np

from tools.audit_native_equity_snapshots import snapshot_row


def inputs():
    event = {"event_id": "one", "ts": "2026-09-14T10:00:00+00:00",
             "balance": 100, "equity": 109.09}
    source_msc = int(datetime.fromisoformat(event["ts"]).timestamp() * 1000) + 10_800_000
    positions = [{"position_id": 10, "signal_id": "canal1_10", "entry_msc": source_msc - 100,
                  "exit_msc": source_msc + 100, "direction": "BUY", "entry_price": 100, "volume": .1}]
    market = (np.array([source_msc - 10], dtype=np.int64), np.array([101.]), np.array([101.1]))
    fx = (np.array([source_msc - 20], dtype=np.int64), np.array([1.09]), np.array([1.1]))
    return event, positions, market, fx


def test_account_snapshot_keeps_exact_and_mismatched_marks_distinct():
    event, positions, market, fx = inputs()
    row = snapshot_row(event, positions, market, fx, offset_seconds=10_800,
                       clock_status="no_direct_anchor", max_fx_age_ms=5000, contract_size=100)
    assert row["status"] == "nonzero_snapshot_exact"
    assert row["open_position_ids"] == [10]
    assert row["equity_minus_balance_eur"] == "9.09"
    assert row["modeled_floating_eur"] == "9.09"
    event["equity"] = 110
    row = snapshot_row(event, positions, market, fx, offset_seconds=10_800,
                       clock_status="no_direct_anchor", max_fx_age_ms=5000, contract_size=100)
    assert row["status"] == "nonzero_snapshot_difference"
    assert row["model_minus_account_eur"] == "-0.91"


def test_account_snapshot_retains_stale_fx_and_unattributed_floating():
    event, positions, market, fx = inputs()
    fx = (fx[0] - 6000, fx[1], fx[2])
    row = snapshot_row(event, positions, market, fx, offset_seconds=10_800,
                       clock_status="no_direct_anchor", max_fx_age_ms=5000, contract_size=100)
    assert row["status"] == "blocked_stale_quote"
    assert "modeled_floating_eur" not in row
    row = snapshot_row(event, [], market, fx, offset_seconds=10_800,
                       clock_status="no_direct_anchor", max_fx_age_ms=5000, contract_size=100)
    assert row["status"] == "unattributed_account_floating"
    assert row["open_position_count"] == 0
