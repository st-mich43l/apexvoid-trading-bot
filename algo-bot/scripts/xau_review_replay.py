"""Read-only XAU trade-quality measurement (PR-X0): three tables, three pre-registered decision rules.

It answers, from data that already exists, three questions about XAU Auto Algo zone trades (supply, demand, order block, FVG) and prints which branch of the
owner's rule (fixed on 23 Nov 2026, before any data was seen) applies. It chooses no threshold, tunes nothing and adds no variant.

  M1  Does the candle reaction separate outcomes?  touch-only vs confirmed, and terciles of the raw factor score      -> D3 (PR-X2)
  M2  Would a deeper or spread ladder have paid?    V0 (today), Va (deepest feasible), Vb (spread), Vc (inverted)      -> D4 (PR-X3)
  M3  What does the break-even runner block?        same_direction_group_limit refusals while every blocker was risk-free -> D5 (PR-X4)

Read-only: Postgres is only read from and the session is READ ONLY; Redis is only read from (ZRANGE, XRANGE, SCAN, GET). It writes files to --out and nothing else.
``--selftest`` includes a guard that scans this file's own source for any write verb.

Run inside the algo-bot container (it reads the bot's own runtime config for the Postgres and Redis URLs):

  docker compose cp algo-bot/scripts/xau_review_replay.py algo-bot:/tmp/
  docker compose exec algo-bot python /tmp/xau_review_replay.py --out /var/log/apexvoid/xau_review

Simulation (M2) follows kl_replay.py and the forward-outcome model: bars are the BID series; the ask is bid + the minute's mean spread (market_quote_minutes) or 3 pips
when there is none. A BUY enters on the ask and exits on the bid; a SELL enters on the bid and exits on the ask (the side physical trading requires, and the more
conservative one for a BUY stop than reading the stop on the ask). A bar that touches both a stop and a target is a stop; the bar that fills a leg cannot take a
target for it (its stop still applies). Targets are 1R..4R of the variant's own L1 to the plan's stop, closing 40/20/20/20 of the filled volume; at TP1 the stop moves
to L1 +/- 6 ticks and unfilled legs are cancelled (a leg touched in the TP1 bar counts as filled: the order inside a bar is unknown). The risk leg is ignored.
Every variant is scored in units of V0's planned risk (V0's L1 to the stop), total volume 1.0.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

# The script is copied into the container one file at a time and run as ``python /tmp/xau_review_replay.py`` with PYTHONPATH=/app; in CI it runs from algo-bot.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PIP = 0.1  # one XAU pip in price units
TICK = 0.01
DIGITS = 2
DEFAULT_SPREAD_PIPS = 3.0
BE_BUFFER_TICKS = 6  # execution.stops.be_buffer_ticks
HORIZON_SECONDS = 24 * 3600
TARGET_R = (1.0, 2.0, 3.0, 4.0)
TARGET_CLOSE = (0.4, 0.2, 0.2, 0.2)
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900}
STRATEGY_IDS = ("supply", "demand", "order_block", "fvg")
TOUCH_CODE = "candle_confirmation_optional"
REFUSAL_REASON = "same_direction_group_limit"
DEFAULT_LABEL_SET = "v1:proximal_limit:max"  # outcome_model.Assumptions().label_set, the labeller's default

# --- the owner's pre-registered thresholds (fixed 23 Nov 2026) ---------------------------------------------------------------------------------------------------
M1_MIN_GAP_R = 0.20
M1_MIN_N_PER_HALF = 20
M1_MAX_MISSING_SHARE = 0.20
M2_FIDELITY_MIN = 0.75
M3_MIN_N = 15

FORBIDDEN_TOKENS = (
  "INS" + "ERT", "UPD" + "ATE", "DEL" + "ETE", "UPS" + "ERT", "TRUN" + "CATE",
  "." + "set(", "." + "xadd(", "." + "hset(", "." + "zadd(", "." + "delete(", "upsert" + "_label",
)


# =====================================================================================================================================================================
# Common helpers


def halves_midpoint(since: int, until: int) -> float:
  return (since + until) / 2.0


def half_of(ts: float, since: int, until: int) -> int:
  """0 for the first half of [since, until], 1 for the second (split at the time midpoint)."""
  return 0 if ts < halves_midpoint(since, until) else 1


def _mean(values: list[float]) -> float | None:
  return sum(values) / len(values) if values else None


def _fmt(x, nd: int = 3) -> str:
  if x is None or (isinstance(x, float) and math.isnan(x)):
    return "n/a"
  return f"{x:.{nd}f}"


def _sign(x: float) -> int:
  return 0 if abs(x) < 1e-9 else (1 if x > 0 else -1)


def iso(ts: float | int | None) -> str:
  return "n/a" if ts is None else datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d %H:%M")


def md_table(headers: list[str], rows: list[list]) -> list[str]:
  out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
  for row in rows:
    out.append("| " + " | ".join("" if c is None else str(c) for c in row) + " |")
  return out


def write_csv(path: str, headers: list[str], rows: list[list]) -> None:
  with open(path, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(headers)
    writer.writerows(rows)


def scan_source(text: str) -> list[str]:
  """Write verbs found in ``text`` (the read-only guard: ``--selftest`` runs it over this very file)."""
  return [token for token in FORBIDDEN_TOKENS if token in text]


# =====================================================================================================================================================================
# M1: does the reaction separate outcomes?


def is_stop_exit(exit_path) -> bool:
  return "stop" in str(exit_path or "").lower()


def cohort_stats(rows: list[dict]) -> dict:
  """n, net R, expectancy, win rate, and the stop-before-1R rate (known mfe only; the rest is reported as unknown)."""
  n = len(rows)
  nets = [r["net_r"] for r in rows]
  known = [r for r in rows if r.get("mfe_r") is not None]
  stops = [r for r in known if r["mfe_r"] < 1.0 and is_stop_exit(r.get("exit_path"))]
  return {
    "n": n, "net_r": sum(nets) if n else 0.0, "exp": _mean(nets), "win": (sum(1 for x in nets if x > 0) / n) if n else None,
    "stop_before_1r": len(stops), "stop_rate": (len(stops) / len(known)) if known else None, "mfe_unknown": n - len(known),
  }


def split_halves(rows: list[dict], since: int, until: int) -> tuple[list[dict], list[dict]]:
  first = [r for r in rows if half_of(r["closed_at"], since, until) == 0]
  second = [r for r in rows if half_of(r["closed_at"], since, until) == 1]
  return first, second


def tercile_cuts(rows: list[dict]) -> tuple[float, float] | None:
  scores = sorted(r["raw_score"] for r in rows if r.get("raw_score") is not None)
  if len(scores) < 3:
    return None
  return scores[len(scores) // 3], scores[(2 * len(scores)) // 3]


def tercile_of(score: float, cuts: tuple[float, float]) -> int:
  return 1 if score < cuts[0] else (2 if score < cuts[1] else 3)


def m1_tables(rows: list[dict], since: int, until: int) -> tuple[list[list], list[list], tuple[float, float] | None]:
  """(M1a rows, M1b rows, the tercile cut points)."""
  m1a = []
  scopes = [("ALL", rows)] + [(s, [r for r in rows if r["strategy_id"] == s]) for s in STRATEGY_IDS]
  for scope, subset in scopes:
    for cohort in ("touch_only", "confirmed", "unknown"):
      mine = [r for r in subset if (r["touch_only"] is None) == (cohort == "unknown") and (cohort == "unknown" or r["touch_only"] == (cohort == "touch_only"))]
      first, second = split_halves(mine, since, until)
      for label, part in (("window", mine), ("half1", first), ("half2", second)):
        s = cohort_stats(part)
        m1a.append([scope, cohort, label, s["n"], _fmt(s["net_r"], 2), _fmt(s["exp"]), _fmt(s["win"], 2), s["stop_before_1r"], _fmt(s["stop_rate"], 2), s["mfe_unknown"]])
  cuts = tercile_cuts(rows)
  m1b = []
  if cuts is not None:
    scored = [r for r in rows if r.get("raw_score") is not None]
    for tercile in (1, 2, 3):
      mine = [r for r in scored if tercile_of(r["raw_score"], cuts) == tercile]
      first, second = split_halves(mine, since, until)
      m1b.append([f"T{tercile}", len(mine), _fmt(_mean([r["net_r"] for r in mine])), len(first), _fmt(_mean([r["net_r"] for r in first])), len(second), _fmt(_mean([r["net_r"] for r in second]))])
  return m1a, m1b, cuts


def project_date(n: int, since: int, until: int, per_half: int) -> str:
  """Date at which a cohort of ``n`` trades over [since, until] would hold ``per_half`` in each half at the window's own rate (2 x per_half in total)."""
  days = (until - since) / 86400.0
  if n <= 0 or days <= 0:
    return "never at this rate (no trades)"
  return iso(since + (2 * per_half) / (n / days) * 86400.0)[:10]


