import json, os, sys, time
sys.path.insert(0, '.')
from pathlib import Path
from research.history_replay import History, load_universe, simulate_day
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.protection_contract import ProtectionProfile
from tools.run_week_causal_controls import policies
cal = json.load(open(sys.argv[1]))['delays_ms']; out = sys.argv[2]
def ex(ch):
    pe = dict(policy_extension="basket_guard_v1", request_quote_binding="timestamp_and_ordinal") if ch == 'canal1' else {}
    return ExecutionAssumptions(entry_fill_latency_ms=cal['entry_fill_latency_ms'], quote_view_lag_ms=cal.get('quote_view_lag_ms', 0),
        protection=ProtectionProfile(0.01, 2, 20, 0, cal['protection_processing_ms'], cal['protection_ack_ms'], cal['protection_retry_ms'], **pe),
        market=MarketProfile(cal['entry_ack_ms'], cal['close_processing_ms'], cal['close_ack_ms'], 0.01, 1.0, 0.01))
gen = {k: v.with_change(provider_management_mode='ignore') for k, v in policies().items()}
fe = {ch: FastEvaluator(execution=ex(ch)) for ch in gen}
T = 'runtime_data/history_triggers_v1/'
trig = load_universe(Path(os.environ.get('UNIVERSE','runtime_data/signal_universe_v1/signals.jsonl')))
d = json.load(open(T + 'tg_delay_samples_v2.json'))
h = History(Path(os.environ.get('TICKS','runtime_data/ticks_all')), trig, {'canal1': d['canal1'], 'canal2': d['canal2']}, seed=int(os.environ.get('SEED', '0')))
rows = []; t0 = time.time(); n = 0
for day, market, conv, items in h.days():
    r = simulate_day(items, gen, fe, market, conv)
    for x in r: x['day'] = day
    rows += r; n += 1
    for f in fe.values(): f.clear_cache()
json.dump(rows, open(out, 'w'))
print('days', n, 'triggers', len(trig), 'rows', len(rows), 'secs %.0f' % (time.time() - t0))
