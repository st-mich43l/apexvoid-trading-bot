"""Regenerate internal/strategy/scalpbreakoutretest/testdata/source-cases.json.

Seeded random-walk M5 series run through the Python Breakout Retest V2 level
sources as they stood at commit a1c77584: the setup window's micro structure
(swings, equal highs/lows), the structure-flip, liquidity-level and M5 key-level
candidates, and the compression box with its legacy break/retest detector. The Go
unit test builds the same from the same bars and requires identical output.
"""

import json
import os
import random
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

from app.analysis.engine import AnalysisSettings, scalp_structure
from app.scalping.microstructure import (
  build_micro_structure,
  detect_breakout_retest,
  find_compression_box,
  liquidity_level_candidates,
  m5_structure_flip_candidates,
  structure_flip_candidates,
)
from app.scalping.unified_context import _m5_atr, scalp_structure_payload

ENGINE = os.environ.get("ENGINE_DIR", "/engine")
OUT = os.path.join(ENGINE, "internal/strategy/scalpbreakoutretest/testdata/source-cases.json")
BASE = 1_790_000_000
PIP = 0.1


def walk(seed, n=120, start=4100.0, step=0.35, wick=(0.04, 0.3), compress=None):
  rng = random.Random(seed)
  price, rows = start, []
  for i in range(n):
    scale = step * (0.25 if compress and compress[0] <= i < compress[1] else 1.0)
    o = price
    c = round(o + rng.gauss(0, scale), 2)
    h = round(max(o, c) + rng.uniform(*wick), 2)
    l = round(min(o, c) - rng.uniform(*wick), 2)
    rows.append([round(o, 2), h, l, c])
    price = c
  return rows


def frame(rows):
  df = pd.DataFrame([{"t": BASE + 300 * i, "open": o, "high": h, "low": l, "close": c, "volume": 1} for i, (o, h, l, c) in enumerate(rows)])
  df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("t"), unit="s", utc=True), name="time")
  return df


def cand(c):
  return {"level": float(c.level), "source": c.source, "subtype": c.subtype, "timeframe": c.timeframe,
          "source_index": c.source_index}


cases = []
configs = [(seed, None) for seed in range(1, 13)] + [(seed, (60, 108)) for seed in range(20, 30)]
for seed, compress in configs:
  rows = walk(seed, compress=compress)
  df = frame(rows)
  atr = _m5_atr(df, pip_size=PIP)
  micro = build_micro_structure(df, equal_tol=0.5 * PIP, price_digits=2)
  structure = scalp_structure(df, AnalysisSettings(pip_size=PIP))
  key_levels, _ = scalp_structure_payload(structure)
  record = {
    "seed": seed, "bars": rows, "atr": atr,
    "swings": [[s.kind, s.price, s.index] for s in micro.swings],
    "equal_highs": list(micro.equal_highs), "equal_lows": list(micro.equal_lows),
    "key_levels": [[l["price"], l["touches"]] for l in key_levels],
    "price": float(df["close"].iloc[-1]),
    "flip": {}, "liquidity": {}, "m5": {}, "box": None,
  }
  for side in ("BUY", "SELL"):
    record["flip"][side] = [cand(c) for c in structure_flip_candidates(
      micro, side=side, current_index=len(df) - 1, timeframe="M5", min_age_bars=3, max_age_bars=240, atr=atr, min_level_spacing_atr=0.3)]
    record["liquidity"][side] = [cand(c) for c in liquidity_level_candidates(micro, side=side, timeframe="M5")]
    record["m5"][side] = [cand(c) for c in m5_structure_flip_candidates(key_levels, side=side, current_price=record["price"], min_touches=2)]
  box = find_compression_box(df, atr=atr, min_box_bars=8, max_box_bars=20, box_max_atr=1.5, min_touches_per_side=2, touch_tol_atr=0.20)
  if box is not None:
    record["box"] = {k: box[k] for k in ("box_low", "box_high", "box_bars", "box_end_index", "touch_count")}
    min_disp = max(PIP * 3, atr * 0.25)
    record["box"]["detect"] = {}
    for side in ("BUY", "SELL"):
      ev = detect_breakout_retest(df, direction=side, box_high=box["box_high"], box_low=box["box_low"], min_displacement=min_disp,
                                  retest_lookback_bars=5, require_retest_rejection=True, box_end_index=box["box_end_index"])
      record["box"]["detect"][side] = {k: ev.get(k) for k in ("state", "level", "close", "break_displacement")}
  cases.append(record)

# End to end: the armed-compression-box series with a synthetic M1 confirmation
# frame (or none), through Python's discover_breakout_retest as the live lane
# drove it.
from dataclasses import replace

from app.runtime.instrument_config import instrument_runtime_view
from app.scalping.strategies import discover_breakout_retest
from app.scalping.unified_context import build_scalp_context_and_micro

cfg = instrument_runtime_view("XAU")
M5_END = BASE + 300 * 120
T1 = M5_END - 60


