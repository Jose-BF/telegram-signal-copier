"""Compare the two historically conflated Gold 555 behaviors."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.dubai_iterative.fast_engine import FastEvaluator  # noqa: E402
from research.dubai_iterative.engine import simulate  # noqa: E402
from research.dubai_iterative.oracle import certify_candidate  # noqa: E402
from research.gold_iterative.contracts import (  # noqa: E402
    gold_555_flat_cancel_genome,
    gold_555_until_expiry_genome,
)
from research.gold_iterative.dataset import (  # noqa: E402
    load_gold_direct_dataset,
    load_gold_now_dataset,
)
from research.gold_iterative.folds import build_gold_fold_plan  # noqa: E402
from research.gold_iterative.semantics_comparison import (  # noqa: E402
    compare_result_vectors,
    summarize_results,
)
from research.dubai_iterative.dataset import (  # noqa: E402
    VerifiedParquetTickSource,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--provider-catalog", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--market-ticks", type=Path, required=True)
    parser.add_argument("--conversion-ticks", type=Path, required=True)
    parser.add_argument("--money-contract", type=Path, required=True)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--signal-scope", choices=("now", "direct"), default="now")
    parser.add_argument("--max-hold-minutes", type=int, default=240)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    money_contract = json.loads(args.money_contract.read_text(encoding="utf-8"))
    loader = (
        load_gold_now_dataset
        if args.signal_scope == "now"
        else load_gold_direct_dataset
    )
    dataset = loader(
        replay_path=args.replay,
        audit_path=args.audit,
        provider_catalog_path=args.provider_catalog,
        raw_events_path=args.events,
        market_ticks=VerifiedParquetTickSource(
            args.market_ticks,
            expected_symbol="XAUUSD",
        ),
        conversion_ticks=VerifiedParquetTickSource(
            args.conversion_ticks,
            expected_symbol="EURUSD",
        ),
        money_contract=money_contract,
        from_date=args.from_date,
        to_date=args.to_date,
        max_hold_minutes=args.max_hold_minutes,
    )
    fold_plan = build_gold_fold_plan(dataset)
    complete_days = set(fold_plan.complete_days)
    paths = tuple(path for path in dataset.paths if path.day in complete_days)
    variants = (
        (
            "deterministic_flat_cancel",
            gold_555_flat_cancel_genome(),
            "cancel_remaining_entries_when_the_basket_first_becomes_flat",
        ),
        (
            "deterministic_until_expiry",
            gold_555_until_expiry_genome(),
            "keep_remaining_entries_eligible_until_the_original_30m_expiry",
        ),
    )
    reports = {}
    for name, genome, meaning in variants:
        evaluator = FastEvaluator()
        results = []
        scalar_results = []
        for index, path in enumerate(paths, start=1):
            results.append(evaluator(path, genome))
            scalar_results.append(simulate(path, genome))
            evaluator.clear_cache()
            if index == len(paths) or index % 25 == 0:
                print(f"[{name}] {index}/{len(paths)} signals evaluated")
        certificate = certify_candidate(paths, genome, tuple(results))
        scalar_mismatches = compare_result_vectors(
            tuple(results), tuple(scalar_results)
        )
        summary = summarize_results(
            paths,
            tuple(results),
            oracle_status=(
                certificate.status if not scalar_mismatches else "blocked"
            ),
        )
        summary.update({
            "meaning": meaning,
            "research_genome_fingerprint": genome.fingerprint,
            "source_parameter_fingerprint": (
                genome.source_strategy_fingerprint
            ),
            "oracle_status": certificate.status,
            "oracle_mismatch_count": len(certificate.mismatches),
            "engine_parity": {
                "status": (
                    "pass"
                    if certificate.status == "pass" and not scalar_mismatches
                    else "blocked"
                ),
                "scalar_fast_mismatches": list(scalar_mismatches),
                "fast_oracle_mismatch_count": len(certificate.mismatches),
            },
        })
        reports[name] = summary

    payload = {
        "schema_version": 1,
        "purpose": "separate_deterministic_gold_555_lifecycle_semantics",
        "evidence_role": "shadow_prediction",
        "cohort": {
            "from_date": args.from_date,
            "to_date": args.to_date,
            "signal_scope": args.signal_scope,
            "eligible_signals": len(dataset.eligible_signal_ids),
            "loaded_paths": len(dataset.paths),
            "evaluated_complete_day_signals": len(paths),
            "complete_days": list(fold_plan.complete_days),
            "incomplete_days_excluded": list(fold_plan.incomplete_days),
        },
        "source_hashes": dict(sorted(dataset.source_hashes.items())),
        "variants": reports,
        "interpretation": {
            "actual_mt5": "not_calculated_by_this_report",
            "live_logic_mirror": "certified_separately",
            "observed_live_lifecycle": (
                "scheduler_dependent_and_not_represented_as_a_strategy"
            ),
            "shadow_prediction": (
                "counterfactual_from_telegram_and_ticks; one exact value per "
                "explicit strategy meaning"
            ),
            "promotion_allowed": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    for name, report in reports.items():
        print(f"{name}: {report['status']} | {report['net_eur']} EUR")
    print(f"Output: {args.output.resolve()}")
    return 0 if all(row["status"] == "certified" for row in reports.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
