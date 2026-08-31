from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from strategy_shadow_catalog import build_shadow_catalog
from strategy_shadow_contracts import (
    ShadowManagementEvent,
    ShadowPosition,
    ShadowSignalState,
    ShadowTick,
)
from strategy_shadow_engine import advance_tick, apply_management, register_signal
from strategy_shadow_manifest import build_catalog_manifest
from strategy_shadow_parity import compare_logic_signatures, shadow_logic_signature
from strategy_shadow_settlement import (
    ParquetShadowTickReader,
    ShadowRegistrationTickRead,
    ShadowTickRead,
    actual_rows_from_ledger,
    eligible_signal_ids,
    reconstruct_registration_records,
    settle_shadow_records,
)
from tools import build_strategy_shadow_report


BASE = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
BASE_MSC = int(BASE.timestamp() * 1000)


def test_cli_loader_keeps_signal_received_as_the_independent_denominator(
        tmp_path):
    path = tmp_path / "events.jsonl"
    rows = [
        {"sig": "bot", "ev": "strategy_shadow_runtime_started"},
        {"sig": "canal1_3999", "ev": "signal_received"},
        {"sig": "canal2_4000", "ev": "gold_555_entry_watch_started"},
        {"sig": "canal1_3999", "ev": "unrelated_event"},
    ]
    path.write_text(
        "".join(f"{json.dumps(row)}\n" for row in rows),
        encoding="utf-8",
    )

    loaded = build_strategy_shadow_report._load_shadow_records(path)

    assert [row["ev"] for row in loaded] == [
        "strategy_shadow_runtime_started",
        "signal_received",
        "gold_555_entry_watch_started",
    ]


def test_builder_trusts_only_matching_historical_shadow_contracts(monkeypatch):
    commit = "a" * 40
    current = {
        path: f"blob-{index}"
        for index, path in enumerate(
            build_strategy_shadow_report.SHADOW_CONTRACT_PATHS
        )
    }

    def matching_blob(ref, path):
        if ref == "WORKTREE":
            return current[path]
        return current[path] if ref == commit else "different"

    monkeypatch.setattr(
        build_strategy_shadow_report,
        "_git_blob_id",
        matching_blob,
    )

    trusted = build_strategy_shadow_report._trusted_source_commits([
        {"code_commit": commit},
        {"code_commit": "b" * 40},
    ])

    assert set(trusted) == {commit}
    assert len(trusted[commit]) == 64


def _gold_reconstruction_inputs(*, actionable_management: bool = False):
    catalog = build_shadow_catalog()
    commit = "a" * 40
    session_id = "session-reconstruct"
    revision_id = "msgrev-reconstruct"
    decision_id = "decision-reconstruct"
    signal_id = "canal2_4000"
    runtime_started = {
        "sig": "bot",
        "ev": "strategy_shadow_runtime_started",
        "session_id": session_id,
        "ts": (BASE - timedelta(seconds=1)).isoformat(),
        "code_commit": commit,
        "candidates": {
            channel: [policy.candidate_id for policy in policies]
            for channel, policies in catalog.items()
        },
        "controls": {
            "canal1": "dubai_balanced_v1",
            "canal2": "gold_now_555_v1",
        },
    }
    signal_received = {
        "sig": signal_id,
        "ev": "signal_received",
        "channel": "canal2",
        "direction": "BUY",
        "entry_source_kind": "telegram_now",
        "live_strategy_id": "gold_now_555_v1",
        "live_strategy_fingerprint": catalog["canal2"][0].strategy_fingerprint,
        "tg_ts": BASE.isoformat(),
        "message_revision_id": revision_id,
        "decision_id": decision_id,
        "session_id": session_id,
        "ts": (BASE + timedelta(milliseconds=500)).isoformat(),
        "code_commit": commit,
        "event_id": "event-signal",
        "payload_sha256": "1" * 64,
    }
    watch_started = {
        "sig": signal_id,
        "ev": "gold_555_entry_watch_started",
        "strategy_id": "gold_now_555_v1",
        "strategy_fingerprint": catalog["canal2"][0].strategy_fingerprint,
        "reference_price": 100.2,
        "reference_bid": 100.0,
        "reference_ask": 100.2,
        "reference_tick_time_msc": BASE_MSC + 10_800_000,
        "intent": {
            "message_id": 4000,
            "direction": "BUY",
            "source_kind": "telegram_now",
            "telegram_timestamp": BASE.isoformat(),
        },
        "watch": {
            "direction": "BUY",
            "reference": 100.2,
            "observed_at": BASE.isoformat(),
        },
        "message_revision_id": revision_id,
        "decision_id": decision_id,
        "session_id": session_id,
        "ts": (BASE + timedelta(seconds=1)).isoformat(),
        "code_commit": commit,
        "event_id": "event-watch",
        "payload_sha256": "2" * 64,
    }
    management = []
    if actionable_management:
        management.append({
            "message_id": 4001,
            "observed_ts_utc": (BASE + timedelta(minutes=1)).isoformat(),
            "text": "Close now",
            "classified_action": "CLOSE_ALL",
            "modality": "executable",
            "execution_options": ["CLOSE_ALL"],
        })
    provider_catalog = {
        "signals": [{
            "provider_signal_id": signal_id,
            "record_type": "formal_signal",
            "channel": "canal2",
            "root_message_id": 4000,
            "direction": "BUY",
            "semantic_status": "complete",
            "semantic_gaps": [],
            "canonicalization_issues": [],
            "management_events": management,
            "entry_contract": {
                "status": "ready",
                "trigger_message_id": 4000,
                "trigger_kind": "text",
                "trigger_telegram_utc": BASE.isoformat(),
                "direction": "BUY",
                "blockers": [],
            },
        }],
    }
    return {
        "records": [runtime_started, signal_received, watch_started],
        "provider_catalog": provider_catalog,
        "trusted_commits": {commit: "verified-code-contract"},
        "signal_id": signal_id,
    }


