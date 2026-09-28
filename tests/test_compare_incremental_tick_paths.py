from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tools.compare_incremental_tick_paths import (
    admit_native_window_positions, aggregate_model_grid, compare_aligned_samples,
    first_entry_evidence, require_recomputed_scalar,
)


def sample(millisecond, *, realized="0", floating="0", volume="0", count=0,
           quote_millisecond=None):
    at = datetime.fromtimestamp(millisecond / 1000, timezone.utc)
    quote_at = datetime.fromtimestamp(
        (quote_millisecond if quote_millisecond is not None else millisecond) / 1000,
        timezone.utc)
    return {"at": at, "quote_at": quote_at,
            "realized": Decimal(realized), "floating": Decimal(floating),
            "total": Decimal(realized) + Decimal(floating),
            "long_volume": Decimal(volume), "short_volume": Decimal(0),
            "open_count": count}


def test_common_tick_comparison_keeps_duplicate_quotes_and_skips_deal_boundary():
    grid = [
        [0, 10_000_000, [[0, 0, "0", 0]], []],
        [1, 11_000_000, [[0, -100, "0.04", 1]], []],
        [2, 11_000_000, [[20, 0, "0", 0]], []],
    ]
    native = [sample(10), sample(10, quote_millisecond=9),
              sample(11, floating="-0.80", volume="0.04", count=1),
              sample(11, realized="0.20")]
    result = compare_aligned_samples(grid, native, scope_index=0)
    assert result["sample_count"] == 3
    assert result["different_total_ticks"] == 1
    assert result["different_floating_ticks"] == 1
    assert result["different_exposure_ticks"] == 0
    assert result["first_divergence"]["tick_index"] == 1
    assert result["first_divergence"]["fields"] == ["total", "floating"]
    assert result["model_max_drawdown_minor"] == 100
    assert result["native_max_drawdown_minor"] == 80
    assert result["max_abs_total_delta_minor"] == 20
    assert result["union_exposed_ticks"] == 1
    assert result["different_total_exposed_ticks"] == 1
    assert result["max_abs_total_delta_exposed_minor"] == 20


def test_common_tick_comparison_rejects_missing_or_shifted_observation():
    grid = [[0, 10_000_000, [None], []]]
    with pytest.raises(ValueError, match="model risk state unavailable"):
        compare_aligned_samples(grid, [sample(10)], scope_index=0)
    grid[0][2] = [[0, 0, "0", 0]]
    with pytest.raises(ValueError, match="quote grid mismatch"):
        compare_aligned_samples(grid, [sample(11)], scope_index=0)


def test_scalar_gate_rejects_tampered_comparison_rows():
    rows = [{"signal_id": "canal2_1", "status": "blocked_comparison"}]
    counts = {"blocked_comparison": 1}
    require_recomputed_scalar({"rows": rows, "counts": counts}, rows, counts)
    with pytest.raises(ValueError, match="scalar comparison differs"):
        require_recomputed_scalar(
            {"rows": [{**rows[0], "status": "matched_scalar_only"}],
             "counts": counts}, rows, counts)


def test_account_grid_sums_scopes_and_rejects_unknown_state():
    grid = [[0, 10_000_000, [[20, -30, "0.04", 1], [-5, 10, "0.01", 1]], []]]
    aggregate = aggregate_model_grid(grid)
    assert aggregate == [[0, 10_000_000, [[15, -20, "0.05", 2]], []]]
    native = [sample(10, realized="0.15", floating="-0.20",
                     volume="0.05", count=2)]
    assert compare_aligned_samples(aggregate, native, scope_index=0)[
        "same_grid_fields_equal"] is True
    grid[0][2][1] = None
    with pytest.raises(ValueError, match="model account state unavailable"):
        aggregate_model_grid(grid)


def test_account_window_requires_closed_complete_native_universe():
    positions = [{"position_id": 1, "signal_id": "canal2_a",
                  "first_native_msc": 110, "last_native_msc": 150},
                 {"position_id": 2, "signal_id": "canal1_b",
                  "first_native_msc": 120, "last_native_msc": 180}]
    assert admit_native_window_positions(
        positions, start_source_msc=100, cutoff_source_msc=200,
        admitted_ids={"canal2_a", "canal1_b"}) == {1, 2}
    with pytest.raises(ValueError, match="outside admitted universe"):
        admit_native_window_positions(
            positions, start_source_msc=100, cutoff_source_msc=200,
            admitted_ids={"canal2_a"})
    positions[1]["last_native_msc"] = 210
    with pytest.raises(ValueError, match="crosses window boundary"):
        admit_native_window_positions(
            positions, start_source_msc=100, cutoff_source_msc=200,
            admitted_ids={"canal2_a", "canal1_b"})
    positions[1]["first_native_msc"] = 90
    positions[1]["last_native_msc"] = 180
    with pytest.raises(ValueError, match="crosses window boundary"):
        admit_native_window_positions(
            positions, start_source_msc=100, cutoff_source_msc=200,
            admitted_ids={"canal2_a", "canal1_b"})


def test_first_entry_evidence_keeps_timing_price_and_volume_distinct():
    model = [{"opened_at": "2026-09-18T12:30:24.616000+00:00",
              "requested_ns": 1_789_734_624_616_000_000,
              "entry_price": 4361.05, "volume": 0.01}]
    native = [{"entry_msc": 1_789_745_424_575,
               "entry_price": "4361.07", "volume": "0.01"}]
    result = first_entry_evidence(
        model, native, offset_seconds=10_800, scalar_entry_deltas=[41])
    assert result["model_minus_native_ms"] == 41
    assert result["model_minus_native_price"] == "-0.02"
    assert result["same_entry_volume"] is True
    assert result["ordinal_pairing_not_ticket_identity"] is True
    with pytest.raises(ValueError, match="scalar timing"):
        first_entry_evidence(model, native, offset_seconds=10_800,
                             scalar_entry_deltas=[40])
    with pytest.raises(ValueError, match="model first entry clock"):
        first_entry_evidence([{**model[0], "requested_ns": model[0]["requested_ns"] + 1}],
                             native, offset_seconds=10_800,
                             scalar_entry_deltas=[41])
    with pytest.raises(ValueError, match="first entry price or volume"):
        first_entry_evidence(model, [{**native[0], "entry_price": "NaN"}],
                             offset_seconds=10_800, scalar_entry_deltas=[41])
