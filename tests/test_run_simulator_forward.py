from datetime import timedelta
import json

import pandas as pd
import pytest

from tools import prepare_simulator_forward as forward
from tools import run_protection_controls as controls
from tools import run_simulator_forward as runner


def _frozen_protocol(tmp_path, monkeypatch, *, capture_contract=None, start_utc=None, end_utc=None):
    start = forward.START if start_utc is None else forward.utc(start_utc)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(forward, "ROOT", tmp_path)
    monkeypatch.setattr(controls, "ROOT", tmp_path)
    monkeypatch.setattr(controls, "FORBIDDEN_IMPORTS", {"forbidden_forward_fixture"})
    monkeypatch.setattr(forward, "_now", lambda: start - timedelta(minutes=15))

    source = tmp_path / "tools" / "run_simulator_forward.py"
    source.parent.mkdir()
    source.write_text("# Frozen forward runner fixture.\n")
    additional_sources = [
        {"path": str(source), "sha256": controls.digest(source)},
    ]
    if capture_contract is not None:
        collector = tmp_path / runner.COLLECTOR_SOURCE
        collector.parent.mkdir(parents=True)
        collector.write_text("# Frozen morning collector fixture.\n")
        capture_contract["collector_sha256"] = controls.digest(collector)
        additional_sources.append(
            {"path": str(collector), "sha256": controls.digest(collector)}
        )
    market_sources = dict.fromkeys(forward.MARKET_SOURCES, "b" * 64)
    implementation = {
        "controls": {
            "iterative": {"sha256": "a" * 64, "source_sha256": market_sources},
            "control_sources": {},
        },
        "forward_sources": {
            "tools/prepare_simulator_forward.py": "b" * 64,
            **market_sources,
        },
    }
    monkeypatch.setattr(forward, "identity", lambda: implementation)

    verification = tmp_path / "verification.json"
    verification_sha = controls.save(verification, {
        "status": "local_suite_verified",
        "implementation_sha256": "a" * 64,
        "exit_code": 0,
        "same_implementation": True,
        "changed_sources": [],
        "changed_wrappers": [],
        "suite": {"tests": 10, "failures": 0, "errors": 0, "skipped": 0},
        "completed_at_utc": (start - timedelta(hours=1)).isoformat(),
    })
    profile = tmp_path / "profile.json"
    profile_payload = {
        "contract": "simulator_forward_profile_v1",
        "execution": {
            "latency_ms": 0,
            "entry_fill_latency_ms": 0,
            "protection": dict(controls.PROFILE_SPECS[0]),
            "market": {
                "entry_acknowledgement_delay_ms": 0,
                "close_processing_delay_ms": 0,
                "close_acknowledgement_delay_ms": 0,
                "volume_min": 0.01,
                "volume_max": 100.0,
                "volume_step": 0.01,
            },
        },
        "capabilities": dict.fromkeys(forward.CAPABILITIES, "verified"),
        "verification": {"path": str(verification), "sha256": verification_sha},
        "additional_sources": additional_sources,
    }
    if capture_contract is not None:
        profile_payload["capture_contract"] = capture_contract
    controls.save(profile, profile_payload)
    frozen = tmp_path / "frozen"
    prepared = forward.prepare(profile, out=frozen, start_utc=start_utc, end_utc=end_utc)
    assert prepared["status"] == "frozen_waiting_for_cohort"
    return frozen / "protocol.json"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    return _frozen_protocol(tmp_path, monkeypatch)


@pytest.fixture
def capture_setup(tmp_path, monkeypatch):
    capture_contract = {
        "collector_sha256": None,
        "wrapper_sha256": "c" * 64,
        "expected_live_commit": "d" * 40,
        "expected_account_identity_sha256": "e" * 64,
    }
    protocol = _frozen_protocol(
        tmp_path, monkeypatch, capture_contract=capture_contract
    )
    return protocol, capture_contract


