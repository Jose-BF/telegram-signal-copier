"""M3 - delivery 1: map of the terrain (level-1 calculator, approximate, no broker latencies).

Writes out/search2/mapa/{mapa.json,informe.html}. Every number comes from
runtime_data/signal_paths_v1 (hashes in the .json files there) or from 1 s paths rebuilt from
runtime_data/ticks_all. Money: EUR at 0.01 lot per leg weight unit.

"Señal" = real minus the average of the three random-time placebos (same day and direction):
what the provider's timing adds on top of the market drift.
"""
import html
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '.')
import numpy as np

from research.basket_grid import BasketRule, build_paths, evaluate as basket_eval
from research.bracket_grid import SimpleRule, simple_outcomes, TP_GRID, SL_GRID
from research.signal_paths import TIME_EXITS_MIN, load

P = 'runtime_data/signal_paths_v1/'
U = 'runtime_data/signal_universe_v1/'
PLACEBOS = ('placebo_random_time_s0', 'placebo_random_time_s1', 'placebo_random_time_s2')
OUT = Path('out/search2/mapa')


def months_of(z):
    return np.array([o[:7] for o in z['observed']])


def simple_maps():
    times = (30, 60, 240, None)
    rules = [SimpleRule(inv, tp, sl, t) for inv in (False, True) for t in times for tp in TP_GRID for sl in SL_GRID]
    res = {}
    for u in ('real',) + PLACEBOS:
        z = load(P + u + '.npz'); M = simple_outcomes(z, rules); mon = months_of(z)
        for ch in ('canal1', 'canal2'):
            m = z['channel'] == ch
            months = sorted(set(mon[m]))
            res[(u, ch)] = {"mean": M[m].mean(0), "net": M[m].sum(0), "n": int(m.sum()),
                            "pos_months": np.array([(M[m & (mon == mo)].sum(0) > 0) for mo in months]).sum(0),
                            "months": len(months)}
    out = {}
    for ch in ('canal1', 'canal2'):
        real = res[('real', ch)]
        plac = np.mean([res[(u, ch)]["mean"] for u in PLACEBOS], axis=0)
        best_plac_net = np.max([res[(u, ch)]["net"] for u in PLACEBOS], axis=0)
        cells = []
        for j, r in enumerate(rules):
            cells.append({"invert": r.invert, "tp": r.tp, "sl": r.sl, "t": r.time_min,
                          "mean": round(float(real["mean"][j]), 3), "net": round(float(real["net"][j]), 1),
                          "signal": round(float(real["mean"][j] - plac[j]), 3), "pos_months": int(real["pos_months"][j]),
                          "months": real["months"], "beats_all_placebos": bool(real["net"][j] > best_plac_net[j])})
        out[ch] = {"n": real["n"], "cells": cells,
                   "share_positive": round(float(np.mean(real["net"] > 0)), 3),
                   "share_positive_placebo": round(float(np.mean([np.mean(res[(u, ch)]["net"] > 0) for u in PLACEBOS])), 3)}
    return out, times


def family_555():
    """555 neighbourhood on canal2: adverse x rebound x ladder step, the rest of the 555 unchanged."""
    base = dict(entry="adverse_reversal", weights=(0.04, 0.03, 0.03, 0.03, 0.03), tp_steps=(0.5, 1.0, 1.5, 2.0, 2.5),
                trail=30.0, lock_arm_eur=30.0, lock_give_eur=1.0, time_min=180, time_mode="non_negative", expiry_min=30)
    grid = [(x, y, s) for x in (0.5, 1.0, 1.5, 2.0, 3.0) for y in (0.5, 1.0, 1.5, 2.0, 3.0) for s in (1.0, 1.5, 2.0, 3.0)]
    return [BasketRule(e_x=x, e_y=y, step=s, **base) for x, y, s in grid], grid


def family_dubai():
    """Dubai neighbourhood on canal1: market entry + adverse ladder (0.01, 0.04, 0.04) x basket cap x time."""
    grid = [(s, c, t) for s in (2.0, 3.0, 4.0, 5.0, 6.0) for c in (15.0, 25.0, 35.0, 50.0) for t in (30, 40, 60, 120)]
    return [BasketRule(weights=(0.01, 0.04, 0.04), step=s, basket_stop_eur=c, lock_arm_eur=10.0, lock_give_eur=2.0,
                       time_min=t, time_mode="loss_only", expiry_min=15) for s, c, t in grid], grid


