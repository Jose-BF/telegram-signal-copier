from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from strategy_shadow_catalog import build_shadow_catalog
from strategy_shadow_report import build_report


def _complete_rows(*, signals_per_channel: int = 15):
    catalog = build_shadow_catalog()
    candidate_rows = []
    actual_rows = []
    base = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
    for channel in ("canal1", "canal2"):
        for index in range(signals_per_channel):
            signal_id = f"{channel}_{1000 + index}"
            registered = base + timedelta(minutes=index)
            outcome = registered + timedelta(minutes=30)
            actual_rows.append(
                {
                    "channel": channel,
                    "signal_id": signal_id,
                    "day": registered.date().isoformat(),
                    "entry_count": 3,
                    "exit_reason": "provider_close",
                    "net_eur": 1.0,
                    "control_mirror_match": True,
                    "telegram_lineage_complete": True,
                }
            )
            for rank, policy in enumerate(catalog[channel]):
                candidate_rows.append(
                    {
                        "channel": channel,
                        "signal_id": signal_id,
                        "candidate_id": policy.candidate_id,
                        "role": policy.role,
                        "strategy_fingerprint": policy.strategy_fingerprint,
                        "execution_fingerprint": policy.execution_fingerprint,
                        "registered_at_utc": registered.isoformat(),
                        "outcome_at_utc": outcome.isoformat(),
                        "day": registered.date().isoformat(),
                        "entry_count": rank + 1,
                        "exit_reason": "shadow_exit",
                        "status": "closed",
                        "net_eur": float(3 - rank),
                        "mfe_eur": float(6 - rank),
                        "mae_eur": float(-(rank + 1)),
                        "complete": True,
                        "evidence_blockers": [],
                        "control_prediction": {
                            "entry_price_error": 0.2,
                            "entry_time_error_ms": 40,
                            "net_eur_error": 0.1,
                        },
                    }
                )
    return candidate_rows, actual_rows


@pytest.mark.parametrize(
    "blocker",
    [
        "control_mirror_mismatch",
        "tick_gap",
        "telegram_lineage_incomplete",
        "money_contract_missing",
        "registered_after_outcome",
    ],
)
def test_report_refuses_to_rank_when_required_evidence_is_blocked(blocker):
    candidate_rows, actual_rows = _complete_rows()
    if blocker == "control_mirror_mismatch":
        actual_rows[0]["control_mirror_match"] = False
    elif blocker == "telegram_lineage_incomplete":
        actual_rows[0]["telegram_lineage_complete"] = False
    elif blocker == "registered_after_outcome":
        candidate_rows[0]["registered_at_utc"] = (
            datetime.fromisoformat(candidate_rows[0]["outcome_at_utc"])
            + timedelta(seconds=1)
        ).isoformat()
    else:
        candidate_rows[0]["evidence_blockers"] = [blocker]

    report = build_report(candidate_rows, actual_rows)

    assert report["ranking_allowed"] is False
    assert report["winner"] is None
    assert blocker in report["blockers"]


def test_report_summarizes_signals_days_candidates_and_nine_pairings():
    candidate_rows, actual_rows = _complete_rows()

    report = build_report(candidate_rows, actual_rows)

    assert report["ranking_allowed"] is True
    assert report["checkpoint"]["label"] == "diagnostic"
    assert report["checkpoint"]["untouched_signals"] == 30
    assert len(report["signals"]) == 30
    assert len(report["days"]) == 1
    assert len(report["candidate_totals"]) == 6
    assert len(report["pairings"]) == 9
    assert report["winner"] == {
        "canal1": "dubai_balanced_v1",
        "canal2": "gold_now_555_v1",
        "pairing": "dubai_balanced_v1+gold_now_555_v1",
    }
    assert report["candidate_totals"]["dubai_balanced_v1"]["net_eur"] == 45.0
    assert report["candidate_totals"]["gold_now_555_v1"]["net_eur"] == 45.0
    assert report["pairings"][0]["net_eur"] == 90.0