def _inputs(tmp_path, *, stale_metadata=False, start=None, end=None):
    start = forward.START if start is None else start
    end = forward.END if end is None else end
    events = tmp_path / "events.jsonl"
    rows = [
        {
            "ev": "telegram_raw",
            "channel": "canal2",
            "message_id": 1,
            "message_revision_id": "revision-1",
            "date_utc": (start + timedelta(minutes=1)).isoformat(),
            "ts": (start + timedelta(minutes=1)).isoformat(),
            "text": "BUY GOLD NOW",
            "transport_secret": "must-not-cross-boundary",
        },
        {"ev": "mt5_deal", "deal": "observed-fill-must-not-cross-boundary"},
        {
            "ev": "telegram_raw",
            "channel": "canal2",
            "message_id": 2,
            "message_revision_id": "revision-2",
            "date_utc": (start + timedelta(minutes=5)).isoformat(),
            "ts": (start + timedelta(minutes=5)).isoformat(),
            "text": "SELL GOLD NOW",
        },
    ]
    events.write_text("".join(json.dumps(row) + "\n" for row in rows))

    offsets = (0, 60, 61, 62, 63, 64, 300, 301, 302, 303, 304, 7200)
    times = [start + timedelta(seconds=value) for value in offsets[:-1]] + [end]
    bids = (100.0, 100.0, 98.8, 100.3, 100.5, 102.0,
            103.0, 104.2, 102.7, 102.7, 101.0, 101.0)
    asks = tuple(value + 0.2 for value in bids)
    tapes = {}
    for symbol in ("XAUUSD", "EURUSD"):
        path = tmp_path / f"{symbol}.parquet"
        stamps = pd.to_datetime(times, utc=True)
        source_ms = stamps.astype("datetime64[ms, UTC]").astype("int64") + 10_800_000
        if symbol == "XAUUSD":
            bid, ask = bids, asks
        else:
            bid, ask = (1.10,) * len(times), (1.11,) * len(times)
        pd.DataFrame({
            "time_utc": stamps,
            "source_time_msc": source_ms,
            "bid": bid,
            "ask": ask,
        }).to_parquet(path)
        tapes[symbol] = path

    clock = tmp_path / "clock.json"
    controls.save(clock, {
        "contract": "simulator_forward_clock_evidence_v1",
        "captured_at_utc": end,
        "clock_domain": "UTC",
        "broker_epoch_offset_seconds": 10_800,
        "monotonic_order_verified": True,
    })
    metadata = tmp_path / "metadata.json"
    captured = start - timedelta(seconds=1) if stale_metadata else end
    controls.save(metadata, {
        "contract": "simulator_forward_metadata_evidence_v1",
        "captured_at_utc": captured,
        "account_currency": "EUR",
        "symbols": {
            "XAUUSD": {
                "point": 0.01,
                "digits": 2,
                "contract_size": 100.0,
                "stops_level_points": 20,
                "freeze_level_points": 0,
                "volume_min": 0.01,
                "volume_max": 100.0,
                "volume_step": 0.01,
            },
            "EURUSD": {"point": 0.00001, "digits": 5},
        },
    })
    return events, tapes, clock, metadata


def _produce(protocol, tmp_path, *, stale_metadata=False):
    events, tapes, clock, metadata = _inputs(
        tmp_path, stale_metadata=stale_metadata
    )
    out = tmp_path / "dataset"
    report = runner.produce(
        protocol,
        events=events,
        market_tape=tapes["XAUUSD"],
        conversion_tape=tapes["EURUSD"],
        clock_evidence=clock,
        metadata_evidence=metadata,
        cutoff=forward.END,
        out=out,
        now=forward.END,
    )
    return out, report


