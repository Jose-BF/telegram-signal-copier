import json
from datetime import date, timedelta

import pandas as pd

import observed_tick_replay_validator
from tools import ensure_replay_tick_cache


def _ticks(rows):
    df = pd.DataFrame(rows)
    df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)
    df["time_msc"] = df["time_utc"].astype("int64") // 1_000_000
    return df


def _write_tick_contract(cache_dir, day):
    day_end = day + timedelta(days=1)
    try:
        frame = pd.read_parquet(
            cache_dir / f"{day.isoformat()}.parquet"
        )
        row_count = len(frame)
        content_digest = ensure_replay_tick_cache.tick_content_sha256(frame)
    except Exception:
        row_count = 1
        content_digest = "a" * 64
    ensure_replay_tick_cache.write_day_contract(
        cache_dir,
        day,
        time_evidence={
            "source_time_basis": "mt5_server_epoch",
            "utc_offset_seconds": 10_800,
            "offset_detection_method": "fill_anchor",
            "offset_reference": {"signal_id": "canal1_1"},
        },
        semantic_validation={
            "valid": True,
            "anchors_checked": 1,
            "anchors_matched": 1,
            "max_time_delta_ms": 0,
            "max_price_delta": 0.0,
            "errors": [],
        },
        coverage={
            "source_query_start_utc": f"{day.isoformat()}T00:00:00+00:00",
            "source_query_end_utc": f"{day_end.isoformat()}T00:00:00+00:00",
            "captured_at_utc": f"{day_end.isoformat()}T00:01:00+00:00",
            "first_tick_utc": f"{day.isoformat()}T00:00:00+00:00",
            "last_tick_utc": f"{day_end.isoformat()}T00:00:00+00:00",
            "complete_from_utc": f"{day.isoformat()}T00:00:00+00:00",
            "complete_through_utc": f"{day_end.isoformat()}T00:00:00+00:00",
            "row_count": row_count,
        },
        source_verification={
            "verified": True,
            "method": "full_day_vs_two_half_days_v1",
            "content_digest": "time_bid_ask_sequence_sha256_v1",
            "symbol": "XAUUSD",
            "primary_row_count": row_count,
            "verification_row_count": row_count,
            "primary_content_sha256": content_digest,
            "verification_content_sha256": content_digest,
            "errors": [],
        },
        symbol="XAUUSD",
    )


def _set_partial_coverage(cache_dir, day, complete_through):
    contract_path = cache_dir / f"{day.isoformat()}.parquet.meta.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    day_start = f"{day.isoformat()}T00:00:00+00:00"
    contract["coverage"] = {
        "source_query_start_utc": day_start,
        "source_query_end_utc": "2026-07-07T00:00:00+00:00",
        "captured_at_utc": complete_through,
        "first_tick_utc": day_start,
        "last_tick_utc": complete_through,
        "complete_from_utc": day_start,
        "complete_through_utc": complete_through,
        "row_count": 1,
    }
    contract_path.write_text(json.dumps(contract), encoding="utf-8")


def _ticket(**overrides):
    base = {
        "ticket": 101,
        "role": "market_a",
        "open_dt_utc": "2026-07-06T10:00:00+00:00",
        "open_price": 4200.0,
        "close_dt_utc": "2026-07-06T10:01:30+00:00",
        "close_price": 4202.0,
        "close_reason": "tp",
        "is_closed": True,
        "sl_history": [
            {
                "ts": "2026-07-06T10:00:10+00:00",
                "status": "confirmed",
                "sl": 4195.0,
            }
        ],
        "tp_history": [
            {
                "ts": "2026-07-06T10:00:10+00:00",
                "status": "confirmed",
                "tp": 4202.0,
            }
        ],
    }
    base.update(overrides)
    return base


def _trade(**overrides):
    base = {
        "sig_id": "canal1_1",
        "channel": "canal1",
        "direction": "BUY",
        "open_dt_utc": "2026-07-06T10:00:00+00:00",
        "close_dt_utc": "2026-07-06T10:01:30+00:00",
        "tickets": [_ticket()],
    }
    base.update(overrides)
    return base


def test_buy_tp_replays_from_bid_ticks_after_tp_is_confirmed():
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:00:05+00:00", "bid": 4202.5, "ask": 4202.7},
        {"time_utc": "2026-07-06T10:00:20+00:00", "bid": 4201.5, "ask": 4201.7},
        {"time_utc": "2026-07-06T10:01:30+00:00", "bid": 4202.0, "ask": 4202.2},
    ])

    result = observed_tick_replay_validator.validate_ticket(
        _trade(), _ticket(), ticks)

    assert result["status"] == "exact"
    assert result["first_touch"]["reason"] == "tp"
    assert result["first_touch"]["time_utc"] == "2026-07-06T10:01:30+00:00"
    assert result["first_touch"]["side"] == "bid"


