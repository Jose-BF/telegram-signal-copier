"""Read-only historical extraction from an explicitly isolated MT5 demo copy."""

from __future__ import annotations

import argparse
import configparser
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_ini(path):
    data = Path(path).read_bytes()
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.read_string(data.decode("utf-16" if data.startswith(b"\xff\xfe") else "utf-8-sig"))
    return config


def safe_config(login, server):
    if not str(login).isdigit() or server not in {"VantageMarkets-Demo", "VantageInternational-Demo"}:
        raise ValueError("Only the declared Vantage demo histories are supported")
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read_dict({
        "Common": {"Login": str(login), "Server": server, "KeepPrivate": "0",
                   "NewsEnable": "0", "CertInstall": "0", "ProxyEnable": "0"},
        "Charts": {"ProfileLast": "HistoryReadOnly", "MaxBars": "100000", "SaveDeleted": "0"},
        "Experts": {"Enabled": "0", "AllowLiveTrading": "0", "AllowDllImport": "0",
                    "Account": "1", "Profile": "1", "Chart": "1", "Api": "1", "WebRequest": "0"},
        "Email": {"Enable": "0"}, "Notification": {"Enable": "0"},
        "StartUp": {"Expert": "", "Script": "", "Symbol": ""},
        "Tester": {"Expert": "", "UseCloud": "0"},
    })
    stream = io.StringIO()
    config.write(stream, space_around_delimiters=False)
    return stream.getvalue()


def owned_process(executable, process_exe, command_line, *, include_updaters=False):
    expected = Path(executable).resolve()
    if not process_exe:
        return False
    actual = Path(process_exe).resolve()
    if actual == expected:
        return True
    return bool(include_updaters and actual.name.lower() == "terminal64.exe"
                and actual.parent.name.lower() == "liveupdate" and "/update" in command_line
                and f"/path:{expected.parent}" in command_line
                and f"/config:{expected.parent / 'history.ini'}" in command_line)


