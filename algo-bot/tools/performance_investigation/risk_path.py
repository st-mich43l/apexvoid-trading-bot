"""Cost on stop-outs, serial dependence, daily R, drawdown, concurrency and the zero-edge risk of ruin."""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

T = trades()
print('== average R on full stops by symbol (a pure stop is -1.00R)')
for s in PIP:
  v = [t['R'] for t in T if t['sym'] == s and t['exit_path'] == 'full_stop']
  print(f"  {s:7} n={len(v):3} mean {st.mean(v):+.3f}R  stop median {st.median(t['stop'] for t in T if t['sym'] == s):.1f} pips")
xk = [t['R'] for t in T if t['sym'] == 'XAU' and t['exit_path'] == 'full_stop' and t['strategy'] == 'key_level']
print(f"  XAU key_level stops: n={len(xk)} mean {st.mean(xk):+.3f}R")
print('\n== serial dependence')
R = [t['R'] for t in sorted(T, key=lambda t: t['t1'])]
m = st.mean(R)
print('  lag-1 autocorrelation of trade R:', round(sum((R[i] - m) * (R[i + 1] - m) for i in range(len(R) - 1)) / sum((x - m) ** 2 for x in R), 3))
best = cur = 0
for x in R:
  cur = cur + 1 if x < 0 else 0
  best = max(best, cur)
print('  longest losing streak:', best)
print('\n== daily R and drawdown')
day = collections.defaultdict(float)
for t in T:
  day[dt.datetime.fromtimestamp(t['t1'], dt.UTC).date()] += t['R']
ds = [day[d] for d in sorted(day)]
print('  days', len(ds), 'mean daily R', round(st.mean(ds), 2), 'stdev', round(st.pstdev(ds), 2), 'worst', round(min(ds), 1), 'best', round(max(ds), 1))
eq = peak = mdd = 0
for x in R:
  eq += x
  peak = max(peak, eq)
  mdd = max(mdd, peak - eq)
print('  cumulative R', round(sum(R), 1), 'max drawdown (R)', round(mdd, 1))
print('\n== concurrency of open groups')
ev = []
for t in T:
  ev += [(t['t0'], 1), (t['t1'], -1)]
ev.sort()
cur = mx = area = 0
last = ev[0][0]
for tm, d in ev:
  area += cur * (tm - last)
  last = tm
  cur += d
  mx = max(mx, cur)
print('  average open groups', round(area / (ev[-1][0] - ev[0][0]), 2), 'max', mx)
print('\n== zero-edge risk of ruin: 400 trades, R of -1 or +1.3 with probability that nets to zero')
rr = random.Random(4)
for risk in (0.5, 1.0, 2.0, 3.0, 5.0):
  bad = 0
  N = 3000
  for _ in range(N):
    e = 100.0
    for _ in range(400):
      e += e * risk / 100 * (1.3 if rr.random() < 1 / 2.3 else -1)
      if e < 50:
        bad += 1
        break
  print(f"  {risk:.1f}% risk per trade: P(lose half the account) = {bad / N:.0%}")
