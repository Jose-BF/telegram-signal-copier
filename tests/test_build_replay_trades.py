import json

import pytest

import build_replay_trades
import replay_source_contract


def _closed_position(ticket=111, role="market_a", pnl=3.25):
    return {
        "ticket": ticket,
        "role": role,
        "open_price": 4500.10,
        "open_dt_utc": "2026-06-03T09:00:01+00:00",
        "close_price": 4502.30,
        "close_dt_utc": "2026-06-03T09:04:30+00:00",
        "close_reason": "tp",
        "is_closed": True,
        "pnl_net": pnl,
        "volume": 0.01,
        "sl_history": [
            {"ts": "2026-06-03T09:00:45+00:00",
             "sl": 4496.0, "status": "confirmed", "source": "SL #111"},
        ],
        "tp_history": [
            {"ts": "2026-06-03T09:00:45+00:00",
             "tp": 4502.3, "status": "confirmed", "source": "TP #111"},
        ],
    }


def _ledger_row(**overrides):
    base = {
        "sig_id": "canal2_13265",
        "channel": "canal2",
        "direction": "SELL",
        "signal_dt_utc": "2026-06-03T09:00:00+00:00",
        "open_dt_utc": "2026-06-03T09:00:01+00:00",
        "close_dt_utc": "2026-06-03T09:04:30+00:00",
        "status": "closed",
        "n_positions": 1,
        "n_closed": 1,
        "n_open": 0,
        "positions": [_closed_position()],
        "pnl_real_mt5": 3.25,
        "pnl_mt5_complete": True,
        "reconciled_ok": True,
        "journal_has_signal_closed": True,
        "range": [4500.0, 4504.0],
        "tps": [4498.0, 4496.0],
        "sl": 4508.0,
        "effective_tps": [4498.0, 4496.0],
        "effective_sl": 4508.0,
        "effective_levels_source": {"sl": "journal", "tps": "journal"},
        "flags": [],
        "anomalies": [],
        "health": "ok",
        "analysis_excluded": False,
        "analysis_exclusions": [],
        "signal_text": "SELL NOW",
        "management": [],
        "timeline": [],
        "order_lifecycle": [],
        "strategy_snapshot": {"entry_mode": "scale_out"},
        "entry_quality": {"case": "A_inside"},
    }
    base.update(overrides)
    return base


def test_closed_trade_with_complete_levels_is_replay_ready():
    events = [
        {"ts": "2026-06-03T09:00:00+00:00", "sig": "canal2_13265",
         "ev": "signal_received", "direction": "SELL", "raw_text": "SELL NOW"},
        {"ts": "2026-06-03T09:00:01+00:00", "sig": "canal2_13265",
         "ev": "market_filled", "ticket": 111, "price": 4500.10,
         "bid": 4500.08, "ask": 4500.10, "spread": 0.02},
    ]

    replay = build_replay_trades.build_replay_trade(_ledger_row(), events)

    assert replay["schema_version"] == 1
    assert replay["sig_id"] == "canal2_13265"
    assert replay["simulation_ready"] is True
    assert replay["replay_ready"] is True
    assert replay["gaps"] == []
    assert replay["tickets"][0]["ticket"] == 111
    assert replay["tickets"][0]["fill_event"]["ev"] == "market_filled"
    assert replay["tickets"][0]["sl_history"][0]["sl"] == 4496.0
    assert replay["levels"]["effective_sl"] == 4508.0


def test_zone_entry_provenance_is_explicit_in_replay_trade():
    provenance = {
        "source_kind": "zone_first_touch",
        "zone_plan_message_id": 700,
        "zone_thread_root_message_id": 699,
        "zone_entry_generation": 1,
        "zone_trigger_kind": "first_touch",
        "zone_trigger_side": "ask",
        "zone_trigger_price": 4055.2,
        "zone_trigger_time": 1785920400,
        "zone_trigger_time_msc": 1785920400123,
    }
    replay = build_replay_trades.build_replay_trade(
        _ledger_row(entry_provenance=provenance),
        [],
    )

    assert replay["entry_provenance"] == provenance