class ReconstructionReader:
    def __init__(self, *, blockers=()):
        self.blockers = tuple(blockers)

    def registration_tick_evidence(self, **_kwargs):
        return ShadowRegistrationTickRead(
            normalized_time_msc=(None if self.blockers else BASE_MSC),
            complete=not self.blockers,
            evidence_id="verified-registration-tick",
            blockers=self.blockers,
        )


def test_missing_gold_registration_is_reconstructed_only_from_verified_sources():
    inputs = _gold_reconstruction_inputs()

    records, audits = reconstruct_registration_records(
        inputs["records"],
        provider_catalog=inputs["provider_catalog"],
        tick_reader=ReconstructionReader(),
        trusted_source_commits=inputs["trusted_commits"],
        since=BASE.date(),
        until=BASE.date(),
    )

    assert len(records) == 3
    assert {row["candidate_id"] for row in records} == {
        "gold_now_555_v1",
        "gold_now_b210_v1",
        "gold_now_c490_v1",
    }
    assert all(
        row["registration_source"]
        == "reconstructed_from_upstream_evidence"
        for row in records
    )
    assert all(row["state"]["registered_tick_msc"] == BASE_MSC for row in records)
    assert all(row["state"]["reference_price"] == 100.2 for row in records)
    assert all(row["message_revision_id"] == "msgrev-reconstruct" for row in records)
    assert audits == ({
        "channel": "canal2",
        "signal_id": inputs["signal_id"],
        "status": "reconstructed",
        "reconstructed_candidates": [
            "gold_now_555_v1",
            "gold_now_b210_v1",
            "gold_now_c490_v1",
        ],
        "blockers": [],
        "evidence_id": records[0]["reconstruction_evidence_id"],
        "source_commit": "a" * 40,
        "registered_tick_msc": BASE_MSC,
    },)


def test_reconstruction_refuses_unverified_strategy_code():
    inputs = _gold_reconstruction_inputs()

    records, audits = reconstruct_registration_records(
        inputs["records"],
        provider_catalog=inputs["provider_catalog"],
        tick_reader=ReconstructionReader(),
        trusted_source_commits={},
        since=BASE.date(),
        until=BASE.date(),
    )

    assert records == ()
    assert audits[0]["status"] == "blocked"
    assert audits[0]["blockers"] == ["reconstruction_source_code_unverified"]


def test_catalog_manifest_cannot_replace_historical_engine_verification():
    inputs = _gold_reconstruction_inputs()
    inputs["records"][0]["catalog_manifest"] = build_catalog_manifest()

    records, audits = reconstruct_registration_records(
        inputs["records"],
        provider_catalog=inputs["provider_catalog"],
        tick_reader=ReconstructionReader(),
        trusted_source_commits={},
        since=BASE.date(),
        until=BASE.date(),
    )

    assert records == ()
    assert audits[0]["blockers"] == [
        "reconstruction_source_code_unverified"
    ]


def test_reconstruction_ignores_signals_from_before_shadow_runtime():
    inputs = _gold_reconstruction_inputs()
    records = [
        record
        for record in inputs["records"]
        if record["ev"] != "strategy_shadow_runtime_started"
    ]

    reconstructed, audits = reconstruct_registration_records(
        records,
        provider_catalog=inputs["provider_catalog"],
        tick_reader=ReconstructionReader(),
        trusted_source_commits=inputs["trusted_commits"],
        since=BASE.date(),
        until=BASE.date(),
    )

    assert reconstructed == ()
    assert audits == ()


def test_reconstruction_refuses_executable_provider_management():
    inputs = _gold_reconstruction_inputs(actionable_management=True)

    records, audits = reconstruct_registration_records(
        inputs["records"],
        provider_catalog=inputs["provider_catalog"],
        tick_reader=ReconstructionReader(),
        trusted_source_commits=inputs["trusted_commits"],
        since=BASE.date(),
        until=BASE.date(),
    )

    assert records == ()
    assert audits[0]["status"] == "blocked"
    assert audits[0]["blockers"] == [
        "reconstruction_provider_management_requires_replay"
    ]


def test_reconstruction_refuses_an_unverified_reference_tick():
    inputs = _gold_reconstruction_inputs()

    records, audits = reconstruct_registration_records(
        inputs["records"],
        provider_catalog=inputs["provider_catalog"],
        tick_reader=ReconstructionReader(
            blockers=("registration_tick_quote_mismatch",),
        ),
        trusted_source_commits=inputs["trusted_commits"],
        since=BASE.date(),
        until=BASE.date(),
    )

    assert records == ()
    assert audits[0]["status"] == "blocked"
    assert audits[0]["blockers"] == ["registration_tick_quote_mismatch"]


