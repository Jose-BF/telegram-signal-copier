"""Offline collector contracts. All native APIs and protocol freezes are fixtures."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
import zipfile

import pandas as pd
import pytest

from tools import collect_simulator_window as collector
from tools import prepare_simulator_forward as forward
from tools import run_protection_controls as controls
from tools import run_simulator_forward as runner
from tests.test_run_simulator_forward import _frozen_protocol, _native_capture


UTC = timezone.utc


def explicit_bridge_fixture(tmp_path, monkeypatch, day=10):
    start = datetime(2026, 9, day, 13, tzinfo=UTC)
    end = start + timedelta(hours=2)
    # The fixture writes a collector at COLLECTOR_SOURCE, without touching any real source.
    monkeypatch.setattr(runner, "COLLECTOR_SOURCE", "producer/collect_simulator_window.py")
    source = tmp_path / runner.COLLECTOR_SOURCE
    sha = hashlib.sha256(("# Frozen morning collector fixture." + os.linesep).encode()).hexdigest()
    binding = {
        "collector_sha256": sha, "wrapper_sha256": "c" * 64,
        "expected_live_commit": "d" * 40,
        "expected_account_identity_sha256": "e" * 64,
        "collector_source": {"path": str(source), "sha256": sha},
    }
    protocol_path = _frozen_protocol(tmp_path, monkeypatch, capture_contract=binding,
                                     start_utc=start, end_utc=end)
    protocol = controls.read(protocol_path)
    capture, _ = _native_capture(tmp_path, binding, start=start, end=end)
    manifest_path = capture / "manifest.json"
    manifest = controls.read(manifest_path)
    manifest.update({
        "contract": "simulator_causal_capture_v2", "phase": "final",
        "forward_protocol": {"sha256": controls.digest(protocol_path),
                             "contract": protocol["contract"],
                             "fingerprint": protocol["fingerprint"]},
        "collector_source": binding["collector_source"],
        "source_hashes_verified_before_after": True,
    })
    manifest_path.write_bytes(controls.encode(manifest))
    return SimpleNamespace(source=source, sha=sha, protocol=protocol, manifest=manifest,
                           manifest_path=manifest_path, capture=capture, binding=binding,
                           protocol_path=protocol_path, start=start, end=end)


@pytest.mark.parametrize("day", [10, 11])
def test_explicit_collector_source_admitted_only_with_frozen_proof(tmp_path, monkeypatch, day):
    fixture = explicit_bridge_fixture(tmp_path, monkeypatch, day)
    watched = {}
    result = runner._capture_inputs(fixture.capture, None, fixture.end + timedelta(seconds=10), fixture.protocol, watched)
    assert result is not None
    assert watched[fixture.source.resolve()] == fixture.sha


@pytest.mark.parametrize("mutation", [
    "unlisted", "duplicate", "source_hash", "source_path", "outside_workspace",
    "extra_binding", "string_source", "manifest_source", "protocol_sha", "protocol_contract",
    "unverified_hashes", "window_start", "window_end", "final_early", "checkpoint_late", "source_bytes",
])
def test_bridge_rejects_unfrozen_or_wrong_window_producer(tmp_path, monkeypatch, mutation):
    fixture = explicit_bridge_fixture(tmp_path, monkeypatch)
    binding = fixture.protocol["profile_payload"]["capture_contract"]
    additional = fixture.protocol["profile_payload"]["additional_sources"]
    manifest = fixture.manifest
    if mutation == "unlisted":
        additional.pop()
    elif mutation == "duplicate":
        additional.append(additional[-1])
    elif mutation == "source_hash":
        binding["collector_source"]["sha256"] = "f" * 64
    elif mutation == "source_path":
        binding["collector_source"]["path"] = str(tmp_path / "unfrozen.py")
    elif mutation == "outside_workspace":
        binding["collector_source"]["path"] = str(tmp_path.parent / "outside.py")
    elif mutation == "extra_binding":
        binding["unexpected"] = True
    elif mutation == "string_source":
        binding["collector_source"] = str(fixture.source)
    elif mutation == "manifest_source":
        manifest["collector_source"] = {"path": str(tmp_path / "other.py"), "sha256": fixture.sha}
    elif mutation == "protocol_sha":
        manifest["forward_protocol"]["sha256"] = "f" * 64
    elif mutation == "protocol_contract":
        manifest["forward_protocol"]["contract"] = "simulator_forward_protocol_v1"
    elif mutation == "unverified_hashes":
        manifest["source_hashes_verified_before_after"] = False
    elif mutation in {"window_start", "window_end"}:
        field = "window_start_utc" if mutation == "window_start" else "window_end_exclusive_utc"
        manifest[field] = collector.strict_utc(manifest[field]) - timedelta(days=1)
    elif mutation == "final_early":
        manifest["event_cutoff_utc"] = fixture.end - timedelta(seconds=1)
    elif mutation == "checkpoint_late":
        manifest["phase"] = "checkpoint"
    else:
        fixture.source.write_bytes(b"# Modified producer.\n")
    fixture.manifest_path.write_bytes(controls.encode(manifest))
    with pytest.raises(ValueError):
        runner._capture_inputs(fixture.capture, None, fixture.end + timedelta(seconds=10), fixture.protocol, {})


def test_bridge_watches_the_producer_until_validation_finishes(tmp_path, monkeypatch):
    fixture = explicit_bridge_fixture(tmp_path, monkeypatch)
    watched = {}
    runner._capture_inputs(fixture.capture, None, fixture.end + timedelta(seconds=10), fixture.protocol, watched)
    fixture.source.write_bytes(b"# Mutation after input normalization.\n")
    with pytest.raises(ValueError, match="frozen input changed"):
        forward._finish(watched, forward.identity(), time.monotonic())


class Native(SimpleNamespace):
    def _asdict(self):
        return vars(self).copy()


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    COPY_TICKS_ALL = 0

    def __init__(self, window, observed):
        self.window, self.observed = window, observed
        self.calls = []
        self.account = Native(server="fixture-demo", login=123, currency="EUR", trade_mode=0)
        self.connected = True
        self.initialize_ok = True
        self.on_ticks = lambda: None

    def initialize(self, path, *, timeout):
        self.calls.append(("initialize", path, timeout))
        return self.initialize_ok

    def shutdown(self):
        self.calls.append(("shutdown",))

    def last_error(self):
        return (1, "fixture")

    def account_info(self):
        self.calls.append(("account_info",))
        return self.account

    def terminal_info(self):
        return Native(connected=self.connected)

    def symbol_info_tick(self, symbol):
        return Native(time_msc=int((self.observed.timestamp() + 10800) * 1000), bid=1.1, ask=1.2)

    def symbol_info(self, symbol):
        return Native(**dict.fromkeys(collector.SYMBOL_CONTRACT_FIELDS, 0))

    def positions_get(self):
        return []

    def orders_get(self):
        return []

    def copy_ticks_range(self, symbol, start, end, flags):
        self.calls.append(("ticks", symbol, start, end, flags))
        self.on_ticks()
        stamps = (start - timedelta(milliseconds=1), start, end - timedelta(milliseconds=1), end)
        return [{"time_msc": int(stamp.timestamp() * 1000), "bid": 1.1, "ask": 1.2}
                for stamp in stamps]

    def history_deals_get(self, start, end):
        self.calls.append(("deals", start, end))
        stamps = (self.window.start - timedelta(milliseconds=1), self.window.start,
                  self.window.end - timedelta(milliseconds=1), self.window.end)
        return [Native(time_msc=int((stamp.timestamp() + 10800) * 1000)) for stamp in stamps]

    def history_orders_get(self, start, end):
        self.calls.append(("history_orders", start, end))
        return []


def raw(stamp, revision="r1"):
    row = dict.fromkeys(collector.RAW_FIELDS)
    row.update(ev="telegram_raw", channel="canal2", message_id=1,
               message_revision_id=revision, date_utc=stamp.isoformat(),
               ts=stamp.isoformat(), text="BUY GOLD NOW", is_edit=False)
    return row


def capture_fixture(tmp_path, monkeypatch, *, day=10, phase="final"):
    window = collector.CaptureWindow(datetime(2026, 9, day, 13, tzinfo=UTC),
                                     datetime(2026, 9, day, 15, tzinfo=UTC))
    observed = {"pre_window": window.start - timedelta(minutes=1),
                "checkpoint": window.start + timedelta(minutes=30),
                "final": window.end + timedelta(seconds=2)}[phase]
    fake = FakeMT5(window, observed)
    source = tmp_path / "collect_simulator_window.py"
    source.write_bytes(b"# Standalone collector source fixture, never executed.\n")
    wrapper = tmp_path / "collect_simulator_window.ps1"
    wrapper.write_bytes(b"# Read-only wrapper fixture, never executed.\n")
    monkeypatch.setattr(collector, "__file__", str(source))
    monkeypatch.setattr(collector, "_now", lambda: observed)
    monkeypatch.setattr(collector, "_session_id", lambda pid: 2)
    monkeypatch.setattr(collector, "_terminal_inventory", lambda: [
        {"Id": 1234, "SessionId": 2, "StartTime": "fixture-start", "Path": str(collector.TERMINAL)}])
    monkeypatch.setattr(collector, "_git", lambda *args: "" if args[0] == "status" else "d" * 40)
    monkeypatch.setattr(collector, "_runtime_state", lambda now: ({}, []))
    native_import = collector.importlib.import_module
    monkeypatch.setattr(collector.importlib, "import_module",
                        lambda name: fake if name == "MetaTrader5" else native_import(name))
    control = tmp_path / "outputs"
    control.mkdir()
    monkeypatch.setattr(collector, "CONTROL", control)
    journal = tmp_path / "journal.jsonl"
    # The inherited whole-prefix hash and 1 MiB boundary are actual fixture bytes.
    prefix = b" " * (collector.BOUNDARY_BYTES - 1) + b"\n"
    events = [raw(window.start - timedelta(days=1), "yesterday"),
              raw(window.start - timedelta(milliseconds=1), "before"),
              raw(window.start, "first"),
              raw(window.start + timedelta(minutes=40), "later"),
              raw(window.end - timedelta(milliseconds=1), "last"), raw(window.end, "end")]
    journal.write_bytes(prefix + b"".join(controls.encode(row).replace(b"\n", b" ") + b"\n"
                                         for row in events))
    monkeypatch.setattr(collector, "JOURNAL", journal)
    context = tmp_path / "context.json"
    controls.save(context, {"live_strategy_contract": {"ev": "live_strategy_contract"}})
    prior_raw = tmp_path / "prior_raw.json"
    controls.save(prior_raw, [raw(window.start - timedelta(days=1), "prior-day")])
    anchor_path = tmp_path / "anchor.json"
    controls.save(anchor_path, {
        "contract": "morning_causal_capture_v1",
        "window_start_utc": window.start - timedelta(days=1),
        "window_end_exclusive_utc": window.end - timedelta(days=1),
        "event_cutoff_utc": window.end - timedelta(days=1),
        "event_evidence": {
            "prefix_end_bytes": len(prefix),
            "inherited_whole_prefix_sha256": hashlib.sha256(prefix).hexdigest(),
            "prefix_boundary_sha256": hashlib.sha256(prefix).hexdigest(),
            "source_chain_sha256": "a" * 64,
            "latest_context_file": context.name, "latest_context_sha256": controls.digest(context),
            "raw_messages_file": prior_raw.name, "raw_messages_sha256": controls.digest(prior_raw),
        },
    })
    identity = hashlib.sha256(b"fixture-demo:123").hexdigest()
    proof = {"path": str(source), "sha256": controls.digest(source)}
    protocol_path = tmp_path / "protocol.json"
    protocol = {
        "contract": "simulator_forward_protocol_v2", "start_utc": window.start.isoformat(),
        "end_utc": window.end.isoformat(), "frozen_at_utc": (window.start - timedelta(hours=1)).isoformat(),
        "profile_payload": {"additional_sources": [proof], "capture_contract": {
            "collector_source": proof, "collector_sha256": proof["sha256"],
            "wrapper_sha256": controls.digest(wrapper), "expected_live_commit": "d" * 40,
            "expected_account_identity_sha256": identity,
        }},
    }
    protocol["fingerprint"] = hashlib.sha256(controls.encode(protocol)).hexdigest()
    controls.save(protocol_path, protocol)
    argv = ["--start-utc", window.start.isoformat(), "--end-utc", window.end.isoformat(),
            "--protocol", str(protocol_path), "--protocol-sha256", controls.digest(protocol_path),
            "--phase", phase, "--anchor-manifest", str(anchor_path),
            "--anchor-manifest-sha256", controls.digest(anchor_path),
            "--expected-commit", "d" * 40, "--expected-account-identity", identity,
            "--expected-terminal-pid", "1234", "--expected-session-id", "2",
            "--wrapper-path", str(wrapper)]
    return SimpleNamespace(window=window, observed=observed, fake=fake, source=source,
                           wrapper=wrapper, protocol_path=protocol_path, argv=argv,
                           args=collector._parse_args(argv), journal=journal,
                           anchor=anchor_path, control=control, prior_raw=prior_raw)


@pytest.mark.parametrize("day", [10, 11])
@pytest.mark.parametrize("phase", ["pre_window", "checkpoint", "final"])
def test_standalone_capture_has_real_dates_exact_window_and_read_only_native_calls(tmp_path, monkeypatch, day, phase):
    fixture = capture_fixture(tmp_path, monkeypatch, day=day, phase=phase)
    # This is the standalone entry point; it never imports the real MT5 package.
    assert collector.main(fixture.argv) == 0
    output, = [path for path in fixture.control.iterdir() if path.is_dir()]
    manifest = controls.read(output / "manifest.json")
    assert manifest["contract"] == "simulator_causal_capture_v2"
    assert manifest["phase"] == phase
    assert collector.strict_utc(manifest["window_start_utc"]) == fixture.window.start
    assert collector.strict_utc(manifest["window_end_exclusive_utc"]) == fixture.window.end
    assert collector.strict_utc(manifest["event_cutoff_utc"]) == fixture.observed
    assert manifest["forward_protocol"]["sha256"] == controls.digest(fixture.protocol_path)
    assert manifest["source_hashes_verified_before_after"] is True
    assert manifest["read_only"] is True and manifest["orders_sent_by_collector"] == 0
    assert manifest["terminal_before"] == manifest["terminal_after"]
    assert fixture.observed.strftime("%Y%m%dt%H%M%Sz") in manifest["label"]
    rows = controls.read(output / "raw_messages_window.json")
    assert [row["message_revision_id"] for row in rows] == {
        "pre_window": [], "checkpoint": ["first"], "final": ["first", "later", "last"],
    }[phase]
    assert manifest["event_evidence"]["raw_message_causal_availability"]["window_rows_cumulative"] == len(rows)
    assert manifest["event_evidence"]["source_integrity_contract"] == "prior_verified_whole_hash_plus_boundary_and_delta_chain"
    assert fixture.fake.calls[0] == ("initialize", str(collector.TERMINAL), 15000)
    assert fixture.fake.calls[-1] == ("shutdown",)
    for call in fixture.fake.calls:
        if call[0] != "ticks":
            continue
        _, symbol, start, end, _ = call
        lookback = timedelta(seconds=5) if symbol == "EURUSD" else timedelta(0)
        assert start == fixture.window.start - lookback + timedelta(hours=3)
        assert end == min(fixture.observed, fixture.window.end) + timedelta(hours=3)
        frame = pd.read_parquet(output / f"{symbol}.parquet")
        assert len(frame) == 2
        assert frame["time_utc"].min() == fixture.window.start - lookback
        assert frame["time_utc"].max() < min(fixture.observed, fixture.window.end)
    deal_call, = [call for call in fixture.fake.calls if call[0] == "deals"]
    assert deal_call[1] == fixture.window.start - timedelta(hours=6)
    assert deal_call[2] == min(max(fixture.observed, fixture.window.start), fixture.window.end) + timedelta(hours=6)
    assert manifest["broker_evidence"]["in_window_deal_rows"] == {"pre_window": 0, "checkpoint": 1, "final": 2}[phase]
    with zipfile.ZipFile(output.with_suffix(".zip")) as archive:
        assert archive.read("manifest.json") == (output / "manifest.json").read_bytes()


@pytest.mark.parametrize("start,end", [
    ("2026-09-10T13:00:00", "2026-09-10T15:00:00Z"),
    ("2026-09-10T15:00:00+02:00", "2026-09-10T17:00:00+02:00"),
    ("2026-09-10T13:00:00Z", "2026-09-10T15:00:00.001Z"),
    ("2026-09-10T13:00:00Z", "2026-09-10T13:00:00Z"),
    ("2026-09-10T13:00:00Z", "2026-09-09T15:00:00Z"),
    (None, "2026-09-10T15:00:00Z"),
])
def test_window_rejects_naive_non_utc_missing_and_unbounded_dates(start, end):
    with pytest.raises(ValueError):
        collector.CaptureWindow(start, end)


@pytest.mark.parametrize("flag", ["--start-utc", "--end-utc", "--protocol", "--protocol-sha256"])
def test_cli_requires_both_window_args_and_frozen_protocol(tmp_path, monkeypatch, flag):
    fixture = capture_fixture(tmp_path, monkeypatch)
    index = fixture.argv.index(flag)
    del fixture.argv[index:index + 2]
    with pytest.raises(SystemExit):
        collector._parse_args(fixture.argv)
    assert not fixture.fake.calls


@pytest.mark.parametrize("phase,seconds", [("pre_window", 0), ("pre_window", 7201),
                                         ("checkpoint", -1), ("checkpoint", 7200),
                                         ("final", 7199), ("bogus", 5)])
def test_phase_boundaries_cannot_be_backdated(phase, seconds):
    start = datetime(2026, 9, 10, 13, tzinfo=UTC)
    window = collector.CaptureWindow(start, start + timedelta(hours=2))
    with pytest.raises(collector.CaptureError):
        collector._phase_guard(phase, start + timedelta(seconds=seconds), window)


@pytest.mark.parametrize("kind", ["source", "wrapper", "protocol", "anchor", "prior_raw"])
@pytest.mark.parametrize("when", ["before", "during"])
def test_immutable_input_mutation_never_publishes_success(tmp_path, monkeypatch, kind, when):
    fixture = capture_fixture(tmp_path, monkeypatch)
    path = {"source": fixture.source, "wrapper": fixture.wrapper,
            "protocol": fixture.protocol_path, "anchor": fixture.anchor,
            "prior_raw": fixture.prior_raw}[kind]
    def mutate():
        path.write_bytes(path.read_bytes() + b" ")
    if when == "before":
        mutate()
    else:
        fixture.fake.on_ticks = mutate
    assert collector.main(fixture.argv) == 2
    assert not list(fixture.control.glob("*/manifest.json"))
    assert not any(call[0] == "initialize" for call in fixture.fake.calls) if when == "before" else (
        fixture.fake.calls[-1] == ("shutdown",))


@pytest.mark.parametrize("guard", ["session", "missing_terminal", "pid", "terminal_session", "path",
                                 "two_terminals", "commit", "dirty", "anchor_boundary", "short_prefix"])
def test_all_preinitialize_guards_fail_closed(tmp_path, monkeypatch, guard):
    fixture = capture_fixture(tmp_path, monkeypatch)
    if guard == "session":
        monkeypatch.setattr(collector, "_session_id", lambda pid: 99)
    elif guard in {"commit", "dirty"}:
        monkeypatch.setattr(collector, "_git", lambda *args: "dirty" if guard == "dirty" and args[0] == "status" else "e" * 40)
    elif guard in {"anchor_boundary", "short_prefix"}:
        fixture.journal.write_bytes(b"x" * (collector.BOUNDARY_BYTES if guard == "anchor_boundary" else 10))
    else:
        item = {"Id": 1234, "SessionId": 2, "StartTime": "fixture", "Path": str(collector.TERMINAL)}
        if guard == "pid":
            item["Id"] = 55
        if guard == "terminal_session":
            item["SessionId"] = 99
        if guard == "path":
            item["Path"] = str(tmp_path / "wrong-terminal.exe")
        inventory = [] if guard == "missing_terminal" else [item, item] if guard == "two_terminals" else [item]
        monkeypatch.setattr(collector, "_terminal_inventory", lambda: inventory)
    assert collector.main(fixture.argv) == 2
    assert not fixture.fake.calls
    assert not list(fixture.control.glob("*/manifest.json"))


@pytest.mark.parametrize("guard", ["identity", "currency", "real_account", "disconnected", "initialize_failure"])
def test_account_and_connection_guards_always_disconnect_without_orders(tmp_path, monkeypatch, guard):
    fixture = capture_fixture(tmp_path, monkeypatch)
    if guard == "identity":
        fixture.fake.account.login += 1
    elif guard == "currency":
        fixture.fake.account.currency = "USD"
    elif guard == "real_account":
        fixture.fake.account.trade_mode = 2
    elif guard == "disconnected":
        fixture.fake.connected = False
    else:
        fixture.fake.initialize_ok = False
    assert collector.main(fixture.argv) == 2
    assert fixture.fake.calls[0] == ("initialize", str(collector.TERMINAL), 15000)
    assert fixture.fake.calls[-1] == ("shutdown",)
    assert not any(call[0] in {"ticks", "deals", "history_orders"} for call in fixture.fake.calls)
    assert not list(fixture.control.glob("*/manifest.json"))


@pytest.mark.parametrize("flag,value", [
    ("--max-event-bytes", "0"), ("--max-event-bytes", "536870913"),
    ("--max-event-rows", "1000001"), ("--max-tick-rows-per-symbol", "2000001"),
    ("--max-history-rows", "100001"), ("--max-runtime-seconds", "29"),
    ("--max-runtime-seconds", "601"), ("--expected-terminal-pid", "0"),
    ("--expected-session-id", "0"),
])
def test_cli_rejects_weakened_budgets_and_session_zero(tmp_path, monkeypatch, flag, value):
    fixture = capture_fixture(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        collector._parse_args([*fixture.argv, flag, value])
    assert not fixture.fake.calls


@pytest.mark.parametrize("budget", ["event_bytes", "event_rows", "tick_rows", "history_rows", "runtime"])
def test_capture_data_and_time_budgets_do_not_publish_manifest(tmp_path, monkeypatch, budget):
    fixture = capture_fixture(tmp_path, monkeypatch)
    if budget == "runtime":
        clock = [0.0]
        monkeypatch.setattr(collector.time, "monotonic", lambda: clock[0])
        fixture.fake.on_ticks = lambda: clock.__setitem__(0, 601.0)
        argv = fixture.argv
    else:
        flag = {"event_bytes": "--max-event-bytes", "event_rows": "--max-event-rows",
                "tick_rows": "--max-tick-rows-per-symbol", "history_rows": "--max-history-rows"}[budget]
        argv = [*fixture.argv, flag, "1"]
    assert collector.main(argv) == 2
    assert not list(fixture.control.glob("*/manifest.json"))
    if any(call[0] == "initialize" for call in fixture.fake.calls):
        assert fixture.fake.calls[-1] == ("shutdown",)


@pytest.mark.parametrize("moment", ["before_initialize", "after_initialize", "after_shutdown"])
def test_terminal_identity_is_rechecked_throughout_capture(tmp_path, monkeypatch, moment):
    fixture = capture_fixture(tmp_path, monkeypatch)
    calls = [0]
    def inventory():
        calls[0] += 1
        threshold = {"before_initialize": 2, "after_initialize": 3, "after_shutdown": 4}[moment]
        return [{"Id": 1234, "SessionId": 2, "StartTime": "original" if calls[0] < threshold else "replaced",
                 "Path": str(collector.TERMINAL)}]
    monkeypatch.setattr(collector, "_terminal_inventory", inventory)
    assert collector.main(fixture.argv) == 2
    assert not list(fixture.control.glob("*/manifest.json"))
    if moment == "before_initialize":
        assert not fixture.fake.calls
    else:
        assert fixture.fake.calls[-1] == ("shutdown",)


def test_label_cannot_claim_yesterday_or_another_phase(tmp_path, monkeypatch):
    fixture = capture_fixture(tmp_path, monkeypatch)
    assert collector.main([*fixture.argv, "--label", "final_20260909t150002z_1234abcd"]) == 2
    with pytest.raises(SystemExit):
        collector._parse_args([*fixture.argv, "--label", "check_20260910t150002z_1234abcd"])
    assert not fixture.fake.calls


def test_existing_output_is_untouched_even_on_failure(tmp_path, monkeypatch):
    fixture = capture_fixture(tmp_path, monkeypatch)
    argv = [*fixture.argv, "--label", f"final_{fixture.observed:%Y%m%dt%H%M%Sz}_1234abcd"]
    assert collector.main(argv) == 0
    before = {str(path): controls.digest(path) for path in fixture.control.rglob("*") if path.is_file()}
    assert collector.main(argv) == 2
    after = {str(path): controls.digest(path) for path in fixture.control.rglob("*") if path.is_file()}
    assert before == after
    assert not list(fixture.control.glob("*/failure.json"))


@pytest.mark.parametrize("change", ["wrong_window", "naive", "late_freeze", "future_freeze", "v1_relabel",
                                  "missing_source", "duplicate_source", "binding_commit", "binding_account"])
def test_collector_requires_exact_frozen_window_and_source_binding(tmp_path, monkeypatch, change):
    fixture = capture_fixture(tmp_path, monkeypatch)
    value = controls.read(fixture.protocol_path)
    if change == "wrong_window":
        value["start_utc"] = (fixture.window.start + timedelta(minutes=1)).isoformat()
    elif change == "naive":
        value["start_utc"] = "2026-09-10T13:00:00"
    elif change == "late_freeze":
        value["frozen_at_utc"] = fixture.window.start.isoformat()
    elif change == "future_freeze":
        monkeypatch.setattr(collector, "_now", lambda: fixture.window.start - timedelta(hours=2))
        fixture.argv[fixture.argv.index("--phase") + 1] = "pre_window"
    elif change == "v1_relabel":
        value["contract"] = "simulator_forward_protocol_v1"
    elif change == "missing_source":
        value["profile_payload"]["additional_sources"] = []
    elif change == "duplicate_source":
        value["profile_payload"]["additional_sources"] *= 2
    elif change == "binding_commit":
        value["profile_payload"]["capture_contract"]["expected_live_commit"] = "e" * 40
    else:
        value["profile_payload"]["capture_contract"]["expected_account_identity_sha256"] = "e" * 64
    value.pop("fingerprint")
    value["fingerprint"] = hashlib.sha256(controls.encode(value)).hexdigest()
    fixture.protocol_path.write_bytes(controls.encode(value))
    fixture.argv[fixture.argv.index("--protocol-sha256") + 1] = controls.digest(fixture.protocol_path)
    assert collector.main(fixture.argv) == 2
    assert not fixture.fake.calls


def test_source_mutation_while_archiving_cannot_publish_admissible_manifest(tmp_path, monkeypatch):
    fixture = capture_fixture(tmp_path, monkeypatch)
    native_write = zipfile.ZipFile.write
    def mutate_after_write(self, *args, **kwargs):
        result = native_write(self, *args, **kwargs)
        fixture.source.write_bytes(b"# Mutated during packaging.\n")
        return result
    monkeypatch.setattr(zipfile.ZipFile, "write", mutate_after_write)
    assert collector.main(fixture.argv) == 2
    assert not list(fixture.control.glob("*/manifest.json"))
    assert not list(fixture.control.glob("*.zip"))


def test_append_only_prefix_delta_mutation_is_detected(tmp_path, monkeypatch):
    fixture = capture_fixture(tmp_path, monkeypatch)
    def mutate():
        payload = fixture.journal.read_bytes()
        fixture.journal.write_bytes(payload.replace(b"BUY", b"SELL", 1))
    fixture.fake.on_ticks = mutate
    assert collector.main(fixture.argv) == 2
    assert not list(fixture.control.glob("*/manifest.json"))


def test_archived_collector_is_not_imported_or_rewritten():
    root = Path(__file__).resolve().parents[1]
    archived = root / "runtime_data/causal_capture_today_20260909/operations/collect_morning_window.py"
    old = archived.read_text(encoding="utf-8")
    new = (root / "tools/collect_simulator_window.py").read_text(encoding="utf-8")
    assert "WINDOW_START = datetime(2026, 9, 9, 6, 30, tzinfo=UTC)" in old
    assert "WINDOW_START" not in new and "WINDOW_END" not in new
    assert "import collect_morning_window" not in new
    assert "spec_from_file_location" not in new
    assert "order_send(" not in new and "mt5.login(" not in new


def test_checkpoint_to_final_preserves_causal_chain_and_inherited_missing_cases(tmp_path, monkeypatch):
    fixture = capture_fixture(tmp_path, monkeypatch, phase="checkpoint")
    prefix = fixture.journal.read_bytes()[:collector.BOUNDARY_BYTES]
    first = raw(fixture.window.start, "same-revision")
    first.pop("reply_to_msg_id")
    fixture.journal.write_bytes(prefix + json.dumps(first).encode() + b"\nnot-json\n")
    assert collector.main(fixture.argv) == 0
    checkpoint, = [path for path in fixture.control.iterdir() if path.is_dir()]
    frozen_hashes = {path: controls.digest(path) for path in checkpoint.iterdir()}
    before = controls.read(checkpoint / "manifest.json")
    # A later revision conflict must also be evaluated against the retained rows.
    second = raw(fixture.window.start + timedelta(minutes=40), "same-revision")
    fixture.journal.write_bytes(fixture.journal.read_bytes() + json.dumps(second).encode() + b"\n")
    fixture.observed = fixture.window.end + timedelta(seconds=2)
    fixture.fake.observed = fixture.observed
    monkeypatch.setattr(collector, "_now", lambda: fixture.observed)
    argv = [*fixture.argv, "--phase", "final", "--anchor-manifest", str(checkpoint / "manifest.json"),
            "--anchor-manifest-sha256", controls.digest(checkpoint / "manifest.json")]
    assert collector.main(argv) == 0
    final, = [path for path in fixture.control.iterdir() if path.is_dir() and path != checkpoint]
    after = controls.read(final / "manifest.json")
    evidence = after["event_evidence"]
    assert evidence["anchor_manifest_sha256"] == controls.digest(checkpoint / "manifest.json")
    assert evidence["anchor_prefix_bytes"] == before["event_evidence"]["prefix_end_bytes"]
    assert evidence["inherited_whole_prefix_sha256"] == before["event_evidence"]["inherited_whole_prefix_sha256"]
    assert evidence["source_chain_sha256"] != before["event_evidence"]["source_chain_sha256"]
    assert evidence["raw_message_causal_availability"]["window_rows_cumulative"] == 2
    assert evidence["raw_message_causal_availability"]["missing_fields"] == {"missing_key:reply_to_msg_id": 1}
    assert evidence["raw_message_causal_availability"]["revision_conflicts"] == ["same-revision"]
    assert evidence["invalid_delta_rows"] == before["event_evidence"]["invalid_delta_rows"]
    assert frozen_hashes == {path: controls.digest(path) for path in checkpoint.iterdir()}


def test_legacy_whole_prefix_anchor_requires_boundary_and_context(tmp_path, monkeypatch):
    fixture = capture_fixture(tmp_path, monkeypatch)
    original = controls.read(fixture.anchor)
    evidence = original["event_evidence"]
    legacy = {"contract": "incremental_live_day_capture_v1", "event_evidence": {
        "reconstructed_prefix_bytes": evidence["prefix_end_bytes"],
        "reconstructed_prefix_sha256": evidence["inherited_whole_prefix_sha256"],
    }}
    fixture.anchor.write_bytes(controls.encode(legacy))
    argv = [*fixture.argv, "--anchor-manifest-sha256", controls.digest(fixture.anchor)]
    assert collector.main(argv) == 2
    argv += ["--legacy-boundary-sha256", evidence["prefix_boundary_sha256"]]
    assert collector.main(argv) == 2
    argv += ["--prior-context-json", str(fixture.anchor.parent / evidence["latest_context_file"]),
             "--prior-context-sha256", evidence["latest_context_sha256"]]
    assert collector.main(argv) == 0


def test_anchor_from_other_window_cannot_skip_requested_inputs(tmp_path, monkeypatch):
    fixture = capture_fixture(tmp_path, monkeypatch)
    anchor = controls.read(fixture.anchor)
    anchor["event_cutoff_utc"] = fixture.window.start + timedelta(minutes=1)
    fixture.anchor.write_bytes(controls.encode(anchor))
    argv = [*fixture.argv, "--anchor-manifest-sha256", controls.digest(fixture.anchor)]
    assert collector.main(argv) == 2
    assert not fixture.fake.calls


@pytest.mark.parametrize("issue", ["stale_clock", "ambiguous_clock", "inconsistent_clock", "invalid_quote"])
def test_native_clock_and_quote_guards_still_block(tmp_path, monkeypatch, issue):
    fixture = capture_fixture(tmp_path, monkeypatch)
    original = fixture.fake.symbol_info_tick
    def quote(symbol):
        tick = original(symbol)
        if issue == "stale_clock":
            tick.time_msc -= 16000
        elif issue == "ambiguous_clock":
            tick.time_msc -= 1800000
        elif issue == "inconsistent_clock" and symbol == "EURUSD":
            tick.time_msc += 3600000
        elif issue == "invalid_quote":
            tick.bid = float("nan")
        return tick
    fixture.fake.symbol_info_tick = quote
    assert collector.main(fixture.argv) == 2
    assert fixture.fake.calls[-1] == ("shutdown",)
    assert not list(fixture.control.glob("*/manifest.json"))


def test_wrapper_has_hidden_bounded_process_logs_and_no_scheduler():
    root = Path(__file__).resolve().parents[1]
    wrapper = root / "runtime_data/calculator_delivery_20260910/forward_operations/collect_simulator_window.ps1"
    text = wrapper.read_text(encoding="utf-8")
    assert "-WindowStyle Hidden" in text
    assert "-RedirectStandardOutput" in text and "-RedirectStandardError" in text
    assert "$process.WaitForExit($MaxRuntimeSeconds * 1000)" in text
    assert "$process.Kill()" in text
    assert "Stop-Process" not in text and "ScheduledTask" not in text
    assert "--start-utc" in text and "--end-utc" in text
    assert "--protocol-sha256" in text and "--wrapper-path" in text


@pytest.mark.parametrize("phase", ["pre_window", "checkpoint", "final"])
def test_event_budget_defaults_do_not_expand_with_bootstrap_support(tmp_path, monkeypatch, phase):
    fixture = capture_fixture(tmp_path, monkeypatch, phase=phase)
    args = collector._parse_args(fixture.argv)
    assert args.max_event_bytes == 402_653_184
    assert args.max_event_rows == 750_000
    assert args.max_tick_rows_per_symbol == 2_000_000
    assert args.max_runtime_seconds == 420


@pytest.mark.parametrize("phase", ["pre_window", "checkpoint", "final"])
@pytest.mark.parametrize("flag,value", [("--max-event-bytes", "1073741824"),
                                       ("--max-event-rows", "2000000")])
def test_expanded_bootstrap_budget_requires_explicit_prewindow_arguments(tmp_path, monkeypatch, phase, flag, value):
    fixture = capture_fixture(tmp_path, monkeypatch, phase=phase)
    argv = [*fixture.argv, flag, value, "--max-runtime-seconds", "600"]
    if phase == "pre_window":
        args = collector._parse_args(argv)
        assert getattr(args, flag[2:].replace("-", "_")) == int(value)
        assert args.max_runtime_seconds == 600
    else:
        with pytest.raises(SystemExit):
            collector._parse_args(argv)


@pytest.mark.parametrize("flag,value", [("--max-event-bytes", "1073741825"),
                                       ("--max-event-rows", "2000001"),
                                       ("--max-tick-rows-per-symbol", "2000001"),
                                       ("--max-runtime-seconds", "601")])
def test_prewindow_bootstrap_cannot_exceed_authorized_bounds(tmp_path, monkeypatch, flag, value):
    fixture = capture_fixture(tmp_path, monkeypatch, phase="pre_window")
    with pytest.raises(SystemExit):
        collector._parse_args([*fixture.argv, flag, value])


def test_bootstrap_preserves_entire_delta_without_importing_old_messages(tmp_path, monkeypatch):
    fixture = capture_fixture(tmp_path, monkeypatch, phase="pre_window")
    prefix = fixture.journal.read_bytes()[:collector.BOUNDARY_BYTES]
    old_rows = [raw(fixture.window.start - timedelta(hours=hour), f"prior-{hour}") for hour in range(24, 0, -1)]
    delta = b"".join(json.dumps(row).encode() + b"\n" for row in old_rows)
    fixture.journal.write_bytes(prefix + delta)
    assert collector.main([*fixture.argv, "--max-event-bytes", "1073741824",
                           "--max-event-rows", "2000000", "--max-runtime-seconds", "600"]) == 0
    output, = [path for path in fixture.control.iterdir() if path.is_dir()]
    manifest = controls.read(output / "manifest.json")
    evidence = manifest["event_evidence"]
    assert evidence["delta_rows"] == 24
    assert evidence["delta_bytes"] == len(delta)
    assert evidence["delta_sha256"] == hashlib.sha256(delta).hexdigest()
    assert evidence["prefix_end_bytes"] == len(prefix) + len(delta)
    assert evidence["full_source_rescan"] is False
    assert controls.read(output / "raw_messages_window.json") == []
    import gzip
    assert gzip.decompress((output / "event_delta.jsonl.gz").read_bytes()) == delta
    limits = controls.read(output / "capture_started.json")["limits"]
    assert limits["event_bytes"] == 1_073_741_824
    assert limits["event_rows"] == 2_000_000
    assert limits["runtime_seconds"] == 600
