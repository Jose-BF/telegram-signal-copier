"""Stress test of the approved managements WITHOUT their signal filter: all real signals,
random-time signals (3 seeds) and flipped signals, Jan-25 Sep, broker C_p50."""
import json, sys, time
sys.path.insert(0, '.')
from pathlib import Path
from research.dubai_iterative.contracts import StrategyGenome
from research.history_search import evaluate


def main():
    sel = json.load(open('search/pipeline/selection_stage2.json'))
    U = 'runtime_data/signal_universe_v1/'
    days = sorted(p.stem for p in Path('runtime_data/ticks_all/XAUUSD').glob('*.parquet') if '2026-01-01' <= p.stem < '2026-09-26')
    cal = {'C_p50': json.load(open('runtime_data/execution_calibration_v2/C_p50_q450.json'))}
    pops = {'canal1': [StrategyGenome.from_dict(sel['canal1'][3]['genome'])],
            'canal2': [StrategyGenome.from_dict(sel['canal2'][1]['genome'])]}
    out = {}
    for name in ('signals', 'placebo_random_time_s0', 'placebo_random_time_s1', 'placebo_random_time_s2', 'placebo_flipped_s0'):
        t = time.time()
        rows = evaluate('runtime_data/ticks_all', days, pops, cal, workers=2, universe=U + name + '.jsonl')
        out[name] = rows
        print(name, len(rows), round(time.time() - t), flush=True)
    json.dump(out, open('search/pipeline/stress.json', 'w'))
    print('done', flush=True)



if __name__ == '__main__':
    main()
