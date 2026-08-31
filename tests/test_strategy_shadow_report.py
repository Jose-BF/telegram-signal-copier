from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from strategy_shadow_catalog import build_shadow_catalog
from strategy_shadow_report import build_report


def _complete_rows(*, signals_per_channel: int = 15):
    catalog = build_shadow_catalog()
    source_commit = "a" * 40
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
                    "mt5_reconciled": True,
                    "source_commit": source_commit,
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
                        "registration_source": "observed_runtime",
                        "source_commit": source_commit,
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


def test_invalid_candidate_role_blocks_comparison_without_crashing():
    candidate_rows, actual_rows = _complete_rows(signals_per_channel=1)
    candidate_rows[0]["role"] = "unexpected_role"

    report = build_report(candidate_rows, actual_rows)

    assert report["comparison_allowed"] is False
    assert report["shadow_leader"] is None
    assert "invalid_candidate_role" in report["comparison_blockers"]


def test_report_summarizes_signals_days_candidates_and_nine_pairings():
    candidate_rows, actual_rows = _complete_rows()

    report = build_report(candidate_rows, actual_rows)

    assert report["ranking_allowed"] is True
    assert report["comparison_allowed"] is True
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
    assert report["matrix"]["canal1"]["complete"] is True
    assert report["matrix"]["canal2"]["complete"] is True
    assert next(iter(report["signals"]))["candidates"][
        "dubai_balanced_v1"
    ]["registration_source"] == "observed_runtime"


def test_complete_virtual_matrix_can_be_compared_before_actual_calibration():
    candidate_rows, _actual_rows = _complete_rows(signals_per_channel=1)

    report = build_report(candidate_rows, [])

    assert report["ranking_allowed"] is False
    assert report["comparison_allowed"] is True
    assert report["shadow_leader"] == {
        "canal1": "dubai_balanced_v1",
        "canal2": "gold_now_555_v1",
        "pairing": "dubai_balanced_v1+gold_now_555_v1",
    }
    assert "actual_evidence_missing" in report["blockers"]
    assert "actual_evidence_missing" not in report["comparison_blockers"]


def test_empty_matrix_never_claims_that_a_comparison_is_ready():
    report = build_report([], [])

    assert report["comparison_allowed"] is False
    assert report["shadow_leader"] is None
    assert "no_eligible_signals" in report["comparison_blockers"]


def test_actual_calibration_blocker_does_not_mark_complete_candidates_blocked():
    candidate_rows, actual_rows = _complete_rows(signals_per_channel=1)
    actual_rows[0]["control_mirror_match"] = False

    report = build_report(candidate_rows, actual_rows)

    signal = next(
        row for row in report["signals"]
        if row["channel"] == actual_rows[0]["channel"]
        and row["signal_id"] == actual_rows[0]["signal_id"]
    )
    assert "control_mirror_mismatch" in signal["blockers"]
    assert all(
        candidate["blockers"] == []
        for candidate in signal["candidates"].values()
    )


def test_unreconciled_mt5_result_is_visible_but_not_called_complete():
    candidate_rows, actual_rows = _complete_rows(signals_per_channel=1)
    actual_rows[0]["mt5_reconciled"] = False

    report = build_report(candidate_rows, actual_rows)

    signal = next(
        row for row in report["signals"]
        if row["channel"] == actual_rows[0]["channel"]
        and row["signal_id"] == actual_rows[0]["signal_id"]
    )
    assert "mt5_reconciliation_incomplete" in report["blockers"]
    assert signal["actual"]["net_eur"] == 1.0
    assert signal["actual"]["complete"] is False
    assert report["days"][0]["actual"]["complete"] is False
    assert report["days"][0]["actual"]["net_eur"] is None


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


def test_channel_verdict_is_independent_when_other_channel_has_no_evidence():
    candidate_rows, actual_rows = _complete_rows()
    candidate_rows = [
        row for row in candidate_rows if row["channel"] == "canal2"
    ]
    actual_rows = [
        row for row in actual_rows if row["channel"] == "canal2"
    ]

    report = build_report(candidate_rows, actual_rows)

    assert report["ranking_allowed"] is False
    assert report["channels"]["canal2"]["ranking_allowed"] is True
    assert report["channels"]["canal2"]["winner"] == "gold_now_555_v1"
    assert report["channels"]["canal1"]["ranking_allowed"] is False
    assert "no_eligible_signals" in report["channels"]["canal1"]["blockers"]


