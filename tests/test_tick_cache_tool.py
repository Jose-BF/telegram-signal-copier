import json
from datetime import date

from tools import cache_replay_ticks


def _trade(sig_id, open_dt, close_dt):
    return {
        "sig_id": sig_id,
        "open_dt_utc": open_dt,
        "close_dt_utc": close_dt,
    }


def test_required_dates_include_padded_trade_windows():
    trades = [
        _trade(
            "canal1_1",
            "2026-07-06T23:59:30+00:00",
            "2026-07-07T00:02:00+00:00",
        ),
        _trade(
            "canal2_2",
            "2026-07-08T10:00:00+00:00",
            "2026-07-08T10:15:00+00:00",
        ),
    ]

    days = cache_replay_ticks.required_dates(trades, pad_minutes=2)

    assert days == [
        date(2026, 7, 6),
        date(2026, 7, 7),
        date(2026, 7, 8),
    ]


def test_cache_status_marks_missing_and_cached_days(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    (cache_dir / "2026-07-06.parquet").write_bytes(b"cached")

    status = cache_replay_ticks.build_status(
        [_trade("canal1_1", "2026-07-06T10:00:00+00:00",
                "2026-07-07T10:00:00+00:00")],
        cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert status["ok"] is False
    assert status["required_days"] == ["2026-07-06", "2026-07-07"]
    assert status["cached_days"] == ["2026-07-06"]
    assert status["missing_days"] == ["2026-07-07"]


def test_dry_run_cli_writes_tick_cache_status(tmp_path):
    replay_path = tmp_path / "replay_trades.jsonl"
    status_path = tmp_path / "tick_cache_status.json"
    cache_dir = tmp_path / "ticks_cache"
    replay_path.write_text(
        json.dumps(_trade(
            "canal1_1",
            "2026-07-06T10:00:00+00:00",
            "2026-07-06T10:05:00+00:00",
        )) + "\n",
        encoding="utf-8",
    )

    exit_code = cache_replay_ticks.main([
        "--input",
        str(replay_path),
        "--status",
        str(status_path),
        "--cache-dir",
        str(cache_dir),
        "--dry-run",
        "--quiet",
    ])

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert status["dry_run"] is True
    assert status["missing_days"] == ["2026-07-06"]
