from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import json

import pandas as pd

from research.gold_iterative.dataset import (
    load_gold_direct_dataset,
    load_gold_now_dataset,
)


class FakeTickSource:
    def __init__(
        self,
        frames=None,
        blockers=None,
        parquet_hash=None,
        evidence=None,
    ):
        self.frames = frames or {}
        self.blockers = blockers or {}
        self.parquet_hash = parquet_hash or "a" * 64
        self.evidence = evidence or {}

    def load_day(self, day: date):
        key = day.isoformat()
        if key in self.blockers:
            return pd.DataFrame(), None, list(self.blockers[key])
        day_evidence = {
            "day": key,
            "parquet_sha256": self.parquet_hash,
            **self.evidence.get(key, {}),
        }
        return (
            self.frames[key].copy(),
            day_evidence,
            [],
        )


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _replay(signal_id, *, source_kind="telegram_now", pnl="2.35"):
    return {
        "sig_id": signal_id,
        "channel": "canal2",
        "direction": "BUY",
        "signal_dt_utc": "2026-07-27T09:00:00+00:00",
        "pnl_real_mt5": pnl,
        "entry_provenance": {"source_kind": source_kind},
        "tickets": [
            {
                "ticket": 101,
                "volume": 0.01,
                "open_dt_utc": "2026-07-27T09:00:00+00:00",
                "open_price": 100.2,
                "close_dt_utc": "2026-07-27T09:05:00+00:00",
                "close_price": 102.0,
                "close_reason": "tp",
                "role": "market_a",
                "pnl_net": pnl,
                "fill_event": {
                    "ts": "2026-07-27T09:00:00.125+00:00",
                    "entry_source_kind": source_kind,
                },
                "tp_history": [],
                "sl_history": [],
            }
        ],
        "management": [],
    }


