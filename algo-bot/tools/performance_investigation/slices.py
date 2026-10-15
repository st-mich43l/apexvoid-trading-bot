import os, sys; sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from common import *
import bisect
T=trades()
H1={s:bars(s,'M15') for s in PIP}   # use M15 for trend context (captures have M15)
def ret(sym,t,hours):
    b=H1[sym]; ts=[x[0] for x in b]
    i=bisect.bisect_right(ts,t)-1; j=bisect.bisect_right(ts,t-hours*3600)-1
    if i<0 or j<0: return None
    return (b[i][4]-b[j][4])/b[i][4]
for t in T:
    sg=1 if t['dir']=='BUY' else -1
    r24=ret(t['sym'],t['t0'],24); r4=ret(t['sym'],t['t0'],4)
    t['al24']=None if r24 is None else (r24*sg>0)
    t['al4']=None if r4 is None else (r4*sg>0)
    d=dt.datetime.fromtimestamp(t['t0'],dt.UTC); t['hour']=d.hour; t['dow']=d.weekday()
    t['sess']='asia' if d.hour<7 else 'london' if d.hour<12 else 'overlap' if d.hour<16 else 'ny' if d.hour<21 else 'late'
def sm(rows,label,min_n=15):
    if len(rows)<min_n: return
    v=[r['R'] for r in rows]; se=st.pstdev(v)/math.sqrt(len(v))
    print(f"  {label:34} n={len(v):3} meanR={st.mean(v):+.3f} ±{1.96*se:.2f}  stop%={sum(1 for r in rows if r['exit_path']=='full_stop')/len(v):.0%}")
print('ALL'); sm(T,'all')
print('\nBy direction'); [sm([r for r in T if r['dir']==d],d) for d in ('BUY','SELL')]
for s in PIP: print(s,end=' '); [sm([r for r in T if r['dir']==d and r['sym']==s],f'{s} {d}') for d in ('BUY','SELL')]
print('\nTrend alignment (24h return sign in the trade direction)')
for lab,key in (('24h','al24'),('4h','al4')):
    sm([r for r in T if r[key] is True],f'with {lab} trend'); sm([r for r in T if r[key] is False],f'against {lab} trend')
print('\nBy session (UTC hour of fill)'); [sm([r for r in T if r['sess']==s],s) for s in ('asia','london','overlap','ny','late')]
print('\nBy weekday'); [sm([r for r in T if r['dow']==d],['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d]) for d in range(7)]
print('\nXAU only by session'); [sm([r for r in T if r['sym']=='XAU' and r['sess']==s],'XAU '+s) for s in ('asia','london','overlap','ny','late')]
print('\nXAU with/against 24h trend'); sm([r for r in T if r['sym']=='XAU' and r['al24'] is True],'XAU with'); sm([r for r in T if r['sym']=='XAU' and r['al24'] is False],'XAU against')
print('\nFX with/against 24h trend'); sm([r for r in T if r['sym']!='XAU' and r['al24'] is True],'FX with'); sm([r for r in T if r['sym']!='XAU' and r['al24'] is False],'FX against')
# permutation test of best slice (session x symbol) to see if max slice mean is unusual
slices=collections.defaultdict(list)
for r in T: slices[(r['sym'],r['sess'])].append(r['R'])
obs=max((st.mean(v),k,len(v)) for k,v in slices.items() if len(v)>=20)
rr=random.Random(3); allR=[r['R'] for r in T]; ks=[(k,len(v)) for k,v in slices.items() if len(v)>=20]
cnt=0; N=2000
for _ in range(N):
    rr.shuffle(allR); i=0; best=-9
    # reassign shuffled R to slices of same sizes: approximate by sampling without replacement
    pool=allR[:]
    rr.shuffle(pool); idx=0
    for k,n in ks:
        m=st.mean(pool[idx:idx+n]); idx+=n; best=max(best,m)
    if best>=obs[0]: cnt+=1
print(f"\nbest (symbol, session) slice: {obs[1]} n={obs[2]} meanR={obs[0]:+.3f}; probability the best of {len(ks)} slices is this high by chance: {cnt/N:.2f}")
