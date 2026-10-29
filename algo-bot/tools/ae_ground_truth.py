"""Score the Analysis Engine's structure and zones against an independent labelling of the same candles.

Reproduce (repository root, Python 3.12):

    cd analysis-engine && GT_DUMP_DIR=/tmp/gt go test ./test/groundtruth -run TestDumpState
    cd ../algo-bot && python -m tools.ae_ground_truth /tmp/gt \
        --hand-marked tools/data/ground-truth-hand-marked.json \
        --out tools/data/ae-ground-truth-results.json

What the labels are. Nothing here reuses engine code or engine parameters. ``Oracle`` is a
separate, deliberately plain implementation of textbook definitions, in a different language, over the
raw closed candles of the committed captures:

* swing: the strict extreme of +-5 bars whose price then reverses by at least 1.5 ATR (Wilder ATR(14))
  within 20 bars; tiers at 1.5 and 3.0 ATR;
* fair value gap: three candles whose first high is below the third low (or the mirror), of at
  least 0.2 ATR; invalidated once a later candle closes through its far edge;
* impulse origin (the thing a supply/demand zone or order block is supposed to mark): the last
  opposite-colour candle before a run of same-colour candles that travels at least 2.0 ATR.

Plus **hand-marked** items (``--hand-marked``): structures and zones a person marked on a rendered chart
(``render`` subcommand), scored in their own section so a definition-based label never stands in for them.

What is scored, per checkpoint of the replay (the engine's full state at that bar):

* precision: of what the engine reports, how much is real by the definition above;
* recall: of what the labels say exists, how much the engine reports.

Disagreements that are a different *definition* (an engine rule the label does not share, such as
a different mitigation rule) are reported as their own class with examples, not folded into the
rates. A rate is a statement about agreement with the stated definition, not about trading value.
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CAPTURES = REPO / "analysis-engine" / "testdata"
TF_SECONDS = {"M5": 300, "M15": 900, "H1": 3600}
LIVE = {"fresh", "touched", "partially_mitigated"}

SWING_K, SWING_REVERSAL_BARS = 5, 20
SWING_TIERS = (1.5, 3.0)
FVG_MIN_ATR, FVG_RECALL_ATR, FVG_LOOKBACK = 0.2, 0.5, 150
IMPULSE_ATR, IMPULSE_MAX_BARS, IMPULSE_LOOKBACK = 2.0, 6, 200
REACTION_ATR, REACTION_BARS = 1.5, 8
EDGE_TOLERANCE_ATR = 0.1
EXAMPLES = 4


class Series:
  """Closed candles of one timeframe up to a checkpoint, with Wilder ATR(14)."""

  def __init__(self, rows: list[list[float]]):
    self.t = [int(r[0]) for r in rows]
    self.o = [r[1] for r in rows]
    self.h = [r[2] for r in rows]
    self.l = [r[3] for r in rows]
    self.c = [r[4] for r in rows]
    self.index = {t: i for i, t in enumerate(self.t)}
    self.atr = self._atr()

  def _atr(self, period: int = 14) -> list[float]:
    out = [0.0] * len(self.t)
    previous_close, value = None, None
    ranges = []
    for i in range(len(self.t)):
      high_low = self.h[i] - self.l[i]
      true_range = high_low if previous_close is None else max(high_low, abs(self.h[i] - previous_close), abs(self.l[i] - previous_close))
      previous_close = self.c[i]
      if value is None:
        ranges.append(true_range)
        if len(ranges) == period:
          value = sum(ranges) / period
          out[i] = value
        continue
      value = (value * (period - 1) + true_range) / period
      out[i] = value
    return out

  def __len__(self) -> int:
    return len(self.t)


def load_capture(name: str) -> dict:
  return json.loads((CAPTURES / name).read_text())["timeframes"]


def series_until(timeframes: dict, tf: str, last_time: int) -> Series | None:
  rows = [r for r in timeframes.get(tf, []) if r[0] <= last_time]
  return Series(rows) if len(rows) > 60 else None


# ---------------------------------------------------------------- oracle labels

def oracle_swings(s: Series) -> list[dict]:
  """Significant swings: a strict +-5 bar extreme that reverses >= 1.5 ATR within 20 bars.
  Only swings with five bars to their right are labelled (they cannot be known earlier)."""
  out = []
  for i in range(SWING_K, len(s) - SWING_K):
    if s.atr[i] <= 0:
      continue
    window = range(i - SWING_K, i + SWING_K + 1)
    if s.h[i] == max(s.h[j] for j in window) and sum(1 for j in window if s.h[j] == s.h[i]) == 1:
      drop = s.h[i] - min(s.l[j] for j in range(i + 1, min(len(s), i + 1 + SWING_REVERSAL_BARS)))
      if drop >= SWING_TIERS[0] * s.atr[i]:
        out.append({"kind": "high", "i": i, "time": s.t[i], "price": s.h[i], "reversal_atr": drop / s.atr[i]})
    if s.l[i] == min(s.l[j] for j in window) and sum(1 for j in window if s.l[j] == s.l[i]) == 1:
      rise = max(s.h[j] for j in range(i + 1, min(len(s), i + 1 + SWING_REVERSAL_BARS))) - s.l[i]
      if rise >= SWING_TIERS[0] * s.atr[i]:
        out.append({"kind": "low", "i": i, "time": s.t[i], "price": s.l[i], "reversal_atr": rise / s.atr[i]})
  return out


def oracle_gaps(s: Series) -> list[dict]:
  """Fair value gaps (>= 0.2 ATR) with their oracle liveness at the end of the series."""
  out = []
  for i in range(2, len(s)):
    atr = s.atr[i]
    if atr <= 0:
      continue
    if s.l[i] > s.h[i - 2] and s.l[i] - s.h[i - 2] >= FVG_MIN_ATR * atr:
      low, high, side = s.h[i - 2], s.l[i], "demand"
      live = all(s.c[j] >= low for j in range(i + 1, len(s)))
    elif s.h[i] < s.l[i - 2] and s.l[i - 2] - s.h[i] >= FVG_MIN_ATR * atr:
      low, high, side = s.h[i], s.l[i - 2], "supply"
      live = all(s.c[j] <= high for j in range(i + 1, len(s)))
    else:
      continue
    out.append({"i": i, "time": s.t[i], "side": side, "low": low, "high": high, "size_atr": (high - low) / atr, "live": live})
  return out


def oracle_impulses(s: Series) -> list[dict]:
  """The origin candle of every run of same-colour candles that travels >= 2 ATR, with liveness."""
  out, seen = [], set()
  for i in range(1, len(s) - 1):
    if s.atr[i] <= 0:
      continue
    bull = s.c[i] > s.o[i]
    bear = s.c[i] < s.o[i]
    if not (bull or bear):
      continue
    j = i
    while j + 1 < len(s) and j - i + 1 < IMPULSE_MAX_BARS and ((s.c[j + 1] > s.o[j + 1]) if bull else (s.c[j + 1] < s.o[j + 1])):
      j += 1
    travel = (s.c[j] - s.o[i]) if bull else (s.o[i] - s.c[j])
    if travel < IMPULSE_ATR * s.atr[i]:
      continue
    origin = next((k for k in range(i - 1, max(-1, i - 4), -1) if (s.c[k] < s.o[k] if bull else s.c[k] > s.o[k])), None)
    if origin is None or origin in seen:
      continue
    seen.add(origin)
    low, high = s.l[origin], s.h[origin]
    side = "demand" if bull else "supply"
    live = all((s.c[k] >= low) if bull else (s.c[k] <= high) for k in range(j + 1, len(s)))
    out.append({"i": origin, "time": s.t[origin], "side": side, "low": low, "high": high, "travel_atr": travel / s.atr[i], "live": live})
  return out


# ---------------------------------------------------------------- scoring

def _rate(hit: int, total: int) -> float | None:
  return None if total == 0 else round(hit / total, 3)


def score_swings(frame: dict, s: Series, tf: str, acc: dict) -> None:
  atr_of = lambda t: s.atr[s.index[t]] if t in s.index else 0.0
  engine = [x for x in frame["swings"] or [] if x["time"] in s.index]
  oracle = oracle_swings(s)
  first = min((x["time"] for x in engine), default=None)
  slot = acc[tf]
  for swing in engine:
    i = s.index[swing["time"]]
    lo, hi = max(0, i - 2), min(len(s), i + 3)
    if i < 2 or i + 2 >= len(s):
      continue
    price = s.h[i] if swing["kind"] == "high" else s.l[i]
    extreme = (swing["kind"] == "high" and s.h[i] == max(s.h[lo:hi])) or (swing["kind"] == "low" and s.l[i] == min(s.l[lo:hi]))
    slot["engine"][swing["layer"]]["n"] += 1
    slot["engine"][swing["layer"]]["extreme"] += int(extreme)
    slot["engine"][swing["layer"]]["price_matches_candle"] += int(abs(swing["price"] - price) <= 1e-9 * max(1.0, abs(price)))
    if s.atr[i] > 0 and i + SWING_REVERSAL_BARS < len(s):
      later = range(i + 1, i + 1 + SWING_REVERSAL_BARS)
      reversal = (s.h[i] - min(s.l[j] for j in later)) if swing["kind"] == "high" else (max(s.h[j] for j in later) - s.l[i])
      slot["engine"][swing["layer"]]["measured"] += 1
      slot["engine"][swing["layer"]]["reverses_1atr"] += int(reversal >= 1.0 * s.atr[i])
      slot["engine"][swing["layer"]]["reverses_3atr"] += int(reversal >= 3.0 * s.atr[i])
    if not extreme and len(slot["examples"]["not_a_local_extreme"]) < EXAMPLES:
      slot["examples"]["not_a_local_extreme"].append({"time": swing["time"], "layer": swing["layer"], "kind": swing["kind"]})
  by_kind: dict[str, list[dict]] = defaultdict(list)
  for swing in engine:
    by_kind[swing["kind"]].append(swing)
  for label in oracle:
    if first is not None and label["time"] < first:
      continue  # older than the engine's calculation window
    atr = atr_of(label["time"])
    matched = next((x for x in by_kind[label["kind"]] if abs(x["time"] - label["time"]) <= TF_SECONDS[tf]
                    and abs(x["price"] - label["price"]) <= 0.05 * atr), None)
    for tier in SWING_TIERS:
      if label["reversal_atr"] >= tier:
        slot["recall"][str(tier)]["n"] += 1
        slot["recall"][str(tier)]["hit"] += int(matched is not None)
    if matched is None and label["reversal_atr"] >= SWING_TIERS[1] and len(slot["examples"]["missed_swing_3atr"]) < EXAMPLES:
      slot["examples"]["missed_swing_3atr"].append({"time": label["time"], "kind": label["kind"], "reversal_atr": round(label["reversal_atr"], 2)})


def score_breaks(frame: dict, s: Series, tf: str, acc: dict) -> None:
  """A close-confirmed break must follow a real first close beyond one of the engine's own swings of
  that layer. The engine stamps the confirming candle, which may trail the first close beyond (a
  displacement or a second close); the lag is reported, and a break with no earlier close beyond at
  all is the hard failure."""
  slot = acc[tf]
  for brk in frame["breaks"] or []:
    if not brk["close_beyond"] or brk["time"] not in s.index:
      continue
    t = s.index[brk["time"]]
    kind = "high" if brk["direction"] == "BUY" else "low"
    lags = []
    for swing in frame["swings"] or []:
      if swing["kind"] != kind or swing["layer"] != brk["layer"] or swing["confirmed_at"] > brk["time"] or swing["time"] not in s.index:
        continue
      start = s.index[swing["time"]] + 1
      if brk["direction"] == "BUY":
        crossing = next((k for k in range(start, len(s)) if s.c[k] > swing["price"]), None)
      else:
        crossing = next((k for k in range(start, len(s)) if s.c[k] < swing["price"]), None)
      if crossing is not None and crossing <= t + 1:
        lags.append(t - crossing)
    slot["n"] += 1
    if not lags:
      slot["no_prior_close_beyond"] += 1
      if len(slot["examples"]) < EXAMPLES:
        slot["examples"].append({"time": brk["time"], "direction": brk["direction"], "layer": brk["layer"], "type": brk["type"]})
      continue
    lag = min(lags, key=lambda x: abs(x))  # the swing whose first close beyond is nearest the stamp
    slot["lags"].append(lag)


def _edges_match(zone: dict, low: float, high: float, atr: float) -> bool:
  tolerance = EDGE_TOLERANCE_ATR * atr
  return abs(zone["low"] - low) <= tolerance and abs(zone["high"] - high) <= tolerance


BREACH_MARGINS = (0.0, 0.5, 1.0, 2.0)
BREACH_RECLAIM_BARS = 6


def unreclaimed_breach(s: Series, start: int, side: str, edge: float, margin: float, atr: float) -> bool:
  """A close beyond the far edge by more than ``margin`` ATR that no close back on the zone's side
  undoes within six bars: price has left the zone and stayed away."""
  for k in range(start, len(s)):
    beyond = (s.c[k] < edge - margin * atr) if side == "demand" else (s.c[k] > edge + margin * atr)
    if not beyond:
      continue
    last = min(len(s) - 1, k + BREACH_RECLAIM_BARS)
    back = any((s.c[j] >= edge) if side == "demand" else (s.c[j] <= edge) for j in range(k + 1, last + 1))
    if not back and last - k >= 1:
      return True
  return False


def _zone_start(s: Series, zone: dict) -> int | None:
  if zone["origin_time"] not in s.index:
    return None
  return s.index[zone["origin_time"]] + 1


def score_fvg(frame: dict, s: Series, tf: str, acc: dict) -> None:
  slot = acc[tf]
  atr = s.atr[-1]
  if atr <= 0:
    return
  gaps = oracle_gaps(s)
  every_engine = [z for z in frame["zones"] or [] if z["kind"] == "fvg"]
  engine = [z for z in every_engine if z["state"] in LIVE]
  for zone in engine:
    width = (zone["high"] - zone["low"]) / atr
    slot["engine_n"] += 1
    slot["size_classes"]["<0.2" if width < 0.2 else "0.2-0.5" if width < 0.5 else ">=0.5"] += 1
    start = _zone_start(s, zone)
    if start is not None:
      edge = zone["low"] if zone["side"] == "demand" else zone["high"]
      for margin in BREACH_MARGINS:
        if unreclaimed_breach(s, start + 2, zone["side"], edge, margin, atr):
          slot["breach"][str(margin)] += 1
          if margin == 1.0 and len(slot["examples"]["live_but_price_left_by_1atr"]) < EXAMPLES:
            slot["examples"]["live_but_price_left_by_1atr"].append({"origin_time": zone["origin_time"], "side": zone["side"], "state": zone["state"], "low": zone["low"], "high": zone["high"]})
  # every engine FVG, whatever its state, must be a real three-candle gap
  for zone in every_engine:
    slot["all_n"] += 1
    real = any(g["side"] == zone["side"] and _edges_match(zone, g["low"], g["high"], atr) for g in oracle_gaps_any(s))
    slot["all_real"] += int(real)
    if not real and len(slot["examples"]["engine_zone_without_a_gap"]) < EXAMPLES:
      slot["examples"]["engine_zone_without_a_gap"].append({"origin_time": zone["origin_time"], "side": zone["side"], "low": zone["low"], "high": zone["high"]})
  floor = len(s) - FVG_LOOKBACK
  for gap in gaps:
    if not gap["live"] or gap["i"] < floor or gap["size_atr"] < FVG_RECALL_ATR:
      continue
    slot["oracle_n"] += 1
    hit = any(z["side"] == gap["side"] and _edges_match(z, gap["low"], gap["high"], atr) for z in engine)
    slot["oracle_hit"] += int(hit)
    if not hit and len(slot["examples"]["missed_live_gap"]) < EXAMPLES:
      slot["examples"]["missed_live_gap"].append({"time": gap["time"], "side": gap["side"], "low": gap["low"], "high": gap["high"], "size_atr": round(gap["size_atr"], 2)})


def oracle_gaps_any(s: Series) -> list[dict]:
  """Every three-candle gap of any size (the engine publishes zones of any size; strategies filter)."""
  out = []
  for i in range(2, len(s)):
    if s.l[i] > s.h[i - 2]:
      out.append({"side": "demand", "low": s.h[i - 2], "high": s.l[i]})
    elif s.h[i] < s.l[i - 2]:
      out.append({"side": "supply", "low": s.h[i], "high": s.l[i - 2]})
  return out


def _reaction_move(s: Series, i: int, side: str, edge_high: float, edge_low: float) -> float | None:
  after = range(i + 1, min(len(s), i + 1 + REACTION_BARS))
  if len(after) < REACTION_BARS or s.atr[i] <= 0:
    return None
  move = (max(s.h[j] for j in after) - edge_high) if side == "demand" else (edge_low - min(s.l[j] for j in after))
  return move / s.atr[i]


def score_impulses(frame: dict, s: Series, tf: str, acc: dict) -> None:
  slot = acc[tf]
  atr = s.atr[-1]
  if atr <= 0:
    return
  zones = [z for z in frame["zones"] or [] if z["kind"] in ("supply", "demand", "order_block")]
  # precision: does the engine's supply/demand zone have the reaction it exists to mark?
  for zone in (z for z in zones if z["kind"] in ("supply", "demand")):
    if zone["origin_time"] not in s.index:
      continue
    i = s.index[zone["origin_time"]]
    move = _reaction_move(s, i, zone["side"], zone["high"], zone["low"])
    if move is None:
      continue
    slot["engine_n"] += 1
    slot["engine_reacted"] += int(move >= REACTION_ATR)
    if move < REACTION_ATR and len(slot["examples"]["zone_without_reaction"]) < EXAMPLES:
      slot["examples"]["zone_without_reaction"].append({"origin_time": zone["origin_time"], "kind": zone["kind"], "move_atr": round(move, 2)})
    if zone["state"] in LIVE:
      edge = zone["low"] if zone["side"] == "demand" else zone["high"]
      slot["live_n"] += 1
      slot["live_breached_1atr"] += int(unreclaimed_breach(s, i + 2, zone["side"], edge, 1.0, atr))
  # baseline: how often does an arbitrary candle have the same reaction? A zone must beat this.
  for i in range(1, len(s) - REACTION_BARS - 1, 7):
    for side in ("demand", "supply"):
      move = _reaction_move(s, i, side, s.h[i], s.l[i])
      if move is not None:
        slot["baseline_n"] += 1
        slot["baseline_reacted"] += int(move >= REACTION_ATR)
  floor = len(s) - IMPULSE_LOOKBACK
  live_zones = [z for z in zones if z["state"] in LIVE]
  for label in oracle_impulses(s):
    if not label["live"] or label["i"] < floor:
      continue
    slot["oracle_n"] += 1
    width = label["high"] - label["low"]
    overlapping = False
    hit = False
    for zone in live_zones:
      if zone["side"] != label["side"]:
        continue
      overlap = min(zone["high"], label["high"]) - max(zone["low"], label["low"])
      if overlap > 0 and overlap >= 0.3 * min(width, zone["high"] - zone["low"] or width):
        overlapping = True
        if abs(zone["origin_time"] - label["time"]) <= 3 * TF_SECONDS[tf]:
          hit = True
          break
    slot["oracle_overlapped"] += int(overlapping)
    slot["oracle_hit"] += int(hit)
    if not hit and len(slot["examples"]["missed_live_impulse_origin"]) < EXAMPLES:
      slot["examples"]["missed_live_impulse_origin"].append({"time": label["time"], "side": label["side"], "low": label["low"], "high": label["high"],
                                                              "travel_atr": round(label["travel_atr"], 2), "overlapped_by_some_zone": overlapping})


# ---------------------------------------------------------------- hand-marked

def score_hand_marked(path: str, snapshots: dict) -> dict:
  """Items a person marked on a rendered chart: {capture, tf, as_of, swings:[{kind,time}], zones:[{side,low,high}]}.
  Scored against the engine's state at the nearest checkpoint at or after ``as_of``."""
  document = json.loads(Path(path).read_text())
  out = {"windows": 0, "swing_marks": 0, "swing_found": 0, "zone_marks": 0, "zone_found": 0, "engine_swings_in_window": 0,
         "engine_swings_marked": 0, "details": []}
  for window in document.get("windows", []):
    key = (window["capture"], window["tf"])
    snapshot = next((s for s in snapshots.get(window["capture"], []) if s["bar_time"] >= window["as_of"]), None)
    if snapshot is None or window["tf"] not in snapshot["frames"]:
      out["details"].append({"window": window.get("id"), "error": "no checkpoint at or after as_of"})
      continue
    frame = snapshot["frames"][window["tf"]]
    tf_seconds = TF_SECONDS[window["tf"]]
    out["windows"] += 1
    detail = {"window": window.get("id"), "checkpoint": snapshot["bar_time"], "swings_missed": [], "zones_missed": []}
    for mark in window.get("swings", []):
      out["swing_marks"] += 1
      found = any(x["kind"] == mark["kind"] and abs(x["time"] - mark["time"]) <= tf_seconds for x in frame["swings"] or [])
      out["swing_found"] += int(found)
      if not found:
        detail["swings_missed"].append(mark)
    marked_times = {(m["kind"], m["time"]) for m in window.get("swings", [])}
    lo, hi = window.get("from", 0), window.get("to", 1 << 62)
    for x in frame["swings"] or []:
      if x["layer"] in ("micro", "internal") or not lo <= x["time"] <= hi:
        continue
      out["engine_swings_in_window"] += 1
      out["engine_swings_marked"] += int(any(k == x["kind"] and abs(t - x["time"]) <= tf_seconds for k, t in marked_times))
    for mark in window.get("zones", []):
      out["zone_marks"] += 1
      width = mark["high"] - mark["low"]
      found = any(z["side"] == mark["side"] and z["state"] in LIVE and
                  min(z["high"], mark["high"]) - max(z["low"], mark["low"]) >= 0.3 * min(width, z["high"] - z["low"] or width)
                  for z in frame["zones"] or [])
      out["zone_found"] += int(found)
      if not found:
        detail["zones_missed"].append(mark)
    out["details"].append(detail)
  out["swing_recall"] = _rate(out["swing_found"], out["swing_marks"])
  out["zone_recall"] = _rate(out["zone_found"], out["zone_marks"])
  out["swing_precision_of_intermediate_major_in_window"] = _rate(out["engine_swings_marked"], out["engine_swings_in_window"])
  return out