def test_malformed_watch_reference_blocks_instead_of_crashing_settlement():
    inputs = _gold_reconstruction_inputs()
    inputs["records"][2]["watch"]["reference"] = None

    records, audits = reconstruct_registration_records(
        inputs["records"],
        provider_catalog=inputs["provider_catalog"],
        tick_reader=ReconstructionReader(),
        trusted_source_commits=inputs["trusted_commits"],
        since=BASE.date(),
        until=BASE.date(),
    )

    assert records == ()
    assert "reconstruction_reference_price_mismatch" in audits[0]["blockers"]


def test_reconstruction_preserves_an_authentic_candidate_registration():
    inputs = _gold_reconstruction_inputs()
    policy = build_shadow_catalog()["canal2"][0]
    state = register_signal(
        policy,
        signal_id=inputs["signal_id"],
        source_message_id=4000,
        direction="BUY",
        registered_at_utc=BASE.isoformat(),
        registered_tick_msc=BASE_MSC,
        reference_price=100.2,
    )
    existing = {
        "sig": inputs["signal_id"],
        "ev": "strategy_shadow_registered",
        "channel": "canal2",
        "candidate_id": policy.candidate_id,
        "role": policy.role,
        "strategy_fingerprint": policy.strategy_fingerprint,
        "execution_fingerprint": policy.execution_fingerprint,
        "state_hash": state.state_hash,
        "state": state.to_dict(),
        "message_revision_id": "msgrev-reconstruct",
        "decision_id": "decision-reconstruct",
    }

    records, audits = reconstruct_registration_records(
        [*inputs["records"], existing],
        provider_catalog=inputs["provider_catalog"],
        tick_reader=ReconstructionReader(),
        trusted_source_commits=inputs["trusted_commits"],
        since=BASE.date(),
        until=BASE.date(),
    )

    assert {row["candidate_id"] for row in records} == {
        "gold_now_b210_v1",
        "gold_now_c490_v1",
    }
    assert audits[0]["reconstructed_candidates"] == [
        "gold_now_b210_v1",
        "gold_now_c490_v1",
    ]


def test_ledger_prefilter_uses_only_the_requested_active_shadow_cohort():
    records = [
        {
            "sig": "canal1_old",
            "ev": "signal_received",
            "channel": "canal1",
            "session_id": "before-shadow",
            "ts": BASE.isoformat(),
        },
        {
            "sig": "bot",
            "ev": "strategy_shadow_runtime_started",
            "session_id": "active-shadow",
            "ts": (BASE + timedelta(seconds=1)).isoformat(),
        },
        {
            "sig": "canal1_current",
            "ev": "signal_received",
            "channel": "canal1",
            "session_id": "active-shadow",
            "ts": (BASE + timedelta(seconds=2)).isoformat(),
        },
    ]

    assert eligible_signal_ids(
        records,
        since=BASE.date(),
        until=BASE.date(),
    ) == {"canal1_current"}


def _tick(seconds: int, bid: float, ask: float) -> ShadowTick:
    observed = BASE + timedelta(seconds=seconds)
    return ShadowTick(
        time_msc=int(observed.timestamp() * 1000),
        bid=bid,
        ask=ask,
        observed_at_utc=observed.isoformat(),
        positive_eur_per_move_lot=100.0,
        negative_eur_per_move_lot=100.0,
        money_evidence_id="money-verified",
    )


def _dubai_registrations() -> list[dict]:
    rows = []
    for policy in build_shadow_catalog()["canal1"]:
        state = register_signal(
            policy,
            signal_id="canal1_3000",
            source_message_id=3000,
            direction="BUY",
            registered_at_utc=BASE.isoformat(),
            registered_tick_msc=BASE_MSC,
        )
        rows.append({
            "sig": state.signal_id,
            "ev": "strategy_shadow_registered",
            "channel": state.channel,
            "candidate_id": state.candidate_id,
            "role": policy.role,
            "strategy_fingerprint": state.strategy_fingerprint,
            "execution_fingerprint": state.execution_fingerprint,
            "state_hash": state.state_hash,
            "state": state.to_dict(),
            "message_revision_id": "msgrev-1",
            "decision_id": "decision-1",
            "ts": BASE.isoformat(),
        })
    return rows


class CompleteReader:
    def read(self, _start: datetime, _end: datetime) -> ShadowTickRead:
        return ShadowTickRead(
            ticks=(
                _tick(1, 100.0, 100.2),
                _tick(41 * 60, 99.8, 100.0),
            ),
            complete=True,
            evidence_id="ticks-complete",
        )

    def cost_blockers(self, _state: ShadowSignalState) -> tuple[str, ...]:
        return ()


class RecordingReader(CompleteReader):
    def __init__(self):
        self.starts: list[datetime] = []

    def read(self, start: datetime, end: datetime) -> ShadowTickRead:
        self.starts.append(start)
        return super().read(start, end)


def test_settlement_rebuilds_all_three_rows_for_every_registered_signal():
    result = settle_shadow_records(
        _dubai_registrations(),
        tick_reader=CompleteReader(),
        since=BASE.date(),
        until=BASE.date(),
    )

    assert len(result["candidate_rows"]) == 3
    assert {row["candidate_id"] for row in result["candidate_rows"]} == {
        "dubai_balanced_v1",
        "dubai_frontloaded_30m_v1",
        "dubai_frontloaded_40m_v1",
    }
    assert all(
        row["logic_signature"]["strategy_id"] == row["candidate_id"]
        for row in result["candidate_rows"]
    )


