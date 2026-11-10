from sim import *
rows = [json.loads(l) for l in open(S + 'study.jsonl')]
def show(label, sets):
    if not sets: print(label, 'n=0'); return
    atr = [s['atr'] for s in sets]
    sr = [s['components']['structural_risk_pips'] for s in sets]; er = [s['components']['effective_risk_pips'] for s in sets]
    eff = [s['components']['effective_room_r'] for s in sets]; lim = [s['components']['room_limited_by_barrier'] for s in sets]
    chase = [s['components'].get('entry_chase_atr', float('nan')) for s in sets]
    struct = [s['components']['score_structure'] for s in sets]
    print(f"{label}: n={len(sets)}  M5 ATR median ${statistics.median(atr):.2f} (range {min(atr):.2f}-{max(atr):.2f}) ")
    print(f"   structural risk pips median {statistics.median(sr):.0f} (min {min(sr):.0f}, max {max(sr):.0f}); effective {statistics.median(er):.0f}; effective risk / ATR median {statistics.median([e*PIP/a for e,a in zip(er,atr)]):.2f}")
    print(f"   floor-bound (structural < 50 pips): {sum(x<50 for x in sr)}/{len(sets)}  | structural risk / ATR median {statistics.median([e*PIP/a for e,a in zip(sr,atr)]):.2f}")
    print(f"   target kind: {dict(collections.Counter(s['target_kind'] for s in sets))}")
    print(f"   technical RR median {statistics.median(s['rr'] for s in sets):.2f}  effective room R median {statistics.median(eff):.2f}; room limited by barrier {int(sum(lim))}/{len(sets)}; eff room<1R: {sum(e<1 for e in eff)}")
    print(f"   entry_chase_atr at publication median {statistics.median(c for c in chase if c==c):.2f} (max {max(c for c in chase if c==c):.2f}); score_structure<0.5: {sum(x<0.5 for x in struct)}; quality overall median {statistics.median(s['quality'] for s in sets):.2f}")
    print(f"   confirmation candle: wick {sum(s['wick'] for s in sets)} engulf {sum(s['engulf'] for s in sets)} none {sum(not s['wick'] and not s['engulf'] for s in sets)}; major {sum(s['major'] for s in sets)}; sessions utc hour {sorted(collections.Counter(time.gmtime(s['created_at']).tm_hour for s in sets).items())}")
show('PUBLISHED NOW (B, floor 2, optional)', [r for r in rows if r['rec'] == 'setup'])
show('B floor1 optional', [r for r in rows if r['rec'] == 'setup_open' and r['kind'] == 'break_retest'])
show('A floor1 (refused by floor)', [r for r in rows if r['rec'] == 'setup_open' and r['kind'] == 'sweep_reclaim'])
# time-to-fill and distance at publication
print()
for rec in ('setup', 'setup_open'):
    for kind in ('break_retest', 'sweep_reclaim'):
        sets = [r for r in rows if r['rec'] == rec and r['kind'] == kind]
        if not sets: continue
        ttf = []; dist = []
        for s in sets:
            r = simulate(s)
            if r['status'] == 'filled':
                start = [i for i, b in enumerate(BARS) if b['t'] >= s['created_at']][0]
                ttf.append((r['fill'] - start) * 5)
                px = BARS[start - 1]['c']
                dist.append(abs(px - r['ref']) / s['atr'])
        if ttf: print(f"{rec}/{kind}: n={len(sets)} filled {len(ttf)} minutes to fill median {statistics.median(ttf)} (0=next bar) p75 {sorted(ttf)[int(.75*len(ttf))]}; price vs ref at creation (ATR) median {statistics.median(dist):.2f}")
