import json

import pandas as pd

import observed_tick_replay_validator


def _ticks(rows):
    df = pd.DataFrame(rows)
    df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)
    df["time_msc"] = df["time_utc"].astype("int64") // 1_000_000
    return df


def _ticket(**overrides):
    base = {
        "ticket": 101,
        "role": "market_a",
        "open_dt_utc": "2026-07-06T10:00:00+00:00",
        "open_price": 4200.0,
        "close_dt_utc": "2026-07-06T10:02:00+00:00",
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
        "close_dt_utc": "2026-07-06T10:02:00+00:00",
        "tickets": [_ticket()],
    }
    base.update(overrides)
    return base


def test_buy_tp_replays_from_bid_ticks_after_tp_is_confirmed():
    ticks = _ticks([
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
        {"time_utc": "2026-07-06T10:00:10+00:00", "bid": 4202.5, "ask": 4202.7},
        {"time_utc": "2026-07-06T10:00:40+00:00", "bid": 4201.5, "ask": 4201.7},
    ])

    result = observed_tick_replay_validator.validate_ticket(_trade(), ticket, ticks)

    assert result["status"] == "mismatch"
    assert "no_level_touch_before_close" in result["blockers"]


def test_trade_blocks_when_tick_cache_is_missing(tmp_path):
    result = observed_tick_replay_validator.validate_trade(
        _trade(),
        tick_cache_dir=tmp_path / "ticks_cache",
    )

    assert result["status"] == "blocked"
    assert "missing_tick_cache:2026-07-06" in result["blockers"]


def test_cli_writes_observed_tick_replay_audit_and_status(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    _ticks([
        {"time_utc": "2026-07-06T10:00:20+00:00", "bid": 4201.0, "ask": 4201.2},
        {"time_utc": "2026-07-06T10:01:30+00:00", "bid": 4202.0, "ask": 4202.2},
    ]).to_parquet(cache_dir / "2026-07-06.parquet", index=False)
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