def _native_capture(tmp_path, capture_contract, *, start=None, end=None):
    start = forward.START if start is None else start
    end = forward.END if end is None else end
    source = tmp_path / "native-source"
    source.mkdir()
    events, tapes, _, _ = _inputs(source, start=start, end=end)
    capture = tmp_path / "native-capture"
    capture.mkdir()

    raw_messages = []
    for line in events.read_text().splitlines():
        row = json.loads(line)
        if row.get("ev") == "telegram_raw":
            raw_messages.append({name: row.get(name) for name in runner.RAW_FIELDS})
    raw_path = capture / "raw_messages_window.json"
    controls.save(raw_path, raw_messages)

    final_tick = end - timedelta(milliseconds=1)
    for symbol, source_path in tapes.items():
        frame = pd.read_parquet(source_path)
        frame.loc[frame.index[-1], "time_utc"] = final_tick
        frame.loc[frame.index[-1], "source_time_msc"] = (
            int(final_tick.timestamp() * 1000) + 10_800_000
        )
        frame["time"] = frame["source_time_msc"] // 1000
        frame["time_msc"] = frame["source_time_msc"]
        frame["flags"] = 6
        frame.to_parquet(capture / f"{symbol}.parquet", index=False)

    symbol_contract = {
        "XAUUSD": {
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "point": 0.01,
            "digits": 2,
            "trade_stops_level": 20,
            "trade_freeze_level": 0,
            "trade_contract_size": 100.0,
            "currency_base": "XAU",
            "currency_profit": "USD",
            "currency_margin": "USD",
            "filling_mode": 2,
            "order_mode": 127,
        },
        "EURUSD": {
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "point": 0.00001,
            "digits": 5,
            "trade_stops_level": 0,
            "trade_freeze_level": 0,
            "trade_contract_size": 100_000.0,
            "currency_base": "EUR",
            "currency_profit": "USD",
            "currency_margin": "EUR",
            "filling_mode": 2,
            "order_mode": 127,
        },
    }
    metadata_path = capture / "symbol_metadata.json"
    controls.save(metadata_path, symbol_contract)
    (capture / "broker_deals.json").write_bytes(b"not-json-observed-data")

    source_cutoff = end + timedelta(seconds=2)
    samples = []
    for index in range(3):
        observed = end + timedelta(seconds=5, milliseconds=index * 200)
        for symbol, bid, ask in (
            ("XAUUSD", 100.0, 100.2),
            ("EURUSD", 1.10, 1.11),
        ):
            raw_time = int((observed.timestamp() + 10_800) * 1000)
            samples.append({
                "sample": index,
                "symbol": symbol,
                "observed_utc": observed,
                "raw_time_msc": raw_time,
                "bid": bid,
                "ask": ask,
                "offset_seconds": 10_800,
                "residual_seconds": (
                    raw_time / 1000 - observed.timestamp() - 10_800
                ),
            })

    files = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": controls.digest(path),
        }
        for path in capture.iterdir()
        if path.is_file()
    }
    tick_evidence = {}
    for symbol in ("XAUUSD", "EURUSD"):
        path = capture / f"{symbol}.parquet"
        frame = pd.read_parquet(path)
        tick_evidence[symbol] = {
            "symbol": symbol,
            "status": "captured",
            "rows": len(frame),
            "window_end_exclusive_utc": end,
            "parquet_sha256": controls.digest(path),
        }
    manifest = {
        "contract": runner.CAPTURE_CONTRACT,
        "status": "captured_with_blockers",
        "blockers": [
            "runtime_heartbeat_pid_missing",
            "runtime_heartbeat_stale",
        ],
        "completed_at_utc": end + timedelta(seconds=8),
        "window_start_utc": start,
        "window_end_exclusive_utc": end,
        "event_cutoff_utc": source_cutoff,
        "event_evidence": {
            "raw_messages_file": raw_path.name,
            "raw_messages_sha256": controls.digest(raw_path),
            "invalid_delta_rows": [],
            "raw_message_causal_availability": {
                "window_rows_cumulative": len(raw_messages),
                "missing_fields": {},
                "revision_conflicts": [],
            },
        },
        "live_code": {
            "clean": True,
            "collector_sha256": capture_contract["collector_sha256"],
            "wrapper_sha256": capture_contract["wrapper_sha256"],
            "commit": capture_contract["expected_live_commit"],
        },
        "account": {
            "account_identity_sha256": capture_contract[
                "expected_account_identity_sha256"
            ],
            "currency": "EUR",
            "terminal_connected": True,
        },
        "clock_samples": samples,
        "raw_server_epoch_minus_utc_seconds": 10_800,
        "symbol_contract": symbol_contract,
        "tick_evidence": tick_evidence,
        "files": files,
        "read_only": True,
        "normal_operations_only": True,
        "orders_sent_by_collector": 0,
    }
    controls.save(capture / "manifest.json", manifest)
    return capture, samples


