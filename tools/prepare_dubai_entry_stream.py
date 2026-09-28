"""Freeze offline entry decisions and recompute coverage, never execute trades."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.dubai_annual_coverage import RawSource, coverage_metrics, digest, msc, read
from research.dubai_annual_universe import load_catalog
from research.dubai_entry_stream import build_stream, write_stream
from research.telegram_export import _hash


ROOT = Path(__file__).resolve().parents[1]
METRICS = ("coverage_reasons", "quote_coverage_pass", "broker_offset_seconds_at_start",
    "market_quotes_in_horizon", "first_quote_delay_ms", "last_quote_age_ms",
    "max_internal_market_gap_ms", "market_gaps_over_limit", "first_market_gap_over_limit",
    "invalid_conversion_points", "max_prior_conversion_age_ms", "first_invalid_conversion_utc",
    "end_utc", "raw_source_days", "missing_raw_source_days")


def prepare(catalog_dir, universe_dir, prior_coverage_dir, raw_audit_path, protocol_path):
    started, watched = time.monotonic(), {}

    def watch(path, expected=None):
        path = Path(path).resolve()
        actual = digest(path)
        if expected is not None and expected != actual:
            raise ValueError(f"input hash mismatch: {path.name}")
        if str(path) in watched and watched[str(path)] != actual:
            raise ValueError("input changed during preparation")
        watched[str(path)] = actual
        return actual

    def archive(directory, identity_key):
        directory = Path(directory).resolve()
        manifest = read(directory / "manifest.json")
        watch(directory / "manifest.json")
        if _hash({key: val for key, val in manifest.items() if key != identity_key}) != manifest[identity_key]:
            raise ValueError("archive identity mismatch")
        for name, spec in manifest["artifacts"].items():
            if Path(name).name != name:
                raise ValueError("unsafe artifact path")
            watch(directory / name, spec["sha256"])
        return manifest

    catalog_dir, universe_dir, prior_coverage_dir = map(Path, (catalog_dir, universe_dir, prior_coverage_dir))
    protocol = read(protocol_path)
    watch(protocol_path)
    for relative in ("research/dubai_entry_stream.py", "tools/prepare_dubai_entry_stream.py", "parser.py"):
        watch(ROOT / relative)
    catalog_manifest = archive(catalog_dir, "catalog_identity_sha256")
    catalog, messages = load_catalog(catalog_dir)
    universe = archive(universe_dir, "universe_identity_sha256")
    if (catalog["catalog_identity_sha256"] != protocol["catalog_identity_sha256"]
            or universe["universe_identity_sha256"] != protocol["legacy_universe_identity_sha256"]
            or universe["inputs"]["catalog_identity_sha256"] != catalog["catalog_identity_sha256"]):
        raise ValueError("mixed catalog/universe protocol")
    if protocol["rolling_protocol"] != universe["contract"]["rolling_protocol"] or protocol["pair_seconds"] != catalog["contract"]["companion_seconds"]:
        raise ValueError("unannounced grouping or rolling interval change")
    snapshots = {snapshot["raw_snapshot_sha256"] for row in messages for snapshot in row["snapshots"]}
    if not {label["raw_snapshot_sha256"] for label in protocol["semantic_labels"]} <= snapshots:
        raise ValueError("semantic label without retained snapshot")
    legacy = [json.loads(line) for line in (universe_dir / "entries.jsonl").read_text(encoding="utf-8").splitlines()]
    if not 0 < len(messages) <= 10_000:
        raise ValueError("message budget exceeded")
    result = build_stream(messages, legacy, protocol)
    print(json.dumps({"event": "entry_decisions_frozen_before_prices", "summary": result["summary"]}), flush=True)

    previous = archive(prior_coverage_dir, "audit_identity_sha256")
    for path, sha in previous["inputs"]["watched_files"].items():
        watch(path, sha)
    coverage_protocol = read(prior_coverage_dir / "protocol.json")
    watch(raw_audit_path, coverage_protocol["raw_audit_sha256"])
    if max(coverage_protocol["horizons_seconds"]) > protocol["rolling_protocol"]["horizon_seconds"]:
        raise ValueError("coverage horizon exceeds frozen purge")
    source = RawSource(raw_audit_path, coverage_protocol["broker_clock"]["segments"], watch)
    cache = {}
    for line in (prior_coverage_dir / "coverage.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["start_utc"] is None:
            continue
        key = (msc(row["start_utc"]), row["horizon_seconds"])
        metrics = {name: row[name] for name in METRICS}
        if key in cache and cache[key] != metrics:
            raise ValueError("prior coverage disagrees for the same clock/horizon")
        cache[key] = metrics
    previous_keys, measured = set(cache), 0
    coverage = []
    usage = {(r["scenario"], r["entry_id"]): r for r in result["entry_fold_usage"]}
    for index, trigger in enumerate(result["triggers"]):
        if time.monotonic() - started > 900:
            raise TimeoutError("15-minute preparation budget exhausted")
        for horizon in coverage_protocol["horizons_seconds"]:
            start = msc(trigger["trigger_utc"])
            key = (start, horizon)
            if key not in cache:
                market, market_ids, market_missing = source.window("XAUUSD", start, start + horizon * 1000)
                fx, fx_ids, fx_missing = source.window("EURUSD", start, start + horizon * 1000)
                metrics = coverage_metrics(start, horizon, market, fx, coverage_protocol)
                metrics.update(raw_source_days=market_ids + fx_ids, missing_raw_source_days=market_missing + fx_missing)
                if market_missing or fx_missing:
                    metrics["coverage_reasons"] = sorted(set(metrics["coverage_reasons"] + ["raw_source_day_missing"]))
                    metrics["quote_coverage_pass"] = False
                cache[key] = metrics
                measured += 1
            coverage.append({"trigger_id": trigger["trigger_id"], "scenario": trigger["scenario"],
                "trigger_utc": trigger["trigger_utc"], "horizon_seconds": horizon,
                "coverage_reused_from_previous_audit": key in previous_keys,
                "broker_clock_status": "declared_hypothesis", "engine_admitted": False,
                "money_contract_verified": False, "check_folds": usage[(trigger["scenario"], trigger["entry_id"])]["check_folds"],
                **cache[key]})
        if (index + 1) % 100 == 0:
            print(json.dumps({"event": "trigger_coverage_checked", "triggers": index + 1, "total": len(result["triggers"])}), flush=True)
    result["coverage"] = coverage
    for scenario, summary in result["summary"].items():
        summary["coverage"] = {}
        for horizon in coverage_protocol["horizons_seconds"]:
            rows = [r for r in coverage if r["scenario"] == scenario and r["horizon_seconds"] == horizon]
            summary["coverage"][str(horizon)] = {"all_triggers": len(rows),
                "quote_coverage_pass": sum(r["quote_coverage_pass"] for r in rows),
                "reason_counts": dict(Counter(reason for row in rows for reason in row["coverage_reasons"])),
                "monthly": {month: {"triggers": sum(r["trigger_utc"].startswith(month) for r in rows),
                    "quote_coverage_pass": sum(r["trigger_utc"].startswith(month) and r["quote_coverage_pass"] for r in rows)}
                    for month in summary["monthly_triggers"]}}
    result["summary"]["coverage_work"] = {"new_clock_horizon_windows_measured": measured,
        "rows_reusing_prior_audit": sum(r["coverage_reused_from_previous_audit"] for r in coverage),
        "coverage_rows": len(coverage), "prior_audit_identity_sha256": previous["audit_identity_sha256"]}
    for path, sha in list(watched.items()):
        watch(path, sha)
    if load_catalog(catalog_dir)[0] != catalog_manifest:
        raise ValueError("catalog source changed")
    result["inputs"] = {"watched_files": watched, "catalog_identity_sha256": catalog["catalog_identity_sha256"],
        "legacy_universe_identity_sha256": universe["universe_identity_sha256"],
        "protected_archive_dirs": [str(p.resolve()) for p in (catalog_dir, universe_dir, prior_coverage_dir, source.directory)],
        "coverage_protocol_sha256": digest(prior_coverage_dir / "protocol.json")}
    result["environment"] = {"python": platform.python_version()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--universe", required=True, type=Path)
    parser.add_argument("--prior-coverage", required=True, type=Path)
    parser.add_argument("--raw-audit", required=True, type=Path)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output already exists")
    result = prepare(args.catalog, args.universe, args.prior_coverage, args.raw_audit, args.protocol)
    manifest = write_stream(result, args.output)
    print(json.dumps({"identity": manifest["stream_identity_sha256"], "summary": result["summary"]}), flush=True)


if __name__ == "__main__":
    main()
