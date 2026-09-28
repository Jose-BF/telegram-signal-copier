"""The combos chosen on RANDOM-time signals (placebo finalists), now run on the REAL signals, Jan-25 Sep."""
import json, sys, time
sys.path.insert(0, '.')
from pathlib import Path
from research.dubai_iterative.contracts import StrategyGenome
from research.history_search import evaluate
from research.report_stage2 import summarize


def main():
    pp = json.load(open('search/pipeline/placebo_procedure.json'))['selection']
    sel = {ch: [{**x, 'universe': 'real'} for x in pp[ch]] for ch in pp}
    U = 'runtime_data/signal_universe_v1/'
    days = sorted(p.stem for p in Path('runtime_data/ticks_all/XAUUSD').glob('*.parquet') if '2026-01-01' <= p.stem < '2026-09-26')
    out = {'selection': sel, 'period': '2026-01-01:2026-09-26', 'rows': {}}
    for sc in ('A_p50', 'C_p50', 'C_p90'):
        cal = {sc: json.load(open(f'runtime_data/execution_calibration_v2/{sc}_q450.json'))}
        t0 = time.time()
        out['rows'][sc] = evaluate('runtime_data/ticks_all', days, {ch: [StrategyGenome.from_dict(x['genome']) for x in sel[ch]] for ch in sel},
                                   cal, workers=2, universe=U + 'signals.jsonl', filters={ch: [x['filter'] for x in sel[ch]] for ch in sel},
                                   features=U + 'features.jsonl', curves=(sc == 'C_p50'))
        print(sc, len(out['rows'][sc]), round(time.time() - t0), flush=True)
    json.dump(out, open('search/pipeline/placebo_on_real.json', 'w'))
    print('done', flush=True)



if __name__ == '__main__':
    main()
