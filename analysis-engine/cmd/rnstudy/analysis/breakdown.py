from sim import *
rows = [json.loads(l) for l in open(S + 'study.jsonl')]
def sess(h):
    return 'asia(21-07)' if (h >= 21 or h < 7) else ('london(07-12)' if h < 12 else 'newyork(12-21)')
def collect(sets):
    out = []
    for s in sets:
        r = simulate(s)
        if r['status'] != 'filled': continue
        h = time.gmtime(s['created_at']).tm_hour
        out.append(dict(s=s, r=r, sess=sess(h), dir=s['dir'], major=s['major'], atr=s['atr']))
    return out
def table(items, key, label):
    g = collections.defaultdict(list)
    for it in items: g[key(it)].append(it)
    print(f'  by {label}:')
    for k in sorted(g, key=str):
        v = g[k]
        a = stats([x['r']['r_go'] for x in v]); b = stats([x['r']['r_ladder'] for x in v])
        print(f"    {str(k):16s} n={len(v):2d} | go mean {a['mean']:+.2f} win {a['win']*100:3.0f}% | ladder mean {b['mean']:+.2f} win {b['win']*100:3.0f}%")
for label, sets in (('PUBLISHED NOW (B, n=13)', [r for r in rows if r['rec'] == 'setup']),
                    ('B at floor 1, optional (n=37)', [r for r in rows if r['rec'] == 'setup_open' and r['kind'] == 'break_retest']),
                    ('A at floor 1 (n=28)', [r for r in rows if r['rec'] == 'setup_open' and r['kind'] == 'sweep_reclaim'])):
    items = collect(sets)
    print('==', label, 'filled', len(items))
    table(items, lambda it: it['dir'], 'direction')
    table(items, lambda it: it['sess'], 'session (UTC)')
    atrs = sorted(it['atr'] for it in items)
    t1, t2 = atrs[len(atrs) // 3], atrs[2 * len(atrs) // 3]
    table(items, lambda it: 'low ATR' if it['atr'] <= t1 else ('mid ATR' if it['atr'] <= t2 else 'high ATR'), f'volatility (M5 ATR terciles ${t1:.1f}/${t2:.1f})')
    def ctx(it):
        buy = it['dir'] == 'BUY'; lt = it['s'].get('local_trend')
        if lt == 'range': return 'range'
        return 'with M5 trend' if (buy and lt == 'up') or (not buy and lt == 'down') else 'against M5 trend'
    table(items, ctx, 'M5 structure at publication')
    table(items, lambda it: 'major x50' if it['major'] else 'minor', 'handle')
    if label.startswith('B at floor'):
        table(items, lambda it: 'rejection candle' if (it['s']['wick'] or it['s']['engulf']) else 'plain touch', 'retest candle')
