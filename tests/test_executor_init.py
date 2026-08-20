from types import SimpleNamespace

import executor
import pytest


@pytest.fixture(autouse=True)
def _isolate_journal(monkeypatch):
    monkeypatch.setattr(executor, "_emit_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(executor, "_symbol_point_cache", None)
    monkeypatch.setattr(
        executor.mt5,
        "symbol_info",
        lambda symbol: SimpleNamespace(point=0.01),
    )


def _account(login=1, server="TestServer-Demo"):
    return SimpleNamespace(
        login=login,
        server=server,
        name="Test Account",
        balance=10000.0,
        currency="USD",
    )


def test_init_skips_login_when_terminal_already_on_target_account(monkeypatch):
    login_calls = []

    monkeypatch.setattr(executor.mt5, "initialize", lambda: True)
    monkeypatch.setattr(executor.mt5, "account_info", lambda: _account())
    monkeypatch.setattr(
        executor.mt5, "login",
        lambda *args, **kwargs: login_calls.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(executor.mt5, "symbol_select", lambda symbol, enable: True)

    assert executor.init() is True
    assert login_calls == []
    assert executor._symbol_point_cache == 0.01


def test_init_logs_in_when_terminal_is_on_different_account(monkeypatch):
    login_calls = []
    accounts = [_account(login=999, server="OtherServer"), _account()]

    monkeypatch.setattr(executor.mt5, "initialize", lambda: True)
    monkeypatch.setattr(executor.mt5, "account_info", lambda: accounts.pop(0))
    monkeypatch.setattr(
        executor.mt5,
        "login",
        lambda *args, **kwargs: login_calls.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(executor.mt5, "symbol_select", lambda symbol, enable: True)

    assert executor.init() is True
    assert len(login_calls) == 1


def test_init_journals_connected_account_evidence(monkeypatch):
    events = []

    monkeypatch.setattr(executor.mt5, "initialize", lambda: True)
    monkeypatch.setattr(executor.mt5, "account_info", lambda: _account())
    monkeypatch.setattr(executor.mt5, "symbol_select", lambda symbol, enable: True)
    monkeypatch.setattr(
        executor,
        "_emit_event",
        lambda sig_id, ev, **ctx: events.append((sig_id, ev, ctx)),
    )

    assert executor.init() is True
    assert events == [(
        "bot",
        "mt5_account_connected",
        {
            "login": 1,
            "server": "TestServer-Demo",
            "name": "Test Account",
            "currency": "USD",
            "balance": 10000.0,
            "equity": None,
        },
    )]