def m1_decide(rows: list[dict], since: int, until: int) -> tuple[str, str]:
  """(branch, reason). Pooled over the four strategies; branches A, B, C or INSUFFICIENT DATA, applied exactly as pre-registered."""
  n = len(rows)
  if n == 0:
    return "INSUFFICIENT DATA", "no XAU zone trades in the window"
  no_evidence = sum(1 for r in rows if r["touch_only"] is None)
  no_score = sum(1 for r in rows if r.get("raw_score") is None)
  if no_evidence / n > M1_MAX_MISSING_SHARE or no_score / n > M1_MAX_MISSING_SHARE:
    return "INSUFFICIENT DATA", (
      f"evidence code missing on {no_evidence}/{n} trades, raw factor score missing on {no_score}/{n} (limit {M1_MAX_MISSING_SHARE:.0%} of the cohort)"
    )
  first, second = split_halves(rows, since, until)
  per_half = []
  for part in (first, second):
    touch = [r for r in part if r["touch_only"] is True]
    conf = [r for r in part if r["touch_only"] is False]
    per_half.append((len(touch), len(conf), _mean([r["net_r"] for r in touch]), _mean([r["net_r"] for r in conf])))
  a_ok = all(nt >= M1_MIN_N_PER_HALF and nc >= M1_MIN_N_PER_HALF and et is not None and ec is not None and ec - et >= M1_MIN_GAP_R - 1e-12 for nt, nc, et, ec in per_half)
  gaps = ", ".join(
    f"half{i + 1}: touch n {nt} exp {_fmt(et)} vs confirmed n {nc} exp {_fmt(ec)}" for i, (nt, nc, et, ec) in enumerate(per_half)
  )
  if a_ok:
    return "A", f"touch-only is lower than confirmed by >= {M1_MIN_GAP_R} R with n >= {M1_MIN_N_PER_HALF} per cohort in both halves ({gaps})"
  cuts = tercile_cuts(rows)
  b_ok = False
  tercile_text = "terciles unavailable"
  if cuts is not None:
    exps = []
    for part in (first, second):
      line = []
      for tercile in (1, 2, 3):
        mine = [r["net_r"] for r in part if r.get("raw_score") is not None and tercile_of(r["raw_score"], cuts) == tercile]
        line.append(_mean(mine))
      exps.append(line)
    b_ok = all(all(x is not None for x in line) and line[0] < line[1] < line[2] for line in exps)
    tercile_text = "; ".join(f"half{i + 1} T1/T2/T3 exp " + "/".join(_fmt(x) for x in line) for i, line in enumerate(exps))
  if b_ok:
    return "B", f"(A) does not hold ({gaps}) and expectancy rises strictly T1 < T2 < T3 in both halves ({tercile_text})"
  touch_n = sum(1 for r in rows if r["touch_only"] is True)
  conf_n = sum(1 for r in rows if r["touch_only"] is False)
  dates = f"touch-only n {touch_n} reaches 20 per half about {project_date(touch_n, since, until, M1_MIN_N_PER_HALF)}, confirmed n {conf_n} about {project_date(conf_n, since, until, M1_MIN_N_PER_HALF)}"
  return "C", f"neither (A) ({gaps}) nor (B) ({tercile_text}); {dates}"


# =====================================================================================================================================================================
# M2: bars, simulation, variants


@dataclass
class Bars:
  tf: str
  source: str
  t: list[int] = field(default_factory=list)
  o: list[float] = field(default_factory=list)
  h: list[float] = field(default_factory=list)
  l: list[float] = field(default_factory=list)
  c: list[float] = field(default_factory=list)

  def first_at_or_after(self, ts: float) -> int:
    lo, hi = 0, len(self.t)
    while lo < hi:
      mid = (lo + hi) // 2
      if self.t[mid] < ts:
        lo = mid + 1
      else:
        hi = mid
    return lo

  def covers(self, start_ts: float, end_ts: float) -> bool:
    """True when the bars start at or before ``start_ts`` (to within one bar) and run to ``end_ts``."""
    if not self.t:
      return False
    step = TF_SECONDS.get(self.tf, 60)
    # The end tolerates five bars: a trade still inside its 24 h window runs to the end of the data (flagged bars_end), it is not skipped for being recent.
    return self.t[0] <= start_ts + step and self.t[-1] + 5 * step >= end_ts

  def span(self) -> str:
    return f"{iso(self.t[0])} -> {iso(self.t[-1])}" if self.t else "empty"


def make_bars(tf: str, source: str, rows: list[tuple]) -> Bars:
  bars = Bars(tf, source)
  for ts, o, h, l, c in sorted(rows):
    bars.t.append(int(ts)); bars.o.append(float(o)); bars.h.append(float(h)); bars.l.append(float(l)); bars.c.append(float(c))
  return bars


def spread_price(spread_fn, ts: int) -> float:
  """The bid-to-ask spread (price units) of the minute containing ``ts``."""
  value = spread_fn(ts - ts % 60) if spread_fn else None
  return DEFAULT_SPREAD_PIPS * PIP if value is None else float(value)


def simulate_ladder(
  bars: Bars, *, buy: bool, legs: list[tuple[float, float]], stop: float, start_ts: int, fill_end_ts: int, risk_unit_pips: float, spread_fn=None,
  horizon_seconds: int = HORIZON_SECONDS,
) -> dict:
  """One variant on one trade: (result in V0-risk units, fills per leg, missed, flags, outcome). See the module docstring for the rules."""
  sign = 1.0 if buy else -1.0
  if not legs or not (risk_unit_pips > 0):
    return {"r": None, "fills": [], "missed": False, "flags": ["bad_geometry"], "outcome": "bad_geometry"}
  l1 = legs[0][0]
  own_risk = abs(l1 - stop)
  if not own_risk > 0 or (buy and stop >= l1) or (not buy and stop <= l1):
    return {"r": None, "fills": [False] * len(legs), "missed": False, "flags": ["bad_geometry"], "outcome": "bad_geometry"}
  targets = [l1 + sign * k * own_risk for k in TARGET_R]
  filled_bar: list[int | None] = [None] * len(legs)
  stop_live = stop
  pending_stop: float | None = None
  cancelled = False
  booked = 0
  base_volume = 0.0
  realized = 0.0  # pips x volume
  closed_volume = 0.0
  first_fill_ts: int | None = None
  flags: list[str] = []
  outcome = "no_fill"
  last_close = None
  i = bars.first_at_or_after(start_ts)
  while i < len(bars.t):
    ts = bars.t[i]
    spread = spread_price(spread_fn, ts)
    hi, lo = bars.h[i], bars.l[i]
    if first_fill_ts is not None and ts - first_fill_ts >= horizon_seconds:
      flags.append("horizon_24h")
      last_close = bars.c[i] + (0.0 if buy else spread)  # closed at the close of the bar that reaches 24 h
      break
    if first_fill_ts is None and ts >= fill_end_ts:
      break
    if not cancelled and ts < fill_end_ts:
      for j, (price, _ratio) in enumerate(legs):
        if filled_bar[j] is None and ((lo + spread <= price) if buy else (hi >= price)):
          filled_bar[j] = i
          if first_fill_ts is None:
            first_fill_ts = ts
    filled = [j for j in range(len(legs)) if filled_bar[j] is not None]
    if not filled:
      i += 1
      continue
    volume = sum(legs[j][1] for j in filled)
    vwap = sum(legs[j][0] * legs[j][1] for j in filled) / volume
    remaining = volume - closed_volume if booked == 0 else base_volume - closed_volume
    if buy:
      stop_hit = lo <= stop_live
    else:
      stop_hit = hi + spread >= stop_live
    if stop_hit:  # a bar that touches a stop and a target is a stop
      realized += remaining * sign * (stop_live - vwap) / PIP
      closed_volume += remaining
      outcome = "stop" if booked == 0 else f"tp{booked}_then_stop"
      last_close = None
      break
    if any(filled_bar[j] is not None and filled_bar[j] < i for j in range(len(legs))):  # the bar that filled a leg cannot take a target for it
      while booked < len(targets):
        target = targets[booked]
        hit = (hi >= target) if buy else (lo + spread <= target)
        if not hit:
          break
        if booked == 0:
          base_volume = volume  # unfilled legs are cancelled here
          cancelled = True
          pending_stop = l1 + sign * BE_BUFFER_TICKS * TICK
        close_volume = min(TARGET_CLOSE[booked] * base_volume, base_volume - closed_volume)
        realized += close_volume * sign * (target - vwap) / PIP
        closed_volume += close_volume
        booked += 1
        if base_volume - closed_volume <= 1e-9:
          break
      if booked and base_volume - closed_volume <= 1e-9:
        outcome = f"tp{booked}_full"
        last_close = None
        break
    if pending_stop is not None:
      stop_live, pending_stop = pending_stop, None
    last_close = bars.c[i] + (0.0 if buy else spread)
    i += 1
  fills = [filled_bar[j] is not None for j in range(len(legs))]
  if first_fill_ts is None:
    return {"r": 0.0, "fills": fills, "missed": True, "flags": flags, "outcome": "missed"}
  volume = sum(legs[j][1] for j in range(len(legs)) if filled_bar[j] is not None)
  vwap = sum(legs[j][0] * legs[j][1] for j in range(len(legs)) if filled_bar[j] is not None) / volume
  total = volume if booked == 0 else base_volume
  still_open = total - closed_volume
  if still_open > 1e-9:
    if i >= len(bars.t) and "horizon_24h" not in flags:
      flags.append("bars_end")
    if last_close is None:
      j = min(i, len(bars.t) - 1)
      last_close = bars.c[j] + (0.0 if buy else spread_price(spread_fn, bars.t[j]))
    realized += still_open * sign * (last_close - vwap) / PIP
    outcome = "open_at_horizon" if "horizon_24h" in flags else "open_at_bars_end"
  return {"r": realized / risk_unit_pips, "fills": fills, "missed": False, "flags": flags, "outcome": outcome}


def _ladder(direction: str, start: float, far: float, spacing_pips: float, spread_across: bool) -> list[tuple[float, float]]:
  from app.autotrade.xau_ladder import LadderSettings, price_ladder

  settings = LadderSettings(min_spacing_pips=Decimal(str(round(spacing_pips, 6))), spread_across_zone=spread_across)
  prices, ratios = price_ladder(direction, start, far, digits=DIGITS, pip_size=PIP, settings=settings, strict=False)
  return list(zip((float(p) for p in prices), (float(r) for r in ratios)))


