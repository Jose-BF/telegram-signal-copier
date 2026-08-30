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


def _source_verification(*, symbol="XAUUSD", row_count=100):
    digest = "a" * 64
    return {
        "verified": True,
        "method": "full_day_vs_two_half_days_v1",
        "content_digest": "time_bid_ask_sequence_sha256_v1",
        "symbol": symbol,
        "primary_row_count": row_count,
        "verification_row_count": row_count,
        "primary_content_sha256": digest,
        "verification_content_sha256": digest,
        "errors": [],
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
        source_verification=_source_verification(),
        symbol="XAUUSD",
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


def test_required_provider_days_include_unexecuted_and_sunday_signals():
    catalog = {
        "signals": [
            {
                "record_type": "formal_signal",
                "provider_signal_id": "canal2_msg_380",
                "channel": "canal2",
                "first_observed_utc": "2026-07-23T15:30:25+00:00",
                "entry_contract": {
                    "status": "ready",
                    "direction": "BUY",
                    "trigger_observed_utc": "2026-07-23T15:30:25+00:00",
                    "blockers": [],
                },
            },
            {
                "record_type": "formal_signal",
                "provider_signal_id": "canal1_sunday",
                "channel": "canal1",
                "first_observed_utc": "2026-07-12T21:05:00+00:00",
                "entry_contract": {
                    "status": "ready",
                    "direction": "SELL",
                    "trigger_observed_utc": "2026-07-12T21:05:00+00:00",
                    "blockers": [],
                },
            },
            {
                "record_type": "context",
                "provider_signal_id": "context_only",
                "first_observed_utc": "2026-07-16T10:00:00+00:00",
            },
        ]
    }

    days = ensure_replay_tick_cache.required_provider_dates(
        catalog,
        since=datetime(2026, 7, 6, tzinfo=timezone.utc),
        until=datetime(2026, 7, 24, tzinfo=timezone.utc),
        latency_scenarios_ms=[0],
        offset_candidates_seconds=[10_800],
    )

    assert days == [
        date(2026, 7, 12),
        date(2026, 7, 13),
        date(2026, 7, 23),
    ]


def test_provider_cli_scope_can_exclude_open_day_without_hiding_trade_windows(
    tmp_path,
):
    replay_path = tmp_path / "replay_trades.jsonl"
    catalog_path = tmp_path / "provider_signal_catalog.json"
    status_path = tmp_path / "replay_tick_cache_status.json"
    cache_dir = tmp_path / "ticks_cache"
    replay_path.write_text(
        json.dumps(_trade(
            "actual_current_day",
            "2026-08-30T10:00:00+00:00",
            "2026-08-30T10:05:00+00:00",
        )) + "\n",
        encoding="utf-8",
    )
    catalog_path.write_text(
        json.dumps({
            "signals": [
                {
                    "record_type": "formal_signal",
                    "provider_signal_id": "closed_day_signal",
                    "channel": "canal1",
                    "first_observed_utc": "2026-08-27T10:00:00+00:00",
                    "entry_contract": {
                        "status": "ready",
                        "direction": "BUY",
                        "trigger_observed_utc": "2026-08-27T10:00:00+00:00",
                        "blockers": [],
                    },
                },
                {
                    "record_type": "formal_signal",
                    "provider_signal_id": "open_day_signal",
                    "channel": "canal1",
                    "first_observed_utc": "2026-08-30T10:00:00+00:00",
                    "entry_contract": {
                        "status": "ready",
                        "direction": "BUY",
                        "trigger_observed_utc": "2026-08-30T10:00:00+00:00",
                        "blockers": [],
                    },
                },
            ],
        }) + "\n",
        encoding="utf-8",
    )

    exit_code = ensure_replay_tick_cache.main([
        "--input", str(replay_path),
        "--catalog", str(catalog_path),
        "--status", str(status_path),
        "--cache-dir", str(cache_dir),
        "--provider-until", "2026-08-29",
        "--dry-run",
        "--quiet",
    ])

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert "2026-08-27" in status["scope"]["additional_required_days"]
    assert "2026-08-30" not in status["scope"]["additional_required_days"]
    assert "2026-08-30" in status["required_days"]


def test_status_additional_days_require_complete_day_coverage(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    day = date(2026, 7, 23)
    (cache_dir / "2026-07-23.parquet").write_bytes(b"partial-provider-day")
    _write_valid_contract(cache_dir, day)
    _set_contract_coverage(
        cache_dir,
        day,
        _tick_coverage(
            day,
            captured_at="2026-07-23T17:00:00+00:00",
            complete_through="2026-07-23T16:59:59+00:00",
        ),
    )

    status = ensure_replay_tick_cache.build_status(
        [],
        cache_dir=cache_dir,
        pad_minutes=0,
        additional_required_days=[day],
    )

    assert status["ok"] is False
    assert status["required_days"] == ["2026-07-23"]
    assert status["incomplete_days"] == ["2026-07-23"]
    assert status["scope"]["additional_required_days"] == ["2026-07-23"]


def test_adjacent_verified_contract_seeds_missing_day_offset(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    previous = date(2026, 7, 22)
    (cache_dir / "2026-07-22.parquet").write_bytes(b"verified-neighbour")
    _write_valid_contract(cache_dir, previous)

    evidence = ensure_replay_tick_cache.load_adjacent_time_evidence(
        cache_dir,
        [date(2026, 7, 23), date(2026, 7, 24)],
        expected_symbol="XAUUSD",
    )

    assert list(evidence) == [previous]
    assert evidence[previous]["utc_offset_seconds"] == 10_800
    assert evidence[previous]["source_time_basis"] == "mt5_server_epoch"


def test_verified_cache_offsets_ignore_unverified_day_contracts(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    valid_day = date(2026, 7, 22)
    invalid_day = date(2026, 7, 23)
    (cache_dir / "2026-07-22.parquet").write_bytes(b"verified")
    _write_valid_contract(cache_dir, valid_day)
    (cache_dir / "2026-07-23.parquet").write_bytes(b"unverified")
    (cache_dir / "2026-07-23.parquet.meta.json").write_text(
        json.dumps({"utc_offset_seconds": -18_000}),
        encoding="utf-8",
    )

    offsets = ensure_replay_tick_cache.verified_cache_offset_candidates(
        cache_dir,
        expected_symbol="XAUUSD",
    )

    assert offsets == [10_800]


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
    assert record["symbol"] == "XAUUSD"
    assert record["anchor_validation"] == _semantic_validation()
    assert record["parquet_sha256"] == ensure_replay_tick_cache._file_sha256(parquet)
    assert record["contract_sha256"] == ensure_replay_tick_cache._file_sha256(
        cache_dir / "2026-07-06.parquet.meta.json"
    )
    assert record["size_bytes"] == parquet.stat().st_size


def test_cache_status_rejects_contract_without_expected_symbol(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    day = date(2026, 7, 6)
    parquet = cache_dir / "2026-07-06.parquet"
    parquet.write_bytes(b"legacy identity-free ticks")
    contract_path = _write_valid_contract(cache_dir, day)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract.pop("symbol")
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    status = ensure_replay_tick_cache.build_status(
        [_trade(
            "canal1_1",
            "2026-07-06T10:00:00+00:00",
            "2026-07-06T10:05:00+00:00",
        )],
        cache_dir=cache_dir,
        pad_minutes=0,
        expected_symbol="XAUUSD",
    )

    assert status["ok"] is False
    assert status["invalid_days"] == ["2026-07-06"]


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

    expected = ensure_replay_tick_cache.DEFAULT_CACHE_DIR.relative_to(
        ensure_replay_tick_cache.REPO_DIR
    ).as_posix()
    assert status["cache_dir"] == expected
    assert "\\" not in status["cache_dir"]


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
                    "source_verification": _source_verification(),
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
                    "source_verification": _source_verification(),
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
                    "source_verification": _source_verification(),
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


def test_extract_fill_anchors_prefers_canonical_deal_time_over_response_time():
    trades = [{
        "sig_id": "canal2_1716",
        "direction": "BUY",
        "tickets": [{
            "ticket": 201,
            "open_dt_utc": "2026-08-19T12:57:38+00:00",
            "open_price": 4331.25,
            "fill_event": {
                "ts": "2026-08-19T12:57:39.451+00:00",
                "price": 4331.25,
            },
        }],
    }]

    anchors = ensure_replay_tick_cache.extract_fill_anchors(trades)

    anchor = anchors[date(2026, 8, 19)][0]
    assert anchor.time_utc == datetime(
        2026, 8, 19, 12, 57, 38, tzinfo=timezone.utc
    )


def test_intraday_capture_after_broker_close_proves_session_horizon():
    day = date(2026, 7, 16)
    ticks = pd.DataFrame([{
        "time_utc": pd.Timestamp("2026-07-16T20:57:59.173+00:00"),
        "bid": 3990.0,
        "ask": 3990.2,
    }])

    coverage = ensure_replay_tick_cache.build_tick_coverage(
        ticks,
        day,
        captured_at=datetime(2026, 7, 16, 21, 35, tzinfo=timezone.utc),
        utc_offset_seconds=10_800,
    )

    assert coverage["last_tick_utc"] == "2026-07-16T20:57:59.173000+00:00"
    assert coverage["complete_through_utc"] == "2026-07-16T20:58:00+00:00"


def test_legacy_intraday_contract_uses_verified_session_close_boundary():
    contract = {
        "utc_offset_seconds": 10_800,
        "coverage": {
            "captured_at_utc": "2026-07-16T21:35:00+00:00",
            "last_tick_utc": "2026-07-16T20:57:59.173000+00:00",
            "complete_from_utc": "2026-07-16T00:00:00+00:00",
            "complete_through_utc": "2026-07-16T20:57:59.173000+00:00",
        },
    }
    required_from = datetime(
        2026, 7, 16, 12, 57, 55, tzinfo=timezone.utc,
    )
    session_close = datetime(
        2026, 7, 16, 20, 58, tzinfo=timezone.utc,
    )

    assert ensure_replay_tick_cache.coverage_satisfies_window(
        contract,
        required_from,
        session_close,
    )
    assert not ensure_replay_tick_cache.coverage_satisfies_window(
        contract,
        required_from,
        session_close + timedelta(milliseconds=1),
    )


def test_mt5_tick_source_uses_half_open_end_and_preserves_equal_time_order(
    monkeypatch,
):
    day = date(2026, 7, 13)
    day_start = datetime(2026, 7, 13, tzinfo=timezone.utc)
    same_time = day_start + timedelta(seconds=1)
    day_end = day_start + timedelta(days=1)

    class FakeMT5:
        COPY_TICKS_ALL = 7

        @staticmethod
        def initialize():
            return True

        @staticmethod
        def symbol_select(_symbol, _enabled):
            return True

        @staticmethod
        def copy_ticks_range(_symbol, _date_from, _date_to, _flags):
            return [
                {
                    "time_msc": int(same_time.timestamp() * 1000),
                    "bid": 100.0,
                    "ask": 100.2,
                    "last": 0.0,
                    "volume": 0,
                    "flags": 1,
                },
                {
                    "time_msc": int(same_time.timestamp() * 1000),
                    "bid": 99.9,
                    "ask": 100.1,
                    "last": 0.0,
                    "volume": 0,
                    "flags": 2,
                },
                {
                    "time_msc": int(day_end.timestamp() * 1000),
                    "bid": 101.0,
                    "ask": 101.2,
                    "last": 0.0,
                    "volume": 0,
                    "flags": 3,
                },
            ]

        @staticmethod
        def last_error():
            return (0, "ok")

        @staticmethod
        def shutdown():
            return None

    monkeypatch.setitem(sys.modules, "MetaTrader5", FakeMT5)
    source = ensure_replay_tick_cache.MT5TickSource(
        "XAUUSD",
        preloaded_time_evidence_by_day={day: _time_evidence(0)},
    )

    ticks = source.fetch_ticks(day_start, day_end)

    assert ticks["bid"].tolist() == [100.0, 99.9]
    assert ticks["flags"].tolist() == [1, 2]
    assert (ticks["time_utc"] < pd.Timestamp(day_end)).all()


def test_source_acquisition_verification_rejects_missing_chunk_tick():
    day = date(2026, 7, 13)
    day_start = datetime(2026, 7, 13, tzinfo=timezone.utc)
    noon = day_start + timedelta(hours=12)
    primary = pd.DataFrame([
        {
            "time_utc": pd.Timestamp(day_start + timedelta(seconds=1)),
            "bid": 100.0,
            "ask": 100.2,
        },
        {
            "time_utc": pd.Timestamp(noon + timedelta(seconds=1)),
            "bid": 101.0,
            "ask": 101.2,
        },
    ])

    class MissingTickSource:
        symbol = "XAUUSD"

        @staticmethod
        def fetch_ticks(t_from_utc, _t_to_utc):
            if t_from_utc == day_start:
                return primary.iloc[[0]].copy()
            return primary.iloc[0:0].copy()

    result = ensure_replay_tick_cache.verify_day_source_acquisition(
        MissingTickSource(),
        day,
        primary,
    )

    assert result["verified"] is False
    assert result["method"] == "full_day_vs_two_half_days_v1"
    assert result["primary_row_count"] == 2
    assert result["verification_row_count"] == 1
    assert result["errors"] == ["source_row_count_mismatch"]


def test_source_acquisition_verification_rejects_reordered_equal_count():
    day = date(2026, 7, 13)
    day_start = datetime(2026, 7, 13, tzinfo=timezone.utc)
    noon = day_start + timedelta(hours=12)
    primary = pd.DataFrame([
        {
            "time_utc": pd.Timestamp(day_start + timedelta(seconds=1)),
            "bid": 100.0,
            "ask": 100.2,
        },
        {
            "time_utc": pd.Timestamp(day_start + timedelta(seconds=2)),
            "bid": 100.1,
            "ask": 100.3,
        },
    ])

    class ReorderedTickSource:
        symbol = "XAUUSD"

        @staticmethod
        def fetch_ticks(t_from_utc, _t_to_utc):
            if t_from_utc == day_start:
                return primary.iloc[::-1].reset_index(drop=True)
            if t_from_utc == noon:
                return primary.iloc[0:0].copy()
            raise AssertionError(t_from_utc)

    result = ensure_replay_tick_cache.verify_day_source_acquisition(
        ReorderedTickSource(),
        day,
        primary,
    )

    assert result["verified"] is False
    assert result["primary_row_count"] == result["verification_row_count"] == 2
    assert result["errors"] == ["source_content_mismatch"]


def test_source_acquisition_verification_accepts_exact_independent_halves():
    day = date(2026, 7, 13)
    day_start = datetime(2026, 7, 13, tzinfo=timezone.utc)
    noon = day_start + timedelta(hours=12)
    primary = pd.DataFrame([
        {
            "time_utc": pd.Timestamp(day_start + timedelta(seconds=1)),
            "bid": 100.0,
            "ask": 100.2,
        },
        {
            "time_utc": pd.Timestamp(noon + timedelta(seconds=1)),
            "bid": 101.0,
            "ask": 101.2,
        },
    ])

    class ExactTickSource:
        symbol = "XAUUSD"

        @staticmethod
        def fetch_ticks(t_from_utc, _t_to_utc):
            if t_from_utc == day_start:
                return primary.iloc[[0]].copy()
            if t_from_utc == noon:
                return primary.iloc[[1]].copy()
            raise AssertionError(t_from_utc)

    result = ensure_replay_tick_cache.verify_day_source_acquisition(
        ExactTickSource(),
        day,
        primary,
    )

    assert result["verified"] is True
    assert result["primary_row_count"] == result["verification_row_count"] == 2
    assert (
        result["primary_content_sha256"]
        == result["verification_content_sha256"]
    )
    assert result["errors"] == []


def test_contract_without_verified_source_acquisition_is_rejected(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    day = date(2026, 7, 13)
    (cache_dir / f"{day.isoformat()}.parquet").write_bytes(b"ticks")

    contract_path = ensure_replay_tick_cache.write_day_contract(
        cache_dir,
        day,
        time_evidence=_time_evidence(),
        semantic_validation=_semantic_validation(),
        coverage=_tick_coverage(
            day,
            captured_at="2026-07-14T00:01:00+00:00",
            complete_through="2026-07-14T00:00:00+00:00",
        ),
        source_verification={
            "verified": False,
            "method": "full_day_vs_two_half_days_v1",
            "symbol": "XAUUSD",
            "primary_row_count": 1,
            "verification_row_count": 0,
            "primary_content_sha256": "a" * 64,
            "verification_content_sha256": "b" * 64,
            "errors": ["source_content_mismatch"],
        },
        symbol="XAUUSD",
    )

    assert contract_path.is_file()
    assert ensure_replay_tick_cache.load_valid_day_contract(
        cache_dir,
        day,
        expected_symbol="XAUUSD",
    ) is None