def m1_frame(level, side, confirm, seed):
  rng = random.Random(seed)
  rows, price = [], level + (0.5 if side == "BUY" else -0.5)
  for i in range(60):
    o = round(price + rng.gauss(0, 0.05), 2)
    c = round(o + rng.gauss(0, 0.08), 2)
    rows.append([o, round(max(o, c) + 0.04, 2), round(min(o, c) - 0.04, 2), c])
    price = c
  if confirm:
    # A wick through the level that closes back beyond it in the trade direction.
    if side == "BUY":
      rows[-1] = [round(level - 0.1, 2), round(level + 0.15, 2), round(level - 0.2, 2), round(level + 0.1, 2)]
    else:
      rows[-1] = [round(level + 0.1, 2), round(level + 0.2, 2), round(level - 0.15, 2), round(level - 0.1, 2)]
  else:
    # The last bars close against the trade direction: no confirmation.
    if side == "BUY":
      rows[-1] = [round(level + 0.3, 2), round(level + 0.35, 2), round(level - 0.2, 2), round(level - 0.1, 2)]
    else:
      rows[-1] = [round(level - 0.3, 2), round(level + 0.2, 2), round(level - 0.35, 2), round(level + 0.1, 2)]
  m1 = pd.DataFrame([{"t": T1 - 60 * (59 - i), "open": o, "high": h, "low": l, "close": c, "volume": 1} for i, (o, h, l, c) in enumerate(rows)])
  m1.index = pd.DatetimeIndex(pd.to_datetime(m1.pop("t"), unit="s", utc=True), name="time")
  return rows, m1


import app.scalping.strategies as strategies_module

_seen_levels = []
_real_confirm = strategies_module.confirm_m1_execution


def _recording_confirm(df, **kwargs):
  _seen_levels.append((kwargs.get("direction"), kwargs.get("level")))
  return _real_confirm(df, **kwargs)


strategies_module.confirm_m1_execution = _recording_confirm


def best_level(snap, m1, df, micro5, side):
  """The level discovery chose for `side`, learnt from the M1 confirmation call."""
  _seen_levels.clear()
  discover_breakout_retest(snap, build_micro_structure(m1, equal_tol=0.5 * PIP, price_digits=2), m1, cfg, pip_size=PIP, now=T1,
                           spread_pips=0.0, idle_reasons=[], m5_df=df, m5_micro=micro5)
  return next((lvl for direction, lvl in _seen_levels if direction == side), None)


e2e = []
for record in cases:
  if not record["box"]:
    continue
  rows = record["bars"]
  df = frame(rows)
  for side in ("BUY", "SELL"):
    detect = record["box"]["detect"][side]
    if detect["state"] != "armed":
      continue
    level = detect["level"]
    for confirm in (True, False):
      m1_rows, m1 = m1_frame(level, side, False, record["seed"])
      probe_snap, _m, _ms = build_scalp_context_and_micro(symbol="XAU", windows={"m1": m1, "m5": df, "m15": None, "h1": None}, price=float(m1["close"].iloc[-1]),
                                                          pip_size=PIP, now=T1, cfg=cfg, htf_bias="up", m5_structure="range", regime="range")
      kl0, zs0 = scalp_structure_payload(scalp_structure(df, AnalysisSettings(pip_size=PIP)))
      probe_snap = replace(probe_snap, key_levels=kl0, zones=zs0)
      chosen = best_level(probe_snap, m1, df, build_micro_structure(df, equal_tol=0.5 * PIP, price_digits=2), side)
      if chosen is not None:
        level = chosen
      m1_rows, m1 = m1_frame(level, side, confirm, record["seed"])
      windows = {"m1": m1, "m5": df, "m15": None, "h1": None}
      price = float(m1["close"].iloc[-1])
      snap, _micro, _ = build_scalp_context_and_micro(symbol="XAU", windows=windows, price=price, pip_size=PIP, now=T1, cfg=cfg,
                                                      htf_bias="up", m5_structure="range", regime="range")
      structure = scalp_structure(df, AnalysisSettings(pip_size=PIP))
      kl, zs = scalp_structure_payload(structure)
      snap = replace(snap, key_levels=kl, zones=zs)
      micro1 = build_micro_structure(m1, equal_tol=0.5 * PIP, price_digits=2)
      micro5 = build_micro_structure(df, equal_tol=0.5 * PIP, price_digits=2)
      idle = []
      opps = discover_breakout_retest(snap, micro1, m1, cfg, pip_size=PIP, now=T1, spread_pips=0.0, idle_reasons=idle, m5_df=df, m5_micro=micro5)
      e2e.append({
        "seed": record["seed"], "side": side, "confirm": confirm, "m5": rows, "m1": m1_rows, "idle": idle,
        "opportunities": [{
          "direction": o.direction, "zone_low": o.zone_low, "zone_high": o.zone_high, "invalidation": o.invalidation_price,
          "target": o.expected_target_price, "stop_pips": o.expected_stop_pips, "target_pips": o.expected_target_pips,
          "source": o.measured["v2"]["break_source"], "quality": o.measured["v2"]["quality_score"], "level": o.key_level,
        } for o in opps],
      })

json.dump({"oracle_commit": "a1c77584", "pip_size": PIP, "m5_end": M5_END, "cases": cases, "e2e": e2e}, open(OUT, "w"), separators=(",", ":"))
print(len(e2e), "end-to-end cases,", sum(len(c["opportunities"]) for c in e2e), "opportunities;", len(cases), "series;", sum(1 for c in cases if c["box"]), "with a box;",
      sum(1 for c in cases for s in ("BUY", "SELL") if c["box"] and c["box"]["detect"][s]["state"] == "armed"), "armed boxes;",
      sum(len(c["equal_highs"]) + len(c["equal_lows"]) for c in cases), "equal levels")
