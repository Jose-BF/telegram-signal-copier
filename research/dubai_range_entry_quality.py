"""Finite causal abstention overlays; retained fills are never modified."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import time

from research.dubai_clock_audit import _write
from research.dubai_range_study import stats, verify_study
from research.strategy_study import ROOT, _digest, _read, _sha, _verify_sources, _watch


THRESHOLDS = ("0.25", "0.50", "0.75", "1.00")
KNOWN = {"simulated", "unfilled"}


def decision(row, threshold):
    if row["status"] not in KNOWN:
        return {"decision": "base_blocked_or_not_applicable", "ratio": None, "quote": None}
    requests = [e for e in row["result"]["market_events"] if e["kind"] == "entry_requested"]
    if not requests:
        if row["status"] != "unfilled":
            raise ValueError("filled base without causal request")
        return {"decision": "base_no_order", "ratio": None, "quote": None}
    if len(requests) != 1 or requests[0]["tick_index"] != 0 or requests[0]["request_id"] != 1:
        raise ValueError("not the frozen immediate single-entry decision")
    genome = row["genome"]
    if genome["entry_mode"] != "signal_market" or genome["leg_count"] != 1 or genome["target_mode"] != "per_leg_levels":
        raise ValueError("unsupported overlay base")
    sign = Decimal(1 if row["direction"] == "BUY" else -1)
    quote, stop, target = map(lambda v: Decimal(str(v)),
        (requests[0]["price"], genome["stop_value"], genome["target_steps"][0]))
    distance = sign * (quote - stop)
    if distance <= 0:
        raise ValueError("invalid decision stop distance")
    nominal = distance * Decimal(100) * Decimal(str(genome["volume_weights"][0]))
    if abs(nominal - Decimal(str(row["nominal_risk_usd"]))) > Decimal("0.00000001"):
        raise ValueError("decision quote does not reconcile to frozen nominal risk")
    ratio = sign * (target - quote) / distance
    return {"decision": "retain" if ratio >= Decimal(threshold) else "abstain", "ratio": str(ratio), "quote": str(quote)}


def overlay_row(base, outcome):
    row = deepcopy(base)
    if outcome["decision"] == "abstain":
        row["status"], row["net_after_cost_eur"], row["nominal_risk_usd"] = "unfilled", "0", 0
    return row


def run(base_dir, output_dir):
    started = time.monotonic()
    base, output = Path(base_dir).resolve(), Path(output_dir).resolve()
    if output.exists() or output.is_relative_to(base) or base.is_relative_to(output):
        raise ValueError("immutable overlay output overlaps or exists")
    proof = verify_study(base)
    watched = {}
    for path in [base / name for name in ("manifest.json", "protocol.json", "results.jsonl")] + [
        ROOT / "research/dubai_range_entry_quality.py", ROOT / "research/dubai_range_study.py",
        ROOT / "docs/development/2026-09-14-canal1-entry-quality-protocol.md"]:
        _watch(path, _digest(path), watched)
    rows = [json.loads(line) for line in (base / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    base_rows = [r for r in rows if r["policy_id"] in {"range:on_levels:tp1:60", "range:on_levels:tp1:240"}]
    if len(base_rows) != 258 * 2 * 2 or len({r["signal_id"] for r in base_rows}) != 258:
        raise ValueError("unexpected frozen universe")
    output.mkdir(parents=True)
    _write(output / "protocol.json", {"base": proof, "thresholds": THRESHOLDS, "hold_minutes": [60, 240],
        "scope": "causal single-entry abstention; unchanged retained isolated fills; no fresh OOS or live admission",
        "selected_policy": None, "orders_sent": 0, "max_rows": 4128, "max_wall_seconds": 300})
    records, groups, transformed = [], {}, {}
    for minutes in (60, 240):
        for profile in ("reference", "adverse_execution"):
            selected = [r for r in base_rows if r["policy_id"].endswith(":" + str(minutes)) and r["profile"] == profile]
            for threshold in THRESHOLDS:
                key = f"rr{threshold}:{minutes}|{profile}"
                decisions = [(r, decision(r, threshold)) for r in selected]
                adapted = [overlay_row(r, d) for r, d in decisions]
                transformed[(minutes, profile, threshold)] = adapted
                group = stats(adapted)
                group["abstained_signals"] = sum(d["decision"] == "abstain" for _,d in decisions)
                group["net_without_best_day_eur"] = str(Decimal(group["net_after_cost_eur"]) - max(map(Decimal, group["daily"].values())))
                groups[key] = group
                records.extend({"signal_id": r["signal_id"], "profile": profile, "hold_minutes": minutes,
                    "threshold": threshold, "base_status": r["status"], **d,
                    "net_after_cost_eur": "0" if d["decision"] == "abstain" else r["net_after_cost_eur"]} for r,d in decisions)
    sensitivity = {}
    for profile in ("reference", "adverse_execution"):
        for threshold in THRESHOLDS:
            long = {r["signal_id"]: r for r in transformed[(240, profile, threshold)]}
            short = transformed[(60, profile, threshold)]
            extra = [r for r in short if r["status"] in KNOWN and long[r["signal_id"]]["status"] not in KNOWN]
            # This is a coverage diagnostic, not admission into the four-hour matrix.
            natural = [r for r in extra if r["status"] == "unfilled" or not any(e["reason"] == "time_exit" for e in r["result"]["exits"])]
            value = sum((Decimal(r["net_after_cost_eur"]) for r in natural), Decimal(0))
            sensitivity[f"rr{threshold}|{profile}"] = {"short_only_known": len(extra), "natural_or_no_position": len(natural),
                "natural_net_eur": str(value), "four_hour_net_plus_known_natural_diagnostic_eur":
                str(Decimal(groups[f"rr{threshold}:240|{profile}"]["net_after_cost_eur"]) + value),
                "remaining_unknown_signals": 168 - groups[f"rr{threshold}:240|{profile}"]["known"] - len(natural)}
    if len(records) != 4128 or time.monotonic() - started > 300:
        raise ValueError("overlay matrix or budget exceeded")
    _write(output / "results.json", records)
    _write(output / "summary.json", {"groups": groups, "coverage_sensitivity": sensitivity,
        "selected_policy": None, "money_contract_verified": False, "orders_sent": 0})
    _verify_sources(watched)
    manifest = {"base_identity": proof["identity"], "inputs": watched,
        "artifacts": {name: _digest(output / name) for name in ("protocol.json", "results.json", "summary.json")},
        "rows": len(records), "engine_calls": 0, "wall_seconds": time.monotonic() - started}
    manifest["identity"] = _sha(manifest)
    _write(output / "manifest.json", manifest)
    return manifest
