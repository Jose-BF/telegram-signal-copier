import json,sys,collections
rows=json.load(open(sys.argv[1]))
st=collections.Counter((r['ch'],r['status']) for r in rows); print(dict(st))
errs=collections.Counter(r.get('error','')[:70] for r in rows if r['status']=='error'); print('errors',errs.most_common(5))
print('censored',sum(1 for r in rows if r.get('censored')),'pnl None',sum(1 for r in rows if r['status']=='filled' and r['pnl'] is None))
for ch in ('canal1','canal2'):
  print(ch)
  m=collections.defaultdict(lambda:[0,0.0,0.0,0,0.0])
  for r in rows:
    if r['ch']!=ch or r['status']!='filled' or r['pnl'] is None: continue
    k=r['day'][:7]; m[k][0]+=1; m[k][1]+=r['pnl']; m[k][2]=max(m[k][2],r['dd'] or 0); m[k][3]+=r['pnl']>0; m[k][4]+=r['dd'] or 0
  tot=0
  for k in sorted(m):
    n,p,mx,w,dd=m[k]; tot+=p; print('  %s n=%3d net %8.2f win%% %3.0f maxDD %6.2f avgDD %5.2f'%(k,n,p,100*w/n,mx,dd/n))
  print('  total %.2f'%tot)
