from __future__ import annotations

from datetime import date
import hashlib
import json

import pandas as pd

from research.gold_iterative.dataset import load_gold_now_dataset


class FakeTickSource:
    def __init__(self, frames=None, blockers=None, parquet_hash=None):
        self.frames = frames or {}
        self.blockers = blockers or {}
        self.parquet_hash = parquet_hash or "a" * 64

    def load_day(self, day: date):
        key = day.isoformat()
        if key in self.blockers:
            return pd.DataFrame(), None, list(self.blockers[key])
        return (
            self.frames[key].copy(),
            {"day": key, "parquet_sha256": self.parquet_hash},
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
        "revisions": [
            {
                "observed_ts_utc": "2026-07-27T09:00:00.100+00:00",
                "telegram_ts_utc": "2026-07-27T09:00:00+00:00",
                "text": text,
                "parsed": {"direction": "BUY"},
            }
        ],
        "management_events": [],
        "entry_contract": {"status": "ready", "blockers": []},
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
    assert [path.signal_id for path in dataset.paths] == ["canal2_10"]
    assert dataset.exclusions["tick_replay_blocked"] == ("canal2_11",)
    assert dataset.exclusions["actual_evidence_missing"] == ("canal2_12",)
    assert dataset.exclusions["entry_source_kind_mismatch"] == ("canal2_14",)
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
