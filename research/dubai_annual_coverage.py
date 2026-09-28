"""Offline annual clock/quote diagnostics. Never admit data or run strategies."""

from collections import Counter, OrderedDict, defaultdict
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pyarrow

from broker_tick_clock import normalize_server_msc
from research.causal_replay import align_conversion_quotes, fx_validation_contract
from research.dubai_annual_universe import _bytes as universe_bytes, load_catalog, rolling_cohorts
from research.strategy_study import _coverage


ROOT = Path(__file__).resolve().parents[1]
DAY_MS = 86_400_000
CLOCKS = {
    "publication_reference": "publication_reference_utc",
    "known_unedited_component": "known_unedited_component_utc",
    "known_revision_or_initial_component": "known_revision_or_initial_component_utc",
    "observed_receipt": "received_utc",
}
SOURCES = (
    "research/dubai_annual_coverage.py", "tools/audit_dubai_annual_coverage.py",
    "research/dubai_annual_universe.py", "research/dubai_export_catalog.py",
    "research/telegram_export.py", "broker_tick_clock.py", "research/strategy_study.py",
    "research/causal_replay.py", "research/dubai_iterative/dataset.py",
)


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("explicit timestamp timezone required")
    return parsed.astimezone(timezone.utc)


def msc(value):
    stamp = pd.Timestamp(utc(value))
    if stamp.value % 1_000_000:
        raise ValueError("sub-millisecond timestamp unsupported")
    return stamp.value // 1_000_000


def iso_msc(value):
    return pd.Timestamp(int(value), unit="ms", tz="UTC").isoformat().replace("+00:00", "Z")


