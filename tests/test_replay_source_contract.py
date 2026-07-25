import json

import replay_source_contract


def _write_sources(root):
    ledger = root / "ledger.jsonl"
    events = root / "trade_events.jsonl"
    replay = root / "replay_trades.jsonl"
    ledger.write_text('{"sig_id":"canal1_1"}\n', encoding="utf-8")
    events.write_text(
        '{"sig":"canal1_1","ev":"market_filled"}\n',
        encoding="utf-8",
    )
    replay.write_text(
        '{"sig_id":"canal1_1","tickets":[]}\n',
        encoding="utf-8",
    )
    return ledger, events, replay


def test_manifest_binds_replay_to_exact_ledger_and_events(tmp_path):
    ledger, events, replay = _write_sources(tmp_path)

    manifest_path = replay_source_contract.write_manifest(
        replay_path=replay,
        ledger_path=ledger,
        events_path=events,
        row_count=1,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["replay"]["row_count"] == 1
    assert replay_source_contract.validate_manifest(
        replay_path=replay,
        ledger_path=ledger,
        events_path=events,
        manifest_path=manifest_path,
    ) == []


def test_manifest_rejects_replay_after_ledger_changes(tmp_path):
    ledger, events, replay = _write_sources(tmp_path)
    manifest_path = replay_source_contract.write_manifest(
        replay_path=replay,
        ledger_path=ledger,
        events_path=events,
        row_count=1,
    )

    ledger.write_text(
        '{"sig_id":"canal1_1"}\n{"sig_id":"canal1_2"}\n',
        encoding="utf-8",
    )

    assert replay_source_contract.validate_manifest(
        replay_path=replay,
        ledger_path=ledger,
        events_path=events,
        manifest_path=manifest_path,
    ) == ["source_changed:ledger"]


def test_manifest_rejects_replay_content_changes(tmp_path):
    ledger, events, replay = _write_sources(tmp_path)
    manifest_path = replay_source_contract.write_manifest(
        replay_path=replay,
        ledger_path=ledger,
        events_path=events,
        row_count=1,
    )

    replay.write_text("", encoding="utf-8")

    assert replay_source_contract.validate_manifest(
        replay_path=replay,
        ledger_path=ledger,
        events_path=events,
        manifest_path=manifest_path,
    ) == ["replay_changed"]


def test_missing_manifest_fails_closed(tmp_path):
    ledger, events, replay = _write_sources(tmp_path)

    assert replay_source_contract.validate_manifest(
        replay_path=replay,
        ledger_path=ledger,
        events_path=events,
    ) == ["missing_replay_source_manifest"]
