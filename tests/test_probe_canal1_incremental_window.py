import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from research.dubai_iterative.shared_replay import SharedRiskFrame, SharedRiskPoint
from tools.probe_canal1_incremental_window import (
    basket_risk_summary, compact_risk_grid, digest, load_day, native_start_snapshot, risk_summary,
    run,
)


def test_frozen_day_loader_checks_hash_and_window_before_replay(tmp_path):
    symbol_dir = tmp_path / "XAUUSD"
    symbol_dir.mkdir()
    data = symbol_dir / "2026-09-17.parquet"
    meta = symbol_dir / "2026-09-17.json"
    pd.DataFrame({"time_msc": [10_800_000, 10_801_000, 10_802_000],
                  "bid": [100.0, 101.0, 102.0],
                  "ask": [100.2, 101.2, 102.2]}).to_parquet(data)
    meta.write_text(json.dumps({
        "status": "raw_reads_consistent", "symbol": "XAUUSD",
        "source_epoch_day": "2026-09-17", "rows": 3,
        "sha256": digest(data), "bytes": data.stat().st_size,
        "clock_admitted": False}), encoding="utf-8")

    tape, proof = load_day(
        tmp_path, "XAUUSD", "2026-09-17", start_ns=0,
        cutoff_ns=2_000_000_000, offset_seconds=10_800)
    assert list(tape[0]) == [0, 1_000_000_000]
    assert proof["selected_rows"] == 2
    assert proof["clock_admitted_by_source"] is False

    meta.write_text(meta.read_text(encoding="utf-8").replace(digest(data), "0" * 64),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="source is not verified"):
        load_day(tmp_path, "XAUUSD", "2026-09-17", start_ns=0,
                 cutoff_ns=2_000_000_000, offset_seconds=10_800)


def test_basket_risk_reduction_uses_zero_origin_and_keeps_exposure_path():
    values = [(-80, -80, (('a', 0.01, 100.0),)),
              (120, 20, (('a', 0.01, 100.0),)),
              (-20, -70, (('a', 0.01, 100.0), ('b', 0.04, 99.0))),
              (50, 0, ())]
    frames = []
    for index, (realized, floating, positions) in enumerate(values):
        point = SharedRiskPoint("canal1", "canal1_test", index, index + 10,
                                0, realized, floating, positions)
        frames.append(SharedRiskFrame(index, index + 10,
                                      (("canal1", "canal1_test", point),)))
    shared = SimpleNamespace(
        baskets=(("canal1", "canal1_test", SimpleNamespace(blockers=())),),
        risk_grid=tuple(frames), blockers=())

    row = basket_risk_summary(shared)["canal1_test"]
    assert row["known_frames"] == 4
    assert row["unknown_frames"] == 0
    assert row["max_drawdown_minor_model"] == 230
    assert row["drawdown_peak_time_ns"] == 11
    assert row["drawdown_trough_time_ns"] == 12
    assert row["minimum_from_origin_minor_model"] == -160
    assert row["max_gross_volume_model"] == "0.05"
    assert row["first_exposed_time_ns"] == 10
    assert row["last_exposed_time_ns"] == 12
    assert row["final_total_minor_model"] == 50
    assert len(row["path_sha256"]) == 64
    assert risk_summary(shared)["max_drawdown_minor_model"] == 230


def test_unknown_basket_frame_blocks_complete_metrics_without_erasing_count():
    point = SharedRiskPoint("canal1", "canal1_test", 0, 10, 0, 0, 0, ())
    shared = SimpleNamespace(
        baskets=(("canal1", "canal1_test", SimpleNamespace(blockers=())),),
        risk_grid=(SharedRiskFrame(0, 10, (("canal1", "canal1_test", point),)),
                   SharedRiskFrame(1, 11, (("canal1", "canal1_test", None),),
                                   ("unresolved_scope:canal1_test",))),
        blockers=())
    row = basket_risk_summary(shared)["canal1_test"]
    assert row["known_frames"] == 1
    assert row["unknown_frames"] == 1
    assert row["max_drawdown_minor_model"] is None
    assert row["final_total_minor_model"] is None
    assert row["blockers"] == ["unresolved_scope:canal1_test",
                               "incomplete_basket_risk_path"]


def test_missing_grid_interval_is_reported_but_never_given_complete_metrics():
    exposed = SharedRiskPoint("canal1", "canal1_test", 0, 10, 0, 0, -50,
                              (("a", 0.01, 100.0),))
    closed = SharedRiskPoint("canal1", "canal1_test", 3, 13, 0, 20, 0, ())
    shared = SimpleNamespace(
        baskets=(("canal1", "canal1_test", SimpleNamespace(blockers=())),),
        risk_grid=(SharedRiskFrame(0, 10, (("canal1", "canal1_test", exposed),)),
                   SharedRiskFrame(3, 13, (("canal1", "canal1_test", closed),))),
        expected_quote_count=4, blockers=())
    row = basket_risk_summary(shared)["canal1_test"]
    assert row["known_frames"] == 2
    assert row["max_gross_volume_model"] == "0.01"
    assert row["max_drawdown_minor_model"] is None
    assert row["final_total_minor_model"] is None
    assert row["blockers"] == ["incomplete_post_event_grid"]

    invalid = SimpleNamespace(**{**vars(shared), "risk_grid":
        (shared.risk_grid[0], shared.risk_grid[0])})
    with pytest.raises(ValueError, match="chronology mismatch"):
        basket_risk_summary(invalid)