def normalize_raw_clock(frame, segments):
    """Invert a declared piecewise clock; reject ambiguous or unmapped epochs."""
    if frame.time_msc.dtype.kind not in "iu" or frame.time.dtype.kind not in "iu":
        raise ValueError("raw clock requires integer seconds and milliseconds")
    raw = frame.time_msc.to_numpy(dtype=np.int64)
    values = frame[["bid", "ask"]].to_numpy(dtype=float)
    if (np.any(raw // 1000 != frame.time.to_numpy()) or np.any(np.diff(raw) < 0)
            or not np.isfinite(values).all() or (values <= 0).any() or (values[:, 1] < values[:, 0]).any()):
        raise ValueError("invalid raw time or Bid/Ask")
    result, matches = np.zeros(len(raw), dtype=np.int64), np.zeros(len(raw), dtype=np.int8)
    for segment in segments:
        offset_ms = -normalize_server_msc(0, segment["utc_offset_seconds"])
        candidate = raw - offset_ms
        selected = (candidate >= msc(segment["start_utc"])) & (candidate < msc(segment["end_exclusive_utc"]))
        result[selected] = candidate[selected]
        matches[selected] += 1
    if (matches != 1).any():
        raise ValueError("declared clock maps raw quotes ambiguously or outside its period")
    if (np.diff(result) < 0).any():
        raise ValueError("normalized clock reverses; do not sort to hide it")
    return result * 1_000_000, values[:, 0], values[:, 1]


def offset_at(value, segments):
    matches = [s["utc_offset_seconds"] for s in segments
               if msc(s["start_utc"]) <= value < msc(s["end_exclusive_utc"])]
    if len(matches) != 1:
        raise ValueError("clock outside declared segments")
    return matches[0]


class RawSource:
    """Bound source-day bytes, with a small read-only cache; no MT5 dependency."""

    def __init__(self, audit_path, segments, watch):
        self.audit = read(audit_path)
        self.directory = Path(self.audit["inputs"]["raw_dir"]).resolve()
        self.segments, self.watch, self.records, self.cache = segments, watch, {}, OrderedDict()
        for name in ("contract", "binding"):
            watch(self.directory / f"{name}.json", self.audit["inputs"][f"{name}_sha256"])
        contract = read(self.directory / "contract.json")
        if contract["clock_admitted"] or contract["engine_dataset_ready"]:
            raise ValueError("expected an unadmitted raw archive")
        for record in self.audit["raw_day_artifacts"]:
            symbol, day = record["symbol"], record["source_epoch_day"]
            key = (symbol, day)
            if key in self.records:
                raise ValueError("duplicate raw source day")
            path = self.directory / symbol / f"{day}.json"
            if path != Path(record["metadata_path"]).resolve():
                raise ValueError("raw metadata path mismatch")
            watch(path, record["metadata_sha256"])
            meta = read(path)
            if (meta["symbol"] != symbol or meta["source_epoch_day"] != day or meta["server"] != contract["server"]
                    or meta["rows"] != record["rows"] or meta["status"] != record["status"]):
                raise ValueError("raw day identity mismatch")
            if meta["artifact"]:
                price = self.directory / symbol / f"{day}.parquet"
                if (price != Path(record["artifact_path"]).resolve() or meta["artifact"] != price.name
                        or meta["sha256"] != record["sha256"]):
                    raise ValueError("raw price identity mismatch")
                watch(price, record["sha256"])
            elif record["rows"] != 0:
                raise ValueError("missing nonempty artifact")
            self.records[key] = record

    def day(self, symbol, day):
        key = (symbol, day)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        record = self.records[key]
        if record["rows"]:
            data = Path(record["artifact_path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError("raw quotes changed before decode")
            frame = pd.read_parquet(io.BytesIO(data), columns=["time", "time_msc", "bid", "ask"])
            start = msc(day + "T00:00:00Z")
            if len(frame) != record["rows"] or not frame.time_msc.between(start, start + DAY_MS - 1).all():
                raise ValueError("source-day row bounds mismatch")
            result = normalize_raw_clock(frame, self.segments)
        else:
            result = (np.array([], dtype=np.int64), np.array([], dtype=float), np.array([], dtype=float))
        for values in result:
            values.setflags(write=False)
        self.cache[key] = result
        if len(self.cache) > 12:
            self.cache.popitem(last=False)
        return result

    def window(self, symbol, start, end):
        offsets = [s["utc_offset_seconds"] * 1000 for s in self.segments]
        left, right = start + min(offsets) - 60_000, end + max(offsets) + 60_000
        pieces, evidence, missing = [], [], []
        for day_number in range(left // DAY_MS, right // DAY_MS + 1):
            day = iso_msc(day_number * DAY_MS)[:10]
            key = (symbol, day)
            evidence.append(f"{symbol}:{day}")
            if key not in self.records:
                missing.append(f"{symbol}:{day}")
            else:
                pieces.append(self.day(*key))
        tape = tuple(np.concatenate([p[i] for p in pieces]) if pieces else
                     np.array([], dtype=np.int64 if i == 0 else float) for i in range(3))
        if np.any(np.diff(tape[0]) < 0):
            raise ValueError("unordered source shards")
        return tape, evidence, missing


def coverage_metrics(start, horizon, market, conversion, protocol):
    end = start + horizon * 1000
    segments = protocol["broker_clock"]["segments"]
    offset = offset_at(start, segments)
    config = {key: protocol[key] for key in ("max_market_gap_ms", "max_fx_age_ms", "max_fx_interval_ms")}
    config.update(broker_clock={"utc_offset_seconds": offset}, money={"rollover_hour_server": 0})
    signal = SimpleNamespace(observed_at=utc(iso_msc(start)))
    reasons = _coverage(signal, utc(iso_msc(end)), market, conversion, config)
    if offset_at(end, segments) != offset:
        reasons.append("horizon_crosses_declared_clock_transition")
    times = market[0]
    left, right = np.searchsorted(times, [start * 1_000_000, end * 1_000_000], side="left")
    right = np.searchsorted(times, end * 1_000_000, side="right")
    selected = times[left:right]
    gaps = np.diff(selected) // 1_000_000
    bad = np.flatnonzero(gaps > protocol["max_market_gap_ms"])
    fx_bad, max_age, first_fx_bad = None, None, None
    if len(selected) and len(conversion[0]):
        _, _, ages, valid = align_conversion_quotes(selected, conversion,
            max_fx_age_ms=protocol["max_fx_age_ms"], max_fx_interval_ms=protocol["max_fx_interval_ms"])
        fx_bad = int((~valid).sum())
        finite_age = ages[np.isfinite(ages) & (ages >= 0)]
        max_age = float(finite_age.max()) if len(finite_age) else None
        if fx_bad:
            first_fx_bad = iso_msc(selected[np.flatnonzero(~valid)[0]] // 1_000_000)
    first_gap = None
    if len(bad):
        i = bad[0]
        first_gap = {"from_utc": iso_msc(selected[i] // 1_000_000),
                     "to_utc": iso_msc(selected[i + 1] // 1_000_000), "gap_ms": int(gaps[i])}
    return {"coverage_reasons": sorted(set(reasons)), "quote_coverage_pass": not reasons,
        "broker_offset_seconds_at_start": offset, "market_quotes_in_horizon": len(selected),
        "first_quote_delay_ms": int(selected[0] // 1_000_000 - start) if len(selected) else None,
        "last_quote_age_ms": int(end - selected[-1] // 1_000_000) if len(selected) else None,
        "max_internal_market_gap_ms": int(gaps.max()) if len(gaps) else None,
        "market_gaps_over_limit": len(bad), "first_market_gap_over_limit": first_gap,
        "invalid_conversion_points": fx_bad, "max_prior_conversion_age_ms": max_age,
        "first_invalid_conversion_utc": first_fx_bad, "end_utc": iso_msc(end)}


def scenario_membership(entries, rolling):
    folds, usage = [], {}
    for clock, field in CLOCKS.items():
        in_period, outside = [], set()
        for row in entries:
            value = row[field]
            if value is not None:
                if utc(rolling["start"]) <= utc(value) < utc(rolling["end_exclusive"]):
                    in_period.append({**row, "publication_reference_utc": value})
                else:
                    outside.add(row["entry_id"])
        clock_folds, clock_usage = rolling_cohorts(in_period, **rolling)
        folds.extend({"message_clock": clock, **f} for f in clock_folds)
        by_id = {r["entry_id"]: r for r in clock_usage}
        for row in entries:
            key = row["entry_id"]
            usage[(clock, key)] = {**by_id.get(key, {"entry_id": key, "development_folds": [],
                "check_folds": [], "purged_folds": [], "warmup_only": None}),
                "membership_status": "in_period" if key in by_id else
                    "clock_outside_period" if key in outside else "clock_unavailable"}
    return folds, usage


def summarize(rows):
    groups, monthly, folds = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    for row in rows:
        label = f"{row['message_clock']}:{row['horizon_seconds']}"
        targets = [groups[label], monthly[f"{row['publication_reference_utc'][:7]}:{label}"]]
        targets.extend(folds[f"{fold}:{label}"] for fold in row["check_folds"])
        for target in targets:
            target["all_entry_hypotheses"] += 1
            target["clock_available"] += row["start_utc"] is not None
            target["quote_coverage_pass"] += row["quote_coverage_pass"]
            target["initial_at_reference_supported"] += row["initial_at_reference_supported"]
            target["engine_admitted"] += row["engine_admitted"]
            for reason in row["coverage_reasons"]:
                target[f"reason:{reason}"] += 1
    return {"groups": dict(groups), "monthly_by_publication_month": dict(monthly), "check_fold_groups": dict(folds)}


def build_audit(universe_dir, raw_audit_path, protocol_path):
    started = time.monotonic()
    watched = {}

    def watch(path, expected=None):
        path = Path(path).resolve()
        actual = digest(path)
        if expected is not None and actual != expected:
            raise ValueError(f"frozen input changed: {path.name}")
        if str(path) in watched and watched[str(path)] != actual:
            raise ValueError("source changed during audit")
        watched[str(path)] = actual
        return actual

    protocol = read(protocol_path)
    watch(protocol_path)
    if (protocol["schema_version"] != "dubai_annual_coverage_protocol_v1"
            or protocol["broker_clock"]["status"] != "declared_hypothesis"
            or protocol["search_candidates"] != 0 or protocol["engine_admitted"] is not False):
        raise ValueError("only declared, unadmitted, zero-search diagnostics")
    fx_validation_contract(protocol["max_fx_age_ms"], protocol["max_fx_interval_ms"])
    for horizon in protocol["horizons_seconds"]:
        if type(horizon) is not int or not 0 < horizon <= 14_400:
            raise ValueError("horizon outside annual envelope")
    for source in SOURCES:
        watch(ROOT / source)
    reference = protocol["threshold_reference"]
    watch(reference["path"], reference["sha256"])
    old_config = read(reference["path"])
    for key in ("max_market_gap_ms", "max_fx_age_ms", "max_fx_interval_ms"):
        if protocol[key] != old_config[key]:
            raise ValueError("coverage thresholds changed from declared reference")
    universe_dir = Path(universe_dir).resolve()
    manifest = read(universe_dir / "manifest.json")
    watch(universe_dir / "manifest.json", protocol["universe_manifest_sha256"])
    claimed = manifest["universe_identity_sha256"]
    if hashlib.sha256(universe_bytes({k: v for k, v in manifest.items() if k != "universe_identity_sha256"})).hexdigest() != claimed:
        raise ValueError("universe identity mismatch")
    for name, proof in manifest["artifacts"].items():
        if Path(name).name != name:
            raise ValueError("unsafe universe artifact")
        watch(universe_dir / name, proof["sha256"])
    watch(manifest["inputs"]["ledger_path"], manifest["inputs"]["ledger_sha256"])
    catalog_dir = Path(manifest["inputs"]["catalog_dir"])
    watch(catalog_dir / "manifest.json", manifest["inputs"]["catalog_manifest_sha256"])
    catalog, _ = load_catalog(catalog_dir)
    if catalog["catalog_identity_sha256"] != manifest["inputs"]["catalog_identity_sha256"]:
        raise ValueError("catalog identity mismatch")
    entries = [json.loads(line) for line in (universe_dir / "entries.jsonl").read_text(encoding="utf-8").splitlines()]
    if not 0 < len(entries) <= 1000:
        raise ValueError("annual audit entry budget exceeded")
    if len({r["entry_id"] for r in entries}) != len(entries):
        raise ValueError("duplicate entry")
    if any(r["engine_admitted"] or r["chat_id"] != 1642806869 or r["symbol"] != "XAUUSD" for r in entries):
        raise ValueError("unexpected annual entry contract")
    watch(raw_audit_path, protocol["raw_audit_sha256"])
    source = RawSource(raw_audit_path, protocol["broker_clock"]["segments"], watch)
    rolling = manifest["contract"]["rolling_protocol"]
    if max(protocol["horizons_seconds"]) > rolling["horizon_seconds"]:
        raise ValueError("horizon exceeds frozen purge")
    folds, membership = scenario_membership(entries, rolling)
    rows, metrics_cache = [], {}
    for index, entry in enumerate(entries):
        if time.monotonic() - started > 900:
            raise TimeoutError("annual audit exceeded 15 minutes; no complete archive")
        for clock, field in CLOCKS.items():
            value = entry[field]
            for horizon in protocol["horizons_seconds"]:
                row = {key: entry[key] for key in ("entry_id", "chat_id", "symbol", "direction", "message_ids",
                    "publication_reference_utc", "initial_at_reference_supported", "forwarded", "review_flags", "integrity_issues")}
                row.update(message_clock=clock, start_utc=value, horizon_seconds=horizon,
                    broker_clock_status="declared_hypothesis", clock_empirically_verified=False,
                    engine_admitted=False, causal_grouping_admitted=False,
                    money_contract_verified=False, **membership[(clock, entry["entry_id"])])
                if value is None:
                    row.update(quote_coverage_pass=False, coverage_reasons=["message_clock_unavailable"], raw_source_days=[])
                else:
                    start = msc(value)
                    cache_key = (start, horizon)
                    if cache_key not in metrics_cache:
                        market, market_ids, market_missing = source.window("XAUUSD", start, start + horizon * 1000)
                        fx, fx_ids, fx_missing = source.window("EURUSD", start, start + horizon * 1000)
                        result = coverage_metrics(start, horizon, market, fx, protocol)
                        result["raw_source_days"] = market_ids + fx_ids
                        result["missing_raw_source_days"] = market_missing + fx_missing
                        if market_missing or fx_missing:
                            result["coverage_reasons"] = sorted(set(result["coverage_reasons"] + ["raw_source_day_missing"]))
                            result["quote_coverage_pass"] = False
                        metrics_cache[cache_key] = result
                    row.update(metrics_cache[cache_key])
                rows.append(row)
        if (index + 1) % 50 == 0:
            print(json.dumps({"event": "entries_checked", "entries": index + 1, "total": len(entries)}), flush=True)
    references = audit_references(source, watch)
    # Recheck all bound bytes, including raw files not needed by individual windows.
    for path, sha in list(watched.items()):
        watch(path, sha)
    if load_catalog(catalog_dir)[0] != catalog:
        raise ValueError("catalog changed during audit")
    return {"protocol": protocol, "inputs": {"universe_identity_sha256": claimed,
        "watched_files": watched, "protected_archive_dirs": [str(universe_dir), str(catalog_dir),
            str(source.directory), catalog["inputs"]["admission_dir"]]}, "environment": {"python": platform.python_version(),
        "numpy": np.__version__, "pandas": pd.__version__, "pyarrow": pyarrow.__version__},
        "rows": rows, "folds": folds, "clock_references": references,
        "summary": {"entries": len(entries), "rows": len(rows), "engine_admitted": 0,
            "clock_evidence_tier": "documented_rule_hypothesis_with_limited_prior_reference_corroboration",
            "reference_methods": dict(Counter(r["method"] for r in references)),
            "reference_offsets_match_declared_rule": all(r["offset_matches_rule"] for r in references),
            "fill_reference_checks": dict(Counter(r["recheck_status"] for r in references)), **summarize(rows)}}


def audit_references(source, watch):
    """Corroborate retained sidecar references, not independently re-prove fills."""
    rows = []
    for prior in source.audit["reference_comparisons"]:
        watch(prior["reference_metadata_path"], prior["reference_metadata_sha256"])
        watch(prior["reference_path"], prior["reference_sha256"])
        meta = read(prior["reference_metadata_path"])
        day = prior["utc_day"]
        rule_offset = offset_at(msc(day + "T12:00:00Z"), source.segments)
        row = {"symbol": prior["symbol"], "utc_day": day,
            "method": meta["offset_detection_method"], "prior_metadata_path": prior["reference_metadata_path"],
            "prior_metadata_sha256": prior["reference_metadata_sha256"],
            "offset_seconds": meta["utc_offset_seconds"], "offset_matches_rule": meta["utc_offset_seconds"] == rule_offset,
            "recheck_status": "retained_metadata_only", "annual_clock_verified": False,
            "original_fill_logs_revalidated": False}
        reference = meta["offset_reference"]
        if row["method"] == "fill_anchor":
            anchor = msc(reference["anchor_time_utc"])
            raw = reference["raw_time_msc"]
            expected_ns = normalize_server_msc(raw, meta["utc_offset_seconds"]) * 1_000_000
            tape, evidence, missing = source.window(prior["symbol"], anchor, anchor + 1)
            indices = np.flatnonzero(tape[0] == expected_ns)
            side = {"bid": 1, "ask": 2}[reference["quote_side"]]
            delta = min((abs(float(tape[side][i]) - reference["fill_price"]) for i in indices), default=None)
            time_delta = abs(expected_ns // 1_000_000 - anchor)
            matched = (not missing and delta is not None and abs(delta - reference["price_delta"]) < 1e-9
                       and time_delta == reference["time_delta_ms"])
            row.update(raw_time_msc=raw, anchor_time_utc=reference["anchor_time_utc"], quote_side=reference["quote_side"],
                quote_found_count=len(indices), price_delta=delta, time_delta_ms=time_delta, raw_source_days=evidence,
                recheck_status="stored_fill_reference_reproduced" if matched else "stored_fill_reference_mismatch")
        rows.append(row)
    return rows


def write_audit(result, output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("immutable audit output already exists")
    protected = [Path(path).resolve() for path in result["inputs"]["watched_files"]]
    roots = [Path(path).resolve() for path in result["inputs"]["protected_archive_dirs"]]
    if (any(path.is_relative_to(output) for path in protected)
            or any(output.is_relative_to(root) for root in roots)):
        raise ValueError("output overlaps frozen input archive")
    payloads = {"summary.json": encoded(result["summary"]), "protocol.json": encoded(result["protocol"]),
        "coverage.jsonl": b"".join(encoded(r) for r in result["rows"]),
        "rolling_folds.jsonl": b"".join(encoded(r) for r in result["folds"]),
        "clock_references.jsonl": b"".join(encoded(r) for r in result["clock_references"])}
    manifest = {"schema_version": "dubai_annual_coverage_v1", "engine_admitted": False,
        "inputs": result["inputs"], "environment": result["environment"],
        "artifacts": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in payloads.items()}}
    manifest["audit_identity_sha256"] = hashlib.sha256(encoded(manifest)).hexdigest()
    payloads["manifest.json"] = encoded(manifest)
    output.mkdir(parents=True)
    for name, data in payloads.items():
        with (output / name).open("xb") as stream:
            stream.write(data)
    return manifest
