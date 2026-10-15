import os, sys; sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from common import *
import bisect
OOS=int(dt.datetime(2026,10,7,tzinfo=dt.UTC).timestamp())
recs=[json.loads(l) for l in open(AEQ)]
def thin(rs):
    seen=set(); out=[]
    for r in sorted(rs,key=lambda r:(r['eval_time'],r['id'])):
        k=(r['symbol'],r['strategy'],r['direction'],r['eval_time']//3600)
        if k not in seen: seen.add(k); out.append(r)
    return out
R=thin(recs)
M5={s:bars(s) for s in PIP}; T5={s:[x[0] for x in M5[s]] for s in PIP}
M15={s:bars(s,'M15') for s in PIP}; T15={s:[x[0] for x in M15[s]] for s in PIP}
def ret24(sym,t):
    b=M15[sym]; ts=T15[sym]; i=bisect.bisect_right(ts,t)-1; j=bisect.bisect_right(ts,t-86400)-1
    if i<0 or j<0: return None
    return (b[i][4]-b[j][4])/b[i][4]
def sess(t):
    h=dt.datetime.fromtimestamp(t,dt.UTC).hour
    return 'asia' if h<7 else 'london' if h<12 else 'overlap' if h<16 else 'ny' if h<21 else 'late'
# signal outcomes (Go sim, production envelope geometry), 1R race
sig=[]
for r in R:
    o=r['sim'].get('oneR_cost',{}).get('result')
    if o not in ('target','stop'): continue
    sg=1 if r['direction']=='BUY' else -1
    x=ret24(r['symbol'],r['eval_time'])
    if x is None: continue
    sig.append(dict(sym=r['symbol'],t=r['eval_time'],hit=1 if o=='target' else 0,sess=sess(r['eval_time']),al=(x*sg>0),strategy=r['strategy']))
# random baseline with the same stop distances (per symbol median exec risk), random time/direction
med={}
for r in recs:
    if r['exec_risk_pips']: med.setdefault(r['symbol'],[]).append(r['exec_risk_pips'])
med={s:st.median(v) for s,v in med.items()}
rnd=random.Random(11); base=[]
for s in PIP:
    b=M5[s]; S=med[s]*PIP[s]; sp=SPREAD[s]
    for i in range(0,len(b)-300,4):
        side=rnd.choice((1,-1)); e=b[i][4]
        e2=e+sp if side>0 else e
        stop=e2-S if side>0 else e+S+sp; tgt=e2+S if side>0 else e-S
        res=None
        for j in range(i+1,min(len(b),i+289)):
            hi,lo=b[j][2],b[j][3]
            if side>0:
                if lo<=stop: res=0;break
                if hi>=tgt: res=1;break
            else:
                if hi>=stop: res=0;break
                if lo<=tgt: res=1;break
        if res is None: continue
        x=ret24(s,b[i][0])
        if x is None: continue
        base.append(dict(sym=s,t=b[i][0],hit=res,sess=sess(b[i][0]),al=(x*side>0)))
def rate(rows): return (sum(r['hit'] for r in rows)/len(rows),len(rows)) if rows else (float('nan'),0)
def line(label,f):
    a=[r for r in sig if f(r)]; b=[r for r in base if f(r)]
    if len(a)<30: return
    pa,na=rate(a); pb,nb=rate(b)
    se=math.sqrt(pa*(1-pa)/na+pb*(1-pb)/nb)
    print(f"  {label:28} signals {pa:.3f} (n={na:4}) | random {pb:.3f} (n={nb:5}) | diff {pa-pb:+.3f}  (z={(pa-pb)/se:+.1f}, naive)")
print('P(+1R before stop): AE signals vs random entries with the same stops, by bucket')
line('ALL',lambda r:True)
for s in ('asia','london','overlap','ny','late'): line('session '+s,lambda r,s=s:r['sess']==s)
for a in (True,False): line('with 24h trend' if a else 'against 24h trend',lambda r,a=a:r['al']==a)
for s in PIP: line(s,lambda r,s=s:r['sym']==s)
for s in PIP:
    for a in (True,False): line(f'{s} {"with" if a else "against"} trend',lambda r,s=s,a=a:r['sym']==s and r['al']==a)
print('\nout of sample only (>= 7 Oct) for the same buckets')
for s in ('asia','london','overlap','ny'): line('OOS session '+s,lambda r,s=s:r['sess']==s and r['t']>=OOS)
for a in (True,False): line('OOS with trend' if a else 'OOS against trend',lambda r,a=a:r['al']==a and r['t']>=OOS)