def build_variants(
  *, direction: str, strategy_id: str, plan_legs: list[tuple[float, float]], zone: tuple[float, float] | None, spacing_price: float, entry_plan: dict | None,
) -> dict[str, dict]:
  """name -> {"legs": [(price, ratio)], "applicable": bool, "note": str}. V0 is the plan as published; the others are built from it and from the opportunity."""
  buy = direction.upper() == "BUY"
  spacing_pips = spacing_price / PIP if spacing_price > 0 else 10.0
  out: dict[str, dict] = {"V0": {"legs": list(plan_legs), "applicable": True, "note": "the plan as published"}}
  # Va: the deepest feasible candidate of the SD-5 entry plan (supply and demand only).
  if strategy_id not in ("supply", "demand"):
    out["Va"] = {"legs": [], "applicable": False, "note": "not applicable (no entry plan)"}
  else:
    candidates = [c for c in (entry_plan or {}).get("candidates", []) if c.get("feasible")]
    deepest = max(candidates, key=lambda c: float(c.get("depth_fraction") or 0.0)) if candidates else None
    selected_price = (entry_plan or {}).get("price")
    if deepest is None or zone is None or selected_price is None or abs(float(deepest["price"]) - float(selected_price)) < 1e-9 or float(deepest["price"]) == plan_legs[0][0]:
      out["Va"] = {"legs": list(plan_legs), "applicable": True, "note": "no deeper feasible candidate: equals V0"}
    else:
      low, high = zone
      start = min(high, max(low, float(deepest["price"])))
      far = low if buy else high
      out["Va"] = {"legs": _ladder(direction, start, far, spacing_pips, False), "applicable": True, "note": f"deepest feasible candidate {deepest.get('kind')} at {deepest['price']}"}
  # Vb: the same band, legs spread across the zone.
  if zone is None:
    out["Vb"] = {"legs": [], "applicable": False, "note": "no published zone"}
  else:
    low, high = zone
    start = min(high, max(low, plan_legs[0][0]))
    far = low if buy else high
    out["Vb"] = {"legs": _ladder(direction, start, far, spacing_pips, True), "applicable": True, "note": "spread_across_zone=True"}
  # Vc: V0's prices with the ratios reversed (60/20/20 -> 20/20/60, 60/40 -> 40/60).
  ratios = [r for _p, r in plan_legs][::-1]
  out["Vc"] = {"legs": [(p, r) for (p, _), r in zip(plan_legs, ratios)], "applicable": True, "note": "ratios reversed"}
  return out


def m2_aggregate(trades: list[dict], since: int, until: int) -> tuple[list[list], dict]:
  """Table M2 rows and the per-variant sums (whole window and per half, against V0 on the same trades) the decision reads."""
  rows = []
  sums: dict[str, dict] = {}
  for name in ("V0", "Va", "Vb", "Vc"):
    used = [t for t in trades if t["variants"].get(name, {}).get("applicable") and t["variants"][name].get("r") is not None]
    entry = {"n": len(used)}
    for label, part in (("window", used), ("half1", [t for t in used if half_of(t["closed_at"], since, until) == 0]), ("half2", [t for t in used if half_of(t["closed_at"], since, until) == 1])):
      vals = [t["variants"][name]["r"] for t in part]
      v0 = [t["variants"]["V0"]["r"] for t in part]
      entry[label] = {"n": len(part), "net": sum(vals), "mean": _mean(vals), "v0_net": sum(v0)}
    legs = [sum(1 for t in used if len(t["variants"][name]["fills"]) > k and t["variants"][name]["fills"][k]) for k in range(3)]
    missed = sum(1 for t in used if t["variants"][name]["missed"])
    sums[name] = entry
    for label in ("window", "half1", "half2"):
      e = entry[label]
      rows.append([name, label, e["n"], legs[0] if label == "window" else "", legs[1] if label == "window" else "", legs[2] if label == "window" else "",
                   missed if label == "window" else "", _fmt(e["net"], 2), _fmt(e["mean"]), _fmt(e["v0_net"], 2) if name != "V0" else ""])
  return rows, sums


def m2_decide(trades: list[dict], sums: dict, since: int, until: int) -> tuple[str, str]:
  """(D4 value, reason): the fidelity gate, then the variant with the highest whole-window net among those that beat V0 in both halves."""
  replayed = [t for t in trades if t["variants"]["V0"].get("r") is not None]
  if not replayed:
    return "FIDELITY FAILED", "no trade could be replayed (no covering bars), so the fidelity gate cannot pass"
  same = sum(1 for t in replayed if _sign(t["variants"]["V0"]["r"]) == _sign(t["net_r"]))
  share = same / len(replayed)
  if share < M2_FIDELITY_MIN - 1e-12:
    return "FIDELITY FAILED", f"V0 has the same sign as the actual net R on {same}/{len(replayed)} = {share:.1%} of replayed trades (gate {M2_FIDELITY_MIN:.0%})"
  winners = []
  notes = []
  for name in ("Va", "Vb", "Vc"):
    s = sums[name]
    beats = all(s[h]["n"] > 0 and s[h]["net"] > s[h]["v0_net"] + 1e-12 for h in ("half1", "half2"))
    notes.append(f"{name} window {_fmt(s['window']['net'], 2)} (V0 same trades {_fmt(s['window']['v0_net'], 2)}), half1 {_fmt(s['half1']['net'], 2)} vs {_fmt(s['half1']['v0_net'], 2)}, half2 {_fmt(s['half2']['net'], 2)} vs {_fmt(s['half2']['v0_net'], 2)}, beats V0 in both halves: {'yes' if beats else 'no'}")
    if beats:
      winners.append((s["window"]["net"], name))
  if not winners:
    return "NONE", f"fidelity {same}/{len(replayed)} = {share:.1%} passes; no variant beats V0 in both halves ({'; '.join(notes)})"
  best = max(winners)[1]
  return best, f"fidelity {same}/{len(replayed)} = {share:.1%} passes; {best} has the highest whole-window net R_v0 among those that beat V0 in both halves ({'; '.join(notes)})"


# =====================================================================================================================================================================
# M3: what does the break-even runner block?


def classify_refusal(refusal: dict, tp1_ts: dict, be_known: dict) -> dict:
  """Risk-free blocker test. ``tp1_ts``: plan id -> earliest TP1 booking time; ``be_known``: plan id -> True/False once the stop at or past entry is established.

  Risk-free only when EVERY blocking group had booked TP1 before the refusal and its stop was at or past entry. If any fact cannot be established the refusal is
  ``undetermined`` (counted and listed, never dropped); a blocker whose TP1 is known to be later than the refusal makes it ``not_risk_free``.
  """
  blockers = refusal.get("blockers") or []
  if not blockers:
    return {"status": "undetermined", "why": "no blocking group ids on the refusal"}
  why = []
  not_free = False
  unknown = False
  for plan_id in blockers:
    t = tp1_ts.get(plan_id)
    if t is None:
      unknown = True
      why.append(f"{plan_id[:18]}: TP1 time unknown")
    elif t > refusal["checked_at"]:
      not_free = True
      why.append(f"{plan_id[:18]}: TP1 after the refusal")
    elif be_known.get(plan_id) is not True:
      if be_known.get(plan_id) is False:
        not_free = True
        why.append(f"{plan_id[:18]}: stop still behind entry")
      else:
        unknown = True
        why.append(f"{plan_id[:18]}: stop position unknown")
  if not_free:
    return {"status": "not_risk_free", "why": "; ".join(why)}
  if unknown:
    return {"status": "undetermined", "why": "; ".join(why)}
  return {"status": "risk_free", "why": "every blocker had booked TP1 with its stop at or past entry"}


def label_net_r(outcome: str | None, r_low, r_high, r_mark) -> float | None:
  """Net R of a forward label: tp / sl and the open marks count; ambiguous, unfilled and no-data labels do not."""
  if outcome in ("tp", "sl") and r_low is not None:
    return float(r_low)
  if outcome in ("open_timeout", "open_data_end") and r_mark is not None:
    return float(r_mark)
  return None


def m3_decide(refusals: list[dict], since: int, until: int) -> tuple[str, str]:
  """(D5 value, reason) over the labelled, risk-free refusals."""
  labelled = [r for r in refusals if r["status"] == "risk_free" and r.get("net_r") is not None]
  n = len(labelled)
  net = sum(r["net_r"] for r in labelled)
  halves = [[r for r in labelled if half_of(r["checked_at"], since, until) == h] for h in (0, 1)]
  nets = [sum(r["net_r"] for r in part) for part in halves]
  text = f"{n} labelled risk-free refusals, net R {_fmt(net, 2)} (half1 n {len(halves[0])} {_fmt(nets[0], 2)}, half2 n {len(halves[1])} {_fmt(nets[1], 2)})"
  if n >= M3_MIN_N and net > 0 and all(len(part) > 0 and x > 0 for part, x in zip(halves, nets)):
    return "false", text + "; net R > 0 with n >= 15 and positive in both halves"
  if n < M3_MIN_N:
    return "n<15 ship anyway", text + "; n < 15, ship anyway (aligns Python with the executor, which already ignores a TP1-booked plan)"
  if all(len(part) > 0 and x < 0 for part, x in zip(halves, nets)) and net < 0:
    return "keep true", text + "; net R negative in both halves"
  return "keep true, re-measure", text + "; neither clearly positive nor negative"


# =====================================================================================================================================================================
# Data access (read-only)


def _secs(v) -> int | None:
  if v is None:
    return None
  v = int(v)
  return v // 1000 if v > 10_000_000_000 else v


def _json(v):
  if v is None or isinstance(v, (dict, list)):
    return v
  try:
    return json.loads(v)
  except (TypeError, ValueError):
    return None


def evidence_codes(payload: dict | None) -> set[str] | None:
  if not isinstance(payload, dict) or not isinstance(payload.get("evidence"), list):
    return None
  return {str(e.get("code")) for e in payload["evidence"] if isinstance(e, dict) and e.get("code")}


def raw_factor_score(payload: dict | None):
  try:
    value = payload["technical_context"]["confluence"]["raw_factor_score"]
  except (KeyError, TypeError):
    return None
  return None if value is None else float(value)


COHORT_SQL = """
SELECT r.group_id, r.setup_type, r.direction, r.net_r, r.exit_path, r.closed_at, r.mfe_r, r.mae_r, r.risk_pips,
       s.strategy AS snapshot_strategy, s.admitted_at, s.opportunity_id, s.plan,
       o.creation_envelope, o.terminal_envelope, o.expires_at AS opp_expires_at, o.terminal_at AS opp_terminal_at
FROM auto_trade_results r
JOIN auto_trade_plan_snapshots s ON s.plan_id = r.group_id
LEFT JOIN analysis_opportunities o ON o.opportunity_id = s.opportunity_id
WHERE r.trade_stream = 'algo_auto'
  AND r.symbol = 'XAU'
  AND r.net_r IS NOT NULL
  AND r.closed_at >= $1 AND r.closed_at < $2
ORDER BY r.closed_at
"""


