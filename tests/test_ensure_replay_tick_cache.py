import json
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from tools import ensure_replay_tick_cache


def _trade(sig_id, open_dt, close_dt):
    return {
        "sig_id": sig_id,
        "open_dt_utc": open_dt,
        "close_dt_utc": close_dt,
    }


def _time_evidence(offset_seconds=10_800):
    return {
        "source_time_basis": "mt5_server_epoch",
        "utc_offset_seconds": offset_seconds,
        "offset_detection_method": "fill_anchor",
        "offset_reference": {
            "signal_id": "canal2_1",
            "ticket": 101,
            "anchor_time_utc": "2026-07-13T08:00:00+00:00",
            "raw_time_msc": 1_783_936_800_000,
            "quote_side": "ask",
            "fill_price": 4059.61,
        },
    }


def _semantic_validation(valid=True):
    return {
        "valid": valid,
        "anchors_checked": 1,
        "anchors_matched": 1 if valid else 0,
        "max_time_delta_ms": 0 if valid else None,
        "max_price_delta": 0.01 if valid else None,
        "errors": [] if valid else ["fill_anchor_outside_tolerance"],
    }


def _tick_coverage(day, *, captured_at, complete_through, row_count=100):
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return {
        "source_query_start_utc": day_start.isoformat(),
        "source_query_end_utc": (day_start + timedelta(days=1)).isoformat(),
        "captured_at_utc": captured_at,
        "first_tick_utc": day_start.isoformat(),
        "last_tick_utc": complete_through,
        "complete_from_utc": day_start.isoformat(),
        "complete_through_utc": complete_through,
        "row_count": row_count,
    }


def _write_valid_contract(cache_dir, day):
    day_end = datetime(
        day.year, day.month, day.day, tzinfo=timezone.utc
    ) + timedelta(days=1)
    return ensure_replay_tick_cache.write_day_contract(
        cache_dir,
        day,
        time_evidence=_time_evidence(),
        semantic_validation=_semantic_validation(),
        coverage=_tick_coverage(
            day,
            captured_at=(day_end + timedelta(minutes=1)).isoformat(),
            complete_through=day_end.isoformat(),
        ),
    )


def _set_contract_coverage(cache_dir, day, coverage):
    path = cache_dir / f"{day.isoformat()}.parquet.meta.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["coverage"] = coverage
    path.write_text(json.dumps(payload), encoding="utf-8")


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
    _write_valid_contract(cache_dir, date(2026, 7, 6))

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
    assert status["tick_time_contract"] == "mt5_server_epoch_utc_v3"


