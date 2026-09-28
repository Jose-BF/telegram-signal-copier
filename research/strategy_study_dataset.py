"""Read-only bridge from fixed study inputs to bounded own-rule search.

Only inputs cross this boundary. The fixed-run result, observed fills and live
modules never do. Loading a dataset does not authorize searching or selection.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
import math
from pathlib import Path
import sys
import time

import numpy as np

from research import strategy_study as fixed
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.dataset import StrategyDataset, TickSource


_IMPORTED_IDENTITY = fixed.current_identity()
_LIVE_IMPORTS = fixed.FORBIDDEN_IMPORTS - {
    "research.dubai_iterative.search", "research.gold_iterative.search",
}


def _verify_implementation():
    current = fixed.current_identity()
    if current != _IMPORTED_IDENTITY:
        raise ValueError("loaded study/search implementation changed; use a fresh interpreter")
    fixed._check_loaded_sources(current)
    if _LIVE_IMPORTS.intersection(sys.modules):
        raise ValueError("live module violates the offline study data boundary")


@dataclass(frozen=True)
class StudyDatasetBundle:
    dataset: StrategyDataset
    config: dict
    market_tick_source: TickSource
    conversion_tick_source: TickSource
    max_fx_age_ms: int
    max_fx_interval_ms: int
    inventory: dict
    identity: dict
    _source_bindings: dict
    _config_digest: str
    _inventory_digest: str
    _identity_digest: str
    _dataset_contract_digest: str
    _canonical_source_digests: tuple[str, str]

    def verify_sources(self):
        """Call around search, portfolio work and archive publication."""
        _verify_implementation()
        fixed._verify_sources(self._source_bindings)
        if (fixed._sha(self.config) != self._config_digest
                or fixed._sha(self.inventory) != self._inventory_digest
                or fixed._sha(self.identity) != self._identity_digest
                or fixed._sha(self.dataset.source_hashes) != self._dataset_contract_digest):
            raise ValueError("study input contract or identity changed in memory")
        if (self.max_fx_age_ms != self.config["max_fx_age_ms"]
                or self.max_fx_interval_ms != self.config["max_fx_interval_ms"]):
            raise ValueError("bundle FX validation contract changed")
        current_tapes = (self.market_tick_source.content_identity, self.conversion_tick_source.content_identity)
        if current_tapes != self._canonical_source_digests:
            raise ValueError("bundle canonical source contract changed")


def load_study_dataset(config_path) -> StudyDatasetBundle:
    """Reuse M7 admission, clocks, coverage and bounds without running engines.