def test_matching_reason_with_early_touch_is_not_exact():
    ticket = _ticket(
        close_dt_utc="2026-07-06T10:20:00+00:00",
        close_price=4202.0,
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:01:00+00:00", "bid": 4202.0, "ask": 4202.2},
        {"time_utc": "2026-07-06T10:20:00+00:00", "bid": 4201.0, "ask": 4201.2},
    ])

    result = observed_tick_replay_validator.validate_ticket(
        _trade(close_dt_utc=ticket["close_dt_utc"]),
        ticket,
        ticks,
    )

    assert result["status"] == "mismatch"
    assert any(
        blocker.startswith("first_touch_time_mismatch:101:")
        for blocker in result["blockers"]
    )


def test_delayed_mt5_batch_close_is_eligible_without_claiming_exact_time():
    ticket = _ticket(
        close_dt_utc="2026-07-06T10:20:00+00:00",
        close_price=4202.0,
        close_deal=None,
        close_event={
            "ev": "positions_closed_by_mt5",
            "ts": "2026-07-06T10:20:00+00:00",
            "ticket": 101,
            "exit_price": 4202.0,
            "closed_by_tag": "TP1",
        },
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:01:00+00:00", "bid": 4202.0, "ask": 4202.2},
        {"time_utc": "2026-07-06T10:20:00+00:00", "bid": 4201.0, "ask": 4201.2},
    ])

    result = observed_tick_replay_validator.validate_ticket(
        _trade(close_dt_utc=ticket["close_dt_utc"]),
        ticket,
        ticks,
    )

    assert result["status"] == "delayed_close_observation"
    assert result["blockers"] == []
    assert result["first_touch"]["time_utc"] == (
        "2026-07-06T10:01:00+00:00"
    )
    assert result["observed_close_utc"] == (
        "2026-07-06T10:20:00+00:00"
    )
    assert result["limitations"] == [
        "per_ticket_close_time_unavailable:101",
    ]


def test_delayed_batch_stop_accepts_mt5_level_fill_with_tick_gap():
    ticket = _ticket(
        close_dt_utc="2026-07-06T10:20:00+00:00",
        close_price=4195.0,
        close_reason="sl",
        close_deal=None,
        close_event={
            "ev": "positions_closed_by_mt5",
            "ts": "2026-07-06T10:20:00+00:00",
            "ticket": 101,
            "exit_price": 4195.0,
            "closed_by_tag": "SL",
        },
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:01:00+00:00", "bid": 4194.7, "ask": 4194.9},
        {"time_utc": "2026-07-06T10:20:00+00:00", "bid": 4201.0, "ask": 4201.2},
    ])

    result = observed_tick_replay_validator.validate_ticket(
        _trade(close_dt_utc=ticket["close_dt_utc"]),
        ticket,
        ticks,
    )

    assert result["status"] == "delayed_close_observation"
    assert result["blockers"] == []
    assert any(
        warning.startswith("observed_level_fill_delta:101:")
        for warning in result["warnings"]
    )


def test_delayed_batch_stop_accepts_late_level_ack_only_when_touch_is_unchanged():
    ticket = _ticket(
        open_price=4122.66,
        close_dt_utc="2026-07-06T10:20:00+00:00",
        close_price=4131.5,
        close_reason="sl",
        close_deal=None,
        close_event={
            "ev": "positions_closed_by_mt5",
            "ts": "2026-07-06T10:20:00+00:00",
            "ticket": 101,
            "exit_price": 4131.5,
            "closed_by_tag": "SL",
        },
        sl_history=[
            {
                "ts": "2026-07-06T10:00:05+00:00",
                "status": "confirmed",
                "sl": 4131.67,
            },
            {
                "ts": "2026-07-06T10:00:10+00:00",
                "status": "requested",
                "sl": 4131.5,
            },
            {
                "ts": "2026-07-06T10:19:00+00:00",
                "status": "confirmed",
                "sl": 4131.5,
            },
        ],
        tp_history=[
            {
                "ts": "2026-07-06T10:00:05+00:00",
                "status": "confirmed",
                "tp": 4100.0,
            },
        ],
    )
    trade = _trade(
        direction="SELL",
        close_dt_utc=ticket["close_dt_utc"],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4122.5, "ask": 4122.7},
        {"time_utc": "2026-07-06T10:01:00+00:00", "bid": 4131.6, "ask": 4131.79},
        {"time_utc": "2026-07-06T10:20:00+00:00", "bid": 4156.1, "ask": 4156.3},
    ])

    result = observed_tick_replay_validator.validate_ticket(
        trade,
        ticket,
        ticks,
    )

    assert result["status"] == "delayed_close_observation"
    assert result["blockers"] == []
    assert result["first_touch"]["level"] == 4131.5
    assert result["first_touch"]["time_utc"] == "2026-07-06T10:01:00+00:00"
    assert result["limitations"] == [
        "per_ticket_close_time_unavailable:101",
        "level_acknowledgement_delayed:101",
    ]