def test_actual_blocker_in_one_channel_does_not_contaminate_other_channel():
    candidate_rows, actual_rows = _complete_rows()
    canal1_actual = next(
        row for row in actual_rows if row["channel"] == "canal1"
    )
    canal1_actual["control_mirror_match"] = False

    report = build_report(candidate_rows, actual_rows)

    assert report["channels"]["canal1"]["ranking_allowed"] is False
    assert "control_mirror_mismatch" in report["channels"]["canal1"][
        "blockers"
    ]
    assert report["channels"]["canal2"]["ranking_allowed"] is True
    assert "control_mirror_mismatch" not in report["channels"]["canal2"][
        "blockers"
    ]


def test_report_computes_control_parity_from_structural_signatures():
    candidate_rows, actual_rows = _complete_rows()
    catalog = build_shadow_catalog()
    controls = {
        channel: next(
            policy.candidate_id
            for policy in policies
            if policy.role == "live_control"
        )
        for channel, policies in catalog.items()
    }
    candidates = {
        (row["channel"], row["signal_id"], row["candidate_id"]): row
        for row in candidate_rows
    }
    for actual in actual_rows:
        actual.pop("control_mirror_match")
        signature = {
            "schema_version": 1,
            "strategy_id": controls[actual["channel"]],
            "positions": [{"leg_index": 0, "volume": 0.04}],
        }
        actual["logic_signature"] = signature
        candidates[
            actual["channel"],
            actual["signal_id"],
            controls[actual["channel"]],
        ]["logic_signature"] = signature

    report = build_report(candidate_rows, actual_rows)

    assert report["ranking_allowed"] is True
    assert all(
        signal["actual"]["control_mirror_match"] is True
        for signal in report["signals"]
    )
    assert all(
        signal["actual"]["control_parity"]["differences"] == []
        for signal in report["signals"]
    )
    assert report["control_parity"]["canal1"]["matched"] == 15
    assert report["control_parity"]["canal2"]["matched"] == 15
    assert report["control_parity"]["canal2"]["unverified"] == 0


def test_missing_structural_signature_is_unverified_not_silently_mismatched():
    candidate_rows, actual_rows = _complete_rows(signals_per_channel=1)
    actual_rows[0].pop("control_mirror_match")

    report = build_report(candidate_rows, actual_rows)

    assert "control_mirror_unverified" in report["blockers"]
    assert "control_mirror_mismatch" not in report["signals"][0]["blockers"]


def test_malformed_explicit_mirror_value_is_treated_as_unverified():
    candidate_rows, actual_rows = _complete_rows(signals_per_channel=1)
    actual_rows[0]["control_mirror_match"] = []

    report = build_report(candidate_rows, actual_rows)

    assert "control_mirror_unverified" in report["blockers"]


def test_source_commit_mismatch_blocks_adoption_but_not_shadow_comparison():
    candidate_rows, actual_rows = _complete_rows()
    target = actual_rows[0]
    for row in candidate_rows:
        if (
            row["channel"] == target["channel"]
            and row["signal_id"] == target["signal_id"]
            and row["role"] == "live_control"
        ):
            row["source_commit"] = "b" * 40

    report = build_report(candidate_rows, actual_rows)

    assert report["comparison_allowed"] is True
    assert report["ranking_allowed"] is False
    assert "source_commit_mismatch" in report["blockers"]


def test_unknown_source_commit_blocks_adoption():
    candidate_rows, actual_rows = _complete_rows()
    actual_rows[0]["source_commit"] = None

    report = build_report(candidate_rows, actual_rows)

    assert report["ranking_allowed"] is False
    assert "source_commit_unverified" in report["blockers"]


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


def test_incomplete_candidate_is_never_presented_as_zero_profit():
    candidate_rows, actual_rows = _complete_rows(signals_per_channel=1)
    target = candidate_rows[0]
    target.update({
        "status": "incomplete",
        "complete": False,
        "net_eur": None,
        "mfe_eur": None,
        "mae_eur": None,
        "evidence_blockers": ["signal_registration_missing"],
    })

    report = build_report(candidate_rows, actual_rows)

    candidate = report["signals"][0]["candidates"][target["candidate_id"]]
    total = report["candidate_totals"][target["candidate_id"]]
    assert candidate["net_eur"] is None
    assert total["net_eur"] is None
    assert total["settled_net_eur"] == 0.0
    assert report["pairings"][0]["net_eur"] is not None
    assert any(row["net_eur"] is None for row in report["pairings"])


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
    affected = next(
        row for row in report["signals"]
        if row["signal_id"] == actual_rows[1]["signal_id"]
        and row["channel"] == actual_rows[1]["channel"]
    )
    assert affected["actual"]["net_eur"] is None
