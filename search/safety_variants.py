"""Canal2 approved management + disaster stops. Filtered and unfiltered, 3 brokers, Jan-25 Sep."""
import json, sys, time
sys.path.insert(0, '.')
from pathlib import Path
from research.dubai_iterative.contracts import StrategyGenome
from research.history_search import evaluate


def main():
    sel = json.load(open('search/pipeline/selection_stage2.json'))
    base = sel['canal2'][1]['genome']; flt = sel['canal2'][1]['filter']
    V = [('base', {})] + [(f'cap{c}', dict(stop_mode='basket_money', stop_value=float(c))) for c in (100, 150, 200, 300)] \
        + [('t240', dict(time_exit_mode='always', time_exit_min=240)), ('t480', dict(time_exit_mode='always', time_exit_min=480)),
           ('cap200_t480', dict(stop_mode='basket_money', stop_value=200.0, time_exit_mode='always', time_exit_min=480))]
    gs = []
    for name, ch in V:
        g = StrategyGenome.from_dict({**base, **ch}); gs.append(g)
    U = 'runtime_data/signal_universe_v1/'
    days = sorted(p.stem for p in Path('runtime_data/ticks_all/XAUUSD').glob('*.parquet') if '2026-01-01' <= p.stem < '2026-09-26')
    out = {'variants': [n for n, _ in V], 'rows': {}}
    for sc in ('A_p50', 'C_p50', 'C_p90'):
        cal = {sc: json.load(open(f'runtime_data/execution_calibration_v2/{sc}_q450.json'))}
        for tag, filters in (('filtered', {'canal2': [flt] * len(gs)}), ('all', None)):
            t = time.time()
            rows = evaluate('runtime_data/ticks_all', days, {'canal2': gs}, cal, workers=2, universe=U + 'signals.jsonl',
                            filters=filters, features=U + 'features.jsonl')
            out['rows'][f'{sc}|{tag}'] = rows
            print(sc, tag, len(rows), round(time.time() - t), flush=True)
    json.dump(out, open('search/pipeline/safety_variants.json', 'w'))
    print('done', flush=True)



if __name__ == '__main__':
    main()
