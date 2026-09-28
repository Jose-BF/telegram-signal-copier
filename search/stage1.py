"""Stage 1: evaluate management genomes on the SEARCH period only, on real signals,
random-time placebo and inverted signals (slow-broker scenario C). Filters are applied
afterwards on these rows. Batches are saved as they finish (resumable)."""
import gzip, json, sys, time
sys.path.insert(0, '.')
from pathlib import Path
from research.history_search import evaluate
from research.history_space import population


def main():

    N, SEED, BATCH = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    OUT = Path('search/stage1'); OUT.mkdir(parents=True, exist_ok=True)
    U = 'runtime_data/signal_universe_v1/'
    UNIVERSES = {'real': U + 'signals.jsonl', 'placebo': U + 'placebo_random_time_s0.jsonl', 'inverted': U + 'placebo_flipped_s0.jsonl'}
    SEARCH = ('2026-01-01', '2026-07-01')   # fixed before looking at any result
    days = sorted(p.stem for p in Path('runtime_data/ticks_all/XAUUSD').glob('*.parquet') if SEARCH[0] <= p.stem < SEARCH[1])
    cal = {'C': json.load(open('runtime_data/execution_calibration_v2/C_p50_q450.json'))}
    pop = population(N, SEED)
    json.dump({ch: [{'family': f, 'genome': g.to_dict(), 'fingerprint': g.fingerprint} for g, f in v] for ch, v in pop.items()},
              open(OUT / 'genomes.json', 'w'))
    total = len(pop['canal1'])
    for b0 in range(0, total, BATCH):
        for name, uni in UNIVERSES.items():
            dst = OUT / f'rows_{name}_{b0:04d}.jsonl.gz'
            if dst.exists():
                continue
            t = time.time()
            sub = {ch: [g for g, _ in v[b0:b0 + BATCH]] for ch, v in pop.items()}
            rows = evaluate('runtime_data/ticks_all', days, sub, cal, workers=2, universe=uni)
            with gzip.open(str(dst) + '.tmp', 'wt') as fh:
                for sc, ch, gi, day, tid, pnl, dd, cens in rows:
                    fh.write(json.dumps([ch, b0 + gi, day, tid, pnl, dd, cens]) + '\n')
            Path(str(dst) + '.tmp').rename(dst)
            print(time.strftime('%H:%M'), 'batch', b0, name, 'rows', len(rows), 'secs', round(time.time() - t), flush=True)
    print('done', flush=True)



if __name__ == '__main__':
    main()
