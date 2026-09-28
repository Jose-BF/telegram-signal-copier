"""M8 for one explicit recipe (family + filter + genome index): three brokers with minute curves,
account daily table, FTMO 2-step and carril-B capital.
usage: python search2/finalize_recipe.py out/search2/receta_c2_escalera_n30.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')
from research.prop_rules import FTMO_2STEP
from research.prop_sim import best_multiplier, challenge_stats, monte_carlo
from search2.families import families
from search2.finalize2 import money, run_pick


def main():
    rec = json.load(open(sys.argv[1]))
    fam = {f.name: f for f in families()}[rec["family"]]
    out = {"recipe": rec, "rule": repr(fam.rules[rec["genome_index"]]), "scenarios": {}}
    for sc in ("A_p50", "C_p50", "C_p90"):
        m = money(run_pick(fam, rec, sc))
        days = m.pop("days")
        m["active_days"] = sum(1 for d in days if d[4])
        if sc == "C_p50":
            best, table = best_multiplier(days, FTMO_2STEP, balance=100_000.0, max_fail=0.2)
            out["ftmo_100k"] = {"best": best, "by_k": table}
            if best:
                out["ftmo_100k"]["start_every_day"] = challenge_stats(days, best[0], FTMO_2STEP, 100_000.0)
            out["daily"] = [(d, round(o, 2), round(l, 2), round(c, 2), a) for d, o, l, c, a in days]
        out["scenarios"][sc] = m
        print(sc, {k: v for k, v in m.items() if k != "monthly"}, m["monthly"], flush=True)
    print("FTMO 100k:", out.get("ftmo_100k", {}).get("best"))
    dst = Path('out/search2/final'); dst.mkdir(parents=True, exist_ok=True)
    (dst / (Path(sys.argv[1]).stem + '_final.json')).write_text(json.dumps(out, indent=1, default=str), encoding='utf-8')


if __name__ == '__main__':
    main()
