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
B={s:bars(s) for s in PIP}; TS={s:[x[0] for x in B[s]] for s in PIP}
def race(sym,j0,side,entry,stop_d,tgt_d,spread,horizon=288):
    b=B[sym]
    e=entry+(spread if side>0 else 0)
    stop=e-stop_d if side>0 else entry+stop_d+spread
    tgt=e+tgt_d if side>0 else entry-tgt_d
    for j in range(j0,min(len(b),j0+horizon)):
        hi,lo=b[j][2],b[j][3]
        if side>0:
            if lo<=stop: return 0
            if hi>=tgt: return 1
        else:
            if hi>=stop: return 0
            if lo<=tgt: return 1
    return None
rnd=random.Random(5); out=[]; nofill=0
for r in R:
    sym=r['symbol']
    if not r.get('targets') or not r.get('invalidation'): continue
    side=1 if r['direction']=='BUY' else -1
    lo,hi=r['entry_low'],r['entry_high']; ref=r['ref_price']
    inside = lo<=ref<=hi
    entry=ref if inside else (hi if side>0 else lo)
    stop_d=abs(entry-r['invalidation']); tgt_d=(r['targets'][0]-entry)*side
    pip=PIP[sym]
    if stop_d<=0 or tgt_d<=0 or stop_d/pip<3: continue
    i0=bisect.bisect_right(TS[sym],r['eval_time'])-1
    if i0<0 or i0>len(B[sym])-330: continue
    b=B[sym]
    if inside: j0=i0+1
    else:
        # limit at proximal edge, valid 12 bars; price must trade through it
        fill=None
        for j in range(i0+1,i0+13):
            if (side>0 and b[j][3]<=entry) or (side<0 and b[j][2]>=entry): fill=j; break
        if fill is None: nofill+=1; continue
        j0=fill+1
    res=race(sym,j0,side,entry,stop_d,tgt_d,SPREAD[sym])
    if res is None: continue
    base=[]; tries=0
    while len(base)<6 and tries<40:
        tries+=1
        j=min(max(i0+rnd.randint(-864,864),1),len(b)-330)
        x=race(sym,j+1,rnd.choice((1,-1)),b[j][4],stop_d,tgt_d,SPREAD[sym])
        if x is not None: base.append(x)
    if len(base)<3: continue
    rr=tgt_d/stop_d; bh=sum(base)/len(base)
    out.append(dict(sym=sym,strategy=r['strategy'],t=r['eval_time'],inside=inside,hit=res,base=bh,rr=rr,ev=res*rr-(1-res),evb=bh*rr-(1-bh),stop_pips=stop_d/pip))
print('candidates',len(out),'limit never filled (excluded)',nofill)
def ci(vals_by_cluster,reps=1500):
    gs=list(vals_by_cluster.values()); rr=random.Random(2); ms=[]
    for _ in range(reps):
        flat=[v for g in (rr.choice(gs) for _ in gs) for v in g]; ms.append(st.mean(flat))
    ms.sort(); return ms[int(.025*reps)],ms[int(.975*reps)]
def summ(rows,label):
    if len(rows)<20: return
    cl=collections.defaultdict(list)
    for x in rows: cl[(x['sym'],x['t']//86400)].append(x['ev']-x['evb'])
    lo,hi=ci(cl)
    print(f"{label:26} n={len(rows):5} hit={st.mean(x['hit'] for x in rows):.3f} base={st.mean(x['base'] for x in rows):.3f} | EV {st.mean(x['ev'] for x in rows):+.3f}R vs random {st.mean(x['evb'] for x in rows):+.3f}R  diff {st.mean(x['ev']-x['evb'] for x in rows):+.3f}R CI[{lo:+.3f},{hi:+.3f}] medStop {st.median(x['stop_pips'] for x in rows):.0f}p RR {st.median(x['rr'] for x in rows):.2f}")
summ(out,'ALL')
summ([x for x in out if x['inside']],'inside-zone entries only')
summ([x for x in out if not x['inside']],'limit-fill entries only')
summ([x for x in out if x['t']<OOS],'development (<7 Oct)')
summ([x for x in out if x['t']>=OOS],'out of sample (>=7 Oct)')
print()
for s in ('XAU','EURUSD','GBPUSD','USDJPY','GBPJPY'): summ([x for x in out if x['sym']==s],s)
print()
for s,_ in collections.Counter(x['strategy'] for x in out).most_common(): summ([x for x in out if x['strategy']==s],s)
json.dump(out,open(os.path.join(os.environ.get('INV_OUT','.'),'technical_geometry.json'),'w'))
