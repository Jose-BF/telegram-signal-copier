"""Condition the frozen weekly Gold policy on native entries, then compare exits."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import time

import numpy as np

from research.causal_comparison import SequenceEvent, compare_sequences
from research.causal_replay import compile_signals, make_path, time_ns, utc
from research.conditioned_management import replay_management
from research.observed_tp_receipts import tp_receipt_state
from research.risk_trajectory import RiskSpec, compare_risk
from tools.compare_risk_trajectories import _summary, paired_quotes
from tools.probe_canal1_incremental_window import digest, load_day
from tools.run_causal_controls import STICKERS
from tools.run_week_causal_controls import policies


MAX_WINDOWS = 4
MAX_ROWS = 24
MAX_SECONDS = 600
OFFSET_SECONDS = 10_800


def native_events(positions, *, direction, start, cutoff):
    """Build entry-only conditioning and observed exits from exact money rows."""
    if not positions or len(positions) > 5:
        raise ValueError("native leg count outside conditioned budget")
    positions = sorted(positions, key=lambda row: row["entry_msc"])
    if len({row["entry_msc"] for row in positions}) != len(positions):
        raise ValueError("native entry ordinal ambiguous")
    entries, exits = [], []
    for slot, position in enumerate(positions, 1):
        if (position["direction"] != direction
                or Decimal(str(position["costs_eur"])) != 0):
            raise ValueError("native direction or costs unsupported")
        opened = datetime.fromtimestamp(
            position["entry_msc"] / 1000 - OFFSET_SECONDS, timezone.utc)
        closed = datetime.fromtimestamp(
            position["exit_msc"] / 1000 - OFFSET_SECONDS, timezone.utc)
        if not start < opened < closed < cutoff:
            raise ValueError("native fill crosses conditioned window")
        entries.append(SequenceEvent(slot, "entry", opened, direction,
                                     Decimal(str(position["entry_price"])),
                                     Decimal(str(position["volume"])),
                                     Decimal(0), "observed_native_fill"))
        exits.append(SequenceEvent(slot, "exit", closed, direction,
                                   Decimal(str(position["exit_price"])),
                                   Decimal(str(position["volume"])),
                                   Decimal(str(position["actual_net_eur"])),
                                   "observed_native_exit"))
    return tuple(entries), tuple(exits)


def require_native_risk_metrics(observed, frozen):
    metrics, archived = observed.get("metrics"), frozen.get("path", {}).get("metrics")
    if (metrics is None or archived is None
            or any(Decimal(str(metrics[name])) != Decimal(str(archived[name]))
                   for name in ("final_net", "max_drawdown"))):
        raise ValueError("conditioned observed risk differs from frozen native path")


def receipt_states(signal_id, positions, simulated, timeline_by_ticket):
    """Classify model TP exits using only client responses known at that time."""
    ordered = sorted(positions, key=lambda row: row["entry_msc"])
    states = []
    for event in simulated:
        if event.kind != "exit":
            continue
        row = {"slot": event.slot, "model_exit_at": event.at.isoformat()}
        if event.mechanism != "per_leg_target":
            row["status"] = "not_target_exit"
        else:
            position = ordered[event.slot - 1]
            leg = timeline_by_ticket.get(position["position_id"])
            if leg is None:
                row["status"] = "missing_receipt_trace"
            else:
                if (leg["signal_id"] != signal_id or leg["leg_index"] != event.slot - 1
                        or leg["native_position_id"] != position["position_id"]
                        or Decimal(str(leg["target_level"])) != event.price
                        or Decimal(str(position["exit_price"])) != event.price):
                    raise ValueError("conditioned TP receipt identity or level differs")
                if leg["initial_order_requested_tp"] is not None:
                    row["status"] = "initial_tp_acceptance_unproven"
                else:
                    at_msc = time_ns(event.at) // 1_000_000
                    state = tp_receipt_state(leg, at_msc)
                    row.update(status=state["target_status"],
                               client_confirmed_tp=state["confirmed_tp"],
                               inflight_attempt_ids=state["inflight_attempt_ids"],
                               first_target_accepted_minus_model_ms=(
                                   leg["first_target_accepted_response_utc_msc"] - at_msc
                                   if leg["first_target_accepted_response_utc_msc"] is not None
                                   else None))
        states.append(row)
    return states


def audit_window(model, tick, money, broker, raw, native_risk, root, *, started,
                 timeline_by_ticket=None):
    if (tick.get("contract") != "incremental_common_tick_path_comparison_v1"
            or tick.get("status") != "diagnostic_only"
            or tick["sources"]["model_sha256"] != model["_input_sha256"]
            or model["sources"]["raw_sha256"] != raw["_input_sha256"]
            or tick["sources"]["money_sha256"] != money["_input_sha256"]
            or tick["sources"]["native_sha256"] != native_risk["_input_sha256"]
            or model["clock_offset_seconds_hypothesis"] != OFFSET_SECONDS
            or broker["account"]["currency"] != "EUR"
            or money["account_currency"] != "EUR"):
        raise ValueError("conditioned window sources not bound")
    start, cutoff = utc(model["start_utc"]), utc(model["cutoff_utc"])
    if (tick["start_utc"] != model["start_utc"]
            or tick["cutoff_utc"] != model["cutoff_utc"]
            or start.date() != cutoff.date() or not start < cutoff
            or not utc(raw["start_utc"]) <= start < cutoff <= utc(raw["end_utc"])):
        raise ValueError("conditioned window clock mismatch")
    day = start.date().isoformat()
    market, market_proof = load_day(
        root, "XAUUSD", day, start_ns=time_ns(start),
        cutoff_ns=time_ns(cutoff), offset_seconds=OFFSET_SECONDS)
    conversion, fx_proof = load_day(
        root, "EURUSD", day, start_ns=time_ns(start),
        cutoff_ns=time_ns(cutoff), offset_seconds=OFFSET_SECONDS,
        initial_padding_ns=5_000_000_000)
    if (model["sources"]["XAUUSD"]["parquet_sha256"] != market_proof["parquet_sha256"]
            or model["sources"]["EURUSD"]["parquet_sha256"] != fx_proof["parquet_sha256"]):
        raise ValueError("conditioned market tape differs from shared replay")
    compiled, _ = compile_signals(
        raw["rows"], start=start, cutoff=cutoff,
        sticker_directions=STICKERS, max_entry_age_s=120)
    gold = {signal.signal_id: signal for signal in compiled if signal.channel == "canal2"}
    if list(gold) != model["other_signal_ids"]:
        raise ValueError("conditioned Gold causal universe changed")
    positions = defaultdict(list)
    for position in money["positions"]:
        positions[position["signal_id"]].append(position)
    frozen_by_signal = {row["signal_id"]: row for row in native_risk["baskets"]}
    if len(frozen_by_signal) != len(native_risk["baskets"]):
        raise ValueError("duplicate frozen native risk identity")
    genome = policies()["canal2"]
    if model["policy_fingerprints"]["canal2"] != genome.fingerprint:
        raise ValueError("conditioned Gold policy differs from shared replay")
    spec = RiskSpec(
        "EUR", broker["account"]["currency_digits"],
        Decimal(str(broker["instrument"]["contract_size"])),
        broker["conversion"]["orientation"],
        broker["conversion"]["max_quote_age_ms"], 5_000)
    market_pair = (market[0], np.column_stack(market[1:]))
    conversion_pair = (conversion[0], np.column_stack(conversion[1:]))
    rows = []
    for basket in tick["rows"]:
        if time.monotonic() - started > MAX_SECONDS:
            raise TimeoutError("conditioned case budget exhausted")
        row = {"channel": basket["channel"], "signal_id": basket["signal_id"],
               "tick_status": basket["status"]}
        if basket["status"] not in {"path_discrepant", "same_grid_fields_equal"}:
            row.update(status="blocked_prior_tick_gate", blockers=[basket["status"]])
        elif basket["channel"] != "canal2":
            row.update(status="blocked_incremental_canal1_management",
                       blockers=["isolated_policy_omits_dynamic_canal1_management"])
        else:
            try:
                signal = gold[basket["signal_id"]]
                entries, exits = native_events(
                    positions[basket["signal_id"]], direction=signal.direction,
                    start=start, cutoff=cutoff)
                path = make_path(
                    signal, genome, market=market, conversion=conversion,
                    cutoff=cutoff,
                    contract_size=float(broker["instrument"]["contract_size"]),
                    currency_digits=broker["account"]["currency_digits"],
                    max_fx_age_ms=broker["conversion"]["max_quote_age_ms"],
                    market_sha256=market_proof["parquet_sha256"],
                    conversion_sha256=fx_proof["parquet_sha256"],
                    tape_start=start)
                management, simulated = replay_management(path, genome, entries)
                row.update(native_entry_count=len(entries),
                           management_status=management["status"],
                           management_blockers=management["blockers"],
                           engine_mismatches=management["engine_mismatches"],
                           declared_policy_fingerprint=genome.fingerprint,
                           conditioned_policy_fingerprint=management[
                               "conditioned_policy"]["source_strategy_fingerprint"])
                if simulated is None:
                    row.update(status="blocked_conditioned_management",
                               blockers=management["blockers"])
                else:
                    observed = (*entries, *exits)
                    combined = (*observed, *simulated)
                    quotes = paired_quotes(
                        market_pair, conversion_pair, combined,
                        start=min(event.at for event in combined),
                        end=max(event.at for event in combined))
                    comparison = compare_risk(observed, simulated, quotes, spec=spec)
                    for name in ("observed", "simulated"):
                        comparison[name] = _summary(comparison[name])
                    frozen = frozen_by_signal[basket["signal_id"]]
                    require_native_risk_metrics(comparison["observed"], frozen)
                    actual_net = sum((Decimal(str(position["actual_net_eur"]))
                                      for position in positions[basket["signal_id"]]),
                                     Decimal(0))
                    if (comparison["observed"]["metrics"] is None
                            or comparison["observed"]["metrics"]["final_net"] != actual_net):
                        raise ValueError("conditioned native booked money mismatch")
                    row.update(status=comparison["status"],
                               risk=comparison,
                               sequence=compare_sequences(observed, simulated),
                               frozen_native_drawdown_eur=frozen["path"]["metrics"]["max_drawdown"],
                               frozen_native_net_eur=frozen["path"]["metrics"]["final_net"],
                               native_booked_net_eur=str(actual_net))
                    if timeline_by_ticket is not None:
                        row["tp_receipt_states"] = receipt_states(
                            basket["signal_id"], positions[basket["signal_id"]],
                            simulated, timeline_by_ticket)
            except (KeyError, TypeError, ValueError) as exc:
                row.update(status="blocked_conditioned_input", blockers=[str(exc)])
        rows.append(row)
        print(json.dumps({"signal": row["signal_id"], "status": row["status"]}), flush=True)
    return rows, {"day": day, "market_sha256": market_proof["parquet_sha256"],
                  "fx_sha256": fx_proof["parquet_sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", nargs=2, action="append", metavar=("MODEL", "TICK"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--money", type=Path, required=True)
    parser.add_argument("--broker", type=Path, required=True)
    parser.add_argument("--native-risk", type=Path, required=True)
    parser.add_argument("--tp-timeline", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= len(args.window) <= MAX_WINDOWS:
        raise ValueError("conditioned window budget exceeded")
    started = time.monotonic()
    watched = {path: digest(path) for path in (
        args.raw, args.money, args.broker, args.native_risk,
        *((args.tp_timeline,) if args.tp_timeline is not None else ()),
        *(path for pair in args.window for path in map(Path, pair)),
        Path(__file__), Path(__file__).with_name("probe_canal1_incremental_window.py"),
        Path(__file__).with_name("run_week_causal_controls.py"),
        Path(__file__).with_name("compare_risk_trajectories.py"),
        *(Path(__file__).resolve().parents[1] / name for name in (
            "research/conditioned_management.py",
            "research/observed_tp_receipts.py",
            "research/causal_replay.py",
            "research/causal_comparison.py",
            "research/risk_trajectory.py",
            "research/dubai_iterative/engine.py",
            "research/dubai_iterative/fast_engine.py",
            "research/dubai_iterative/oracle.py",
            "research/gold_iterative/live_parity.py")))}
    raw = json.loads(args.raw.read_text(encoding="utf-8-sig"))
    money = json.loads(args.money.read_text(encoding="utf-8"))
    broker = json.loads(args.broker.read_text(encoding="utf-8"))
    native_risk = json.loads(args.native_risk.read_text(encoding="utf-8"))
    if (raw.get("contract") != "frozen_raw_telegram_slice_v1"
            or raw.get("causal_input_only") is not True
            or raw.get("raw_row_count") != len(raw.get("rows", []))):
        raise ValueError("frozen Telegram input unavailable")
    raw["_input_sha256"] = watched[args.raw]
    money["_input_sha256"] = watched[args.money]
    if (native_risk.get("contract") != "native_week_full_tick_risk_diagnostic_v1"
            or [sha for name, sha in native_risk["inputs_sha256"].items()
                if Path(name).name == args.money.name] != [watched[args.money]]):
        raise ValueError("frozen native risk money source changed")
    native_risk["_input_sha256"] = watched[args.native_risk]
    timeline_by_ticket = None
    if args.tp_timeline is not None:
        timeline = json.loads(args.tp_timeline.read_text(encoding="utf-8"))
        if (timeline.get("contract") != "tp_request_timeline_diagnostic_v1"
                or timeline.get("leg_count") != len(timeline.get("rows", []))):
            raise ValueError("TP receipt timeline contract differs")
        for name, expected in timeline["inputs_sha256"].items():
            if digest(Path(name)) != expected:
                raise ValueError(f"TP receipt timeline source changed: {name}")
        timeline_by_ticket = {row["native_position_id"]: row for row in timeline["rows"]}
        if len(timeline_by_ticket) != timeline["leg_count"]:
            raise ValueError("TP receipt ticket identity ambiguous")
    rows, tape_proofs = [], []
    for model_name, tick_name in args.window:
        model_path, tick_path = Path(model_name), Path(tick_name)
        model = json.loads(model_path.read_text(encoding="utf-8"))
        tick = json.loads(tick_path.read_text(encoding="utf-8"))
        model["_input_sha256"] = watched[model_path]
        window_rows, proof = audit_window(
            model, tick, money, broker, raw, native_risk, args.root, started=started,
            timeline_by_ticket=timeline_by_ticket)
        rows.extend(window_rows)
        tape_proofs.append(proof)
    if len(rows) > MAX_ROWS or len({row["signal_id"] for row in rows}) != len(rows):
        raise ValueError("conditioned basket denominator invalid")
    if any(digest(path) != sha for path, sha in watched.items()):
        raise ValueError("conditioned source changed during run")
    for proof in tape_proofs:
        for symbol, sha in (("XAUUSD", proof["market_sha256"]),
                            ("EURUSD", proof["fx_sha256"])):
            if digest(args.root / symbol / f"{proof['day']}.parquet") != sha:
                raise ValueError("conditioned market source changed during run")
    report = {"contract": "weekly_native_entry_conditioned_risk_diagnostic_v1",
              "status": "diagnostic_only", "full_live_parity_verified": False,
              "entry_decisions_verified": False,
              "client_observation_replay_verified": False,
              "input_basket_count": len(rows),
              "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
              "rows": rows, "tape_proofs": tape_proofs,
              "tp_receipt_statuses": dict(sorted(Counter(
                  state["status"] for row in rows
                  for state in row.get("tp_receipt_states", [])).items())),
              "inputs_sha256": {str(path): sha for path, sha in watched.items()},
              "elapsed_seconds": time.monotonic() - started,
              "limitations": [
                  "Observed entries are fixed inputs; their decisions are not validated.",
                  "Native exits, money and installed protections are not engine inputs.",
                  "The isolated Gold policy does not replay shared client queues or broker acknowledgements.",
                  "Canal1 dynamic management and no-entry scopes remain blocked here.",
                  "Only two retrospective windows are evaluated; this is not fresh holdout evidence.",
                  "Client TP responses do not prove the server installation time or trigger.",
              ]}
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, sort_keys=True, default=str, allow_nan=False)
        target.write("\n")
    print(json.dumps({"statuses": report["statuses"],
                      "input_basket_count": report["input_basket_count"]}))


if __name__ == "__main__":
    main()
