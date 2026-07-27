import json
from pathlib import Path
import sys
from types import SimpleNamespace


def test_installer_copies_compiles_and_hashes_the_read_only_service(
    tmp_path,
):
    from tools import install_broker_money_snapshot_service as installer

    source = tmp_path / "repo" / "BrokerMoneySnapshotService.mq5"
    source.parent.mkdir(parents=True)
    source.write_text(
        "#property service\nvoid OnStart() {}\n",
        encoding="utf-8",
    )
    data_path = tmp_path / "terminal-data"
    metaeditor = tmp_path / "MetaTrader 5" / "metaeditor64.exe"
    metaeditor.parent.mkdir(parents=True)
    metaeditor.write_bytes(b"fake")
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        target = Path(command[1].split(":", 1)[1])
        log = Path(command[2].split(":", 1)[1])
        target.with_suffix(".ex5").write_bytes(b"compiled")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "Result: 0 errors, 0 warnings, 10 ms elapsed",
            encoding="utf-16",
        )
        return SimpleNamespace(returncode=0)

    result = installer.install_service(
        source=source,
        terminal_data_path=data_path,
        metaeditor=metaeditor,
        runner=runner,
    )

    expected_target = (
        data_path
        / "MQL5"
        / "Services"
        / "TelegramSignalCopier"
        / source.name
    )
    assert result["status"] == "compiled"
    assert result["source_path"] == str(expected_target)
    assert result["compiled_path"] == str(expected_target.with_suffix(".ex5"))
    manifest = json.loads(
        Path(result["manifest_path"]).read_text(encoding="utf-8")
    )
    assert manifest["source_sha256"] == result["source_sha256"]
    assert len(manifest["compiled_sha256"]) == 64
    assert len(result["source_sha256"]) == 64
    assert expected_target.read_text(encoding="utf-8") == source.read_text(
        encoding="utf-8"
    )
    assert calls[0][0][0] == str(metaeditor)
    assert calls[0][1]["timeout"] == 90


def test_installer_fails_when_metaeditor_reports_compile_errors(tmp_path):
    from tools import install_broker_money_snapshot_service as installer

    source = tmp_path / "BrokerMoneySnapshotService.mq5"
    source.write_text("#property service\n", encoding="utf-8")
    data_path = tmp_path / "terminal-data"
    metaeditor = tmp_path / "metaeditor64.exe"
    metaeditor.write_bytes(b"fake")

    def runner(command, **_kwargs):
        log = Path(command[2].split(":", 1)[1])
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "Result: 1 errors, 0 warnings, 10 ms elapsed",
            encoding="utf-16",
        )
        return SimpleNamespace(returncode=0)

    try:
        installer.install_service(
            source=source,
            terminal_data_path=data_path,
            metaeditor=metaeditor,
            runner=runner,
        )
    except RuntimeError as exc:
        assert "MQL5 compile failed" in str(exc)
    else:
        raise AssertionError("compile errors must fail installation")


def test_failed_compile_preserves_previous_working_service(tmp_path):
    from tools import install_broker_money_snapshot_service as installer

    source = tmp_path / "repo" / "BrokerMoneySnapshotService.mq5"
    source.parent.mkdir(parents=True)
    source.write_text("#property service\n// new\n", encoding="utf-8")
    data_path = tmp_path / "terminal-data"
    target = (
        data_path
        / "MQL5"
        / "Services"
        / "TelegramSignalCopier"
        / source.name
    )
    target.parent.mkdir(parents=True)
    target.write_text("#property service\n// old\n", encoding="utf-8")
    compiled = target.with_suffix(".ex5")
    compiled.write_bytes(b"old-working-ex5")
    metaeditor = tmp_path / "metaeditor64.exe"
    metaeditor.write_bytes(b"fake")

    def runner(command, **_kwargs):
        log = Path(command[2].split(":", 1)[1])
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "Result: 1 errors, 0 warnings, 10 ms elapsed",
            encoding="utf-16",
        )
        return SimpleNamespace(returncode=0)

    try:
        installer.install_service(
            source=source,
            terminal_data_path=data_path,
            metaeditor=metaeditor,
            runner=runner,
        )
    except RuntimeError as exc:
        assert "MQL5 compile failed" in str(exc)
    else:
        raise AssertionError("compile errors must fail installation")

    assert target.read_text(encoding="utf-8").endswith("// old\n")
    assert compiled.read_bytes() == b"old-working-ex5"