def test_broker_confirmed_stop_execution_lag_is_preserved_as_a_limitation():
    ticket = _ticket(
        open_price=4377.48,
        close_dt_utc="2026-07-06T10:00:07+00:00",
        close_price=4377.48,
        close_reason="sl",
        close_deal={
            "position_id": 101,
            "reason": 4,
            "price": 4377.48,
            "comment": "[sl 4377.48]",
        },
        close_event=None,
        sl_history=[{
            "ts": "2026-07-06T09:59:50+00:00",
            "status": "confirmed",
            "sl": 4377.48,
        }],
        tp_history=[{
            "ts": "2026-07-06T09:59:50+00:00",
            "status": "confirmed",
            "tp": 4368.0,
        }],
    )
    trade = _trade(
        direction="SELL",
        open_dt_utc="2026-07-06T09:59:50+00:00",
        close_dt_utc=ticket["close_dt_utc"],
    )
    ticks = _ticks([
        {
            "time_utc": "2026-07-06T09:59:50+00:00",
            "bid": 4377.20,
            "ask": 4377.30,
        },
        {
            "time_utc": "2026-07-06T10:00:01+00:00",
            "bid": 4377.80,
            "ask": 4377.99,
        },
        {
            "time_utc": "2026-07-06T10:00:07+00:00",
            "bid": 4377.10,
            "ask": 4377.21,
        },
    ])

    result = observed_tick_replay_validator.validate_ticket(
        trade,
        ticket,
        ticks,
    )

    assert result["status"] == "delayed_close_observation"
    assert result["blockers"] == []
    assert result["first_touch"]["time_utc"] == "2026-07-06T10:00:01+00:00"
    assert result["observed_close_utc"] == "2026-07-06T10:00:07+00:00"
    assert result["limitations"] == [
        "broker_stop_execution_delay_observed:101:+6.000s"
    ]


def test_delayed_batch_stop_rejects_late_level_ack_when_touch_time_changes():
    ticket = _ticket(
        open_price=4122.66,
        close_dt_utc="2026-07-06T10:20:00+00:00",
        close_price=4131.5,
        close_reason="sl",
        close_deal=None,
        close_event={
            "ev": "positions_closed_by_mt5",
            "ts": "2026-07-06T10:20:00+00:00",
            "ticket": 101,
            "exit_price": 4131.5,
            "closed_by_tag": "SL",
        },
        sl_history=[
            {
                "ts": "2026-07-06T10:00:05+00:00",
                "status": "confirmed",
                "sl": 4131.67,
            },
            {
                "ts": "2026-07-06T10:00:10+00:00",
                "status": "requested",
                "sl": 4131.5,
            },
            {
                "ts": "2026-07-06T10:19:00+00:00",
                "status": "confirmed",
                "sl": 4131.5,
            },
        ],
        tp_history=[
            {
                "ts": "2026-07-06T10:00:05+00:00",
                "status": "confirmed",
                "tp": 4100.0,
            },
        ],
    )
    trade = _trade(
        direction="SELL",
        close_dt_utc=ticket["close_dt_utc"],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4122.5, "ask": 4122.7},
        {"time_utc": "2026-07-06T10:00:30+00:00", "bid": 4131.4, "ask": 4131.55},
        {"time_utc": "2026-07-06T10:01:00+00:00", "bid": 4131.6, "ask": 4131.79},
        {"time_utc": "2026-07-06T10:20:00+00:00", "bid": 4156.1, "ask": 4156.3},
    ])

    result = observed_tick_replay_validator.validate_ticket(
        trade,
        ticket,
        ticks,
    )

    assert result["status"] == "mismatch"
    assert any(
        blocker.startswith("first_touch_time_mismatch:101:")
        for blocker in result["blockers"]
    )