async def table_exists(pg, name: str) -> bool:
  return bool(await pg.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{name}"))


async def load_redis_bars(r, tf: str) -> Bars:
  from app.marketdata.ohlc import _normalize_price

  rows = await r.zrange(f"bars:XAU:{tf}", 0, -1, withscores=True)
  out = []
  for member, score in rows:
    data = json.loads(member.decode() if isinstance(member, bytes) else member)
    ts = int(float(data.get("t", score)))
    if ts > 10_000_000_000:
      ts //= 1000
    out.append((ts, *(_normalize_price("XAU", float(data[k])) for k in ("o", "h", "l", "c"))))
  return make_bars(tf, f"redis:{tf}", out)


async def load_archive_bars(pg, since: int, until: int) -> Bars | None:
  if not await table_exists(pg, "market_bars"):
    return None
  rows = await pg.fetch(
    "SELECT open_ts, open, high, low, close FROM market_bars WHERE symbol = 'XAU' AND timeframe = 'M1' AND open_ts >= $1 AND open_ts < $2 ORDER BY open_ts",
    since - 86400, until + 2 * 86400,
  )
  return make_bars("M1", "market_bars:M1", [tuple(r) for r in rows]) if rows else None


async def load_spreads(pg, since: int, until: int) -> dict[int, float]:
  if not await table_exists(pg, "market_quote_minutes"):
    return {}
  rows = await pg.fetch("SELECT minute_ts, spread_mean FROM market_quote_minutes WHERE symbol = 'XAU' AND minute_ts >= $1 AND minute_ts < $2", since - 86400, until + 2 * 86400)
  return {int(r["minute_ts"]): float(r["spread_mean"]) for r in rows}


def save_bars(bars: Bars, out_dir: str) -> str:
  path = os.path.join(out_dir, f"bars_XAU_{bars.tf}_{bars.source.split(':')[0]}.csv")
  write_csv(path, ["open_time_utc", "t", "open", "high", "low", "close"], [[iso(bars.t[i]), bars.t[i], bars.o[i], bars.h[i], bars.l[i], bars.c[i]] for i in range(len(bars.t))])
  return path


def pick_bars(sources: list[Bars], start_ts: int, end_ts: int) -> Bars | None:
  """The first source that covers the trade from admission to 24 h after its first fill."""
  for bars in sources:
    if bars.covers(start_ts, end_ts):
      return bars
  return None


def route_refusals_from_records(records: list[dict], since: int, until: int) -> list[dict]:
  """Route outcomes (the stream and the per-match keys) that refused for the same-direction limit, de-duplicated by match id plus reason (earliest time kept)."""
  found: dict[tuple[str, str], dict] = {}
  for rec in records:
    if str(rec.get("symbol", "")).upper() != "XAU" or rec.get("reason_code") != REFUSAL_REASON:
      continue
    checked = int(rec.get("checked_at") or rec.get("detected_at") or 0)
    if not (since <= checked < until):
      continue
    key = (str(rec.get("match_id")), REFUSAL_REASON)
    measured = rec.get("measured") or {}
    entry = {"match_id": key[0], "checked_at": checked, "blockers": list(measured.get("existing_plan_ids") or []), "strategy": rec.get("strategy"), "direction": rec.get("direction"),
             "source": rec.get("_source", "")}
    if key not in found or checked < found[key]["checked_at"]:
      if key in found and not entry["blockers"]:
        entry["blockers"] = found[key]["blockers"]
      found[key] = entry
    elif not found[key]["blockers"] and entry["blockers"]:
      found[key]["blockers"] = entry["blockers"]
  return sorted(found.values(), key=lambda r: r["checked_at"])


LOG_REFUSAL = re.compile(
  r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[INFO\] app\.autotrade\.worker: v8 group exposure refused symbol=XAU match_id=(\S+) reason=" + REFUSAL_REASON + r" XAU (BUY|SELL)"
)


def parse_log_refusals(lines, since: int, until: int) -> list[dict]:
  """Refusals named by the algo-bot file logs (UTC). The line carries a truncated match id and no blocking group ids: they are always ``undetermined``."""
  found = []
  for line in lines:
    m = LOG_REFUSAL.match(line)
    if not m:
      continue
    ts = int(datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    if since <= ts < until:
      found.append({"match_id": m.group(2), "checked_at": ts, "blockers": [], "strategy": None, "direction": m.group(3), "source": "algo_bot_log", "truncated_id": True})
  return found


def merge_refusals(primary: list[dict], from_logs: list[dict]) -> list[dict]:
  """The union: a log line is dropped when a route outcome already names it (its truncated id is a prefix of the full match id, within two minutes)."""
  merged = list(primary)
  for entry in from_logs:
    same = any(p["match_id"].startswith(entry["match_id"]) and abs(p["checked_at"] - entry["checked_at"]) <= 120 for p in primary)
    if not same:
      merged.append(entry)
  return sorted(merged, key=lambda x: x["checked_at"])


async def read_route_records(r) -> tuple[list[dict], dict]:
  """Every XAU route outcome Redis still holds: the history stream plus the per-match keys. Read commands only."""
  records: list[dict] = []
  info = {"stream_entries": 0, "stream_span": "empty", "keys": 0}
  stamps = []
  for _id, fields in await r.xrange("auto_trade:route_history:XAU", "-", "+"):
    payload = fields.get("payload") if isinstance(fields, dict) else None
    rec = _json(payload)
    if isinstance(rec, dict):
      rec["_source"] = "route_history"
      records.append(rec)
      info["stream_entries"] += 1
      stamps.append(int(rec.get("checked_at") or 0))
  async for key in r.scan_iter(match="auto_trade:route_outcome:XAU:*", count=500):
    rec = _json(await r.get(key))
    if isinstance(rec, dict):
      rec["_source"] = "route_outcome_key"
      records.append(rec)
      info["keys"] += 1
      stamps.append(int(rec.get("checked_at") or 0))
  stamps = [s for s in stamps if s]
  if stamps:
    info["stream_span"] = f"{iso(min(stamps))} -> {iso(max(stamps))}"
  return records, info


async def read_tp1_times(r) -> tuple[dict[str, int], dict]:
  """Earliest TP1 booking time per group id from the lifecycle streams (state partially_closed, message 'TP COMPLETED TP1')."""
  out: dict[str, int] = {}
  info = {"events": 0, "span": "empty"}
  stamps = []
  for stream in ("auto_trade:lifecycle_events", "auto_trade:events"):
    for _id, fields in await r.xrange(stream, "-", "+"):
      rec = _json(fields.get("payload")) if isinstance(fields, dict) else None
      if not isinstance(rec, dict):
        continue
      info["events"] += 1
      ts = rec.get("timestamp")
      if ts:
        stamps.append(int(ts))
      if rec.get("state") == "partially_closed" and "TP1" in str(rec.get("message") or "") and rec.get("group_id") and ts:
        gid = str(rec["group_id"])
        out[gid] = min(int(ts), out.get(gid, int(ts)))
  if stamps:
    info["span"] = f"{iso(min(stamps))} -> {iso(max(stamps))}"
  return out, info


async def read_be_known(r, pg, plan_ids: list[str]) -> dict[str, bool]:
  """True once a blocker's stop at or past entry is established: a closed group that booked a target (the executor moves the stop to break-even at TP1), or a
  live group whose runtime state shows the stop at or past its weighted entry."""
  known: dict[str, bool] = {}
  if not plan_ids:
    return known
  rows = await pg.fetch("SELECT group_id, booked_tp_count, exit_path FROM auto_trade_results WHERE group_id = ANY($1)", plan_ids)
  for row in rows:
    if row["booked_tp_count"] is not None and int(row["booked_tp_count"]) >= 1:
      known[row["group_id"]] = True
  for plan_id in plan_ids:
    if known.get(plan_id):
      continue
    runtime = _json(await r.get(f"execution:plan_runtime:{plan_id}"))
    if not isinstance(runtime, dict):
      continue
    entry, stop, direction = runtime.get("GroupWeightedFillPrice") or runtime.get("EntryFillPrice"), runtime.get("CurrentStop"), str(runtime.get("Direction") or "").upper()
    if entry and stop and direction in ("BUY", "SELL"):
      known[plan_id] = (stop >= entry) if direction == "BUY" else (stop <= entry)
  return known


# =====================================================================================================================================================================
# Report


def build_trade_rows(db_rows: list, mapping: dict) -> list[dict]:
  rows = []
  for rec in db_rows:
    creation = _json(rec["creation_envelope"])
    payload = creation.get("payload") if isinstance(creation, dict) else None
    sid = str((payload or {}).get("strategy") or "")
    display = str(rec["snapshot_strategy"] or rec["setup_type"] or "")
    mapping.setdefault(display, set()).add(sid or "(no envelope)")
    codes = evidence_codes(payload)
    rows.append({
      "group_id": rec["group_id"], "strategy_id": sid, "display": display, "direction": str(rec["direction"] or "").upper(), "net_r": float(rec["net_r"]),
      "closed_at": int(rec["closed_at"]), "exit_path": rec["exit_path"], "mfe_r": None if rec["mfe_r"] is None else float(rec["mfe_r"]),
      "touch_only": None if codes is None else (TOUCH_CODE in codes), "raw_score": raw_factor_score(payload),
      "admitted_at": int(rec["admitted_at"]), "plan": _json(rec["plan"]), "payload": payload, "opp_expires_at": _secs(rec["opp_expires_at"]), "opp_terminal_at": _secs(rec["opp_terminal_at"]),
      "opportunity_id": rec["opportunity_id"], "creation": creation, "terminal": _json(rec["terminal_envelope"]),
    })
  return rows


def prepare_m2_trade(t: dict, sources: list[Bars], spreads: dict[int, float], first_fill: int | None, now: int) -> dict:
  """Replay one trade under every variant, or return the reason it was skipped."""
  plan, payload = t["plan"], t["payload"]
  out = {"group_id": t["group_id"], "strategy_id": t["strategy_id"], "closed_at": t["closed_at"], "net_r": t["net_r"], "variants": {}, "skip": None, "bar_source": None, "flags": []}
  try:
    stop = float(plan["stop"]["price"])
    legs = [(float(leg["price"]), float(leg["volume_ratio"])) for leg in plan["entry"]["legs"]]
    spacing = float(plan["entry"].get("min_leg_spacing") or 0.0)
  except (KeyError, TypeError, ValueError):
    out["skip"] = "plan_unreadable"
    return out
  if not legs or t["direction"] not in ("BUY", "SELL"):
    out["skip"] = "no_legs_or_direction"
    return out
  zone = None
  try:
    zone = (float(payload["entry"]["low"]), float(payload["entry"]["high"])) if payload else None
  except (KeyError, TypeError, ValueError):
    zone = None
  entry_plan = (payload or {}).get("entry_plan") if payload else None
  start = t["admitted_at"]
  end_needed = min((first_fill or start) + HORIZON_SECONDS, now)
  bars = pick_bars(sources, start, end_needed)
  if bars is None:
    out["skip"] = "no_covering_bars"
    return out
  out["bar_source"] = bars.source
  ends = [x for x in (t.get("opp_terminal_at"), t.get("opp_expires_at"), _secs(plan["entry"].get("expires_at"))) if x]
  fill_end = min(ends) if ends else start + HORIZON_SECONDS
  buy = t["direction"] == "BUY"
  risk_unit = abs(legs[0][0] - stop) / PIP
  spread_fn = (lambda minute: spreads.get(minute)) if spreads else None
  variants = build_variants(direction=t["direction"], strategy_id=t["strategy_id"], plan_legs=legs, zone=zone, spacing_price=spacing, entry_plan=entry_plan)
  for name, spec in variants.items():
    if not spec["applicable"]:
      out["variants"][name] = {"applicable": False, "r": None, "fills": [], "missed": False, "flags": [], "outcome": "n/a", "note": spec["note"]}
      continue
    sim = simulate_ladder(bars, buy=buy, legs=spec["legs"], stop=stop, start_ts=start, fill_end_ts=fill_end, risk_unit_pips=risk_unit, spread_fn=spread_fn)
    out["variants"][name] = {"applicable": True, **sim, "note": spec["note"]}
  return out


def render_report(ctx: dict) -> str:
  lines = [f"# XAU review replay (PR-X0)", "", f"Window: {iso(ctx['since'])} -> {iso(ctx['until'])} UTC; halves split at {iso(halves_midpoint(ctx['since'], ctx['until']))} by closed_at (M3: by refusal time).", ""]
  lines += ["## Data coverage", ""] + ctx["coverage"] + [""]
  lines += ["## M1a: touch-only vs confirmed (pooled and per strategy)", ""]
  lines += md_table(["scope", "cohort", "part", "n", "net R", "expectancy", "win", "stop before 1R", "stop rate (known mfe)", "mfe unknown"], ctx["m1a"]) + [""]
  lines += [f"## M1b: terciles of raw_factor_score (cut points {ctx['cuts'] if ctx['cuts'] else 'n/a'})", ""]
  lines += md_table(["tercile", "n", "exp, window", "n half1", "exp half1", "n half2", "exp half2"], ctx["m1b"]) + [""]
  lines += ["## M2: variants (R_v0 units, total volume 1.0)", ""]
  lines += md_table(["variant", "part", "n", "L1 fills", "L2 fills", "L3 fills", "missed", "net R_v0", "mean", "V0 net on the same trades"], ctx["m2"]) + [""]
  lines += ["## M2-fid: V0 against the actual result", "", ctx["m2_fid"], ""]
  lines += ["## M3: same_direction_group_limit refusals", ""] + ctx["m3_lines"] + [""]
  lines += ["## Extra count (deferred item, no rule)", "", ctx["extra"], ""]
  lines += ["## Decisions", ""]
  lines += [f"D3 (PR-X2): {ctx['d3'][0]}  — {ctx['d3'][1]}", f"D4 (PR-X3): {ctx['d4'][0]}  — {ctx['d4'][1]}", f"D5 (PR-X4): {ctx['d5'][0]}  — {ctx['d5'][1]}", ""]
  return "\n".join(lines)


async def main(args) -> None:
  import asyncpg
  import redis.asyncio as aioredis
  from app.core.config import runtime_config

  os.makedirs(args.out, exist_ok=True)
  now = int(datetime.now(timezone.utc).timestamp())
  since = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())
  until = now if args.until is None else int(datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc).timestamp())
  r = aioredis.Redis.from_url(args.redis_url or runtime_config.runtime.redis.url, decode_responses=True)
  pg = await asyncpg.connect(args.pg_dsn or runtime_config.runtime.postgres.url)
  await pg.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")  # belt and braces: this session cannot write
  coverage: list[str] = []

  # ---- cohort -------------------------------------------------------------------------------------------------------------------------------------------------------
  mapping: dict[str, set] = {}
  db_rows = await pg.fetch(COHORT_SQL, since, until)
  all_rows = build_trade_rows(db_rows, mapping)
  cohort = [t for t in all_rows if t["strategy_id"] in STRATEGY_IDS or (not t["strategy_id"] and t["display"] in ("Supply Demand", "Order Block", "FVG"))]
  skipped_other = len(all_rows) - len(cohort)
  coverage.append(f"- Cohort: {len(db_rows)} XAU algo_auto results with a plan snapshot in the window; {len(cohort)} are supply/demand/order_block/fvg zone trades ({skipped_other} other strategies skipped).")
  coverage.append("- Snapshot strategy name -> analysis strategy id used for the filter (the snapshot holds display names, the envelope the ids): "
                  + "; ".join(f"{k} -> {sorted(v)}" for k, v in sorted(mapping.items())))
  missing_env = sum(1 for t in cohort if t["payload"] is None)
  coverage.append(f"- Opportunity envelope found for {len(cohort) - missing_env}/{len(cohort)} cohort trades (evidence codes missing: {sum(1 for t in cohort if t['touch_only'] is None)}, raw factor score missing: {sum(1 for t in cohort if t['raw_score'] is None)}).")

  # ---- M1 -------------------------------------------------------------------------------------------------------------------------------------------------------------
  m1a, m1b, cuts = m1_tables(cohort, since, until)
  d3 = m1_decide(cohort, since, until)
  coverage.append(f"- M1: examined {len(cohort)}, usable for touch-only vs confirmed {sum(1 for t in cohort if t['touch_only'] is not None)}, usable for terciles {sum(1 for t in cohort if t['raw_score'] is not None)}; "
                  f"mfe_r unknown on {sum(1 for t in cohort if t['mfe_r'] is None)} (counted as unknown in the stop-before-1R figures).")
  if d3[0] == "INSUFFICIENT DATA":
    print(f"M1: INSUFFICIENT DATA ({d3[1]})")

  # ---- bars -----------------------------------------------------------------------------------------------------------------------------------------------------------
  sources: list[Bars] = []
  archive = await load_archive_bars(pg, since, until)
  if archive is not None:
    sources.append(archive)
  else:
    coverage.append("- Bars: market_bars (the research archive) is absent or empty in this database.")
  for tf in ("M1", "M5", "M15"):
    bars = await load_redis_bars(r, tf)
    sources.append(bars)
    path = save_bars(bars, args.out)
    coverage.append(f"- Bars: redis bars:XAU:{tf}: {len(bars.t)} bars, {bars.span()} -> {path}")
  if archive is not None:
    coverage.append(f"- Bars: market_bars M1: {len(archive.t)} bars, {archive.span()}")
  spreads = await load_spreads(pg, since, until)
  coverage.append(f"- Spread: {len(spreads)} archived quote minutes; every minute without one uses {DEFAULT_SPREAD_PIPS:g} pips.")

  # ---- M2 -------------------------------------------------------------------------------------------------------------------------------------------------------------
  ids = [t["group_id"] for t in cohort]
  fills = await pg.fetch("SELECT group_id, min(filled_at) AS first_fill FROM auto_trade_fills WHERE group_id = ANY($1) GROUP BY group_id", ids) if ids else []
  first_fill = {f["group_id"]: _secs(f["first_fill"]) for f in fills}
  m2_trades = [prepare_m2_trade(t, sources, spreads, first_fill.get(t["group_id"]), now) for t in cohort]
  usable = [t for t in m2_trades if t["skip"] is None]
  skip_counts: dict[str, int] = {}
  for t in m2_trades:
    if t["skip"]:
      skip_counts[t["skip"]] = skip_counts.get(t["skip"], 0) + 1
  by_source: dict[str, int] = {}
  for t in usable:
    by_source[t["bar_source"]] = by_source.get(t["bar_source"], 0) + 1
  coverage.append(f"- M2: examined {len(m2_trades)}, replayed {len(usable)}, skipped {len(m2_trades) - len(usable)} ({skip_counts or 'none'}); bar source per replayed trade: {by_source or 'none'}.")
  coverage.append(f"- M2 flags: {sum(1 for t in usable for v in t['variants'].values() if 'horizon_24h' in v.get('flags', []))} variant runs closed at the 24 h horizon, "
                  f"{sum(1 for t in usable for v in t['variants'].values() if 'bars_end' in v.get('flags', []))} ran out of bars.")
  m2_rows, sums = m2_aggregate(usable, since, until)
  d4 = m2_decide(usable, sums, since, until)
  replayed = [t for t in usable if t["variants"]["V0"].get("r") is not None]
  same = sum(1 for t in replayed if _sign(t["variants"]["V0"]["r"]) == _sign(t["net_r"]))
  m2_fid = f"V0 has the same sign as the actual net R on {same}/{len(replayed)} replayed trades ({(same / len(replayed)) if replayed else 0:.1%}; gate {M2_FIDELITY_MIN:.0%})."
  if d4[0] == "FIDELITY FAILED":
    print("M2: FIDELITY FAILED")
  elif d4[0] == "NONE":
    print("M2: NO VARIANT, PR-X3 does not ship")
  write_csv(os.path.join(args.out, "m2.csv"), ["variant", "part", "n", "L1_fills", "L2_fills", "L3_fills", "missed", "net_R_v0", "mean", "V0_net_same_trades"], m2_rows)
  fid_rows = []
  for t in m2_trades:
    v = t["variants"]
    fid_rows.append([t["group_id"], t["strategy_id"], iso(t["closed_at"]), _fmt(t["net_r"]), t["skip"] or "", t["bar_source"] or ""]
                    + [(_fmt(v[n]["r"]) if n in v and v[n].get("r") is not None else "") for n in ("V0", "Va", "Vb", "Vc")]
                    + ["yes" if (v.get("V0", {}).get("r") is not None and _sign(v["V0"]["r"]) == _sign(t["net_r"])) else ("no" if v.get("V0", {}).get("r") is not None else "")])
  write_csv(os.path.join(args.out, "m2_fidelity.csv"), ["group_id", "strategy", "closed_utc", "actual_net_r", "skip", "bar_source", "V0", "Va", "Vb", "Vc", "V0_same_sign"], fid_rows)

  # ---- M3 -------------------------------------------------------------------------------------------------------------------------------------------------------------
  records, route_info = await read_route_records(r)
  refusals = route_refusals_from_records(records, since, until)
  coverage.append(f"- M3 sources: route_history stream {route_info['stream_entries']} entries and {route_info['keys']} per-match keys, coverage {route_info['stream_span']}; "
                  f"{len(refusals)} same_direction_group_limit refusals in the window after de-duplication by match id plus reason.")
  log_refusals: list[dict] = []
  log_files = 0
  if args.log_dir and os.path.isdir(args.log_dir):
    for name in sorted(os.listdir(args.log_dir)):
      if name.startswith("algo-bot.log"):
        log_files += 1
        with open(os.path.join(args.log_dir, name), errors="replace") as f:
          log_refusals += parse_log_refusals(f, since, until)
  before = len(refusals)
  refusals = merge_refusals(refusals, log_refusals)
  coverage.append(f"- M3 sources, third: the algo-bot file logs ({log_files} files) name {len(log_refusals)} refusals in the window; {len(refusals) - before} were not already in Redis. "
                  "A log line has a truncated match id and no blocking group ids, so those refusals are `undetermined` (counted and listed, never labelled).")
  tp1_ts, tp1_info = await read_tp1_times(r)
  coverage.append(f"- M3 TP1 times: {len(tp1_ts)} groups with a TP1 booking in the lifecycle streams ({tp1_info['events']} events, {tp1_info['span']}).")
  blockers = sorted({b for ref in refusals for b in ref["blockers"]})
  be_known = await read_be_known(r, pg, blockers)
  have_labels = await table_exists(pg, "opportunity_outcome_labels")
  m1_bars = next((b for b in sources if b.tf == "M1" and b.t), None)
  from app.research.outcome_model import Assumptions, Bar, label_opportunity, spec_from_rows

  assumptions = Assumptions()
  label_bars = [] if m1_bars is None else [Bar(m1_bars.t[i], m1_bars.o[i], m1_bars.h[i], m1_bars.l[i], m1_bars.c[i]) for i in range(len(m1_bars.t))]
  m3_rows = []
  for ref in refusals:
    ref.update(classify_refusal(ref, tp1_ts, be_known))
    opp_id = ref["match_id"][3:] if ref["match_id"].startswith("go_") else ref["match_id"]
    net, source, outcome = None, "", ""
    if ref["status"] == "risk_free":
      if have_labels:
        row = await pg.fetchrow("SELECT outcome, r_low, r_high, r_mark FROM opportunity_outcome_labels WHERE opportunity_id = $1 AND label_set = $2", opp_id, assumptions.label_set)
        if row:
          net, source, outcome = label_net_r(row["outcome"], row["r_low"], row["r_high"], row["r_mark"]), "stored", row["outcome"]
      if not source:
        env = await pg.fetchrow("SELECT creation_envelope, terminal_envelope FROM analysis_opportunities WHERE opportunity_id = $1", opp_id)
        if env and m1_bars is not None:
          spec, _version = spec_from_rows(_json(env["creation_envelope"]), _json(env["terminal_envelope"]), pip=PIP)
          if spec is not None:
            label = label_opportunity(spec, label_bars, lambda minute: None, assumptions, coverage_end=m1_bars.t[-1])
            net, source, outcome = label_net_r(label.outcome, label.r_low, label.r_high, label.r_mark), "in_memory", label.outcome
        elif not source:
          source = "no_opportunity_or_no_m1_bars"
    ref.update({"net_r": net, "label_source": source, "label_outcome": outcome})
    m3_rows.append(ref)
  counts = {k: sum(1 for x in m3_rows if x["status"] == k) for k in ("risk_free", "not_risk_free", "undetermined")}
  labelled = [x for x in m3_rows if x["status"] == "risk_free" and x["net_r"] is not None]
  d5 = m3_decide(m3_rows, since, until)
  if d5[0] == "n<15 ship anyway":
    print("M3: n<15, ship anyway (aligns Python with the executor, which already ignores a TP1-booked plan)")
  first, second = [x for x in labelled if half_of(x["checked_at"], since, until) == 0], [x for x in labelled if half_of(x["checked_at"], since, until) == 1]
  m3_summary = [["refusals found", len(m3_rows)], ["risk-free", counts["risk_free"]], ["not risk-free", counts["not_risk_free"]], ["undetermined (listed, not dropped)", counts["undetermined"]],
                ["labelled (stored or in-memory)", len(labelled)], ["labelled net R", _fmt(sum(x["net_r"] for x in labelled), 2)], ["labelled mean R", _fmt(_mean([x["net_r"] for x in labelled]))],
                ["half1: n / net R", f"{len(first)} / {_fmt(sum(x['net_r'] for x in first), 2)}"], ["half2: n / net R", f"{len(second)} / {_fmt(sum(x['net_r'] for x in second), 2)}"]]
  write_csv(os.path.join(args.out, "m3.csv"), ["measure", "value"], m3_summary)
  write_csv(os.path.join(args.out, "m3_refusals.csv"), ["match_id", "refused_utc", "strategy", "direction", "status", "why", "blockers", "label_source", "label_outcome", "net_r", "source"],
            [[x["match_id"], iso(x["checked_at"]), x.get("strategy"), x.get("direction"), x["status"], x.get("why"), ";".join(x["blockers"]), x.get("label_source"), x.get("label_outcome"),
              _fmt(x.get("net_r")), x.get("source")] for x in m3_rows])
  coverage.append(f"- M3: examined {len(m3_rows)}, usable (risk-free and labelled) {len(labelled)}, skipped {len(m3_rows) - len(labelled)} (not risk-free {counts['not_risk_free']}, undetermined {counts['undetermined']}, risk-free but unlabelled {counts['risk_free'] - len(labelled)}).")

  # ---- extra count ----------------------------------------------------------------------------------------------------------------------------------------------------
  if have_labels:
    n_fvg = await pg.fetchval("SELECT count(*) FROM opportunity_outcome_labels WHERE symbol = 'XAU' AND strategy = 'fvg' AND label_set = $1 AND created_at >= $2", assumptions.label_set,
                              int(datetime(2026, 11, 5, tzinfo=timezone.utc).timestamp()))
    extra = f"XAU FVG opportunities with a stored forward outcome label since 5 Nov: {n_fvg} (the 5 Nov decision wants about 150 before the FVG sweep gate returns)."
  else:
    extra = "XAU FVG opportunities with a stored forward outcome label since 5 Nov: 0 (the table opportunity_outcome_labels does not exist in this database: the labeller was never installed); about 150 are needed."
  write_csv(os.path.join(args.out, "m1a.csv"), ["scope", "cohort", "part", "n", "net_R", "expectancy", "win", "stop_before_1R", "stop_rate", "mfe_unknown"], m1a)
  write_csv(os.path.join(args.out, "m1b.csv"), ["tercile", "n", "exp_window", "n_half1", "exp_half1", "n_half2", "exp_half2"], m1b)
  summary = render_report({
    "since": since, "until": until, "coverage": coverage, "m1a": m1a, "m1b": m1b, "cuts": None if cuts is None else (round(cuts[0], 3), round(cuts[1], 3)), "m2": m2_rows, "m2_fid": m2_fid,
    "m3_lines": md_table(["measure", "value"], m3_summary), "extra": extra, "d3": d3, "d4": d4, "d5": d5,
  })
  with open(os.path.join(args.out, "summary.md"), "w") as f:
    f.write(summary)
  print(summary)
  await pg.close()
  await r.aclose()