def test_replay_ready_does_not_depend_on_non_causal_telemetry_events():
    events = [
        {"ts": "2026-06-03T08:59:58+00:00", "sig": "bot",
         "ev": "heartbeat", "open_signals": 0},
        {"ts": "2026-06-03T09:00:00+00:00", "sig": "canal2_13265",
         "ev": "signal_received", "direction": "SELL", "raw_text": "SELL NOW"},
        {"ts": "2026-06-03T09:00:01+00:00", "sig": "canal2_13265",
         "ev": "market_filled", "ticket": 111, "price": 4500.10},
        {"ts": "2026-06-03T09:01:00+00:00", "sig": "canal2_13265",
         "ev": "audit_snapshot", "state_tickets": [111]},
        {"ts": "2026-06-03T09:01:30+00:00", "sig": "canal2_13265",
         "ev": "floating_pl_snapshot", "pl": 0.4},
    ]

    replay = build_replay_trades.build_replay_trade(_ledger_row(), events)

    assert replay["replay_ready"] is True
    assert replay["tickets"][0]["fill_event"]["ev"] == "market_filled"
    assert replay["gaps"] == []

def test_replay_records_runtime_discontinuity_overlapping_trade_close():
    row = _ledger_row(
        close_dt_utc="2026-06-03T09:04:30+00:00",
        positions=[_closed_position()],
    )
    all_events = [
        {
            "ts": "2026-06-03T09:00:01+00:00",
            "sig": "canal2_13265",
            "ev": "market_filled",
            "ticket": 111,
            "price": 4500.10,
        },
        {
            "ts": "2026-06-03T09:01:00+00:00",
            "sig": "canal2_13265",
            "ev": "floating_pl_snapshot",
            "pl": 0.5,
        },
        {
            "ts": "2026-06-03T09:04:20+00:00",
            "sig": "bot",
            "ev": "session_started",
        },
        {
            "ts": "2026-06-03T09:04:40+00:00",
            "sig": "bot",
            "ev": "mt5_connection_change",
            "connected": True,
        },
    ]

    replay = build_replay_trades.build_replay_trades(
        [row],
        build_replay_trades.events_by_signal(all_events),
        operational_events=all_events,
    )[0]

    assert replay["simulation_ready"] is True
    assert replay["operational_context"] == {
        "runtime_discontinuities": [{
            "kind": "session_restart_overlap",
            "unobserved_from_utc": "2026-06-03T09:01:00+00:00",
            "restart_observed_utc": "2026-06-03T09:04:20+00:00",
            "observability_restored_utc": "2026-06-03T09:04:40+00:00",
        }],
    }



def test_position_id_is_used_as_operational_ticket_for_event_matching():
    position = _closed_position(ticket=999)
    position["position_id"] = 111
    row = _ledger_row(positions=[position])
    events = [
        {"ts": "2026-06-03T09:00:01+00:00", "sig": "canal2_13265",
         "ev": "market_filled", "ticket": 111, "price": 4500.10},
    ]

    replay = build_replay_trades.build_replay_trade(row, events)

    ticket = replay["tickets"][0]
    assert ticket["position_ticket"] == 111
    assert ticket["deal_ticket"] == 999
    assert ticket["ticket"] == 111
    assert ticket["fill_event"]["ticket"] == 111


def test_ticket_preserves_mt5_deal_detail():
    position = _closed_position(ticket=999)
    position.update({
        "position_id": 111,
        "pnl_components": {
            "profit": 2.81,
            "swap": -0.01,
            "commission": -0.04,
            "fee": -0.01,
            "net": 2.75,
        },
        "open_deal": {"ticket": 1307240053, "time_msc": 1783076049123},
        "close_deal": {"ticket": 1307244455, "time_msc": 1783077119876},
        "deals": [
            {"ticket": 1307240053, "entry": 0, "time_msc": 1783076049123},
            {"ticket": 1307244455, "entry": 1, "time_msc": 1783077119876},
        ],
    })

    replay = build_replay_trades.build_replay_trade(
        _ledger_row(positions=[position]), [])

    ticket = replay["tickets"][0]
    assert ticket["pnl_components"]["net"] == 2.75
    assert ticket["open_deal"]["time_msc"] == 1783076049123
    assert ticket["close_deal"]["time_msc"] == 1783077119876
    assert [deal["entry"] for deal in ticket["deals"]] == [0, 1]