def test_matching_path_with_fill_delta_is_exact_and_records_slippage():
    ticket = _ticket(
        close_dt_utc="2026-07-06T10:01:30+00:00",
        close_price=4202.3,
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:01:30+00:00", "bid": 4202.4, "ask": 4202.6},
    ])

    result = observed_tick_replay_validator.validate_ticket(
        _trade(close_dt_utc=ticket["close_dt_utc"]),
        ticket,
        ticks,
    )

    assert result["status"] == "exact"
    assert any(
        warning.startswith("observed_level_fill_delta:101:")
        for warning in result["warnings"]
    )
    assert result["blockers"] == []


def test_quote_fill_delta_is_execution_evidence_not_path_failure():
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4300.0, "ask": 4300.2},
        {"time_utc": "2026-07-06T10:01:30+00:00", "bid": 4310.0, "ask": 4310.2},
    ])

    result = observed_tick_replay_validator.validate_ticket(
        _trade(), _ticket(), ticks)

    assert result["status"] == "exact"
    assert result["alignment"]["open"]["time_delta_ms"] == 0
    assert result["alignment"]["open"]["price_delta"] == 100.2
    assert any(
        warning.startswith("observed_open_execution_delta:101:")
        for warning in result["warnings"]
    )
    assert not any(
        blocker.startswith("open_tick_price_mismatch:101:")
        for blocker in result["blockers"]
    )


def test_unverified_open_tick_alignment_blocks_exact_baseline():
    ticks = _ticks([{
        "time_utc": "2026-07-06T10:01:30+00:00",
        "bid": 4202.0,
        "ask": 4202.2,
    }])

    result = observed_tick_replay_validator.validate_ticket(
        _trade(), _ticket(), ticks)

    assert result["status"] == "blocked"
    assert "open_tick_alignment_unverified:101" in result["blockers"]


def test_open_quote_delta_does_not_invalidate_verified_path():
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4200.0, "ask": 4200.2},
        {"time_utc": "2026-07-06T10:01:30+00:00", "bid": 4202.0, "ask": 4202.2},
    ])

    result = observed_tick_replay_validator.validate_ticket(
        _trade(), _ticket(), ticks)

    assert result["status"] == "exact"
    assert any(
        warning.startswith("observed_open_execution_delta:101:+0.20")
        for warning in result["warnings"]
    )


def test_sell_sl_replays_from_ask_ticks():
    trade = _trade(direction="SELL")
    ticket = _ticket(
        open_price=4200.0,
        close_price=4205.0,
        close_reason="sl",
        sl_history=[{
            "ts": "2026-07-06T10:00:00+00:00",
            "status": "confirmed",
            "sl": 4205.0,
        }],
        tp_history=[{
            "ts": "2026-07-06T10:00:00+00:00",
            "status": "confirmed",
            "tp": 4190.0,
        }],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4200.0, "ask": 4200.2},
        {"time_utc": "2026-07-06T10:01:00+00:00", "bid": 4204.7, "ask": 4204.9},
        {"time_utc": "2026-07-06T10:01:30+00:00", "bid": 4204.8, "ask": 4205.0},
    ])

    result = observed_tick_replay_validator.validate_ticket(trade, ticket, ticks)

    assert result["status"] == "exact"
    assert result["first_touch"]["reason"] == "sl"
    assert result["first_touch"]["side"] == "ask"


def test_level_touch_before_confirmation_does_not_count():
    ticket = _ticket(
        close_dt_utc="2026-07-06T10:01:00+00:00",
        tp_history=[{
            "ts": "2026-07-06T10:00:30+00:00",
            "status": "confirmed",
            "tp": 4202.0,
        }],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:00:10+00:00", "bid": 4202.5, "ask": 4202.7},
        {"time_utc": "2026-07-06T10:00:40+00:00", "bid": 4201.5, "ask": 4201.7},
    ])

    result = observed_tick_replay_validator.validate_ticket(_trade(), ticket, ticks)

    assert result["status"] == "mismatch"
    assert "no_level_touch_before_close" in result["blockers"]


def test_zero_level_is_ignored_as_missing_price():
    ticket = _ticket(
        close_reason="sl",
        close_price=4195.0,
        tp_history=[{
            "ts": "2026-07-06T10:00:10+00:00",
            "status": "confirmed",
            "tp": 0.0,
        }],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:00:20+00:00", "bid": 4201.5, "ask": 4201.7},
        {"time_utc": "2026-07-06T10:01:30+00:00", "bid": 4195.0, "ask": 4195.2},
    ])

    result = observed_tick_replay_validator.validate_ticket(_trade(), ticket, ticks)

    assert result["status"] == "exact"
    assert result["first_touch"]["reason"] == "sl"


