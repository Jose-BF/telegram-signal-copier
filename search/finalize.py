"""Freeze the selection using ONLY search + validation, then run the final exam
(14-25 Sep) and write a plain-Spanish report."""
import hashlib, json, subprocess, sys, time
sys.path.insert(0, '.')
from pathlib import Path
from research.report_stage2 import summarize
from research.describe_genome import describe_genome
from research.signal_filters import describe


def main():

    P = Path(sys.argv[1] if len(sys.argv) > 1 else 'search/pipeline')
    sel = json.load(open(P / 'selection_stage2.json'))
    fin = json.load(open(P / 'finalists.json'))
    S = summarize(P / 'stage2_search_validation.json')
    g = lambda r, sc, p, k='net': r.get(sc, {}).get(p, {}).get(k)
    rows = []
    for r in S:
        ch, i = r['channel'], r['index']
        st1 = fin[ch][i - 1] if i > 0 else None      # index 0 = current strategy (reference)
        v = [g(r, sc, 'validacion') for sc in ('A_p50', 'C_p50', 'C_p90')]
        wk_pos, wk = g(r, 'C_p50', 'validacion', 'weeks_pos'), g(r, 'C_p50', 'validacion', 'weeks')
        checks = {
            'supera_suerte': bool(st1 and st1['beats_bar']),
            'vecinos_ok': bool(st1 and (st1.get('neighbors_positive_pct') or 0) >= 70),
            'valida_3_brokers': all(x is not None and x > 0 for x in v),
            'semanas_ok': bool(wk and wk_pos / wk >= 0.5),
        }
        rows.append({**r, 'stage1': st1, 'checks': checks, 'aprobada': i > 0 and all(checks.values()),
                     'descripcion': describe_genome(sel[ch][i]['genome']) + ' | señales: ' + describe(sel[ch][i]['filter'])
                     + (' | OPERANDO AL REVÉS DE LA SEÑAL' if sel[ch][i].get('universe') == 'inverted' else '')})
    frozen = {'time': time.strftime('%Y-%m-%d %H:%M'), 'approved': [(r['channel'], r['index']) for r in rows if r['aprobada']]}
    blob = json.dumps(frozen, sort_keys=True).encode()
    frozen['sha256'] = hashlib.sha256(blob).hexdigest()
    json.dump(frozen, open(P / 'frozen_selection.json', 'w'), indent=1)
    print('FROZEN', frozen, flush=True)
    # final exam only now
    subprocess.run([sys.executable, 'search/stage2.py', str(P / 'selection_stage2.json'), str(P / 'stage2_final.json'),
                    '2026-09-14:2026-09-26'], check=True)
    F = {(r['channel'], r['index']): r for r in summarize(P / 'stage2_final.json')}
    for r in rows:
        f = F.get((r['channel'], r['index']), {})
        r['final'] = {sc: f.get(sc, {}).get('final') for sc in ('A_p50', 'C_p50', 'C_p90')}
    json.dump(rows, open(P / 'report_rows.json', 'w'), indent=1, default=str)
    print('report rows written', flush=True)



if __name__ == '__main__':
    main()
