"""Compare native-conditioned Gold paths under strict and retrospective FX coverage."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from research.causal_replay import compile_signals, make_path, time_ns, utc
from research.conditioned_management import replay_management
from research.risk_trajectory import RiskSpec, reconstruct_risk
from tools.audit_tp_quote_corridors import quote_corridor
from tools.audit_week_conditioned_controls import native_events
from tools.compare_risk_trajectories import paired_quotes
from tools.probe_canal1_incremental_window import digest, load_day
from tools.run_causal_controls import STICKERS, encode, save
from tools.run_week_causal_controls import policies


MAX_CASES = 4
MAX_SECONDS = 600
OFFSET_SECONDS = 10_800
FX_INTERVAL_MS = 60_000
PROJECT = Path(__file__).resolve().parents[1]


def verify_case_inputs(model, native, *, model_path, native_path, root):
    """Use an archived window as a bound scope, not as a current model result."""
    sources = model["sources"]
    raw_path = root / "raw_week_20260914_19_bg_v1.json"
    if digest(raw_path) != sources["raw_sha256"]:
        raise ValueError("archived raw message source changed")
    day = utc(model["start_utc"]).date().isoformat()
    for symbol in ("XAUUSD", "EURUSD"):
        stem = root / symbol / day
        if (digest(stem.with_suffix(".json")) != sources[symbol]["metadata_sha256"]
                or digest(stem.with_suffix(".parquet")) != sources[symbol]["parquet_sha256"]):
            raise ValueError(f"archived {symbol} tape source changed")
    for name, sha in native["inputs_sha256"].items():
        path = Path(name)
        if digest(path if path.is_absolute() else PROJECT / path) != sha:
            raise ValueError(f"native risk source changed: {name}")
    if (native["source_clock_offset_seconds_hypothesis"] != OFFSET_SECONDS
            or model["clock_offset_seconds_hypothesis"] != OFFSET_SECONDS
            or model["native_start_control"]["open_position_count"] != 0):
        raise ValueError("native/model start or clock evidence changed")
    native_inputs = native["inputs_sha256"]
    for filename, field in (("native_week_reconciled.json", "reconciled_sha256"),
                            ("native_week_deals_20260919.json", "deals_sha256")):
        matching = [sha for name, sha in native_inputs.items()
                    if Path(name).name == filename]
        if matching != [model["native_start_control"][field]]:
            raise ValueError("archived start ledger not bound to native risk")
    implementation_match = {
        name: digest(PROJECT / name) == sha
        for name, sha in sources["implementation_sha256"].items()
    }
    return {"archived_model_sha256": digest(model_path),
            "native_risk_sha256": digest(native_path),
            "raw_sha256": sources["raw_sha256"],
            "archived_runner_matches_current": (
                digest(PROJECT / "tools/probe_canal1_incremental_window.py")
                == sources["runner_sha256"]),
            "archived_implementation_matches_current": all(implementation_match.values()),
            "changed_archived_implementation_files": sorted(
                name for name, matches in implementation_match.items() if not matches),
            "archived_model_outputs_not_used": True}


def bracketed_quotes(market, conversion, events):
    """Attach only the next FX timestamp; the marked FX price remains prior."""
    market_pair = (market[0], np.column_stack(market[1:]))
    conversion_pair = (conversion[0], np.column_stack(conversion[1:]))
    quotes = paired_quotes(market_pair, conversion_pair, events,
                           start=min(event.at for event in events),
                           end=max(event.at for event in events))
    fx_times = conversion[0]
    result = []
    for quote in quotes:
        next_index = int(np.searchsorted(fx_times, time_ns(quote.at), side="right"))
        next_at = (pd.Timestamp(int(fx_times[next_index]), tz="UTC").to_pydatetime()
                   if next_index < len(fx_times) else None)
        result.append(replace(quote, conversion_next_at=next_at))
    return result


def path_difference(observed, simulated):
    """Keep the full denominator but emit only compact, reproducible diagnostics."""
    left, right = observed["samples"], simulated["samples"]
    if len(left) != len(right):
        raise ValueError("different common-grid sample counts")
    counts = Counter()
    first = None
    max_total_difference = Decimal(0)
    fields = ("total", "floating", "realized", "long_volume", "short_volume",
              "open_count", "positions")
    for actual, model in zip(left, right, strict=True):
        if (actual["at"], actual["ordinal"]) != (model["at"], model["ordinal"]):
            raise ValueError("different common-grid sample clocks")
        if actual["total"] is None or model["total"] is None:
            counts["unknown"] += 1
            continue
        counts["comparable"] += 1
        changed = [field for field in fields if actual[field] != model[field]]
        for field in changed:
            counts[field] += 1
        if changed:
            counts["any"] += 1
            if first is None:
                first = {"at": actual["at"], "ordinal": actual["ordinal"],
                         "fields": changed, "native_total_eur": actual["total"],
                         "model_total_eur": model["total"]}
        max_total_difference = max(max_total_difference,
                                   abs(actual["total"] - model["total"]))
    return {"sample_count": len(left), "counts": dict(counts),
            "first_divergence": first,
            "max_abs_total_difference_eur": max_total_difference,
            "native_sample_sha256": hashlib.sha256(encode(left)).hexdigest(),
            "model_sample_sha256": hashlib.sha256(encode(right)).hexdigest()}


def exit_timing(observed, simulated):
    """Pair only identical logical slots; never select the nearest broker fill."""
    def exits(events):
        rows = [event for event in events if event.kind == "exit"]
        indexed = {event.slot: event for event in rows}
        if len(indexed) != len(rows):
            raise ValueError("multiple exits for one logical slot")
        return indexed

    native, model = exits(observed), exits(simulated)
    if native.keys() != model.keys():
        raise ValueError("native/model exit slot sets differ")
    result = []
    for slot in sorted(native):
        actual, replayed = native[slot], model[slot]
        if actual.volume != replayed.volume or actual.direction != replayed.direction:
            raise ValueError("native/model exit slot contract differs")
        result.append({"slot": slot, "native_exit_at": actual.at,
                       "model_exit_at": replayed.at,
                       "model_minus_native_ms": int((replayed.at - actual.at).total_seconds() * 1000),
                       "native_exit_price": actual.price,
                       "model_exit_price": replayed.price,
                       "native_booked_eur": actual.money,
                       "model_booked_eur": replayed.money,
                       "model_mechanism": replayed.mechanism})
    return result


def audit_case(model_path, signal_id, *, root, native_path, money_path,
               broker_path, started):
    if time.monotonic() - started > MAX_SECONDS:
        raise TimeoutError("FX sensitivity time budget exceeded")
    model = json.loads(model_path.read_text(encoding="utf-8"))
    native = json.loads(native_path.read_text(encoding="utf-8"))
    money = json.loads(money_path.read_text(encoding="utf-8"))
    broker = json.loads(broker_path.read_text(encoding="utf-8"))
    sources = verify_case_inputs(model, native, model_path=model_path,
                                 native_path=native_path, root=root)
    if (native["inputs_sha256"].get(str(money_path.relative_to(Path(__file__).resolve().parents[1])))
            != digest(money_path)):
        raise ValueError("native money anchor not bound to risk source")
    if (native["inputs_sha256"].get(str(broker_path.relative_to(Path(__file__).resolve().parents[1])))
            != digest(broker_path)):
        raise ValueError("broker money contract not bound to risk source")
    if (model["clock_offset_seconds_hypothesis"] != OFFSET_SECONDS
            or model["status"] != "blocked"
            or signal_id not in model["other_signal_ids"]
            or broker["account"]["currency"] != "EUR"
            or broker["conversion"]["max_quote_age_ms"] != 5_000
            or broker["conversion"]["max_quote_interval_ms"] != FX_INTERVAL_MS):
        raise ValueError("case identity or predeclared FX contract mismatch")
    start, cutoff = utc(model["start_utc"]), utc(model["cutoff_utc"])
    day = start.date().isoformat()
    market, market_proof = load_day(root, "XAUUSD", day,
                                    start_ns=time_ns(start), cutoff_ns=time_ns(cutoff),
                                    offset_seconds=OFFSET_SECONDS)
    conversion, fx_proof = load_day(root, "EURUSD", day,
                                    start_ns=time_ns(start), cutoff_ns=time_ns(cutoff),
                                    offset_seconds=OFFSET_SECONDS,
                                    initial_padding_ns=FX_INTERVAL_MS * 1_000_000)
    if (market_proof["parquet_sha256"] != model["sources"]["XAUUSD"]["parquet_sha256"]
            or fx_proof["parquet_sha256"] != model["sources"]["EURUSD"]["parquet_sha256"]):
        raise ValueError("case tape differs from bound model")
    raw_path = root / "raw_week_20260914_19_bg_v1.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    compiled, _ = compile_signals(raw["rows"], start=start, cutoff=cutoff,
                                  sticker_directions=STICKERS, max_entry_age_s=120)
    gold = {signal.signal_id: signal for signal in compiled if signal.channel == "canal2"}
    if list(gold) != model["other_signal_ids"]:
        raise ValueError("Gold causal signal universe changed")
    signal = gold[signal_id]
    positions = [row for row in money["positions"] if row["signal_id"] == signal_id]
    entries, exits = native_events(positions, direction=signal.direction,
                                  start=start, cutoff=cutoff)
    if not entries or len(entries) != len(exits):
        raise ValueError("conditioned native fill scope incomplete")
    genome = policies()["canal2"]
    if model["policy_fingerprints"]["canal2"] != genome.fingerprint:
        raise ValueError("policy fingerprint changed")
    common = dict(market=market, conversion=conversion, cutoff=cutoff,
                  contract_size=float(broker["instrument"]["contract_size"]),
                  currency_digits=broker["account"]["currency_digits"],
                  max_fx_age_ms=broker["conversion"]["max_quote_age_ms"],
                  market_sha256=market_proof["parquet_sha256"],
                  conversion_sha256=fx_proof["parquet_sha256"], tape_start=start)
    strict, strict_events = replay_management(make_path(signal, genome, **common),
                                               genome, entries)
    historical, simulated = replay_management(
        make_path(signal, genome, max_fx_interval_ms=FX_INTERVAL_MS, **common),
        genome, entries)
    row = {"signal_id": signal_id, "channel": "canal2",
           "strict_status": strict["status"], "strict_blockers": strict["blockers"],
           "strict_model_events_available": strict_events is not None,
           "retrospective_status": historical["status"],
           "retrospective_blockers": historical["blockers"],
           "engine_mismatches": historical["engine_mismatches"],
           "native_entry_count": len(entries), "sources": sources,
           "money_sha256": digest(money_path), "broker_sha256": digest(broker_path)}
    if strict_events is not None or simulated is None or any(historical["engine_mismatches"].values()):
        row["status"] = "blocked_unexpected_conditioned_result"
        return row
    observed = (*entries, *exits)
    all_events = (*observed, *simulated)
    quotes = bracketed_quotes(market, conversion, all_events)
    spec = RiskSpec("EUR", broker["account"]["currency_digits"],
                    Decimal(str(broker["instrument"]["contract_size"])),
                    broker["conversion"]["orientation"],
                    broker["conversion"]["max_quote_age_ms"], 5_000)
    native_path_risk = reconstruct_risk(observed, quotes, spec=spec,
                                        grid_events=simulated,
                                        retrospective_fx_interval_ms=FX_INTERVAL_MS)
    model_path_risk = reconstruct_risk(simulated, quotes, spec=spec,
                                       grid_events=observed,
                                       retrospective_fx_interval_ms=FX_INTERVAL_MS)
    frozen = {basket["signal_id"]: basket for basket in native["baskets"]}[signal_id]
    if frozen["direct_clock_anchor_for_all_event_days"] is not True:
        raise ValueError("native exit lacks direct day clock anchor")
    booked = sum((Decimal(str(position["actual_net_eur"])) for position in positions),
                 Decimal(0))
    row["risk_gate_evidence"] = {
        "booked_native_net_eur": booked,
        "common_grid_native_net_eur": (native_path_risk["metrics"] or {}).get("final_net"),
        "frozen_native_net_eur": (frozen["path"]["metrics"] or {}).get("final_net"),
        "common_grid_model_net_eur": (model_path_risk["metrics"] or {}).get("final_net"),
        "native_blockers": native_path_risk["blockers"],
        "model_blockers": model_path_risk["blockers"],
    }
    if (native_path_risk["blockers"] or model_path_risk["blockers"]
            or native_path_risk["metrics"] is None or model_path_risk["metrics"] is None
            or native_path_risk["metrics"]["final_net"] != booked
            or Decimal(str(frozen["path"]["metrics"]["final_net"])) != booked):
        row.update(status="blocked_incomplete_risk_path",
                   risk_blockers=sorted(set(native_path_risk["blockers"]
                                            + model_path_risk["blockers"])))
        return row
    flag_frame = pd.read_parquet(root / "XAUUSD" / f"{day}.parquet",
                                 columns=["time_msc", "flags"])
    flag_times = (flag_frame.time_msc.to_numpy(dtype=np.int64)
                  - OFFSET_SECONDS * 1000) * 1_000_000
    selected = ((flag_times >= time_ns(start)) & (flag_times < time_ns(cutoff)))
    if not np.array_equal(flag_times[selected], market[0]):
        raise ValueError("quote flags differ from priced source tape")
    flags = flag_frame["flags"].to_numpy(dtype=np.uint32)[selected]
    timed_exits = exit_timing(observed, simulated)
    for item in timed_exits:
        item["terminal_quote_corridor"] = quote_corridor(
            market[0], market[1], market[2], flags,
            model_at=item["model_exit_at"], native_at=item["native_exit_at"],
            direction=signal.direction, target=item["model_exit_price"])
    row.update(status="retrospective_path_compared",
               native_metrics=native_path_risk["metrics"],
               model_metrics=model_path_risk["metrics"],
               frozen_native_metrics=frozen["path"]["metrics"],
               native_bracketed_samples=native_path_risk["retrospective_fx_bracketed_samples"],
               model_bracketed_samples=model_path_risk["retrospective_fx_bracketed_samples"],
               exit_timing=timed_exits,
               path=path_difference(native_path_risk, model_path_risk))
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", nargs=2, action="append", metavar=("MODEL", "SIGNAL"),
                        required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--money", type=Path, required=True)
    parser.add_argument("--broker", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or len(args.case) != MAX_CASES:
        raise ValueError("immutable output or four-case budget invalid")
    if len({signal for _, signal in args.case}) != MAX_CASES:
        raise ValueError("duplicate conditioned signal")
    root = args.root.resolve()
    native, money, broker = (path.resolve() for path in
                             (args.native, args.money, args.broker))
    started = time.monotonic()
    sources = {name: digest(Path(__file__).resolve().parents[1] / name) for name in (
        "tools/audit_conditioned_fx_path_sensitivity.py",
        "research/risk_trajectory.py", "research/causal_replay.py",
        "research/conditioned_management.py", "tools/audit_week_conditioned_controls.py")}
    sources["tools/audit_tp_quote_corridors.py"] = digest(
        PROJECT / "tools/audit_tp_quote_corridors.py")
    rows = [audit_case(Path(model).resolve(), signal, root=root,
                       native_path=native, money_path=money, broker_path=broker,
                       started=started) for model, signal in args.case]
    if any(digest(Path(__file__).resolve().parents[1] / name) != sha
           for name, sha in sources.items()):
        raise ValueError("audit source changed during run")
    report = {"contract": "conditioned_fx_interval_path_sensitivity_v1",
              "status": "diagnostic_only", "full_live_parity_verified": False,
              "entry_decisions_verified": False, "retrospective_fx_interval_ms": FX_INTERVAL_MS,
              "strict_causal_fx_age_ms": 5_000, "cases": rows,
              "statuses": dict(Counter(row["status"] for row in rows)),
              "sources": sources,
              "limitations": ["Native entries fixed; shared transport and entry decisions not compared.",
                              "Future FX timestamp is used for retrospective coverage, never future price.",
                              "Common retained XAUUSD ticks and event boundaries, not broker account equity.",
                              "Historical diagnostic, not out-of-sample strategy validation or live policy."],
              "elapsed_seconds": time.monotonic() - started}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save(args.output, report)
    print({"output": str(args.output), "statuses": report["statuses"]})


if __name__ == "__main__":
    main()
