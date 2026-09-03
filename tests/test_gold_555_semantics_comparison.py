from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from research.gold_iterative.semantics_comparison import (
    compare_result_vectors,
    summarize_results,
)


def _path(signal_id: str, day: str):
    return SimpleNamespace(signal_id=signal_id, day=day)


def _result(signal_id: str, pnl: str, *, blockers=()):
    opened_at = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        signal_id=signal_id,
        pnl_eur=Decimal(pnl),
        blockers=tuple(blockers),
        entries=(SimpleNamespace(opened_at=opened_at, entry_price=100.25),),
        exits=(
            SimpleNamespace(
                closed_at=opened_at + timedelta(seconds=12),
                exit_price=100.75,
            ),
        ),
        exit_reason="per_leg_target",
        max_favourable_eur=Decimal("3.00"),
        max_adverse_eur=Decimal("-2.00"),
        max_floating_drawdown_eur=Decimal("2.50"),
    )


def test_summary_returns_one_exact_value_and_explicit_daily_accounting():
    summary = summarize_results(
        (_path("a", "2026-09-01"), _path("b", "2026-09-02")),
        (_result("a", "4.00"), _result("b", "-1.00")),
        oracle_status="pass",
    )

    assert summary["status"] == "certified"
    assert summary["net_eur"] == "3.00"
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["daily"] == [
        {"day": "2026-09-01", "signals": 1, "net_eur": "4.00"},
        {"day": "2026-09-02", "signals": 1, "net_eur": "-1.00"},
    ]
    assert "range" not in summary
    assert "lower" not in summary
    assert "upper" not in summary
    assert summary["rows"][0]["first_entry_at"] == "2026-09-01T09:00:00+00:00"
    assert summary["rows"][0]["first_entry_price"] == "100.25"
    assert summary["rows"][0]["first_exit_at"] == "2026-09-01T09:00:12+00:00"
    assert summary["rows"][0]["holding_ms"] == 12_000
    assert summary["holding_time_ms"]["median"] == 12_000


def test_any_unknown_result_blocks_the_total_instead_of_shrinking_the_sample():
    result = _result("a", "4.00", blockers=("missing_tick",))

    summary = summarize_results(
        (_path("a", "2026-09-01"),),
        (result,),
        oracle_status="blocked",
    )

    assert summary["status"] == "blocked"
    assert summary["net_eur"] is None
    assert summary["blockers"] == ["missing_tick", "oracle_not_passed"]


def test_result_vector_comparison_requires_same_ids_and_full_results():
    expected = (_result("a", "4.00"), _result("b", "-1.00"))
    equal = (_result("b", "-1.00"), _result("a", "4.00"))
    changed = (_result("a", "4.00"), _result("b", "-1.01"))

    assert compare_result_vectors(expected, equal) == ()
    assert compare_result_vectors(expected, changed) == ("result_mismatch:b",)
    assert compare_result_vectors(expected, expected[:1]) == (
        "result_missing:b",
    )
