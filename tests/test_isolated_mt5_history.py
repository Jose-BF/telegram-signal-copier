import configparser
from types import SimpleNamespace

import pytest

from tools.isolated_mt5_history import (
    frame_for_interval, immutable_json, inspect_ticks, owned_process, safe_config, validate_connection,
)


def test_startup_has_no_programs_and_disables_all_trading():
    config = configparser.ConfigParser()
    config.read_string(safe_config(123, "VantageMarkets-Demo"))
    for key in ("Enabled", "AllowLiveTrading", "AllowDllImport", "WebRequest"):
        assert config.getint("Experts", key) == 0
    assert config.getint("Experts", "Api") == 1
    assert not config.get("StartUp", "Expert")
    assert not config.get("StartUp", "Script")
    assert not config.get("Tester", "Expert")


@pytest.mark.parametrize("login,server", [(123, "Live"), ("123\nEnabled=1", "VantageMarkets-Demo")])
def test_configuration_rejects_other_servers_or_injected_login(login, server):
    with pytest.raises(ValueError):
        safe_config(login, server)


@pytest.mark.parametrize("changed", ["path", "data_path", "trade_allowed", "tradeapi_disabled",
                                    "dlls_allowed", "connected", "server", "trade_mode"])
def test_connection_fails_closed_on_mismatched_or_trade_enabled_terminal(tmp_path, changed):
    terminal = SimpleNamespace(path=str(tmp_path), data_path=str(tmp_path), trade_allowed=False,
                               tradeapi_disabled=True, dlls_allowed=False, connected=True, build=5640)
    account = SimpleNamespace(server="VantageMarkets-Demo", trade_mode=0, login=123,
                              currency="EUR", leverage=500)
    valid = validate_connection(terminal, account, tmp_path, "VantageMarkets-Demo")
    assert valid["demo"] and valid["tradeapi_disabled"] and not valid["trade_allowed"]
    assert "login" not in valid
    if changed in ("path", "data_path"):
        setattr(terminal, changed, str(tmp_path / "another"))
    elif changed == "server":
        account.server = "Other"
    elif changed == "trade_mode":
        account.trade_mode = 2
    else:
        setattr(terminal, changed, not getattr(terminal, changed))
    with pytest.raises(RuntimeError):
        validate_connection(terminal, account, tmp_path, "VantageMarkets-Demo")


def ticks(times):
    import numpy as np
    data = np.zeros(len(times), dtype=[("time", "<i8"), ("time_msc", "<i8"),
        ("bid", "<f8"), ("ask", "<f8"), ("last", "<f8"), ("volume", "<u8"),
        ("flags", "<u4"), ("volume_real", "<f8")])
    data["time_msc"] = times
    data["time"] = data["time_msc"] // 1000
    data["bid"], data["ask"] = 4000, 4000.2
    return data


def test_interval_keeps_last_millisecond_and_repeated_ticks_without_boundary_duplicates():
    import pandas as pd
    raw = ticks([0, 999, 1000, 1000, 1999, 2000])
    full = frame_for_interval(raw, 0, 2000)
    first = frame_for_interval(raw, 0, 1000)
    second = frame_for_interval(raw, 1000, 2000)
    assert full.equals(pd.concat([first, second], ignore_index=True))
    assert full["time_msc"].tolist() == [0, 999, 1000, 1000, 1999]
    assert inspect_ticks(full)["repeated_millisecond_pairs"] == 1
    assert inspect_ticks(full)["utc_offset_seconds"] is None


def test_bad_quotes_and_reversals_are_retained_and_reported():
    data = ticks([2000, 1000, 3000])
    data["ask"][1] = 0
    data["bid"][2] = float("nan")
    frame = frame_for_interval(data, 0, 4000)
    report = inspect_ticks(frame)
    assert report["rows"] == 3
    assert report["time_reversals"] == 1
    assert report["invalid_bid_ask_rows"] == 2
    assert not report["full_horizon_admitted"]


def test_no_data_and_invalid_schema_remain_distinct():
    assert frame_for_interval(None, 0, 1000) is None
    with pytest.raises(ValueError):
        frame_for_interval([{"time": 1}], 0, 1000)
    assert inspect_ticks(frame_for_interval(ticks([]), 0, 1000))["rows"] == 0


def test_raw_metadata_is_immutable_and_reproducible(tmp_path):
    p = tmp_path / "record.json"
    immutable_json(p, {"rows": 100, "clock_admitted": False})
    original = p.read_bytes()
    immutable_json(p, {"clock_admitted": False, "rows": 100})
    assert p.read_bytes() == original
    with pytest.raises(ValueError, match="Immutable"):
        immutable_json(p, {"rows": 101})


def test_updater_is_owned_only_when_both_target_and_config_match(tmp_path):
    exe = tmp_path / "private/terminal64.exe"
    updater = tmp_path / "system-cache/liveupdate/terminal64.exe"
    args = ["/update", f"/path:{exe.parent}", f"/config:{exe.parent / 'history.ini'}"]
    assert not owned_process(exe, updater, args)
    assert owned_process(exe, updater, args, include_updaters=True)
    assert not owned_process(exe, updater, args[:2], include_updaters=True)
    assert not owned_process(exe, updater, ["/update", "/path:another"], include_updaters=True)
    assert not owned_process(exe, tmp_path / "real/terminal64.exe", [], include_updaters=True)