def test_replay_preserves_verified_mt5_time_offset_for_trade_and_ticket():
    position = _closed_position(ticket=999)
    position["position_id"] = 111
    position["mt5_time_offset_s"] = 10_800
    row = _ledger_row(
        positions=[position],
        mt5_time_offset_s=10_800,
    )

    replay = build_replay_trades.build_replay_trade(row, [])

    assert replay["mt5_time_offset_s"] == 10_800
    assert replay["tickets"][0]["mt5_time_offset_s"] == 10_800


def test_level_history_is_recovered_from_order_lifecycle_by_position_id():
    position = _closed_position(ticket=999)
    position["position_id"] = 111
    position["sl_history"] = []
    position["tp_history"] = []
    row = _ledger_row(
        positions=[position],
        effective_sl=None,
        effective_tps=None,
        order_lifecycle=[
            {"ts": "2026-06-03T09:00:45+00:00",
             "ev": "mt5_modify_confirmed", "ticket": 111,
             "new_sl": 4496.0, "new_tp": 4502.3,
             "label": "SL/TP[0] #111", "retcode": 10009},
            {"ts": "2026-06-03T09:00:46+00:00",
             "ev": "mt5_position_snapshot", "ticket": 111,
             "sl": 4496.0, "tp": 4502.3,
             "label": "SL/TP[0] #111", "retcode": 10009},
        ],
    )

    replay = build_replay_trades.build_replay_trade(row, [])

    ticket = replay["tickets"][0]
    assert replay["simulation_ready"] is True
    assert ticket["sl_history"][0]["sl"] == 4496.0
    assert ticket["sl_history"][0]["status"] == "confirmed"
    assert ticket["tp_history"][1]["tp"] == 4502.3
    assert "missing_effective_sl" not in replay["gaps"]
    assert "missing_effective_tps" not in replay["gaps"]


def test_unattributed_level_window_survives_order_lifecycle_recovery():
    position = _closed_position(ticket=999)
    position["position_id"] = 111
    position["sl_history"] = []
    position["tp_history"] = []
    row = _ledger_row(
        positions=[position],
        order_lifecycle=[{
            "ts": "2026-06-03T09:01:05+00:00",
            "ev": "mt5_level_change_unattributed",
            "ticket": 111,
            "sl": 4500.1,
            "tp": 4502.3,
            "previous": {"sl": 4496.0, "tp": 4502.3},
            "current": {"sl": 4500.1, "tp": 4502.3},
            "changed_fields": ["sl"],
            "observed_interval_start_utc": (
                "2026-06-03T09:01:00+00:00"
            ),
            "observed_interval_end_utc": (
                "2026-06-03T09:01:05+00:00"
            ),
        }],
    )

    replay = build_replay_trades.build_replay_trade(row, [])
    level = replay["tickets"][0]["sl_history"][0]

    assert level["status"] == "observed_unattributed"
    assert level["sl"] == 4500.1
    assert level["observed_interval_start_utc"] == (
        "2026-06-03T09:01:00+00:00"
    )
    assert replay["tickets"][0]["tp_history"] == []


def test_journal_closed_with_mt5_open_position_blocks_replay():
    open_leg = _closed_position(ticket=555, role="scale_out_leg", pnl=0.0)
    open_leg.update({
        "close_price": None,
        "close_dt_utc": None,
        "close_reason": None,
        "is_closed": False,
    })
    row = _ledger_row(
        sig_id="canal2_13288",
        status="partial",
        n_positions=5,
        n_closed=4,
        n_open=1,
        positions=[_closed_position(ticket=i) for i in range(551, 555)]
                  + [open_leg],
        journal_has_signal_closed=True,
        flags=["journal_cerro_pero_MT5_tiene_pos_abierta"],
    )

    replay = build_replay_trades.build_replay_trade(row, [])

    assert replay["simulation_ready"] is False
    assert replay["replay_ready"] is False
    assert "open_positions" in replay["gaps"]
    assert "journal_closed_but_mt5_open" in replay["gaps"]
    assert replay["tickets"][-1]["is_closed"] is False


