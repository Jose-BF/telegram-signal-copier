import json, re, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
sys.path.insert(0,'.')
from tools import probe_canal1_incremental_window as probe
A='runtime_data/week_20260921_v1/attached_20260921_25_full_v1'
cal=json.load(open('runtime_data/execution_calibration_v2/C_p50.json'))
def run(w):
    try:
        r=probe.run(Path(A), start=w['start_utc'], cutoff=w['cutoff_utc'], offset_seconds=10800, assume_initial_complete=True, optional_close_choice='hold',
          native_reconciled=Path('runtime_data/week_20260921_v1/native_reconciled_full_v1.json'), native_deals=Path(A+'/account_history.json'),
          execution_override=cal, max_fx_age_ms=30000, scope_tick_budget=950000, same_ms_message_after_quote=True, raw_name='raw_week_20260921_25_v1.json', ignore_unknown_stickers=True)
        return {b['signal_id']:{'entries':[{'t':str(x['opened_at']),'p':x['entry_price'],'v':x['volume'],'ticket':x['ticket'],'req':x.get('requested_ns')} for x in b['entries']],
                                'exits':[{'t':str(x['closed_at']),'p':x['exit_price'],'v':x['volume'],'ticket':x['ticket'],'reason':x['reason'],'pnl':x['pnl_eur']} for x in b['exits']]} for b in r['basket_rows']}
    except Exception as e:
        return {'error':str(e)}
d=json.load(open('exam/exam_2125_v2.json'))
with ProcessPoolExecutor(2) as p:
    out=list(p.map(run, d['final_windows']))
merged={}
for o in out: merged.update({k:v for k,v in o.items() if k!='error'})
json.dump(merged, open('exam/sim_entries_Cp50.json','w'), default=str)
print(len(merged), sum('error' in o for o in out))