def basket_maps():
    fams = {"555": (family_555(), 'canal2'), "dubai": (family_dubai(), 'canal1')}
    stats = {}
    for u in ('real',) + PLACEBOS:
        paths = build_paths('runtime_data/ticks_all', U + ('signals' if u == 'real' else u) + '.jsonl')
        mon = np.array([o[:7] for o in paths.observed])
        for name, ((rules, grid), ch) in fams.items():
            m = paths.channel == ch
            o = basket_eval(paths, rules, mask=m)
            pm = mon[m]
            months = sorted(set(pm))
            stats[(u, name)] = {"net": o["pnl"].sum(0), "n": o["legs"].astype(bool).sum(0),
                                "worst": o["pnl"].min(0), "min_eq": o["min_equity"].min(0),
                                "pos_months": np.array([(o["pnl"][pm == mo].sum(0) > 0) for mo in months]).sum(0),
                                "months": len(months)}
        print('baskets', u, flush=True)
    out = {}
    for name, ((rules, grid), ch) in fams.items():
        r = stats[('real', name)]
        pl = np.mean([stats[(u, name)]["net"] for u in PLACEBOS], axis=0)
        best_pl = np.max([stats[(u, name)]["net"] for u in PLACEBOS], axis=0)
        out[name] = {"channel": ch, "cells": [
            {"params": list(g), "net": round(float(r["net"][j]), 1), "baskets": int(r["n"][j]),
             "per_basket": round(float(r["net"][j] / max(r["n"][j], 1)), 3),
             "placebo_net_avg": round(float(pl[j]), 1), "beats_all_placebos": bool(r["net"][j] > best_pl[j]),
             "worst_basket": round(float(r["worst"][j]), 1), "deepest_floating": round(float(r["min_eq"][j]), 1),
             "pos_months": int(r["pos_months"][j]), "months": r["months"]} for j, g in enumerate(grid)]}
    return out


def mfe_mae():
    out = {}
    for u in ('real', 'placebo_random_time_s0'):
        z = load(P + u + '.npz'); i = np.arange(len(z['ids'])); d = z['direction'].astype(int)
        for ch in ('canal1', 'canal2'):
            m = z['channel'] == ch
            rows = {}
            for j, t in enumerate(TIME_EXITS_MIN):
                mfe = z['mfe'][i, d, j][m]; mae = z['mae'][i, d, j][m]; val = z['exit_val'][i, d, j][m]
                ok = ~np.isnan(val)
                rows[t] = {k: [round(float(np.nanpercentile(a[ok], q)), 2) for q in (25, 50, 75)]
                           for k, a in (("mfe", mfe), ("mae", mae), ("value", val))}
            out[f"{u}|{ch}"] = rows
    return out


def color(v, scale):
    if v is None or np.isnan(v):
        return "#eee"
    x = max(-1.0, min(1.0, v / scale))
    if x >= 0:
        return f"rgb({int(255 - 120 * x)},{int(255 - 40 * x)},{int(255 - 120 * x)})"
    return f"rgb(255,{int(255 + 130 * x)},{int(255 + 130 * x)})"


def heat_table(cells, key, title, scale):
    tps = [t for t in TP_GRID]; sls = [s for s in SL_GRID]
    look = {(c["tp"], c["sl"]): c for c in cells}
    h = [f'<table class="heat"><caption>{html.escape(title)}</caption><tr><th>TP \\ SL</th>'
         + ''.join(f'<th>{s or "sin"}</th>' for s in sls) + '</tr>']
    for tp in tps:
        h.append(f'<tr><th>{tp or "sin"}</th>')
        for sl in sls:
            c = look.get((tp, sl))
            v = None if c is None else c[key]
            mark = ' ★' if c and c["beats_all_placebos"] and c["pos_months"] >= 0.7 * c["months"] and c["mean"] > 0 else ''
            h.append(f'<td style="background:{color(v, scale)}" title="neto {c and c["net"]} € · meses+ {c and c["pos_months"]}/{c and c["months"]}">'
                     f'{"" if v is None else f"{v:+.2f}"}{mark}</td>')
        h.append('</tr>')
    h.append('</table>')
    return ''.join(h)


