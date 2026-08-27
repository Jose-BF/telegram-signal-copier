import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import config
import dubai_live_candidate
import executor
import listener
import main
import position_lifecycle_monitor
import state as state_module
from live_auditor import AuditSettings, LiveAuditor
from state import Signal, StateManager


def _candidate_event(signal_id, first_fill_at):
    policy = dubai_live_candidate.DubaiLivePolicy()
    legs = policy.entry_plan(
        direction="BUY",
        anchor_price=4200.0,
        opened_at=first_fill_at,
    )
    return {
        "sig": signal_id,
        "ev": "dubai_live_candidate_attached",
        "strategy_id": dubai_live_candidate.CANDIDATE_ID,
        "strategy_fingerprint": policy.fingerprint,
        "entry_anchor": 4200.0,
        "first_fill_at": first_fill_at.isoformat(timespec="milliseconds"),
        "entry_expires_at": legs[0].expires_at.isoformat(
            timespec="milliseconds"
        ),
        "entry_legs": [
            {
                "index": leg.index,
                "volume": leg.volume,
                "trigger_price": leg.trigger_price,
            }
            for leg in legs
        ],
    }


def test_account_evidence_exposes_mt5_trade_mode(monkeypatch):
    monkeypatch.setattr(
        executor.mt5,
        "account_info",
        lambda: SimpleNamespace(
            login=123,
            server="Vantage-Demo",
            name="Demo",
            currency="EUR",
            balance=10000.0,
            equity=9999.0,
            trade_mode=0,
        ),
    )

    evidence = executor.account_evidence()

    assert evidence["trade_mode"] == 0
    assert evidence["trade_mode_name"] == "demo"


def test_candidate_startup_accepts_demo_and_rejects_real_or_unknown(monkeypatch):
    monkeypatch.setattr(config, "STRATEGY_C1_BALANCED_V1_ENABLED", True)

    main._assert_dubai_candidate_demo_account({
        "trade_mode": 0,
        "trade_mode_name": "demo",
        "currency": "EUR",
    })
    with pytest.raises(ValueError, match="demo"):
        main._assert_dubai_candidate_demo_account({
            "trade_mode": 2,
            "trade_mode_name": "real",
            "currency": "EUR",
        })
    with pytest.raises(ValueError, match="verificar"):
        main._assert_dubai_candidate_demo_account({})
    with pytest.raises(ValueError, match="EUR"):
        main._assert_dubai_candidate_demo_account({
            "trade_mode": 0,
            "trade_mode_name": "demo",
            "currency": "USD",
        })


def test_candidate_startup_requires_every_frozen_lot_to_fit_broker_contract(
    monkeypatch,
):
    monkeypatch.setattr(config, "STRATEGY_C1_BALANCED_V1_ENABLED", True)

    main._assert_dubai_candidate_broker_volume(SimpleNamespace(
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    ))
    with pytest.raises(ValueError, match="volumen"):
        main._assert_dubai_candidate_broker_volume(SimpleNamespace(
            volume_min=0.05,
            volume_max=100.0,
            volume_step=0.01,
        ))
    monkeypatch.setattr(
        main.executor.mt5, "symbol_info", lambda _symbol: None,
    )
    with pytest.raises(ValueError, match="verificar"):
        main._assert_dubai_candidate_broker_volume(None)