def test_producer_emits_only_raw_fields_and_frozen_quote_proofs(setup, tmp_path):
    dataset, report = _produce(setup, tmp_path)
    messages = controls.read(dataset / "raw_messages.json")
    protocol = controls.read(dataset / "protocol.json")

    assert report["status"] == "dataset_ready"
    assert report["eligible_signal_ids"] == ["canal2_1", "canal2_2"]
    assert all(set(row) == set(runner.RAW_FIELDS) for row in messages)
    assert "must-not-cross-boundary" not in json.dumps(messages)
    assert "observed-fill" not in json.dumps(messages)
    assert protocol["producer"]["sha256"] == controls.digest(
        tmp_path / "tools" / "run_simulator_forward.py"
    )
    for proof in protocol["tapes"].values():
        assert controls.digest(proof["path"]) == proof["sha256"]


def test_producer_rejects_stale_metadata_and_never_publishes_partial_output(
    setup, tmp_path
):
    with pytest.raises(ValueError, match="metadata evidence outside"):
        _produce(setup, tmp_path, stale_metadata=True)
    assert not (tmp_path / "dataset").exists()


def test_quote_tape_cannot_carry_observed_columns(setup, tmp_path):
    events, tapes, clock, metadata = _inputs(tmp_path)
    market = pd.read_parquet(tapes["XAUUSD"])
    market["native_deal"] = 123
    market.to_parquet(tapes["XAUUSD"])

    with pytest.raises(ValueError, match="source-only quote tape"):
        runner.produce(
            setup,
            events=events,
            market_tape=tapes["XAUUSD"],
            conversion_tape=tapes["EURUSD"],
            clock_evidence=clock,
            metadata_evidence=metadata,
            cutoff=forward.END,
            out=tmp_path / "dataset",
            now=forward.END,
        )
    assert not (tmp_path / "dataset").exists()


def test_dataset_directory_is_never_overwritten(setup, tmp_path):
    dataset, _ = _produce(setup, tmp_path)
    before = (dataset / "protocol.json").read_bytes()
    other = tmp_path / "other-inputs"
    other.mkdir()
    events, tapes, clock, metadata = _inputs(other)
    with pytest.raises(ValueError, match="never overwrite"):
        runner.produce(
            setup,
            events=events,
            market_tape=tapes["XAUUSD"],
            conversion_tape=tapes["EURUSD"],
            clock_evidence=clock,
            metadata_evidence=metadata,
            cutoff=forward.END,
            out=dataset,
            now=forward.END,
        )
    assert (dataset / "protocol.json").read_bytes() == before


def test_source_only_reversal_runs_three_engines_and_verified_portfolio(
    setup, tmp_path, monkeypatch
):
    dataset, _ = _produce(setup, tmp_path)
    calls = 0
    original = forward._simulate

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(forward, "_simulate", counted)
    out = tmp_path / "results"
    report = runner.execute(setup, dataset, out=out, now=forward.END)
    payload = controls.read(out / "independent_results.json")
    portfolio = controls.read(out / "portfolio.json")["portfolio"]

    assert calls == 1
    assert report["status"] == "evidence_ready_for_review"
    assert payload["engine_evaluations"] == 6
    assert portfolio["verified"] is True
    assert portfolio["blockers"] == []
    assert portfolio["net_eur"] is not None
    for row, direction in zip(payload["results"], ("BUY", "SELL"), strict=True):
        scalar = row["scalar"]
        assert scalar["exits"][0]["reason"] == "per_leg_target"
        assert {"entry_requested", "entry_filled", "entry_acknowledged"}.issubset(
            event["kind"] for event in scalar["market_events"]
        )
        assert {"installed", "closed"}.issubset(
            event["kind"] for event in scalar["protection_events"]
        )
        if direction == "BUY":
            assert scalar["exits"][0]["exit_price"] < 102.0
        else:
            assert scalar["exits"][0]["exit_price"] > 101.2


def test_incomplete_signal_keeps_portfolio_money_and_risk_unknown(setup, tmp_path):
    events, tapes, clock, metadata = _inputs(tmp_path)
    market = pd.read_parquet(tapes["XAUUSD"])
    market.loc[6:, "bid"] = 103.0
    market.loc[6:, "ask"] = 103.2
    market.to_parquet(tapes["XAUUSD"])
    dataset = tmp_path / "dataset"
    runner.produce(
        setup,
        events=events,
        market_tape=tapes["XAUUSD"],
        conversion_tape=tapes["EURUSD"],
        clock_evidence=clock,
        metadata_evidence=metadata,
        cutoff=forward.END,
        out=dataset,
        now=forward.END,
    )

    report = runner.execute(
        setup, dataset, out=tmp_path / "results", now=forward.END
    )

    assert report["status"] == "incomplete"
    assert report["portfolio"]["verified"] is False
    assert report["portfolio"]["net_eur"] is None
    assert report["portfolio"]["max_drawdown_eur"] is None
    assert "signal_incomplete:canal2_2" in report["portfolio"]["blockers"]


