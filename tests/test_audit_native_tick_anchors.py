import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from tools.audit_native_tick_anchors import audit, digest


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def inputs(tmp_path):
    raw = tmp_path / "raw"
    start = datetime(2026, 9, 17, tzinfo=timezone.utc)
    msc = int(start.timestamp() * 1000)
    save(raw / "contract.json", {"schema_version": "raw_isolated_mt5_history_v1",
        "source_epoch_start": start.isoformat(), "source_epoch_end_exclusive":
        datetime(2026, 9, 18, tzinfo=timezone.utc).isoformat(), "server": "demo",
        "clock_admitted": False, "engine_dataset_ready": False})
    save(raw / "binding.json", {"server": "demo", "currency": "EUR",
        "account_binding_sha256": "bound", "trade_allowed": False, "tradeapi_disabled": True})
    frame = pd.DataFrame({"time_msc": [msc + 1000, msc + 2000, msc + 3000],
                          "bid": [100., 100.1, 100.2], "ask": [100.2, 100.3, 100.4]})
    for symbol in ("XAUUSD", "EURUSD"):
        tape = raw / symbol / "2026-09-17.parquet"
        tape.parent.mkdir(parents=True)
        frame.to_parquet(tape, index=False)
        save(tape.with_suffix(".json"), {"status": "raw_reads_consistent", "reads_consistent": True,
            "symbol": symbol, "server": "demo", "clock_admitted": False,
            "rows": len(frame), "artifact": tape.name, "sha256": digest(tape), "bytes": tape.stat().st_size})
    deals = tmp_path / "native-deals.json"
    save(deals, {"server": "demo", "currency": "EUR", "account_binding_sha256": "bound",
        "deals": [{"ticket": 1, "position_id": 1001, "time_msc": msc + 2050,
                   "symbol": "XAUUSD", "entry": 0, "type": 0, "reason": 3, "price": 100.35},
                  {"ticket": 2, "position_id": 1001, "time_msc": msc + 3050,
                   "symbol": "XAUUSD", "entry": 1, "type": 1, "reason": 4, "price": 100.15}]})
    baskets = tmp_path / "baskets.json"
    save(baskets, {"currency": "EUR", "source_sha256": digest(deals),
        "baskets": [{"signal_id": "canal2_1", "channel": "canal2", "positions": 1}],
        "positions": [{"signal_id": "canal2_1", "first_native_msc": msc + 2050}]})
    inventory = tmp_path / "inventory.json"
    save(inventory, {"scope": {"since_utc": start.isoformat()}, "signals": [
        {"signal_id": "canal2_1", "receipt": {"observed": True,
            "event_utc": datetime.fromtimestamp((msc - 3600_000 + 50) / 1000, timezone.utc).isoformat()}}]})
    return raw, deals, baskets, inventory


def test_native_tick_audit_preserves_causal_quotes_and_offset_hypotheses(tmp_path):
    args = inputs(tmp_path)
    report = audit(*args)
    assert report["status"] == "diagnostic_only"
    assert report["clock_admitted"] is False and report["engine_dataset_ready"] is False
    assert report["scope"]["verified_symbol_days"] == {"XAUUSD": 1, "EURUSD": 1}
    assert report["deal_summary"]["native_xauusd"] == report["deal_summary"]["causal_prior"] == 2
    assert [row["prior_age_ms"] for row in report["deals"]] == [50, 50]
    assert [row["fill_minus_prior"] for row in report["deals"]] == [.05, -.05]
    assert [(row["causal_minute_low"], row["causal_minute_high"]) for row in report["deals"]] == [
        (100.2, 100.3), (100., 100.2)]
    assert [row["reason"] for row in report["deals"]] == [3, 4]
    assert report["offset_checks"]["3600"]["residual_ms"]["median"] == 2000
    assert report["offset_checks"]["10800"]["negative_before_receipt"] == 1
    assert len(report["receipts"]) == 1 and not report["missing_receipts"]


