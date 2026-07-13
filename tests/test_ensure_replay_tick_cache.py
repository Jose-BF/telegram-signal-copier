import json
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace

from tools import ensure_replay_tick_cache


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

    days = ensure_replay_tick_cache.required_dates(trades, pad_minutes=2)

    assert days == [
        date(2026, 7, 6),
        date(2026, 7, 7),
        date(2026, 7, 8),
    ]


def test_cache_status_marks_missing_and_cached_days(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    (cache_dir / "2026-07-06.parquet").write_bytes(b"cached")
    ensure_replay_tick_cache.write_day_contract(
        cache_dir, date(2026, 7, 6))

    status = ensure_replay_tick_cache.build_status(
        [_trade("canal1_1", "2026-07-06T10:00:00+00:00",
                "2026-07-07T10:00:00+00:00")],
        cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert status["ok"] is False
    assert status["required_days"] == ["2026-07-06", "2026-07-07"]
    assert status["cached_days"] == ["2026-07-06"]
    assert status["missing_days"] == ["2026-07-07"]
    assert status["tick_time_contract"] == "mt5_utc_v2"


def test_unversioned_or_tampered_cache_day_is_invalid(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    day = date(2026, 7, 8)
    day_file = cache_dir / "2026-07-08.parquet"
    day_file.write_bytes(b"legacy")
    trades = [_trade(
        "canal2_1",
        "2026-07-08T10:00:00+00:00",
        "2026-07-08T10:05:00+00:00",
    )]

    legacy_status = ensure_replay_tick_cache.build_status(
        trades,
        cache_dir=cache_dir,
        pad_minutes=0,
    )
    ensure_replay_tick_cache.write_day_contract(cache_dir, day)
    valid_status = ensure_replay_tick_cache.build_status(
        trades,
        cache_dir=cache_dir,
        pad_minutes=0,
    )
    day_file.write_bytes(b"tampered")
    tampered_status = ensure_replay_tick_cache.build_status(
        trades,
        cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert legacy_status["invalid_days"] == ["2026-07-08"]
    assert legacy_status["cached_days"] == []
    assert valid_status["ok"] is True
    assert valid_status["cached_days"] == ["2026-07-08"]
    assert tampered_status["invalid_days"] == ["2026-07-08"]


def test_load_valid_day_contract_returns_normalized_evidence(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    day = date(2026, 7, 6)
    parquet = cache_dir / "2026-07-06.parquet"
    parquet.write_bytes(b"verified tick bytes")
    ensure_replay_tick_cache.write_day_contract(cache_dir, day)

    record = ensure_replay_tick_cache.load_valid_day_contract(cache_dir, day)

    assert record == {
        "day": "2026-07-06",
        "tick_time_contract": "mt5_utc_v2",
        "time_basis": "UTC",
        "parquet_sha256": ensure_replay_tick_cache._file_sha256(parquet),
        "size_bytes": parquet.stat().st_size,
    }


def test_cache_status_uses_repo_relative_cache_dir_for_default_cache():
    status = ensure_replay_tick_cache.build_status(
        [],
        cache_dir=ensure_replay_tick_cache.DEFAULT_CACHE_DIR,
        pad_minutes=0,
    )

    assert status["cache_dir"] == "data/ticks_cache"


def test_refresh_cache_days_removes_only_explicit_day_files(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    july_8 = cache_dir / "2026-07-08.parquet"
    july_9 = cache_dir / "2026-07-09.parquet"
    unrelated = cache_dir / "notes.txt"
    july_8.write_bytes(b"old-contract")
    july_9.write_bytes(b"keep")
    unrelated.write_text("keep", encoding="utf-8")

    removed = ensure_replay_tick_cache.refresh_cache_days(
        [date(2026, 7, 8)],
        cache_dir=cache_dir,
    )

    assert removed == [date(2026, 7, 8)]
    assert not july_8.exists()
    assert july_9.read_bytes() == b"keep"
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_refresh_day_cli_redownloads_invalidated_required_day(
    tmp_path,
    monkeypatch,
):
    replay_path = tmp_path / "replay_trades.jsonl"
    status_path = tmp_path / "replay_tick_cache_status.json"
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    day_file = cache_dir / "2026-07-08.parquet"
    day_file.write_bytes(b"old-contract")
    replay_path.write_text(
        json.dumps(_trade(
            "canal2_1",
            "2026-07-08T10:00:00+00:00",
            "2026-07-08T10:05:00+00:00",
        )) + "\n",
        encoding="utf-8",
    )
    requested = []

    def fake_ensure(days, *, cache_dir, symbol, verbose):
        requested.extend(days)
        for day in days:
            (cache_dir / f"{day.isoformat()}.parquet").write_bytes(b"utc-v2")
        return {"downloaded": len(days)}

    monkeypatch.setattr(
        ensure_replay_tick_cache,
        "ensure_missing_days",
        fake_ensure,
    )

    exit_code = ensure_replay_tick_cache.main([
        "--input",
        str(replay_path),
        "--status",
        str(status_path),
        "--cache-dir",
        str(cache_dir),
        "--ensure",
        "--refresh-day",
        "2026-07-08",
        "--quiet",
    ])

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert requested == [date(2026, 7, 8)]
    assert day_file.read_bytes() == b"utc-v2"
    assert status["refresh_requested_days"] == ["2026-07-08"]
    assert status["refresh_removed_days"] == ["2026-07-08"]
    assert status["tick_time_contract"] == "mt5_utc_v2"


def test_ensure_cli_automatically_replaces_unversioned_required_day(
    tmp_path,
    monkeypatch,
):
    replay_path = tmp_path / "replay_trades.jsonl"
    status_path = tmp_path / "replay_tick_cache_status.json"
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    day_file = cache_dir / "2026-07-08.parquet"
    day_file.write_bytes(b"legacy")
    replay_path.write_text(
        json.dumps(_trade(
            "canal2_1",
            "2026-07-08T10:00:00+00:00",
            "2026-07-08T10:05:00+00:00",
        )) + "\n",
        encoding="utf-8",
    )
    requested = []

    def fake_ensure(days, *, cache_dir, symbol, verbose):
        requested.extend(days)
        for day in days:
            (cache_dir / f"{day.isoformat()}.parquet").write_bytes(b"utc-v2")
        return {"downloaded": len(days)}

    monkeypatch.setattr(
        ensure_replay_tick_cache,
        "ensure_missing_days",
        fake_ensure,
    )

    exit_code = ensure_replay_tick_cache.main([
        "--input",
        str(replay_path),
        "--status",
        str(status_path),
        "--cache-dir",
        str(cache_dir),
        "--ensure",
        "--quiet",
    ])

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert requested == [date(2026, 7, 8)]
    assert status["invalid_days"] == []
    assert status["cached_days"] == ["2026-07-08"]
    assert (cache_dir / "2026-07-08.parquet.meta.json").is_file()


def test_dry_run_cli_writes_tick_cache_status(tmp_path):
    replay_path = tmp_path / "replay_trades.jsonl"
    status_path = tmp_path / "replay_tick_cache_status.json"
    cache_dir = tmp_path / "ticks_cache"
    replay_path.write_text(
        json.dumps(_trade(
            "canal1_1",
            "2026-07-06T10:00:00+00:00",
            "2026-07-06T10:05:00+00:00",
        )) + "\n",
        encoding="utf-8",
    )

    exit_code = ensure_replay_tick_cache.main([
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


def test_tool_exposes_repo_root_when_run_as_script(monkeypatch):
    repo_dir = str(ensure_replay_tick_cache.REPO_DIR)
    monkeypatch.setattr(
        sys,
        "path",
        [p for p in sys.path if p != repo_dir],
    )

    ensure_replay_tick_cache.ensure_repo_import_path()

    assert sys.path[0] == repo_dir


def test_mt5_tick_source_uses_utc_without_stale_tick_offset(monkeypatch):
    requested = []
    tick_dt = datetime(2026, 7, 6, 10, 0, 1, tzinfo=timezone.utc)

    class FakeMT5:
        COPY_TICKS_ALL = 7

        @staticmethod
        def initialize():
            return True

        @staticmethod
        def symbol_select(_symbol, _enabled):
            return True

        @staticmethod
        def symbol_info_tick(_symbol):
            # Deliberately stale: the request contract must not depend on it.
            return SimpleNamespace(
                time=int(datetime(2026, 7, 3, tzinfo=timezone.utc).timestamp())
            )

        @staticmethod
        def copy_ticks_range(symbol, date_from, date_to, flags):
            requested.append((symbol, date_from, date_to, flags))
            return [{
                "time_msc": int(tick_dt.timestamp() * 1000),
                "bid": 4100.0,
                "ask": 4100.2,
                "last": 0.0,
                "volume": 0,
                "flags": 0,
            }]

        @staticmethod
        def last_error():
            return (0, "ok")

        @staticmethod
        def shutdown():
            return None

    monkeypatch.setitem(sys.modules, "MetaTrader5", FakeMT5)
    source = ensure_replay_tick_cache.MT5TickSource("XAUUSD")
    date_from = datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc)
    date_to = datetime(2026, 7, 6, 10, 1, tzinfo=timezone.utc)

    ticks = source.fetch_ticks(date_from, date_to)

    assert requested == [("XAUUSD", date_from, date_to, FakeMT5.COPY_TICKS_ALL)]
    assert ticks.iloc[0]["time_utc"].to_pydatetime() == tick_dt