def test_scope_blocker_does_not_contaminate_other_basket_risk():
    bad = SharedRiskPoint("canal1", "canal1_bad", 0, 10, 0, None, None, ())
    good = SharedRiskPoint("canal2", "canal2_good", 0, 10, 0, 20, 0, ())
    shared = SimpleNamespace(
        baskets=(("canal1", "canal1_bad", SimpleNamespace(blockers=("fx_stale",))),
                 ("canal2", "canal2_good", SimpleNamespace(blockers=()))),
        risk_grid=(SharedRiskFrame(0, 10, (("canal1", "canal1_bad", bad),
                                           ("canal2", "canal2_good", good))),),
        expected_quote_count=1,
        blockers=("canal1_bad:fx_stale",))
    rows = basket_risk_summary(shared)
    assert rows["canal1_bad"]["max_drawdown_minor_model"] is None
    assert rows["canal2_good"]["blockers"] == []
    assert rows["canal2_good"]["final_total_minor_model"] == 20
    assert risk_summary(shared)["max_drawdown_minor_model"] is None

    shared.blockers = ("shared_fx_unknown",)
    assert basket_risk_summary(shared)["canal2_good"]["blockers"] == [
        "shared_fx_unknown"]


def test_compact_risk_grid_preserves_quote_identity_money_and_exposure():
    point = SharedRiskPoint("canal2", "canal2_test", 0, 10, 0, 25, -80,
                            (("a", 0.01, 100.0), ("b", 0.04, 99.0)))
    shared = SimpleNamespace(
        baskets=(("canal2", "canal2_test", SimpleNamespace(blockers=())),),
        risk_grid=(SharedRiskFrame(0, 10, (("canal2", "canal2_test", point),)),
                   SharedRiskFrame(1, 11, (("canal2", "canal2_test", None),),
                                   ("unresolved_scope:canal2_test",))),
        expected_quote_count=2)
    result = compact_risk_grid(shared)
    assert result["scopes"] == [["canal2", "canal2_test"]]
    assert result["rows"] == [
        [0, 10, [[25, -80, "0.05", 2]], []],
        [1, 11, [None], ["unresolved_scope:canal2_test"]],
    ]
    assert result["expected_quote_count"] == 2


def test_compact_risk_grid_accepts_only_finished_flat_carry_forward():
    old = SharedRiskPoint("canal2", "canal2_test", 0, 10, 0, 25, 0, ())
    result = SimpleNamespace(blockers=(), pnl_eur=Decimal("0.25"))
    shared = SimpleNamespace(
        baskets=(("canal2", "canal2_test", result),),
        risk_grid=(SharedRiskFrame(1, 11, (("canal2", "canal2_test", old),)),),
        expected_quote_count=2)
    assert compact_risk_grid(shared)["rows"] == [
        [1, 11, [[25, 0, "0", 0]], []]]
    exposed = SharedRiskPoint("canal2", "canal2_test", 0, 10, 0, 25, 0,
                              (("a", 0.01, 100.0),))
    shared.risk_grid = (SharedRiskFrame(1, 11,
                        (("canal2", "canal2_test", exposed),)),)
    with pytest.raises(ValueError, match="not post-event settled"):
        compact_risk_grid(shared)


def test_native_start_snapshot_detects_prior_position_and_binds_source(tmp_path):
    deals = tmp_path / "deals.json"
    deals.write_text("{}", encoding="utf-8")
    positions = tmp_path / "positions.json"
    positions.write_text(json.dumps({
        "source_sha256": digest(deals),
        "positions": [{"signal_id": "canal2_3102", "position_id": 1,
                       "entry_volume": "0.03",
                       "first_native_msc": 13_800_000,
                       "last_native_msc": 15_000_000}],
    }), encoding="utf-8")
    at = datetime.fromtimestamp(3600, timezone.utc)
    open_state = native_start_snapshot(positions, deals, at, offset_seconds=10_800)
    assert open_state["open_position_count"] == 1
    assert open_state["open_signal_ids"] == ["canal2_3102"]
    assert open_state["reconciled_sha256"] == digest(positions)
    flat = native_start_snapshot(
        positions, deals, datetime.fromtimestamp(0, timezone.utc),
        offset_seconds=10_800)
    assert flat["open_position_count"] == 0
    deals.write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="source mismatch"):
        native_start_snapshot(positions, deals, at, offset_seconds=10_800)


def test_assumed_complete_opening_requires_native_position_control(tmp_path):
    with pytest.raises(ValueError, match="native start control required"):
        run(tmp_path, start="2026-09-17T13:45:00+00:00",
            cutoff="2026-09-17T14:55:00+00:00", offset_seconds=10_800,
            assume_initial_complete=True)