def test_native_tick_audit_rejects_unbound_or_changed_inputs(tmp_path):
    raw, deals, baskets, inventory = inputs(tmp_path)
    value = json.loads(baskets.read_text())
    value["source_sha256"] = "0" * 64
    save(baskets, value)
    with pytest.raises(ValueError, match="identity"):
        audit(raw, deals, baskets, inventory)
    value["source_sha256"] = digest(deals)
    save(baskets, value)
    frame = pd.read_parquet(raw / "EURUSD" / "2026-09-17.parquet")
    frame.loc[0, "ask"] = 999.
    frame.to_parquet(raw / "EURUSD" / "2026-09-17.parquet", index=False)
    with pytest.raises(ValueError, match="bytes changed"):
        audit(raw, deals, baskets, inventory)


def test_missing_source_day_remains_in_denominator(tmp_path):
    raw, deals, baskets, inventory = inputs(tmp_path)
    value = json.loads(deals.read_text())
    value["deals"].append({"ticket": 3, "position_id": 1002, "time_msc":
        value["deals"][0]["time_msc"] + 86_400_000,
        "symbol": "XAUUSD", "entry": 0, "type": 0, "price": 101.})
    save(deals, value)
    linked = json.loads(baskets.read_text())
    linked["source_sha256"] = digest(deals)
    save(baskets, linked)
    report = audit(raw, deals, baskets, inventory)
    assert report["deal_summary"]["native_xauusd"] == 3
    assert report["deal_summary"]["causal_prior"] == 2
    assert report["deal_blockers"] == {"source_epoch_day_not_extracted": 1}


def test_independent_clock_anchor_is_reported_without_admitting_utc(tmp_path):
    args = inputs(tmp_path)
    msc = int(datetime(2026, 9, 17, tzinfo=timezone.utc).timestamp() * 1000)
    gmt_epoch = msc // 1000 - 10_800 + 2
    clock = tmp_path / "clock.json"
    save(clock, {"schema_version": 2, "account": {"server": "demo", "currency": "EUR"},
        "instrument": {"symbol": "XAUUSD"}, "swap_snapshots": [{
            "account_server": "demo", "instrument_symbol": "XAUUSD",
            "captured_at_utc": datetime.fromtimestamp(gmt_epoch, timezone.utc).isoformat(),
            "time_evidence": {"source": "mql5_service_v1", "captured_server_epoch": msc // 1000 + 2,
                "captured_gmt_epoch": gmt_epoch, "utc_offset_seconds": 10_800,
                "last_server_tick_epoch": msc // 1000 + 2, "server_tick_lag_seconds": 0,
                "evidence_age_seconds": 4, "evidence_sha256": "evidence",
                "market_session_open": True, "mql_tick_fresh": True,
                "python_tick_time_basis": "broker_server_epoch", "python_tick_epoch": msc // 1000 + 2}}
        ]})
    report = audit(*args, clock_contract_path=clock)
    day = report["independent_clock_evidence"]["days"]["2026-09-17"]
    assert day["status"] == "direct_anchor_available"
    assert day["offset_seconds"] == 10_800
    assert day["anchors"][0]["raw_tick_age_ms_at_python_second_end"] == 999
    assert report["clock_admitted"] is False
    assert report["engine_dataset_ready"] is False

    value = json.loads(clock.read_text())
    value["swap_snapshots"][0]["time_evidence"]["utc_offset_seconds"] = 7200
    save(clock, value)
    with pytest.raises(ValueError, match="contradictory broker clock"):
        audit(*args, clock_contract_path=clock)


def test_duplicate_inventory_signal_cannot_be_silently_collapsed(tmp_path):
    raw, deals, baskets, inventory = inputs(tmp_path)
    value = json.loads(inventory.read_text())
    value["signals"].append(value["signals"][0])
    save(inventory, value)
    with pytest.raises(ValueError, match="duplicate signal"):
        audit(raw, deals, baskets, inventory)