# ---------------------------------------------------------------- driver

def _new_swing_slot():
  return {"engine": defaultdict(lambda: Counter()), "recall": defaultdict(lambda: Counter()), "examples": defaultdict(list)}


def _plain(node):
  if isinstance(node, defaultdict):
    node = dict(node)
  if isinstance(node, dict):
    return {k: _plain(v) for k, v in node.items()}
  return node


def run(dump_dir: str, hand_marked: str = "") -> dict:
  snapshots: dict[str, list[dict]] = defaultdict(list)
  for path in sorted(glob.glob(str(Path(dump_dir) / "*.jsonl"))):
    with open(path) as handle:
      for line in handle:
        snapshot = json.loads(line)
        snapshots[snapshot["capture"]].append(snapshot)
  swings = defaultdict(_new_swing_slot)
  breaks = defaultdict(lambda: {"n": 0, "no_prior_close_beyond": 0, "lags": [], "examples": []})
  fvg = defaultdict(lambda: {"engine_n": 0, "all_n": 0, "all_real": 0, "size_classes": Counter(), "breach": Counter(),
                             "oracle_n": 0, "oracle_hit": 0, "examples": defaultdict(list)})
  impulses = defaultdict(lambda: {"engine_n": 0, "engine_reacted": 0, "live_n": 0, "live_breached_1atr": 0, "baseline_n": 0, "baseline_reacted": 0,
                                  "oracle_n": 0, "oracle_hit": 0, "oracle_overlapped": 0, "examples": defaultdict(list)})
  count = 0
  for capture, items in snapshots.items():
    timeframes = load_capture(capture)
    for snapshot in items:
      for tf, frame in snapshot["frames"].items():
        s = series_until(timeframes, tf, frame["last_candle_time"])
        if s is None:
          continue
        count += 1
        score_swings(frame, s, tf, swings)
        score_breaks(frame, s, tf, breaks)
        if tf in ("M15", "H1"):
          score_fvg(frame, s, tf, fvg)
          score_impulses(frame, s, tf, impulses)
  report: dict = {
    "checkpoints_frames_scored": count,
    "captures": sorted(snapshots),
    "definitions": {
      "swing": f"strict +-{SWING_K}-bar extreme reversing >= {SWING_TIERS[0]} ATR within {SWING_REVERSAL_BARS} bars (tiers {SWING_TIERS})",
      "fvg": f"three-candle gap >= {FVG_MIN_ATR} ATR; recall counts live gaps >= {FVG_RECALL_ATR} ATR in the last {FVG_LOOKBACK} bars; edges within {EDGE_TOLERANCE_ATR} ATR",
      "impulse_origin": f"last opposite-colour candle before a <= {IMPULSE_MAX_BARS}-candle same-colour run of >= {IMPULSE_ATR} ATR; last {IMPULSE_LOOKBACK} bars",
      "reaction": f">= {REACTION_ATR} ATR away from a supply/demand zone within {REACTION_BARS} bars of its origin",
    },
    "swings": {}, "breaks": {}, "fvg": {}, "supply_demand": {},
  }
  for tf, slot in sorted(swings.items()):
    layers = {}
    for layer, c in sorted(slot["engine"].items()):
      layers[layer] = {"n": c["n"], "local_extreme_rate": _rate(c["extreme"], c["n"]), "price_matches_candle_rate": _rate(c["price_matches_candle"], c["n"]),
                       "reverses_1_atr_within_20_bars": _rate(c["reverses_1atr"], c["measured"]), "reverses_3_atr_within_20_bars": _rate(c["reverses_3atr"], c["measured"])}
    report["swings"][tf] = {
      "engine_by_layer": layers,
      "recall": {tier: {"oracle_n": c["n"], "recall": _rate(c["hit"], c["n"])} for tier, c in sorted(slot["recall"].items())},
      "examples": _plain(slot["examples"]),
    }
  for tf, slot in sorted(breaks.items()):
    lags = sorted(slot["lags"])
    report["breaks"][tf] = {
      "close_breaks_n": slot["n"], "no_prior_close_beyond_rate": _rate(slot["no_prior_close_beyond"], slot["n"]),
      "stamp_lag_bars": {"median": statistics.median(lags) if lags else None, "p90": lags[int(0.9 * (len(lags) - 1))] if lags else None,
                         "share_within_3_bars": _rate(sum(1 for x in lags if x <= 3), slot["n"])},
      "examples": slot["examples"], "note": "the engine keeps only the latest break per layer, so only precision is scorable"}
  for tf, slot in sorted(fvg.items()):
    live = slot["engine_n"]
    report["fvg"][tf] = {
      "engine_zones_any_state": slot["all_n"], "share_that_are_real_three_candle_gaps": _rate(slot["all_real"], slot["all_n"]),
      "engine_live_n": live,
      "live_by_size_atr": dict(slot["size_classes"]),
      "live_zones_price_has_left_unreclaimed": {f"beyond_{m}_atr": _rate(slot["breach"][m], live) for m in map(str, BREACH_MARGINS)},
      "oracle_live_gaps_n": slot["oracle_n"], "recall_of_live_gaps_ge_0.5_atr": _rate(slot["oracle_hit"], slot["oracle_n"]),
      "examples": _plain(slot["examples"]),
    }
  for tf, slot in sorted(impulses.items()):
    report["supply_demand"][tf] = {
      "engine_zones_scored": slot["engine_n"], "precision_zone_has_reaction": _rate(slot["engine_reacted"], slot["engine_n"]),
      "baseline_arbitrary_candle_has_same_reaction": _rate(slot["baseline_reacted"], slot["baseline_n"]),
      "live_zones_price_has_left_by_1_atr_unreclaimed": _rate(slot["live_breached_1atr"], slot["live_n"]),
      "oracle_origins_live": slot["oracle_n"],
      "recall_origin_zone_with_matching_origin_time": _rate(slot["oracle_hit"], slot["oracle_n"]),
      "origin_overlapped_by_any_live_zone_of_that_side": _rate(slot["oracle_overlapped"], slot["oracle_n"]),
      "examples": _plain(slot["examples"]),
    }
  if hand_marked and Path(hand_marked).exists():
    report["hand_marked"] = score_hand_marked(hand_marked, snapshots)
  return report


