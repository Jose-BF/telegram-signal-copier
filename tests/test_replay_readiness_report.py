import json
from datetime import date

import pandas as pd

import replay_readiness_report
from tools import ensure_replay_tick_cache


def _ticket(ticket=101):
    return {
        "ticket": ticket,
        "open_dt_utc": "2026-07-06T10:00:00+00:00",
        "open_price": 4200.0,
        "close_dt_utc": "2026-07-06T10:05:00+00:00",
        "close_price": 4202.0,
        "is_closed": True,
        "pnl_net": 1.75,
        "pnl_components": {
            "profit": 1.80,
            "swap": 0.0,
            "commission": -0.05,
            "fee": 0.0,
            "net": 1.75,
        },
        "open_deal": {"ticket": 9001, "time_msc": 1783076049123},
        "close_deal": {"ticket": 9002, "time_msc": 1783076349123},
        "sl_history": [{"ts": "2026-07-06T10:00:02+00:00", "sl": 4190.0}],
        "tp_history": [{"ts": "2026-07-06T10:00:02+00:00", "tp": 4202.0}],
    }


def _trade(**overrides):
    base = {
        "sig_id": "canal1_1",
        "channel": "canal1",
        "direction": "BUY",
        "signal_dt_utc": "2026-07-06T09:59:58+00:00",
        "open_dt_utc": "2026-07-06T10:00:00+00:00",
        "close_dt_utc": "2026-07-06T10:05:00+00:00",
        "status": "closed",
        "tickets": [_ticket()],
    }
    base.update(overrides)
    return base


def _audit(**overrides):
    base = {
        "sig_id": "canal1_1",
        "status": "exact",
        "diff": 0.0,
        "assumptions": [],
    }
    base.update(overrides)
    return base


def _observed(**overrides):
    base = {
        "sig_id": "canal1_1",
        "status": "exact",
        "validation_contract": "causal_path_v2",
        "fill_price_authority": "mt5_deals",
        "market_session_contract": "vantage_xauusd_standard_v1",
        "blockers": [],
        "tickets": [],
    }
    base.update(overrides)
    return base


def _write_valid_tick_day(cache_dir, day="2026-07-06"):
    cache_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([{
        "time_utc": pd.Timestamp(f"{day}T10:00:00+00:00"),
        "time_msc": int(
            pd.Timestamp(f"{day}T10:00:00+00:00").timestamp() * 1000),
        "bid": 4199.8,
        "ask": 4200.0,
    }])
    frame.to_parquet(cache_dir / f"{day}.parquet", index=False)
    ensure_replay_tick_cache.write_day_contract(
        cache_dir,
        date.fromisoformat(day),
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


def _set_partial_coverage(cache_dir, day, complete_through):
    contract_path = cache_dir / f"{day}.parquet.meta.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["coverage"] = {
        "source_query_start_utc": f"{day}T00:00:00+00:00",
        "source_query_end_utc": "2026-07-07T00:00:00+00:00",
        "captured_at_utc": complete_through,
        "first_tick_utc": f"{day}T00:00:00+00:00",
        "last_tick_utc": complete_through,
        "complete_from_utc": f"{day}T00:00:00+00:00",
        "complete_through_utc": complete_through,
        "row_count": 1,
    }
    contract_path.write_text(json.dumps(contract), encoding="utf-8")