def test_mt5_sl_comment_without_recorded_level_change_names_missing_evidence():
    trade = _trade(direction="SELL")
    ticket = _ticket(
        open_price=4200.0,
        close_price=4200.2,
        close_reason="sl",
        sl_history=[{
            "ts": "2026-07-06T10:00:00+00:00",
            "status": "confirmed",
            "sl": 4210.0,
        }],
        tp_history=[{
            "ts": "2026-07-06T10:00:00+00:00",
            "status": "confirmed",
            "tp": 4190.0,
        }],
        close_deal={"comment": "[sl 4200.20]"},
    )
    ticks = _ticks([
        {
            "time_utc": "2026-07-06T10:00:00+00:00",
            "bid": 4200.0,
            "ask": 4200.2,
        },
        {
            "time_utc": "2026-07-06T10:01:30+00:00",
            "bid": 4200.0,
            "ask": 4200.2,
        },
    ])

    result = observed_tick_replay_validator.validate_ticket(
        trade,
        ticket,
        ticks,
    )

    assert result["status"] == "mismatch"
    assert result["blockers"] == ["missing_sl_transition_evidence:101"]


def test_be_request_four_ms_after_broker_close_is_causal_race_not_mismatch():
    trade = _trade(direction="SELL", mt5_time_offset_s=10_800)
    raw_broker_close_msc = int(
        pd.Timestamp("2026-07-06T13:01:30.887+00:00").timestamp() * 1000
    )
    ticket = _ticket(
        open_price=4200.2,
        close_dt_utc="2026-07-06T10:01:30.887+00:00",
        close_price=4200.2,
        close_reason="sl",
        sl_history=[
            {
                "ts": "2026-07-06T10:00:00+00:00",
                "status": "confirmed",
                "sl": 4210.0,
            },
            {
                "ts": "2026-07-06T10:01:30.891+00:00",
                "status": "requested",
                "sl": 4200.2,
            },
        ],
        tp_history=[{
            "ts": "2026-07-06T10:00:00+00:00",
            "status": "confirmed",
            "tp": 4190.0,
        }],
        close_deal={
            "comment": "[sl 4200.20]",
            "time_msc": raw_broker_close_msc,
        },
    )
    ticks = _ticks([
        {
            "time_utc": "2026-07-06T10:00:00.000+00:00",
            "bid": 4200.0,
            "ask": 4200.2,
        },
        {
            "time_utc": "2026-07-06T10:01:30.887+00:00",
            "bid": 4200.0,
            "ask": 4200.2,
        },
    ])

    result = observed_tick_replay_validator.validate_ticket(
        trade,
        ticket,
        ticks,
    )

    assert result["status"] == "exact"
    assert result["first_touch"]["reason"] == "be"
    assert result["blockers"] == []
    assert any(
        item.startswith("causal_ordering_tolerance_applied:101:")
        for item in result["limitations"]
    )

def test_unlogged_sl_during_runtime_gap_is_external_not_causal_mismatch():
    trade = _trade(
        direction="SELL",
        operational_context={
            "runtime_discontinuities": [{
                "kind": "session_restart_overlap",
                "unobserved_from_utc": "2026-07-06T10:00:30+00:00",
                "restart_observed_utc": "2026-07-06T10:01:25+00:00",
                "observability_restored_utc": "2026-07-06T10:01:40+00:00",
            }],
        },
    )
    ticket = _ticket(
        open_price=4200.0,
        close_dt_utc="2026-07-06T10:01:20+00:00",
        close_price=4200.2,
        close_reason="sl",
        sl_history=[{
            "ts": "2026-07-06T10:00:00+00:00",
            "status": "confirmed",
            "sl": 4210.0,
        }],
        tp_history=[{
            "ts": "2026-07-06T10:00:00+00:00",
            "status": "confirmed",
            "tp": 4190.0,
        }],
        close_deal={"comment": "[sl 4200.20]"},
    )
    ticks = _ticks([
        {
            "time_utc": "2026-07-06T10:00:00+00:00",
            "bid": 4200.0,
            "ask": 4200.2,
        },
        {
            "time_utc": "2026-07-06T10:01:20+00:00",
            "bid": 4200.0,
            "ask": 4200.2,
        },
    ])

    ticket_result = observed_tick_replay_validator.validate_ticket(
        trade,
        ticket,
        ticks,
    )

    assert ticket_result["status"] == "external_intervention"
    assert ticket_result["blockers"] == []
    assert ticket_result["limitations"] == [
        "unobserved_sl_transition_during_runtime_gap:101"
    ]



