import json, sys, numpy as np
sys.path.insert(0, '.')
from research.search_judge import judge
from research.signal_filters import describe


def main():
    U = 'runtime_data/signal_universe_v1/'
    unis = {'real': U + 'features.jsonl', 'placebo': U + 'features_placebo_random_time_s0.jsonl'}
    if len(sys.argv) > 1 and sys.argv[1] == 'all':
        unis['inverted'] = U + 'features_placebo_flipped_s0.jsonl'
    G = json.load(open('search/stage1/genomes.json'))
    res, filters = judge('search/stage1', unis, None, {'canal1': '2026-04-01', 'canal2': '2026-05-20'})
    summary = {}
    for ch in ('canal1', 'canal2'):
        r = res[ch]
        bar = float(np.max(r['placebo']['t'])); bar95 = float(np.percentile(r['placebo']['t'][np.isfinite(r['placebo']['t'])], 99)) if np.isfinite(r['placebo']['t']).any() else None
        print('==', ch, 'genomes', r['real']['genomes'], '| luck bar (best placebo score) %.2f' % bar, '| placebo eligible', int(np.isfinite(r['placebo']['t']).sum()), 'real eligible', int(np.isfinite(r['real']['t']).sum()))
        live_t = r['real']['t'][0, 0]; print('   live genome, no filter: score', round(float(live_t), 2) if np.isfinite(live_t) else 'no elegible',
                                           'net', round(float(r['real']['net'][0, 0]), 1), 'n', int(r['real']['n'][0, 0]))
        for uni in [u for u in ('real', 'inverted') if u in r]:
            t = r[uni]['t']; order = np.dstack(np.unravel_index(np.argsort(-t, axis=None), t.shape))[0][:8]
            print('   top', uni)
            for fi, gi in order:
                if not np.isfinite(t[fi, gi]): break
                print('     score %.2f %s n %d net %.0f h1 %.0f h2 %.0f | genome %d (%s) | %s' % (t[fi, gi], 'SUPERA' if t[fi, gi] > bar else 'no supera', r[uni]['n'][fi, gi], r[uni]['net'][fi, gi], r[uni]['h1'][fi, gi], r[uni]['h2'][fi, gi], gi, G[ch][gi]['family'], describe(filters[fi])))



if __name__ == '__main__':
    main()
