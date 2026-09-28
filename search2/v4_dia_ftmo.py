"""V4: FTMO day (00:00 CE(S)T) and end-of-day behaviour for R1, R2 and the R1+R2 account.

Rebuilds minute curves with the exact engine (C_p50), the account equity with floating, and the
daily tables with the broker day and with FTMO's CE(S)T day. Saves the CE(S)T tables (used by M).
usage: python search2/v4_dia_ftmo.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '.')
import numpy as np

from research.account_sim import account_equity, daily_table
from research.prop_sim import daily_table_cet
from search2.families import families
from search2.finalize2 import run_pick

RECIPES = {"R1": "out/search2/receta_c2_escalera_n30.json", "R2": "out/search2/receta_c1_seguir_1pata.json"}


def baskets(rows):
    return [(np.array(r[8][0]), np.array(r[8][1]), np.array(r[8][2]), r[5] or 0.0) for r in rows if r[8]], \
           sum(1 for r in rows if r[7])


def main():
    fams = {f.name: f for f in families()}
    per = {}
    for name, path in RECIPES.items():
        rec = json.load(open(path))
        b, censored = baskets(run_pick(fams[rec["family"]], rec, "C_p50"))
        per[name] = b
        print(name, "cestas", len(b), "abiertas al corte de día/6 h (el bot debe cerrarlas igual)", censored, flush=True)
    per["R1+R2"] = per["R1"] + per["R2"]
    out = {}
    for name, b in per.items():
        start = min(int(x[0][0]) for x in b); end = max(int(x[0][-1]) for x in b)
        grid, eq_min, eq_last = account_equity(b, start, end)
        broker, cet = daily_table(grid, eq_min, eq_last), daily_table_cet(grid, eq_min, eq_last)
        wb = min(l - o for _, o, l, c, a in broker); wc = min(l - o for _, o, l, c, a in cet)
        out[name] = {"peor_dia_broker": round(wb, 2), "peor_dia_ftmo": round(wc, 2), "dias": len(cet),
                     "tabla_cet": [(d, round(o, 2), round(l, 2), round(c, 2), a) for d, o, l, c, a in cet]}
        print(f"{name}: peor día (día del broker) {wb:.2f} € | peor día (día FTMO CE(S)T) {wc:.2f} €", flush=True)
    dst = Path('out/search2/v4'); dst.mkdir(parents=True, exist_ok=True)
    (dst / 'v4_dia_ftmo.json').write_text(json.dumps(out), encoding='utf-8')


if __name__ == '__main__':
    main()
