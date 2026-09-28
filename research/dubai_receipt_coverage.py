"""Bounded quote coverage for retained receipts; no strategy evaluations."""

from collections import Counter, defaultdict
from pathlib import Path
import time

from research.causal_replay import utc
from research.dubai_annual_coverage import RawSource, coverage_metrics, msc
from research.dubai_annual_dataset import AnnualInputs
from research.dubai_clock_audit import _write
from research.dubai_entry_probe import _archive
from research.dubai_receipt_inventory import verify_inventory
from research.strategy_study import ROOT, _check_current, _digest, _read, _sha, _verify_sources, _watch


SCHEMA = "dubai_receipt_quote_coverage_v1"
HORIZONS = (1200, 3600, 7200, 14400)
BUDGET = {"max_windows": 1200, "max_source_quotes": 150_000_000,
          "max_window_quotes": 300_000_000, "max_wall_seconds": 1800}
SOURCES = ("research/dubai_receipt_coverage.py", "tools/audit_dubai_receipt_coverage.py",
           "research/dubai_receipt_inventory.py", "tools/audit_dubai_receipts.py")
_IMPORTED = {name: _digest(ROOT / name) for name in SOURCES if (ROOT / name).is_file()}
ARTIFACTS = {"protocol.json", "coverage.json", "summary.json"}
IDENTITY_FIELDS = ("signal_id", "source_evidence_tier", "received_utc", "published_utc",
                   "initial_receipt_supported", "fresh_initial_receipt_within_5s", "first_source_row_sha256")
LIMITATIONS = [
    "Receipt/source evidence tiers remain separate. Quote coverage does not manufacture absent chat or revision evidence.",
    "Every retained directional ID has one row per horizon; unsupported receipts are not priced and are not silently deleted.",
    "Freshness is descriptive, independent of outcomes. Missing/changed later exports are not an eligibility filter.",
    "Unchanged historical Bid/Ask, causal prior EURUSD conversion and coverage thresholds under the declared broker clock.",
    "Coverage endpoints/gaps do not certify a complete provider feed, all historical ticks, real fills or account money.",
    "No entry/exit strategy, provider stop backdating, optimization, candidate selection, capital or live activation.",
    "Retrospective data reuse, not fresh OOS. Windows crossing rollover or declared clock changes remain blocked.",
]


class CoverageBudget:
    def __init__(self):
        self.started, self.source_quotes, self.window_quotes = time.monotonic(), 0, 0

    def check(self):
        if time.monotonic() - self.started >= BUDGET["max_wall_seconds"]:
            raise TimeoutError("receipt coverage wall budget exhausted")

    def charge_source(self, count):
        self.check()
        if self.source_quotes + count > BUDGET["max_source_quotes"]:
            raise ValueError("receipt coverage source quote budget exhausted")
        self.source_quotes += count

    def charge_window(self, count):
        self.check()
        if self.window_quotes + count > BUDGET["max_window_quotes"]:
            raise ValueError("receipt coverage window quote budget exhausted")
        self.window_quotes += count


class BoundedRawSource(RawSource):
    def __init__(self, *args, budget, **kwargs):
        self.budget = budget
        super().__init__(*args, **kwargs)

    def day(self, symbol, day):
        if (symbol, day) not in self.cache:
            self.budget.charge_source(self.records[(symbol, day)]["rows"])
        return super().day(symbol, day)


def window_row(entry, horizon, inputs, budget):
    if type(horizon) is not int or horizon not in HORIZONS:
        raise ValueError("undeclared receipt horizon")
    budget.check()
    row = {k: entry[k] for k in IDENTITY_FIELDS}
    row.update(horizon_seconds=horizon, status="receipt_unsupported", reasons=list(entry["entry_reasons"]), coverage=None)
    if not entry["initial_receipt_supported"]:
        if not row["reasons"]:
            raise ValueError("unsupported receipt lacks a reason")
        return row
    if row["reasons"]:
        raise ValueError("supported receipt contains unresolved input reasons")
    stamp = msc(entry["received_utc"])
    market, market_ids, market_missing = inputs.raw.window("XAUUSD", stamp, stamp + horizon * 1000)
    fx, fx_ids, fx_missing = inputs.raw.window("EURUSD", stamp, stamp + horizon * 1000)
    metrics = coverage_metrics(stamp, horizon, market, fx, inputs.protocol)
    metrics.update(raw_source_days=market_ids + fx_ids, missing_raw_source_days=market_missing + fx_missing)
    if market_missing or fx_missing:
        metrics["coverage_reasons"] = sorted(set(metrics["coverage_reasons"] + ["raw_source_day_missing"]))
        metrics["quote_coverage_pass"] = False
    budget.charge_window(metrics["market_quotes_in_horizon"])
    return dict(row, status="quote_window_ready" if metrics["quote_coverage_pass"] else "quote_window_blocked",
                reasons=metrics["coverage_reasons"], coverage=metrics)


