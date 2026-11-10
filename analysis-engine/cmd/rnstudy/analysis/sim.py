import json, math, statistics, random, sys, collections, time
import os
S = os.environ['RN_DIR'].rstrip('/') + '/'
PIP = 0.1
FLOOR, CAP = 50 * PIP, 70 * PIP     # XAU stop envelope (config/instruments.yml: min 50, max 70 pips; pip = $0.10)
SPREAD = 0.30                       # USD (3 pips); XAU max_spread_pips 5
LADDER = [(1.0, .4), (2.0, .2), (3.0, .2), (4.0, .2)]
HORIZON = 288                       # 24h of M5

cap = json.load(open(S + 'cap-xau.json'))
BARS = [dict(t=r[0], o=r[1], h=r[2], l=r[3], c=r[4]) for r in cap['timeframes']['M5']]
IDX = {b['t']: i for i, b in enumerate(BARS)}

def simulate(s, spread=SPREAD, expiry_bars=24, enforce_cap=True):
    """One resting limit at the proximal edge of the entry band (BUY: entry_high, SELL: entry_low), the stop at max(structural, floor) behind it.
    bid candles; long pays ask = bid + spread. Stop before target inside one bar. Returns None when refused/never filled."""
    buy = s['dir'] == 'BUY'
    ref = s['entry_high'] if buy else s['entry_low']
    stop_pub, target = s['stop'], s['target']
    risk_struct = (ref - stop_pub) if buy else (stop_pub - ref)
    if not risk_struct > 0 or (buy and not target > ref) or (not buy and not target < ref):
        return dict(status='bad_geometry')
    if enforce_cap and risk_struct > CAP + 1e-9:
        return dict(status='refused_cap', risk=risk_struct)
    R = max(risk_struct, FLOOR)
    stop = ref - R if buy else ref + R
    start = IDX.get(s['created_at'])
    if start is None:
        # first bar at/after created_at
        cands = [i for i, b in enumerate(BARS) if b['t'] >= s['created_at']]
        if not cands: return dict(status='no_data')
        start = cands[0]
    fill = None
    for j in range(start, min(start + expiry_bars, len(BARS))):
        b = BARS[j]
        # invalidated before the fill: the published structural stop traded through (bid for BUY, ask for SELL)
        if buy and b['l'] <= stop_pub and not (b['l'] + spread <= ref): pass
        if buy:
            if b['l'] + spread <= ref: fill = j; break
            if b['l'] <= stop_pub: return dict(status='invalidated')
        else:
            if b['h'] >= ref: fill = j; break
            if b['h'] + spread >= stop_pub: return dict(status='invalidated')
    if fill is None: return dict(status='unfilled')
    out = dict(status='filled', fill=fill, R=R, ref=ref, buy=buy, struct=risk_struct / PIP, floor_bound=risk_struct < FLOOR, t=BARS[fill]['t'])
    # M1: the Go target (single exit)
    res = None; mfe = 0.0; mae = 0.0
    for j in range(fill, min(fill + HORIZON, len(BARS))):
        b = BARS[j]
        if buy:
            adv = (ref - b['l']) / R; fav = (b['h'] - ref) / R
            hit_sl = b['l'] <= stop; hit_tp = b['h'] >= target and j > fill - 1
        else:
            adv = (b['h'] + spread - ref) / R; fav = (ref - (b['l'] + spread)) / R
            hit_sl = b['h'] + spread >= stop; hit_tp = (b['l'] + spread) <= target
        mae = max(mae, adv)
        if hit_sl: res = -1.0; mfe = max(mfe, 0) ; break
        mfe = max(mfe, fav)
        if hit_tp:
            res = abs(target - ref) / R; break
    if res is None:
        b = BARS[min(fill + HORIZON, len(BARS)) - 1]
        res = ((b['c'] - ref) if buy else (ref - (b['c'] + spread))) / R
        out['open_at_end'] = True
    out.update(r_go=res, mfe=mfe, mae=min(mae, 5), go_rr=abs(target - ref) / R)
    # M2: 1R/2R/3R/4R ladder 40/20/20/20, stop to entry after 1R
    rem = 1.0; pnl = 0.0; cur_stop = stop; be = False; li = 0; done = False
    for j in range(fill, min(fill + HORIZON, len(BARS))):
        b = BARS[j]
        if buy: sl = b['l'] <= cur_stop
        else: sl = b['h'] + spread >= cur_stop
        if sl:
            pnl += rem * ((cur_stop - ref) / R if buy else (ref - cur_stop) / R); rem = 0; done = True; break
        while li < len(LADDER):
            lvl, frac = LADDER[li]
            tp = ref + lvl * R if buy else ref - lvl * R
            hit = b['h'] >= tp if buy else (b['l'] + spread) <= tp
            if not hit: break
            pnl += frac * lvl; rem -= frac; li += 1
            if li == 1: cur_stop = ref; be = True
        if li == len(LADDER): done = True; break
    if not done:
        b = BARS[min(fill + HORIZON, len(BARS)) - 1]
        pnl += rem * (((b['c'] - ref) if buy else (ref - (b['c'] + spread))) / R)
    out['r_ladder'] = pnl
    return out

def stats(vals):
    n = len(vals)
    if n == 0: return dict(n=0)
    m = sum(vals) / n
    d = dict(n=n, mean=m, median=statistics.median(vals), total=sum(vals), win=sum(v > 0 for v in vals) / n)
    gp = sum(v for v in vals if v > 0); gl = -sum(v for v in vals if v < 0)
    d['pf'] = gp / gl if gl > 0 else float('inf')
    if n >= 2:
        rnd = random.Random(7)
        bs = sorted(sum(rnd.choice(vals) for _ in range(n)) / n for _ in range(2000))
        d['lo'], d['hi'] = bs[50], bs[1949]
    return d

def fmt(d):
    if d['n'] == 0: return 'n=0'
    s = f"n={d['n']:<3} mean {d['mean']:+.2f}R med {d['median']:+.2f} win {d['win']*100:3.0f}% PF {d['pf']:.2f} tot {d['total']:+.1f}"
    if 'lo' in d: s += f" CI[{d['lo']:+.2f},{d['hi']:+.2f}]"
    return s
