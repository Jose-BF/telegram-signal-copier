import json, time, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0,'.')
import numpy as np
from research.causal_replay import compile_signals, make_path, utc, time_ns
from research.causal_canal1_stream import compile_canal1_stream
from research.causal_replay import CausalSignal
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.protection_contract import ProtectionProfile
from tools.run_causal_controls import STICKERS
from tools.run_week_causal_controls import policies
from tools import probe_canal1_incremental_window as probe
A=Path('runtime_data/week_20260921_v1/attached_20260921_25_full_v1')
rows=json.load(open(A/'raw_week_20260921_25_v1.json'))['rows']
day='2026-09-22'; start=utc(day+'T00:00:00+00:00'); cutoff=utc(day+'T20:50:00+00:00')
market,_=probe.load_day(A,'XAUUSD',day,start_ns=time_ns(start),cutoff_ns=time_ns(cutoff),offset_seconds=10800)
conv,_=probe.load_day(A,'EURUSD',day,start_ns=time_ns(start),cutoff_ns=time_ns(cutoff),offset_seconds=10800,initial_padding_ns=5_000_000_000)
other,_=compile_signals(rows,start=start,cutoff=cutoff,sticker_directions=STICKERS,max_entry_age_s=120)
gold=[s for s in other if s.channel=='canal2']
st=compile_canal1_stream(rows,start=start,cutoff=cutoff,sticker_directions=STICKERS,max_entry_age_s=120,optional_close_choice='hold')
dub=[i.value for i in st.timeline if isinstance(i.value,CausalSignal)]
gen=policies(); print(list(gen))
ex=ExecutionAssumptions(entry_fill_latency_ms=789, protection=ProtectionProfile(0.01,2,20,0,142,2,1048), market=MarketProfile(10,294,10,0.01,1.0,0.01))
ex1=ExecutionAssumptions(entry_fill_latency_ms=789, protection=ProtectionProfile(0.01,2,20,0,142,2,1048,policy_extension="basket_guard_v1",request_quote_binding="timestamp_and_ordinal"), market=MarketProfile(10,294,10,0.01,1.0,0.01))
for name, sigs, g in (('canal2',gold,gen['canal2']),('canal1',dub,gen['canal1'])):
    paths=[]
    for s in sigs:
        try:
            paths.append(make_path(s,g,market=market,conversion=conv,cutoff=min(cutoff, s.observed_at+timedelta(hours=6)),contract_size=100.0,currency_digits=2,max_fx_age_ms=30000,market_sha256='x',conversion_sha256='y'))
        except Exception as e: print('path err',s.signal_id,e)
    ex_=ex1 if name=='canal1' else ex
    fe=FastEvaluator(execution=ex_)
    t=time.time(); res=[fe(p,g) for p in paths]; t1=time.time()-t
    t=time.time(); res=[fe(p,g) for p in paths]; t2=time.time()-t
    t=time.time(); res2=[simulate(p,g,execution=ex_) for p in paths[:3]]; t3=(time.time()-t)/max(1,len(paths[:3]))
    print(name,'paths',len(paths),'ticks/path',int(np.mean([len(p.times_ns) for p in paths])) if paths else 0,'fast first %.2fs warm %.3fs per path %.4fs'%(t1,t2,t2/max(1,len(paths))),'scalar per path %.2fs'%t3, 'blockers', [r.blockers[:2] for r in res][:3], [str(r.pnl_eur) for r in res][:6])
    sc=[simulate(p,g,execution=ex_) for p in paths]
    fr=[fe(p,g) for p in paths]
    print(name,'parity pnl', sum(str(a.pnl_eur)==str(b.pnl_eur) for a,b in zip(sc,fr)),'/',len(paths), [(str(a.pnl_eur),str(b.pnl_eur)) for a,b in zip(sc,fr) if str(a.pnl_eur)!=str(b.pnl_eur)][:4])