def test_path_preparation_failure_preserves_prior_rows_and_blocks_portfolio(
    setup, tmp_path
):
    dataset, _ = _produce(setup, tmp_path)
    out = tmp_path / "results"
    report = runner.execute(setup, dataset, out=out, now=forward.END)
    payload = controls.read(out / "independent_results.json")
    payload["results"][1]["preparation_blockers"] = ["market_tape_has_no_quote"]
    protocol = controls.read(setup)
    dataset_payload = controls.read(dataset / "protocol.json")
    dataset_payload["messages_path"] = str(dataset / "raw_messages.json")

    portfolio = runner._portfolio(
        protocol,
        dataset_payload,
        report["eligible_signal_ids"],
        payload,
        {"blockers": [], "incomplete": []},
    )

    assert portfolio["verified"] is False
    assert portfolio["net_eur"] is None
    assert portfolio["max_drawdown_eur"] is None
    assert portfolio["signal_ids"] == ["canal2_1", "canal2_2"]
    assert portfolio["blockers"] == [
        "path_preparation_failed:canal2_2:market_tape_has_no_quote"
    ]


def test_portfolio_make_path_value_error_does_not_abort_prior_rows(
    setup, tmp_path, monkeypatch
):
    dataset, _ = _produce(setup, tmp_path)
    out = tmp_path / "results"
    report = runner.execute(setup, dataset, out=out, now=forward.END)
    payload = controls.read(out / "independent_results.json")
    protocol = controls.read(setup)
    dataset_payload = controls.read(dataset / "protocol.json")
    dataset_payload["messages_path"] = str(dataset / "raw_messages.json")
    original = runner.make_path

    def make_path(signal, *args, **kwargs):
        if signal.signal_id == "canal2_2":
            raise ValueError("market tape has no quote in the control window")
        return original(signal, *args, **kwargs)

    monkeypatch.setattr(runner, "make_path", make_path)
    portfolio = runner._portfolio(
        protocol,
        dataset_payload,
        report["eligible_signal_ids"],
        payload,
        {"blockers": [], "incomplete": []},
    )

    assert portfolio["verified"] is False
    assert portfolio["net_eur"] is None
    assert portfolio["signal_ids"] == ["canal2_1", "canal2_2"]
    assert portfolio["blockers"] == [
        "path_preparation_failed:canal2_2:market tape has no quote in the control window"
    ]


def test_late_signal_keeps_blocked_row_and_actual_engine_evaluation_count(
    setup, tmp_path
):
    events, tapes, clock, metadata = _inputs(tmp_path)
    with events.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "ev": "telegram_raw",
            "channel": "canal2",
            "message_id": 3,
            "message_revision_id": "revision-3",
            "date_utc": (forward.END - timedelta(seconds=2)).isoformat(),
            "ts": (forward.END - timedelta(seconds=2)).isoformat(),
            "text": "BUY GOLD NOW",
        }) + "\n")
    for path in tapes.values():
        frame = pd.read_parquet(path)
        final = forward.END - timedelta(seconds=4)
        frame.loc[frame.index[-1], "time_utc"] = final
        frame.loc[frame.index[-1], "source_time_msc"] = (
            int(final.timestamp() * 1000) + 10_800_000
        )
        frame.to_parquet(path)
    dataset = tmp_path / "dataset"
    runner.produce(
        setup,
        events=events,
        market_tape=tapes["XAUUSD"],
        conversion_tape=tapes["EURUSD"],
        clock_evidence=clock,
        metadata_evidence=metadata,
        cutoff=forward.END,
        out=dataset,
        now=forward.END,
    )

    out = tmp_path / "results"
    report = runner.execute(setup, dataset, out=out, now=forward.END)
    payload = controls.read(out / "independent_results.json")

    assert [row["signal_id"] for row in payload["results"]] == [
        "canal2_1", "canal2_2", "canal2_3"
    ]
    assert payload["engine_evaluations"] == 6
    assert payload["results"][2]["preparation_blockers"]
    assert all(payload["results"][2][name]["exits"] == [] for name in (
        "scalar", "fast", "oracle"
    ))
    assert report["status"] == "blocked"
    assert report["portfolio"]["net_eur"] is None
    assert any(
        blocker.startswith("path_preparation_failed:canal2_3:")
        for blocker in report["portfolio"]["blockers"]
    )
    assert "signal_without_market_quote:canal2_3" in report["incomplete"]


