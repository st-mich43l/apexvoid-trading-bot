from sim import *
rows = [json.loads(l) for l in open(S + 'study.jsonl')]
f = lambda t: time.strftime('%m-%d %H:%M', time.gmtime(t))
def run(sets, label, show=False):
    res = [(s, simulate(s)) for s in sets]
    c = collections.Counter(r['status'] for _, r in res)
    filled = [r for _, r in res if r['status'] == 'filled']
    print(f"{label:38s} n={len(sets):3d} {dict(c)}")
    if filled:
        print('    Go target :', fmt(stats([r['r_go'] for r in filled])))
        print('    R ladder  :', fmt(stats([r['r_ladder'] for r in filled])))
        print('    MFE/MAE med', round(statistics.median(r['mfe'] for r in filled), 2), round(statistics.median(r['mae'] for r in filled), 2))
    if show:
        for s, r in res:
            print('     ', f(s['created_at']), s['kind'][:5], s['dir'], s['handle'], r['status'], {k: round(v, 2) for k, v in r.items() if k in ('r_go', 'r_ladder', 'mfe', 'mae')})
    return res
for rec, lab in (('setup_conf', 'PROD-OLD (confirmation required, floor2)'), ('setup', 'PROD-NOW (confirmation optional, floor2)'),
                 ('setup_open_conf', 'confirmation required, floor1'), ('setup_open', 'confirmation optional, floor1')):
    print('==', lab)
    sets = [r for r in rows if r['rec'] == rec]
    run(sets, 'all', show=(rec in ('setup_conf', 'setup')))
    for k in ('break_retest', 'sweep_reclaim'):
        run([r for r in sets if r['kind'] == k], k)
