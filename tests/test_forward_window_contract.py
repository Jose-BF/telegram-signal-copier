from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from tests.test_prepare_simulator_forward import (
    setup, freeze, dataset, engine_results, comparison_report,
)
from tests.test_run_simulator_forward import _inputs, _frozen_protocol, _native_capture
from tools import prepare_simulator_forward as forward
from tools import run_protection_controls as controls
from tools import run_simulator_forward as runner


THURSDAY = forward.utc("2026-09-10T13:00:00Z")
FRIDAY = forward.utc("2026-09-11T13:00:00Z")


def custom_freeze(setup, monkeypatch, start=THURSDAY, *, out=None):
    profile, default_out, _ = setup
    monkeypatch.setattr(forward, "_now", lambda: start - timedelta(minutes=15))
    report = forward.prepare(
        profile, out=out or default_out, start_utc=start,
        end_utc=start + timedelta(hours=2),
    )
    assert report["status"] == "frozen_waiting_for_cohort"
    return report


def rehash(path, value):
    payload = {key: item for key, item in value.items() if key != "fingerprint"}
    value["fingerprint"] = hashlib.sha256(controls.encode(payload)).hexdigest()
    Path(path).write_bytes(controls.encode(value))


def test_two_custom_windows_coexist_without_changing_legacy(setup, monkeypatch, tmp_path):
    legacy, _ = freeze(setup)
    before = legacy.read_bytes()
    reports = [custom_freeze(setup, monkeypatch, start, out=tmp_path / name)
               for start, name in ((THURSDAY, "thu"), (FRIDAY, "fri"))]
    assert reports[0]["fingerprint"] != reports[1]["fingerprint"]
    for report, start in zip(reports, (THURSDAY, FRIDAY), strict=True):
        protocol, _ = forward._frozen_protocol(report["protocol"], {}, setup[2])
        assert protocol["contract"] == "simulator_forward_protocol_v2"
        assert forward.utc(protocol["start_utc"]) == start
        assert forward.utc(protocol["end_utc"]) == start + timedelta(hours=2)
        assert protocol["budget"] == forward.BUDGET
        assert protocol["dataset_rules"] == forward.DATA_RULES
        assert protocol["required_lifecycle_checks"] == list(forward.LIFECYCLE_CHECKS)
        assert protocol["full_live_parity_verified"] is False
    assert forward.START == forward.utc("2026-09-09T06:30:00Z")
    assert forward.END == forward.utc("2026-09-09T08:30:00Z")
    assert legacy.read_bytes() == before
    forward._frozen_protocol(legacy, {}, setup[2])
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    for report, start in zip(reversed(reports), (FRIDAY, THURSDAY), strict=True):
        directory = tmp_path / start.strftime("inputs-%Y%m%d")
        directory.mkdir()
        end = start + timedelta(hours=2)
        data = produce_explicit(report["protocol"], directory, start, end)
        executed = runner.execute(report["protocol"], data, out=directory / "results", now=end)
        assert executed["denominator"] == 2
        assert executed["engine_evaluations"] == 6
        assert executed["counts"] == {"simulated_entries": 2, "simulated_exits": 0}
        assert executed["portfolio"]["verified"] is False
        assert executed["portfolio"]["net_eur"] is None
        replay = forward.check(report["protocol"], data, stage="independent",
                               results=directory / "results" / "independent_results.json", now=end)
        assert replay["counts"] == executed["counts"]
        assert set(replay["blockers"]) == set(executed["blockers"])
        assert len(replay["blockers"]) == 6
        assert all(reason.endswith(":path_ended_before_strategy_exit") for reason in replay["blockers"])


@pytest.mark.parametrize("start,end", [
    (THURSDAY, None), (None, FRIDAY),
    (THURSDAY, THURSDAY), (FRIDAY, THURSDAY),
    (THURSDAY, THURSDAY + timedelta(hours=2, microseconds=1)),
    ("2026-09-10T13:00:00", "2026-09-10T15:00:00Z"),
    ("2026-09-10T15:00:00+02:00", "2026-09-10T17:00:00+02:00"),
    (THURSDAY, "not-a-timestamp"), ("", FRIDAY),
])
def test_invalid_custom_window_cannot_publish(setup, start, end):
    profile, out, _ = setup
    with pytest.raises(ValueError):
        forward.prepare(profile, out=out, start_utc=start, end_utc=end)
    assert not out.exists()