def isolated_processes(executable, *, include_updaters=False):
    import psutil
    result = []
    for process in psutil.process_iter(["exe", "cmdline"]):
        try:
            if owned_process(executable, process.info["exe"], process.info["cmdline"] or [],
                             include_updaters=include_updaters):
                result.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def prepare_copy(source_data, source_exe, destination):
    source_data, source_exe, destination = map(lambda p: Path(p).resolve(), (source_data, source_exe, destination))
    allowed = (Path(os.environ["LOCALAPPDATA"]) / "Codex" / "MT5History").resolve()
    if destination == allowed or not destination.is_relative_to(allowed) or destination.exists():
        raise ValueError("Require a fresh isolated subdirectory under LocalAppData/Codex/MT5History")
    config = read_ini(source_data / "config/common.ini")
    server, login = config.get("Common", "Server"), config.get("Common", "Login")
    rendered = safe_config(login, server)
    paths = [source_exe, source_data / "config/common.ini", source_data / "config/accounts.dat",
             source_data / "config/servers.dat"]
    before = {str(p): digest(p) for p in paths}
    destination.mkdir(parents=True)
    (destination / "config").mkdir()
    (destination / "MQL5/Profiles/Charts/HistoryReadOnly").mkdir(parents=True)
    shutil.copy2(source_exe, destination / "terminal64.exe")
    for name in ("accounts.dat", "servers.dat"):
        shutil.copy2(source_data / "config" / name, destination / "config" / name)
    (destination / "history.ini").write_text(rendered, encoding="utf-16")
    (destination / "config/common.ini").write_text(rendered, encoding="utf-16")
    copied = []
    for server_name in ("VantageMarkets-Demo", "VantageInternational-Demo"):
        source = source_data / "bases" / server_name
        for symbol in ("XAUUSD", "EURUSD"):
            for path in sorted((source / "ticks" / symbol).glob("2026*.tkc")):
                target = destination / path.relative_to(source_data)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied.append({"source_path": str(path), "sha256": digest(path), "copy_sha256": digest(target)})
        if (source / "symbols").exists():
            shutil.copytree(source / "symbols", destination / "bases" / server_name / "symbols")
    if before != {str(p): digest(p) for p in paths} or any(r["sha256"] != r["copy_sha256"] for r in copied):
        raise RuntimeError("Source changed during copy or copied bytes mismatch")
    manifest = {"schema_version": "isolated_mt5_history_v1", "server": server,
        "terminal_dir": str(destination), "terminal_sha256": digest(destination / "terminal64.exe"),
        "source_hashes": before, "copied_native_files": copied, "profiles_or_programs_copied": False,
        "history_config_sha256": digest(destination / "history.ini")}
    (destination / "isolation.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def validate_connection(terminal, account, directory, expected_server):
    directory = Path(directory).resolve()
    if terminal is None or account is None:
        raise RuntimeError("Terminal or demo account unavailable")
    if Path(terminal.path).resolve() != directory or Path(terminal.data_path).resolve() != directory:
        raise RuntimeError("MT5 connected outside isolated directory")
    if terminal.trade_allowed or not terminal.tradeapi_disabled or terminal.dlls_allowed:
        raise RuntimeError("Read-only trading safeguards not active")
    if account.server != expected_server or account.trade_mode != 0:
        raise RuntimeError("Wrong server or non-demo account")
    if not terminal.connected:
        raise RuntimeError("Demo history connection unavailable")
    return {"server": account.server, "demo": True, "currency": account.currency,
        "leverage": account.leverage, "current_metadata_only": True,
        "account_binding_sha256": hashlib.sha256(f"{account.server}:{account.login}".encode()).hexdigest(),
        "terminal_path": str(directory), "data_path": str(directory), "build": terminal.build,
        "trade_allowed": bool(terminal.trade_allowed), "tradeapi_disabled": bool(terminal.tradeapi_disabled),
        "dlls_allowed": bool(terminal.dlls_allowed), "connected": bool(terminal.connected)}


@contextmanager
def connect_isolated(directory):
    import MetaTrader5 as mt5
    import psutil
    directory = Path(directory).resolve()
    allowed = (Path(os.environ["LOCALAPPDATA"]) / "Codex/MT5History").resolve()
    if directory == allowed or not directory.is_relative_to(allowed):
        raise ValueError("Not an approved isolated directory")
    manifest = json.loads((directory / "isolation.json").read_text(encoding="utf-8"))
    exe, config_path = directory / "terminal64.exe", directory / "history.ini"
    if digest(exe) != manifest["terminal_sha256"] or digest(config_path) != manifest["history_config_sha256"]:
        raise RuntimeError("Isolated executable or startup configuration changed")
    if isolated_processes(exe, include_updaters=True):
        raise RuntimeError("Isolated terminal already running; do not reuse unknown process ownership")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    process = None
    try:
        process = subprocess.Popen([str(exe), "/portable", f"/config:{config_path}"],
                                   cwd=directory, startupinfo=startup)
        time.sleep(3)
        if not isolated_processes(exe):
            raise RuntimeError("Isolated terminal did not remain running")
        if not mt5.initialize(str(exe), portable=True, timeout=20000):
            raise RuntimeError(f"Isolated MT5 initialization failed with code {mt5.last_error()[0]}")
        info = validate_connection(mt5.terminal_info(), mt5.account_info(), directory, manifest["server"])
        info["mt5_python_version"] = mt5.__version__
        yield mt5, info
    finally:
        mt5.shutdown()
        if process is not None:
            for owned in isolated_processes(exe, include_updaters=True):
                try:
                    owned.terminate()
                    owned.wait(timeout=10)
                except psutil.NoSuchProcess:
                    pass
        if isolated_processes(exe, include_updaters=True):
            raise RuntimeError("Owned isolated terminal remains running")


def probe(directory, days):
    report = {"schema_version": "isolated_mt5_probe_v1", "samples": [], "engine_admitted": False}
    with connect_isolated(directory) as (mt5, binding):
        report["binding"] = binding
        print(json.dumps({"event": "read_only_connection_verified", **binding}), flush=True)
        for symbol in ("XAUUSD", "EURUSD"):
            if not mt5.symbol_select(symbol, True):
                raise RuntimeError("Symbol selection failed")
            for day in days:
                start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(hours=12)
                ticks = mt5.copy_ticks_range(symbol, start, start + timedelta(minutes=5), mt5.COPY_TICKS_ALL)
                row = {"symbol": symbol, "source_epoch_date": day, "rows": len(ticks) if ticks is not None else None,
                       "error_code": mt5.last_error()[0], "clock_admitted": False}
                if ticks is not None and len(ticks):
                    row.update(first_source_time_msc=int(ticks[0]["time_msc"]),
                               last_source_time_msc=int(ticks[-1]["time_msc"]),
                               first_bid=float(ticks[0]["bid"]), first_ask=float(ticks[0]["ask"]))
                report["samples"].append(row)
                print(json.dumps(row), flush=True)
    report["isolated_terminal_stopped"] = True
    return report


def frame_for_interval(raw, start_msc, end_msc):
    import pandas as pd
    if raw is None:
        return None
    frame = pd.DataFrame(raw)
    required = {"time", "time_msc", "bid", "ask", "last", "volume", "flags", "volume_real"}
    if not required <= set(frame.columns):
        raise ValueError("Raw MT5 tick schema missing required fields")
    return frame.loc[(frame["time_msc"] >= start_msc) & (frame["time_msc"] < end_msc)].reset_index(drop=True)


def inspect_ticks(frame):
    import numpy as np
    times = frame["time_msc"].to_numpy(dtype="int64")
    bid, ask = frame["bid"].to_numpy(), frame["ask"].to_numpy()
    gaps = np.diff(times)
    return {"rows": len(frame),
        "first_source_time_msc": int(times[0]) if len(times) else None,
        "last_source_time_msc": int(times[-1]) if len(times) else None,
        "time_reversals": int((gaps < 0).sum()),
        "repeated_millisecond_pairs": int((gaps == 0).sum()),
        "second_millisecond_mismatches": int((frame["time"].to_numpy() != times // 1000).sum()),
        "invalid_bid_ask_rows": int((~np.isfinite(bid) | ~np.isfinite(ask) | (bid <= 0) | (ask <= 0) | (ask < bid)).sum()),
        "max_internal_gap_ms": int(gaps.max()) if len(gaps) else None,
        "source_time_basis": "raw_mt5_epoch_not_yet_admitted_as_utc", "utc_offset_seconds": None,
        "full_horizon_admitted": False}


def immutable_json(path, value):
    path = Path(path)
    data = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("Immutable extraction artifact conflict")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def extract(directory, output, start_day, end_exclusive_day, max_wall_seconds):
    import pandas as pd
    directory, output = Path(directory).resolve(), Path(output).resolve()
    if output.is_relative_to(directory) or directory.is_relative_to(output):
        raise ValueError("Separate public raw-data archive from private terminal state")
    start = datetime.fromisoformat(start_day).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(end_exclusive_day).replace(tzinfo=timezone.utc)
    if not 0 < (end - start).days <= 366 or start.time() != end.time() or start.hour != 0:
        raise ValueError("Require a bounded calendar interval of at most 366 days")
    if not 60 <= max_wall_seconds <= 3600:
        raise ValueError("Extraction wall budget must be between 60 and 3600 seconds")
    started = time.monotonic()
    manifest = json.loads((directory / "isolation.json").read_text(encoding="utf-8"))
    contract = {"schema_version": "raw_isolated_mt5_history_v1", "server": manifest["server"],
        "source_epoch_start": start.isoformat(), "source_epoch_end_exclusive": end.isoformat(),
        "symbols": ["XAUUSD", "EURUSD"], "clock_admitted": False, "engine_dataset_ready": False,
        "provider_prices_used": False, "timezone_conversion_applied": False,
        "day_verification": "full_day_vs_two_half_days_all_raw_fields",
        "isolation_manifest_sha256": digest(directory / "isolation.json"),
        "implementation_sha256": digest(__file__)}
    immutable_json(output / "contract.json", contract)
    with connect_isolated(directory) as (mt5, binding):
        immutable_json(output / "binding.json", binding)
        print(json.dumps({"event": "extract_connected", "server": binding["server"],
                          "trade_allowed": binding["trade_allowed"], "tradeapi_disabled": binding["tradeapi_disabled"]}), flush=True)
        for symbol in contract["symbols"]:
            if not mt5.symbol_select(symbol, True):
                raise RuntimeError("Symbol unavailable on declared server")
            info = mt5.symbol_info(symbol)
            fields = ("name", "path", "currency_base", "currency_profit", "currency_margin", "digits", "point",
                      "trade_contract_size", "trade_tick_size", "trade_calc_mode", "volume_min", "volume_max", "volume_step")
            immutable_json(output / symbol / "current_symbol_metadata.json",
                           {"historical_contract_verified": False, **{key: getattr(info, key) for key in fields}})
            day = start
            while day < end:
                label = day.date().isoformat()
                metadata = output / symbol / f"{label}.json"
                if metadata.exists():
                    previous = json.loads(metadata.read_text(encoding="utf-8"))
                    if previous.get("artifact"):
                        if digest(output / symbol / previous["artifact"]) != previous["sha256"]:
                            raise RuntimeError("Existing raw price artifact changed")
                    day += timedelta(days=1)
                    continue
                if time.monotonic() - started >= max_wall_seconds:
                    print(json.dumps({"event": "budget_checkpoint", "next_symbol": symbol, "next_day": label}), flush=True)
                    return {"status": "budget_checkpoint", "next_symbol": symbol, "next_day": label}
                validate_connection(mt5.terminal_info(), mt5.account_info(), directory, manifest["server"])
                stop, middle = day + timedelta(days=1), day + timedelta(hours=12)
                frames, errors = [], []
                for left, right in ((day, stop), (day, middle), (middle, stop)):
                    # Query the upper boundary inclusively, then partition by
                    # milliseconds. This retains the whole last second and
                    # prevents double-counting a tick exactly at noon/midnight.
                    raw = mt5.copy_ticks_range(symbol, left, right, mt5.COPY_TICKS_ALL)
                    errors.append(mt5.last_error()[0])
                    frames.append(frame_for_interval(raw, int(left.timestamp() * 1000), int(right.timestamp() * 1000)))
                full, first, second = frames
                row = {"symbol": symbol, "server": binding["server"], "source_epoch_day": label,
                       "query_error_codes": errors, "engine_admitted": False, "artifact": None,
                       "clock_admitted": False, "extracted_at_utc": datetime.now(timezone.utc).isoformat()}
                # A no-history result remains explicit, never a fabricated
                # trading-session closure. Other errors block consistency.
                read_ok = all(code in (1, -4) for code in errors)
                if full is None or full.empty:
                    row.update(status="no_ticks_returned" if read_ok else "query_error", rows=0,
                               reads_consistent=False, session_closed_proved=False)
                else:
                    halves = pd.concat([f if f is not None else full.iloc[:0] for f in (first, second)], ignore_index=True)
                    consistent = read_ok and full.equals(halves)
                    row.update(inspect_ticks(full), reads_consistent=consistent,
                               status="raw_reads_consistent" if consistent else "raw_reads_disagree")
                    artifact = output / symbol / f"{label}.parquet"
                    if artifact.exists():
                        raise RuntimeError("Unmanifested raw artifact requires explicit recovery")
                    full.to_parquet(artifact, index=False, compression="zstd")
                    row.update(artifact=artifact.name, sha256=digest(artifact), bytes=artifact.stat().st_size)
                immutable_json(metadata, row)
                print(json.dumps({"event": "day_saved", "symbol": symbol, "day": label,
                                  "rows": row["rows"], "status": row["status"]}), flush=True)
                day = stop
    return {"status": "finished", "isolated_terminal_stopped": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--source-data", required=True, type=Path)
    prepare.add_argument("--source-exe", required=True, type=Path)
    prepare.add_argument("--destination", required=True, type=Path)
    check = sub.add_parser("probe")
    check.add_argument("--terminal-dir", required=True, type=Path)
    check.add_argument("--days", nargs="+", required=True)
    check.add_argument("--report", required=True, type=Path)
    download = sub.add_parser("extract")
    download.add_argument("--terminal-dir", required=True, type=Path)
    download.add_argument("--output", required=True, type=Path)
    download.add_argument("--start", required=True)
    download.add_argument("--end-exclusive", required=True)
    download.add_argument("--max-wall-seconds", type=int, default=1800)
    args = parser.parse_args()
    if args.mode == "prepare":
        result = prepare_copy(args.source_data, args.source_exe, args.destination)
        print(json.dumps({"prepared": result["terminal_dir"], "server": result["server"],
                          "copied_native_files": len(result["copied_native_files"])}))
    elif args.mode == "probe":
        if args.report.exists():
            raise ValueError("Probe report already exists")
        result = probe(args.terminal_dir, args.days)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
        print(json.dumps({"report": str(args.report), "isolated_terminal_stopped": True}))
    else:
        result = extract(args.terminal_dir, args.output, args.start, args.end_exclusive, args.max_wall_seconds)
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
