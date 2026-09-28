from pathlib import Path
import pytest

from tools.audit_mt5_terminal_open_logs import bind_terminal, deal_coverage, read_log


def test_read_log_extracts_identity_and_madrid_clock(tmp_path: Path):
    path = tmp_path / "20260918.log"
    path.write_text(
        "0\t0\t14:30:00.589\tTrades\t'123': market sell 0.04 XAUUSD\n"
        "0\t0\t14:30:43.784\tTrades\t'123': accepted market sell 0.04 XAUUSD\n"
        "0\t0\t14:31:07.926\tTrades\t'123': deal #1637895850 sell 0.04 XAUUSD (based on order #2038395764)\n"
        "0\t0\t14:31:08.007\tTrades\t'123': order #2038395764 sell 0.04 XAUUSD done in 67489.360 ms\n",
        encoding="utf-16",
    )
    rows = read_log(path)
    assert [row["kind"] for row in rows] == ["market", "accepted", "deal", "done"]
    assert rows[0]["utc_ms"] == 1789734600589
    assert rows[2]["deal"] == 1637895850
    assert rows[2]["order"] == rows[3]["order"] == 2038395764
    assert rows[3]["done_ms"] == "67489.360"


def test_bind_preserves_clock_order_anomaly_and_ambiguous_stages():
    call = {"signal_id": "canal2_2908", "native_entry_deal": 15, "action_id": "a",
            "native_entry_volume": 0.04, "call_started_utc": "2026-09-18T12:00:00+00:00",
            "call_response_utc": "2026-09-18T12:00:03+00:00",
            "mt5_order_send_roundtrip_ms": "3000"}
    window = {"events": [{"ev": "mt5_order_result", "deal": 15, "action_id": "a", "order": 20}]}
    base = 1789732800000  # 2026-09-18 12:00:00 UTC.
    rows = [
        {"kind": "market", "utc_ms": base + 10, "direction": "BUY", "volume": "0.04", "line": 1,
         "local_clock": "14:00:00.010"},
        {"kind": "market", "utc_ms": base + 20, "direction": "BUY", "volume": "0.04", "line": 2,
         "local_clock": "14:00:00.020"},
        {"kind": "accepted", "utc_ms": base + 2500, "direction": "BUY", "volume": "0.04", "line": 3,
         "local_clock": "14:00:02.500"},
        {"kind": "deal", "utc_ms": base + 2000, "order": 20, "deal": 15, "line": 4,
         "local_clock": "14:00:02.000"},
        {"kind": "done", "utc_ms": base + 3050, "order": 20, "done_ms": "3001.250", "line": 5,
         "local_clock": "14:00:03.050"},
    ]
    result = bind_terminal(call, window, rows, "BUY", 20)
    assert result["status"] == "terminal_deal_and_done_bound"
    assert result["call_clock_order_consistent"] is False
    assert result["terminal_done_minus_python_response_ms"] == 50
    assert result["market_candidate_count"] == 2
    assert "market_to_accepted_ms" not in result


def test_bind_reports_negative_accepted_to_deal_without_reordering():
    call = {"signal_id": "canal2_2908", "native_entry_deal": 15, "action_id": "a",
            "native_entry_volume": 0.04, "call_started_utc": "2026-09-18T12:00:00+00:00",
            "call_response_utc": "2026-09-18T12:00:03+00:00",
            "mt5_order_send_roundtrip_ms": "3000"}
    window = {"events": [{"ev": "mt5_order_result", "deal": 15, "action_id": "a", "order": 20}]}
    base = 1789732800000
    rows = [
        {"kind": "market", "utc_ms": base + 10, "direction": "BUY", "volume": "0.04", "line": 1,
         "local_clock": "14:00:00.010"},
        {"kind": "accepted", "utc_ms": base + 2500, "direction": "BUY", "volume": "0.04", "line": 2,
         "local_clock": "14:00:02.500"},
        {"kind": "deal", "utc_ms": base + 2000, "order": 20, "deal": 15, "line": 3,
         "local_clock": "14:00:02.000"},
        {"kind": "done", "utc_ms": base + 2900, "order": 20, "done_ms": "2900", "line": 4,
         "local_clock": "14:00:02.900"},
    ]
    result = bind_terminal(call, window, rows, "BUY", 20)
    assert result["accepted_to_deal_ms"] == -500
    assert result["terminal_stage_order_consistent"] is False
    assert result["call_clock_order_consistent"] is True

    with pytest.raises(ValueError, match="conflicts with native deal"):
        bind_terminal(call, window, rows, "BUY", 21)


def test_deal_coverage_keeps_passive_and_unlinked_source_deals():
    native = {"deals": [{"ticket": 1, "order": 10, "entry": 0, "reason": 3},
                        {"ticket": 2, "order": 20, "entry": 1, "reason": 5},
                        {"ticket": 99, "order": 99, "entry": 0, "reason": 0}]}
    baskets = {"positions": [{"signal_id": "canal2_1", "deal_tickets": [1, 2]}]}
    logs = {"20260918": [{"kind": "deal", "deal": 1, "order": 10}]}
    report = deal_coverage(native, baskets, logs)
    assert report["basket_linked_native_deals"] == 2
    assert report["statuses"] == {"bound_exact_deal_order": 1, "absent_from_terminal_trades": 1}
    assert report["exceptions"] == [{"signal_id": "canal2_1", "native_deal": 2,
                                     "native_order": 20, "native_entry": 1, "native_reason": 5,
                                     "terminal_match_count": 0, "status": "absent_from_terminal_trades"}]
    assert report["unlinked_source_deals"] == 1