@pytest.mark.parametrize("late", [THURSDAY, THURSDAY + timedelta(hours=3)])
def test_past_or_started_window_stays_blocked(setup, monkeypatch, late):
    monkeypatch.setattr(forward, "_now", lambda: late)
    profile, out, _ = setup
    report = forward.prepare(profile, out=out, start_utc=THURSDAY,
                             end_utc=THURSDAY + timedelta(hours=2))
    assert "forward_freeze_deadline_missed" in report["blockers"]
    assert not out.exists()


def test_supplied_now_cannot_backdate_real_freeze(setup, monkeypatch):
    monkeypatch.setattr(forward, "_now", lambda: THURSDAY)
    profile, out, _ = setup
    with pytest.raises(ValueError, match="deadline"):
        forward.prepare(profile, out=out, now=THURSDAY - timedelta(hours=1),
                        start_utc=THURSDAY, end_utc=THURSDAY + timedelta(hours=2))
    assert not out.exists()


@pytest.mark.parametrize("completed", [THURSDAY, THURSDAY + timedelta(seconds=1)])
def test_verification_at_or_after_start_is_not_admitted(setup, monkeypatch, completed):
    profile, out, _ = setup
    value = controls.read(profile)
    proof = value["verification"]
    verification = controls.read(proof["path"])
    verification["completed_at_utc"] = completed
    Path(proof["path"]).write_bytes(controls.encode(verification))
    proof["sha256"] = controls.digest(proof["path"])
    profile.write_bytes(controls.encode(value))
    monkeypatch.setattr(forward, "_now", lambda: THURSDAY - timedelta(minutes=15))
    report = forward.prepare(profile, out=out, start_utc=THURSDAY,
                             end_utc=THURSDAY + timedelta(hours=2))
    assert "verification_not_completed_before_forward_window" in report["blockers"]
    assert not out.exists()


@pytest.mark.parametrize("change", ["date", "v1_relabel", "long_window", "late_freeze"])
def test_protocol_tampering_is_rejected(setup, monkeypatch, change):
    report = custom_freeze(setup, monkeypatch)
    path = setup[1] / "protocol.json"
    value = controls.read(path)
    if change == "date":
        value["end_utc"] = (THURSDAY + timedelta(hours=1)).isoformat()
        path.write_bytes(controls.encode(value))
    else:
        if change == "v1_relabel":
            value["contract"] = "simulator_forward_protocol_v1"
        elif change == "long_window":
            value["end_utc"] = (THURSDAY + timedelta(hours=3)).isoformat()
        else:
            value["frozen_at_utc"] = THURSDAY.isoformat()
        rehash(path, value)
    with pytest.raises(ValueError):
        forward._frozen_protocol(report["protocol"], {}, setup[2])