def render(data, times):
    parts = ['<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>Mapa de señales</title><style>',
             ':root{--bg:#fff;--fg:#1a1a1a;--mut:#666}body{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px}',
             'table.heat{border-collapse:collapse;margin:8px 12px 18px 0;display:inline-table;font-size:12px}',
             '.heat td,.heat th{border:1px solid #ddd;padding:3px 5px;text-align:right}.heat caption{font-weight:600;text-align:left;padding:4px 0}',
             'table.list{border-collapse:collapse;font-size:12px;margin:6px 0 16px}.list td,.list th{border-bottom:1px solid #e5e5e5;padding:3px 8px;text-align:right}',
             '.note{color:var(--mut);font-size:12px}.wrap{overflow-x:auto}h2{margin-top:28px}</style></head><body>',
             '<h1>Mapa de las señales: dónde está el terreno bueno</h1>',
             f'<p class="note">Generado {time.strftime("%d/%m/%Y %H:%M")}. Calculadora rápida (nivel 1): aproximada y sin retrasos del broker. '
             'Importes en € a 0,01 lotes. "Señal" = real menos la media de 3 placebos de hora al azar (mismo día y dirección): '
             'lo que aporta el momento del proveedor por encima de la tendencia del oro. ★ = gana a los 3 placebos, media positiva y ≥70 % de meses positivos.</p>']
    for ch, name in (('canal1', 'Canal 1 · Dubai'), ('canal2', 'Canal 2 · Gold')):
        d = data["simple"][ch]
        parts.append(f'<h2>{name}: una entrada, objetivo (TP) y stop (SL)</h2>'
                     f'<p>{d["n"]} señales. Reglas con beneficio: <b>{d["share_positive"]*100:.1f} %</b> con señales reales frente a '
                     f'{d["share_positive_placebo"]*100:.1f} % con placebos.</p>')
        for inv in (False, True):
            for t in times:
                cells = [c for c in d["cells"] if c["invert"] == inv and c["t"] == t]
                lab = f'{"Invertir" if inv else "Seguir"} · salida {t or "al corte (6 h o fin de día)"}{" min" if t else ""}'
                parts.append('<div class="wrap">' + heat_table(cells, "mean", lab + " · € por operación", 1.5)
                             + heat_table(cells, "signal", lab + " · parte de la señal", 1.0) + '</div>')
        top = sorted(d["cells"], key=lambda c: -c["net"])[:10]
        parts.append('<table class="list"><tr><th>regla</th><th>neto €</th><th>€/op</th><th>señal €/op</th><th>meses+</th><th>gana a placebos</th></tr>'
                     + ''.join(f'<tr><td style="text-align:left">{"invertir" if c["invert"] else "seguir"} TP {c["tp"] or "-"} SL {c["sl"] or "-"} T {c["t"] or "corte"}</td>'
                               f'<td>{c["net"]:+.0f}</td><td>{c["mean"]:+.2f}</td><td>{c["signal"]:+.2f}</td><td>{c["pos_months"]}/{c["months"]}</td>'
                               f'<td>{"sí" if c["beats_all_placebos"] else "no"}</td></tr>' for c in top) + '</table>')
    for fam, title, heads in (("555", "Familia 555 (Gold): retroceso × rebote × paso de escalera", ("retroceso $", "rebote $", "paso $")),
                              ("dubai", "Familia Dubai: paso de escalera × tope de cesta × tiempo", ("paso $", "tope €", "tiempo min"))):
        cells = sorted(data["baskets"][fam]["cells"], key=lambda c: -c["net"])
        parts.append(f'<h2>{title}</h2><p class="note">El resto de la gestión igual que la estrategia en vivo. '
                     'Aviso: en cestas la calculadora tiene un error de ±0,5 €/cesta (ver control M2).</p>'
                     '<table class="list"><tr>' + ''.join(f'<th>{h}</th>' for h in heads)
                     + '<th>neto €</th><th>cestas</th><th>€/cesta</th><th>placebo €</th><th>gana a placebos</th><th>peor cesta €</th><th>flotante más hondo €</th><th>meses+</th></tr>'
                     + ''.join('<tr>' + ''.join(f'<td>{p}</td>' for p in c["params"])
                               + f'<td>{c["net"]:+.0f}</td><td>{c["baskets"]}</td><td>{c["per_basket"]:+.2f}</td><td>{c["placebo_net_avg"]:+.0f}</td>'
                               f'<td>{"sí" if c["beats_all_placebos"] else "no"}</td><td>{c["worst_basket"]:.0f}</td><td>{c["deepest_floating"]:.0f}</td>'
                               f'<td>{c["pos_months"]}/{c["months"]}</td></tr>' for c in cells[:25]) + '</table>')
    parts.append('<h2>Cómo se mueve el precio tras una señal (siguiendo su dirección)</h2><table class="list"><tr><th>universo · canal</th><th>min</th>'
                 '<th>a favor (MFE) p25/p50/p75 $</th><th>en contra (MAE) p25/p50/p75 $</th><th>resultado si cierras ahí p25/p50/p75 $</th></tr>')
    for key, rows in data["mfe_mae"].items():
        for t, v in rows.items():
            parts.append(f'<tr><td style="text-align:left">{key.replace("|", " · ")}</td><td>{t}</td><td>{" / ".join(map(str, v["mfe"]))}</td>'
                         f'<td>{" / ".join(map(str, v["mae"]))}</td><td>{" / ".join(map(str, v["value"]))}</td></tr>')
    parts.append('</table></body></html>')
    return ''.join(parts)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t = time.time()
    simple, times = simple_maps(); print('simple', round(time.time() - t), 's', flush=True)
    baskets = basket_maps(); print('baskets', round(time.time() - t), 's', flush=True)
    data = {"simple": simple, "baskets": baskets, "mfe_mae": mfe_mae(),
            "sources": {f: json.load(open(P + f + '.json'))["npz_sha256"] for f in ('real',) + PLACEBOS}}
    (OUT / 'mapa.json').write_text(json.dumps(data), encoding='utf-8')
    (OUT / 'informe.html').write_text(render(data, times), encoding='utf-8')
    print('ok', round(time.time() - t), 's')


if __name__ == '__main__':
    main()
