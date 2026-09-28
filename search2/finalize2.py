"""M8: the final recipes -> money. For each family/carril that the judge accepts, take its final pick
(filter x genome chosen on all months), rerun it with the exact engine on the three brokers with
minute curves, build the account's daily table (floating included) and compute:
  - net per month, worst day, deepest drop, worst basket (base lots);
  - carril A: FTMO 2-step Monte Carlo pass rate and best lot multiplier for a 100k account;
  - carril B: capital needed (2 x deepest drop incl. floating) and EUR/month per 1,000 EUR of it;
  - the walk-forward (out-of-sample) money from the judge, which is what is promised.
usage: python search2/finalize2.py [scenario_for_judge=C_p50]
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, '.')
import numpy as np

from research.account_sim import account_equity, daily_table
from research.dubai_iterative.contracts import StrategyGenome
from research.history_search import evaluate
from research.prop_rules import FTMO_2STEP
from research.prop_sim import best_multiplier, capital_needed, challenge_stats
from search2.families import families
from search2.judge_run import FEATS


def accepted(judge, min_gap=0.0):
    """Families/carriles whose walk-forward on real signals is positive and beats the placebo's."""
    out = []
    for r in judge:
        for carril in ("A", "B"):
            rr, pp = r["real"].get(carril), r["placebo"].get(carril)
            if not rr or not rr["final_pick"]:
                continue
            w, wp = rr["walk_forward"], pp["walk_forward"] if pp else {"total": 0.0}
            if w["total"] > 0 and w["total"] > wp["total"] + min_gap and w["pos_months"] >= (w["n_months"] + 1) // 2:
                out.append((r["family"], carril, rr))
    return out


def run_pick(fam, pick, scenario):
    genome = StrategyGenome.from_dict(json.loads(np.load(f'runtime_data/search2_engine_v1/{fam.name}__real__C_p50.npz')["genomes"][pick["genome_index"]]))
    days = sorted(p.stem for p in Path('runtime_data/ticks_all/XAUUSD').glob('*.parquet'))
    cal = json.load(open(f'runtime_data/execution_calibration_v2/{scenario}_q450.json'))
    if fam.channel == 'canal1' and (genome.target_mode != 'none' or genome.stop_mode == 'fixed_move'):
        cal = {**cal, "policy_extension": {"canal1": "none"}}
    rows = evaluate('runtime_data/ticks_all', days, {fam.channel: [genome]}, {scenario: cal}, workers=4,
                    universe=fam.universe, filters={fam.channel: [pick["filter"]]}, features=FEATS[fam.universe], curves=True)
    return rows


def money(rows):
    baskets, pnl, months = [], [], {}
    for r in rows:
        curve = r[8]
        p = r[5] or 0.0
        pnl.append(p)
        months[r[3][:7]] = months.get(r[3][:7], 0.0) + p
        if curve:
            baskets.append((np.array(curve[0]), np.array(curve[1]), np.array(curve[2]), p))
    if not baskets:
        return None
    start = min(int(b[0][0]) for b in baskets); end = max(int(b[0][-1]) for b in baskets)
    grid, eq_min, eq_last = account_equity(baskets, start, end)
    days = daily_table(grid, eq_min, eq_last)
    worst_day = min(low - o for _, o, low, c, a in days)
    cap, deepest = capital_needed(days)
    return {"n": len(pnl), "net": round(sum(pnl), 2), "worst_basket": round(min(pnl), 2),
            "monthly": {k: round(v, 1) for k, v in sorted(months.items())},
            "worst_day": round(worst_day, 2), "deepest_drop": deepest, "capital_needed_B": cap, "days": days}


def main():
    judge = json.load(open(f'out/search2/juicio/{sys.argv[1] if len(sys.argv) > 1 else "C_p50"}.json'))
    fams = {f.name: f for f in families()}
    out = []
    for name, carril, rr in accepted(judge):
        fam, pick = fams[name], rr["final_pick"]
        entry = {"family": name, "carril": carril, "channel": fam.channel, "filter_text": pick["filter_text"],
                 "rule": pick["rule"], "walk_forward": {k: rr["walk_forward"][k] for k in ("total", "pos_months", "n_months", "traded_months")},
                 "walk_forward_months": [{"month": m["month"], "net": m["net"]} for m in rr["walk_forward"]["months"]],
                 "scenarios": {}}
        for sc in ("A_p50", "C_p50", "C_p90"):
            m = money(run_pick(fam, pick, sc))
            if m is None:
                continue
            days = m.pop("days")
            if sc == "C_p50":
                if carril == "A":
                    best, table = best_multiplier(days, FTMO_2STEP, balance=100_000.0)
                    entry["ftmo_100k"] = {"best": best, "by_k": table,
                                          "start_every_day": challenge_stats(days, best[0], FTMO_2STEP, 100_000.0) if best else None}
                else:
                    months = max(len(m["monthly"]), 1)
                    entry["own_capital"] = {"capital_needed": m["capital_needed_B"],
                                            "eur_month_per_1000": round(1000 * m["net"] / months / max(m["capital_needed_B"], 1e-9), 2)}
            entry["scenarios"][sc] = m
        out.append(entry)
        print(name, carril, json.dumps({sc: {k: v for k, v in s.items() if k != "monthly"} for sc, s in entry["scenarios"].items()}), flush=True)
    dst = Path('out/search2/final'); dst.mkdir(parents=True, exist_ok=True)
    (dst / 'recetas.json').write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(), "recipes": out},
                                                 indent=1, default=str), encoding='utf-8')


if __name__ == '__main__':
    main()