@pytest.mark.parametrize("start", [THURSDAY, FRIDAY])
def test_custom_produce_and_three_engine_run_keep_blocked_denominator(
    setup, monkeypatch, tmp_path, start
):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    report = custom_freeze(setup, monkeypatch, start)
    end = start + timedelta(hours=2)
    events, tapes, clock, metadata = _inputs(tmp_path, start=start, end=end)
    with events.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "ev": "telegram_raw", "channel": "canal2", "message_id": 3,
            "message_revision_id": "revision-3", "text": "BUY GOLD NOW",
            "date_utc": (end - timedelta(seconds=2)).isoformat(),
            "ts": (end - timedelta(seconds=2)).isoformat(),
        }) + "\n")
    for path in tapes.values():
        frame = pd.read_parquet(path)
        final = end - timedelta(seconds=4)
        frame.loc[frame.index[-1], "time_utc"] = final
        frame.loc[frame.index[-1], "source_time_msc"] = int(final.timestamp() * 1000) + 10_800_000
        frame.to_parquet(path)
    data = tmp_path / "dataset"
    produced = runner.produce(
        report["protocol"], events=events, market_tape=tapes["XAUUSD"],
        conversion_tape=tapes["EURUSD"], clock_evidence=clock, metadata_evidence=metadata,
        cutoff=end, out=data, now=end,
    )
    assert produced["denominator"] == 3
    assert forward.utc(controls.read(data / "protocol.json")["start_utc"]) == start
    out = tmp_path / "results"
    executed = runner.execute(report["protocol"], data, out=out, now=end)
    payload = controls.read(out / "independent_results.json")
    assert executed["denominator"] == 3
    assert executed["status"] == "blocked"
    assert payload["engine_evaluations"] == 6
    assert [row["signal_id"] for row in payload["results"]] == ["canal2_1", "canal2_2", "canal2_3"]
    assert payload["results"][2]["preparation_blockers"]
    assert executed["portfolio"]["verified"] is False
    assert executed["portfolio"]["net_eur"] is None
    assert executed["portfolio"]["max_drawdown_eur"] is None
    assert "signal_without_market_quote:canal2_3" in executed["incomplete"]
    assert executed["quantitative_agreement_admitted"] is False
    assert executed["full_live_parity_verified"] is False
    assert executed["mass_search_authorized"] is False
    assert executed["search_candidates"] == 0


def produce_explicit(protocol, directory, start, end):
    events, tapes, clock, metadata = _inputs(directory, start=start, end=end)
    data = directory / "dataset"
    runner.produce(protocol, events=events, market_tape=tapes["XAUUSD"],
                   conversion_tape=tapes["EURUSD"], clock_evidence=clock,
                   metadata_evidence=metadata, cutoff=end, out=data, now=end)
    return data


@pytest.mark.parametrize("repair_hash", [False, True])
def test_other_window_dataset_cannot_be_reused(setup, monkeypatch, tmp_path, repair_hash):
    reports = [custom_freeze(setup, monkeypatch, start, out=tmp_path / name)
               for start, name in ((THURSDAY, "thu"), (FRIDAY, "fri"))]
    data = dataset(tmp_path, reports[0]["protocol"], reports[0]["protocol_sha256"])
    if repair_hash:
        path = data / "protocol.json"
        value = controls.read(path)
        value["forward_protocol_sha256"] = reports[1]["protocol_sha256"]
        path.write_bytes(controls.encode(value))
    with pytest.raises(ValueError, match="frozen independent experiment|admissible future window"):
        forward.check(reports[1]["protocol"], data, now=FRIDAY + timedelta(hours=2))


@pytest.mark.parametrize("change", ["raw", "tape", "cutoff", "naive_cutoff", "offset_start"])
def test_custom_dataset_rechecks_window_not_just_protocol_hash(setup, monkeypatch, tmp_path, change):
    report = custom_freeze(setup, monkeypatch)
    data = dataset(tmp_path, report["protocol"], report["protocol_sha256"])
    path = data / "protocol.json"
    value = controls.read(path)
    if change == "raw":
        raw_path = data / "raw_messages.json"
        rows = controls.read(raw_path)
        rows[0]["ts"] = forward.START.isoformat()
        raw_path.write_bytes(controls.encode(rows))
        value["raw_messages_sha256"] = controls.digest(raw_path)
    elif change == "tape":
        tape_path = data / "XAUUSD.parquet"
        frame = pd.read_parquet(tape_path)
        frame.loc[frame.index[0], "time_utc"] = forward.START
        frame.loc[frame.index[0], "source_time_msc"] = int(forward.START.timestamp() * 1000) + 10_800_000
        frame.to_parquet(tape_path)
        value["tapes"]["XAUUSD"]["sha256"] = controls.digest(tape_path)
    elif change == "cutoff":
        value["cutoff_utc"] = FRIDAY.isoformat()
    elif change == "naive_cutoff":
        value["cutoff_utc"] = "2026-09-10T15:00:00"
    else:
        value["start_utc"] = "2026-09-10T15:00:00+02:00"
    path.write_bytes(controls.encode(value))
    with pytest.raises(ValueError):
        forward.check(report["protocol"], data, now=FRIDAY)