def test_positions_closed_by_mt5_event_reconstructs_missing_close_deals():
    open_leg = _closed_position(ticket=1561080218, role="scale_out_leg", pnl=0.0)
    open_leg.update({
        "close_price": None,
        "close_dt_utc": None,
        "close_reason": None,
        "is_closed": False,
        "pnl_net": 0.0,
    })
    row = _ledger_row(
        sig_id="canal1_20789",
        status="partial",
        open_dt_utc="2026-07-08T16:32:45+00:00",
        n_positions=4,
        n_closed=1,
        n_open=3,
        positions=[
            _closed_position(ticket=1561080190, pnl=3.44),
            open_leg,
        ],
        pnl_real_mt5=3.44,
        pnl_journal=-10.66,
        journal_has_signal_closed=True,
        flags=["journal_cerro_pero_MT5_tiene_pos_abierta"],
        health="degraded",
    )
    events = [
        {
            "ts": "2026-07-08T16:50:49.225+00:00",
            "sig": "canal1_20789",
            "ev": "positions_closed_by_mt5",
            "closures": [
                {
                    "ticket": 1561080190,
                    "exit_price": 4502.30,
                    "pnl": 3.44,
                    "closed_by_tag": "TP1",
                    "distance_to_tag": 0.0,
                },
                {
                    "ticket": 1561080218,
                    "exit_price": 4510.0,
                    "pnl": -14.10,
                    "closed_by_tag": "SL",
                    "distance_to_tag": 0.0,
                },
            ],
            "summary_by_tag": {"TP1": 1, "SL": 1},
        }
    ]

    replay = build_replay_trades.build_replay_trade(row, events)

    assert replay["status"] == "closed"
    assert replay["close_dt_utc"] == "2026-07-08T16:50:49+00:00"
    assert replay["duration_min"] == 18.1
    assert replay["pnl_real_mt5"] == -10.66
    assert replay["pnl_real_mt5_source"] == "positions_closed_by_mt5"
    assert replay["simulation_ready"] is True
    assert "open_positions" not in replay["gaps"]
    assert "journal_closed_but_mt5_open" not in replay["gaps"]
    reconstructed = replay["tickets"][1]
    assert reconstructed["is_closed"] is True
    assert reconstructed["close_price"] == 4510.0
    assert reconstructed["close_reason"] == "sl"
    assert reconstructed["pnl_net"] == -14.10
    assert reconstructed["close_event"]["ev"] == "positions_closed_by_mt5"


def test_no_position_ledger_uses_journal_events_when_mt5_history_is_missing():
    row = _ledger_row(
        sig_id="canal2_3021",
        status="no_position",
        open_dt_utc=None,
        close_dt_utc=None,
        n_positions=0,
        n_closed=0,
        n_open=0,
        positions=[],
        pnl_real_mt5=0,
        pnl_journal=-36.44,
        pnl_mt5_complete=False,
        journal_has_signal_closed=True,
        flags=[
            "PNL_PARCIAL_mt5_identifico_0_de_5_pos "
            "(posiciones del journal no halladas en MT5)"
        ],
        range=[4122.5, 4127.5],
        tps=[4130.5, 4132.5, 4135.0, 4142.0],
        sl=4119.5,
        effective_tps=[4130.5, 4132.5, 4135.0, 4142.0],
        effective_sl=4119.5,
    )
    events = [
        {"ts": "2026-07-09T14:09:44.917+00:00", "sig": "canal2_3021",
         "ev": "signal_received", "direction": "BUY", "raw_text": "Buy Gold Now"},
        {"ts": "2026-07-09T14:09:45.065+00:00", "sig": "canal2_3021",
         "ev": "market_filled", "ticket": 1567026280, "price": 4127.83},
        {"ts": "2026-07-09T14:09:45.189+00:00", "sig": "canal2_3021",
         "ev": "scale_out_leg_filled", "ticket": 1567026288, "price": 4127.83},
        {"ts": "2026-07-09T14:15:09.255+00:00", "sig": "canal2_3021",
         "ev": "positions_closed_by_mt5",
         "closures": [
             {"ticket": 1567026280, "exit_price": 4119.5,
              "pnl": -7.28, "closed_by_tag": "SL"},
             {"ticket": 1567026288, "exit_price": 4119.5,
              "pnl": -7.28, "closed_by_tag": "SL"},
         ]},
        {"ts": "2026-07-09T14:15:09.753+00:00", "sig": "canal2_3021",
         "ev": "signal_closed", "tag": "LOSS_CLEAN", "total_pl": -14.56},
    ]

    replay = build_replay_trades.build_replay_trade(row, events)

    assert replay["status"] == "closed"
    assert replay["open_dt_utc"] == "2026-07-09T14:09:45+00:00"
    assert replay["close_dt_utc"] == "2026-07-09T14:15:09+00:00"
    assert replay["pnl_real_mt5"] == -14.56
    assert replay["pnl_real_mt5_source"] == "positions_closed_by_mt5"
    assert replay["simulation_ready"] is True
    assert "missing_tickets" not in replay["gaps"]
    assert replay["tickets"][0]["ticket"] == 1567026280
    assert replay["tickets"][0]["close_reason"] == "sl"
    assert replay["tickets"][1]["fill_event"]["ev"] == "scale_out_leg_filled"


