"""Evidence-tiered receipt adapter and four unchanged, isolated controls."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import time

from research.causal_replay import make_path, utc
from research.dubai_annual_coverage import msc
from research.dubai_annual_dataset import AnnualInputs
from research.dubai_clock_audit import _write
from research.dubai_entry_probe import _archive, _rows, compare_results
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_paired_clocks import fixed_controls, verify_controls as verify_paired
from research.dubai_receipt_coverage import BoundedRawSource, verify_coverage, window_row
from research.dubai_shared_lab import _evaluate, execution_profiles
from research.execution_profile import execution_from_mapping
from research.strategy_study import ROOT, _check_current, _digest, _encode, _read, _sha, _verify_sources, _watch


SCHEMA = "dubai_expanded_receipt_controls_v1"
HORIZON = 1200
BUDGET = {"max_evaluations": 7000, "max_engine_quote_visits": 150_000_000,
          "max_source_quotes": 150_000_000, "max_wall_seconds": 3600, "max_path_quotes": 3_000_000}
EXTRA_COST = Decimal("10.00")
SOURCES = ("research/dubai_receipt_controls.py", "tools/run_dubai_receipt_controls.py")
_IMPORTED = {name: _digest(ROOT / name) for name in SOURCES if (ROOT / name).is_file()}
ARTIFACTS = {"protocol.json", "inventory.json", "results.jsonl", "summary.json"}
EVIDENCE_FIELDS = ("signal_id", "message_id", "direction", "received_utc", "published_utc",
    "source_evidence_tier", "first_source_line_1based", "first_source_row_sha256",
    "raw_chat_id", "raw_message_revision_id", "initial_receipt_supported", "fresh_initial_receipt_within_5s")
PRIOR_FIELDS = ("case_id", "control", "profile", "strategy_fingerprint", "status", "reasons", "engines", "mismatches")
LIMITATIONS = [
    "Four unchanged own-rule controls; isolated 0.01-lot positions, not discovery, portfolio or account equity.",
    "Original message evidence tiers remain explicit; absent numeric chat or canonical revision is never manufactured.",
    "Every retained directional identity remains in the matrix. Later exports or provider outcomes are not input filters.",
    "Prices only for supported initial receipts and covered windows; unfilled is zero, unknown data is not zero.",
    "Historical Bid/Ask and causal prior EURUSD under the unchanged declared clock and coverage contract.",
    "Reference/adverse fills and 10 EUR/lot extra round-trip costs are hypotheses, not broker-verified account money.",
    "Known-signal sums and closed-day drawdown exclude missing data and concurrent floating account equity.",
    "All histories and imported controls are reused retrospectively, not fresh OOS; no live activation or selected policy.",
    "Archive verification rebuilds inputs/paths and checks hashes, matrix, parity and prior exact rows; it does not rerun every engine.",
]


@dataclass(frozen=True)
class ReceiptTrigger:
    """Own-rule path interface, not a canonical Telegram revision."""

    signal_id: str
    channel: str
    direction: str
    observed_at: datetime
    published_at: datetime
    source_evidence_tier: str
    source_row_sha256: str
    provider_events: tuple = ()


def receipt_trigger(entry):
    mid, tier, sha = entry["message_id"], entry["source_evidence_tier"], entry["first_source_row_sha256"]
    if (type(mid) is not int or mid <= 0 or entry["signal_id"] != f"canal1_{mid}"
            or entry["direction"] not in {"BUY", "SELL"} or entry["initial_receipt_supported"] is not True
            or entry["retained_revision_is_edit"] is not False or entry["entry_reasons"]
            or type(entry["first_source_line_1based"]) is not int or entry["first_source_line_1based"] <= 0
            or not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)):
        raise ValueError("unsupported or inconsistent receipt input")
    numeric, canonical = entry["numeric_chat_observed"], entry["canonical_revision_observed"]
    chat, revision = entry["raw_chat_id"], entry["raw_message_revision_id"]
    if tier == "legacy_channel_tag_only":
        valid = numeric is False and canonical is False and chat is None and revision is None
    elif tier == "legacy_observed_chat_without_revision":
        valid = numeric is True and canonical is False and type(chat) is int and chat == -1001642806869 and revision is None
    elif tier == "canonical_chat_revision":
        valid = (numeric is True and canonical is True and type(chat) is int and chat == -1001642806869
                 and isinstance(revision, str) and bool(revision))
    else:
        valid = False
    if not valid:
        raise ValueError("receipt evidence tier or observed identity differs")
    observed, published = utc(entry["received_utc"]), utc(entry["published_utc"])
    if published > observed:
        raise ValueError("receipt precedes publication")
    if entry["fresh_initial_receipt_within_5s"] is not ((observed - published).total_seconds() <= 5):
        raise ValueError("receipt freshness differs from its clocks")
    return ReceiptTrigger("receipt:" + entry["signal_id"], "canal1", entry["direction"], observed, published, tier, sha)


class RunBudget:
    def __init__(self):
        self.started = time.monotonic()
        self.evaluations = self.quote_visits = self.source_quotes = self.window_quotes = 0

    def check(self):
        if time.monotonic() - self.started >= BUDGET["max_wall_seconds"]:
            raise TimeoutError("receipt control wall budget exhausted")

    def charge(self, quotes):
        self.check()
        if (self.evaluations + 1 > BUDGET["max_evaluations"]
                or self.quote_visits + quotes > BUDGET["max_engine_quote_visits"]):
            raise ValueError("receipt control engine budget exhausted")
        self.evaluations += 1
        self.quote_visits += quotes

    def charge_source(self, quotes):
        self.check()
        if self.source_quotes + quotes > BUDGET["max_source_quotes"]:
            raise ValueError("receipt control source quote budget exhausted")
        self.source_quotes += quotes

    def charge_window(self, quotes):
        self.check()
        if self.window_quotes + quotes > BUDGET["max_engine_quote_visits"]:
            raise ValueError("receipt control window budget exhausted")
        self.window_quotes += quotes


def load_receipt_path(entry, expected, inputs, base, budget):
    trigger = receipt_trigger(entry) if entry["initial_receipt_supported"] else None
    actual = window_row(entry, HORIZON, inputs, budget)
    if actual != expected:
        raise ValueError("frozen receipt coverage differs before engine")
    audit = {k: entry[k] for k in EVIDENCE_FIELDS}
    audit.update(case_id="receipt:" + entry["signal_id"], status="data_blocked", reasons=list(actual["reasons"]),
                 coverage=actual["coverage"])
    if actual["status"] != "quote_window_ready":
        return None, audit
    if actual["coverage"]["market_quotes_in_horizon"] > BUDGET["max_path_quotes"]:
        raise ValueError("receipt path quote budget exhausted")
    stamp = msc(entry["received_utc"])
    market, market_ids, unused = inputs.raw.window("XAUUSD", stamp, stamp + HORIZON * 1000)
    fx, fx_ids, unused = inputs.raw.window("EURUSD", stamp, stamp + HORIZON * 1000)
    try:
        path = make_path(trigger, base, market=market, conversion=fx,
            cutoff=trigger.observed_at + timedelta(seconds=HORIZON),
            contract_size=inputs.reference["money"]["contract_size"], currency_digits=2,
            max_fx_age_ms=inputs.protocol["max_fx_age_ms"], max_fx_interval_ms=inputs.protocol["max_fx_interval_ms"],
            market_sha256=_sha({"raw": inputs.protocol["raw_audit_sha256"], "days": market_ids}),
            conversion_sha256=_sha({"raw": inputs.protocol["raw_audit_sha256"], "days": fx_ids}))
    except ValueError as exc:
        audit["reasons"].append(str(exc))
        return None, audit
    return path, dict(audit, status="data_ready")


def _decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("nonfinite receipt-control money")
    return result


def _money(value):
    return format(value, ".2f") if value is not None else None


def summarize_subset(rows):
    counts, by_day = Counter(r["status"] for r in rows), defaultdict(list)
    if len({r["signal_id"] for r in rows}) != len(rows) or set(counts) - {"simulated", "unfilled", "data_blocked"}:
        raise ValueError("duplicate identity or incident in receipt statistics")
    known, filled, floating, daily = [], 0, [], {}
    for row in rows:
        day = row["received_utc"][:10]
        by_day[day].append(row)
        if row["status"] == "data_blocked":
            if row["engines"]:
                raise ValueError("data-blocked row contains money")
            continue
        result = row["engines"]["oracle"]
        net, volume = _decimal(result["pnl_eur"]), _decimal(result["filled_volume"])
        if (volume < 0 or volume > Decimal(".01") or (row["status"] == "unfilled" and (net or volume))
                or (row["status"] == "simulated" and volume <= 0)):
            raise ValueError("inconsistent control fill envelope")
        value = net - volume * EXTRA_COST
        known.append(value)
        filled += volume > 0
        floating.append(_decimal(result["max_floating_drawdown_eur"]))
        daily[day] = daily.get(day, Decimal(0)) + value
    complete = sorted(day for day, values in by_day.items() if all(r["status"] != "data_blocked" for r in values))
    balance = peak = drawdown = Decimal(0)
    for day in sorted(daily):
        balance += daily[day]
        peak = max(peak, balance)
        drawdown = max(drawdown, peak - balance)
    positive_days = [v for v in daily.values() if v > 0]
    return {"retained": len(rows), "evaluable": len(known), "filled": filled,
        "status_counts": dict(sorted(counts.items())), "retained_days": len(by_day), "complete_days": len(complete),
        "hypothetical_known_net_after_cost_eur": _money(sum(known, Decimal(0))) if known else None,
        "complete_day_net_after_cost_eur": _money(sum((daily[d] for d in complete), Decimal(0))) if complete else None,
        "known_closed_day_drawdown_eur": _money(drawdown) if known else None,
        "worst_known_signal_after_cost_eur": _money(min(known)) if known else None,
        "largest_single_position_floating_drawdown_eur": _money(max(floating)) if floating else None,
        "positive_signals": sum(v > 0 for v in known), "negative_signals": sum(v < 0 for v in known),
        "positive_day_concentration": float(max(positive_days) / sum(positive_days)) if positive_days else None,
        "known_daily_after_cost_eur": {d: _money(v) for d, v in sorted(daily.items())}}


def _key(row):
    return row["case_id"], row["control"], row["profile"]


def compare_prior(rows, prior_rows):
    old = [r for r in prior_rows if r["case_id"].startswith("receipt:")]
    index = {_key(r): r for r in rows}
    if not old or len({_key(r) for r in old}) != len(old):
        raise ValueError("missing or duplicate prior receipt matrix")
    for prior in old:
        current = index.get(_key(prior))
        if current is None or any(current[k] != prior[k] for k in PRIOR_FIELDS):
            raise ValueError("expanded receipt result differs from exact prior row")
    return {"matched_rows": len(old), "matched_identities": len({r["case_id"] for r in old}),
            "all_result_fields_equal": True}


def summarize_results(entries, inventory, rows, controls, profiles, prior_rows):
    expected = {("receipt:" + e["signal_id"], rule, profile) for e in entries for rule in controls for profile in profiles}
    if len({_key(r) for r in rows}) != len(rows) or {_key(r) for r in rows} != expected:
        raise ValueError("missing, duplicate or mixed expanded receipt matrix")
    audits = {r["signal_id"]: r for r in inventory}
    if len(audits) != len(inventory) or set(audits) != {e["signal_id"] for e in entries}:
        raise ValueError("expanded inventory denominator differs")
    for row in rows:
        source = audits[row["signal_id"]]
        if any(row[k] != source[k] for k in EVIDENCE_FIELDS) or row["case_id"] != source["case_id"]:
            raise ValueError("result receipt evidence differs")
        fingerprint = StrategyGenome.from_dict(controls[row["control"]]).fingerprint
        if row["strategy_fingerprint"] != fingerprint:
            raise ValueError("result control fingerprint differs")
        if source["status"] == "data_blocked":
            if row["status"] != "data_blocked" or row["engines"] or row["reasons"] != source["reasons"]:
                raise ValueError("blocked receipt incorrectly simulated")
        else:
            status, reasons, mismatches = compare_results(row["engines"])
            if ((row["status"], row["reasons"], row["mismatches"]) != (status, reasons, mismatches)
                    or status not in {"simulated", "unfilled"}):
                raise ValueError("receipt engine parity or verdict differs")
            for result in row["engines"].values():
                if result["signal_id"] != row["case_id"] or result["strategy_fingerprint"] != fingerprint:
                    raise ValueError("engine result identity differs")
                opened = sum((_decimal(e["volume"]) for e in result["entries"]), Decimal(0))
                closed = sum((_decimal(e["volume"]) for e in result["exits"]), Decimal(0))
                start = utc(row["received_utc"])
                if (opened != closed or opened != _decimal(result["filled_volume"]) or opened > Decimal(".01")
                        or any(utc(e["opened_at"]) < start for e in result["entries"])
                        or any(utc(e["closed_at"]) > start + timedelta(seconds=HORIZON) for e in result["exits"])):
                    raise ValueError("receipt result outside isolated position envelope")
    reports = {}
    for rule in sorted(controls):
        for profile in sorted(profiles):
            values = [r for r in rows if r["control"] == rule and r["profile"] == profile]
            groups = {"all_retained": values,
                "supported_initial": [r for r in values if r["initial_receipt_supported"]],
                "fresh_initial": [r for r in values if r["fresh_initial_receipt_within_5s"]]}
            groups.update({"tier:" + tier: [r for r in values if r["source_evidence_tier"] == tier]
                           for tier in sorted({r["source_evidence_tier"] for r in values})})
            groups.update({"month:" + month: [r for r in values if r["received_utc"].startswith(month)]
                           for month in sorted({r["received_utc"][:7] for r in values})})
            reports[rule + ":" + profile] = {k: summarize_subset(v) for k, v in groups.items()}
    return {"schema_version": SCHEMA, "retained_identities": len(entries), "evaluation_rows": len(rows),
        "status_counts": dict(sorted(Counter(r["status"] for r in rows).items())), "groups": reports,
        "prior_exact_match": compare_prior(rows, prior_rows),
        "selection": {"selected_policy": None, "promotion_eligible": False},
        "money_contract_verified": False, "account_currency_money_verified": False,
        "live_activation_allowed": False, "new_strategies": 0, "orders_sent": 0, "limitations": LIMITATIONS}


def _current(implementation):
    _check_current(implementation)
    if any(_digest(ROOT / name) != value for name, value in _IMPORTED.items()):
        raise ValueError("loaded receipt-control implementation changed")


def _inputs(coverage_dir, prior_dir, budget):
    coverage_dir, prior_dir, watched = Path(coverage_dir).resolve(), Path(prior_dir).resolve(), {}
    def watch(path, expected=None):
        budget.check()
        _watch(path, _digest(path) if expected is None else expected, watched)
    _archive(coverage_dir, "receipt_coverage_identity_sha256", watch)
    # The independent coverage verifier also decodes quotes; charge its full pass.
    budget.charge_source(_read(coverage_dir / "summary.json")["usage"]["source_quotes_decoded"])
    coverage_proof = verify_coverage(coverage_dir)
    prior_proof = verify_paired(prior_dir)
    _archive(prior_dir, "paired_identity_sha256", watch)
    cp, pp = _read(coverage_dir / "protocol.json"), _read(prior_dir / "protocol.json")
    receipt_dir = Path(cp["receipt_dir"])
    rp = _read(receipt_dir / "protocol.json")
    inputs = AnnualInputs(rp["stream_dir"], cp["coverage_dir"], cp["raw_audit_path"])
    for path, sha in watched.items():
        inputs.watch(path, sha)
    for name in SOURCES:
        inputs.watch(ROOT / name)
    inputs.raw = BoundedRawSource(cp["raw_audit_path"], inputs.protocol["broker_clock"]["segments"], inputs.watch, budget=budget)
    profiles = execution_profiles(inputs.reference["execution"])
    for profile in profiles.values():
        profile["protection"]["request_quote_binding"] = "timestamp_and_ordinal"
    profiles = json.loads(_encode(profiles))
    controls = fixed_controls()
    if (pp["controls"] != controls or pp["execution_profiles"] != profiles or pp["money"] != inputs.reference["money"]
            or pp["horizon_seconds"] != HORIZON or inputs.reference["money"]["contract_size"] != 100):
        raise ValueError("prior control, execution or money assumptions differ")
    entries = _read(receipt_dir / "inventory.json")["entries"]
    coverage = {r["signal_id"]: r for r in _read(coverage_dir / "coverage.json") if r["horizon_seconds"] == HORIZON}
    if not entries or len(entries) * len(controls) * len(profiles) * 3 > BUDGET["max_evaluations"]:
        raise ValueError("full expanded matrix exceeds evaluation budget")
    quotes = sum(r["coverage"]["market_quotes_in_horizon"] for r in coverage.values() if r["status"] == "quote_window_ready")
    if quotes * len(controls) * len(profiles) * 3 > BUDGET["max_engine_quote_visits"]:
        raise ValueError("full expanded matrix exceeds quote budget")
    inputs.verify_sources()
    _current(inputs.implementation)
    context = {"coverage_dir": str(coverage_dir), "coverage_proof": coverage_proof,
        "prior_dir": str(prior_dir), "prior_proof": prior_proof, "receipt_dir": str(receipt_dir),
        "receipt_entries_sha256": _sha(entries), "retained_identities": len(entries),
        "implementation": inputs.implementation, "controls": controls, "execution_profiles": profiles,
        "money": inputs.reference["money"], "coverage_protocol": inputs.protocol}
    return inputs, entries, coverage, _rows(prior_dir / "results.jsonl"), context


def run_controls(coverage_dir, prior_dir, output_dir, *, progress=None):
    output, budget = Path(output_dir).resolve(), RunBudget()
    if output.exists():
        raise ValueError("immutable receipt-control output already exists")
    inputs, entries, coverage, prior_rows, context = _inputs(coverage_dir, prior_dir, budget)
    roots = [Path(context[k]) for k in ("coverage_dir", "prior_dir", "receipt_dir")] + inputs.protected_dirs
    if (any(output.is_relative_to(p.resolve()) or p.resolve().is_relative_to(output) for p in roots)
            or any(Path(p).is_relative_to(output) for p in inputs.watched)):
        raise ValueError("receipt-control output overlaps a protected source")
    protocol = dict(context, schema_version=SCHEMA, budget=BUDGET, horizon_seconds=HORIZON,
        extra_cost_eur_per_lot_round_trip=str(EXTRA_COST), parameter_optimization=False,
        data_use="retrospective_expanded_fixed_controls", limitations=LIMITATIONS)
    output.mkdir(parents=True, exist_ok=False)
    _write(output / "protocol.json", protocol)
    engines = {name: (execution_from_mapping(p), FastEvaluator(execution=execution_from_mapping(p)))
               for name, p in context["execution_profiles"].items()}
    base = StrategyGenome.from_dict(context["controls"]["market"])
    prior_index = {_key(r): r for r in prior_rows if r["case_id"].startswith("receipt:")}
    rows, inventory = [], []
    ordered = sorted(entries, key=lambda e: (utc(e["received_utc"]), e["signal_id"]))
    with (output / "results.jsonl").open("xb") as stream:
        for i, entry in enumerate(ordered, 1):
            path, audit = load_receipt_path(entry, coverage[entry["signal_id"]], inputs, base, budget)
            inventory.append(audit)
            for name, mapping in context["controls"].items():
                genome = StrategyGenome.from_dict(mapping)
                for profile, (execution, fast) in engines.items():
                    row = {k: audit[k] for k in (*EVIDENCE_FIELDS, "case_id")}
                    row.update(control=name, profile=profile, strategy_fingerprint=genome.fingerprint,
                               status="data_blocked", reasons=audit["reasons"], engines={}, mismatches={})
                    if path is not None:
                        result, unused = _evaluate(path, genome, execution, fast, budget)
                        row.update(result)
                    rows.append(row)
                    stream.write(_encode(row))
                    prior = prior_index.get(_key(row))
                    if (row["status"] not in {"simulated", "unfilled", "data_blocked"}
                            or (prior is not None and any(row[k] != prior[k] for k in PRIOR_FIELDS))):
                        stream.flush()
                        _write(output / "incident.json", {"new_result": row, "prior_result": prior})
                        raise ValueError("expanded receipt engine/prior incident; incomplete archive preserved")
            stream.flush()
            if progress and (i % 20 == 0 or i == len(ordered)):
                progress({"completed_identities": i, "retained_identities": len(ordered), "evaluations": budget.evaluations,
                    "engine_quote_visits": budget.quote_visits, "source_quotes_decoded_including_verification": budget.source_quotes})
    summary = summarize_results(entries, inventory, rows, context["controls"], context["execution_profiles"], prior_rows)
    summary["usage"] = {"evaluations": budget.evaluations, "engine_quote_visits": budget.quote_visits,
        "source_quotes_decoded_including_verification": budget.source_quotes,
        "elapsed_seconds": time.monotonic() - budget.started}
    inputs.verify_sources()
    _current(inputs.implementation)
    budget.check()
    _write(output / "inventory.json", inventory)
    _write(output / "summary.json", summary)
    manifest = {"schema_version": SCHEMA, "status": "complete_expanded_fixed_controls_only",
        "inputs": {"watched_files": inputs.watched},
        "artifacts": {name: {"sha256": _digest(output / name)} for name in sorted(ARTIFACTS)}}
    manifest["receipt_controls_identity_sha256"] = _sha(manifest)
    _write(output / "manifest.json", manifest)
    return summary


def verify_controls(output_dir):
    output, watched, budget = Path(output_dir).resolve(), {}, RunBudget()
    if {p.name for p in output.iterdir()} != ARTIFACTS | {"manifest.json"}:
        raise ValueError("incomplete or mixed receipt-control archive")
    def watch(path, expected=None):
        budget.check()
        _watch(path, _digest(path) if expected is None else expected, watched)
    manifest = _archive(output, "receipt_controls_identity_sha256", watch)
    if manifest["schema_version"] != SCHEMA or set(manifest["artifacts"]) != ARTIFACTS:
        raise ValueError("unsupported receipt-control archive")
    protocol = _read(output / "protocol.json")
    _current(protocol["implementation"])
    if (protocol["schema_version"] != SCHEMA or protocol["budget"] != BUDGET
            or protocol["horizon_seconds"] != HORIZON or protocol["parameter_optimization"] is not False
            or protocol["extra_cost_eur_per_lot_round_trip"] != str(EXTRA_COST) or protocol["limitations"] != LIMITATIONS):
        raise ValueError("receipt-control protocol differs")
    inputs, entries, coverage, prior_rows, context = _inputs(protocol["coverage_dir"], protocol["prior_dir"], budget)
    if any(protocol[k] != value for k, value in context.items()):
        raise ValueError("receipt-control source context differs")
    base = StrategyGenome.from_dict(context["controls"]["market"])
    inventory = [load_receipt_path(e, coverage[e["signal_id"]], inputs, base, budget)[1]
                 for e in sorted(entries, key=lambda r: (utc(r["received_utc"]), r["signal_id"]))]
    if inventory != _read(output / "inventory.json"):
        raise ValueError("receipt path recomputation differs")
    rows, stored = _rows(output / "results.jsonl"), _read(output / "summary.json")
    summary = summarize_results(entries, inventory, rows, context["controls"], context["execution_profiles"], prior_rows)
    if summary != {k: v for k, v in stored.items() if k != "usage"}:
        raise ValueError("receipt-control aggregation differs")
    quotes = {r["case_id"]: r["coverage"]["market_quotes_in_horizon"] for r in inventory if r["status"] == "data_ready"}
    evaluations = 3 * sum(r["status"] != "data_blocked" for r in rows)
    visits = 3 * sum(quotes[r["case_id"]] for r in rows if r["status"] != "data_blocked")
    usage = stored["usage"]
    if (usage["evaluations"] != evaluations or usage["engine_quote_visits"] != visits
            or evaluations > BUDGET["max_evaluations"] or visits > BUDGET["max_engine_quote_visits"]
            or budget.source_quotes != usage["source_quotes_decoded_including_verification"]
            or not 0 < usage["elapsed_seconds"] < BUDGET["max_wall_seconds"]):
        raise ValueError("receipt-control resource accounting differs")
    inputs.verify_sources()
    _verify_sources(watched)
    _current(protocol["implementation"])
    budget.check()
    return {"status": "verified_expanded_fixed_controls_only",
        "receipt_controls_identity_sha256": manifest["receipt_controls_identity_sha256"],
        "retained_identities": len(entries), "evaluation_rows": len(rows), "engine_evaluations": evaluations,
        "prior_exact_match": summary["prior_exact_match"]}