def test_produce_cli_accepts_explicit_normalized_inputs(
    setup, tmp_path, capsys, monkeypatch
):
    events, tapes, clock, metadata = _inputs(tmp_path)
    out = tmp_path / "dataset"
    monkeypatch.setattr(runner, "_now", lambda: forward.END)

    def forbidden_print(*args, **kwargs):
        raise AssertionError("CLI must not call global print")

    monkeypatch.setattr("builtins.print", forbidden_print)

    code = runner.main([
        "produce",
        "--protocol",
        str(setup),
        "--events",
        str(events),
        "--market-tape",
        str(tapes["XAUUSD"]),
        "--conversion-tape",
        str(tapes["EURUSD"]),
        "--clock-evidence",
        str(clock),
        "--metadata-evidence",
        str(metadata),
        "--cutoff",
        forward.END.isoformat(),
        "--out",
        str(out),
    ])
    printed = json.loads(capsys.readouterr().out)

    assert code == 0
    assert printed["status"] == "dataset_ready"
    assert (out / "protocol.json").is_file()


def test_capture_cli_normalizes_native_source_and_preserves_truthful_proofs(
    capture_setup, tmp_path, capsys, monkeypatch
):
    protocol, capture_contract = capture_setup
    capture, samples = _native_capture(tmp_path, capture_contract)
    out = tmp_path / "dataset"
    monkeypatch.setattr(
        runner, "_now", lambda: forward.END + timedelta(seconds=10)
    )

    code = runner.main([
        "produce",
        "--protocol",
        str(protocol),
        "--capture",
        str(capture),
        "--out",
        str(out),
    ])
    printed = json.loads(capsys.readouterr().out)
    dataset = controls.read(out / "protocol.json")
    clock = controls.read(out / "clock_evidence.json")
    metadata = controls.read(out / "metadata_evidence.json")
    manifest_path = capture / "manifest.json"
    raw_path = capture / "raw_messages_window.json"

    assert code == 0
    assert printed["status"] == "dataset_ready"
    assert runner.utc(printed["cutoff_utc"]) == forward.END
    assert printed["capture_blockers"] == [
        "runtime_heartbeat_pid_missing",
        "runtime_heartbeat_stale",
    ]
    assert dataset["capture_blockers"] == printed["capture_blockers"]
    assert dataset["source_evidence"]["capture_manifest"]["sha256"] == (
        controls.digest(manifest_path)
    )
    assert dataset["source_evidence"]["raw_messages_source"]["sha256"] == (
        controls.digest(raw_path)
    )
    assert runner.utc(clock["captured_at_utc"]) == samples[-1]["observed_utc"]
    assert runner.utc(clock["captured_at_utc"]) > forward.END
    assert runner.utc(metadata["captured_at_utc"]) == (
        forward.END + timedelta(seconds=8)
    )
    assert clock["source_capture_sha256"] == controls.digest(manifest_path)
    assert metadata["source_capture_sha256"] == controls.digest(manifest_path)
    for symbol in ("XAUUSD", "EURUSD"):
        retained = pd.read_parquet(out / f"{symbol}.parquet")
        assert list(retained.columns) == list(runner.TAPE_FIELDS)
        proof = dataset["source_evidence"]["quotes"][symbol]
        assert proof["source_sha256"] == controls.digest(
            capture / f"{symbol}.parquet"
        )

    report = runner.execute(
        protocol,
        out,
        out=tmp_path / "results",
        now=forward.END + timedelta(seconds=10),
    )
    assert report["status"] == "blocked"
    assert report["blockers"] == [
        "capture:runtime_heartbeat_pid_missing",
        "capture:runtime_heartbeat_stale",
    ]
    assert report["portfolio"]["net_eur"] is None
    assert "forward:capture:runtime_heartbeat_pid_missing" in (
        report["portfolio"]["blockers"]
    )
