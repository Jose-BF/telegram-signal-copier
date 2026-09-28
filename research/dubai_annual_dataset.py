"""Lazy annual own-rule inputs with unchanged source, clock and coverage gates."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import sys

import pandas as pd

from research import strategy_study as fixed
from research.causal_replay import make_path
from research.dubai_annual_coverage import RawSource, coverage_metrics, msc
from research.dubai_entry_probe import _archive, _rows, SCENARIOS
from research.dubai_entry_stream import to_causal_signals
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.dataset import StrategyDataset
from research.dubai_iterative.portfolio import build_portfolio_tape


EXTRA_SOURCES = ("research/dubai_annual_dataset.py", "research/dubai_family_catalog.py",
                 "research/dubai_shared_lab.py", "tools/run_dubai_shared_lab.py")
_IMPORTED = {name: fixed._digest(fixed.ROOT / name) for name in EXTRA_SOURCES if (fixed.ROOT / name).exists()}
_LIVE_IMPORTS = fixed.FORBIDDEN_IMPORTS - {"research.dubai_iterative.search", "research.gold_iterative.search"}


@dataclass(frozen=True)
class AnnualDay:
    scenario: str
    day: str
    horizon_seconds: int
    rows: tuple
    paths: tuple
    source_hashes: dict
    portfolio_tape: object | None

    @property
    def complete(self):
        return bool(self.rows) and all(r["status"] == "data_ready" for r in self.rows)

    def as_strategy_dataset(self):
        exclusions = defaultdict(list)
        for row in self.rows:
            for reason in row["reasons"]:
                exclusions[reason].append(row["trigger_id"])
        return StrategyDataset(paths=self.paths, eligible_signal_ids=tuple(r["trigger_id"] for r in self.rows),
            eligible_signal_days={r["trigger_id"]: self.day for r in self.rows}, eligible_actual_pnl_eur=Decimal("0.00"),
            actual_evidence_signal_ids=(), exclusions={k: tuple(v) for k, v in exclusions.items()},
            source_hashes=dict(self.source_hashes), account_currency="EUR", currency_digits=2,
            max_hold_minutes=self.horizon_seconds // 60)


class AnnualInputs:
    def __init__(self, stream_dir, coverage_dir, raw_audit_path):
        self.stream_dir, self.coverage_dir, self.raw_audit_path = map(
            lambda p: Path(p).resolve(), (stream_dir, coverage_dir, raw_audit_path))
        self.watched, self.implementation = {}, fixed.current_identity()
        self._check_code()
        for relative in _IMPORTED:
            self.watch(fixed.ROOT / relative, _IMPORTED[relative])
        self.stream = _archive(self.stream_dir, "stream_identity_sha256", self.watch)
        self.prior_coverage = _archive(self.coverage_dir, "audit_identity_sha256", self.watch)
        if self.stream.get("schema_version") != "dubai_entry_stream_v1" or self.stream.get("engine_dataset_ready") is not False:
            raise ValueError("expected the explicit unadmitted hypothetical stream")
        self.protocol = fixed._read(self.coverage_dir / "protocol.json")
        self.watch(self.coverage_dir / "protocol.json", self.stream["inputs"]["coverage_protocol_sha256"])
        self.watch(self.raw_audit_path, self.protocol["raw_audit_sha256"])
        if self.protocol["broker_clock"]["status"] != "declared_hypothesis":
            raise ValueError("declared broker clock required")
        reference = self.protocol["threshold_reference"]
        self.watch(reference["path"], reference["sha256"])
        self.reference = fixed.load_config(reference["path"])
        for name in ("max_market_gap_ms", "max_fx_age_ms", "max_fx_interval_ms"):
            if self.protocol[name] != self.reference[name]:
                raise ValueError("coverage threshold changed from the frozen reference")
        if self.reference["money"]["rollover_hour_server"] != 0:
            raise ValueError("annual clock coverage requires the frozen midnight rollover")
        self.rolling = fixed._read(self.stream_dir / "protocol.json")["rolling_protocol"]
        if "rolling_folds.jsonl" not in self.stream["artifacts"]:
            raise ValueError("missing rolling fold artifact")
        self.folds = _rows(self.stream_dir / "rolling_folds.jsonl")
        self.triggers = _rows(self.stream_dir / "triggers.jsonl")
        if not 0 < len(self.triggers) <= 2500:
            raise ValueError("annual signal inventory outside budget")
        self.signals = {s.signal_id: s for scenario in SCENARIOS for s in to_causal_signals(
            [r for r in self.triggers if r["scenario"] == scenario], scenario=scenario)}
        if len(self.signals) != len(self.triggers):
            raise ValueError("mixed or duplicate scenario inventory")
        start, end = map(fixed._utc_explicit, (self.rolling["start"], self.rolling["end_exclusive"]))
        by_id = {r["trigger_id"]: r for r in self.triggers}
        if any(not start <= s.observed_at < end for s in self.signals.values()):
            raise ValueError("trigger outside frozen annual period")
        self.coverage = {}
        for row in _rows(self.stream_dir / "coverage.jsonl"):
            key = (row["trigger_id"], row["horizon_seconds"])
            trigger = by_id.get(row["trigger_id"])
            if (key in self.coverage or trigger is None or row["horizon_seconds"] not in {1200, 14400}
                    or row["scenario"] != trigger["scenario"] or row["trigger_utc"] != trigger["trigger_utc"]):
                raise ValueError("mixed or duplicate annual coverage identity")
            self.coverage[key] = row
        self.horizons = sorted({key[1] for key in self.coverage})
        if not self.horizons or set(self.coverage) != {(key, h) for key in self.signals for h in self.horizons}:
            raise ValueError("incomplete annual coverage denominator")
        for fold in self.folds:
            for field in ("development_entry_ids", "check_entry_ids", "purged_entry_ids"):
                if any(key not in by_id or by_id[key]["scenario"] != fold["scenario"] for key in fold[field]):
                    raise ValueError("mixed annual fold membership")
        self.raw = RawSource(self.raw_audit_path, self.protocol["broker_clock"]["segments"], self.watch)
        self.protected_dirs = [self.stream_dir, self.coverage_dir, self.raw.directory,
                               *map(Path, self.stream["inputs"]["protected_archive_dirs"])]
        self._memory = fixed._sha(self._memory_contract())
        self.verify_sources()

    def _check_code(self):
        current = fixed.current_identity()
        if current != self.implementation:
            raise ValueError("implementation or environment changed")
        fixed._check_loaded_sources(current)
        if _LIVE_IMPORTS.intersection(sys.modules):
            raise ValueError("live import violates annual offline boundary")
        if any(fixed._digest(fixed.ROOT / name) != sha for name, sha in _IMPORTED.items()):
            raise ValueError("loaded annual implementation changed; use a fresh interpreter")

    def _memory_contract(self):
        return {"triggers": self.triggers, "coverage": list(self.coverage.values()), "folds": self.folds,
                "rolling": self.rolling, "protocol": self.protocol, "reference": self.reference,
                "raw_records": list(self.raw.records.values()), "signals": [
                    (s.signal_id, s.direction, s.observed_at, s.published_at, s.message_revision_id) for s in self.signals.values()]}

    def watch(self, path, expected=None):
        sha = fixed._digest(path) if expected is None else expected
        fixed._watch(path, sha, self.watched)
        return sha

    def verify_sources(self):
        self._check_code()
        fixed._verify_sources(self.watched)
        if fixed._sha(self._memory_contract()) != self._memory:
            raise ValueError("annual input contract changed in memory")

    def inventory(self):
        scenarios = {}
        for scenario in SCENARIOS:
            selected = [r for r in self.triggers if r["scenario"] == scenario]
            days = sorted({r["trigger_utc"][:10] for r in selected})
            horizons = {}
            for horizon in self.horizons:
                rows = [self.coverage[(r["trigger_id"], horizon)] for r in selected]
                day_counts = {day: {"triggers": sum(r["trigger_utc"].startswith(day) for r in rows),
                    "data_ready": sum(r["trigger_utc"].startswith(day) and r["quote_coverage_pass"] for r in rows)} for day in days}
                horizons[str(horizon)] = {"triggers": len(rows), "data_ready": sum(r["quote_coverage_pass"] for r in rows),
                    "blocked_ids": [r["trigger_id"] for r in rows if not r["quote_coverage_pass"]],
                    "reason_counts": dict(Counter(reason for r in rows for reason in r["coverage_reasons"])),
                    "complete_days": sum(v["triggers"] == v["data_ready"] for v in day_counts.values()), "days": day_counts}
            scenarios[scenario] = {"triggers": len(selected), "days": len(days), "horizons": horizons}
        fold_coverage = []
        for fold in self.folds:
            for horizon in self.horizons:
                counts = {role: {"triggers": len(fold[role + "_entry_ids"]), "data_ready": sum(
                    self.coverage[(key, horizon)]["quote_coverage_pass"] for key in fold[role + "_entry_ids"])}
                    for role in ("development", "check", "purged")}
                fold_coverage.append({"scenario": fold["scenario"], "fold_id": fold["fold_id"],
                    "horizon_seconds": horizon, "counts": counts, "status": "retrospective_not_fresh_oos"})
        return {"schema_version": "dubai_annual_lazy_inputs_v1", "scenarios": scenarios, "fold_coverage": fold_coverage,
            "rolling_protocol": self.rolling, "stream_identity_sha256": self.stream["stream_identity_sha256"],
            "quote_revalidation": "archived_coverage_bound_now_raw_windows_rechecked_when_loaded",
            "engine_dataset_ready": False, "money_contract_verified": False,
            "account_currency_money_verified": False, "observed_receipt_available": False,
            "source_count": len(self.watched), "implementation": self.implementation,
            "context_before_trigger_available_in_paths": False, "automatic_selection_allowed": False}

    def load_day(self, scenario, day, horizon, *, max_path_quotes=2_000_000, max_source_quotes=4_000_000):
        if scenario not in SCENARIOS or horizon not in self.horizons:
            raise ValueError("unsupported annual scenario or horizon")
        selected = sorted((r for r in self.triggers if r["scenario"] == scenario and r["trigger_utc"].startswith(day)),
                          key=lambda r: (msc(r["trigger_utc"]), r["trigger_id"]))
        if not selected:
            raise ValueError("no triggers for selected complete day")
        if sum(self.coverage[(r["trigger_id"], horizon)]["market_quotes_in_horizon"] for r in selected) > max_path_quotes:
            raise ValueError("day path quote budget exceeded; no truncation")
        start = min(msc(r["trigger_utc"]) for r in selected)
        end = max(msc(r["trigger_utc"]) for r in selected) + horizon * 1000
        days = {day_id for r in selected for day_id in self.coverage[(r["trigger_id"], horizon)]["raw_source_days"]}
        count = sum(self.raw.records[tuple(d.split(":"))]["rows"] for d in days if tuple(d.split(":")) in self.raw.records)
        if count > max_source_quotes:
            raise ValueError("day source quote budget exceeded; no truncation")
        frames, tapes = {}, {}
        # Load the same canonical source days once for portfolio valuation; path
        # windows below are independently matched against the frozen coverage.
        for role, symbol in (("market", "XAUUSD"), ("conversion", "EURUSD")):
            values, _ids, _missing = self.raw.window(symbol, start, end)
            if len(values[0]) > max_source_quotes:
                raise ValueError("canonical source quote budget exceeded")
            frames[role] = pd.DataFrame({"time_utc": pd.to_datetime(values[0], unit="ns", utc=True), "bid": values[1], "ask": values[2]})
            tapes[role] = fixed._CanonicalSource(frames[role], {"raw_audit": self.protocol["raw_audit_sha256"], "days": sorted(days)})
        rows, paths = [], []
        genome = StrategyGenome.from_dict(self.reference["strategy"])
        for trigger in selected:
            key, stamp = trigger["trigger_id"], msc(trigger["trigger_utc"])
            market, market_ids, market_missing = self.raw.window("XAUUSD", stamp, stamp + horizon * 1000)
            fx, fx_ids, fx_missing = self.raw.window("EURUSD", stamp, stamp + horizon * 1000)
            metrics = coverage_metrics(stamp, horizon, market, fx, self.protocol)
            metrics.update(raw_source_days=market_ids + fx_ids, missing_raw_source_days=market_missing + fx_missing)
            if market_missing or fx_missing:
                metrics["coverage_reasons"] = sorted(set(metrics["coverage_reasons"] + ["raw_source_day_missing"]))
                metrics["quote_coverage_pass"] = False
            if any(self.coverage[(key, horizon)][name] != value for name, value in metrics.items()):
                raise ValueError(f"coverage revalidation incident: {key}")
            reasons = list(metrics["coverage_reasons"])
            if not reasons:
                signal = self.signals[key]
                try:
                    paths.append(make_path(signal, genome, market=market, conversion=fx,
                        cutoff=signal.observed_at + timedelta(seconds=horizon), contract_size=self.reference["money"]["contract_size"],
                        currency_digits=2, max_fx_age_ms=self.protocol["max_fx_age_ms"], max_fx_interval_ms=self.protocol["max_fx_interval_ms"],
                        market_sha256=fixed._sha({"raw": self.protocol["raw_audit_sha256"], "days": market_ids}),
                        conversion_sha256=fixed._sha({"raw": self.protocol["raw_audit_sha256"], "days": fx_ids})))
                except ValueError as exc:
                    reasons.append(str(exc))
            rows.append(dict(trigger, coverage=metrics, status="data_blocked" if reasons else "data_ready", reasons=reasons))
        portfolio_tape = build_portfolio_tape(paths, market_tick_source=tapes["market"], conversion_tick_source=tapes["conversion"],
            max_conversion_age_ms=self.protocol["max_fx_age_ms"], max_conversion_interval_ms=self.protocol["max_fx_interval_ms"]) if paths else None
        hashes = {"annual_stream": self.stream["stream_identity_sha256"], "raw_audit": self.protocol["raw_audit_sha256"],
                  "day_inventory": fixed._sha(rows), "scenario": fixed._sha(scenario), "horizon": fixed._sha(horizon)}
        return AnnualDay(scenario, day, horizon, tuple(rows), tuple(paths), hashes, portfolio_tape)
