"""Generate the Go/Python parity fixture for analysis-engine/internal/barrier.

The expected output is produced by the REAL Python barrier-book code (the
Python-origin path in algo-bot/app/autotrade/structural_barriers.py), so the
Go port is checked against the implementation it was ported from. Regenerate:

  cd algo-bot && PYTHONPATH=. python ../analysis-engine/test/barrier/testdata/gen_python_parity_golden.py \
    > ../analysis-engine/test/barrier/testdata/python_parity_golden.json
"""
import json, random, sys
from types import SimpleNamespace

from app.autotrade.structural_barriers import (
  DEFAULT_STRUCTURAL_TIMEFRAMES, StructuralBarrier, _merge_same_side, _resolve_cross_side_overlaps,
)
from app.autotrade.structural_target_room import zone_meets_execution_width

random.seed(20260930)
TFS = ["M1", "M5", "M15", "H1"]
CASES = []


def make_case(idx):
  pip = random.choice([0.0001, 0.01, 0.1])
  base = {0.0001: 1.13, 0.01: 156.7, 0.1: 4180.0}[pip]
  atrs = {tf: pip * random.uniform(2, 120) for tf in TFS}
  # Deliberately produce touching, nested, zero-width and huge zones.
  n = random.randint(0, 40)
  zones = []
  for _ in range(n):
    tf = random.choice(TFS)
    side = random.choice(["buy", "sell"])
    low = base + pip * random.uniform(-150, 150)
    kind = random.random()
    if kind < 0.08:
      width = 0.0
    elif kind < 0.16:
      width = pip * random.uniform(300, 900)  # trips the pip / ATR width gate
    else:
      width = pip * random.uniform(1, 60)
    high = low + width
    if zones and random.random() < 0.15:  # touch an earlier zone at a shared edge
      prev = random.choice(zones)
      low = prev["high"] if random.random() < 0.5 else prev["low"] - width
      high = low + width
    live = random.random() < 0.85
    zones.append({
      "timeframe": tf, "side": side, "low": low, "high": high, "atr": atrs[tf] if random.random() > 0.05 else 0.0,
      "score": (round(random.uniform(0, 1), 1) if random.random() < 0.6 else round(random.uniform(0, 1), 4)), "touches": random.randint(0, 4), "live": live,
    })
  cfg = {
    "pip_size": pip,
    "max_width_atr": random.choice([2.0, 2.0, 1.0, 6.0]),
    "max_width_pips": random.choice([100.0, 100.0, 40.0]),
  }
  # --- expected, computed by the real Python code ---
  barriers = []
  for z in zones:
    if not z["live"] or z["timeframe"] not in DEFAULT_STRUCTURAL_TIMEFRAMES:
      continue
    py_zone = SimpleNamespace(low=z["low"], high=z["high"])
    if not zone_meets_execution_width(
      py_zone, atr=z["atr"], pip_size=cfg["pip_size"],
      max_width_atr=cfg["max_width_atr"], max_width_pips=cfg["max_width_pips"],
    ):
      continue
    barriers.append(StructuralBarrier(
      side=z["side"], low=z["low"], high=z["high"], tier="zone", score=z["score"],
      touches=z["touches"], mitigated=False, source_timeframes=(z["timeframe"],),
    ))
  expected = [
    {"side": b.side, "low": b.low, "high": b.high, "tier": b.tier, "score": b.score,
     "touches": b.touches, "source_timeframes": list(b.source_timeframes)}
    for b in _resolve_cross_side_overlaps(_merge_same_side(list(barriers)))
  ]
  return {"name": "case_%03d" % idx, "config": cfg, "zones": zones, "expected": expected}


for i in range(120):
  CASES.append(make_case(i))
# Guarantee the interesting paths are represented at all.
print("cases", len(CASES), "with cross-side conflicts:", sum(
  1 for c in CASES if len({b["side"] for b in c["expected"]}) == 2),
  "non-empty:", sum(1 for c in CASES if c["expected"]), file=sys.stderr)
json.dump({"generated_by": "algo-bot structural_barriers._merge_same_side/_resolve_cross_side_overlaps + zone_meets_execution_width",
           "cases": CASES}, sys.stdout, separators=(",", ":"))