def test_unattributed_observed_sl_window_remains_non_exact_but_identified():
    trade = _trade(direction="SELL")
    ticket = _ticket(
        open_price=4200.0,
        close_price=4200.2,
        close_reason="sl",
        sl_history=[
            {
                "ts": "2026-07-06T10:00:00+00:00",
                "status": "confirmed",
                "sl": 4210.0,
            },
            {
                "ts": "2026-07-06T10:01:25+00:00",
                "status": "observed_unattributed",
                "observed_interval_start_utc": (
                    "2026-07-06T10:01:20+00:00"
                ),
                "observed_interval_end_utc": (
                    "2026-07-06T10:01:25+00:00"
                ),
                "sl": 4200.2,
            },
        ],
        tp_history=[{
            "ts": "2026-07-06T10:00:00+00:00",
            "status": "confirmed",
            "tp": 4190.0,
        }],
        close_deal={"comment": "[sl 4200.20]"},
    )
    ticks = _ticks([
        {
            "time_utc": "2026-07-06T10:00:00+00:00",
            "bid": 4200.0,
            "ask": 4200.2,
        },
        {
            "time_utc": "2026-07-06T10:01:30+00:00",
            "bid": 4200.0,
            "ask": 4200.2,
        },
    ])

    result = observed_tick_replay_validator.validate_ticket(
        trade,
        ticket,
        ticks,
    )

    assert result["status"] == "mismatch"
    assert result["blockers"] == [
        "unattributed_sl_transition_window:101"
    ]


def test_bot_close_replays_as_market_close_near_close_time():
    ticket = _ticket(
        close_reason="bot_close",
        close_dt_utc="2026-07-06T10:02:00+00:00",
        close_price=4201.8,
        sl_history=[],
        tp_history=[],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:01:57+00:00", "bid": 4201.2, "ask": 4201.4},
        {"time_utc": "2026-07-06T10:02:01+00:00", "bid": 4201.8, "ask": 4202.0},
    ])

    result = observed_tick_replay_validator.validate_ticket(_trade(), ticket, ticks)

    assert result["status"] == "exact"
    assert result["first_touch"]["reason"] == "bot_close"
    assert result["first_touch"]["side"] == "bid"


def test_bot_close_quote_delta_is_recorded_as_execution_slippage():
    ticket = _ticket(
        close_reason="bot_close",
        close_dt_utc="2026-07-06T10:02:00+00:00",
        close_price=4201.8,
        sl_history=[],
        tp_history=[],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:02:00+00:00", "bid": 4202.0, "ask": 4202.2},
    ])

    result = observed_tick_replay_validator.validate_ticket(
        _trade(), ticket, ticks)

    assert result["status"] == "exact"
    assert any(
        warning.startswith("observed_close_execution_delta:101:+0.20")
        for warning in result["warnings"]
    )


def test_mt5_other_reason_replays_as_external_market_close():
    ticket = _ticket(
        close_reason="other",
        close_dt_utc="2026-07-06T10:02:00+00:00",
        close_price=4201.8,
        sl_history=[],
        tp_history=[],
    )
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:02:00+00:00", "bid": 4202.0, "ask": 4202.2},
    ])

    result = observed_tick_replay_validator.validate_ticket(
        _trade(), ticket, ticks)

    assert result["status"] == "exact"
    assert result["first_touch"]["reason"] == "external_close"
    assert result["first_touch"]["side"] == "bid"
    assert any(
        warning.startswith("observed_close_execution_delta:101:+0.20")
        for warning in result["warnings"]
    )


def test_trade_blocks_when_tick_cache_is_missing(tmp_path):
    result = observed_tick_replay_validator.validate_trade(
        _trade(),
        tick_cache_dir=tmp_path / "ticks_cache",
    )

    assert result["status"] == "blocked"
    assert "missing_tick_cache:2026-07-06" in result["blockers"]