def test_actual_ledger_exposes_structural_signature_without_claiming_match():
    policy = build_shadow_catalog()["canal1"][0]
    ledger = [{
        "sig_id": "canal1_3000",
        "channel": "canal1",
        "direction": "BUY",
        "signal_dt_utc": BASE.isoformat(),
        "n_positions": 1,
        "pnl_real_mt5": -0.40,
        "status": "closed",
        "strategy_snapshot": {
            "live_strategy_id": policy.candidate_id,
            "live_strategy_fingerprint": policy.strategy_fingerprint,
            "code_commit": "a" * 40,
        },
        "positions": [{
            "role": "market_a",
            "volume": 0.01,
            "open_price": 101.20,
            "is_closed": True,
            "close_reason": "bot_close",
            "open_deal": {"comment": "c1_3000_dv1"},
            "tp_history": [],
            "sl_history": [],
        }],
        "reconciled_ok": True,
        "pnl_mt5_complete": True,
    }]

    actual = actual_rows_from_ledger(
        ledger,
        _dubai_registrations(),
        since=BASE.date(),
        until=BASE.date(),
    )

    assert "control_mirror_match" not in actual[0]
    assert actual[0]["logic_signature"]["strategy_id"] == (
        "dubai_balanced_v1"
    )
    assert actual[0]["logic_signature_blockers"] == []
    assert actual[0]["source_commit"] == "a" * 40


def test_no_position_ledger_can_certify_a_matching_cancelled_control():
    commit = "a" * 40
    registrations = []
    cancelled_control = None
    control_policy = None
    for policy in build_shadow_catalog()["canal2"]:
        state = register_signal(
            policy,
            signal_id="canal2_5000",
            source_message_id=5000,
            direction="BUY",
            registered_at_utc=BASE.isoformat(),
            registered_tick_msc=BASE_MSC,
            reference_price=100.2,
        )
        registrations.append({
            "sig": state.signal_id,
            "ev": "strategy_shadow_registered",
            "channel": state.channel,
            "candidate_id": state.candidate_id,
            "role": policy.role,
            "strategy_fingerprint": state.strategy_fingerprint,
            "execution_fingerprint": state.execution_fingerprint,
            "state_hash": state.state_hash,
            "state": state.to_dict(),
            "message_revision_id": "msgrev-5000",
            "decision_id": "decision-5000",
            "code_commit": commit,
            "ts": BASE.isoformat(),
        })
        if policy.role == "live_control":
            control_policy = policy
            cancelled_control = ShadowSignalState.from_dict({
                **state.to_dict(),
                "status": "cancelled",
                "exit_reason": "entry_expired",
            })
    ledger = [{
        "sig_id": "canal2_5000",
        "channel": "canal2",
        "direction": "BUY",
        "signal_dt_utc": BASE.isoformat(),
        "n_positions": 0,
        "pnl_real_mt5": 0,
        "status": "no_position",
        "strategy_snapshot": None,
        "positions": [],
        "reconciled_ok": None,
        "pnl_mt5_complete": True,
    }]

    actual = actual_rows_from_ledger(
        ledger,
        registrations,
        since=BASE.date(),
        until=BASE.date(),
    )[0]
    comparison = compare_logic_signatures(
        actual["logic_signature"],
        shadow_logic_signature(cancelled_control, control_policy),
    )

    assert actual["mt5_reconciled"] is True
    assert actual["reconciliation_basis"] == "no_position_zero_exposure"
    assert actual["source_commit"] == commit
    assert comparison["match"] is True


def test_settlement_certifies_matching_live_control_structure():
    policy = build_shadow_catalog()["canal1"][0]
    ledger = [{
        "sig_id": "canal1_3000",
        "channel": "canal1",
        "direction": "BUY",
        "signal_dt_utc": BASE.isoformat(),
        "n_positions": 1,
        "pnl_real_mt5": -0.40,
        "status": "closed",
        "strategy_snapshot": {
            "live_strategy_id": policy.candidate_id,
            "live_strategy_fingerprint": policy.strategy_fingerprint,
            "code_commit": "a" * 40,
        },
        "positions": [{
            "role": "market_a",
            "volume": 0.01,
            "open_price": 101.20,
            "is_closed": True,
            "close_reason": "bot_close",
            "open_deal": {"comment": "c1_3000_dv1"},
            "tp_history": [],
            "sl_history": [],
        }],
        "reconciled_ok": True,
        "pnl_mt5_complete": True,
    }]
    records = _dubai_registrations()
    actual = actual_rows_from_ledger(
        ledger,
        records,
        since=BASE.date(),
        until=BASE.date(),
    )

    result = settle_shadow_records(
        records,
        tick_reader=CompleteReader(),
        since=BASE.date(),
        until=BASE.date(),
        actual_rows=actual,
    )

    signal = result["report"]["signals"][0]
    assert signal["actual"]["control_mirror_match"] is True
    assert signal["actual"]["control_parity"]["differences"] == []


def test_settlement_starts_at_the_frozen_registration_tick_boundary():
    records = _dubai_registrations()
    earlier_msc = BASE_MSC - 750
    for record in records:
        state = dict(record["state"])
        state["registered_tick_msc"] = earlier_msc
        rebuilt = ShadowSignalState.from_dict(state)
        record["state"] = rebuilt.to_dict()
        record["state_hash"] = rebuilt.state_hash
    reader = RecordingReader()

    settle_shadow_records(
        records,
        tick_reader=reader,
        since=BASE.date(),
        until=BASE.date(),
    )

    assert reader.starts[0] == datetime.fromtimestamp(
        earlier_msc / 1000.0,
        tz=timezone.utc,
    )


