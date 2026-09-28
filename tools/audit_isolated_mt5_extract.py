"""Verify raw annual MT5 files and compare prior quotes without admitting clocks."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from tools.isolated_mt5_history import digest, immutable_json, inspect_ticks


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def raw_sequence(directory, symbol, day, offset_seconds):
    start = datetime.fromisoformat(day + "T00:00:00+00:00")
    left = int(start.timestamp() * 1000) + offset_seconds * 1000
    right = left + 86400000
    frames = []
    for epoch_day in range(left // 86400000, (right - 1) // 86400000 + 1):
        label = datetime.fromtimestamp(epoch_day * 86400, tz=start.tzinfo).date().isoformat()
        meta = read(directory / symbol / f"{label}.json")
        if meta["artifact"]:
            frame = pd.read_parquet(directory / symbol / meta["artifact"], columns=["time_msc", "bid", "ask"])
            frames.append(frame.loc[(frame.time_msc >= left) & (frame.time_msc < right)])
    if not frames:
        return pd.DataFrame(columns=["time_msc", "bid", "ask"])
    return pd.concat(frames, ignore_index=True)


def compare_sequence(old, new):
    old_time = old.source_time_msc.to_numpy(dtype="int64")
    new_time = new.time_msc.to_numpy(dtype="int64")
    same_count = len(old) == len(new)
    times_equal = same_count and np.array_equal(old_time, new_time)
    quotes_equal = times_equal and np.array_equal(old[["bid", "ask"]].to_numpy(), new[["bid", "ask"]].to_numpy())
    result = {"old_rows": len(old), "new_rows": len(new), "source_times_identical": times_equal,
              "ordered_bid_ask_identical": bool(quotes_equal)}
    if times_equal and len(old):
        result["max_abs_bid_difference"] = float(np.max(np.abs(old.bid.to_numpy() - new.bid.to_numpy())))
        result["max_abs_ask_difference"] = float(np.max(np.abs(old.ask.to_numpy() - new.ask.to_numpy())))
    return result


def build_audit(directory, reference_manifest, isolation_manifest):
    directory = Path(directory).resolve()
    contract = read(directory / "contract.json")
    binding = read(directory / "binding.json")
    if contract["engine_dataset_ready"] or contract["clock_admitted"] or binding["trade_allowed"] or not binding["tradeapi_disabled"]:
        raise ValueError("Unexpected admission or trading state")
    if contract["server"] != binding["server"]:
        raise ValueError("Source server mismatch")
    records, stats, monthly = [], defaultdict(Counter), defaultdict(Counter)
    start, end = map(datetime.fromisoformat, (contract["source_epoch_start"], contract["source_epoch_end_exclusive"]))
    for symbol in contract["symbols"]:
        day = start
        while day < end:
            label = day.date().isoformat()
            path = directory / symbol / f"{label}.json"
            meta = read(path)
            if meta["symbol"] != symbol or meta["server"] != binding["server"] or meta["source_epoch_day"] != label:
                raise ValueError("Day identity mismatch")
            records.append({"metadata_path": str(path), "metadata_sha256": digest(path),
                            "symbol": symbol, "source_epoch_day": label, "status": meta["status"], "rows": meta["rows"]})
            count = stats[symbol]
            count["calendar_days"] += 1
            count[meta["status"]] += 1
            count["rows"] += meta["rows"]
            monthly[f"{label[:7]}:{symbol}"]["rows"] += meta["rows"]
            monthly[f"{label[:7]}:{symbol}"]["nonempty_days"] += meta["rows"] > 0
            if meta["artifact"]:
                price_path = directory / symbol / meta["artifact"]
                if price_path.parent != directory / symbol or price_path.name != f"{label}.parquet":
                    raise ValueError("Unsafe raw price artifact path")
                if digest(price_path) != meta["sha256"] or price_path.stat().st_size != meta["bytes"]:
                    raise ValueError("Raw price bytes mismatch")
                frame = pd.read_parquet(price_path)
                inspected = inspect_ticks(frame)
                if any(meta[key] != value for key, value in inspected.items()):
                    raise ValueError("Stored quote diagnostics disagree with actual data")
                if not ((frame.time_msc >= int(day.timestamp() * 1000)) &
                        (frame.time_msc < int((day + timedelta(days=1)).timestamp() * 1000))).all():
                    raise ValueError("Raw quotes outside named source-epoch day")
                records[-1].update(artifact_path=str(price_path), sha256=meta["sha256"], bytes=meta["bytes"])
                for key in ("invalid_bid_ask_rows", "time_reversals", "second_millisecond_mismatches", "bytes"):
                    count[key] += meta[key]
                del frame
            day += timedelta(days=1)
        print(json.dumps({"event": "raw_symbol_verified", "symbol": symbol, **dict(count)}), flush=True)
    references, comparisons = read(reference_manifest), []
    for record in references["tick_source_selection"]["selected_records"]:
        old_path, old_meta_path = Path(record["path"]), Path(record["metadata_path"])
        if digest(old_path) != record["sha256"] or digest(old_meta_path) != record["metadata_sha256"]:
            raise ValueError("Reference bytes changed")
        old_meta = read(old_meta_path)
        offset = old_meta["utc_offset_seconds"]
        old = pd.read_parquet(old_path, columns=["source_time_msc", "time_msc", "bid", "ask"])
        new = raw_sequence(directory, record["symbol"], record["day"], offset)
        row = {"symbol": record["symbol"], "utc_day": record["day"],
            "reference_path": str(old_path), "reference_sha256": record["sha256"],
            "reference_metadata_path": str(old_meta_path), "reference_metadata_sha256": record["metadata_sha256"],
            "offset_seconds_from_reference_only": offset,
            "reference_stored_clock_arithmetic_matches": bool(np.array_equal(
                old.time_msc.to_numpy(), old.source_time_msc.to_numpy() - offset * 1000)),
            **compare_sequence(old, new), "annual_clock_admitted": False}
        comparisons.append(row)
        del old, new
    isolation = read(isolation_manifest)
    original_checks = [{"path": path, "sha256": sha, "unchanged": digest(path) == sha}
                       for path, sha in isolation["source_hashes"].items()]
    original_checks += [{"path": row["source_path"], "sha256": row["sha256"],
                         "unchanged": digest(row["source_path"]) == row["sha256"]}
                        for row in isolation["copied_native_files"]]
    result = {"schema_version": "annual_raw_mt5_audit_v1", "inputs": {
        "raw_dir": str(directory), "contract_sha256": digest(directory / "contract.json"),
        "binding_sha256": digest(directory / "binding.json"),
        "reference_manifest_path": str(Path(reference_manifest).resolve()), "reference_manifest_sha256": digest(reference_manifest),
        "isolation_manifest_sha256": digest(isolation_manifest), "audit_implementation_sha256": digest(__file__)},
        "summary": {"symbols": {key: dict(value) for key, value in stats.items()},
            "calendar_symbol_days": len(records), "reference_days_compared": len(comparisons),
            "reference_days_identical": sum(row["ordered_bid_ask_identical"] for row in comparisons),
            "reference_rows_compared": sum(row["old_rows"] for row in comparisons),
            "original_source_files_checked": len(original_checks),
            "original_source_files_unchanged": all(row["unchanged"] for row in original_checks),
            "engine_admitted": 0, "annual_clock_admitted": False},
        "monthly": {key: dict(value) for key, value in sorted(monthly.items())},
        "raw_day_artifacts": records, "reference_comparisons": comparisons,
        "original_source_checks": original_checks,
        "limitations": ["Same-read consistency does not prove broker history completeness.",
            "Empty days are explicit; market closure is not inferred from empty results.",
            "Existing summer clock references do not prove the offset of every earlier day.",
            "Current account/symbol metadata is not a historical cost or money contract.",
            "Per-signal full-horizon admission and edited-message clocks remain separate work."]}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument("--reference-manifest", required=True, type=Path)
    parser.add_argument("--isolation-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Audit output already exists")
    result = build_audit(args.raw_dir, args.reference_manifest, args.isolation_manifest)
    immutable_json(args.output, result)
    print(json.dumps(result["summary"], indent=2), flush=True)


if __name__ == "__main__":
    main()