@pytest.mark.parametrize("change", ["clock", "metadata", "tape", "raw_naive", "raw_offset", "cutoff"])
def test_custom_producer_rejects_wrong_window_sources(setup, monkeypatch, tmp_path, change):
    report = custom_freeze(setup, monkeypatch)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    end = THURSDAY + timedelta(hours=2)
    events, tapes, clock, metadata = _inputs(tmp_path, start=THURSDAY, end=end)
    cutoff = end
    if change in ("clock", "metadata"):
        path = clock if change == "clock" else metadata
        value = controls.read(path)
        value["captured_at_utc"] = forward.END.isoformat()
        path.write_bytes(controls.encode(value))
    elif change == "tape":
        source = tmp_path / "old_source"
        source.mkdir()
        _, tapes, _, _ = _inputs(source)
    elif change.startswith("raw_"):
        rows = [json.loads(line) for line in events.read_text().splitlines()]
        rows[0]["ts"] = ("2026-09-10T13:01:00" if change == "raw_naive"
                         else "2026-09-10T15:01:00+02:00")
        events.write_text("".join(json.dumps(row) + "\n" for row in rows))
    else:
        cutoff = end + timedelta(seconds=1)
    out = tmp_path / "dataset"
    with pytest.raises(ValueError):
        runner.produce(report["protocol"], events=events, market_tape=tapes["XAUUSD"],
                       conversion_tape=tapes["EURUSD"], clock_evidence=clock,
                       metadata_evidence=metadata, cutoff=cutoff, out=out, now=FRIDAY)
    assert not out.exists()


@pytest.mark.parametrize("start", [THURSDAY, FRIDAY])
def test_native_capture_uses_custom_window_and_keeps_capture_blockers(tmp_path, monkeypatch, start):
    end = start + timedelta(hours=2)
    binding = {"collector_sha256": None, "wrapper_sha256": "c" * 64,
               "expected_live_commit": "d" * 40, "expected_account_identity_sha256": "e" * 64}
    protocol = _frozen_protocol(tmp_path, monkeypatch, capture_contract=binding,
                                start_utc=start, end_utc=end)
    capture, _ = _native_capture(tmp_path, binding, start=start, end=end)
    data = tmp_path / "dataset"
    now = end + timedelta(seconds=10)
    produced = runner.produce(protocol, capture=capture, out=data, now=now)
    assert produced["denominator"] == 2
    assert produced["cutoff_utc"] == end
    assert produced["capture_blockers"] == ["runtime_heartbeat_pid_missing", "runtime_heartbeat_stale"]
    executed = runner.execute(protocol, data, out=tmp_path / "results", now=now)
    assert executed["denominator"] == 2
    assert executed["engine_evaluations"] == 6
    assert "capture:runtime_heartbeat_stale" in executed["blockers"]
    assert executed["portfolio"]["net_eur"] is None
    assert executed["portfolio"]["verified"] is False
    original = (capture / "manifest.json").read_bytes()
    manifest = controls.read(capture / "manifest.json")
    manifest["window_start_utc"] = forward.START
    manifest["window_end_exclusive_utc"] = forward.END
    (capture / "manifest.json").write_bytes(controls.encode(manifest))
    with pytest.raises(ValueError, match="admissible source-only window"):
        runner.produce(protocol, capture=capture, out=tmp_path / "wrong_dataset", now=now)
    assert original != (capture / "manifest.json").read_bytes()
    assert not (tmp_path / "wrong_dataset").exists()


@pytest.mark.parametrize("name", forward.EXACT_NATIVE_CHECKS)
def test_custom_window_does_not_relax_exact_native_checks(setup, monkeypatch, tmp_path, name):
    report = custom_freeze(setup, monkeypatch)
    data = dataset(tmp_path, report["protocol"], report["protocol_sha256"])
    results = engine_results(tmp_path, report["protocol"], data, monkeypatch)
    comparison = comparison_report(tmp_path, report["protocol"], results)
    value = controls.read(comparison)
    value["rows"][0]["lifecycle_checks"][name]["status"] = "differences_for_review"
    comparison.write_bytes(controls.encode(value))
    checked = forward.check(report["protocol"], data, stage="observed", results=results,
                            comparison=comparison, now=THURSDAY + timedelta(hours=2))
    assert f"canal2_1:lifecycle_unverified:{name}" in checked["blockers"]
    assert checked["status"] == "blocked"
    assert checked["denominator"] == 2