def test_management_after_one_equal_millisecond_tick_waits_for_next_tick():
    live_first = ShadowTick(
        time_msc=_tick(1, 100.0, 100.2).time_msc,
        bid=100.0,
        ask=100.2,
        observed_at_utc=_tick(1, 100.0, 100.2).observed_at_utc,
        positive_eur_per_move_lot=100.0,
        negative_eur_per_move_lot=100.0,
        money_evidence_id="money-verified",
        flags=6,
    )
    first = ShadowTick(
        **{**live_first.to_dict(), "flags": 134},
    )
    second = ShadowTick(
        time_msc=first.time_msc,
        bid=100.1,
        ask=100.3,
        observed_at_utc=first.observed_at_utc,
        positive_eur_per_move_lot=100.0,
        negative_eur_per_move_lot=100.0,
        money_evidence_id="money-verified",
    )
    records = _dubai_registrations()
    policies = {
        policy.candidate_id: policy
        for policy in build_shadow_catalog()["canal1"]
    }
    for registration in list(records):
        policy = policies[registration["candidate_id"]]
        initial = register_signal(
            policy,
            signal_id="canal1_3000",
            source_message_id=3000,
            direction="BUY",
            registered_at_utc=BASE.isoformat(),
            registered_tick_msc=BASE_MSC,
        )
        after_first = advance_tick(policy, initial, live_first).state
        event = ShadowManagementEvent(
            event_id="close-between-equal-ms-ticks",
            signal_id="canal1_3000",
            action="CLOSE_ALL",
            observed_at_utc=first.observed_at_utc,
            observed_tick_msc=first.time_msc,
        )
        managed = apply_management(policy, after_first, event).state
        records.append({
            "sig": managed.signal_id,
            "ev": "strategy_shadow_transition",
            "channel": managed.channel,
            "candidate_id": managed.candidate_id,
            "transition": "provider_close_pending",
            "reason": "CLOSE_ALL",
            "transition_tick_msc": first.time_msc,
            "state_hash": managed.state_hash,
            "state": managed.to_dict(),
            "ts": first.observed_at_utc,
        })

    class EqualMillisecondReader:
        def read(self, _start: datetime, _end: datetime) -> ShadowTickRead:
            return ShadowTickRead(
                ticks=(first, second),
                complete=True,
                evidence_id="equal-ms-source-order",
            )

        def cost_blockers(self, _state: ShadowSignalState) -> tuple[str, ...]:
            return ()

    result = settle_shadow_records(
        records,
        tick_reader=EqualMillisecondReader(),
        since=BASE.date(),
        until=BASE.date(),
    )

    assert all(row["entry_count"] == 1 for row in result["candidate_rows"])
    assert all(
        row["exit_reason"] == "provider_close"
        for row in result["candidate_rows"]
    )
    assert all(row["complete"] for row in result["candidate_rows"])
    assert all(row["status"] == "closed" for row in result["candidate_rows"])
    assert result["report"]["comparison_allowed"] is True
    assert result["report"]["matrix"]["canal1"] == {
        "eligible_signals": 1,
        "expected_rows": 3,
        "observed_rows": 3,
        "settled_rows": 3,
        "blocked_rows": 0,
        "open_rows": 0,
        "complete": True,
    }


def test_missing_candidate_registration_is_visible_instead_of_omitted():
    registrations = _dubai_registrations()[:-1]

    result = settle_shadow_records(
        registrations,
        tick_reader=CompleteReader(),
        since=BASE.date(),
        until=BASE.date(),
    )

    assert len(result["candidate_rows"]) == 3
    missing = next(
        row for row in result["candidate_rows"]
        if row["candidate_id"] == "dubai_frontloaded_40m_v1"
    )
    assert missing["status"] == "incomplete"
    assert missing["complete"] is False
    assert missing["evidence_blockers"] == ["candidate_registration_missing"]
    assert missing["net_eur"] is None
    assert missing["mfe_eur"] is None
    assert missing["mae_eur"] is None
    assert result["report"]["comparison_allowed"] is False
    assert result["report"]["matrix"]["canal1"]["blocked_rows"] == 1
    assert result["report"]["candidate_totals"][
        "dubai_frontloaded_40m_v1"
    ]["net_eur"] is None


def test_signal_received_in_active_shadow_session_cannot_be_silently_omitted():
    records = [
        {
            "sig": "bot",
            "ev": "strategy_shadow_runtime_started",
            "session_id": "session-live",
            "ts": (BASE - timedelta(seconds=1)).isoformat(),
        },
        {
            "sig": "canal1_3999",
            "ev": "signal_received",
            "channel": "canal1",
            "direction": "BUY",
            "session_id": "session-live",
            "ts": BASE.isoformat(),
            "tg_ts": BASE.isoformat(),
        },
    ]

    result = settle_shadow_records(
        records,
        tick_reader=CompleteReader(),
        since=BASE.date(),
        until=BASE.date(),
    )

    assert len(result["candidate_rows"]) == 3
    assert all(
        row["evidence_blockers"] == ["signal_registration_missing"]
        for row in result["candidate_rows"]
    )
    assert result["report"]["matrix"]["canal1"] == {
        "eligible_signals": 1,
        "expected_rows": 3,
        "observed_rows": 3,
        "settled_rows": 0,
        "blocked_rows": 3,
        "open_rows": 0,
        "complete": False,
    }


