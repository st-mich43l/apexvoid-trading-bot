"""Generate the Go/Python parity fixture for analysis-engine/internal/momentum.

Expected state/velocity/acceleration come from the REAL Python
app.analysis.momentum.momentum_state, so the Go port is checked against the
implementation it was ported from. Regenerate:

  cd algo-bot && PYTHONPATH=. python ../analysis-engine/test/momentum/testdata/gen_python_parity_golden.py \\
    > ../analysis-engine/test/momentum/testdata/python_parity_golden.json
"""
import json
import random
import sys

import pandas as pd

from app.analysis.momentum import momentum_state

random.seed(20260930)
CASES = []


def series(n, base, step):
  rows = []
  price = base
  for i in range(n):
    drift = random.choice([-1, 1]) * random.random() * step
    if random.random() < 0.3:
      drift *= 4  # bursts, so both bull and bear states are reached
    o = price
    c = price + drift
    h = max(o, c) + random.random() * step
    l = min(o, c) - random.random() * step
    rows.append((3600 * i, o, h, l, c, 100.0))
    price = c
  return rows


for idx in range(160):
  n = random.choice([0, 1, 2, 3, 5, 9, 10, 17, 18, 25, 40, 80])
  base, step = random.choice([(4180.0, 3.0), (1.13, 0.0006), (156.7, 0.08)])
  rows = series(n, base, step)
  lookback = random.choice([1, 3, 8, 8, 8, 12, 20])
  bull = random.choice([0.15, 0.15, 0.05, 0.3])
  bear = -bull if random.random() < 0.8 else -random.choice([0.1, 0.2])
  if n == 0:
    # An empty frame is a caller-side no-op in production; keep the case out of
    # the pandas call but record the documented neutral result.
    expected = {"state": "neutral", "velocity": 0.0, "acceleration": 0.0, "lookback": max(1, lookback)}
  else:
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    r = momentum_state(df, None, lookback, bull, bear)
    expected = {"state": r.state, "velocity": r.velocity, "acceleration": r.acceleration, "lookback": r.lookback}
  CASES.append({
    "name": "case_%03d" % idx,
    "config": {"lookback": lookback, "bull": bull, "bear": bear},
    "candles": rows,
    "expected": expected,
  })

states = {}
for c in CASES:
  states[c["expected"]["state"]] = states.get(c["expected"]["state"], 0) + 1
print("cases", len(CASES), "states", states, "nonzero accel:", sum(1 for c in CASES if c["expected"]["acceleration"] != 0), file=sys.stderr)
json.dump({"generated_by": "algo-bot app.analysis.momentum.momentum_state", "cases": CASES}, sys.stdout, separators=(",", ":"))