Missing eligible paths remain exclusions, so coverage_complete stays false.
This diagnostic has no observed accounting and cannot produce a selection.
"""
    started = time.monotonic()
    _verify_implementation()
    config_path = Path(config_path).resolve()
    config_sha = fixed._digest(config_path)
    config = fixed.load_config(config_path)
    watched = {}
    fixed._watch(config_path, config_sha, watched)
    loader = fixed._export_input if config["input_kind"] == "telegram_export" else fixed._control_input
    rows, signals, input_info = loader(config, watched)
    pending = sorted((row for row in rows if row["status"] == "pending"),
                     key=lambda row: (signals[row["signal_id"]].observed_at, row["signal_id"]))
    frames, quote_count = fixed._load_tapes(config, watched)
    fixed._deadline(started, config)
    market, conversion = fixed._arrays(frames["market"]), fixed._arrays(frames["conversion"])
    horizon = timedelta(seconds=config["horizon_seconds"])
    path_quotes = sum(
        int(np.searchsorted(market[0], fixed.time_ns(signals[row["signal_id"]].observed_at + horizon), side="right")
            - np.searchsorted(market[0], fixed.time_ns(signals[row["signal_id"]].observed_at), side="left"))
        for row in pending
    )
    over_budget = (len(pending) > config["budget"]["max_signals"]
                   or 3 * len(pending) > config["budget"]["max_evaluations"]
                   or path_quotes > config["budget"]["max_quotes"])
    genome = StrategyGenome.from_dict(config["strategy"])
    paths, exclusions = [], defaultdict(list)
    market_hash, conversion_hash = (fixed._sha(config["sources"][role]) for role in ("market", "conversion"))
    for row in pending:
        fixed._deadline(started, config)
        signal = signals[row["signal_id"]]
        cutoff = signal.observed_at + horizon
        row["cutoff_utc"] = str(cutoff)
        row["source_admission_reasons"] = list(row["reasons"])
        reasons = (["declared_cohort_exceeds_budget_no_truncation"] if over_budget
                   else fixed._coverage(signal, cutoff, market, conversion, config))
        if not reasons:
            try:
                path = fixed.make_path(
                    signal, genome, market=market, conversion=conversion, cutoff=cutoff,
                    contract_size=config["money"]["contract_size"],
                    currency_digits=config["money"]["currency_digits"],
                    max_fx_age_ms=config["max_fx_age_ms"],
                    max_fx_interval_ms=config["max_fx_interval_ms"],
                    market_sha256=market_hash, conversion_sha256=conversion_hash,
                )
            except ValueError as exc:
                reasons = [str(exc)]
            else:
                paths.append(path)
        row.update(status="budget_blocked" if over_budget else "data_blocked" if reasons else "data_ready",
                   reasons=reasons)
        for reason in reasons:
            exclusions[reason].append(signal.signal_id)
    inventory = {
        "status": "diagnostic_input_only", "input_kind": config["input_kind"],
        "scenario": config["scenario"], "cohort": config["cohort"], "period": config["period"],
        "rows": rows, "input_diagnostics": input_info["input_diagnostics"],
        "source_admission_blockers": input_info["source_admission_blockers"],
        "denominator": {"all_archive_identities": len(rows), "selected_trigger_identities": len(pending),
                        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items()))},
        "quotes_loaded": quote_count, "planned_path_quotes": path_quotes,
        "fx_validation": fixed.fx_validation_contract(config["max_fx_age_ms"], config["max_fx_interval_ms"]),
        "money_contract_verified": False, "account_currency_money_verified": False,
        "observed_accounting_available": False, "automatic_admission": False,
        "search_candidates": 0, "limitations": fixed.LIMITATIONS,
    }
    config_digest, inventory_digest = fixed._sha(config), fixed._sha(inventory)
    source_hashes = {
        "study_config": config_sha, "normalized_study_config": config_digest,
        "study_input_inventory": inventory_digest, "study_source_bindings": fixed._sha(watched),
        "market_ticks": market_hash, "conversion_ticks": conversion_hash,
    }
    dataset = StrategyDataset(
        paths=tuple(paths), eligible_signal_ids=tuple(row["signal_id"] for row in pending),
        eligible_signal_days={row["signal_id"]: signals[row["signal_id"]].observed_at.date().isoformat()
                              for row in pending},
        eligible_actual_pnl_eur=Decimal("0.00"), actual_evidence_signal_ids=(),
        exclusions={reason: tuple(ids) for reason, ids in sorted(exclusions.items())},
        source_hashes=source_hashes, account_currency=config["money"]["account_currency"],
        currency_digits=config["money"]["currency_digits"],
        max_hold_minutes=math.ceil(config["horizon_seconds"] / 60),
    )
    identity = {
        "schema_version": "own_rule_search_input_v1", "config_path": str(config_path),
        "config_sha256": config_sha, "normalized_config_sha256": config_digest,
        "inventory_sha256": inventory_digest, "sources": dict(watched),
        "implementation": _IMPORTED_IDENTITY, "input_info": input_info,
        "money_contract_verified": False, "account_currency_money_verified": False,
        "observed_accounting_available": False, "selection_allowed": False,
        "source_interface": "connected_to_m7_diagnostic",
        "fx_validation": fixed.fx_validation_contract(config["max_fx_age_ms"], config["max_fx_interval_ms"]),
    }
    market_source = fixed._CanonicalSource(frames["market"], config["sources"]["market"])
    conversion_source = fixed._CanonicalSource(frames["conversion"], config["sources"]["conversion"])
    bundle = StudyDatasetBundle(
        dataset=dataset, config=config,
        market_tick_source=market_source, conversion_tick_source=conversion_source,
        max_fx_age_ms=config["max_fx_age_ms"], max_fx_interval_ms=config["max_fx_interval_ms"],
        inventory=inventory, identity=identity,
        _source_bindings=dict(watched), _config_digest=config_digest,
        _inventory_digest=inventory_digest, _identity_digest=fixed._sha(identity),
        _dataset_contract_digest=fixed._sha(source_hashes),
        _canonical_source_digests=(market_source.content_identity, conversion_source.content_identity),
    )
    fixed._deadline(started, config)
    bundle.verify_sources()
    return bundle
