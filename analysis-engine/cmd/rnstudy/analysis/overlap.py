import json, time, collections
import os
S = os.environ['RN_DIR'].rstrip('/') + '/'
f=lambda t:time.strftime('%m-%d %H:%M',time.gmtime(t))
env=[json.loads(l)['payload'] for l in open(S+'env-prod.jsonl')]
rn=[p for p in env if p['strategy']=='round_number']
oth=[p for p in env if p['strategy']!='round_number']
def box(p): return p['entry']['low'],p['entry']['high']
def inter(a,b,tol=0): return a[0]-tol<=b[1] and b[0]-tol<=a[1]
def life(p): return p['created_at'],p['expires_at']
tot=collections.Counter(); rows=[]
for r in rn:
    ra,rb=box(r),life(r); rs=r['invalidation']['price']; rt=r['targets'][0]['price']['price']
    hit=collections.defaultdict(lambda: collections.Counter())
    for o in oth:
        if o['direction']!=r['direction']: continue
        ob,ol=box(o),life(o)
        win=ol[0]<=rb[1] and rb[0]<=ol[1]
        if not win: continue
        ent=inter(ra,ob)
        ent1=inter(ra,ob,1.0)
        stp=abs(o['invalidation']['price']-rs)<=1.0
        tgt=abs(o['targets'][0]['price']['price']-rt)<=1.0
        h=hit[o['strategy']]
        h['window']+=1
        if ent: h['entry_overlap']+=1
        if ent1: h['entry_within_1usd']+=1
        if ent1 and stp: h['entry+stop']+=1
        if ent1 and stp and tgt: h['entry+stop+target']+=1
        if tgt: h['same_target']+=1
    rows.append((r,hit))
print('RN envelopes',len(rn),'others',len(oth))
agg=collections.defaultdict(collections.Counter); anyov=0
for r,hit in rows:
    ev=[x['code'] for x in r['evidence']]
    print(f(r['created_at']),r['direction'],f"{r['entry']['low']}-{r['entry']['high']}", {s:dict(c) for s,c in hit.items() if c['entry_within_1usd']})
    for s,c in hit.items():
        for k,v in c.items(): agg[s][k]+=(1 if v else 0)
    if any(c['entry_within_1usd'] for c in hit.values()): anyov+=1
print('\nRN setups with >=1 same-direction other-strategy envelope whose entry is within $1 and whose lifespan overlaps:',anyov,'/',len(rn))
print('\nper strategy: number of RN setups (of %d) with at least one such envelope'%len(rn))
for s,c in sorted(agg.items()): print(' ',s,dict(c))
