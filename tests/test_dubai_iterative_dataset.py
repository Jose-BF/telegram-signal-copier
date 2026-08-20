from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import json

import pandas as pd
import pytest

from research.dubai_iterative.dataset import VerifiedParquetTickSource, load_dubai_dataset


class FakeTickSource:
    def __init__(self, frames=None, blockers=None, parquet_hash=None):
        self.frames = frames or {}
        self.blockers = blockers or {}
        self.parquet_hash = parquet_hash or "a" * 64
        self.loaded = []

    def load_day(self, day: date):
        key = day.isoformat()
        self.loaded.append(key)
        if key in self.blockers:
            return pd.DataFrame(), None, list(self.blockers[key])
        frame = self.frames[key].copy()
        return frame, {"day": key, "parquet_sha256": self.parquet_hash}, []


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _trade(signal_id="canal1_1", channel="canal1", direction="BUY"):
    return {
        "sig_id": signal_id,
        "channel": channel,
        "direction": direction,
        "signal_dt_utc": "2026-07-27T09:00:00+00:00",
        "pnl_real_mt5": "2.35",
        "tickets": [
            {
                "ticket": 101,
                "volume": 0.01,
                "open_dt_utc": "2026-07-27T09:00:00+00:00",
                "open_price": 100.2,
                "close_dt_utc": "2026-07-27T09:05:00.250+00:00",
                "close_price": 102.0,
                "close_reason": "tp",
                "role": "market_a",
                "pnl_net": 2.35,
                "fill_event": {"ts": "2026-07-27T09:00:00.125+00:00"},
                "tp_history": [
                    {
                        "ts": "2026-07-27T09:00:00.200+00:00",
                        "status": "confirmed",
                        "tp": 102.0,
                    }
                ],
                "sl_history": [
                    {
                        "ts": "2026-07-27T09:00:00.200+00:00",
                        "status": "confirmed",
                        "sl": 95.0,
                    }
                ],
            }
        ],
        "management": [],
        "timeline": [],
    }


def _ticks():
    return pd.DataFrame(
        {
            "time_utc": pd.to_datetime(
                [
                    "2026-07-27T09:00:00.100Z",
                    "2026-07-27T09:00:00.125Z",
                    "2026-07-27T09:00:01Z",
                    "2026-07-27T13:00:00Z",
                ],
                format="mixed",
                utc=True,
            ),
            "bid": [100.0, 100.1, 100.5, 101.0],
            "ask": [100.2, 100.3, 100.7, 101.2],
        }
    )


def _money_contract():
    return {
        "account": {"currency": "EUR", "currency_digits": 2},
        "instrument": {"symbol": "XAUUSD", "contract_size": 100.0},
        "conversion": {"orientation": "identity", "max_quote_age_ms": 60_000},
        "costs": {
            "commission_model": "observed_zero_intraday",
            "fee_model": "observed_zero_intraday",
            "swap_model": "intraday_only_zero",
        },
    }


def test_loader_keeps_exact_dubai_and_reports_every_exclusion(tmp_path):
    replay = _write_jsonl(
        tmp_path / "replay.jsonl",
        [
            _trade("canal1_1"),
            _trade("canal1_2"),
            _trade("canal2_3", channel="canal2"),
        ],
    )
    audit = _write_jsonl(
        tmp_path / "audit.jsonl",
        [
            {"sig_id": "canal1_1", "status": "exact", "blockers": []},
            {"sig_id": "canal1_2", "status": "blocked", "blockers": ["gap"]},
            {"sig_id": "canal2_3", "status": "exact", "blockers": []},
        ],
    )
    market = FakeTickSource({"2026-07-27": _ticks()})

    dataset = load_dubai_dataset(
        replay_path=replay,
        audit_path=audit,
        market_ticks=market,
        conversion_ticks=None,
        money_contract=_money_contract(),
        from_date="2026-07-27",
        to_date="2026-08-14",
    )

    assert [path.signal_id for path in dataset.paths] == ["canal1_1"]
    assert dataset.exclusions == {"blocked": ("canal1_2",)}
    assert dataset.actual_pnl_eur == Decimal("2.35")