def render(capture: str, tf: str, end: int, bars: int, out: str) -> None:
  """Draw the last ``bars`` candles closed at or before ``end`` (the engine checkpoint), numbered
  every 5th bar, with no engine output on it, for a person to mark by eye."""
  import matplotlib
  matplotlib.use("Agg")
  import matplotlib.pyplot as plt

  rows = [r for r in load_capture(capture)[tf] if r[0] <= end][-bars:]
  fig, ax = plt.subplots(figsize=(18, 7))
  for n, (t, o, h, l, c, _) in enumerate(rows):
    color = "#2a9d8f" if c >= o else "#e63946"
    ax.plot([n, n], [l, h], color=color, linewidth=1)
    ax.add_patch(plt.Rectangle((n - 0.3, min(o, c)), 0.6, abs(c - o) or 1e-9, color=color))
  ax.set_xticks(range(0, len(rows), 5))
  ax.set_xticklabels([f"{n}\n{__import__('datetime').datetime.fromtimestamp(rows[n][0], __import__('datetime').UTC):%d %H:%M}" for n in range(0, len(rows), 5)], fontsize=7)
  ax.grid(alpha=0.25)
  ax.set_title(f"{capture} {tf} to {end} ({len(rows)} bars)")
  fig.tight_layout()
  fig.savefig(out, dpi=90)
  print(json.dumps({"first_time": rows[0][0], "last_time": rows[-1][0], "times": [r[0] for r in rows]}))


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("dump_dir", nargs="?")
  parser.add_argument("--hand-marked", default="")
  parser.add_argument("--out", default="")
  parser.add_argument("--render", nargs=5, metavar=("CAPTURE", "TF", "END", "BARS", "PNG"))
  args = parser.parse_args()
  if args.render:
    capture, tf, end, bars, png = args.render
    render(capture, tf, int(end), int(bars), png)
    return
  report = run(args.dump_dir, args.hand_marked)
  text = json.dumps(report, indent=1, sort_keys=True)
  if args.out:
    Path(args.out).write_text(text + "\n")
  print(text[:6000])


if __name__ == "__main__":
  main()
