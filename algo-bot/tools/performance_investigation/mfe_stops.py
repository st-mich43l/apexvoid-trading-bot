import os, sys; sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from common import *
import bisect
T=trades(); B={s:bars(s) for s in PIP}; TS={s:[x[0] for x in B[s]] for s in PIP}
def window(sym,t0,t1):
    i=bisect.bisect_left(TS[sym],t0-300+1)  # bar containing t0 starts <= t0
    i=max(0,i-1) if False else bisect.bisect_right(TS[sym],t0)-1
    j=bisect.bisect_right(TS[sym],t1)
    return B[sym][max(i,0):j]
rows=[]
for t in T:
    pip=t['pip']; S=t['stop']*pip; e=t['entry']; sgn=1 if t['dir']=='BUY' else -1
    w=window(t['sym'],t['t0'],t['t1'])
    if not w: continue
    fav=max((b[2]-e) if sgn>0 else (e-b[3]) for b in w)/S
    adv=max((e-b[3]) if sgn>0 else (b[2]-e) for b in w)/S
    # after exit: next 24h path
    j=bisect.bisect_right(TS[t['sym']],t['t1']); post=B[t['sym']][j:j+288]
    post_fav=max(((b[2]-e) if sgn>0 else (e-b[3]) for b in post),default=0)/S
    post_adv=max(((e-b[3]) if sgn>0 else (b[2]-e) for b in post),default=0)/S
    # first hit order in post window: +1R before -1R from entry?
    hit=None
    for b in post:
        up=(b[2]-e)/S if sgn>0 else (e-b[3])/S; dn=(e-b[3])/S if sgn>0 else (b[2]-e)/S
        if dn>=1.0: hit='minus_1R_first' ; break
        if up>=1.0: hit='plus_1R_first'; break
    rows.append(dict(t=t,fav=fav,adv=adv,post_fav=post_fav,post_adv=post_adv,hit=hit,hold=(t['t1']-t['t0'])/60))
print('n',len(rows))
for path in ('full_stop','tp1_be','tp2_full','tp1_stop'):
    g=[r for r in rows if r['t']['exit_path']==path]
    if not g: continue
    print(f"{path:10} n={len(g):3} hold_min median={st.median(r['hold'] for r in g):.0f}  MFE_R median={st.median(r['fav'] for r in g):.2f} p75={sorted(r['fav'] for r in g)[3*len(g)//4]:.2f}  MAE_R median={st.median(r['adv'] for r in g):.2f}")
fs=[r for r in rows if r['t']['exit_path']=='full_stop']
print('\nFULL STOPS:',len(fs))
c=collections.Counter(r['hit'] for r in fs); print(' after the stop, within 24h (price vs ENTRY +-1R):',dict(c))
for lo,hi in ((0,0.25),(0.25,0.5),(0.5,0.75),(0.75,1.0),(1.0,9)):
    n=sum(1 for r in fs if lo<=r['fav']<hi); print(f"  stopped trades whose MFE before the stop was {lo}-{hi}R: {n} ({n/len(fs):.0%})")
print(' share of full stops that first went >=0.5R in favour:',round(sum(1 for r in fs if r['fav']>=0.5)/len(fs),2),' >=0.75R:',round(sum(1 for r in fs if r['fav']>=0.75)/len(fs),2))
print(' stop->went on to reach 1R in favour within 24h (from entry):',round(sum(1 for r in fs if r['post_fav']>=1.0)/len(fs),2))
# random baseline for 'after stopped, reach +1R from entry within 24h': random entries that hit -1R first
import random
rnd=random.Random(1); tot=0; reach=0
for s,b in B.items():
    for i in range(0,len(b)-600,9):
        med={'XAU':42,'EURUSD':12,'GBPUSD':15,'USDJPY':18,'GBPJPY':22}[s]*PIP[s]
        side=rnd.choice((1,-1)); e=b[i][4]
        stopped=None
        for j in range(i+1,min(len(b),i+289)):
            hi,lo=b[j][2],b[j][3]
            if (side>0 and lo<=e-med) or (side<0 and hi>=e+med): stopped=j; break
            if (side>0 and hi>=e+med) or (side<0 and lo<=e-med): break
        if stopped is None: continue
        tot+=1
        post=b[stopped+1:stopped+289]
        if any(((x[2]-e) if side>0 else (e-x[3]))>=med for x in post): reach+=1
print(' random-entry baseline: stopped first, then reached +1R from entry within 24h:',round(reach/tot,2),'n',tot)
import json
json.dump([dict(sym=r['t']['sym'],strategy=r['t']['strategy'],dir=r['t']['dir'],exit=r['t']['exit_path'],R=r['t']['R'],fav=r['fav'],adv=r['adv'],post_fav=r['post_fav'],hold=r['hold']) for r in rows],open(os.path.join(os.environ.get('INV_OUT','.'),'mfe.json'),'w'))