def test_actual_ledger_row_remains_visible_when_all_shadow_rows_are_missing():
    records = [
        {
            "sig": "bot",
            "ev": "strategy_shadow_runtime_started",
            "session_id": "session-live",
            "ts": (BASE - timedelta(seconds=1)).isoformat(),
        },
        {
            "sig": "canal1_3999",
            "ev": "signal_received",
            "channel": "canal1",
            "direction": "BUY",
            "session_id": "session-live",
            "ts": BASE.isoformat(),
        },
    ]
    ledger = [{
        "sig_id": "canal1_3999",
        "channel": "canal1",
        "signal_dt_utc": BASE.isoformat(),
        "n_positions": 1,
        "pnl_real_mt5": 4.25,
        "status": "closed",
        "positions": [],
        "reconciled_ok": True,
        "pnl_mt5_complete": True,
    }]

    actual = actual_rows_from_ledger(
        ledger,
        (record for record in records),
        since=BASE.date(),
        until=BASE.date(),
    )

    assert len(actual) == 1
    assert actual[0]["signal_id"] == "canal1_3999"
    assert actual[0]["net_eur"] == 4.25
    assert actual[0]["telegram_lineage_complete"] is False


def test_duplicate_actual_ledger_rows_reach_report_validation():
    records = _dubai_registrations()
    base_row = {
        "sig_id": "canal1_3000",
        "channel": "canal1",
        "signal_dt_utc": BASE.isoformat(),
        "n_positions": 1,
        "status": "closed",
        "positions": [],
        "reconciled_ok": True,
        "pnl_mt5_complete": True,
    }
    actual = actual_rows_from_ledger(
        [
            {**base_row, "pnl_real_mt5": 1.0},
            {**base_row, "pnl_real_mt5": 2.0},
        ],
        records,
        since=BASE.date(),
        until=BASE.date(),
    )

    result = settle_shadow_records(
        records,
        tick_reader=CompleteReader(),
        since=BASE.date(),
        until=BASE.date(),
        actual_rows=actual,
    )

    assert len(actual) == 2
    assert "duplicate_actual_result" in result["report"]["blockers"]


def test_missing_actual_pnl_stays_unknown_and_the_report_remains_serializable():
    records = _dubai_registrations()
    ledger = [{
        "sig_id": "canal1_3000",
        "channel": "canal1",
        "signal_dt_utc": BASE.isoformat(),
        "n_positions": 1,
        "pnl_real_mt5": None,
        "status": "closed",
        "positions": [],
        "reconciled_ok": False,
        "pnl_mt5_complete": False,
    }]
    actual = actual_rows_from_ledger(
        ledger,
        records,
        since=BASE.date(),
        until=BASE.date(),
    )

    result = settle_shadow_records(
        records,
        tick_reader=CompleteReader(),
        since=BASE.date(),
        until=BASE.date(),
        actual_rows=actual,
    )

    assert actual[0]["net_eur"] is None
    assert result["report"]["signals"][0]["actual"]["net_eur"] is None
    assert "invalid_actual_result" in result["report"]["blockers"]
    assert isinstance(result["settlement_hash"], str)


def test_registration_record_hash_must_match_its_frozen_state():
    registrations = _dubai_registrations()
    registrations[0] = {
        **registrations[0],
        "state_hash": "0" * 64,
    }

    result = settle_shadow_records(
        registrations,
        tick_reader=CompleteReader(),
        since=BASE.date(),
        until=BASE.date(),
    )

    corrupted = next(
        row for row in result["candidate_rows"]
        if row["candidate_id"] == registrations[0]["candidate_id"]
    )
    assert corrupted["status"] == "incomplete"
    assert corrupted["evidence_blockers"] == [
        "candidate_registration_hash_mismatch"
    ]


def test_settlement_is_deterministic_for_identical_evidence():
    kwargs = {
        "tick_reader": CompleteReader(),
        "since": BASE.date(),
        "until": BASE.date(),
    }

    first = settle_shadow_records(_dubai_registrations(), **kwargs)
    second = settle_shadow_records(_dubai_registrations(), **kwargs)

    assert first["settlement_hash"] == second["settlement_hash"]
    assert first["candidate_rows"] == second["candidate_rows"]
    assert first["report"] == second["report"]


def test_tick_evidence_ignores_non_calculation_capture_metadata():
    import pandas as pd

    def read_with(captured_at: str, validation_value: float) -> ShadowTickRead:
        reader = ParquetShadowTickReader.__new__(ParquetShadowTickReader)
        reader.ticks_cache_dir = None
        reader.money_ticks_cache_dir = None
        reader.money_contract = {
            "schema_version": 1,
            "captured_at_utc": captured_at,
            "account": {"currency": "EUR", "currency_digits": 2},
            "instrument": {
                "symbol": "XAUUSD",
                "currency_profit": "EUR",
                "contract_size": 100.0,
                "tick_size": 0.01,
                "trade_calc_mode": 4,
            },
            "conversion": {"orientation": "identity", "symbol": None},
            "costs": {
                "commission_model": "observed_zero_intraday",
                "fee_model": "observed_zero_intraday",
                "swap_model": "intraday_only_zero",
            },
            "live_validation": {"actual_tick_value_profit": validation_value},
        }
        reader.symbol = "XAUUSD"
        reader._xau_days = {}
        reader._money_days = {}
        reader._contracts = {}
        frame = pd.DataFrame([{
            "time_utc": pd.Timestamp(BASE + timedelta(seconds=1)),
            "bid": 100.0,
            "ask": 100.2,
            "last": 0.0,
            "flags": 0,
            "volume_real": 0.0,
        }])
        contract = {
            "contract_sha256": "a" * 64,
            "utc_offset_seconds": 0,
            "coverage": {
                "complete_from_utc": BASE.isoformat(),
                "complete_through_utc": (
                    BASE + timedelta(minutes=1)
                ).isoformat(),
                "captured_at_utc": (
                    BASE + timedelta(minutes=1)
                ).isoformat(),
                "last_tick_utc": (
                    BASE + timedelta(seconds=1)
                ).isoformat(),
            },
        }
        reader._load_day = lambda *_args, **_kwargs: (frame, contract, None)
        return reader.read(BASE, BASE + timedelta(seconds=2))

    first = read_with("2026-08-30T10:00:00+00:00", 0.87)
    second = read_with("2026-08-30T11:00:00+00:00", 0.88)

    assert first.evidence_id == second.evidence_id
    assert first.ticks[0].money_evidence_id == second.ticks[0].money_evidence_id


