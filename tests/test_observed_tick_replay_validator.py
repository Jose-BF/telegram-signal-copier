import json
from datetime import date

import pandas as pd

import observed_tick_replay_validator
from tools import ensure_replay_tick_cache


def _ticks(rows):
    df = pd.DataFrame(rows)
    df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)
    df["time_msc"] = df["time_utc"].astype("int64") // 1_000_000
    return df


def _write_tick_contract(cache_dir, day):
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
    )


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


def test_matching_reason_and_time_with_different_fill_price_is_not_exact():
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

    assert result["status"] == "mismatch"
    assert any(
        blocker.startswith("first_touch_price_mismatch:101:")
        for blocker in result["blockers"]
    )


def test_shifted_tick_cache_is_rejected_by_open_price_alignment():
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4300.0, "ask": 4300.2},
        {"time_utc": "2026-07-06T10:01:30+00:00", "bid": 4310.0, "ask": 4310.2},
    ])

    result = observed_tick_replay_validator.validate_ticket(
        _trade(), _ticket(), ticks)

    assert result["status"] == "blocked"
    assert result["alignment"]["open"]["time_delta_ms"] == 0
    assert result["alignment"]["open"]["price_delta"] == 100.2
    assert any(
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


def test_open_quote_must_match_mt5_fill_to_the_cent():
    ticks = _ticks([
        {"time_utc": "2026-07-06T10:00:00+00:00", "bid": 4200.0, "ask": 4200.2},
        {"time_utc": "2026-07-06T10:01:30+00:00", "bid": 4202.0, "ask": 4202.2},
    ])

    result = observed_tick_replay_validator.validate_ticket(
        _trade(), _ticket(), ticks)

    assert result["status"] == "blocked"
    assert any(
        blocker.startswith("open_tick_price_mismatch:101:+0.20")
        for blocker in result["blockers"]
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


def test_bot_close_quote_must_match_mt5_fill_to_the_cent():
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

    assert result["status"] == "mismatch"
    assert any(
        blocker.startswith("bot_close_price_mismatch:101:+0.20")
        for blocker in result["blockers"]
    )


def test_trade_blocks_when_tick_cache_is_missing(tmp_path):
    result = observed_tick_replay_validator.validate_trade(
        _trade(),
        tick_cache_dir=tmp_path / "ticks_cache",
    )

    assert result["status"] == "blocked"
    assert "missing_tick_cache:2026-07-06" in result["blockers"]


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
    assert contract["parquet_sha256"] == ensure_replay_tick_cache._file_sha256(parquet)
    assert contract["size_bytes"] == parquet.stat().st_size


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
    assert status["summary"]["exact"] == 1


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
