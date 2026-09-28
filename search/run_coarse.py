import json, os, sys, time
sys.path.insert(0, '.')
from pathlib import Path
from research.history_search import evaluate, metrics
from research.history_space import population


def main():
    N = int(sys.argv[1]); SEED = int(sys.argv[2]); OUT = Path(sys.argv[3]); PERIOD = sys.argv[4]  # e.g. 2026-01-01:2026-07-01
    lo, hi = PERIOD.split(':')
    days = sorted(p.stem for p in Path('runtime_data/hist_ticks/XAUUSD').glob('*.parquet') if lo <= p.stem < hi)
    pop = population(N, SEED)
    cals = {k: json.load(open(f'runtime_data/execution_calibration_v2/{k}_q450.json')) for k in ('A_p50', 'C_p50', 'C_p90')}
    t = time.time()
    rows = evaluate('runtime_data/hist_ticks', days, {ch: [g for g, _ in v] for ch, v in pop.items()}, cals, workers=2)
    m = metrics(rows, None)
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump({'period': PERIOD, 'seed': SEED, 'n': N, 'days': len(days), 'secs': round(time.time() - t),
               'genomes': {ch: [{'family': f, 'genome': g.to_dict(), 'fingerprint': g.fingerprint} for g, f in v] for ch, v in pop.items()},
               'metrics': [{'scenario': k[0], 'channel': k[1], 'genome': k[2], **v} for k, v in m.items()]},
              open(OUT / 'coarse.json', 'w'))
    json.dump(rows, open(OUT / 'rows.json', 'w'))
    print('done secs', round(time.time() - t), 'rows', len(rows))



if __name__ == '__main__':
    main()