def test_loader_uses_fill_event_milliseconds_and_executable_quote_side(tmp_path):
    replay = _write_jsonl(tmp_path / "replay.jsonl", [_trade()])
    audit = _write_jsonl(
        tmp_path / "audit.jsonl",
        [{"sig_id": "canal1_1", "status": "exact", "blockers": []}],
    )

    dataset = load_dubai_dataset(
        replay_path=replay,
        audit_path=audit,
        market_ticks=FakeTickSource({"2026-07-27": _ticks()}),
        conversion_ticks=None,
        money_contract=_money_contract(),
        from_date="2026-07-27",
        to_date="2026-07-27",
    )

    path = dataset.paths[0]
    assert path.opened_at.isoformat() == "2026-07-27T09:00:00.125000+00:00"
    assert path.times_ns[0] == pd.Timestamp("2026-07-27T09:00:00.100Z").value
    assert path.exit_quotes.tolist() == path.bid.tolist()
    assert path.legs[0].tp_events[0].level == 102.0
    assert path.legs[0].sl_events[0].level == 95.0
    assert path.legs[0].closed_at.isoformat() == (
        "2026-07-27T09:05:00.250000+00:00"
    )
    assert path.legs[0].close_price == 102.0
    assert path.legs[0].close_reason == "tp"
    assert path.legs[0].role == "market_a"
    assert path.contract_size == 100.0
    assert path.conversion_orientation == "identity"
    assert path.currency_digits == 2