def test_settlement_hash_covers_actual_calibration_and_is_order_independent():
    actual_a = {
        "channel": "canal1",
        "signal_id": "canal1_3000",
        "day": BASE.date().isoformat(),
        "entry_count": 3,
        "exit_reason": "provider_close",
        "net_eur": 1.0,
        "control_mirror_match": False,
        "telegram_lineage_complete": True,
    }
    actual_b = {
        "channel": "canal2",
        "signal_id": "canal2_unused",
        "day": BASE.date().isoformat(),
        "entry_count": 1,
        "exit_reason": "sl",
        "net_eur": -2.0,
        "control_mirror_match": False,
        "telegram_lineage_complete": True,
    }
    kwargs = {
        "tick_reader": CompleteReader(),
        "since": BASE.date(),
        "until": BASE.date(),
    }

    first = settle_shadow_records(
        _dubai_registrations(), actual_rows=[actual_a, actual_b], **kwargs,
    )
    reordered = settle_shadow_records(
        _dubai_registrations(), actual_rows=[actual_b, actual_a], **kwargs,
    )
    changed = settle_shadow_records(
        _dubai_registrations(),
        actual_rows=[{**actual_a, "net_eur": 2.0}, actual_b],
        **kwargs,
    )

    assert first["settlement_hash"] == reordered["settlement_hash"]
    assert first["actual_rows"] == reordered["actual_rows"]
    assert first["settlement_hash"] != changed["settlement_hash"]


def test_incomplete_tick_history_blocks_all_three_rows_explicitly():
    class MissingReader:
        def read(self, _start: datetime, _end: datetime) -> ShadowTickRead:
            return ShadowTickRead(
                ticks=(),
                complete=False,
                evidence_id="ticks-missing",
                blockers=("tick_cache_incomplete:2026-08-27",),
            )

    result = settle_shadow_records(
        _dubai_registrations(),
        tick_reader=MissingReader(),
        since=BASE.date(),
        until=BASE.date(),
    )

    assert len(result["candidate_rows"]) == 3
    assert all(row["status"] == "incomplete" for row in result["candidate_rows"])
    assert all(
        "tick_cache_incomplete:2026-08-27" in row["evidence_blockers"]
        for row in result["candidate_rows"]
    )
    assert result["report"]["matrix"]["canal1"]["blocked_rows"] == 3


def test_terminal_result_is_blocked_when_broker_rollover_cost_path_is_unknown():
    class CostBlockedReader(CompleteReader):
        def cost_blockers(self, _state: ShadowSignalState) -> tuple[str, ...]:
            return ("broker_rollover_cost_path_unmodeled",)

    result = settle_shadow_records(
        _dubai_registrations(),
        tick_reader=CostBlockedReader(),
        since=BASE.date(),
        until=BASE.date(),
    )

    assert len(result["candidate_rows"]) == 3
    assert all(row["status"] == "incomplete" for row in result["candidate_rows"])
    assert all(row["entry_count"] > 0 for row in result["candidate_rows"])
    assert all(row["net_eur"] is None for row in result["candidate_rows"])
    assert all(
        row["evidence_blockers"] == [
            "broker_rollover_cost_path_unmodeled"
        ]
        for row in result["candidate_rows"]
    )


def test_parquet_reader_detects_a_position_crossing_broker_midnight():
    reader = ParquetShadowTickReader.__new__(ParquetShadowTickReader)
    reader.symbol = "XAUUSD"
    reader._contracts = {
        ("XAUUSD", BASE.date()): {"utc_offset_seconds": 3 * 3600},
    }
    opened = datetime(2026, 8, 27, 20, 59, tzinfo=timezone.utc)
    closed = datetime(2026, 8, 27, 21, 1, tzinfo=timezone.utc)
    state = ShadowSignalState.new(
        signal_id="canal1_rollover",
        source_message_id=1,
        candidate_id="dubai_balanced_v1",
        channel="canal1",
        direction="BUY",
        registered_at_utc=opened.isoformat(),
        registered_tick_msc=int(opened.timestamp() * 1000),
    )
    state = ShadowSignalState.from_dict({
        **state.to_dict(),
        "status": "closed",
        "positions": [ShadowPosition(
            leg_index=0,
            volume=0.01,
            entry_price=4300.0,
            opened_tick_msc=int(opened.timestamp() * 1000),
            opened_at_utc=opened.isoformat(),
            status="closed",
            close_price=4301.0,
            closed_tick_msc=int(closed.timestamp() * 1000),
            close_reason="test",
        ).to_dict()],
    })

    assert reader.cost_blockers(state) == (
        "broker_rollover_cost_path_unmodeled",
    )