def test_journal_fallback_recovers_volume_from_consistent_mt5_snapshots():
    row = _ledger_row(
        sig_id="canal2_volume_recovery",
        status="no_position",
        open_dt_utc=None,
        close_dt_utc=None,
        n_positions=0,
        n_closed=0,
        n_open=0,
        positions=[],
        pnl_real_mt5=0,
        pnl_journal=-1.02,
        pnl_mt5_complete=False,
        journal_has_signal_closed=True,
    )
    events = [
        {
            "ts": "2026-07-22T08:21:21.672+00:00",
            "sig": "canal2_volume_recovery",
            "ev": "market_filled",
            "ticket": 1634455411,
            "price": 4116.21,
        },
        {
            "ts": "2026-07-22T08:21:22.183+00:00",
            "sig": "canal2_volume_recovery",
            "ev": "mt5_position_snapshot",
            "ticket": 1634455411,
            "position_exists": True,
            "volume": 0.01,
            "price_open": 4116.21,
        },
        {
            "ts": "2026-07-22T08:21:34.705+00:00",
            "sig": "canal2_volume_recovery",
            "ev": "mt5_position_snapshot",
            "ticket": 1634455411,
            "position_exists": True,
            "volume": 0.01,
            "price_open": 4116.21,
        },
        {
            "ts": "2026-07-22T08:41:14.261+00:00",
            "sig": "canal2_volume_recovery",
            "ev": "positions_closed_by_mt5",
            "closures": [{
                "ticket": 1634455411,
                "exit_price": 4117.37,
                "pnl": -1.02,
                "closed_by_tag": "CLOSE_FIRST",
            }],
        },
    ]

    replay = build_replay_trades.build_replay_trade(row, events)

    assert replay["tickets"][0]["volume"] == 0.01
    assert replay["tickets"][0]["volume_source"] == (
        "mt5_position_snapshot_consensus"
    )


def test_journal_fallback_does_not_guess_volume_from_conflicting_snapshots():
    row = _ledger_row(
        sig_id="canal2_volume_conflict",
        status="no_position",
        open_dt_utc=None,
        close_dt_utc=None,
        positions=[],
        n_positions=0,
        n_closed=0,
        n_open=0,
        pnl_real_mt5=0,
        pnl_journal=0,
        pnl_mt5_complete=False,
        journal_has_signal_closed=False,
    )
    events = [
        {
            "ts": "2026-07-22T08:21:21.672+00:00",
            "sig": "canal2_volume_conflict",
            "ev": "market_filled",
            "ticket": 1634455411,
            "price": 4116.21,
        },
        {
            "ts": "2026-07-22T08:21:22.183+00:00",
            "sig": "canal2_volume_conflict",
            "ev": "mt5_position_snapshot",
            "ticket": 1634455411,
            "position_exists": True,
            "volume": 0.01,
        },
        {
            "ts": "2026-07-22T08:21:34.705+00:00",
            "sig": "canal2_volume_conflict",
            "ev": "mt5_position_snapshot",
            "ticket": 1634455411,
            "position_exists": True,
            "volume": 0.02,
        },
        {
            "ts": "2026-07-22T08:41:14.261+00:00",
            "sig": "canal2_volume_conflict",
            "ev": "positions_closed_by_mt5",
            "closures": [{
                "ticket": 1634455411,
                "exit_price": 4117.37,
                "pnl": -1.02,
                "closed_by_tag": "CLOSE_FIRST",
            }],
        },
    ]

    replay = build_replay_trades.build_replay_trade(row, events)

    assert replay["tickets"][0]["volume"] is None
    assert replay["tickets"][0]["volume_source"] is None


