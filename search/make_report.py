"""report_rows.json -> informe en español llano (markdown)."""
import json, sys
from pathlib import Path


def main():

    P = Path(sys.argv[1] if len(sys.argv) > 1 else 'search/pipeline')
    rows = json.load(open(P / 'report_rows.json'))
    frozen = json.load(open(P / 'frozen_selection.json'))
    NAME = {'canal1': 'Canal 1 (Dubai)', 'canal2': 'Canal 2 (Gold NOW)'}
    SC = [('A_p50', 'broker rápido'), ('C_p50', 'broker lento'), ('C_p90', 'broker atascado')]


    def e(x):
        return '—' if x is None else f"{x:+.0f} €"


    def per(r, p, sc='C_p50'):
        return (r.get(sc) or {}).get(p) or {}


    out = [f"# Resultados de la primera búsqueda masiva\n",
           f"Selección congelada {frozen['time']} (huella {frozen['sha256'][:12]}) ANTES del examen final.\n",
           "Importes con lote de la estrategia (0.01 base). 'Búsqueda' = donde se eligió; 'Validación' = jul–11 sep, "
           "nunca vista al elegir; 'Examen final' = 14–25 sep, solo se miró tras congelar.\n"]
    for ch in ('canal1', 'canal2'):
        rs = [r for r in rows if r['channel'] == ch]
        if not rs:
            continue
        out.append(f"\n## {NAME[ch]}\n")
        out.append("| # | Qué hace | Búsqueda | Validación (rápido / lento / atascado) | Examen final (lento) | Peor cesta valid. | Peor semana valid. | Semanas + valid. | Aprobada |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for r in rs:
            tag = 'ACTUAL (referencia)' if r['index'] == 0 else str(r['index'])
            v = ' / '.join(e(per(r, 'validacion', sc).get('net')) for sc, _ in SC)
            pv = per(r, 'validacion')
            fin = (r.get('final') or {}).get('C_p50') or {}
            ok = 'referencia' if r['index'] == 0 else ('✅' if r['aprobada'] else '❌ ' + ', '.join(k for k, x in r['checks'].items() if not x))
            out.append(f"| {tag} | {r['descripcion'].replace(' | ', '; ')} | {e(per(r, 'busqueda').get('net'))} (n={per(r, 'busqueda').get('n')}) | {v} (n={pv.get('n')}) "
                       f"| {e(fin.get('net'))} (n={fin.get('n')}) | {e(pv.get('worst'))} | {e(pv.get('worst_week'))} "
                       f"| {pv.get('weeks_pos')}/{pv.get('weeks')} | {ok} |")
        out.append("\nCuenta de fondeo 10k (lote multiplicado por el 'lote seguro'):\n")
        out.append("| # | Lote seguro (x) | Caída máx. cuenta valid. | Peor día valid. | Retos 2 fases (pasa / falla diario / falla total / sin terminar) | Monte Carlo pasa / falla % |")
        out.append("|---|---|---|---|---|---|")
        for r in rs:
            a = (r.get('account') or {}).get('validacion') or {}
            c = a.get('challenge') or {}
            mc = a.get('montecarlo_pass_fail_pct') or [None, None]
            out.append(f"| {r['index']} | {r.get('lot_multiplier_10k')} | {e(a.get('max_drop_eur'))} | {e(a.get('worst_day_eur'))} "
                       f"| {c.get('pass')}/{c.get('fail_diaria')}/{c.get('fail_total')}/{c.get('unfinished')} | {mc[0]} / {mc[1]} |")
    (P / 'informe.md').write_text('\n'.join(out) + '\n')
    print('\n'.join(out))



if __name__ == '__main__':
    main()