def _catalog_signal(
    signal_id,
    *,
    record_type="formal_signal",
    text="Buy Gold Now",
    execution_ids=None,
    signal_ts="2026-07-27T09:00:00+00:00",
):
    return {
        "provider_signal_id": signal_id,
        "record_type": record_type,
        "channel": "canal2",
        "signal_ts_utc": signal_ts,
        "first_observed_utc": signal_ts,
        "direction": "BUY",
        "effective_range": [99.0, 101.0],
        "effective_tps": [102.0, 103.0],
        "effective_sl": 98.0,
        "revisions": [
            {
                "observed_ts_utc": "2026-07-27T09:00:00.100+00:00",
                "telegram_ts_utc": "2026-07-27T09:00:00+00:00",
                "text": text,
                "parsed": {"direction": "BUY"},
            }
        ],
        "level_timeline": [
            {
                "telegram_ts_utc": "2026-07-27T09:00:02+00:00",
                "observed_ts_utc": "2026-07-27T09:00:05+00:00",
                "tps": [102.0, 103.0],
                "sl": 98.0,
            }
        ],
        "management_events": [
            {
                "telegram_ts_utc": "2026-07-27T09:01:00+00:00",
                "observed_ts_utc": "2026-07-27T09:01:03+00:00",
                "classified_action": "MOVE_SL_TO_BE",
                "text": "Move SL to BE",
            }
        ],
        "entry_contract": {
            "status": "ready",
            "trigger_telegram_utc": signal_ts,
            "trigger_observed_utc": "2026-07-27T09:00:00.100+00:00",
            "blockers": [],
        },
        "execution_sig_ids": list(execution_ids or ()),
        "semantic_status": "complete",
        "semantic_gaps": [],
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


def _swap_snapshot(captured_at_utc, *, include_post=True):
    from tools.capture_broker_money_contract import specification_sha256

    specification = {
        "swap_mode": 1,
        "swap_long": -10.0,
        "swap_short": 5.0,
        "swap_rollover3days": 3,
        "point": 0.01,
        "contract_size": 100.0,
        "currency_profit": "USD",
        "weekday_multipliers": {
            "sunday": 0.0,
            "monday": 1.0,
            "tuesday": 1.0,
            "wednesday": 3.0,
            "thursday": 1.0,
            "friday": 1.0,
            "saturday": 0.0,
        },
    }
    return {
        "captured_at_utc": captured_at_utc,
        "account_server": "VantageMarkets-Demo",
        "account_fingerprint": "a" * 64,
        "instrument_symbol": "XAUUSD",
        "time_evidence": {
            "source": "mql5_service_v1",
            "evidence_sha256": "b" * 64,
            "utc_offset_seconds": 10800,
        },
        "specification": specification,
        "specification_sha256": specification_sha256(specification),
    }


def _points_money_contract(*snapshots):
    return {
        "schema_version": 2,
        "account": {
            "server": "VantageMarkets-Demo",
            "fingerprint": "a" * 64,
            "currency": "USD",
            "currency_digits": 2,
        },
        "instrument": {
            "symbol": "XAUUSD",
            "trade_calc_mode": 4,
            "contract_size": 100.0,
            "tick_size": 0.01,
            "currency_profit": "USD",
        },
        "conversion": {
            "symbol": None,
            "orientation": "identity",
            "max_quote_age_ms": 5_000,
            "max_quote_interval_ms": 60_000,
        },
        "costs": {
            "commission_model": "observed_zero_intraday",
            "fee_model": "observed_zero_intraday",
            "swap_model": "mt5_points_rollover_v1",
            "rollover_clock": "broker_midnight",
            "snapshot_bracket_max_seconds": 900,
            "zero_multiplier_bracket_max_seconds": 72 * 3600,
        },
        "live_validation": {"valid": True},
        "swap_snapshots": list(snapshots),
    }


def _overnight_fixture(tmp_path, *, snapshots):
    fixture = _fixture(tmp_path)
    fixture["replay_path"] = _write_jsonl(tmp_path / "overnight_replay.jsonl", [])
    fixture["audit_path"] = _write_jsonl(tmp_path / "overnight_audit.jsonl", [])
    fixture["provider_catalog_path"] = _write_json(
        tmp_path / "overnight_catalog.json",
        {
            "schema_version": 7,
            "signals": [
                _catalog_signal(
                    "canal2_overnight",
                    signal_ts="2026-07-27T20:30:00+00:00",
                )
            ],
        },
    )
    fixture["market_ticks"] = FakeTickSource(
        {
            "2026-07-27": pd.DataFrame({
                "time_utc": pd.to_datetime(
                    [
                        "2026-07-27T20:30:00Z",
                        "2026-07-27T21:00:00Z",
                        "2026-07-27T23:59:59Z",
                    ],
                    utc=True,
                ),
                "bid": [100.0, 100.0, 100.0],
                "ask": [100.2, 100.2, 100.2],
            }),
            "2026-07-28": pd.DataFrame({
                "time_utc": pd.to_datetime(
                    ["2026-07-28T00:00:00Z", "2026-07-28T00:30:00Z"],
                    utc=True,
                ),
                "bid": [100.0, 100.0],
                "ask": [100.2, 100.2],
            }),
        },
        evidence={
            "2026-07-27": {"utc_offset_seconds": 10800},
            "2026-07-28": {"utc_offset_seconds": 10800},
        },
    )
    fixture["money_contract"] = _points_money_contract(*snapshots)
    return fixture


def _fixture(tmp_path):
    replay = _write_jsonl(
        tmp_path / "replay.jsonl",
        [
            _replay("canal2_10", pnl="2.35"),
            _replay("canal2_11", pnl="-1.15"),
            _replay("canal2_14", source_kind="zone_first_touch"),
        ],
    )
    audit = _write_jsonl(
        tmp_path / "audit.jsonl",
        [
            {"sig_id": "canal2_10", "status": "exact", "blockers": []},
            {"sig_id": "canal2_11", "status": "blocked", "blockers": ["gap"]},
            {"sig_id": "canal2_14", "status": "exact", "blockers": []},
        ],
    )
    catalog = _write_json(
        tmp_path / "provider_catalog.json",
        {
            "schema_version": 7,
            "signals": [
                _catalog_signal("canal2_10", execution_ids=("canal2_10",)),
                _catalog_signal("canal2_11", execution_ids=("canal2_11",)),
                _catalog_signal("canal2_12"),
                _catalog_signal(
                    "canal2_13",
                    record_type="zone_plan",
                    text="Gold Buy Zone 100 - 99",
                ),
                _catalog_signal("canal2_14", execution_ids=("canal2_14",)),
            ],
        },
    )
    raw_events = _write_jsonl(
        tmp_path / "events.jsonl",
        [{"ev": "channel_msg", "sig": "canal2_10"}],
    )
    return {
        "replay_path": replay,
        "audit_path": audit,
        "provider_catalog_path": catalog,
        "raw_events_path": raw_events,
        "market_ticks": FakeTickSource({"2026-07-27": _ticks()}),
        "conversion_ticks": None,
        "money_contract": _money_contract(),
        "from_date": "2026-07-27",
        "to_date": "2026-07-27",
    }


def test_gold_loader_accounts_for_every_formal_now_signal(tmp_path):
    dataset = load_gold_now_dataset(**_fixture(tmp_path))

    assert dataset.eligible_signal_ids == (
        "canal2_10",
        "canal2_11",
        "canal2_12",
        "canal2_14",
    )
    assert [path.signal_id for path in dataset.paths] == [
        "canal2_10",
        "canal2_11",
        "canal2_12",
        "canal2_14",
    ]
    assert dataset.exclusions["actual_tick_replay_blocked"] == ("canal2_11",)
    assert dataset.exclusions["actual_evidence_missing"] == ("canal2_12",)
    assert dataset.exclusions["actual_entry_source_kind_mismatch"] == (
        "canal2_14",
    )
    assert dataset.eligible_signal_days == {
        "canal2_10": "2026-07-27",
        "canal2_11": "2026-07-27",
        "canal2_12": "2026-07-27",
        "canal2_14": "2026-07-27",
    }
    assert "canal2_13" not in {
        signal_id
        for signal_ids in dataset.exclusions.values()
        for signal_id in signal_ids
    }


def test_gold_loader_builds_provider_path_from_telegram_not_mt5(tmp_path):
    fixture = _fixture(tmp_path)

    dataset = load_gold_now_dataset(**fixture)

    path = next(item for item in dataset.paths if item.signal_id == "canal2_10")
    assert path.entry_evidence_kind == "provider_telegram"
    assert path.signal_observed_at.isoformat() == "2026-07-27T09:00:00+00:00"
    assert path.actual_pnl_eur == Decimal("2.35")
    assert path.legs[0].opened_at.isoformat() == "2026-07-27T09:00:00+00:00"
    assert path.legs[0].open_price == 100.0
    assert [item.level for item in path.legs[0].tp_events] == [102.0]
    assert [item.level for item in path.legs[1].tp_events] == [103.0]
    assert path.legs[0].tp_events[0].observed_at.isoformat() == (
        "2026-07-27T09:00:02+00:00"
    )
    assert path.provider_events[0].observed_at.isoformat() == (
        "2026-07-27T09:01:00+00:00"
    )


def test_gold_loader_simulates_signal_without_actual_execution(tmp_path):
    dataset = load_gold_now_dataset(**_fixture(tmp_path))

    path = next(item for item in dataset.paths if item.signal_id == "canal2_12")
    assert path.actual_pnl_eur is None
    assert path.entry_evidence_kind == "provider_telegram"


def test_gold_loader_requires_literal_now_semantics(tmp_path):
    fixture = _fixture(tmp_path)
    catalog = json.loads(fixture["provider_catalog_path"].read_text(encoding="utf-8"))
    catalog["signals"].append(
        _catalog_signal(
            "canal2_15",
            text="Gold Buy Zone 100 - 99",
            execution_ids=("canal2_15",),
        )
    )
    fixture["provider_catalog_path"].write_text(json.dumps(catalog), encoding="utf-8")

    dataset = load_gold_now_dataset(**fixture)

    assert "canal2_15" not in dataset.eligible_signal_ids


def test_gold_direct_loader_adds_explicit_priced_entries_without_mixing_zones(
    tmp_path,
):
    fixture = _fixture(tmp_path)
    catalog = json.loads(fixture["provider_catalog_path"].read_text(encoding="utf-8"))
    direct = _catalog_signal(
        "canal2_15",
        text="Very high risk buy\n\n100\n\nHave your SL at 98",
        execution_ids=(),
    )
    direct["effective_range"] = [100.0, 100.0]
    direct["effective_tps"] = []
    direct["effective_sl"] = 98.0
    direct["semantic_status"] = "incomplete"
    direct["semantic_gaps"] = ["missing_tps"]
    direct["entry_contract"]["trigger_kind"] = "direct_priced_text"
    zone = _catalog_signal(
        "canal2_16",
        record_type="zone_plan",
        text="Buy zone 100 - 99",
        execution_ids=(),
    )
    catalog["signals"].extend((direct, zone))
    fixture["provider_catalog_path"].write_text(
        json.dumps(catalog),
        encoding="utf-8",
    )

    now_dataset = load_gold_now_dataset(**fixture)
    direct_dataset = load_gold_direct_dataset(**fixture)

    assert "canal2_15" not in now_dataset.eligible_signal_ids
    assert "canal2_15" in direct_dataset.eligible_signal_ids
    assert "canal2_16" not in direct_dataset.eligible_signal_ids
    direct_path = next(
        path for path in direct_dataset.paths if path.signal_id == "canal2_15"
    )
    assert direct_path.legs[0].opened_at.isoformat() == (
        "2026-07-27T09:00:00+00:00"
    )
    assert direct_path.legs[0].open_price == 100.0
    assert direct_path.legs[0].sl_events[0].level == 98.0


def test_gold_manifest_binds_catalog_and_raw_causal_events(tmp_path):
    fixture = _fixture(tmp_path)
    dataset = load_gold_now_dataset(**fixture)

    assert dataset.source_hashes["provider_catalog"] == hashlib.sha256(
        fixture["provider_catalog_path"].read_bytes()
    ).hexdigest()
    assert dataset.source_hashes["raw_events"] == hashlib.sha256(
        fixture["raw_events_path"].read_bytes()
    ).hexdigest()
    assert dataset.source_hashes["dataset_contract"]


def test_gold_loader_builds_exact_rollover_lookup_from_broker_evidence(tmp_path):
    fixture = _overnight_fixture(
        tmp_path,
        snapshots=(
            _swap_snapshot("2026-07-27T20:55:00+00:00"),
            _swap_snapshot("2026-07-27T21:05:00+00:00"),
        ),
    )

    dataset = load_gold_now_dataset(**fixture)

    assert len(dataset.paths) == 1
    event = dataset.paths[0].rollover_events[0]
    assert event.observed_at.isoformat() == "2026-07-27T21:00:00+00:00"
    assert event.blocker is None
    assert event.minor_by_volume_unit[0] == 0
    assert event.minor_by_volume_unit[1] == -10
    assert event.minor_by_volume_unit[7] == -70


def test_gold_loader_keeps_path_and_blocks_only_unknown_rollover(tmp_path):
    fixture = _overnight_fixture(
        tmp_path,
        snapshots=(
            _swap_snapshot("2026-07-27T20:55:00+00:00"),
        ),
    )

    dataset = load_gold_now_dataset(**fixture)

    assert len(dataset.paths) == 1
    event = dataset.paths[0].rollover_events[0]
    assert event.observed_at.isoformat() == "2026-07-27T21:00:00+00:00"
    assert event.blocker == (
        "missing_swap_rollover_bracket:2026-07-27T21:00:00+00:00"
    )


def test_gold_loader_keeps_first_executable_tick_after_strategy_horizon(tmp_path):
    fixture = _overnight_fixture(
        tmp_path,
        snapshots=(
            _swap_snapshot("2026-07-27T20:55:00+00:00"),
        ),
    )
    fixture["max_hold_minutes"] = 30
    fixture["market_ticks"].frames["2026-07-27"] = pd.DataFrame({
        "time_utc": pd.to_datetime(
            [
                "2026-07-27T20:30:00Z",
                "2026-07-27T20:59:59Z",
                "2026-07-27T21:02:00Z",
            ],
            utc=True,
        ),
        "bid": [100.0, 100.0, 100.0],
        "ask": [100.2, 100.2, 100.2],
    })

    dataset = load_gold_now_dataset(**fixture)

    path = dataset.paths[0]
    assert pd.Timestamp(path.times_ns[-1], tz="UTC").isoformat() == (
        "2026-07-27T21:02:00+00:00"
    )
    assert path.rollover_events[0].observed_at.isoformat() == (
        "2026-07-27T21:00:00+00:00"
    )
