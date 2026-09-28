"""Exact-engine check of the level-1 leads found by the map (M3), three brokers, all months.

Leads are chosen on the full sample, so this is NOT validation: it only tells whether the
calculator's lead survives the exact engine. Out-of-sample judgement comes from M4/M7.
Inverted rules run on the flipped universe (same time, opposite direction).
usage: python search2/check_leads.py out.json [workers]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')
from research.basket_bridge import rule_to_genome
from research.basket_grid import BasketRule
from research.history_search import evaluate, metrics

U = 'runtime_data/signal_universe_v1/'
LEADS = [
    ("c1_invertir_tp20_t60", "canal1", U + 'placebo_flipped_s0.jsonl',
     BasketRule(weights=(0.01,), tp_steps=(20.0,), time_min=60, time_mode="always")),
    ("c1_invertir_tp20_sl60_t60", "canal1", U + 'placebo_flipped_s0.jsonl',
     BasketRule(weights=(0.01,), tp_steps=(20.0,), sl_move=60.0, time_min=60, time_mode="always")),
    ("c1_dubai_paso3_tope50_t30", "canal1", U + 'signals.jsonl',
     BasketRule(weights=(0.01, 0.04, 0.04), step=3.0, basket_stop_eur=50.0, lock_arm_eur=10.0, lock_give_eur=2.0,
                time_min=30, time_mode="loss_only", expiry_min=15)),
    ("c1_dubai_paso3_tope50_t120", "canal1", U + 'signals.jsonl',
     BasketRule(weights=(0.01, 0.04, 0.04), step=3.0, basket_stop_eur=50.0, lock_arm_eur=10.0, lock_give_eur=2.0,
                time_min=120, time_mode="loss_only", expiry_min=15)),
    ("c2_invertir_tp7_aguantar", "canal2", U + 'placebo_flipped_s0.jsonl',
     BasketRule(weights=(0.01,), tp_steps=(7.0,))),
    ("c2_seguir_tp4_sl30", "canal2", U + 'signals.jsonl',
     BasketRule(weights=(0.01,), tp_steps=(4.0,), sl_move=30.0)),
]


def main():
    out = Path(sys.argv[1]); workers = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    days = sorted(p.stem for p in Path('runtime_data/ticks_all/XAUUSD').glob('*.parquet'))
    cals = {k: json.load(open(f'runtime_data/execution_calibration_v2/{k}_q450.json')) for k in ('A_p50', 'C_p50', 'C_p90')}
    # canal1 per-leg targets need the plain protection profile (the live canal1 guard has no TP)
    cals = {k: {**v, "policy_extension": {"canal1": "none"}} for k, v in cals.items()}
    result = {}
    for uni in sorted({u for _, _, u, _ in LEADS}):
        sub = [(n, ch, r) for n, ch, u, r in LEADS if u == uni]
        pops = {ch: [rule_to_genome(r) for n, c, r in sub if c == ch] for ch in ('canal1', 'canal2')}
        pops = {k: v for k, v in pops.items() if v}
        names = {ch: [n for n, c, r in sub if c == ch] for ch in pops}
        rows = evaluate('runtime_data/ticks_all', days, pops, cals, workers=workers, universe=uni)
        for (sc, ch, gi), v in metrics(rows, {}).items():
            result.setdefault(names[ch][gi], {})[sc] = v
    json.dump(result, open(out, 'w'), indent=1)
    for name, res in result.items():
        print(name)
        for sc, v in sorted(res.items()):
            print(f"  {sc:6s} n={v['n']:4d} neto={v['net']:8.1f} pf={v['pf']:.3f} peor={v['worst_basket']:7.1f} "
                  f"meses+={v['pos_months']}/{v['months']} peor_mes={v['worst_month']:7.1f}")


if __name__ == '__main__':
    main()