def test_loader_freezes_paths_and_binds_money_contract(tmp_path):
    replay = _write_jsonl(tmp_path / "replay.jsonl", [_trade()])
    audit = _write_jsonl(
        tmp_path / "audit.jsonl",
        [{"sig_id": "canal1_1", "status": "exact", "blockers": []}],
    )
    contract = _money_contract()
    dataset = load_dubai_dataset(
        replay_path=replay,
        audit_path=audit,
        market_ticks=FakeTickSource({"2026-07-27": _ticks()}),
        conversion_ticks=None,
        money_contract=contract,
    )

    path = dataset.paths[0]
    assert path.times_ns.flags.writeable is False
    assert path.bid.flags.writeable is False
    assert path.ask.flags.writeable is False
    assert path.fx_valid.flags.writeable is False
    assert path.fx_valid.all()
    expected = hashlib.sha256(
        json.dumps(
            contract,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert dataset.source_hashes["money_contract"] == expected


def test_dataset_identity_binds_tick_bytes_and_loaded_horizon(tmp_path):
    replay = _write_jsonl(tmp_path / "replay.jsonl", [_trade()])
    audit = _write_jsonl(
        tmp_path / "audit.jsonl",
        [{"sig_id": "canal1_1", "status": "exact", "blockers": []}],
    )

    first = load_dubai_dataset(
        replay_path=replay,
        audit_path=audit,
        market_ticks=FakeTickSource(
            {"2026-07-27": _ticks()},
            parquet_hash="a" * 64,
        ),
        conversion_ticks=None,
        money_contract=_money_contract(),
        max_hold_minutes=240,
    )
    changed_ticks = load_dubai_dataset(
        replay_path=replay,
        audit_path=audit,
        market_ticks=FakeTickSource(
            {"2026-07-27": _ticks()},
            parquet_hash="b" * 64,
        ),
        conversion_ticks=None,
        money_contract=_money_contract(),
        max_hold_minutes=240,
    )
    changed_horizon = load_dubai_dataset(
        replay_path=replay,
        audit_path=audit,
        market_ticks=FakeTickSource(
            {"2026-07-27": _ticks()},
            parquet_hash="a" * 64,
        ),
        conversion_ticks=None,
        money_contract=_money_contract(),
        max_hold_minutes=300,
    )

    assert first.source_hashes["market_ticks"] != changed_ticks.source_hashes["market_ticks"]
    assert first.source_hashes["dataset_contract"] != changed_horizon.source_hashes["dataset_contract"]
    assert first.max_hold_minutes == 240
    assert changed_horizon.max_hold_minutes == 300


def test_dataset_keeps_the_full_exact_universe_visible_when_ticks_are_missing(tmp_path):
    first = _trade("canal1_1")
    second = _trade("canal1_2")
    second["signal_dt_utc"] = "2026-07-28T09:00:00+00:00"
    second["pnl_real_mt5"] = "-1.15"
    second["tickets"][0]["open_dt_utc"] = "2026-07-28T09:00:00+00:00"
    second["tickets"][0]["fill_event"]["ts"] = "2026-07-28T09:00:00.125+00:00"
    replay = _write_jsonl(tmp_path / "replay.jsonl", [first, second])
    audit = _write_jsonl(
        tmp_path / "audit.jsonl",
        [
            {"sig_id": "canal1_1", "status": "exact", "blockers": []},
            {"sig_id": "canal1_2", "status": "exact", "blockers": []},
        ],
    )

    dataset = load_dubai_dataset(
        replay_path=replay,
        audit_path=audit,
        market_ticks=FakeTickSource(
            frames={"2026-07-27": _ticks()},
            blockers={"2026-07-28": ["missing_tick_cache:2026-07-28"]},
        ),
        conversion_ticks=None,
        money_contract=_money_contract(),
    )

    assert dataset.eligible_signal_ids == ("canal1_1", "canal1_2")
    assert [path.signal_id for path in dataset.paths] == ["canal1_1"]
    assert dataset.coverage_complete is False
    assert dataset.actual_pnl_eur == Decimal("1.20")
    assert dataset.loaded_actual_pnl_eur == Decimal("2.35")


def test_loader_refuses_a_cost_model_the_engine_cannot_reproduce(tmp_path):
    replay = _write_jsonl(tmp_path / "replay.jsonl", [_trade()])
    audit = _write_jsonl(
        tmp_path / "audit.jsonl",
        [{"sig_id": "canal1_1", "status": "exact", "blockers": []}],
    )
    contract = _money_contract()
    contract["costs"]["commission_model"] = "per_lot_round_turn"

    with pytest.raises(ValueError, match="unsupported broker cost contract"):
        load_dubai_dataset(
            replay_path=replay,
            audit_path=audit,
            market_ticks=FakeTickSource({"2026-07-27": _ticks()}),
            conversion_ticks=None,
            money_contract=contract,
        )


def test_verified_parquet_source_checks_content_and_contract(tmp_path):
    frame = _ticks()
    parquet = tmp_path / "2026-07-27.parquet"
    frame.to_parquet(parquet, index=False)
    meta = {
        "tick_time_contract": "mt5_server_epoch_utc_v3",
        "time_basis": "UTC",
        "semantic_time_valid": True,
        "parquet_sha256": hashlib.sha256(parquet.read_bytes()).hexdigest(),
        "symbol": "XAUUSD",
        "coverage": {
            "complete_from_utc": "2026-07-27T00:00:00+00:00",
            "complete_through_utc": "2026-07-28T00:00:00+00:00",
        },
        "source_verification": {"verified": True, "errors": []},
    }
    (tmp_path / "2026-07-27.parquet.meta.json").write_text(
        json.dumps(meta),
        encoding="utf-8",
    )

    loaded, evidence, blockers = VerifiedParquetTickSource(
        tmp_path,
        expected_symbol="XAUUSD",
    ).load_day(date(2026, 7, 27))

    assert blockers == []
    assert len(loaded) == len(frame)
    assert evidence["parquet_sha256"] == meta["parquet_sha256"]


def test_verified_parquet_source_rejects_tampered_bytes(tmp_path):
    frame = _ticks()
    parquet = tmp_path / "2026-07-27.parquet"
    frame.to_parquet(parquet, index=False)
    (tmp_path / "2026-07-27.parquet.meta.json").write_text(
        json.dumps({
            "tick_time_contract": "mt5_server_epoch_utc_v3",
            "time_basis": "UTC",
            "semantic_time_valid": True,
            "parquet_sha256": "0" * 64,
            "symbol": "XAUUSD",
            "coverage": {
                "complete_from_utc": "2026-07-27T00:00:00+00:00",
                "complete_through_utc": "2026-07-28T00:00:00+00:00",
            },
            "source_verification": {"verified": True, "errors": []},
        }),
        encoding="utf-8",
    )

    frame, evidence, blockers = VerifiedParquetTickSource(
        tmp_path,
        expected_symbol="XAUUSD",
    ).load_day(date(2026, 7, 27))

    assert frame.empty
    assert evidence is None
    assert blockers == ["tick_cache_hash_mismatch:2026-07-27"]


def test_loader_preserves_requested_and_confirmed_level_events(tmp_path):
    trade = _trade()
    trade["tickets"][0]["tp_history"].insert(
        0,
        {
            "ts": "2026-07-27T09:00:00.150+00:00",
            "status": "requested",
            "source": "provider TP1",
            "tp": 102.0,
        },
    )
    replay = _write_jsonl(tmp_path / "replay.jsonl", [trade])
    audit = _write_jsonl(
        tmp_path / "audit.jsonl",
        [{"sig_id": "canal1_1", "status": "exact", "blockers": []}],
    )

    dataset = load_dubai_dataset(
        replay_path=replay,
        audit_path=audit,
        market_ticks=FakeTickSource({"2026-07-27": _ticks()}),
        conversion_ticks=None,
        money_contract=_money_contract(),
    )

    assert [event.status for event in dataset.paths[0].legs[0].tp_events] == [
        "requested",
        "confirmed",
    ]


def test_provider_events_keep_telegram_actions_and_exclude_bot_execution(tmp_path):
    trade = _trade()
    trade["management"] = [
        {
            "ts": "2026-07-27T09:01:00.100+00:00",
            "raw_text": "Close now",
            "classified": "CLOSE_ALL",
            "confidence": 0.95,
        }
    ]
    trade["timeline"] = [
        {
            "ts": "2026-07-27T09:01:00.200+00:00",
            "ev": "mt5_close_requested",
            "ticket": 101,
        }
    ]
    replay = _write_jsonl(tmp_path / "replay.jsonl", [trade])
    audit = _write_jsonl(
        tmp_path / "audit.jsonl",
        [{"sig_id": "canal1_1", "status": "exact", "blockers": []}],
    )

    dataset = load_dubai_dataset(
        replay_path=replay,
        audit_path=audit,
        market_ticks=FakeTickSource({"2026-07-27": _ticks()}),
        conversion_ticks=None,
        money_contract=_money_contract(),
    )

    assert [event.action for event in dataset.paths[0].provider_events] == [
        "CLOSE_ALL"
    ]


def test_sell_path_exits_on_ask(tmp_path):
    replay = _write_jsonl(
        tmp_path / "replay.jsonl",
        [_trade(direction="SELL")],
    )
    audit = _write_jsonl(
        tmp_path / "audit.jsonl",
        [{"sig_id": "canal1_1", "status": "exact", "blockers": []}],
    )

    dataset = load_dubai_dataset(
        replay_path=replay,
        audit_path=audit,
        market_ticks=FakeTickSource({"2026-07-27": _ticks()}),
        conversion_ticks=None,
        money_contract=_money_contract(),
    )

    assert dataset.paths[0].exit_quotes.tolist() == dataset.paths[0].ask.tolist()


def test_loader_refuses_unverified_tick_contract(tmp_path):
    replay = _write_jsonl(tmp_path / "replay.jsonl", [_trade()])
    audit = _write_jsonl(
        tmp_path / "audit.jsonl",
        [{"sig_id": "canal1_1", "status": "exact", "blockers": []}],
    )
    market = FakeTickSource(
        blockers={"2026-07-27": ["invalid_tick_contract:2026-07-27"]}
    )

    dataset = load_dubai_dataset(
        replay_path=replay,
        audit_path=audit,
        market_ticks=market,
        conversion_ticks=None,
        money_contract=_money_contract(),
    )

    assert dataset.paths == ()
    assert dataset.exclusions == {"invalid_tick_contract": ("canal1_1",)}


def test_loader_requires_conversion_ticks_when_contract_is_not_identity(tmp_path):
    replay = _write_jsonl(tmp_path / "replay.jsonl", [_trade()])
    audit = _write_jsonl(
        tmp_path / "audit.jsonl",
        [{"sig_id": "canal1_1", "status": "exact", "blockers": []}],
    )
    contract = _money_contract()
    contract["conversion"] = {
        "orientation": "account_base_profit_quote",
        "symbol": "EURUSD",
        "max_quote_age_ms": 60_000,
    }

    dataset = load_dubai_dataset(
        replay_path=replay,
        audit_path=audit,
        market_ticks=FakeTickSource({"2026-07-27": _ticks()}),
        conversion_ticks=None,
        money_contract=contract,
    )

    assert dataset.exclusions == {"missing_conversion_ticks": ("canal1_1",)}


def test_sparse_conversion_marks_stale_ticks_without_dropping_path(tmp_path):
    replay = _write_jsonl(tmp_path / "replay.jsonl", [_trade()])
    audit = _write_jsonl(
        tmp_path / "audit.jsonl",
        [{"sig_id": "canal1_1", "status": "exact", "blockers": []}],
    )
    contract = _money_contract()
    contract["conversion"] = {
        "orientation": "account_base_profit_quote",
        "symbol": "EURUSD",
        "max_quote_age_ms": 5_000,
    }
    fx_ticks = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(
                ["2026-07-27T08:59:59Z", "2026-07-27T09:00:00.100Z"],
                format="mixed",
                utc=True,
            ),
            "bid": [1.10, 1.11],
            "ask": [1.11, 1.12],
        }
    )

    dataset = load_dubai_dataset(
        replay_path=replay,
        audit_path=audit,
        market_ticks=FakeTickSource({"2026-07-27": _ticks()}),
        conversion_ticks=FakeTickSource({"2026-07-27": fx_ticks}),
        money_contract=contract,
    )

    assert len(dataset.paths) == 1
    path = dataset.paths[0]
    assert path.fx_valid.tolist() == [True, True, True, False]
    assert path.fx_age_ms[-1] > 5_000