@pytest.mark.parametrize(
    ("signal_count", "label"),
    [(14, "collecting"), (15, "diagnostic"), (45, "provisional"), (100, "evidence")],
)
def test_checkpoint_labels_use_untouched_signal_count(signal_count, label):
    rows, actual = _complete_rows(signals_per_channel=signal_count)
    report = build_report(rows, actual)

    assert report["checkpoint"]["label"] == label
    assert report["checkpoint"]["per_channel"] == {
        "canal1": label,
        "canal2": label,
    }
    assert report["ranking_allowed"] is (signal_count >= 15)


def test_causal_prediction_slippage_is_measured_but_does_not_block_ranking():
    candidate_rows, actual_rows = _complete_rows()
    candidate_rows[0]["control_prediction"] = {
        "entry_price_error": 2.75,
        "entry_time_error_ms": 850,
        "net_eur_error": -4.2,
    }

    report = build_report(candidate_rows, actual_rows)

    signal = next(
        row
        for row in report["signals"]
        if row["signal_id"] == candidate_rows[0]["signal_id"]
        and row["channel"] == candidate_rows[0]["channel"]
    )
    assert report["ranking_allowed"] is True
    assert signal["control_prediction"]["entry_price_error"] == 2.75
    assert "control_mirror_mismatch" not in report["blockers"]


def test_missing_actual_signal_and_changed_fingerprint_are_explicit_blockers():
    candidate_rows, actual_rows = _complete_rows()
    actual_rows.pop()
    candidate_rows[-1]["strategy_fingerprint"] = "f" * 64

    report = build_report(candidate_rows, actual_rows)

    assert report["ranking_allowed"] is False
    assert report["winner"] is None
    assert "actual_evidence_missing" in report["blockers"]
    assert "candidate_fingerprint_changed" in report["blockers"]


def test_duplicate_candidate_result_is_not_silently_double_counted():
    candidate_rows, actual_rows = _complete_rows()
    candidate_rows.append(dict(candidate_rows[0]))

    report = build_report(candidate_rows, actual_rows)

    assert report["ranking_allowed"] is False
    assert report["winner"] is None
    assert "duplicate_candidate_result" in report["blockers"]


def test_open_candidate_is_visible_but_cannot_enter_a_ranking():
    candidate_rows, actual_rows = _complete_rows()
    candidate_rows[0]["status"] = "open"

    report = build_report(candidate_rows, actual_rows)

    assert report["ranking_allowed"] is False
    assert report["winner"] is None
    assert "candidate_not_terminal" in report["blockers"]


def test_report_uses_the_prospectively_recorded_control_identity():
    candidate_rows, actual_rows = _complete_rows()
    target_signal = "canal2_1000"
    for row in candidate_rows:
        if row["channel"] != "canal2":
            continue
        row["role"] = (
            "live_control"
            if row["candidate_id"] == "gold_now_c490_v1"
            else "candidate"
        )
        if row["signal_id"] == target_signal:
            row["control_prediction"] = {"source": row["candidate_id"]}

    report = build_report(candidate_rows, actual_rows)

    signal = next(
        row for row in report["signals"]
        if row["signal_id"] == target_signal and row["channel"] == "canal2"
    )
    assert report["ranking_allowed"] is True
    assert signal["control_prediction"] == {"source": "gold_now_c490_v1"}
    assert signal["candidates"]["gold_now_c490_v1"]["role"] == "live_control"


def test_invalid_money_is_never_silently_converted_to_zero_for_ranking():
    candidate_rows, actual_rows = _complete_rows()
    candidate_rows[0]["net_eur"] = float("nan")
    actual_rows[1]["net_eur"] = None

    report = build_report(candidate_rows, actual_rows)

    assert report["ranking_allowed"] is False
    assert report["winner"] is None
    assert "invalid_candidate_result" in report["blockers"]
    assert "invalid_actual_result" in report["blockers"]