def test_resync_restores_candidate_and_only_the_missing_unexpired_leg(
    monkeypatch,
    tmp_path,
):
    first_fill_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        minutes=5
    )
    signal_id = "canal1_26001"
    rows = [
        _candidate_event(signal_id, first_fill_at),
        {"sig": signal_id, "ev": "market_filled", "ticket": 9001},
        {
            "sig": signal_id,
            "ev": "dca_filled",
            "ticket": 9002,
            "candidate_leg_index": 1,
        },
    ]
    events_file = tmp_path / "trade_events.jsonl"
    events_file.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    groups = {
        signal_id: {
            "channel": "canal1",
            "message_id": 26001,
            "direction": "BUY",
            "market_ticket": 9001,
            "market_price": 4200.0,
            "market_sl": 0.0,
            "market_tp": 0.0,
            "position_entries": {9001: 4200.0, 9002: 4196.0},
            "position_stops": {9001: 4191.25, 9002: 4191.25},
            "market_open_time": int(first_fill_at.replace(
                tzinfo=timezone.utc
            ).timestamp()),
            "extra_market_tickets": [],
            "double_market_tickets": [],
            "scale_out_leg_indexes": {},
            "dca_tickets": [9002],
        }
    }
    st = StateManager()
    started = []
    monkeypatch.setattr(
        main.executor, "list_open_positions_grouped", lambda: groups,
    )
    monkeypatch.setattr(
        main.causal_trace,
        "load_signal_origin_index",
        lambda _path: ({}, {}, []),
    )
    monkeypatch.setattr(main.journal, "EVENTS_FILE", events_file)
    monkeypatch.setattr(main.journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(state_module, "state", st)
    monkeypatch.setattr(
        position_lifecycle_monitor,
        "start",
        lambda signal, levels: started.append((signal, levels)),
    )
    monkeypatch.setattr(
        main.executor,
        "mt5",
        SimpleNamespace(symbol_info_tick=lambda _symbol: None),
    )

    main._resync_orphan_positions()

    signal = st.get("canal1", 26001)
    assert signal is not None
    assert signal.live_strategy_id == dubai_live_candidate.CANDIDATE_ID
    assert signal.live_strategy_fingerprint == (
        dubai_live_candidate.CANDIDATE_FINGERPRINT
    )
    assert signal.candidate_entry_anchor == 4200.0
    assert signal.candidate_first_fill_at == first_fill_at.replace(
        microsecond=(first_fill_at.microsecond // 1000) * 1000,
    )
    assert signal.dca_tickets == [9002]
    assert signal.candidate_entry_prices_by_ticket == {
        9001: 4200.0,
        9002: 4196.0,
    }
    assert signal.candidate_hard_stops == {
        9001: 4191.25,
        9002: 4191.25,
    }
    assert signal.sl_by_ticket == signal.candidate_hard_stops
    assert signal.time_stop_at is None
    assert signal.be_at_tp_index is None
    assert started == [(signal, [4192.0])]


def test_resync_market_marker_keeps_candidate_guard_but_adds_no_exposure(
    monkeypatch,
    tmp_path,
):
    opened_at = int(datetime.now(timezone.utc).timestamp()) - 5
    signal_id = "canal1_26003"
    events_file = tmp_path / "trade_events.jsonl"
    events_file.write_text("", encoding="utf-8")
    groups = {
        signal_id: {
            "channel": "canal1",
            "message_id": 26003,
            "direction": "SELL",
            "market_ticket": 9201,
            "market_price": 4200.0,
            "market_sl": 0.0,
            "market_tp": 0.0,
            "market_open_time": opened_at,
            "extra_market_tickets": [],
            "double_market_tickets": [],
            "scale_out_leg_indexes": {},
            "dca_tickets": [],
            "dca_leg_indexes": {},
            "live_strategy_marker": dubai_live_candidate.CANDIDATE_ID,
        }
    }
    st = StateManager()
    starts = []
    anomalies = []
    monkeypatch.setattr(
        main.executor, "list_open_positions_grouped", lambda: groups,
    )
    monkeypatch.setattr(
        main.causal_trace,
        "load_signal_origin_index",
        lambda _path: ({}, {}, []),
    )
    monkeypatch.setattr(main.journal, "EVENTS_FILE", events_file)
    monkeypatch.setattr(main.journal, "event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        main.journal,
        "anomaly",
        lambda sig, category, severity, detail, **fields: anomalies.append(
            (sig, category, severity, detail, fields)
        ),
    )
    monkeypatch.setattr(state_module, "state", st)
    monkeypatch.setattr(
        position_lifecycle_monitor,
        "start",
        lambda signal, levels: starts.append((signal, levels)),
    )
    monkeypatch.setattr(
        main.executor,
        "mt5",
        SimpleNamespace(symbol_info_tick=lambda _symbol: None),
    )

    main._resync_orphan_positions()

    signal = st.get("canal1", 26003)
    assert signal.live_strategy_id == dubai_live_candidate.CANDIDATE_ID
    assert signal.candidate_entry_expires_at <= datetime.utcnow()
    assert starts == [(signal, [])]
    assert anomalies[0][1:3] == ("mt5", "critical")


class _AuditJournal:
    def __init__(self):
        self.events = []
        self.anomalies = []

    def event(self, sig, ev, **fields):
        self.events.append((sig, ev, fields))

    def anomaly(self, sig, category, severity, detail, **fields):
        self.anomalies.append({
            "sig": sig,
            "category": category,
            "severity": severity,
            "detail": detail,
            **fields,
        })


@pytest.mark.parametrize(
    ("installed_sl", "expects_missing_sl"),
    [(4170.0, False), (0.0, True)],
)
def test_auditor_requires_dubai_sl_but_not_a_tp(
    installed_sl,
    expects_missing_sl,
):
    opened_at = datetime(2026, 8, 23, 9, 30, 0)
    signal = Signal(
        channel="canal1",
        message_id=26002,
        direction="SELL",
        timestamp=opened_at,
        market_ticket=9101,
        market_fill_price=4200.0,
        tps=[4196.0, 4192.0],
        sl=4208.0,
    )
    listener._attach_dubai_live_candidate(signal, opened_at)
    audit_journal = _AuditJournal()
    auditor = LiveAuditor(
        settings=AuditSettings(
            level_apply_grace_s=0,
            naked_after_s=0,
            snapshot_every_s=0,
        ),
        journal=audit_journal,
    )
    position = SimpleNamespace(
        ticket=9101,
        magic=config.MT5_MAGIC_CANAL1,
        sl=installed_sl,
        tp=0.0,
        comment="c1_26002",
        price_open=4200.0,
    )

    auditor.audit_cycle(
        signals=[signal],
        positions=[position],
        pending_actions=[],
        now=opened_at + timedelta(minutes=3),
    )

    codes = {row.get("code") for row in audit_journal.anomalies}
    assert ("levels_not_applied" in codes) is expects_missing_sl
    assert ("mt5_position_naked" in codes) is expects_missing_sl


def test_global_naked_watchdog_defers_to_dubai_owned_broker_protection():
    signal = Signal(
        channel="canal1",
        message_id=26004,
        direction="BUY",
        market_ticket=9301,
        market_fill_price=4200.0,
    )
    listener._attach_dubai_live_candidate(
        signal, datetime(2026, 8, 23, 9, 30, 0),
    )

    assert main._is_intentionally_unprotected_candidate(signal) is False
    assert main._candidate_owns_protection(signal) is True
    assert main._should_apply_naked_protective_sl(signal) is False