def test_close_first_maps_to_bot_close_only_with_confirmed_close_chain():
    row = _ledger_row(
        sig_id="canal2_close_first",
        status="no_position",
        open_dt_utc=None,
        close_dt_utc=None,
        positions=[],
        n_positions=0,
        n_closed=0,
        n_open=0,
    )
    events = [
        {
            "ts": "2026-07-22T08:21:21.672+00:00",
            "sig": "canal2_close_first",
            "ev": "market_filled",
            "ticket": 101,
            "price": 4116.21,
            "volume": 0.01,
        },
        {
            "ts": "2026-07-22T08:41:11.564+00:00",
            "sig": "canal2_close_first",
            "ev": "mt5_close_requested",
            "ticket": 101,
            "label": "CLOSE_FIRST BE-timeout #101",
        },
        {
            "ts": "2026-07-22T08:41:11.878+00:00",
            "sig": "canal2_close_first",
            "ev": "mt5_close_result",
            "ticket": 101,
            "retcode": 10009,
            "label": "CLOSE_FIRST BE-timeout #101",
        },
        {
            "ts": "2026-07-22T08:41:11.878+00:00",
            "sig": "canal2_close_first",
            "ev": "mt5_position_snapshot",
            "ticket": 101,
            "after_action": "CLOSE_POSITION",
            "position_exists": False,
        },
        {
            "ts": "2026-07-22T08:41:14.261+00:00",
            "sig": "canal2_close_first",
            "ev": "positions_closed_by_mt5",
            "closures": [{
                "ticket": 101,
                "exit_price": 4117.37,
                "pnl": -1.02,
                "closed_by_tag": "CLOSE_FIRST",
            }],
        },
    ]

    replay = build_replay_trades.build_replay_trade(row, events)

    assert replay["tickets"][0]["close_reason"] == "bot_close"
    assert replay["tickets"][0]["close_reason_evidence"] == (
        "confirmed_bot_close_chain"
    )


def test_close_first_without_confirmed_close_chain_remains_unsupported():
    row = _ledger_row(
        sig_id="canal2_close_first_unverified",
        status="no_position",
        positions=[],
        n_positions=0,
    )
    events = [
        {
            "ts": "2026-07-22T08:21:21.672+00:00",
            "sig": "canal2_close_first_unverified",
            "ev": "market_filled",
            "ticket": 101,
            "price": 4116.21,
            "volume": 0.01,
        },
        {
            "ts": "2026-07-22T08:41:14.261+00:00",
            "sig": "canal2_close_first_unverified",
            "ev": "positions_closed_by_mt5",
            "closures": [{
                "ticket": 101,
                "exit_price": 4117.37,
                "pnl": -1.02,
                "closed_by_tag": "CLOSE_FIRST",
            }],
        },
    ]

    replay = build_replay_trades.build_replay_trade(row, events)

    assert replay["tickets"][0]["close_reason"] == "close_first"
    assert replay["tickets"][0].get("close_reason_evidence") is None


def test_loss_be_maps_to_be_only_with_confirmed_entry_level_stop():
    row = _ledger_row(
        sig_id="canal2_loss_be",
        status="no_position",
        open_dt_utc=None,
        close_dt_utc=None,
        positions=[],
        n_positions=0,
        n_closed=0,
        n_open=0,
        order_lifecycle=[{
            "ts": "2026-07-22T09:21:52.671+00:00",
            "ev": "mt5_modify_confirmed",
            "ticket": 101,
            "new_sl": 4121.94,
            "label": "BE #101 -> 4121.94",
            "retcode": 10009,
        }],
    )
    events = [
        {
            "ts": "2026-07-22T09:13:31.639+00:00",
            "sig": "canal2_loss_be",
            "ev": "market_filled",
            "ticket": 101,
            "price": 4121.94,
            "volume": 0.01,
        },
        {
            "ts": "2026-07-22T09:25:00.000+00:00",
            "sig": "canal2_loss_be",
            "ev": "positions_closed_by_mt5",
            "closures": [{
                "ticket": 101,
                "exit_price": 4121.94,
                "pnl": 0.0,
                "closed_by_tag": "LOSS_BE",
            }],
        },
    ]

    replay = build_replay_trades.build_replay_trade(row, events)

    assert replay["tickets"][0]["close_reason"] == "be"
    assert replay["tickets"][0]["close_reason_evidence"] == (
        "confirmed_entry_level_stop"
    )


