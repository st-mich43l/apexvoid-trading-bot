from sim import *
env = [json.loads(l)['payload'] for l in open(S + 'env-prod.jsonl')]
others = [p for p in env if p['strategy'] != 'round_number']
def conv(p):
    return dict(dir=p['direction'], entry_low=p['entry']['low'], entry_high=p['entry']['high'], stop=p['invalidation']['price'],
                target=p['targets'][0]['price']['price'], created_at=p['created_at'], expires_at=p['expires_at'], strategy=p['strategy'], id=p['id'])
sets = [conv(p) for p in others]
sets.sort(key=lambda s: s['created_at'])
# cluster: same direction, entry zones within $1, created within 4h of the kept one => same trade idea, count once
kept = []
for s in sets:
    dup = False
    for k in kept:
        if k['dir'] == s['dir'] and s['created_at'] - k['created_at'] < 4 * 3600 and k['entry_low'] - 1 <= s['entry_high'] and s['entry_low'] - 1 <= k['entry_high']:
            dup = True; break
    if not dup: kept.append(s)
print('non-RN envelopes', len(sets), 'after same-idea clustering', len(kept))
def align(s, step=10.0, tol=1.5):
    ref = s['entry_high'] if s['dir'] == 'BUY' else s['entry_low']
    h = round(ref / step) * step
    return abs(ref - h) <= tol
def go(group, label):
    res = []
    for s in group:
        r = simulate(s, expiry_bars=max(1, int((s['expires_at'] - s['created_at']) / 300)), enforce_cap=False)
        if r['status'] == 'filled': res.append(r)
    print(f"{label:34s} n_ideas {len(group):3d} filled {len(res):3d} | go {fmt(stats([r['r_go'] for r in res])) if res else ''}")
    if res: print(f"{'':34s}                       | ladder {fmt(stats([r['r_ladder'] for r in res]))}")
    return res
for name, grp in (('ALL non-RN', kept), ('key_level', [s for s in kept if s['strategy'] == 'key_level']), ('zone family', [s for s in kept if s['strategy'] in ('supply', 'demand', 'order_block', 'fvg', 'ifvg', 'confluence_zone', 'flip_zone')])):
    print('==', name)
    a10 = [s for s in grp if align(s, 10, 1.5)]
    n10 = [s for s in grp if not align(s, 10, 1.5)]
    a50 = [s for s in grp if align(s, 50, 1.5)]
    go(a10, '  aligned to $10 handle (<=1.5)'); go(n10, '  not aligned')
    go(a50, '  aligned to $50 handle')

print('\n== PLACEBO: the same "aligned" rule on grids shifted off the round numbers (ALL non-RN ideas, filled only)')
def res_of(group):
    out = []
    for s in group:
        r = simulate(s, expiry_bars=max(1, int((s['expires_at'] - s['created_at']) / 300)), enforce_cap=False)
        if r['status'] == 'filled': out.append((s, r))
    return out
base = res_of(kept)
def rule(off):
    def f(s):
        ref = s['entry_high'] if s['dir'] == 'BUY' else s['entry_low']
        h = round((ref - off) / 10.0) * 10.0 + off
        return abs(ref - h) <= 1.5
    return f
rnd = random.Random(11)
def day(s): return s['created_at'] // 86400
def diff_ci(sel, key):
    A = [r[key] for s, r in base if sel(s)]; B = [r[key] for s, r in base if not sel(s)]
    d = sum(A) / len(A) - sum(B) / len(B)
    days = sorted(set(day(s) for s, _ in base)); byday = collections.defaultdict(list)
    for s, r in base: byday[day(s)].append((sel(s), r[key]))
    bs = []
    for _ in range(2000):
        pick = [rnd.choice(days) for _ in days]
        a = [v for dd in pick for k, v in byday[dd] if k]; b = [v for dd in pick for k, v in byday[dd] if not k]
        if a and b: bs.append(sum(a) / len(a) - sum(b) / len(b))
    bs.sort()
    return len(A), len(B), d, bs[int(.025 * len(bs))], bs[int(.975 * len(bs))]
for off in (0, 2, 3, 7, 8):
    for key in ('r_go', 'r_ladder'):
        a, b, d, lo, hi = diff_ci(rule(off), key)
        print(f"  grid offset {off}  {key:9s} aligned n={a:3d} vs rest n={b:3d} | diff {d:+.2f}R  day-block 95% CI [{lo:+.2f},{hi:+.2f}]" + ('   <- ROUND NUMBERS' if off == 0 else ''))