# =====================================================================================================================================================================
# Self-test (no database, no Redis)


def _row(net, half, touch, score=1.0, *, since=0, until=1000, strategy="supply", mfe=0.5, exit_path="full_stop"):
  closed = since + (until - since) * (0.25 if half == 0 else 0.75)
  return {"net_r": net, "closed_at": closed, "touch_only": touch, "raw_score": score, "strategy_id": strategy, "mfe_r": mfe, "exit_path": exit_path}


def _flat_bars(prices, step=60, tf="M1", t0=0):
  """prices: list of (high, low, close) per bar."""
  return make_bars(tf, "synthetic", [(t0 + i * step, c, h, l, c) for i, (h, l, c) in enumerate(prices)])


def selftest() -> None:
  # --- read-only guard -----------------------------------------------------------------------------------------------------------------------------------------------
  with open(os.path.abspath(__file__)) as f:
    hits = scan_source(f.read())
  assert not hits, f"write verbs found in this script: {hits}"
  assert scan_source("x = 1  # " + "INS" + "ERT INTO t") == ["INS" + "ERT"], "the guard must catch a write verb"
  assert scan_source("c." + "hset(k, 1)") == ["." + "hset("]

  # --- M1 ------------------------------------------------------------------------------------------------------------------------------------------------------------
  S, U = 0, 1000
  def cohort(touch_h1, conf_h1, touch_h2, conf_h2, touch_net=-0.5, conf_net=0.0, score=lambda i: 1.0):
    rows = []
    for half, nt, nc in ((0, touch_h1, conf_h1), (1, touch_h2, conf_h2)):
      rows += [_row(touch_net, half, True, score(i)) for i in range(nt)]
      rows += [_row(conf_net, half, False, score(i)) for i in range(nc)]
    return rows
  branch, _ = m1_decide(cohort(20, 20, 20, 20), S, U)
  assert branch == "A", branch
  branch, reason = m1_decide(cohort(20, 20, 19, 20), S, U)  # (A) fails only because one half has n = 19
  assert branch == "C", (branch, reason)
  assert "touch-only n 39" in reason, reason
  branch, _ = m1_decide(cohort(20, 20, 20, 20, touch_net=-0.1), S, U)  # gap 0.1 < 0.2
  assert branch == "C", branch
  assert m1_decide(cohort(20, 20, 20, 20, touch_net=-0.2), S, U)[0] == "A"  # a gap of exactly 0.20 qualifies
  # (B): not (A) (cohorts have the same expectancy overall) but expectancy rises with the score in both halves
  rows = []
  for half in (0, 1):
    for tercile, score in ((1, 1.0), (2, 2.0), (3, 3.0)):
      rows += [_row(-0.5 + 0.5 * (tercile - 1), half, bool(i % 2), score) for i in range(10)]
  assert m1_decide(rows, S, U)[0] == "B", m1_decide(rows, S, U)
  rows_down = [dict(r, net_r=-r["net_r"]) for r in rows]
  assert m1_decide(rows_down, S, U)[0] == "C"
  missing = cohort(20, 20, 20, 20, score=lambda i: None if i < 9 else 1.0)
  assert sum(1 for r in missing if r["raw_score"] is None) / len(missing) > 0.2
  branch, reason = m1_decide(missing, S, U)
  assert branch == "INSUFFICIENT DATA" and "raw factor score missing" in reason, (branch, reason)
  assert m1_decide([dict(r, touch_only=None) for r in cohort(20, 20, 20, 20)], S, U)[0] == "INSUFFICIENT DATA"
  assert m1_decide([], S, U)[0] == "INSUFFICIENT DATA"
  tables = m1_tables(cohort(2, 2, 2, 2), S, U)
  assert tables[0] and tables[2] is not None

  # --- simulation ----------------------------------------------------------------------------------------------------------------------------------------------------
  SP = DEFAULT_SPREAD_PIPS * PIP  # 0.3
  def sim(bars, legs, stop=90.0, buy=True, fill_end=10**9, unit=100.0, **kw):
    return simulate_ladder(bars, buy=buy, legs=legs, stop=stop, start_ts=0, fill_end_ts=fill_end, risk_unit_pips=unit, **kw)
  # L1 100, stop 90 (risk 10 = 100 pips); TP1 110, TP2 120. A BUY fills when low + spread <= 100.
  fill_bar = (100.5, 99.7, 100.0)
  # the stop wins an ambiguous bar (it touches both the stop and TP1)
  res = sim(_flat_bars([fill_bar, (111.0, 89.0, 100.0)]), [(100.0, 1.0)])
  assert res["outcome"] == "stop" and abs(res["r"] + 1.0) < 1e-9, res
  # a fill bar cannot take a target: bar 0 fills and its range reaches TP1, nothing is booked; a later bar reaches it
  res = sim(_flat_bars([(110.5, 99.7, 100.0), (105.0, 101.0, 102.0)]), [(100.0, 1.0)])
  assert res["outcome"] == "open_at_bars_end" and res["fills"] == [True], res
  # breakeven after TP1 with the 6-tick buffer: 40% at +100 pips, the rest stopped at 100.06 (+0.6 pip): (40 + 0.36) / 100
  res = sim(_flat_bars([fill_bar, (110.0, 100.5, 105.0), (104.0, 100.0, 101.0)]), [(100.0, 1.0)])
  assert res["outcome"] == "tp1_then_stop" and abs(res["r"] - 0.4036) < 1e-9, res
  # SELL mirror: L1 100, stop 110; TP1 90 reached on the ask (low + spread <= 90); stop to 99.94
  res = sim(_flat_bars([(100.2, 99.0, 100.0), (95.0, 89.6, 92.0), (100.5, 94.0, 99.0)]), [(100.0, 1.0)], stop=110.0, buy=False)
  assert res["outcome"] == "tp1_then_stop" and abs(res["r"] - (0.4 * 100 + 0.6 * 0.6) / 100) < 1e-9, res
  # unfilled legs are cancelled at TP1: L2 at 99 is touched only after TP1 and must stay unfilled
  res = sim(_flat_bars([fill_bar, (110.0, 100.5, 105.0), (104.0, 98.0, 99.0)]), [(100.0, 0.6), (99.0, 0.4)])
  assert res["fills"] == [True, False], res
  # ... while a leg filled before TP1 is kept
  res = sim(_flat_bars([(100.5, 98.0, 99.0), (110.0, 100.5, 105.0), (104.0, 98.0, 99.0)]), [(100.0, 0.6), (99.0, 0.4)])
  assert res["fills"] == [True, True], res
  # the 24 h horizon closes at that bar's close and is flagged
  flat = [(101.0, 99.7, 100.5)] + [(101.0, 100.2, 100.5)] * 30
  res = sim(_flat_bars(flat, step=3600), [(100.0, 1.0)])
  assert "horizon_24h" in res["flags"] and res["outcome"] == "open_at_horizon", res
  assert abs(res["r"] - (0.5 / PIP) / 100.0) < 1e-9, res
  # a variant with no fill scores 0 and is missed
  res = sim(_flat_bars([(120.0, 110.0, 115.0)] * 3), [(100.0, 1.0)])
  assert res["missed"] and res["r"] == 0.0, res
  # a fill window that closes before the price arrives
  res = sim(_flat_bars([(120.0, 110.0, 115.0), (101.0, 99.0, 100.0)]), [(100.0, 1.0)], fill_end=60)
  assert res["missed"], res
  # R_v0: a variant with its own L1 (95) is scored in V0's risk (100 -> stop 90 = 100 pips), not its own (50 pips)
  deep = sim(_flat_bars([(100.0, 94.0, 95.0), (96.0, 94.5, 95.5)]), [(95.0, 1.0)], stop=90.0)
  assert deep["outcome"] == "open_at_bars_end" and abs(deep["r"] - ((95.5 - 95.0) / PIP) / 100.0) < 1e-9, deep
  deep_stop = sim(_flat_bars([(100.0, 94.0, 95.0), (96.0, 89.0, 95.5)]), [(95.0, 1.0)], stop=90.0)
  assert abs(deep_stop["r"] - (-50.0 / 100.0)) < 1e-9, deep_stop  # lost 50 pips on a 100-pip V0 unit: -0.5
  # a fixed spread function is honoured (3 pips = 0.3 default; here 1.0 keeps a BUY at 100 from filling at low 99.2)
  res = sim(_flat_bars([(100.5, 99.2, 99.5)]), [(100.0, 1.0)], spread_fn=lambda minute: 1.0)
  assert res["missed"], res
  res = sim(_flat_bars([(100.5, 99.2, 99.5)]), [(100.0, 1.0)])
  assert not res["missed"], res

  # --- variants ------------------------------------------------------------------------------------------------------------------------------------------------------
  zone = (3622.33, 3626.75)
  plan_legs = [(3623.0, 0.6), (3624.0, 0.2), (3625.0, 0.2)]
  plan = {"kind": "stop_cap_fit", "price": 3622.33, "candidates": [
    {"kind": "proximal", "price": 3620.96, "feasible": False, "depth_fraction": 0.0},
    {"kind": "stop_cap_fit", "price": 3622.33, "feasible": True, "depth_fraction": 0.24},
    {"kind": "deep", "price": 3625.0, "feasible": True, "depth_fraction": 0.6},
    {"kind": "deepest_infeasible", "price": 3626.5, "feasible": False, "depth_fraction": 0.9}]}
  v = build_variants(direction="SELL", strategy_id="supply", plan_legs=plan_legs, zone=zone, spacing_price=1.0, entry_plan=plan)
  assert v["V0"]["legs"] == plan_legs
  assert v["Va"]["applicable"] and v["Va"]["legs"][0][0] == 3625.0 and "deepest feasible" in v["Va"]["note"], v["Va"]  # the infeasible 3626.5 is never chosen
  assert [p for p, _ in v["Va"]["legs"]] == [3625.0, 3626.0] and [r for _, r in v["Va"]["legs"]] == [0.6, 0.4], v["Va"]  # 2.0 of depth to the far edge holds only 2 prices one unit apart
  only_selected = dict(plan, candidates=[plan["candidates"][1]])
  v = build_variants(direction="SELL", strategy_id="demand", plan_legs=plan_legs, zone=zone, spacing_price=1.0, entry_plan=only_selected)
  assert v["Va"]["applicable"] and v["Va"]["legs"] == plan_legs and "equals V0" in v["Va"]["note"], v["Va"]  # no deeper feasible candidate: Va is V0
  for sid in ("order_block", "fvg"):
    v = build_variants(direction="SELL", strategy_id=sid, plan_legs=plan_legs, zone=zone, spacing_price=1.0, entry_plan=None)
    assert not v["Va"]["applicable"], sid
  v = build_variants(direction="SELL", strategy_id="supply", plan_legs=plan_legs, zone=zone, spacing_price=1.0, entry_plan=plan)
  spread_prices = [p for p, _ in v["Vb"]["legs"]]
  tight_prices = [p for p, _ in _ladder("SELL", 3623.0, 3626.75, 10.0, False)]
  assert tight_prices == [3623.0, 3624.0, 3625.0], tight_prices
  assert spread_prices[0] == 3623.0 and spread_prices[-1] > 3625.0 and len(spread_prices) == 3, spread_prices  # spread_across_zone=True reaches toward the far edge
  assert [r for _, r in v["Vb"]["legs"]] == [0.6, 0.2, 0.2]
  assert [r for _, r in v["Vc"]["legs"]] == [0.2, 0.2, 0.6] and [p for p, _ in v["Vc"]["legs"]] == [3623.0, 3624.0, 3625.0]
  v2 = build_variants(direction="BUY", strategy_id="fvg", plan_legs=[(100.0, 0.6), (99.0, 0.4)], zone=(95.0, 100.0), spacing_price=1.0, entry_plan=None)
  assert [r for _, r in v2["Vc"]["legs"]] == [0.4, 0.6]  # two legs: 40/60

  # --- M2 rule -------------------------------------------------------------------------------------------------------------------------------------------------------
  def trade(i, actual, v0, half, **rs):
    variants = {"V0": {"applicable": True, "r": v0, "fills": [True], "missed": False}}
    for name in ("Va", "Vb", "Vc"):
      variants[name] = {"applicable": True, "r": rs.get(name, v0), "fills": [True], "missed": False}
    return {"group_id": f"g{i}", "strategy_id": "supply", "closed_at": 250 if half == 0 else 750, "net_r": actual, "variants": variants}
  def build(share_same, n=100, **rs):
    trades = []
    for i in range(n):
      actual = 1.0 if i % 2 == 0 else -1.0
      v0 = actual if i < share_same else -actual
      trades.append(trade(i, actual, v0, i % 2, **{k: (v if callable(v) is False else v(i, v0)) for k, v in rs.items()}))
    return trades
  for same, expected_fail in ((74, True), (75, False)):
    trades = build(same)
    rows, sums = m2_aggregate(trades, S, U)
    value, reason = m2_decide(trades, sums, S, U)
    assert (value == "FIDELITY FAILED") == expected_fail, (same, value, reason)
    if not expected_fail:
      assert value == "NONE", (value, reason)  # every variant equals V0: nothing beats it in either half
  # a variant that wins the whole window but loses one half is not selected
  trades = build(100, Vb=lambda i, v0: (v0 + 1.0) if i % 2 == 0 else (v0 - 0.4))  # the even trades (half 1 of the window) gain 1.0 each, the odd ones (half 2) lose 0.4
  rows, sums = m2_aggregate(trades, S, U)
  assert sums["Vb"]["window"]["net"] > sums["Vb"]["window"]["v0_net"] and sums["Vb"]["half2"]["net"] < sums["Vb"]["half2"]["v0_net"]
  assert m2_decide(trades, sums, S, U)[0] == "NONE"
  # a variant that wins both halves is selected, and the best whole-window net wins among several
  trades = build(100, Va=lambda i, v0: v0 + 0.1, Vc=lambda i, v0: v0 + 0.3)
  rows, sums = m2_aggregate(trades, S, U)
  assert m2_decide(trades, sums, S, U)[0] == "Vc"
  assert m2_decide([], {}, S, U)[0] == "FIDELITY FAILED"
  # Va's totals exclude trades it is not applicable to, and compare with V0 on the same trades
  trades = build(100, Va=lambda i, v0: v0 + 1.0)
  for t in trades[:50]:
    t["variants"]["Va"] = {"applicable": False, "r": None, "fills": [], "missed": False}
  rows, sums = m2_aggregate(trades, S, U)
  assert sums["Va"]["n"] == 50 and abs(sums["Va"]["window"]["net"] - sums["Va"]["window"]["v0_net"] - 50.0) < 1e-9

  # --- M3 ------------------------------------------------------------------------------------------------------------------------------------------------------------
  tp1 = {"p1": 100, "p2": 100}
  be = {"p1": True, "p2": True}
  assert classify_refusal({"blockers": ["p1", "p2"], "checked_at": 200}, tp1, be)["status"] == "risk_free"
  assert classify_refusal({"blockers": ["p1", "p3"], "checked_at": 200}, tp1, be)["status"] == "undetermined"  # p3's TP1 is unknown
  assert classify_refusal({"blockers": ["p1", "p3"], "checked_at": 200}, {"p1": 100, "p3": 300}, {"p1": True, "p3": True})["status"] == "not_risk_free"  # p3 booked TP1 after the refusal
  assert classify_refusal({"blockers": ["p1"], "checked_at": 200}, tp1, {"p1": None})["status"] == "undetermined"  # stop position unknown
  assert classify_refusal({"blockers": ["p1"], "checked_at": 200}, tp1, {"p1": False})["status"] == "not_risk_free"
  assert classify_refusal({"blockers": [], "checked_at": 200}, tp1, be)["status"] == "undetermined"
  def refs(nets_h1, nets_h2, extra_undetermined=0):
    out = [{"status": "risk_free", "net_r": x, "checked_at": 250} for x in nets_h1] + [{"status": "risk_free", "net_r": x, "checked_at": 750} for x in nets_h2]
    out += [{"status": "undetermined", "net_r": 9.0, "checked_at": 250} for _ in range(extra_undetermined)]
    return out
  assert m3_decide(refs([0.5] * 8, [0.5] * 8), S, U)[0] == "false"
  assert m3_decide(refs([0.5] * 8, [-0.1] * 8), S, U)[0] == "keep true, re-measure"  # positive overall, one half negative
  assert m3_decide(refs([-0.5] * 8, [-0.2] * 8), S, U)[0] == "keep true"
  value, reason = m3_decide(refs([0.5] * 7, [0.5] * 7), S, U)
  assert value == "n<15 ship anyway" and "n < 15" in reason and "executor" in reason, (value, reason)
  # an undetermined blocker is counted and excluded: 14 labelled + any number of undetermined stays n<15
  assert m3_decide(refs([0.5] * 7, [0.5] * 7, extra_undetermined=40), S, U)[0] == "n<15 ship anyway"
  assert m3_decide(refs([0.5] * 8, [0.5] * 7), S, U)[0] == "false"  # n = 15, positive in both halves
  assert label_net_r("tp", 2.0, 2.0, None) == 2.0 and label_net_r("sl", -1.0, -1.0, None) == -1.0 and label_net_r("open_timeout", 0, 0, 0.4) == 0.4
  assert label_net_r("ambiguous_sl_tp", -1.0, 2.0, None) is None and label_net_r("expired_unfilled", None, None, None) is None
  recs = [
    {"symbol": "XAU", "reason_code": REFUSAL_REASON, "match_id": "go_opp_a", "checked_at": 500, "measured": {"existing_plan_ids": ["v8:x"]}, "_source": "route_history"},
    {"symbol": "XAU", "reason_code": REFUSAL_REASON, "match_id": "go_opp_a", "checked_at": 450, "measured": {}, "_source": "route_outcome_key"},
    {"symbol": "XAU", "reason_code": "other", "match_id": "go_opp_b", "checked_at": 500}, {"symbol": "EURUSD", "reason_code": REFUSAL_REASON, "match_id": "go_opp_c", "checked_at": 500},
    {"symbol": "XAU", "reason_code": REFUSAL_REASON, "match_id": "go_opp_d", "checked_at": 5000},
  ]
  found = route_refusals_from_records(recs, 0, 1000)
  assert len(found) == 1 and found[0]["checked_at"] == 450 and found[0]["blockers"] == ["v8:x"], found  # de-duplicated, earliest time, blockers kept
  lines = [
    "2026-11-10 04:06:00,862 [INFO] app.autotrade.worker: v8 group exposure refused symbol=XAU match_id=go_opp_405b6 reason=same_direction_group_limit XAU BUY: admission refused (same_direction_group_limit)",
    "2026-11-10 07:26:00,976 [INFO] app.autotrade.worker: v8 group exposure refused symbol=EURUSD match_id=go_opp_b5e7a reason=same_direction_group_limit EURUSD BUY: x",
    "2026-11-10 07:27:00,976 [INFO] app.autotrade.worker: v8 group exposure refused symbol=XAU match_id=go_opp_b5e7a reason=same_thesis_active XAU BUY: x",
  ]
  day = int(datetime(2026, 11, 10, tzinfo=timezone.utc).timestamp())
  parsed = parse_log_refusals(lines, day, day + 86400)
  assert len(parsed) == 1 and parsed[0]["match_id"] == "go_opp_405b6" and parsed[0]["blockers"] == [] and parsed[0]["checked_at"] == day + 4 * 3600 + 360, parsed
  assert parse_log_refusals(lines, day + 86400, day + 2 * 86400) == []
  named = [{"match_id": "go_opp_405b6aaaa", "checked_at": parsed[0]["checked_at"] + 1, "blockers": ["v8:x"]}]
  assert len(merge_refusals(named, parsed)) == 1  # the log line is the same refusal Redis already names
  assert len(merge_refusals([], parsed)) == 1 and classify_refusal(parsed[0], {}, {})["status"] == "undetermined"  # log-only: undetermined, counted
  bars = _flat_bars([(1, 1, 1)] * 10, step=60)
  assert bars.covers(0, 9 * 60 + 5 * 60) and not bars.covers(0, 9 * 60 + 600), "the end of the data is tolerated by five bars, not more"
  print("selftest ok")


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--since", default="2026-11-10", help="window start, UTC date (default: the AE-CF1 deploy)")
  parser.add_argument("--until", default=None, help="window end, UTC date or datetime (default: now)")
  parser.add_argument("--out", default=None, help="output directory (required unless --selftest)")
  parser.add_argument("--selftest", action="store_true")
  parser.add_argument("--pg-dsn", default=None)
  parser.add_argument("--redis-url", default=None)
  parser.add_argument("--log-dir", default="/var/log/apexvoid", help="algo-bot file logs, only counted for coverage")
  parsed = parser.parse_args()
  if parsed.selftest:
    selftest()
  else:
    if not parsed.out:
      parser.error("--out is required")
    asyncio.run(main(parsed))
