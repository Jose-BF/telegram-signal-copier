"""Week exam: replay every window with real or sim-visible activity and compare
per basket with the real fills (net, drawdown, volume).

Windows are cut only where the real account was flat, so each replay may
assume an empty initial universe (the probe re-checks it against native
positions). The simulator never sees native fills: it replays the frozen
Telegram slice on the frozen tick tape with the given execution profile(s).
Real per-basket values come from tools/native_basket_risk_from_deals.py.

Exam criteria (docs/development/estado-y-plan.md, Paso 3):
  * net: |sum sim - sum real| <= 5% of sum |real|
  * drawdown: |sum sim - sum real| <= 5% of sum real, no systematic underestimate
  * range: real value inside the profiles' simulated range in >= 90% baskets
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.causal_canal1_stream import compile_canal1_stream  # noqa: E402
from research.causal_replay import CausalSignal, compile_signals, utc  # noqa: E402
from research.dubai_iterative.latency_model import LatencyModel  # noqa: E402
from tools import probe_canal1_incremental_window as probe  # noqa: E402
from tools.run_causal_controls import STICKERS  # noqa: E402

MERGE_GAP_S = 60
START_MARGIN_S = 25  # < MERGE_GAP_S, so a start never falls inside the previous cluster
SEED_ACTIVITY_S = 60
BUDGET = 750_000


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sim_entry_seeds(rows, day_start, day_end):
    """Entry signals the simulator itself would see that day (both channels)."""
    rows = [row for row in rows if not (row.get("sticker_id") is not None
            and str(row["sticker_id"]) not in STICKERS and not (row.get("text") or "").strip())]
    stream = compile_canal1_stream(rows, start=day_start, cutoff=day_end,
                                   sticker_directions=STICKERS, max_entry_age_s=120,
                                   optional_close_choice="hold")
    seeds = {}
    for item in stream.timeline:
        if isinstance(item.value, CausalSignal):
            seeds[item.value.signal_id] = item.value.observed_at
    other, _ = compile_signals(rows, start=day_start, cutoff=day_end,
                               sticker_directions=STICKERS, max_entry_age_s=120)
    for signal in other:
        if signal.channel == "canal2":
            seeds[signal.signal_id] = signal.observed_at
    return seeds


def build_windows(reconciled, rows, first_day, end_day, offset_s, tail_s):
    """Clusters of activity cut at real-flat points, one UTC date each."""
    to_utc = lambda msc: datetime.fromtimestamp(msc / 1000 - offset_s, timezone.utc)  # noqa: E731
    observed = {}
    for row in rows:
        # Earliest of publication and first observation: a replay window that
        # starts after the publication drops the message as pre-existing.
        key = f"{row['channel']}_{row['message_id']}"
        stamp = min(utc(row["ts"]), utc(row["date_utc"])) if row.get("date_utc") else utc(row["ts"])
        if key not in observed or stamp < observed[key]:
            observed[key] = stamp
    spans, notes = [], []
    for basket in reconciled["baskets"]:
        first, last = to_utc(basket["first_native_msc"]), to_utc(basket["last_native_msc"])
        seen = observed.get(basket["signal_id"])
        begin = min(first, seen) if seen is not None and seen <= first else first
        if seen is None:
            notes.append({"signal_id": basket["signal_id"], "note": "signal_message_not_in_raw_slice"})
        spans.append([begin, last, {basket["signal_id"]}, "real"])
    day = datetime.fromisoformat(first_day).replace(tzinfo=timezone.utc)
    stop = datetime.fromisoformat(end_day).replace(tzinfo=timezone.utc)
    seeds = {}
    while day < stop:
        seeds.update(sim_entry_seeds(rows, day, day + timedelta(days=1)))
        day += timedelta(days=1)
    real_ids = {b["signal_id"] for b in reconciled["baskets"]}
    for signal_id, seen in seeds.items():
        if signal_id not in real_ids:
            seen = min(seen, observed.get(signal_id, seen))
            spans.append([seen, seen + timedelta(seconds=SEED_ACTIVITY_S), {signal_id}, "sim_seed"])
    spans.sort(key=lambda s: s[0])
    clusters = []
    for begin, end, ids, kind in spans:
        if clusters and begin <= clusters[-1][1] + timedelta(seconds=MERGE_GAP_S):
            clusters[-1][1] = max(clusters[-1][1], end)
            clusters[-1][2] |= ids
            clusters[-1][3].add(kind)
        else:
            clusters.append([begin, end, set(ids), {kind}])
    merged = clusters
    windows = []
    starts = [(begin - timedelta(seconds=START_MARGIN_S)).replace(microsecond=0)
              for begin, _, _, _ in merged]
    for index, (begin, end, ids, kinds) in enumerate(merged):
        start = starts[index]
        nxt = starts[index + 1] if index + 1 < len(merged) else None
        cutoff = end + timedelta(seconds=tail_s)
        if nxt is not None:
            cutoff = min(cutoff, nxt - timedelta(seconds=1))
        day_end = datetime.combine(start.date() + timedelta(days=1), datetime.min.time(), timezone.utc)
        crosses = end >= day_end
        cutoff = min(cutoff, day_end - timedelta(milliseconds=1))
        windows.append({"window": index, "start_utc": start.isoformat(),
                        "cutoff_utc": cutoff.replace(microsecond=0).isoformat(),
                        "activity_end_utc": end.isoformat(),
                        "signal_ids": sorted(ids), "kinds": sorted(kinds),
                        "crosses_utc_midnight": crosses})
    return windows, notes


def run_window(args):
    window, profile_name, calibration_path, cfg = args
    started = time.monotonic()
    override, latency_model, canal1_latency = None, None, None
    if calibration_path is not None and calibration_path.startswith("SAMPLED:"):
        # SAMPLED:<calibration.json>:<latency_samples.json>:<seed>[:<canal1_samples.json>]
        # optional 6th field: basket correlation of delays (0 = independent)
        parts = calibration_path.split(":")
        base, samples, seed = parts[1], parts[2], parts[3]
        rho = float(parts[5]) if len(parts) > 5 else 0.0
        override = json.loads(Path(base).read_text(encoding="utf-8"))
        latency_model = LatencyModel.from_samples(
            json.loads(Path(samples).read_text(encoding="utf-8")), int(seed), rho)
        if len(parts) > 4 and parts[4]:
            canal1_latency = LatencyModel.from_samples(
                json.loads(Path(parts[4]).read_text(encoding="utf-8")), int(seed), rho)
    elif calibration_path is not None:
        override = json.loads(Path(calibration_path).read_text(encoding="utf-8"))
    try:
        report = probe.run(Path(cfg["tick_root"]), start=window["start_utc"], cutoff=window["cutoff_utc"],
                           offset_seconds=cfg["offset_s"], assume_initial_complete=True,
                           optional_close_choice="hold",
                           native_reconciled=Path(cfg["reconciled"]), native_deals=Path(cfg["deals"]),
                           include_risk_grid=False, execution_override=override,
                           raw_name=cfg["raw_name"], max_fx_age_ms=cfg["max_fx_age_ms"],
                           scope_tick_budget=cfg["budget"], same_ms_message_after_quote=True,
                           latency_model=latency_model, ignore_unknown_stickers=True,
                           canal1_latency_model=canal1_latency, idle_unresolved_ignored=True)
        baskets = {}
        for signal_id, row in report["basket_risk"].items():
            baskets[signal_id] = {
                "channel": row["channel"],
                "net_eur": None if row["final_total_minor_model"] is None else row["final_total_minor_model"] / 100,
                "dd_eur": None if row["max_drawdown_minor_model"] is None else row["max_drawdown_minor_model"] / 100,
                "max_volume": row["max_gross_volume_model"], "exposed": row["exposed_frames"] > 0,
                "blockers": row["blockers"]}
        return {"window": window["window"], "profile": profile_name, "status": report["status"],
                "same_ms_shifted": len(report["same_ms_shifted_messages"]),
                "ignored_unknown_stickers": len(report["ignored_unknown_sticker_rows"]),
                "shared_blockers": report["shared_blockers"], "baskets": baskets,
                "native_start_open": report["native_start_control"]["open_position_count"],
                "elapsed_s": round(time.monotonic() - started, 2)}
    except Exception as exc:  # recorded, never hidden
        return {"window": window["window"], "profile": profile_name, "status": "error",
                "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:],
                "elapsed_s": round(time.monotonic() - started, 2)}


EXTEND_BLOCKERS = ("basket_still_open_at_cutoff", "path_ended_before_strategy_exit")


def needs_extension(results):
    for result in results:
        for sim in result.get("baskets", {}).values():
            if any(b.endswith(EXTEND_BLOCKERS) for b in sim["blockers"]):
                return True
    return False


def run_day(args):
    """Windows of one UTC date in order; a window whose replay is still open
    at its cutoff is merged with the next one (or extended) and re-run."""
    windows, profiles, cfg = args
    windows = [dict(w) for w in windows]
    out, attempts_log = [], []
    index = 0
    while index < len(windows):
        window = windows[index]
        previous, absorbed = None, []
        for attempt in range(cfg["max_extensions"] + 1):
            results = [run_window((window, name, path, cfg)) for name, path in profiles.items()]
            failed = any(r["status"] == "error" or any(
                "budget_exhausted" in b for sim in r.get("baskets", {}).values() for b in sim["blockers"])
                for r in results)
            if failed and previous is not None:
                # The longer window no longer fits (budget) or fails: keep the
                # last complete replay with its still-open blockers and put
                # back the windows absorbed after it.
                window, results, kept = previous
                windows[index + 1:index + 1] = absorbed[kept:]
                window = {**window, "extension_reverted": True}
                break
            if failed or not needs_extension(results):
                break
            previous = (window, results, len(absorbed))
            # Gold closes 20:55 UTC; later cutoffs only add empty tape.
            day_end = datetime.fromisoformat(window["start_utc"]).replace(
                hour=20, minute=58, second=0, microsecond=0)
            if index + 1 < len(windows):
                nxt = windows.pop(index + 1)
                absorbed.append(nxt)
                window = {**window, "cutoff_utc": nxt["cutoff_utc"],
                          "signal_ids": sorted(set(window["signal_ids"]) | set(nxt["signal_ids"])),
                          "kinds": sorted(set(window["kinds"]) | set(nxt["kinds"])),
                          "merged_windows": window.get("merged_windows", [window["window"]]) + [nxt["window"]]}
            else:
                cutoff = min(datetime.fromisoformat(window["cutoff_utc"]) + timedelta(hours=2), day_end)
                if cutoff.isoformat() == window["cutoff_utc"]:
                    break
                window = {**window, "cutoff_utc": cutoff.isoformat(),
                          "extended": window.get("extended", 0) + 1}
            attempts_log.append({"window": window["window"], "attempt": attempt + 1,
                                 "cutoff_utc": window["cutoff_utc"]})
        window["attempts"] = attempt + 1
        out.append((window, results))
        index += 1
    return out, attempts_log


def score(real, results, profiles, covered=None, range_profiles=None, real_net_only=None):
    """Per-basket comparison and exam metrics per profile.

    real_net_only: real baskets whose risk path is blocked; they are matched on
    net only (DD unknown) instead of being mistaken for simulator-only baskets.
    """
    real_net_only = real_net_only or {}
    rows = {}
    for result in results:
        if result["status"] == "error":
            continue
        for signal_id, sim in result["baskets"].items():
            rows.setdefault(signal_id, {})[result["profile"]] = sim
    per_profile = {}
    for profile in profiles:
        matched, real_only, sim_only, blocked = [], [], [], []
        not_covered = []
        for signal_id, r in real.items():
            sim = rows.get(signal_id, {}).get(profile)
            if covered is not None and signal_id not in covered:
                not_covered.append(signal_id)
            elif sim is None or not sim["exposed"]:
                real_only.append(signal_id)
            elif sim["net_eur"] is None or sim["dd_eur"] is None:
                blocked.append(signal_id)
            else:
                matched.append(signal_id)
        net_only, net_only_missing = [], []
        for signal_id, per in rows.items():
            sim = per.get(profile)
            if signal_id in real_net_only:
                if sim is not None and sim["exposed"] and sim["net_eur"] is not None:
                    net_only.append(signal_id)
            elif signal_id not in real and sim is not None and sim["exposed"]:
                sim_only.append(signal_id)
        for signal_id in real_net_only:
            if signal_id not in net_only and (covered is None or signal_id in covered):
                net_only_missing.append(signal_id)
        scored = [s for s in real if s not in not_covered]
        real_net = sum(real[s]["net_eur"] for s in scored) + sum(
            real_net_only[s] for s in real_net_only if covered is None or s in covered)
        abs_real = sum(abs(real[s]["net_eur"]) for s in scored) + sum(
            abs(real_net_only[s]) for s in real_net_only if covered is None or s in covered)
        sim_net = sum(rows[s][profile]["net_eur"] for s in matched) + sum(
            rows[s][profile]["net_eur"] or 0 for s in sim_only) + sum(
            rows[s][profile]["net_eur"] for s in net_only)
        real_dd = sum(real[s]["dd_eur"] for s in matched)
        sim_dd = sum(rows[s][profile]["dd_eur"] for s in matched)
        under = sum(rows[s][profile]["dd_eur"] < real[s]["dd_eur"] - 0.5 for s in matched)
        over = sum(rows[s][profile]["dd_eur"] > real[s]["dd_eur"] + 0.5 for s in matched)
        per_profile[profile] = {
            "matched": len(matched), "real_only": sorted(real_only), "sim_only": sorted(sim_only),
            "not_covered": sorted(not_covered),
            "matched_net_only_real_path_blocked": sorted(net_only),
            "real_path_blocked_not_simulated": sorted(net_only_missing),
            "blocked": sorted(blocked),
            "real_net_eur": round(real_net, 2), "sim_net_eur": round(sim_net, 2),
            "net_error_pct_of_abs_real": round(100 * abs(sim_net - real_net) / abs_real, 2) if abs_real else None,
            "matched_real_dd_sum": round(real_dd, 2), "matched_sim_dd_sum": round(sim_dd, 2),
            "dd_error_pct": round(100 * (sim_dd - real_dd) / real_dd, 2) if real_dd else None,
            "dd_underestimates_gt_0_5": under, "dd_overestimates_gt_0_5": over}
    table = []
    for signal_id in sorted(set(real) | set(rows)):
        entry = {"signal_id": signal_id, "real": real.get(signal_id)}
        entry["sim"] = {p: rows.get(signal_id, {}).get(p) for p in profiles}
        values = [v for name, v in entry["sim"].items()
                  if (range_profiles is None or name in range_profiles)
                  and v and v["exposed"] and v["dd_eur"] is not None]
        if entry["real"] and values:
            lo_dd, hi_dd = min(v["dd_eur"] for v in values), max(v["dd_eur"] for v in values)
            lo_net, hi_net = min(v["net_eur"] for v in values), max(v["net_eur"] for v in values)
            entry["real_dd_in_sim_range"] = lo_dd - 0.01 <= entry["real"]["dd_eur"] <= hi_dd + 0.01
            entry["real_net_in_sim_range"] = lo_net - 0.01 <= entry["real"]["net_eur"] <= hi_net + 0.01
        table.append(entry)
    ranged = [e for e in table if "real_dd_in_sim_range" in e]
    return per_profile, table, {
        "baskets_with_range": len(ranged),
        "real_dd_inside_range_pct": round(100 * sum(e["real_dd_in_sim_range"] for e in ranged) / len(ranged), 1) if ranged else None,
        "real_net_inside_range_pct": round(100 * sum(e["real_net_in_sim_range"] for e in ranged) / len(ranged), 1) if ranged else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tick-root", type=Path, required=True)
    parser.add_argument("--raw-name", required=True)
    parser.add_argument("--reconciled", type=Path, required=True)
    parser.add_argument("--deals", type=Path, required=True)
    parser.add_argument("--native-risk", type=Path, required=True)
    parser.add_argument("--profile", action="append", required=True,
                        help="name=calibration.json or name=UNCAL")
    parser.add_argument("--first-day", required=True)
    parser.add_argument("--end-day", required=True)
    parser.add_argument("--offset-seconds", type=int, default=10_800)
    parser.add_argument("--tail-seconds", type=int, default=1_800)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-fx-age-ms", type=int, default=30_000,
                        help="EURUSD staleness guard for the EUR conversion (probe default 5000)")
    parser.add_argument("--scope-tick-budget", type=int, default=950_000,
                        help="scopes x ticks per window; the shared replay caps events at 1,000,000")
    parser.add_argument("--max-extensions", type=int, default=3)
    parser.add_argument("--range-prefix", default="",
                        help="profiles whose name starts with this form the per-basket range")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite")
    started = time.monotonic()
    raw_path = args.tick_root / args.raw_name
    rows = json.loads(raw_path.read_text(encoding="utf-8-sig"))["rows"]
    reconciled = json.loads(args.reconciled.read_text(encoding="utf-8"))
    native_risk = json.loads(args.native_risk.read_text(encoding="utf-8"))
    real, real_net_only = {}, {}
    for basket in native_risk["baskets"]:
        metrics = basket.get("path", {}).get("known_sample_metrics") if basket["status"] != "blocked" else None
        if metrics is None:
            # the risk path is blocked (e.g. a broker quote gap) but the basket is
            # real and its net is exact from the deals: compare net only, never DD
            if basket.get("native_net_eur") is not None:
                real_net_only[basket["signal_id"]] = float(basket["native_net_eur"])
            continue
        real[basket["signal_id"]] = {"net_eur": float(metrics["final_net"]), "dd_eur": float(metrics["max_drawdown"]),
                                     "max_volume": metrics["max_gross_volume"]}
    windows, notes = build_windows(reconciled, rows, args.first_day, args.end_day,
                                   args.offset_seconds, args.tail_seconds)
    profiles = {}
    for spec in args.profile:
        name, _, path = spec.partition("=")
        profiles[name] = None if path == "UNCAL" else (path if path.startswith("SAMPLED:") else str(Path(path)))
    cfg = {"tick_root": str(args.tick_root), "offset_s": args.offset_seconds, "raw_name": args.raw_name,
           "reconciled": str(args.reconciled), "deals": str(args.deals),
           "max_fx_age_ms": args.max_fx_age_ms, "budget": args.scope_tick_budget,
           "max_extensions": args.max_extensions}
    by_day = {}
    for window in windows:
        if not window["crosses_utc_midnight"]:
            by_day.setdefault(window["start_utc"][:10], []).append(window)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        day_outputs = list(pool.map(run_day, [(ws, profiles, cfg) for ws in by_day.values()]))
    final_windows, results, extensions, covered = [], [], [], set()
    for day_out, log in day_outputs:
        extensions.extend(log)
        for window, window_results in day_out:
            final_windows.append(window)
            results.extend(window_results)
            if all(r["status"] != "error" for r in window_results):
                covered |= set(window["signal_ids"])
                for r in window_results:
                    covered |= set(r["baskets"])
    range_profiles = None
    if args.range_prefix:
        range_profiles = {name for name in profiles if name.startswith(args.range_prefix)}
    per_profile, table, ranges = score(real, results, list(profiles), covered, range_profiles, real_net_only)
    report = {"contract": "week_exam_v1", "status": "diagnostic_only",
              "first_day": args.first_day, "end_day_exclusive": args.end_day,
              "clock_offset_seconds_hypothesis": args.offset_seconds,
              "profiles": profiles, "windows": windows, "final_windows": final_windows,
              "extensions": extensions, "max_fx_age_ms": args.max_fx_age_ms,
              "scope_tick_budget": args.scope_tick_budget, "window_notes": notes,
              "window_results": results, "per_profile": per_profile, "ranges": ranges,
              "baskets": table,
              "real_baskets_with_path": len(real), "real_baskets_total": len(native_risk["baskets"]),
              "errors": [r for r in results if r["status"] == "error"],
              "inputs_sha256": {str(p): digest(p) for p in (raw_path, args.reconciled, args.deals, args.native_risk,
                                                             Path(__file__), Path(probe.__file__),
                                                             *sorted({Path(part) for v in profiles.values() if v
                                                                      for part in ([x for i, x in enumerate(v.split(":")) if i in (1, 2, 4) and x] if v.startswith("SAMPLED:") else [v])}))},
              "elapsed_s": round(time.monotonic() - started, 1),
              "limitations": ["Windows crossing a UTC midnight are not replayed (probe loads one UTC date).",
                              "Real-only means the replay never exposed that basket inside its window.",
                              "Fixed-quantile execution profiles, not per-request sampled delays."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=1, default=str)
    print(json.dumps({"windows": len(final_windows), "extensions": len(extensions), "errors": len(report["errors"]),
                      "per_profile": per_profile, "ranges": ranges, "elapsed_s": report["elapsed_s"]}, indent=1))


if __name__ == "__main__":
    main()
