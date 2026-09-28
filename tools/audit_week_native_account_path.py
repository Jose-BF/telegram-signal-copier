"""Freeze a modeled native-deal account path, with strict and bracketed FX.

This report reconciles closed deals and the existing per-basket auditor. It
does not contain independent historical MT5 account equity observations.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.account_risk_path import compare_account_paths
from research.risk_trajectory import RiskSpec
from tools import run_causal_controls as causal
from tools import run_week_causal_controls as weekly
from tools.audit_native_money_anchor import digest, read
from tools.compare_week_causal_sequences import _money_binding, _observed


CONTRACT = "native_week_modeled_account_path_v1"
SOURCES = (
    "tools/audit_week_native_account_path.py",
    "research/account_risk_path.py",
    "research/risk_trajectory.py",
    "research/dubai_iterative/portfolio.py",
    "tools/compare_week_causal_sequences.py",
    "tools/run_week_causal_controls.py",
)


def _native_inputs(deals_path, baskets_path, money_path, anchor_path, contract_path,
                   basket_risk_path):
    paths = tuple(map(Path, (deals_path, baskets_path, money_path, anchor_path,
                              contract_path, basket_risk_path)))
    hashes = {str(path): digest(path) for path in paths}
    deals, baskets, money, anchor, contract, basket_risk = map(read, paths)
    if (money.get("contract") != "native_closed_money_anchor_v2"
            or money.get("account_currency") != "EUR"
            or money.get("position_count") != len(money.get("positions", []))
            or baskets.get("source_sha256") != hashes[str(paths[0])]
            or basket_risk.get("contract") != "native_week_full_tick_risk_diagnostic_v1"
            or basket_risk.get("basket_count") != len(baskets.get("baskets", []))
            or basket_risk.get("basket_count") != len(basket_risk.get("baskets", []))
            or anchor.get("contract") != "native_tick_anchor_diagnostic_v1"
            or anchor.get("clock_admitted") is not False
            or contract["account"]["currency"] != "EUR"
            or contract["instrument"]["contract_size"] != 100.0
            or contract["conversion"]["orientation"] != "account_base_profit_quote"):
        raise ValueError("native account source contract mismatch")
    for path in paths[:2] + paths[3:5]:
        _money_binding(money, path)
    for path in paths[:5]:
        matches = [sha for name, sha in basket_risk["inputs_sha256"].items()
                   if Path(name).resolve() == path.resolve()]
        if matches != [hashes[str(path)]]:
            raise ValueError("native per-basket risk source binding mismatch")
    return paths, hashes, deals, baskets, money, anchor, contract, basket_risk


def audit(raw_dir, deals_path, baskets_path, money_path, anchor_path,
          contract_path, basket_risk_path, output_path):
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    (paths, hashes, deals, baskets, money, anchor, contract,
     basket_risk) = _native_inputs(deals_path, baskets_path, money_path,
                                   anchor_path, contract_path, basket_risk_path)
    sources = {name: digest(ROOT / name) for name in SOURCES}
    _, _, days = weekly._days(anchor)
    tapes = weekly._tape_proofs(Path(raw_dir), anchor, days)
    market, conversion = (weekly._load_tape(tapes[symbol])
                          for symbol in ("XAUUSD", "EURUSD"))
    by_signal = defaultdict(list)
    for position in money["positions"]:
        by_signal[position["signal_id"]].append(position)
    if ({row["signal_id"] for row in baskets["baskets"]} != set(by_signal)
            or len(by_signal) != basket_risk["basket_count"]
            or sum(map(len, by_signal.values())) != len(baskets["positions"])):
        raise ValueError("native modeled account basket denominator changed")
    events = {signal: _observed(signal, positions, deals)
              for signal, positions in sorted(by_signal.items())}
    spec = RiskSpec("EUR", contract["account"]["currency_digits"],
                    Decimal(str(contract["instrument"]["contract_size"])),
                    contract["conversion"]["orientation"],
                    contract["conversion"]["max_quote_age_ms"], 5_000)
    strict = compare_account_paths(events, events, market, conversion, spec=spec)
    interval = contract["conversion"]["max_quote_interval_ms"]
    bracketed = compare_account_paths(events, events, market, conversion, spec=spec,
                                      retrospective_fx_interval_ms=interval)
    expected_net = sum((Decimal(str(row["actual_net_eur"])) for row in money["positions"]),
                       Decimal(0))
    if (strict["observed"]["booked_net_eur"] != str(expected_net)
            or bracketed["observed"]["booked_net_eur"] != str(expected_net)
            or any(row["status"] not in {"blocked", "exact_sampled_path_only"}
                   for row in (strict, bracketed))
            or any(row["exposure_difference_marks"] or row["money_difference_marks"]
                   for row in (strict, bracketed))):
        raise ValueError("native account path identity or booked money mismatch")
    per_basket = Counter()
    mismatches = []
    for basket in basket_risk["baskets"]:
        signal = basket["signal_id"]
        result = compare_account_paths({signal: events[signal]}, {signal: events[signal]},
                                       market, conversion, spec=spec,
                                       retrospective_fx_interval_ms=interval)
        metrics = basket["path"]["metrics"]
        observed = result["observed"]
        if (result["status"] != "exact_sampled_path_only"
                or observed["max_drawdown_eur"] != metrics["max_drawdown"]
                or observed["booked_net_eur"] != metrics["final_net"]
                or Decimal(str(observed["max_gross_volume"]))
                   != Decimal(str(metrics["max_gross_volume"]))):
            mismatches.append(signal)
        else:
            per_basket["matched"] += 1
    if mismatches:
        raise ValueError(f"native account/per-basket money path differs: {mismatches}")
    if (any(digest(path) != hashes[str(path)] for path in paths)
            or any(digest(ROOT / name) != sha for name, sha in sources.items())
            or any(digest(row["meta"]) != row["meta_sha256"]
                   or digest(row["data"]) != row["data_sha256"]
                   for symbol in tapes for row in tapes[symbol])):
        raise ValueError("native modeled account source changed during audit")
    report = {"contract": CONTRACT, "status": "diagnostic_only",
              "basket_count": len(by_signal), "position_count": len(money["positions"]),
              "strict_causal": strict, "retrospective_bracketed": bracketed,
              "retrospective_fx_interval_ms": interval,
              "per_basket_metrics_matched": per_basket["matched"],
              "per_basket_metrics_mismatched": mismatches,
              "source_clock_admitted": False,
              "observed_account_equity_compared": False,
              "independent_replay_compared": False,
              "full_live_parity_verified": False,
              "inputs_sha256": hashes, "tape_proofs": tapes,
              "sources_sha256": sources,
              "limitations": [
                  "Observed deals and booked money, but modeled floating from retained XAUUSD and EURUSD ticks.",
                  "Strict causal FX leaves unknown money marks; the retrospective mode uses only prior FX prices.",
                  "A later EURUSD tick proves interval length, not information available to the live decision engine.",
                  "MT5 account equity, credit, margin, rollover and broker conversion pricing are not independently verified.",
                  "The broker-to-UTC offset remains a hypothesis outside directly anchored days.",
              ]}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as stream:
        stream.write(causal.encode(report))
    return {"baskets": report["basket_count"], "positions": report["position_count"],
            "strict_status": strict["status"], "bracketed_status": bracketed["status"],
            "matched_baskets": per_basket["matched"], "output_sha256": digest(output_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("raw-dir", "deals", "baskets", "money", "anchor", "contract",
                 "basket-risk", "output"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    print(audit(args.raw_dir, args.deals, args.baskets, args.money, args.anchor,
                args.contract, args.basket_risk, args.output))


if __name__ == "__main__":
    main()