def test_cache_status_rejects_intraday_cache_that_ends_before_trade_close(
    tmp_path,
):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    day = date(2026, 7, 15)
    (cache_dir / "2026-07-15.parquet").write_bytes(b"partial-but-valid")
    _write_valid_contract(cache_dir, day)
    _set_contract_coverage(
        cache_dir,
        day,
        _tick_coverage(
            day,
            captured_at="2026-07-15T12:51:00+00:00",
            complete_through="2026-07-15T12:50:59+00:00",
        ),
    )

    status = ensure_replay_tick_cache.build_status(
        [_trade(
            "canal1_afternoon",
            "2026-07-15T12:32:14+00:00",
            "2026-07-15T17:15:12+00:00",
        )],
        cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert status["ok"] is False
    assert status.get("cached_days") == []
    assert status.get("incomplete_days") == ["2026-07-15"]
    assert status.get("coverage_by_day", {})["2026-07-15"] == {
        "status": "incomplete",
        "required_from_utc": "2026-07-15T12:32:14+00:00",
        "required_through_utc": "2026-07-15T17:15:12+00:00",
        "complete_from_utc": "2026-07-15T00:00:00+00:00",
        "complete_through_utc": "2026-07-15T12:50:59+00:00",
    }


def test_legacy_contract_infers_partial_coverage_from_last_cached_tick(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    day = date(2026, 7, 15)
    pd.DataFrame([{
        "time_utc": pd.Timestamp("2026-07-15T12:50:59+00:00"),
        "bid": 4030.0,
        "ask": 4030.2,
    }]).to_parquet(cache_dir / "2026-07-15.parquet", index=False)
    contract_path = _write_valid_contract(cache_dir, day)
    legacy_contract = json.loads(contract_path.read_text(encoding="utf-8"))
    legacy_contract.pop("coverage")
    contract_path.write_text(json.dumps(legacy_contract), encoding="utf-8")

    status = ensure_replay_tick_cache.build_status(
        [_trade(
            "canal1_afternoon",
            "2026-07-15T12:32:14+00:00",
            "2026-07-15T17:15:12+00:00",
        )],
        cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert status["incomplete_days"] == ["2026-07-15"]
    assert status["coverage_by_day"]["2026-07-15"][
        "complete_through_utc"
    ] == "2026-07-15T12:50:59+00:00"


def test_cache_status_scope_excludes_historical_invalid_day(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    (cache_dir / "2026-06-09.parquet").write_bytes(b"legacy")
    (cache_dir / "2026-07-06.parquet").write_bytes(b"selected")
    _write_valid_contract(cache_dir, date(2026, 7, 6))
    trades = [
        _trade(
            "canal1_old",
            "2026-06-09T10:00:00+00:00",
            "2026-06-09T10:05:00+00:00",
        ),
        _trade(
            "canal1_new",
            "2026-07-06T10:00:00+00:00",
            "2026-07-06T10:05:00+00:00",
        ),
    ]

    status = ensure_replay_tick_cache.build_status(
        trades,
        cache_dir=cache_dir,
        since=datetime(2026, 7, 6, tzinfo=timezone.utc),
        pad_minutes=0,
    )

    assert status["ok"] is True
    assert status["required_days"] == ["2026-07-06"]
    assert status["invalid_days"] == []
    assert status["n_trades"] == 1
    assert status["scope"] == {
        "since": "2026-07-06T00:00:00+00:00",
        "until": None,
        "input_trades": 2,
        "selected_trades": 1,
    }


def test_scope_uses_trade_cohort_not_close_time_overlap(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    (cache_dir / "2026-07-06.parquet").write_bytes(b"selected")
    _write_valid_contract(cache_dir, date(2026, 7, 6))
    trades = [
        _trade(
            "canal1_old",
            "2026-07-05T23:55:00+00:00",
            "2026-07-06T00:05:00+00:00",
        ),
        _trade(
            "canal1_new",
            "2026-07-06T10:00:00+00:00",
            "2026-07-06T10:05:00+00:00",
        ),
    ]

    status = ensure_replay_tick_cache.build_status(
        trades,
        cache_dir=cache_dir,
        since=datetime(2026, 7, 6, tzinfo=timezone.utc),
        pad_minutes=0,
    )

    assert status["ok"] is True
    assert status["n_trades"] == 1
    assert status["required_days"] == ["2026-07-06"]


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
    _write_valid_contract(cache_dir, day)
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
    _write_valid_contract(cache_dir, day)

    record = ensure_replay_tick_cache.load_valid_day_contract(cache_dir, day)

    assert record["day"] == "2026-07-06"
    assert record["tick_time_contract"] == "mt5_server_epoch_utc_v3"
    assert record["time_basis"] == "UTC"
    assert record["source_time_basis"] == "mt5_server_epoch"
    assert record["utc_offset_seconds"] == 10_800
    assert record["offset_detection_method"] == "fill_anchor"
    assert record["semantic_time_valid"] is True
    assert record["anchor_validation"] == _semantic_validation()
    assert record["parquet_sha256"] == ensure_replay_tick_cache._file_sha256(parquet)
    assert record["size_bytes"] == parquet.stat().st_size


def test_v2_contract_is_rejected_even_when_hash_matches(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    day = date(2026, 7, 8)
    parquet = cache_dir / "2026-07-08.parquet"
    parquet.write_bytes(b"old but internally consistent")
    (cache_dir / "2026-07-08.parquet.meta.json").write_text(
        json.dumps({
            "tick_time_contract": "mt5_utc_v2",
            "time_basis": "UTC",
            "parquet_sha256": ensure_replay_tick_cache._file_sha256(parquet),
        }),
        encoding="utf-8",
    )

    assert ensure_replay_tick_cache.load_valid_day_contract(cache_dir, day) is None


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

    def fake_ensure(days, *, cache_dir, symbol, verbose, trades):
        requested.extend(days)
        for day in days:
            (cache_dir / f"{day.isoformat()}.parquet").write_bytes(b"utc-v2")
        return {
            "downloaded": len(days),
            "day_contracts": {
                day.isoformat(): {
                    "time_evidence": _time_evidence(),
                    "semantic_validation": _semantic_validation(),
                    "coverage": _tick_coverage(
                        day,
                        captured_at=(datetime(
                            day.year, day.month, day.day, tzinfo=timezone.utc
                        ) + timedelta(days=1, minutes=1)).isoformat(),
                        complete_through=(datetime(
                            day.year, day.month, day.day, tzinfo=timezone.utc
                        ) + timedelta(days=1)).isoformat(),
                    ),
                }
                for day in days
            },
        }

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
    assert status["tick_time_contract"] == "mt5_server_epoch_utc_v3"


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

    def fake_ensure(days, *, cache_dir, symbol, verbose, trades):
        requested.extend(days)
        for day in days:
            (cache_dir / f"{day.isoformat()}.parquet").write_bytes(b"utc-v2")
        return {
            "downloaded": len(days),
            "day_contracts": {
                day.isoformat(): {
                    "time_evidence": _time_evidence(),
                    "semantic_validation": _semantic_validation(),
                    "coverage": _tick_coverage(
                        day,
                        captured_at=(datetime(
                            day.year, day.month, day.day, tzinfo=timezone.utc
                        ) + timedelta(days=1, minutes=1)).isoformat(),
                        complete_through=(datetime(
                            day.year, day.month, day.day, tzinfo=timezone.utc
                        ) + timedelta(days=1)).isoformat(),
                    ),
                }
                for day in days
            },
        }

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


def test_ensure_cli_replaces_coverage_incomplete_required_day(
    tmp_path,
    monkeypatch,
):
    replay_path = tmp_path / "replay_trades.jsonl"
    status_path = tmp_path / "replay_tick_cache_status.json"
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    day = date(2026, 7, 15)
    day_file = cache_dir / "2026-07-15.parquet"
    day_file.write_bytes(b"partial-session")
    _write_valid_contract(cache_dir, day)
    _set_contract_coverage(
        cache_dir,
        day,
        _tick_coverage(
            day,
            captured_at="2026-07-15T12:51:00+00:00",
            complete_through="2026-07-15T12:50:59+00:00",
        ),
    )
    replay_path.write_text(
        json.dumps(_trade(
            "canal1_afternoon",
            "2026-07-15T13:00:00+00:00",
            "2026-07-15T17:15:12+00:00",
        )) + "\n",
        encoding="utf-8",
    )
    requested = []

    def fake_ensure(days, *, cache_dir, symbol, verbose, trades):
        requested.extend(days)
        for requested_day in days:
            (cache_dir / f"{requested_day.isoformat()}.parquet").write_bytes(
                b"complete-session"
            )
        return {
            "downloaded": len(days),
            "day_contracts": {
                requested_day.isoformat(): {
                    "time_evidence": _time_evidence(),
                    "semantic_validation": _semantic_validation(),
                    "coverage": _tick_coverage(
                        requested_day,
                        captured_at="2026-07-16T00:05:00+00:00",
                        complete_through="2026-07-16T00:00:00+00:00",
                    ),
                }
                for requested_day in days
            },
        }

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
        "--pad-minutes",
        "0",
        "--quiet",
    ])

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert requested == [day]
    assert day_file.read_bytes() == b"complete-session"
    assert status["cached_days"] == ["2026-07-15"]
    assert status["incomplete_days"] == []


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


def test_mt5_tick_source_converts_vantage_server_epoch_to_utc(monkeypatch):
    requested = []
    day = date(2026, 7, 13)
    anchor_dt = datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)
    server_tick_dt = anchor_dt + timedelta(hours=3)

    class FakeMT5:
        COPY_TICKS_ALL = 7

        @staticmethod
        def initialize():
            return True

        @staticmethod
        def symbol_select(_symbol, _enabled):
            return True

        @staticmethod
        def copy_ticks_range(symbol, date_from, date_to, flags):
            requested.append((symbol, date_from, date_to, flags))
            if not (date_from <= server_tick_dt <= date_to):
                return []
            return [{
                "time_msc": int(server_tick_dt.timestamp() * 1000),
                "bid": 4059.37,
                "ask": 4059.61,
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
    anchor = ensure_replay_tick_cache.FillAnchor(
        signal_id="canal2_1",
        ticket=101,
        time_utc=anchor_dt,
        price=4059.61,
        quote_side="ask",
    )
    source = ensure_replay_tick_cache.MT5TickSource(
        "XAUUSD",
        anchors_by_day={day: [anchor]},
        offset_candidates_seconds=[0, 10_800],
    )
    date_from = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)
    date_to = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)

    ticks = source.fetch_ticks(date_from, date_to)

    assert any(
        call[1] == anchor_dt + timedelta(hours=3, seconds=-3)
        and call[2] == anchor_dt + timedelta(hours=3, seconds=3)
        for call in requested
    )
    assert requested[-1][1] == date_from + timedelta(hours=3)
    assert requested[-1][2] == date_to + timedelta(hours=3)
    assert ticks.iloc[0]["time_utc"].to_pydatetime() == anchor_dt
    evidence = source.time_evidence_for_day(day)
    assert evidence["utc_offset_seconds"] == 10_800
    assert evidence["offset_detection_method"] == "fill_anchor"


def test_historical_day_cannot_inherit_offset_from_current_live_tick(monkeypatch):
    live_utc = datetime.now(timezone.utc)
    server_epoch = live_utc + timedelta(hours=3)

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
            return SimpleNamespace(time=server_epoch.timestamp())

        @staticmethod
        def copy_ticks_range(*_args):
            return []

        @staticmethod
        def last_error():
            return (0, "ok")

        @staticmethod
        def shutdown():
            return None

    monkeypatch.setitem(sys.modules, "MetaTrader5", FakeMT5)
    source = ensure_replay_tick_cache.MT5TickSource(
        "XAUUSD",
        anchors_by_day={},
        offset_candidates_seconds=[0, 10_800],
    )

    with pytest.raises(RuntimeError, match="cannot prove MT5 server offset"):
        source.time_evidence_for_day(date(2025, 1, 15))


def test_shifted_tick_frame_fails_semantic_fill_anchor_validation():
    anchor = ensure_replay_tick_cache.FillAnchor(
        signal_id="canal2_1",
        ticket=101,
        time_utc=datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
        price=4059.61,
        quote_side="ask",
    )
    ticks = pd.DataFrame([{
        "time_utc": pd.Timestamp("2026-07-13T11:00:00Z"),
        "time_msc": 1_783_936_800_000,
        "bid": 4059.37,
        "ask": 4059.61,
    }])

    result = ensure_replay_tick_cache.validate_cached_day_anchors(
        ticks,
        [anchor],
    )

    assert result["valid"] is False
    assert result["anchors_checked"] == 1
    assert result["anchors_matched"] == 0
    assert result["errors"] == ["fill_anchor_outside_tolerance"]


def test_extract_fill_anchors_uses_direction_quote_side():
    trades = [{
        "sig_id": "canal2_1",
        "direction": "SELL",
        "tickets": [{
            "ticket": 101,
            "open_dt_utc": "2026-07-13T08:00:00+00:00",
            "open_price": 4059.37,
        }],
    }]

    anchors = ensure_replay_tick_cache.extract_fill_anchors(trades)

    assert list(anchors) == [date(2026, 7, 13)]
    assert anchors[date(2026, 7, 13)][0].quote_side == "bid"
    assert anchors[date(2026, 7, 13)][0].price == 4059.37
