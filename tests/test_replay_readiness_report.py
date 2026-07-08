import json

import replay_readiness_report


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


def test_trade_is_ready_when_core_replay_inputs_exist(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    (cache_dir / "2026-07-06.parquet").write_bytes(b"ticks")

    row = replay_readiness_report.assess_trade(
        _trade(), _audit(), cache_dir=cache_dir, pad_minutes=0)

    assert row["ready"] is True
    assert row["status"] == "ready"
    assert row["blockers"] == []
    assert row["tick_days"] == ["2026-07-06"]


def test_missing_tick_cache_blocks_full_replay(tmp_path):
    row = replay_readiness_report.assess_trade(
        _trade(), _audit(), cache_dir=tmp_path / "ticks_cache", pad_minutes=0)

    assert row["ready"] is False
    assert row["status"] == "blocked"
    assert "missing_tick_cache:2026-07-06" in row["blockers"]


def test_missing_deal_detail_blocks_full_replay(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    (cache_dir / "2026-07-06.parquet").write_bytes(b"ticks")
    ticket = _ticket()
    ticket.pop("open_deal")

    row = replay_readiness_report.assess_trade(
        _trade(tickets=[ticket]), _audit(), cache_dir=cache_dir, pad_minutes=0)

    assert row["ready"] is False
    assert "missing_ticket_open_deal:101" in row["blockers"]


def test_reconstructed_close_event_warns_instead_of_blocking(tmp_path):
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    (cache_dir / "2026-07-06.parquet").write_bytes(b"ticks")
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
        cache_dir=cache_dir,
        pad_minutes=0,
    )

    assert row["ready"] is True
    assert "missing_ticket_close_deal:101" not in row["blockers"]
    assert "ticket_close_deal_reconstructed:101" in row["warnings"]
    assert "accounting_reconstructed" in row["warnings"]


def test_cli_writes_weekly_readiness_report(tmp_path):
    replay_path = tmp_path / "replay_trades.jsonl"
    audit_path = tmp_path / "accounting_replay_audit.jsonl"
    output_path = tmp_path / "replay_readiness_report.json"
    cache_dir = tmp_path / "ticks_cache"
    cache_dir.mkdir()
    (cache_dir / "2026-07-06.parquet").write_bytes(b"ticks")
    replay_path.write_text(json.dumps(_trade()) + "\n", encoding="utf-8")
    audit_path.write_text(json.dumps(_audit()) + "\n", encoding="utf-8")

    exit_code = replay_readiness_report.main([
        "--replay",
        str(replay_path),
        "--audit",
        str(audit_path),
        "--output",
        str(output_path),
        "--tick-cache-dir",
        str(cache_dir),
        "--pad-minutes",
        "0",
        "--quiet",
    ])

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["summary"]["total"] == 1
    assert report["summary"]["ready"] == 1
    assert report["trades"][0]["sig_id"] == "canal1_1"
