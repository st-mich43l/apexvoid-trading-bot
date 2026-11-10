from sim import *
rows = [json.loads(l) for l in open(S + 'study.jsonl')]
raw = [r for r in rows if r['rec'] == 'raw']
def block(sets, label):
    res = [simulate(s) for s in sets]
    c = collections.Counter(r['status'] for r in res)
    filled = [r for r in res if r['status'] == 'filled']
    g = stats([r['r_go'] for r in filled]); l = stats([r['r_ladder'] for r in filled])
    print(f"{label:30s} setups {len(sets):3d} filled {len(filled):3d} refused_cap {c.get('refused_cap',0)} unfilled {c.get('unfilled',0)+c.get('invalidated',0)} | go {fmt(g) if g['n'] else ''}")
    if filled: print(f"{'':30s}                                              | ladder {fmt(l)}")
    return filled
for opt in (True, False):
    print('=== candle confirmation optional =', opt, '(raw detector, no confluence floor, target = next handle)')
    for kind in ('break_retest', 'sweep_reclaim'):
        print('--', kind)
        block([r for r in raw if r['group'] == 'off0' and r['optional'] == opt and r['kind'] == kind], 'ROUND (all)')
        block([r for r in raw if r['group'] == 'off0' and r['optional'] == opt and r['kind'] == kind and r['major']], '  round major (x50)')
        block([r for r in raw if r['group'] == 'off0' and r['optional'] == opt and r['kind'] == kind and not r['major']], '  round minor')
        ctrl = [r for r in raw if r['group'] != 'off0' and r['optional'] == opt and r['kind'] == kind]
        block(ctrl, 'CONTROL pooled off2/3/7/8')
        for g in ('off2', 'off3', 'off7', 'off8'):
            block([r for r in ctrl if r['group'] == g], '  ' + g)