def test_parquet_reader_proves_the_exact_registration_tick(monkeypatch):
    import pandas as pd

    reader = ParquetShadowTickReader.__new__(ParquetShadowTickReader)
    reader.ticks_cache_dir = None
    reader.symbol = "XAUUSD"
    reader._xau_days = {}
    reader._contracts = {}
    frame = pd.DataFrame([{
        "time_utc": pd.Timestamp(BASE),
        "bid": 100.0,
        "ask": 100.2,
    }])
    contract = {
        "contract_sha256": "a" * 64,
        "utc_offset_seconds": 3 * 3600,
        "coverage": {
            "complete_from_utc": (BASE - timedelta(minutes=1)).isoformat(),
            "complete_through_utc": (BASE + timedelta(minutes=1)).isoformat(),
        },
    }
    monkeypatch.setattr(
        reader,
        "_load_day",
        lambda *args, **kwargs: (frame, contract, None),
    )

    exact = reader.registration_tick_evidence(
        raw_server_msc=BASE_MSC + 10_800_000,
        observed_at_utc=BASE,
        reference_bid=100.0,
        reference_ask=100.2,
    )
    mismatch = reader.registration_tick_evidence(
        raw_server_msc=BASE_MSC + 10_800_000,
        observed_at_utc=BASE,
        reference_bid=99.9,
        reference_ask=100.2,
    )

    assert exact.complete is True
    assert exact.normalized_time_msc == BASE_MSC
    assert mismatch.complete is False
    assert mismatch.blockers == ("registration_tick_quote_mismatch",)


def test_parquet_reader_validates_only_the_requested_intraday_window(
        monkeypatch):
    import pandas as pd

    reader = ParquetShadowTickReader.__new__(ParquetShadowTickReader)
    reader.ticks_cache_dir = None
    reader.money_ticks_cache_dir = None
    reader.money_contract = {
        "instrument": {"contract_size": 100.0},
        "conversion": {"orientation": "identity"},
    }
    reader.symbol = "XAUUSD"
    reader._xau_days = {}
    reader._money_days = {}
    reader._contracts = {}
    frame = pd.DataFrame([{
        "time_utc": pd.Timestamp("2026-08-27T08:30:00+00:00"),
        "bid": 100.0,
        "ask": 100.2,
        "last": 0.0,
        "flags": 0,
        "volume_real": 0.0,
    }])
    contract = {
        "contract_sha256": "a" * 64,
        "utc_offset_seconds": 0,
        "coverage": {
            "complete_from_utc": "2026-08-27T00:00:00+00:00",
            "complete_through_utc": "2026-08-27T12:00:00+00:00",
            "captured_at_utc": "2026-08-27T12:00:00+00:00",
            "last_tick_utc": "2026-08-27T11:59:59+00:00",
        },
    }
    monkeypatch.setattr(
        reader,
        "_load_day",
        lambda *args, **kwargs: (frame, contract, None),
    )

    available = reader.read(
        datetime(2026, 8, 27, 8, tzinfo=timezone.utc),
        datetime(2026, 8, 27, 9, tzinfo=timezone.utc),
    )
    missing = reader.read(
        datetime(2026, 8, 27, 8, tzinfo=timezone.utc),
        datetime(2026, 8, 27, 13, tzinfo=timezone.utc),
    )

    assert available.complete is True
    assert len(available.ticks) == 1
    assert missing.complete is False
    assert missing.blockers == ("tick_cache_incomplete:XAUUSD:2026-08-27",)


def test_parquet_reader_preserves_broker_order_within_one_millisecond(
        monkeypatch):
    import pandas as pd

    reader = ParquetShadowTickReader.__new__(ParquetShadowTickReader)
    reader.ticks_cache_dir = None
    reader.money_ticks_cache_dir = None
    reader.money_contract = {
        "instrument": {"contract_size": 100.0},
        "conversion": {"orientation": "identity"},
    }
    reader.symbol = "XAUUSD"
    reader._xau_days = {}
    reader._money_days = {}
    reader._contracts = {}
    timestamp = pd.Timestamp("2026-08-27T08:30:00+00:00")
    frame = pd.DataFrame([
        {
            "time_utc": timestamp,
            "bid": 101.0,
            "ask": 101.2,
            "last": 0.0,
            "flags": 0,
            "volume_real": 0.0,
        },
        {
            "time_utc": timestamp,
            "bid": 100.0,
            "ask": 100.2,
            "last": 0.0,
            "flags": 0,
            "volume_real": 0.0,
        },
    ])
    contract = {
        "contract_sha256": "a" * 64,
        "utc_offset_seconds": 0,
        "coverage": {
            "complete_from_utc": "2026-08-27T00:00:00+00:00",
            "complete_through_utc": "2026-08-27T12:00:00+00:00",
            "captured_at_utc": "2026-08-27T12:00:00+00:00",
            "last_tick_utc": "2026-08-27T11:59:59+00:00",
        },
    }
    monkeypatch.setattr(
        reader,
        "_load_day",
        lambda *args, **kwargs: (frame, contract, None),
    )

    result = reader.read(
        datetime(2026, 8, 27, 8, tzinfo=timezone.utc),
        datetime(2026, 8, 27, 9, tzinfo=timezone.utc),
    )

    assert [tick.bid for tick in result.ticks] == [101.0, 100.0]