def test_trade_is_ready_when_core_replay_inputs_exist(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    _write_valid_tick_day(cache_dir)

    row = replay_readiness_report.assess_trade(
        _trade(), _audit(), _observed(), cache_dir=cache_dir, pad_minutes=0)

    assert row["ready"] is True
    assert row["status"] == "ready"
    assert row["blockers"] == []
    assert row["tick_days"] == ["2026-07-06"]


def test_missing_tick_cache_blocks_full_replay(tmp_path):
    row = replay_readiness_report.assess_trade(
        _trade(),
        _audit(),
        _observed(),
        cache_dir=tmp_path / "ticks_cache",
        pad_minutes=0,
    )

    assert row["ready"] is False
    assert row["status"] == "blocked"
    assert "missing_tick_cache:2026-07-06" in row["blockers"]


def test_incomplete_tick_cache_coverage_blocks_full_replay(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    _write_valid_tick_day(cache_dir)
    _set_partial_coverage(
        cache_dir,
        "2026-07-06",
        "2026-07-06T10:02:00+00:00",
    )

    row = replay_readiness_report.assess_trade(
        _trade(close_dt_utc="2026-07-06T10:05:00+00:00"),
        _audit(),
        _observed(),
        cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert row["ready"] is False
    assert row["status"] == "blocked"
    assert "incomplete_tick_cache_coverage:2026-07-06" in row["blockers"]


def test_missing_deal_detail_blocks_full_replay(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    _write_valid_tick_day(cache_dir)
    ticket = _ticket()
    ticket.pop("open_deal")

    row = replay_readiness_report.assess_trade(
        _trade(tickets=[ticket]),
        _audit(),
        _observed(),
        cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert row["ready"] is False
    assert "missing_ticket_open_deal:101" in row["blockers"]


def test_reconstructed_close_event_warns_instead_of_blocking(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    _write_valid_tick_day(cache_dir)
    ticket = _ticket()
    ticket["close_deal"] = None
    ticket["close_event"] = {
        "ev": "positions_closed_by_mt5",
        "ts": "2026-07-06T10:05:00+00:00",
        "closed_by_tag": "TP1",
    }

    row = replay_readiness_report.assess_trade(
        _trade(tickets=[ticket]),
        _audit(status="reconstructed", assumptions=["mt5_closure_event_fallback"]),
        _observed(),
        cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert row["ready"] is True
    assert "missing_ticket_close_deal:101" not in row["blockers"]
    assert "ticket_close_deal_reconstructed:101" in row["warnings"]
    assert "accounting_reconstructed" in row["warnings"]
    assert row["accounting_money_exact"] is True


def test_existing_parquet_without_valid_utc_contract_is_blocked(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    pd.DataFrame([{"bid": 4200.0, "ask": 4200.2}]).to_parquet(
        cache_dir / "2026-07-06.parquet", index=False)

    row = replay_readiness_report.assess_trade(
        _trade(), _audit(), _observed(), cache_dir=cache_dir, pad_minutes=0)

    assert row["status"] == "blocked"
    assert "invalid_tick_cache_contract:2026-07-06" in row["blockers"]


def test_observed_path_mismatch_blocks_strict_replay(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    _write_valid_tick_day(cache_dir)

    row = replay_readiness_report.assess_trade(
        _trade(),
        _audit(),
        _observed(status="mismatch", blockers=["first_touch_time_mismatch"]),
        cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert row["status"] == "blocked"
    assert "observed_path_status:mismatch" in row["blockers"]
    assert "observed:first_touch_time_mismatch" in row["blockers"]


def test_exact_observed_path_without_market_session_contract_is_blocked(
    tmp_path,
):
    cache_dir = tmp_path / "ticks_cache"
    _write_valid_tick_day(cache_dir)
    observed = _observed()
    observed.pop("market_session_contract")

    row = replay_readiness_report.assess_trade(
        _trade(),
        _audit(),
        observed,
        cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert row["status"] == "blocked"
    assert "observed_market_session_contract_unverified" in row["blockers"]


def test_unknown_accounting_status_cannot_be_ready(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    _write_valid_tick_day(cache_dir)

    row = replay_readiness_report.assess_trade(
        _trade(),
        _audit(status="unknown"),
        _observed(),
        cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert row["status"] == "blocked"
    assert row["accounting_money_exact"] is False
    assert "accounting_money_not_exact" in row["blockers"]


def test_open_trade_is_pending_instead_of_failed(tmp_path):
    ticket = _ticket()
    for field in ("close_dt_utc", "close_price", "pnl_net", "pnl_components", "close_deal"):
        ticket.pop(field)
    ticket["is_closed"] = False

    row = replay_readiness_report.assess_trade(
        _trade(status="open", close_dt_utc=None, tickets=[ticket]),
        None,
        None,
        cache_dir=tmp_path / "ticks_cache",
        pad_minutes=0,
    )

    assert row["status"] == "pending"
    assert row["ready"] is False
    assert row["blockers"] == []


def test_cli_writes_weekly_readiness_report(tmp_path):
    replay_path = tmp_path / "replay_trades.jsonl"
    audit_path = tmp_path / "accounting_replay_audit.jsonl"
    output_path = tmp_path / "replay_readiness_report.json"
    observed_path = tmp_path / "observed_tick_replay_audit.jsonl"
    cache_dir = tmp_path / "ticks_cache"
    _write_valid_tick_day(cache_dir)
    old_trade = _trade(
        sig_id="canal1_old",
        signal_dt_utc="2026-07-05T09:59:58+00:00",
    )
    replay_path.write_text(
        json.dumps(old_trade) + "\n" + json.dumps(_trade()) + "\n",
        encoding="utf-8",
    )
    audit_path.write_text(
        json.dumps(_audit(sig_id="canal1_old")) + "\n"
        + json.dumps(_audit()) + "\n",
        encoding="utf-8",
    )
    observed_path.write_text(
        json.dumps(_observed(sig_id="canal1_old")) + "\n"
        + json.dumps(_observed()) + "\n",
        encoding="utf-8",
    )

    exit_code = replay_readiness_report.main([
        "--replay",
        str(replay_path),
        "--audit",
        str(audit_path),
        "--observed-audit",
        str(observed_path),
        "--output",
        str(output_path),
        "--tick-cache-dir",
        str(cache_dir),
        "--pad-minutes",
        "0",
        "--since",
        "2026-07-06",
        "--quiet",
    ])

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["summary"]["total"] == 1
    assert report["summary"]["ready"] == 1
    assert report["summary"]["pending"] == 0
    assert report["summary"]["blocked"] == 0
    assert report["scope"] == {
        "since": "2026-07-06",
        "until": None,
        "input_trades": 2,
        "selected_trades": 1,
    }
    assert report["days"] == [{
        "date": "2026-07-06",
        "status": "ready",
        "total": 1,
        "ready": 1,
        "pending": 0,
        "blocked": 0,
    }]
    assert report["trades"][0]["sig_id"] == "canal1_1"
