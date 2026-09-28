from types import SimpleNamespace

import pytest

import main
from mt5_protocol import LookupState


class _Runtime:
    def __init__(self, state=LookupState.FOUND):
        self.state = state
        self.service = object()
        self.closed = False
        self.recovered = False
        self.client = SimpleNamespace(
            worker_pid=4321,
            session_id="worker-session",
            recover_trade_outcomes=self._recover,
            transport_snapshot=lambda: {"scheduling_policy": "test-policy", "capacity": 33},
        )

    async def _recover(self):
        self.recovered = True
        return 2

    async def start(self, timeout=10.0):
        return SimpleNamespace(
            state=self.state,
            error=None if self.state is LookupState.FOUND else "startup_failed",
            native_error=None,
        )

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_owner_is_recovered_before_execution_service_is_installed(monkeypatch):
    runtime = _Runtime()
    installed = []
    events = []

    def install(service):
        assert runtime.recovered is True
        installed.append(service)

    monkeypatch.setattr(main.listener, "install_durable_execution_service", install)
    monkeypatch.setattr(main.journal, "event", lambda *args, **kwargs: events.append((args, kwargs)))

    started = await main._start_mt5_owner(runtime)

    assert started is runtime
    assert installed == [runtime.service]
    assert events[-1][0] == ("bot", "mt5_owner_ready")
    assert events[-1][1]["retained_outcomes_recovered"] == 2
    assert events[-1][1]["transport"]["scheduling_policy"] == "test-policy"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["recovery", "installation", "evidence", "cancelled"])
async def test_startup_failure_after_spawn_always_closes_owner(monkeypatch, failure):
    import asyncio

    runtime = _Runtime()
    installed = []

    async def recover():
        if failure == "recovery":
            raise OSError("offline disk failure")
        if failure == "cancelled":
            raise asyncio.CancelledError()
        return 0

    def install(service):
        installed.append(service)
        if failure == "installation" and service is not None:
            raise OSError("offline installation failure")

    def evidence(*args, **kwargs):
        if failure == "evidence":
            raise OSError("offline evidence failure")

    runtime.client.recover_trade_outcomes = recover
    monkeypatch.setattr(main.listener, "install_durable_execution_service", install)
    monkeypatch.setattr(main.journal, "event", evidence)
    error = asyncio.CancelledError if failure == "cancelled" else OSError
    with pytest.raises(error):
        await main._start_mt5_owner(runtime)
    assert runtime.closed, "worker leaked before _active_mt5_owner assignment"
    if failure in {"installation", "evidence"}:
        assert installed[-1] is None
    else:
        assert installed == []


@pytest.mark.asyncio
async def test_failed_owner_start_is_closed_and_never_installed(monkeypatch):
    runtime = _Runtime(LookupState.UNKNOWN)
    installed = []
    monkeypatch.setattr(
        main.listener,
        "install_durable_execution_service",
        installed.append,
    )

    with pytest.raises(RuntimeError, match="startup_failed"):
        await main._start_mt5_owner(runtime)

    assert runtime.closed is True
    assert runtime.recovered is False
    assert installed == []


@pytest.mark.asyncio
async def test_owner_shutdown_uninstalls_service_before_closing(monkeypatch):
    runtime = _Runtime()
    calls = []
    monkeypatch.setattr(
        main.listener,
        "install_durable_execution_service",
        lambda service: calls.append(("install", service)),
    )

    original_close = runtime.close

    async def close():
        calls.append(("close", None))
        await original_close()

    runtime.close = close
    await main._stop_mt5_owner(runtime)

    assert calls == [("install", None), ("close", None)]
    assert runtime.closed is True


def test_production_owner_uses_durable_runtime_path(monkeypatch, tmp_path):
    monkeypatch.setattr(main.config, "BOT_EXECUTION_LEDGER_FILE", str(tmp_path / "ledger.sqlite3"))

    runtime = main._build_mt5_owner()

    assert runtime.client.store is not None
    assert runtime.client.store.path == tmp_path / "ledger.sqlite3"
    assert runtime.client.config.expected_login == main.config.MT5_LOGIN
    assert runtime.client.config.expected_server == main.config.MT5_SERVER
    assert main.config.MT5_SYMBOL in runtime.client.config.symbols
    assert runtime.client.config.trade_symbols == (main.config.MT5_SYMBOL,)