def test_installer_accepts_metaeditor_exit_one_when_artifacts_are_clean(
    tmp_path,
):
    from tools import install_broker_money_snapshot_service as installer

    source = tmp_path / "BrokerMoneySnapshotService.mq5"
    source.write_text("#property service\n", encoding="utf-8")
    data_path = tmp_path / "terminal-data"
    metaeditor = tmp_path / "metaeditor64.exe"
    metaeditor.write_bytes(b"fake")

    def runner(command, **_kwargs):
        target = Path(command[1].split(":", 1)[1])
        log = Path(command[2].split(":", 1)[1])
        target.with_suffix(".ex5").write_bytes(b"compiled")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "Result: 0 errors, 0 warnings, 10 ms elapsed",
            encoding="utf-16",
        )
        return SimpleNamespace(returncode=1)

    result = installer.install_service(
        source=source,
        terminal_data_path=data_path,
        metaeditor=metaeditor,
        runner=runner,
    )

    assert result["status"] == "compiled"
    assert result["metaeditor_exit_code"] == 1


def test_verify_only_reports_inactive_service_without_traceback(
    monkeypatch,
    capsys,
):
    from tools import install_broker_money_snapshot_service as installer

    fake_mt5 = SimpleNamespace(
        initialize=lambda: True,
        shutdown=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    monkeypatch.setattr(
        installer,
        "_terminal_paths",
        lambda _mt5: (Path("terminal"), Path("metaeditor.exe")),
    )
    monkeypatch.setattr(
        installer,
        "verify_active_service",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("missing verified MQL5 broker swap evidence")
        ),
    )

    result = installer.main(["--verify-only"])

    assert result == 1
    output = capsys.readouterr().out
    assert "Broker-money service: INACTIVE" in output
    assert "missing verified MQL5 broker swap evidence" in output


def test_verify_active_rejects_tampered_compiled_service(
    tmp_path,
    monkeypatch,
):
    from tools import install_broker_money_snapshot_service as installer

    source = tmp_path / "repo" / "BrokerMoneySnapshotService.mq5"
    source.parent.mkdir(parents=True)
    source.write_text("#property service\n", encoding="utf-8")
    data_path = tmp_path / "terminal-data"
    metaeditor = tmp_path / "metaeditor64.exe"
    metaeditor.write_bytes(b"fake")

    def runner(command, **_kwargs):
        staging = Path(command[1].split(":", 1)[1])
        log = Path(command[2].split(":", 1)[1])
        staging.with_suffix(".ex5").write_bytes(b"compiled")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "Result: 0 errors, 0 warnings, 10 ms elapsed",
            encoding="utf-16",
        )
        return SimpleNamespace(returncode=0)

    result = installer.install_service(
        source=source,
        terminal_data_path=data_path,
        metaeditor=metaeditor,
        runner=runner,
    )
    Path(result["compiled_path"]).write_bytes(b"tampered")
    mt5 = SimpleNamespace(
        terminal_info=lambda: SimpleNamespace(
            data_path=str(data_path),
            path=str(metaeditor.parent),
        )
    )

    try:
        installer.verify_active_service(mt5, source=source)
    except RuntimeError as exc:
        assert str(exc) == "installed broker-money EX5 hash mismatch"
    else:
        raise AssertionError("tampered EX5 must not verify as active")


def test_verify_active_rejects_non_certifiable_money_contract(
    tmp_path,
    monkeypatch,
):
    from tools import install_broker_money_snapshot_service as installer

    source = tmp_path / "repo" / "BrokerMoneySnapshotService.mq5"
    source.parent.mkdir(parents=True)
    source.write_text("#property service\n", encoding="utf-8")
    data_path = tmp_path / "terminal-data"
    metaeditor = tmp_path / "metaeditor64.exe"
    metaeditor.write_bytes(b"fake")

    def runner(command, **_kwargs):
        staging = Path(command[1].split(":", 1)[1])
        log = Path(command[2].split(":", 1)[1])
        staging.with_suffix(".ex5").write_bytes(b"compiled")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "Result: 0 errors, 0 warnings, 10 ms elapsed",
            encoding="utf-16",
        )
        return SimpleNamespace(returncode=0)

    installer.install_service(
        source=source,
        terminal_data_path=data_path,
        metaeditor=metaeditor,
        runner=runner,
    )
    mt5 = SimpleNamespace(
        terminal_info=lambda: SimpleNamespace(
            data_path=str(data_path),
            path=str(metaeditor.parent),
        )
    )
    monkeypatch.setattr(
        installer.capture_broker_money_contract,
        "build_contract",
        lambda _mt5: {
            "schema_version": 2,
            "live_validation": {"valid": False},
            "costs": {"swap_model": "unsupported_mt5_swap_mode_2"},
            "swap_snapshots": [{"time_evidence": {}}],
        },
    )

    try:
        installer.verify_active_service(mt5, source=source)
    except RuntimeError as exc:
        assert str(exc).startswith("invalid broker money contract:")
    else:
        raise AssertionError("invalid money contract must not verify active")
