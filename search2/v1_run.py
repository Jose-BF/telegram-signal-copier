"""V1: placebo of the whole search-2 procedure (criterion fixed in search2/v1_preregistro.json).

Judges every family on the real universe and on each placebo universe with the identical code
(search2/judge_run.judge_family) and counts "findings" per universe:
  strong: a simple-filter variant (simples_n30 / simples_n60) with total > 0 and every out-of-sample
          month positive (at least 4 months);
  weak:   a simple-filter variant with total > 0 and at most one negative out-of-sample month.
usage: python search2/v1_run.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')
from research.signal_filters import grid_v2
from search2.families import FLIP, families, placebo_universes
from search2.judge_run import judge_family

SIMPLE = ("simples_n30", "simples_n60")


def findings(res):
    strong, weak = [], []
    for carril in ("A", "B"):
        r = (res or {}).get(carril)
        if not r:
            continue
        for v in SIMPLE:
            x = r["variants"][v]
            if x["total"] > 0 and x["n_months"] >= 4 and x["pos_months"] == x["n_months"]:
                strong.append(f"{carril}:{v}")
            elif x["total"] > 0 and x["pos_months"] >= x["n_months"] - 1:
                weak.append(f"{carril}:{v}")
    return strong, weak


def main():
    filters = grid_v2()
    s1_base, s1_flip = placebo_universes(1)
    table = {}
    for fam in families():
        labels = [("real", fam.universe), ("placebo", fam.placebo),
                  ("placebo_s1", s1_flip if fam.universe == FLIP else s1_base)]
        out = judge_family(fam, "C_p50", filters, labels=labels)
        if out is None:
            print("sin datos reales", fam.name); continue
        for label, _ in labels:
            if not out.get(label):
                continue
            s, w = findings(out[label])
            table.setdefault(label, {})[fam.name] = {"fuertes": s, "debiles": w}
    summary = {}
    for label, fams in table.items():
        strong = sorted({f for f, v in fams.items() if v["fuertes"]})
        weak = sorted({f for f, v in fams.items() if v["debiles"] and not v["fuertes"]})
        summary[label] = {"familias_evaluadas": len(fams), "hallazgos_fuertes": strong, "hallazgos_debiles": weak}
        print(f"{label:12s} familias {len(fams):2d} | fuertes {len(strong)}: {strong} | debiles {len(weak)}: {weak}")
    out = Path('out/search2/v1'); out.mkdir(parents=True, exist_ok=True)
    (out / 'v1_resultado.json').write_text(json.dumps({"summary": summary, "detail": table}, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