def test_loss_be_without_confirmed_entry_level_stop_remains_unsupported():
    row = _ledger_row(
        sig_id="canal2_loss_be_unverified",
        status="no_position",
        positions=[],
        n_positions=0,
    )
    events = [
        {
            "ts": "2026-07-22T09:13:31.639+00:00",
            "sig": "canal2_loss_be_unverified",
            "ev": "market_filled",
            "ticket": 101,
            "price": 4121.94,
            "volume": 0.01,
        },
        {
            "ts": "2026-07-22T09:25:00.000+00:00",
            "sig": "canal2_loss_be_unverified",
            "ev": "positions_closed_by_mt5",
            "closures": [{
                "ticket": 101,
                "exit_price": 4121.94,
                "pnl": 0.0,
                "closed_by_tag": "LOSS_BE",
            }],
        },
    ]

    replay = build_replay_trades.build_replay_trade(row, events)

    assert replay["tickets"][0]["close_reason"] == "loss_be"
    assert replay["tickets"][0].get("close_reason_evidence") is None


def test_closed_mt5_trade_without_signal_closed_is_simulable_but_not_audit_ready():
    row = _ledger_row(
        sig_id="canal2_13293",
        journal_has_signal_closed=False,
        flags=["HUERFANO_journal_sin_signal_closed"],
        reconciled_ok=None,
    )

    replay = build_replay_trades.build_replay_trade(row, [])

    assert replay["simulation_ready"] is True
    assert replay["replay_ready"] is False
    assert "missing_signal_closed" in replay["gaps"]
    assert replay["audit_blockers"] == ["missing_signal_closed"]


def test_write_replay_trades_outputs_jsonl(tmp_path):
    output = tmp_path / "replay.jsonl"

    build_replay_trades.write_replay_trades([_ledger_row()], {}, output)

    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["sig_id"] == "canal2_13265"
    assert rows[0]["replay_ready"] is True


def test_cli_writes_source_manifest_for_replay(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    events = tmp_path / "trade_events.jsonl"
    output = tmp_path / "replay.jsonl"
    ledger.write_text(
        json.dumps(_ledger_row()) + "\n",
        encoding="utf-8",
    )
    events.write_text("", encoding="utf-8")

    exit_code = build_replay_trades.main([
        "--ledger",
        str(ledger),
        "--events",
        str(events),
        "--output",
        str(output),
        "--quiet",
    ])

    manifest = replay_source_contract.default_manifest_path(output)
    assert exit_code == 0
    assert manifest.is_file()
    assert replay_source_contract.validate_manifest(
        replay_path=output,
        ledger_path=ledger,
        events_path=events,
        manifest_path=manifest,
    ) == []


def test_cli_removes_replay_when_sources_change_during_build(
    tmp_path,
    monkeypatch,
):
    ledger = tmp_path / "ledger.jsonl"
    events = tmp_path / "trade_events.jsonl"
    output = tmp_path / "replay.jsonl"
    ledger.write_text(
        json.dumps(_ledger_row()) + "\n",
        encoding="utf-8",
    )
    events.write_text("", encoding="utf-8")
    original_write = build_replay_trades.write_replay_trades

    def write_then_change_source(*args, **kwargs):
        trades = original_write(*args, **kwargs)
        ledger.write_text(
            ledger.read_text(encoding="utf-8") + "{}\n",
            encoding="utf-8",
        )
        return trades

    monkeypatch.setattr(
        build_replay_trades,
        "write_replay_trades",
        write_then_change_source,
    )

    with pytest.raises(
        RuntimeError,
        match="replay sources changed during build",
    ):
        build_replay_trades.main([
            "--ledger",
            str(ledger),
            "--events",
            str(events),
            "--output",
            str(output),
            "--quiet",
        ])

    assert not output.exists()
    assert not replay_source_contract.default_manifest_path(output).exists()
