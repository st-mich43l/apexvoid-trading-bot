"""Regenerate internal/strategy/scalpbreakoutretest/testdata/episode-cases.json.

Each case is a hand-built closed-bar sequence run through the Python Breakout
Retest V2 state machine (app.scalping.microstructure.evaluate_breakout_retest_episode)
as it stood at commit a1c77584, the end of the profitable XAU week. The Go unit
test replays the same bars through the Go state machine and requires the same
state, failure reason and telemetry. Run it like generate_oracle_golden.py in
test/keylevelparity (checkout a1c77584 read-only at /oracle, algo-bot image).
"""

import json
import os
import sys

for key, value in {
  "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
  "TELEGRAM_CHAT_ID": "-100123456789",
  "DATABASE_URL": "postgresql://apexvoid:apexvoid@localhost:5/signals",
  "POSTGRES_PASSWORD": "x",
}.items():
  os.environ.setdefault(key, value)
sys.path.insert(0, "/oracle/algo-bot")
import pandas as pd

from app.scalping.microstructure import evaluate_breakout_retest_episode
from app.scalping.models import BreakoutLevelCandidate

ENGINE = os.environ.get("ENGINE_DIR", "/engine")
OUT = os.path.join(ENGINE, "internal/strategy/scalpbreakoutretest/testdata/episode-cases.json")
BASE = 1_790_000_000
LEVEL = 100.0
ATR = 1.0

DEFAULTS = {
  "cross_tolerance": 0.0, "breakout_margin": 0.0, "max_break_delay_bars": None, "acceptance_bars": 1,
  "acceptance_required_closes": 1, "min_retest_delay_bars": 0, "max_retest_delay_bars": 20,
  "retest_front_run_tolerance": 0.0, "max_retest_penetration": 0.3, "confirmation_mode": "reclaim_close",
}


def bar(o, h, l, c):
  return [o, h, l, c]


def flat(n, price=99.4):
  return [bar(price, price + 0.2, price - 0.2, price) for _ in range(n)]


def mirror(bars):
  return [[2 * LEVEL - o, 2 * LEVEL - l, 2 * LEVEL - h, 2 * LEVEL - c] for o, h, l, c in bars]


def frame(bars):
  rows = []
  for i, (o, h, l, c) in enumerate(bars):
    rows.append({"t": BASE + 300 * i, "open": o, "high": h, "low": l, "close": c, "volume": 1})
  df = pd.DataFrame(rows)
  df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("t"), unit="s", utc=True), name="time")
  return df


CASES = []


def case(name, side, bars, candidate=None, **overrides):
  params = {**DEFAULTS, **overrides}
  if side == "SELL":
    bars = mirror(bars)
  candidate = candidate or {}
  cand = BreakoutLevelCandidate(
    level=LEVEL, side=side, source=candidate.get("source", "m5_swing_high" if side == "BUY" else "m5_swing_low"),
    subtype=candidate.get("subtype", "structure_flip"), timeframe="M5",
    source_index=candidate.get("source_index"), source_time=None, strength=None,
    metadata=candidate.get("metadata", {}),
  )
  result = evaluate_breakout_retest_episode(frame(bars), cand, atr=ATR, mad=0.0, **params)
  keep = {k: result.get(k) for k in (
    "state", "failure_reason", "break_index", "break_time", "break_distance", "break_distance_atr", "accepted",
    "acceptance_bars", "retest_index", "bars_until_retest", "retest_penetration", "retest_penetration_atr",
    "confirmation_type", "directionally_valid_close",
  )}
  CASES.append({
    "name": f"{name} {side}", "side": side, "level": LEVEL, "atr": ATR, "bars": bars,
    "source_index": candidate.get("source_index"), "invalidation": candidate.get("metadata", {}).get("invalidation_level"),
    "params": params, "expect": keep,
  })


# 8 flat bars under the level, then a breakout bar that closes beyond it.
pre = flat(8)
brk = bar(99.6, 100.8, 99.5, 100.6)
hold = bar(100.6, 100.9, 100.4, 100.5)
retest = bar(100.4, 100.7, 99.95, 100.3)       # touches the level, closes back above it
away = [bar(100.8, 101.2, 100.7, 101.0) for _ in range(30)]

for side in ("BUY", "SELL"):
  case("armed at the retest", side, pre + [brk, hold, retest])
  case("no cross yet", side, pre)
  case("break not through the margin", side, pre + [bar(99.6, 100.4, 99.5, 100.3), hold, retest], breakout_margin=0.5)
  case("immediate reclaim fails acceptance", side, pre + [brk, bar(100.5, 100.6, 99.4, 99.7), retest])
  case("two closes required but only one held", side, pre + [brk, hold, bar(100.5, 100.6, 99.4, 99.8)], acceptance_bars=2, acceptance_required_closes=2)
  case("acceptance still pending", side, pre + [brk], acceptance_bars=2, acceptance_required_closes=2)
  case("retest still pending", side, pre + [brk, hold, bar(100.8, 101.3, 100.6, 101.2)])
  case("retest too early is skipped", side, pre + [brk, retest, bar(100.4, 101.2, 100.4, 101.0)], min_retest_delay_bars=2)
  case("retest too late", side, pre + [brk, hold] + away[:5] + [retest], max_retest_delay_bars=3)
  case("retest penetrates too deep", side, pre + [brk, hold, bar(100.4, 100.6, 99.5, 100.2)])
  case("front-run retest still qualifies", side, pre + [brk, hold, bar(100.5, 100.8, 100.15, 100.4)], retest_front_run_tolerance=0.2)
  case("front-run retest disabled never touches", side, pre + [brk, hold, bar(100.5, 100.8, 100.15, 100.4)])
  case("retest closes without flipping the role", side, pre + [brk, hold, bar(100.3, 100.5, 99.95, 99.97)])
  case("high-break confirmation pending", side, pre + [brk, hold, retest], confirmation_mode="retest_high_break")
  case("high-break confirmation armed", side, pre + [brk, hold, retest, bar(100.3, 100.9, 100.25, 100.8)], confirmation_mode="retest_high_break")
  case("high-break confirmation missing", side, pre + [brk, hold, retest, bar(100.3, 100.6, 100.2, 100.4)], confirmation_mode="retest_high_break")
  case("break too long after its source", side, pre + [bar(99.4, 99.6, 99.3, 99.4)] * 4 + [brk, hold, retest], candidate={"source_index": 2}, max_break_delay_bars=3)
  case("box edge breached during acceptance", side, pre + [brk, bar(100.5, 100.6, 98.7, 98.9), retest],
       candidate={"source": "compression_box", "subtype": "range_break", "source_index": 4, "metadata": {"invalidation_level": 99.0, "box_start_index": 0, "box_end_index": 4}})
  case("level cannot be broken at or before its own bar", side, pre + [brk, hold, retest], candidate={"source_index": 8})

json.dump({"oracle_commit": "a1c77584", "cases": CASES}, open(OUT, "w"), separators=(",", ":"))
print(len(CASES), "cases")
