from types import SimpleNamespace

import executor


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