def _group_summary(rows):
    supported = [r for r in rows if r["initial_receipt_supported"]]
    fresh = [r for r in rows if r["fresh_initial_receipt_within_5s"]]

    def complete_days(values):
        days = defaultdict(list)
        for row in values:
            days[row["received_utc"][:10]].append(row["status"] == "quote_window_ready")
        return {"retained_days": len(days), "complete_days": sum(all(v) for v in days.values())}

    return {"retained_identities": len(rows), "supported_receipts": len(supported), "fresh_initial_receipts": len(fresh),
        "quote_window_ready": sum(r["status"] == "quote_window_ready" for r in rows),
        "fresh_receipt_quote_ready": sum(r["status"] == "quote_window_ready" for r in fresh),
        "status_counts": dict(sorted(Counter(r["status"] for r in rows).items())),
        "reason_counts": dict(sorted(Counter(reason for r in rows for reason in r["reasons"]).items())),
        "all_retained_days": complete_days(rows), "supported_receipt_days": complete_days(supported),
        "fresh_supported_receipt_days": complete_days(fresh)}


def coverage_summary(entries, rows, *, horizons=HORIZONS):
    entries_by_id = {r["signal_id"]: r for r in entries}
    expected = {(r["signal_id"], h) for r in entries for h in horizons}
    keys = [(r["signal_id"], r["horizon_seconds"]) for r in rows]
    if len(entries_by_id) != len(entries) or len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("missing, duplicate or mixed receipt coverage matrix")
    for row in rows:
        if any(row[key] != entries_by_id[row["signal_id"]][key] for key in IDENTITY_FIELDS):
            raise ValueError("coverage receipt identity/evidence differs")
        if row["status"] not in {"quote_window_ready", "quote_window_blocked", "receipt_unsupported"}:
            raise ValueError("unknown receipt coverage status")
        if row["initial_receipt_supported"]:
            metrics = row["coverage"]
            if (metrics is None or row["reasons"] != metrics["coverage_reasons"]
                    or (row["status"] == "quote_window_ready") != (not row["reasons"])
                    or metrics["quote_coverage_pass"] != (row["status"] == "quote_window_ready")):
                raise ValueError("inconsistent quote coverage verdict")
        elif row["status"] != "receipt_unsupported" or row["coverage"] is not None or not row["reasons"]:
            raise ValueError("unsupported receipt incorrectly priced")
    tiers, months = sorted({r["source_evidence_tier"] for r in entries}), sorted({r["received_utc"][:7] for r in entries})
    return {"schema_version": SCHEMA, "retained_identities": len(entries), "window_rows": len(rows),
        "horizons": {str(h): _group_summary([r for r in rows if r["horizon_seconds"] == h]) for h in sorted(horizons)},
        "by_evidence_tier": {tier: {str(h): _group_summary([r for r in rows if r["horizon_seconds"] == h
            and r["source_evidence_tier"] == tier]) for h in sorted(horizons)} for tier in tiers},
        "by_receipt_month": {month: {str(h): _group_summary([r for r in rows if r["horizon_seconds"] == h
            and r["received_utc"].startswith(month)]) for h in sorted(horizons)} for month in months},
        "status_counts": dict(sorted(Counter(r["status"] for r in rows).items())),
        "engine_dataset_ready": False, "money_contract_verified": False, "account_currency_money_verified": False,
        "strategy_evaluations": 0, "orders_sent": 0, "selected_policy": None, "limitations": LIMITATIONS}


def _current(implementation):
    _check_current(implementation)
    if any(_digest(ROOT / name) != value for name, value in _IMPORTED.items()):
        raise ValueError("loaded receipt coverage implementation changed")


def _inputs(receipt_dir, coverage_dir, raw_audit_path, budget):
    receipt_dir = Path(receipt_dir).resolve()
    proof = verify_inventory(receipt_dir)
    receipt_protocol = _read(receipt_dir / "protocol.json")
    inputs = AnnualInputs(receipt_protocol["stream_dir"], coverage_dir, raw_audit_path)
    _archive(receipt_dir, "receipt_inventory_identity_sha256", inputs.watch)
    for name in SOURCES:
        inputs.watch(ROOT / name)
    # Same records and clock; this subclass only adds a decode budget.
    inputs.raw = BoundedRawSource(raw_audit_path, inputs.protocol["broker_clock"]["segments"], inputs.watch, budget=budget)
    inputs.verify_sources()
    _current(inputs.implementation)
    entries = _read(receipt_dir / "inventory.json")["entries"]
    if not entries or len(entries) * len(HORIZONS) > BUDGET["max_windows"]:
        raise ValueError("receipt coverage matrix outside the frozen budget")
    return inputs, entries, proof


