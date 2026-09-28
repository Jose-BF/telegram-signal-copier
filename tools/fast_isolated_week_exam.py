"""Isolated-basket fast replay of every real basket of a week vs real (net, DD)."""
import json, os, sys
from datetime import timedelta
from pathlib import Path
sys.path.insert(0,'.')
from research.causal_replay import compile_signals, make_path, utc, time_ns, CausalSignal
from research.causal_canal1_stream import compile_canal1_stream
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.protection_contract import ProtectionProfile
from tools.run_causal_controls import STICKERS
from tools.run_week_causal_controls import policies
from tools import probe_canal1_incremental_window as probe
A=Path(sys.argv[1]); raw=sys.argv[2]; risk=json.load(open(sys.argv[3])); cal=json.load(open(sys.argv[4]))['delays_ms']; days=sys.argv[5].split(',')
rows=[r for r in json.load(open(A/raw))['rows'] if not (r.get('sticker_id') and str(r['sticker_id']) not in STICKERS and not (r.get('text') or '').strip())]
real={b['signal_id']:b['path']['known_sample_metrics'] for b in risk['baskets'] if b.get('path')}
gen=policies()
if os.environ.get('PMODE'):
    gen={k:v.with_change(provider_management_mode=os.environ['PMODE']) for k,v in gen.items()}
def ex(ch):
    pe=dict(policy_extension="basket_guard_v1",request_quote_binding="timestamp_and_ordinal") if ch=='canal1' else {}
    return ExecutionAssumptions(entry_fill_latency_ms=cal['entry_fill_latency_ms'],quote_view_lag_ms=int(os.environ.get('QLAG','0')),
        protection=ProtectionProfile(0.01,2,20,0,cal['protection_processing_ms'],cal['protection_ack_ms'],cal['protection_retry_ms'],**pe),
        market=MarketProfile(cal['entry_ack_ms'],cal['close_processing_ms'],cal['close_ack_ms'],0.01,1.0,0.01))
fe={ch:FastEvaluator(execution=ex(ch)) for ch in ('canal1','canal2')}
out=[]
for day in days:
    start=utc(day+'T00:00:00+00:00'); cutoff=utc(day+'T20:54:00+00:00')
    market,_=probe.load_day(A,'XAUUSD',day,start_ns=time_ns(start),cutoff_ns=time_ns(cutoff),offset_seconds=10800)
    conv,_=probe.load_day(A,'EURUSD',day,start_ns=time_ns(start),cutoff_ns=time_ns(cutoff),offset_seconds=10800,initial_padding_ns=5_000_000_000)
    other,_=compile_signals(rows,start=start,cutoff=cutoff,sticker_directions=STICKERS,max_entry_age_s=120)
    st=compile_canal1_stream(rows,start=start,cutoff=cutoff,sticker_directions=STICKERS,max_entry_age_s=120,optional_close_choice='hold')
    sigs=[s for s in other if s.channel=='canal2']+[i.value for i in st.timeline if isinstance(i.value,CausalSignal)]
    # live bot (listener._handle_canal1_text): a canal1 TEXT entry opens a basket only when no
    # canal1 signal is open, otherwise it is the open signal's companion; STICKERS always open
    # (duplicates <5 s are aliased; the stream already merges them)
    sticker_ids={r['message_id'] for r in rows if r.get('channel')=='canal1' and r.get('sticker_id')}
    open_until=[]
    for s in sorted(sigs,key=lambda x:x.observed_at):
        is_text=s.channel=='canal1' and int(s.signal_id.split('_')[-1]) not in sticker_ids
        if is_text and any(u>s.observed_at for u in open_until):
            out.append({'signal_id':s.signal_id,'channel':'canal1','companion':True}); continue
        try:
            p=make_path(s,gen[s.channel],market=market,conversion=conv,cutoff=cutoff,contract_size=100.0,currency_digits=2,max_fx_age_ms=30000,market_sha256='x',conversion_sha256='y',tape_start=max(start,s.observed_at-timedelta(seconds=5)))
            r=fe[s.channel](p,gen[s.channel])
            if s.channel=='canal1' and r.filled_volume:
                open_until.append(max((x.closed_at for x in r.exits), default=cutoff))
            out.append({'observed':s.observed_at.isoformat(),'channel':s.channel,'close':(max((str(x.closed_at) for x in r.exits),default=None)),'signal_id':s.signal_id,'pnl':None if r.pnl_eur is None else float(r.pnl_eur),'dd':None if r.max_floating_drawdown_eur is None else float(r.max_floating_drawdown_eur),'blockers':list(r.blockers)[:3],'filled':r.filled_volume})
        except Exception as e:
            out.append({'signal_id':s.signal_id,'error':str(e)[:100]})
sim={o['signal_id']:o for o in out}
m=[k for k in real if k in sim and sim[k].get('pnl') is not None and sim[k].get('dd') is not None and sim[k]['filled']>0]
rn=sum(float(real[k]['final_net']) for k in real); sn=sum(sim[k]['pnl'] for k in m)
rd=sum(float(real[k]['max_drawdown']) for k in m); sd=sum(sim[k]['dd'] for k in m)
simonly=[k for k,v in sim.items() if k not in real and v.get('filled')]
json.dump(out,open("exam/fast_exam_out.json","w"))
print('real baskets',len(real),'matched',len(m),'real net %.2f sim net(matched) %.2f'%(rn,sn),'DD real %.2f sim %.2f (%.1f%%)'%(rd,sd,100*(sd-rd)/rd),'sim_only',len(simonly),'errors',sum('error' in v for v in out),'blocked',[k for k,v in sim.items() if v.get('blockers')][:5])