def test_partial_empty_custom_cohort_remains_incomplete(setup, monkeypatch, tmp_path):
    report = custom_freeze(setup, monkeypatch)
    cutoff = THURSDAY + timedelta(minutes=10)
    data = dataset(tmp_path, report["protocol"], report["protocol_sha256"], cutoff=cutoff, count=0)
    checked = forward.check(report["protocol"], data, now=cutoff)
    assert checked["status"] == "incomplete"
    assert checked["denominator"] == 0
    assert set(checked["incomplete"]) == {"insufficient_natural_signals", "observation_window_incomplete"}


def test_actual_freeze_timestamp_and_final_deadline(setup, monkeypatch, tmp_path):
    actual = THURSDAY - timedelta(minutes=15)
    monkeypatch.setattr(forward, "_now", lambda: actual)
    profile, out, _ = setup
    forward.prepare(profile, out=out, now=actual - timedelta(minutes=1),
                    start_utc=THURSDAY, end_utc=THURSDAY + timedelta(hours=2))
    assert forward.utc(controls.read(out / "protocol.json")["frozen_at_utc"]) == actual
    original = forward._finish
    checks = 0

    def finish(*args):
        nonlocal checks
        original(*args)
        checks += 1
        if checks == 2:
            monkeypatch.setattr(forward, "_now", lambda: THURSDAY)

    monkeypatch.setattr(forward, "_finish", finish)
    late_out = tmp_path / "late"
    with pytest.raises(ValueError, match="deadline"):
        forward.prepare(profile, out=late_out, start_utc=THURSDAY,
                        end_utc=THURSDAY + timedelta(hours=2))
    assert not late_out.exists()


def test_complete_custom_control_has_only_existing_bounded_portfolio_admission(tmp_path, monkeypatch):
    end = THURSDAY + timedelta(hours=1)
    protocol = _frozen_protocol(tmp_path, monkeypatch, start_utc=THURSDAY, end_utc=end)
    data = produce_explicit(protocol, tmp_path, THURSDAY, end)
    executed = runner.execute(protocol, data, out=tmp_path / "results", now=end)
    assert executed["status"] == "evidence_ready_for_review"
    assert executed["denominator"] == 2
    assert executed["engine_evaluations"] == 6
    assert executed["portfolio"]["verified"] is True
    assert executed["quantitative_agreement_admitted"] is False
    assert executed["full_live_parity_verified"] is False
    assert executed["mass_search_authorized"] is False


@pytest.mark.parametrize("change", ["implementation", "runner", "profile", "verification"])
def test_new_window_keeps_all_frozen_source_and_profile_proofs(setup, monkeypatch, change):
    report = custom_freeze(setup, monkeypatch)
    profile, _, implementation = setup
    if change == "implementation":
        implementation["controls"]["runtime"] = "changed-runtime"
    else:
        value = controls.read(profile)
        if change == "runner":
            path = Path(value["additional_sources"][1]["path"])
        elif change == "verification":
            path = Path(value["verification"]["path"])
        else:
            path = profile
        path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="frozen implementation|evidence hash mismatch"):
        forward._frozen_protocol(report["protocol"], {}, implementation)


@pytest.mark.parametrize("stamp", ["2026-09-10T12:00:00", "2026-09-10T14:00:00+02:00"])
def test_readiness_verification_must_have_explicit_zero_utc_offset(setup, monkeypatch, stamp):
    profile, out, _ = setup
    value = controls.read(profile)
    proof = value["verification"]
    verification = controls.read(proof["path"])
    verification["completed_at_utc"] = stamp
    Path(proof["path"]).write_bytes(controls.encode(verification))
    proof["sha256"] = controls.digest(proof["path"])
    profile.write_bytes(controls.encode(value))
    with pytest.raises(ValueError, match="normalized UTC"):
        custom_freeze(setup, monkeypatch)
    assert not out.exists()
