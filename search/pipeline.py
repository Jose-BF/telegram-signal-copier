"""After stage 1: select finalists (luck bar), neighbours, exact re-run on search+validation,
freeze the selection by validation, then run the final exam. Everything logged to search/pipeline/."""
import json, os, subprocess, sys, time
sys.path.insert(0, '.')
from pathlib import Path
import numpy as np
from research.search_judge import judge, MIN_N
from research.signal_filters import describe, passes, load_features
from research.neighbors import genome_neighbors, filter_neighbors
from research.history_search import evaluate
from research.dubai_iterative.contracts import StrategyGenome


def main():

    OUT = Path(os.environ.get('PIPE_OUT', 'search/pipeline')); OUT.mkdir(exist_ok=True)
    MAXF = int(os.environ.get('PIPE_MAX', '12'))
    U = 'runtime_data/signal_universe_v1/'
    FEAT = {'real': U + 'features.jsonl', 'placebo': U + 'features_placebo_random_time_s0.jsonl',
            'inverted': U + 'features_placebo_flipped_s0.jsonl'}
    SPLIT = {'canal1': '2026-04-01', 'canal2': '2026-05-20'}
    G = json.load(open('search/stage1/genomes.json'))
    log = open(OUT / 'log.txt', 'a')
    def say(*a):
        msg = time.strftime('%H:%M ') + ' '.join(str(x) for x in a); print(msg, flush=True); log.write(msg + '\n'); log.flush()

    res, filters = judge('search/stage1', FEAT, None, SPLIT)
    bars = {ch: float(np.max(res[ch]['placebo']['t'])) for ch in res}
    say('luck bars', bars)
    # ---- 1. finalists
    final = {}
    for ch in ('canal1', 'canal2'):
        picked, per_g, per_f = [], {}, {}
        cands = []
        for uni in ('real', 'inverted'):
            t = res[ch][uni]['t']
            for fi, gi in zip(*np.unravel_index(np.argsort(-t, axis=None), t.shape)):
                if not np.isfinite(t[fi, gi]) or len(cands) > 4000:
                    break
                cands.append((float(t[fi, gi]), uni, int(fi), int(gi)))
        cands.sort(reverse=True)
        for sc, uni, fi, gi in cands:
            passes_bar = sc > bars[ch]
            key_f = json.dumps(filters[fi], sort_keys=True)
            if per_g.get((uni, gi), 0) >= 2 or per_f.get(key_f, 0) >= 2:
                continue
            if not passes_bar and sum(1 for p in picked if not p['beats_bar']) >= 3:
                continue
            picked.append({'universe': uni, 'genome_index': gi, 'filter_index': fi, 'score': round(sc, 2), 'beats_bar': passes_bar,
                           'n': int(res[ch][uni]['n'][fi, gi]), 'net_search': round(float(res[ch][uni]['net'][fi, gi]), 1)})
            per_g[(uni, gi)] = per_g.get((uni, gi), 0) + 1; per_f[key_f] = per_f.get(key_f, 0) + 1
            if len(picked) >= MAXF:
                break
        final[ch] = picked
        say(ch, 'finalists', len(picked), 'beating the luck bar', sum(p['beats_bar'] for p in picked))
    json.dump(final, open(OUT / 'finalists.json', 'w'), indent=1)
    # ---- 2. neighbours: filter neighbours post-hoc; genome neighbours simulated on the search period
    days_search = sorted(p.stem for p in Path('runtime_data/ticks_all/XAUUSD').glob('*.parquet') if '2026-01-01' <= p.stem < '2026-07-01')
    cal = {'C': json.load(open('runtime_data/execution_calibration_v2/C_p50_q450.json'))}
    for ch, items in final.items():
        for it in items:
            uni = it['universe']; r = res[ch][uni]; gi, fi = it['genome_index'], it['filter_index']
            feats = load_features(FEAT[uni])
            P, C, sig = r['P'], r['C'], r['sig_ids']
            fn = []
            for spec in filter_neighbors(filters[fi]):
                m = np.array([passes(spec, feats.get(s)) for s in sig])
                fn.append(float((P[m, gi]).sum()))
            it['filter_neighbors_net'] = [round(x, 1) for x in fn]
        genomes_n = {}
        for it in items:
            gd = G[ch][it['genome_index']]['genome']
            genomes_n[(it['universe'], it['genome_index'])] = genome_neighbors(ch, gd)
        for uni in ('real', 'inverted'):
            keys = [k for k in genomes_n if k[0] == uni]
            flat = [(k, g) for k in keys for g in genomes_n[k]]
            if not flat:
                continue
            pops = {ch: [StrategyGenome.from_dict(g) for _, g in flat]}
            upath = U + ('signals.jsonl' if uni == 'real' else 'placebo_flipped_s0.jsonl')
            t0 = time.time()
            rows = evaluate('runtime_data/ticks_all', days_search, pops, cal, workers=2, universe=upath)
            say(ch, uni, 'genome neighbours', len(flat), 'secs', round(time.time() - t0))
            feats = load_features(FEAT[uni])
            for it in [i for i in items if i['universe'] == uni]:
                spec = filters[it['filter_index']]
                nets = []
                for j, (k, g) in enumerate(flat):
                    if k != (uni, it['genome_index']):
                        continue
                    nets.append(round(sum((r[5] or 0) for r in rows if r[2] == j and passes(spec, feats.get(r[4]))), 1))
                it['genome_neighbors_net'] = nets
    for ch, items in final.items():
        for it in items:
            allnb = it.get('filter_neighbors_net', []) + it.get('genome_neighbors_net', [])
            it['neighbors_positive_pct'] = round(100 * sum(x > 0 for x in allnb) / len(allnb), 1) if allnb else None
    json.dump(final, open(OUT / 'finalists.json', 'w'), indent=1)
    say('neighbours done')
    if os.environ.get('PIPE_STOP') == 'neighbours':
        sys.exit(0)
    # ---- 3. exact re-run search + validation
    sel = {ch: [{'genome': G[ch][it['genome_index']]['genome'], 'filter': filters[it['filter_index']], 'universe': it['universe'],
                 'label': f"{'INVERTIDA ' if it['universe'] == 'inverted' else ''}gestión #{it['genome_index']} | {describe(filters[it['filter_index']])}"}
                for it in items] for ch, items in final.items()}
    from research.history_space import live
    lv = live()
    for ch in sel:
        sel[ch].insert(0, {'genome': lv[ch].to_dict(), 'filter': {}, 'universe': 'real', 'label': 'estrategia actual (referencia)'})
    json.dump(sel, open(OUT / 'selection_stage2.json', 'w'), indent=1)
    subprocess.run([sys.executable, 'search/stage2.py', str(OUT / 'selection_stage2.json'), str(OUT / 'stage2_search_validation.json'),
                    '2026-01-01:2026-09-12'], check=True)
    say('stage 2 done')



if __name__ == '__main__':
    main()
