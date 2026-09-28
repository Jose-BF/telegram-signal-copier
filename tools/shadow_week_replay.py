"""Replay the six VM shadow strategies (strategy_runtime_contract.py) over a period with the
validated historical simulator (research/history_search.evaluate), three broker scenarios.

The VM shadow report stopped on 30/08 (see estado-y-plan section 6), so this is the local
substitute. Trigger-only replay: provider management is ignored, as in the search.

usage: python tools/shadow_week_replay.py 2026-09-21:2026-09-26 out.json [workers]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')
from research.dubai_iterative.contracts import StrategyGenome
from research.history_search import evaluate, metrics
from tools.run_causal_controls import policies


def shadow_genomes():
    live = policies()
    c1, c2 = live["canal1"], live["canal2"]
    ign = dict(provider_management_mode="ignore")
    front = dict(leg_count=6, volume_weights=(0.01, 0.05, 0.01, 0.02, 0.01, 0.02), stop_value=30.0,
                 profit_lock_giveback=8.0, pending_entry_policy="until_expiry", **ign)
    return {
        "canal1": [
            ("dubai_balanced_v1", c1.with_change(pending_entry_policy="until_expiry", **ign)),
            ("dubai_frontloaded_30m_v1", c1.with_change(time_exit_min=30, **front)),
            ("dubai_frontloaded_40m_v1", c1.with_change(time_exit_min=40, **front)),
        ],
        "canal2": [
            ("gold_now_555_v1", c2.with_change(**ign)),
            ("gold_now_b210_v1", StrategyGenome(
                schema_version=2, entry_mode="signal_market", entry_expiry_min=15, entry_ladder_mode="adverse",
                entry_ladder_step=1.0, leg_count=6, volume_weights=(0.01,) * 6, target_mode="none", be_mode="none",
                stop_mode="basket_money", stop_value=60.0, profit_lock_arm=30.0, profit_lock_giveback=10.0,
                time_exit_min=3, time_exit_mode="profit_only", pending_entry_policy="until_expiry", **ign)),
            ("gold_now_c490_v1", StrategyGenome(
                schema_version=2, entry_mode="signal_market", entry_expiry_min=15, entry_ladder_mode="simultaneous",
                leg_count=5, volume_weights=(0.01,) * 5, target_mode="none", be_mode="price", be_trigger=12.0,
                stop_mode="basket_money", stop_value=100.0, hard_stop_eur_per_leg=20.0, profit_lock_arm=10.0,
                profit_lock_giveback=8.0, time_exit_min=40, time_exit_mode="loss_only", **ign)),
        ],
    }


def main():
    lo, hi = sys.argv[1].split(':')
    out = Path(sys.argv[2])
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    named = shadow_genomes()
    for ch, items in named.items():
        for name, g in items:
            if g.validation_errors():
                raise SystemExit(f"{name}: {g.validation_errors()}")
    days = sorted(p.stem for p in Path('runtime_data/ticks_all/XAUUSD').glob('*.parquet') if lo <= p.stem < hi)
    cals = {k: json.load(open(f'runtime_data/execution_calibration_v2/{k}_q450.json')) for k in ('A_p50', 'C_p50', 'C_p90')}
    # c490 needs the own-rule extension (price break-even); the other Gold shadows run without it,
    # exactly as in the search and the weekly exam.
    main_pops = {"canal1": [g for _, g in named["canal1"]], "canal2": [g for _, g in named["canal2"][:2]]}
    rows = evaluate('runtime_data/ticks_all', days, main_pops, cals, workers=workers)
    ext = {k: {**v, "policy_extension": {"canal2": "own_rule_be_partial_v1"}} for k, v in cals.items()}
    rows += [(sc, ch, 2, *rest) for sc, ch, _gi, *rest in
             evaluate('runtime_data/ticks_all', days, {"canal2": [named["canal2"][2][1]]}, ext, workers=workers)]
    m = metrics(rows, {ch: len(v) for ch, v in named.items()})
    summary = {sc: {named[ch][gi][0]: v for (s, ch, gi), v in m.items() if s == sc} for sc in cals}
    json.dump({"period": sys.argv[1], "days": days, "summary": summary, "rows": rows}, open(out, 'w'))
    for sc, res in summary.items():
        print(sc)
        for name, v in res.items():
            print(f"  {name:26s} n={v['n']:3d} net={v['net']:8.2f} worst={v['worst_basket']:8.2f} "
                  f"dd_max={v['dd_max']:7.2f} cens={v['censored']}")


if __name__ == '__main__':
    main()