def _compute(entries, inputs, budget, progress=None):
    rows = []
    ordered = sorted(entries, key=lambda r: (utc(r["received_utc"]), r["signal_id"]))
    for i, entry in enumerate(ordered, 1):
        for horizon in HORIZONS:
            rows.append(window_row(entry, horizon, inputs, budget))
        if progress and (i % 20 == 0 or i == len(ordered)):
            progress({"completed_identities": i, "retained_identities": len(ordered),
                      "source_quotes_decoded": budget.source_quotes, "window_market_quotes": budget.window_quotes})
    return rows, coverage_summary(entries, rows)


def run_coverage(receipt_dir, coverage_dir, raw_audit_path, output_dir, *, progress=None):
    budget, output = CoverageBudget(), Path(output_dir).resolve()
    if output.exists():
        raise ValueError("immutable receipt coverage output already exists")
    inputs, entries, receipt_proof = _inputs(receipt_dir, coverage_dir, raw_audit_path, budget)
    for root in [Path(receipt_dir), *inputs.protected_dirs]:
        if output.is_relative_to(root.resolve()) or root.resolve().is_relative_to(output):
            raise ValueError("receipt coverage output overlaps a protected archive")
    if any(Path(path).is_relative_to(output) for path in inputs.watched):
        raise ValueError("receipt coverage output contains a source")
    protocol = {"schema_version": SCHEMA, "receipt_dir": str(Path(receipt_dir).resolve()), "receipt_proof": receipt_proof,
        "coverage_dir": str(Path(coverage_dir).resolve()), "raw_audit_path": str(Path(raw_audit_path).resolve()),
        "horizons_seconds": HORIZONS, "budget": BUDGET, "retained_identities": len(entries),
        "implementation": inputs.implementation, "coverage_protocol": inputs.protocol,
        "money_assumptions_reference_only": inputs.reference["money"],
        "selection": "all_retained_ids_prices_only_for_supported_initial_receipts_no_export_filter",
        "data_use": "retrospective_quote_coverage_only", "strategy_evaluations": 0, "limitations": LIMITATIONS}
    output.mkdir(parents=True, exist_ok=False)
    _write(output / "protocol.json", protocol)
    rows, summary = _compute(entries, inputs, budget, progress)
    inputs.verify_sources()
    _current(inputs.implementation)
    budget.check()
    summary["usage"] = {"source_quotes_decoded": budget.source_quotes, "window_market_quotes": budget.window_quotes,
                        "elapsed_seconds": time.monotonic() - budget.started}
    _write(output / "coverage.json", rows)
    _write(output / "summary.json", summary)
    manifest = {"schema_version": SCHEMA, "status": "complete_receipt_quote_coverage_only",
        "inputs": {"watched_files": inputs.watched},
        "artifacts": {name: {"sha256": _digest(output / name)} for name in sorted(ARTIFACTS)}}
    manifest["receipt_coverage_identity_sha256"] = _sha(manifest)
    _write(output / "manifest.json", manifest)
    return summary


def verify_coverage(output_dir):
    output, watched, budget = Path(output_dir).resolve(), {}, CoverageBudget()
    if {p.name for p in output.iterdir()} != ARTIFACTS | {"manifest.json"}:
        raise ValueError("incomplete or mixed receipt coverage archive")

    def watch(path, expected=None):
        budget.check()
        _watch(path, _digest(path) if expected is None else expected, watched)

    manifest = _archive(output, "receipt_coverage_identity_sha256", watch)
    if manifest["schema_version"] != SCHEMA or set(manifest["artifacts"]) != ARTIFACTS:
        raise ValueError("unsupported receipt coverage archive")
    protocol = _read(output / "protocol.json")
    _current(protocol["implementation"])
    if (protocol["horizons_seconds"] != list(HORIZONS) or protocol["budget"] != BUDGET
            or protocol["strategy_evaluations"] != 0 or protocol["limitations"] != LIMITATIONS):
        raise ValueError("receipt coverage protocol changed")
    inputs, entries, proof = _inputs(protocol["receipt_dir"], protocol["coverage_dir"], protocol["raw_audit_path"], budget)
    if (proof != protocol["receipt_proof"] or len(entries) != protocol["retained_identities"]
            or inputs.protocol != protocol["coverage_protocol"] or inputs.implementation != protocol["implementation"]):
        raise ValueError("receipt coverage input identity changed")
    rows, summary = _compute(entries, inputs, budget)
    archived = _read(output / "summary.json")
    if rows != _read(output / "coverage.json") or summary != {k: v for k, v in archived.items() if k != "usage"}:
        raise ValueError("receipt quote coverage recomputation differs")
    if (budget.source_quotes != archived["usage"]["source_quotes_decoded"]
            or budget.window_quotes != archived["usage"]["window_market_quotes"]):
        raise ValueError("receipt coverage resource accounting differs")
    inputs.verify_sources()
    _verify_sources(watched)
    _current(protocol["implementation"])
    budget.check()
    return {"status": "verified_receipt_quote_coverage_only",
        "receipt_coverage_identity_sha256": manifest["receipt_coverage_identity_sha256"],
        "retained_identities": len(entries), "window_rows": len(rows), "strategy_evaluations": 0}
