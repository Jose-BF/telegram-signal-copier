import json,pandas as pd,numpy as np,datetime as dt
OFF=10_800_000
refs=json.load(open('runtime_data/week_20260921_v1/gold555_watch_refs.json'))
rows=[r for r in json.load(open('runtime_data/week_20260921_v1/attached_20260921_25_full_v1/raw_week_20260921_25_v1.json'))['rows'] if r.get('channel')=='canal2']
first={}
for r in rows:
    k='canal2_%s'%r['message_id']; t=dt.datetime.fromisoformat(r['ts']); first[k]=min(first.get(k,t),t)
ticks={}; res=[]
for sig,v in sorted(refs.items()):
    day=dt.datetime.fromtimestamp((v['ref_tick_msc']-OFF)/1000,dt.timezone.utc).strftime('%Y-%m-%d')
    if day not in ticks: ticks[day]=pd.read_parquet(f'runtime_data/week_20260921_v1/attached_20260921_25_full_v1/XAUUSD/{day}.parquet')[['time_msc','bid','ask']]
    d=ticks[day]; tm=d.time_msc.values
    obs_ms=int(first[sig].timestamp()*1000)+OFF
    h=v.get('handler_ts'); h_ms=int(dt.datetime.fromisoformat(h).timestamp()*1000)+OFF if h else None
    i_ref=int(np.searchsorted(tm,v['ref_tick_msc'])); i_last=int(np.searchsorted(tm,obs_ms,side='right')-1); i_first=int(np.searchsorted(tm,obs_ms))
    q=(lambda i: d.ask.values[i] if v['dir']=='BUY' else d.bid.values[i])
    res.append(dict(sig=sig,obs_minus_ref=obs_ms-v['ref_tick_msc'],handler_minus_ref=(h_ms-v['ref_tick_msc']) if h_ms else None,
        ticks_between=i_last-i_ref,ref=v['ref_price'],d_last=round(q(i_last)-v['ref_price'],2),d_first=round(q(i_first)-v['ref_price'],2)))
df=pd.DataFrame(res); pd.set_option('display.width',200); print(df.to_string())
print(df[['obs_minus_ref','handler_minus_ref','ticks_between']].describe(percentiles=[.1,.5,.9]).round(1))
for c in ('d_last','d_first'): print(c,'mean|abs|',round(df[c].abs().mean(),3),'exact',round((df[c]==0).mean(),2),'within0.1',round((df[c].abs()<=0.1).mean(),2))
print('--- reference = last tick <= raw_ts - L')
for L in (0,200,300,400,450,500,550,600,700):
    errs=[];exact=0
    for sig,v in refs.items():
        day=dt.datetime.fromtimestamp((v['ref_tick_msc']-OFF)/1000,dt.timezone.utc).strftime('%Y-%m-%d'); d=ticks[day]; tm=d.time_msc.values
        obs_ms=int(first[sig].timestamp()*1000)+OFF-L
        i=int(np.searchsorted(tm,obs_ms,side='right')-1)
        qv=d.ask.values[i] if v['dir']=='BUY' else d.bid.values[i]
        errs.append(abs(qv-v['ref_price'])); exact+=tm[i]==v['ref_tick_msc']
    e=np.array(errs); print(L,'same tick %d/48'%exact,'mean|err| %.3f'%e.mean(),'p90 %.2f max %.2f'%(np.quantile(e,.9),e.max()))