def test_trade_preserves_delayed_close_observation_status(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:01:00+00:00", "bid": 4202.0, "ask": 4202.2},
        {"time_utc": "2026-07-06T10:20:00+00:00", "bid": 4201.0, "ask": 4201.2},
    ]).to_parquet(cache_dir / "2026-07-06.parquet", index=False)
    _write_tick_contract(cache_dir, date(2026, 7, 6))
    ticket = _ticket(
        close_dt_utc="2026-07-06T10:20:00+00:00",
        close_price=4202.0,
        close_deal=None,
        close_event={
            "ev": "positions_closed_by_mt5",
            "ts": "2026-07-06T10:20:00+00:00",
            "ticket": 101,
            "exit_price": 4202.0,
            "closed_by_tag": "TP1",
        },
    )
    trade = _trade(
        close_dt_utc=ticket["close_dt_utc"],
        tickets=[ticket],
    )

    result = observed_tick_replay_validator.validate_trade(
        trade,
        tick_cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert result["status"] == "delayed_close_observation"
    assert result["delayed_close_observation_tickets"] == 1
    assert result["blockers"] == []


def test_trade_blocks_when_tick_cache_has_no_verified_utc_contract(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    _ticks([{
        "time_utc": "2026-07-06T10:01:30+00:00",
        "bid": 4202.0,
        "ask": 4202.2,
    }]).to_parquet(cache_dir / "2026-07-06.parquet", index=False)

    result = observed_tick_replay_validator.validate_trade(
        _trade(),
        tick_cache_dir=cache_dir,
    )

    assert result["status"] == "blocked"
    assert "invalid_tick_cache_contract:2026-07-06" in result["blockers"]


def test_trade_blocks_when_verified_cache_ends_before_trade_close(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    day = date(2026, 7, 6)
    _ticks([{
        "time_utc": "2026-07-06T10:30:00+00:00",
        "bid": 4200.0,
        "ask": 4200.2,
    }]).to_parquet(cache_dir / "2026-07-06.parquet", index=False)
    _write_tick_contract(cache_dir, day)
    _set_partial_coverage(cache_dir, day, "2026-07-06T10:30:00+00:00")
    trade = _trade(close_dt_utc="2026-07-06T12:00:00+00:00")

    result = observed_tick_replay_validator.validate_trade(
        trade,
        tick_cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert result["status"] == "blocked"
    assert "incomplete_tick_cache_coverage:2026-07-06" in result["blockers"]


def test_tick_loader_exposes_verified_contracts_and_required_days(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    parquet = cache_dir / "2026-07-06.parquet"
    _ticks([{
        "time_utc": "2026-07-06T10:00:00+00:00",
        "bid": 4199.8,
        "ask": 4200.0,
    }]).to_parquet(parquet, index=False)
    _write_tick_contract(cache_dir, date(2026, 7, 6))
    loader = observed_tick_replay_validator.ReplayTickFrameCache(cache_dir)

    loader.load_ticks_for_trade(_trade(sig_id="canal1_1"))
    loader.load_ticks_for_trade(_trade(sig_id="canal1_2"))

    assert loader.required_days == ["2026-07-06"]
    contract = loader.verified_contracts["2026-07-06"]
    assert contract["day"] == "2026-07-06"
    assert contract["tick_time_contract"] == "mt5_server_epoch_utc_v3"
    assert contract["time_basis"] == "UTC"
    assert contract["source_time_basis"] == "mt5_server_epoch"
    assert contract["utc_offset_seconds"] == 10_800
    assert contract["semantic_time_valid"] is True
    assert contract["symbol"] == "XAUUSD"
    assert contract["parquet_sha256"] == ensure_replay_tick_cache._file_sha256(parquet)
    assert contract["contract_sha256"] == ensure_replay_tick_cache._file_sha256(
        cache_dir / "2026-07-06.parquet.meta.json"
    )
    assert contract["size_bytes"] == parquet.stat().st_size


def test_tick_loader_filters_quote_only_ticks_using_verified_server_offset(
    tmp_path,
):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    day = date(2026, 7, 12)
    _ticks([
        {
            "time_utc": "2026-07-12T22:00:00+00:00",
            "bid": 4096.0,
            "ask": 4096.2,
        },
        {
            "time_utc": "2026-07-12T22:01:00+00:00",
            "bid": 4097.0,
            "ask": 4097.2,
        },
    ]).to_parquet(cache_dir / "2026-07-12.parquet", index=False)
    _write_tick_contract(cache_dir, day)
    loader = observed_tick_replay_validator.ReplayTickFrameCache(cache_dir)

    frame, error = loader._load_day("2026-07-12")

    assert error is None
    assert frame["time_utc"].dt.strftime("%H:%M:%S").tolist() == ["22:01:00"]
    assert frame.attrs["market_session_contract"] == (
        "vantage_xauusd_standard_v1"
    )
    assert frame.attrs["quote_only_ticks_removed"] == 1


def test_cli_writes_observed_tick_replay_audit_and_status(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:00:20+00:00", "bid": 4201.0, "ask": 4201.2},
        {"time_utc": "2026-07-06T10:01:30+00:00", "bid": 4202.0, "ask": 4202.2},
    ]).to_parquet(cache_dir / "2026-07-06.parquet", index=False)
    _write_tick_contract(cache_dir, date(2026, 7, 6))
    replay_path = tmp_path / "replay_trades.jsonl"
    output_path = tmp_path / "observed_tick_replay_audit.jsonl"
    status_path = tmp_path / "observed_tick_replay_status.json"
    replay_path.write_text(json.dumps(_trade()) + "\n", encoding="utf-8")

    exit_code = observed_tick_replay_validator.main([
        "--input",
        str(replay_path),
        "--tick-cache-dir",
        str(cache_dir),
        "--output",
        str(output_path),
        "--status",
        str(status_path),
        "--quiet",
    ])

    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert rows[0]["status"] == "exact"
    assert rows[0]["schema_version"] == 2
    assert rows[0]["validation_contract"] == "causal_path_v3"
    assert rows[0]["tick_contract_evidence"]["2026-07-06"] == {
        "symbol": "XAUUSD",
        "parquet_sha256": ensure_replay_tick_cache._file_sha256(
            cache_dir / "2026-07-06.parquet"
        ),
        "contract_sha256": ensure_replay_tick_cache._file_sha256(
            cache_dir / "2026-07-06.parquet.meta.json"
        ),
    }
    assert rows[0]["market_session_contract"] == (
        "vantage_xauusd_standard_v1"
    )
    assert status["summary"]["exact"] == 1
    assert status["schema_version"] == 2
    assert status["validation_contract"] == "causal_path_v3"
    assert status["market_session_contract"] == (
        "vantage_xauusd_standard_v1"
    )


def test_cli_scopes_observed_replay_from_selected_date(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:01:30+00:00", "bid": 4202.0, "ask": 4202.2},
    ]).to_parquet(cache_dir / "2026-07-06.parquet", index=False)
    _write_tick_contract(cache_dir, date(2026, 7, 6))
    replay_path = tmp_path / "replay_trades.jsonl"
    output_path = tmp_path / "observed_tick_replay_audit.jsonl"
    status_path = tmp_path / "observed_tick_replay_status.json"
    old = _trade(
        sig_id="canal1_old",
        open_dt_utc="2026-07-05T10:00:00+00:00",
        close_dt_utc="2026-07-05T10:01:30+00:00",
    )
    replay_path.write_text(
        json.dumps(old) + "\n" + json.dumps(_trade()) + "\n",
        encoding="utf-8",
    )

    exit_code = observed_tick_replay_validator.main([
        "--input", str(replay_path),
        "--tick-cache-dir", str(cache_dir),
        "--output", str(output_path),
        "--status", str(status_path),
        "--since", "2026-07-06",
        "--quiet",
    ])

    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert [row["sig_id"] for row in rows] == ["canal1_1"]
    assert status["scope"] == {
        "since": "2026-07-06",
        "until": None,
        "input_trades": 2,
        "selected_trades": 1,
    }


def test_cli_reuses_cached_tick_day_across_trades(tmp_path, monkeypatch):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    (cache_dir / "2026-07-06.parquet").touch()
    _write_tick_contract(cache_dir, date(2026, 7, 6))
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4199.8, "ask": 4200.0},
        {"time_utc": "2026-07-06T10:00:20+00:00", "bid": 4201.0, "ask": 4201.2},
        {"time_utc": "2026-07-06T10:01:30+00:00", "bid": 4202.0, "ask": 4202.2},
    ])
    calls = []

    def fake_read_parquet(path):
        calls.append(path)
        return ticks.copy()

    monkeypatch.setattr(
        observed_tick_replay_validator.pd,
        "read_parquet",
        fake_read_parquet,
    )
    replay_path = tmp_path / "replay_trades.jsonl"
    output_path = tmp_path / "observed_tick_replay_audit.jsonl"
    status_path = tmp_path / "observed_tick_replay_status.json"
    trades = [
        _trade(sig_id="canal1_1"),
        _trade(sig_id="canal1_2"),
    ]
    replay_path.write_text(
        "\n".join(json.dumps(trade) for trade in trades) + "\n",
        encoding="utf-8",
    )

    exit_code = observed_tick_replay_validator.main([
        "--input",
        str(replay_path),
        "--tick-cache-dir",
        str(cache_dir),
        "--output",
        str(output_path),
        "--status",
        str(status_path),
        "--quiet",
    ])

    assert exit_code == 0
    assert len(calls) == 1
