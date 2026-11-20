"""Read-only, INSTRUMENT-AWARE replay of closed algo_auto trades on the bars still in Redis (FX1 part C).

The ladder, spread and stop cap come from the symbol's own configuration (XAU keeps its 1/2/3/4R ladder; the FX packs replay their 1R/2R 50/50
ladder with the breakeven move), `--strategy '%' ` replays every strategy, and two sections answer the pre-registered FX1 decision rule:
  4. rule inputs: the fidelity gate, the eligibility of each exit/stop variant (halves), and the stop-widening buckets;
  5. strategy x pair cells that lose (listed with the instruments.yml line that would switch them off; never applied here).
`--decide DIR` pools the per-pair trades_*.csv written by earlier runs into the two FX packs (USD majors, JPY crosses) and applies the same rule per pack.

What it answers, today, without waiting for new trades:
  1. Was each trade wrong from the start, or right but stopped before 1R? (MFE/MAE rebuilt from bars)
  2. Which change would have made these same trades pay? (the same entries replayed under alternative exits, stop and entry)

Read-only: it only SELECTs from Postgres and reads (ZRANGE) from Redis. It writes files to --out and nothing else.

Run inside the algo-bot container (it reads the bot's own runtime config for the Postgres and Redis URLs):

  docker compose cp algo-bot/scripts/kl_replay.py algo-bot:/tmp/kl_replay.py
  docker compose exec algo-bot python /tmp/kl_replay.py --symbol EURUSD --strategy '%' --since 2026-11-04 --out /var/log/apexvoid/fx_replay
  docker compose exec algo-bot python /tmp/kl_replay.py --decide --out /var/log/apexvoid/fx_replay   # after every pair has run
  # results land in ./logs/algo-bot/<out dir name>/ on the host

Bars: Redis keeps the newest bars_window_max (2000) bars per timeframe, so M5 reaches back about 7 trading days and M15 about a month. The first
thing the script does is save every bar it can read to CSV, because they roll off every bar. Each trade is replayed on M5 when M5 covers it, else
on M15 (coarser: flagged in the output). M1 covers only the last ~33 hours and is saved but not used.

Simulation rules (conservative): a bar that touches the stop and a target is a stop; the bar containing the fill is excluded; the horizon is 24 h or
the end of the bars, and a trade still open there is marked at that close (flagged). Bars are bid; a SELL's stop and targets are checked against
bid + --spread-pips (it is bought back at the ask). The replay enters the whole position at the first fill (the live trade scaled in), so variant
V0 (the live rules) is reported next to the actual net R as a fidelity check: trust the other variants only as far as V0 tracks reality.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone

HORIZON_SECONDS = 24 * 3600
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}


# --------------------------------------------------------------------------------------------------------------------------------------------------
# Bars


@dataclass
class Bars:
  tf: str
  t: list[int] = field(default_factory=list)  # open time, unix seconds, ascending
  o: list[float] = field(default_factory=list)
  h: list[float] = field(default_factory=list)
  l: list[float] = field(default_factory=list)
  c: list[float] = field(default_factory=list)

  def first_after(self, ts: int) -> int:
    """Index of the first bar whose OPEN is at or after ts (the bar containing ts is excluded when ts is inside it)."""
    lo, hi = 0, len(self.t)
    while lo < hi:
      mid = (lo + hi) // 2
      if self.t[mid] < ts:
        lo = mid + 1
      else:
        hi = mid
    return lo

  def bar_at(self, open_time: int) -> int | None:
    i = self.first_after(open_time)
    return i if i < len(self.t) and self.t[i] == open_time else None


async def load_bars(redis_client, symbol: str, tf: str, normalize) -> Bars:
  rows = await redis_client.zrange(f"bars:{symbol.upper()}:{tf}", 0, -1, withscores=True)
  out = []
  for member, score in rows:
    data = json.loads(member.decode() if isinstance(member, bytes) else member)
    ts = int(float(data.get("t", score)))
    if ts > 10_000_000_000:  # milliseconds
      ts //= 1000
    out.append((ts, *(normalize(symbol, float(data[k])) for k in ("o", "h", "l", "c"))))
  out.sort()
  bars = Bars(tf)
  for ts, o, h, l, c in out:
    bars.t.append(ts); bars.o.append(o); bars.h.append(h); bars.l.append(l); bars.c.append(c)
  return bars


def save_bars(bars: Bars, symbol: str, out_dir: str) -> str:
  path = os.path.join(out_dir, f"bars_{symbol}_{bars.tf}.csv")
  with open(path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["open_time_utc", "t", "open", "high", "low", "close"])
    for i in range(len(bars.t)):
      w.writerow([datetime.fromtimestamp(bars.t[i], timezone.utc).isoformat(), bars.t[i], bars.o[i], bars.h[i], bars.l[i], bars.c[i]])
  return path


# --------------------------------------------------------------------------------------------------------------------------------------------------
# Simulation (pure; unit-tested at the bottom with --selftest)


@dataclass
class Ladder:
  r_multiples: tuple[float, ...]
  close_ratios: tuple[float, ...]
  breakeven_after_first: bool


LIVE_XAU = Ladder((1.0, 2.0, 3.0, 4.0), (0.4, 0.2, 0.2, 0.2), True)
LIVE_FX = Ladder((1.0, 2.0), (0.5, 0.5), True)  # fixed_rr 1R/2R 50/50, stop to breakeven after 1R (config/instruments.yml, the FX packs' targeting)
XAU_SYMBOLS = ("XAU", "XAUUSD", "GOLD")
PACKS = {"usd_majors": ("EURUSD", "GBPUSD"), "jpy_crosses": ("USDJPY", "GBPJPY")}


def live_ladder(symbol: str) -> tuple[Ladder, str]:
  """The symbol's own ladder from its effective instrument config; falls back to the documented constants when the config cannot be read."""
  fallback = LIVE_XAU if symbol.upper() in XAU_SYMBOLS else LIVE_FX
  try:
    from app.core.instrument_geometry import instrument_runtime
    targeting = instrument_runtime(symbol).targeting
    multiples = tuple(float(x) for x in targeting.target_r_multiples)
    ratios = tuple(float(x) for x in targeting.close_ratios)
    if multiples and len(multiples) == len(ratios) and abs(sum(ratios) - 1.0) < 1e-6:
      return Ladder(multiples, ratios, getattr(targeting, "breakeven_after_r", None) is not None), "config"
  except Exception:  # noqa: BLE001 - a replay tool must keep going on a config it cannot read
    pass
  return fallback, "fallback"


def atr_before(bars: "Bars", ts: int, n: int = 14) -> float | None:
  """Mean true range of the n bars that closed before ts (None when fewer than n+1 exist)."""
  end = bars.first_after(ts)
  if end < n + 1:
    return None
  total = 0.0
  for i in range(end - n, end):
    total += max(bars.h[i] - bars.l[i], abs(bars.h[i] - bars.c[i - 1]), abs(bars.l[i] - bars.c[i - 1]))
  return total / n


def simulate(bars: Bars, *, buy: bool, entry: float, stop: float, start_ts: int, ladder: Ladder, spread: float,
             horizon_end: int) -> tuple[float, str]:
  """Net R of one position entered at ``entry`` at ``start_ts`` under ``ladder``. Returns (net_r, outcome)."""
  risk = abs(entry - stop)
  if not risk > 0:
    return float("nan"), "bad_risk"
  sign = 1.0 if buy else -1.0
  targets = [entry + sign * m * risk for m in ladder.r_multiples]
  remaining = 1.0
  realized = 0.0
  live_stop = stop
  booked = 0
  i = bars.first_after(start_ts)
  last_close = None
  while i < len(bars.t) and bars.t[i] < horizon_end:
    hi, lo = bars.h[i], bars.l[i]
    if not buy:  # a SELL is bought back at the ask
      hi, lo = hi + spread, lo + spread
    adverse_hit = lo <= live_stop if buy else hi >= live_stop
    if adverse_hit:  # conservative: the stop comes first in a bar that touches both
      realized += remaining * sign * (live_stop - entry) / risk
      return realized, "stop" if booked == 0 else f"tp{booked}_then_stop"
    while booked < len(targets):
      reached = hi >= targets[booked] if buy else lo <= targets[booked]
      if not reached:
        break
      part = ladder.close_ratios[booked]
      realized += part * ladder.r_multiples[booked]
      remaining -= part
      booked += 1
      if booked == 1 and ladder.breakeven_after_first:
        live_stop = entry
    if remaining <= 1e-9:
      return realized, f"tp{booked}_full"
    last_close = bars.c[i] + (0 if buy else spread)
    i += 1
  if last_close is None:
    return float("nan"), "no_bars"
  realized += remaining * sign * (last_close - entry) / risk
  return realized, "open_at_horizon" if booked == 0 else f"tp{booked}_open_at_horizon"


def excursion(bars: Bars, *, buy: bool, entry: float, stop: float, start_ts: int, end_ts: int, spread: float) -> dict:
  """MFE/MAE in R over the live trade's own life, plus the MFE reached BEFORE the stop was hit (the decisive number)."""
  risk = abs(entry - stop)
  i = bars.first_after(start_ts)
  mfe = mae = mfe_before_stop = 0.0
  stopped = False
  n = 0
  while i < len(bars.t) and bars.t[i] < end_ts:
    hi, lo = bars.h[i], bars.l[i]
    if not buy:
      hi, lo = hi + spread, lo + spread
    fav = (hi - entry) if buy else (entry - lo)
    adv = (entry - lo) if buy else (hi - entry)
    hit = lo <= stop if buy else hi >= stop
    if not stopped and not hit:
      mfe_before_stop = max(mfe_before_stop, fav)
    stopped = stopped or hit
    mfe, mae = max(mfe, fav), max(mae, adv)
    n += 1
    i += 1
  if n == 0 or not risk > 0:
    return {"bars": n, "mfe_r": None, "mae_r": None, "mfe_before_stop_r": None, "stop_touched": None}
  return {"bars": n, "mfe_r": mfe / risk, "mae_r": mae / risk, "mfe_before_stop_r": mfe_before_stop / risk, "stop_touched": stopped}


# --------------------------------------------------------------------------------------------------------------------------------------------------
# Data


TRADES_SQL = """
WITH f AS (
  SELECT group_id, min(filled_at) AS first_fill_at,
         (array_agg(entry_price ORDER BY filled_at, position_id))[1] AS first_fill_price,
         count(*) AS fills
  FROM auto_trade_fills
  WHERE trade_stream = 'algo_auto'
  GROUP BY group_id
)
SELECT r.group_id, r.symbol, r.setup_type, r.direction, r.net_r, r.exit_path, r.closed_at, r.stop_pips AS result_stop_pips,
       f.first_fill_at, f.first_fill_price, f.fills,
       s.opportunity_id, s.final_stop_price, s.final_stop_pips, s.planned_entry_price, s.technical_entry_low, s.technical_entry_high,
       s.technical_invalidation, s.quality_overall, s.stop_widening_ratio, s.technical_stop_pips,
       e.envelope
FROM auto_trade_results r
JOIN f ON f.group_id = r.group_id
LEFT JOIN auto_trade_plan_snapshots s ON s.plan_id = r.group_id
LEFT JOIN LATERAL (
  SELECT envelope FROM analysis_opportunity_events ev
  WHERE ev.opportunity_id = s.opportunity_id AND ev.event_kind = 'creation'
  ORDER BY ev.processed_at LIMIT 1
) e ON TRUE
WHERE r.trade_stream = 'algo_auto'
  AND r.symbol = $1
  AND r.setup_type ILIKE $2
  AND r.closed_at >= $3
  AND r.net_r IS NOT NULL
ORDER BY r.closed_at
"""


def _secs(v) -> int | None:
  if v is None:
    return None
  v = int(v)
  return v // 1000 if v > 10_000_000_000 else v


def _env(envelope, *path):
  node = envelope
  if isinstance(node, str):
    node = json.loads(node)
  for key in path:
    if not isinstance(node, dict):
      return None
    node = node.get(key)
  return node


# --------------------------------------------------------------------------------------------------------------------------------------------------
# Main


def variants(ladder: Ladder) -> list[tuple[str, str]]:
  steps = "/".join(f"{m:g}" for m in ladder.r_multiples) + "R " + "/".join(f"{int(round(c * 100))}" for c in ladder.close_ratios)
  return [
    ("V0_live_ladder", f"{steps}{', BE after the first target' if ladder.breakeven_after_first else ''} (the symbol's live rules; fidelity check vs actual)"),
    ("V1_1R_full", "close everything at 1R"),
    ("V2_1R_half_BE_2R", "50% at 1R, stop to BE, 50% at 2R"),
    ("V3_ladder_no_BE", "live ladder without the breakeven move"),
    ("V4_wider_stop", "live ladder, stop 0.5 M5 ATR further out (targets scale with the new R); refused (0R) if the stop would exceed the cap"),
    ("V5_enter_reaction_close", "live ladder, entered at the close of the confirmation bar instead of the ladder fill, same stop (report only: never implemented)"),
  ]


def summarize(rows: list[dict], key: str) -> dict:
  vals = [r[key] for r in rows if r.get(key) is not None and not (isinstance(r[key], float) and math.isnan(r[key]))]
  if not vals:
    return {"n": 0}
  wins = sum(1 for v in vals if v > 0)
  return {"n": len(vals), "net_r": round(sum(vals), 2), "exp_r": round(statistics.mean(vals), 3), "win": round(wins / len(vals), 2)}


# --------------------------------------------------------------------------------------------------------------------------------------------------
# The pre-registered FX1 decision rule (pure: every threshold below was fixed before the data was read)

FIDELITY_MIN_SAME_SIGN = 0.70   # V0 must have the same sign as the actual result on at least this share of trades ...
FIDELITY_MAX_GAP_PER_TRADE = 0.25  # ... and |V0 total - actual total| <= 0.25R x n
VARIANT_MIN_N = 30
VARIANT_MIN_GAIN_PER_TRADE = 0.2   # better than the actual by at least this many R per trade, with positive expectancy, in BOTH chronological halves
VARIANT_TIE_BREAK = ("V3_ladder_no_BE", "V4_wider_stop", "V1_1R_full")  # the only implementable variants (V2 equals the FX ladder; V5 is never implemented), in tie-break order
TIE_BAND = 0.02
WIDE_RATIO, NARROW_RATIO = 2.0, 1.25
WIDE_MIN_N, WIDE_MAX_EXP = 30, -0.2
NARROW_MIN_N, NARROW_MIN_EXP = 15, 0.0
CELL_MIN_N, CELL_MAX_EXP = 20, -0.4


def _num(v) -> float | None:
  if v is None or v == "":
    return None
  try:
    f = float(v)
  except (TypeError, ValueError):
    return None
  return None if math.isnan(f) else f


def _mean(xs: list[float]) -> float:
  return sum(xs) / len(xs) if xs else float("nan")


def fidelity(rows: list[dict]) -> dict:
  pairs = [(_num(r.get("actual_net_r")), _num(r.get("V0_live_ladder"))) for r in rows]
  pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
  n = len(pairs)
  agree = sum(1 for a, b in pairs if (a > 0) == (b > 0))
  gap = abs(sum(b for _, b in pairs) - sum(a for a, _ in pairs))
  share = agree / n if n else 0.0
  return {"n": n, "same_sign": agree, "share": round(share, 3), "actual": round(sum(a for a, _ in pairs), 2), "v0": round(sum(b for _, b in pairs), 2),
          "gap": round(gap, 2), "passes": n > 0 and share >= FIDELITY_MIN_SAME_SIGN and gap <= FIDELITY_MAX_GAP_PER_TRADE * n}


def variant_check(rows: list[dict], key: str) -> dict:
  """Eligibility of one variant over the trades it could replay: n, gain per trade over the actual, expectancy, and both chronological halves."""
  sub = [(r, _num(r.get(key)), _num(r.get("actual_net_r"))) for r in rows]
  sub = sorted(((r, v, a) for r, v, a in sub if v is not None and a is not None), key=lambda x: _num(x[0].get("closed_ts")) or 0)
  n = len(sub)
  gains = [v - a for _, v, a in sub]
  half = n // 2
  first, second = gains[:half], gains[half:]
  exp = _mean([v for _, v, _ in sub])
  gain = _mean(gains)
  ok = (n >= VARIANT_MIN_N and gain >= VARIANT_MIN_GAIN_PER_TRADE and exp > 0 and bool(first) and _mean(first) > 0 and _mean(second) > 0)
  return {"key": key, "n": n, "exp": exp, "gain": gain, "half1": _mean(first), "half2": _mean(second), "eligible": ok}


def choose_variant(checks: list[dict]) -> str | None:
  """Best gain per trade among the eligible variants; gains within TIE_BAND of the best go to the tie-break order V3, V4, V1."""
  eligible = [c for c in checks if c["eligible"] and c["key"] in VARIANT_TIE_BREAK]
  if not eligible:
    return None
  best = max(c["gain"] for c in eligible)
  tied = [c for c in eligible if best - c["gain"] <= TIE_BAND]
  return min(tied, key=lambda c: VARIANT_TIE_BREAK.index(c["key"]))["key"]


def widening_check(rows: list[dict]) -> dict:
  wide, narrow = [], []
  for r in rows:
    ratio, actual = _num(r.get("stop_widening_ratio")), _num(r.get("actual_net_r"))
    if ratio is None or actual is None:
      continue
    if ratio >= WIDE_RATIO:
      wide.append(actual)
    elif ratio < NARROW_RATIO:
      narrow.append(actual)
  wide_exp, narrow_exp = _mean(wide), _mean(narrow)
  return {"wide_n": len(wide), "wide_exp": wide_exp, "narrow_n": len(narrow), "narrow_exp": narrow_exp,
          "ship": len(wide) >= WIDE_MIN_N and wide_exp <= WIDE_MAX_EXP and len(narrow) >= NARROW_MIN_N and narrow_exp >= NARROW_MIN_EXP}


def losing_cells(rows: list[dict]) -> list[dict]:
  cells: dict[tuple[str, str], list[float]] = {}
  for r in rows:
    a = _num(r.get("actual_net_r"))
    if a is not None:
      cells.setdefault((r.get("symbol") or "?", r.get("setup_type") or "?"), []).append(a)
  out = [{"symbol": s, "strategy": g, "n": len(v), "exp": _mean(v), "net": sum(v)} for (s, g), v in cells.items() if len(v) >= CELL_MIN_N and _mean(v) <= CELL_MAX_EXP]
  return sorted(out, key=lambda c: c["exp"])


def _fmt(x: float, nd: int = 3) -> str:
  return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def strategy_id(label: str) -> str:
  return "_".join(str(label).lower().replace("/", " ").split())


def rule_report(rows: list[dict], label: str, symbols: list[str], max_stop_pips: float | None) -> list[str]:
  """Sections 4 and 5 of a summary: the rule inputs for these trades and the losing strategy x pair cells."""
  used = [r for r in rows if not r.get("skip")]
  fid = fidelity(used)
  lines = [f"## 4. Pre-registered decision rule inputs ({label})", "",
           f"**Fidelity gate** (V0 same sign on >= {FIDELITY_MIN_SAME_SIGN:.0%} of trades and |V0 total - actual total| <= {FIDELITY_MAX_GAP_PER_TRADE}R x n): "
           f"{fid['same_sign']}/{fid['n']} same sign ({fid['share']:.0%}); actual {fid['actual']}R, V0 {fid['v0']}R, gap {fid['gap']}R (limit {round(FIDELITY_MAX_GAP_PER_TRADE * fid['n'], 2)}R) "
           f"-> **{'PASS' if fid['passes'] else 'FAIL'}**", "",
           f"**Variants** (eligible when n >= {VARIANT_MIN_N}, gain over actual >= +{VARIANT_MIN_GAIN_PER_TRADE}R per trade, expectancy > 0, better than actual in both chronological halves; "
           f"tie-break {', '.join(k.split('_')[0] for k in VARIANT_TIE_BREAK)}; V5 is never implemented):", "",
           "| variant | n | expectancy | gain per trade | gain, 1st half | gain, 2nd half | eligible |", "|---|---|---|---|---|---|---|"]
  checks = [variant_check(used, k) for k in ("V1_1R_full", "V2_1R_half_BE_2R", "V3_ladder_no_BE", "V4_wider_stop", "V5_enter_reaction_close")]
  for c in checks:
    mark = "yes" if c["eligible"] else "no"
    if c["key"] in ("V2_1R_half_BE_2R", "V5_enter_reaction_close"):
      mark += " (never implemented)" if c["key"].startswith("V5") else " (same as the FX ladder; nothing to implement)" if c["eligible"] else ""
    lines.append(f"| {c['key']} | {c['n']} | {_fmt(c['exp'])} | {_fmt(c['gain'])} | {_fmt(c['half1'])} | {_fmt(c['half2'])} | {mark} |")
  chosen = choose_variant(checks) if fid["passes"] else None
  lines += ["", f"Variant picked: **{chosen or 'none'}**" + ("" if fid["passes"] else " (the fidelity gate failed, so no exit or stop variant may be implemented)"), ""]
  w = widening_check(used)
  lines += [f"**Stop widening** (ship `stop_envelope.max_floor_widening_ratio: {WIDE_RATIO}` when widened >= {WIDE_RATIO}x trades have expectancy <= {WIDE_MAX_EXP}R (n >= {WIDE_MIN_N}) "
            f"and widened < {NARROW_RATIO}x trades have expectancy >= {NARROW_MIN_EXP}R (n >= {NARROW_MIN_N})): "
            f"widened >= {WIDE_RATIO}x: n {w['wide_n']}, expectancy {_fmt(w['wide_exp'])}R; widened < {NARROW_RATIO}x: n {w['narrow_n']}, expectancy {_fmt(w['narrow_exp'])}R "
            f"-> **{'SHIP' if w['ship'] else 'do not ship'}**", ""]
  cells = losing_cells(used)
  lines += [f"## 5. Strategy x pair cells that lose (n >= {CELL_MIN_N}, expectancy <= {CELL_MAX_EXP}R; LISTED ONLY, nothing is switched off)", ""]
  if not cells:
    lines.append("None.")
  else:
    lines += ["| pair | strategy | n | expectancy | net R | line that would switch it off (owner decision) |", "|---|---|---|---|---|---|"]
    for c in cells:
      lines.append(f"| {c['symbol']} | {c['strategy']} | {c['n']} | {_fmt(c['exp'])} | {_fmt(c['net'], 2)} | "
                   f"`instruments.{c['symbol']}.overrides.execution.go_opportunity.observe_only_strategies: [{strategy_id(c['strategy'])}]` |")
    lines += ["", "That list is `[]` today by the owner rule of 2026-10-14 (no observe-only), so switching a cell off is a decision to reverse that rule for the pair."]
  return lines


def decide(out_dir: str) -> None:
  """Pool the per-pair trades_<PAIR>.csv into the packs and apply the rule per pack (read-only; writes decision_<pack>.md)."""
  for pack, pairs in PACKS.items():
    rows: list[dict] = []
    for pair in pairs:
      path = os.path.join(out_dir, f"trades_{pair}.csv")
      if not os.path.exists(path):
        print(f"missing {path}: run --symbol {pair} first")
        continue
      with open(path, newline="") as f:
        rows += [r for r in csv.DictReader(f)]
    lines = [f"# FX1 part C decision: {pack} ({' + '.join(pairs)})", "", f"{len(rows)} closed algo_auto trades pooled.", ""] + rule_report(rows, pack, list(pairs), None)
    path = os.path.join(out_dir, f"decision_{pack}.md")
    with open(path, "w") as f:
      f.write("\n".join(lines) + "\n")
    print("\n".join(lines) + f"\n\nfile: {path}\n")


async def main(args) -> None:
  import asyncpg
  import redis.asyncio as aioredis
  from app.core.config import runtime_config
  from app.autotrade import units
  from app.marketdata.ohlc import _normalize_price

  os.makedirs(args.out, exist_ok=True)
  r = aioredis.Redis.from_url(args.redis_url or runtime_config.runtime.redis.url, decode_responses=True)
  pg = await asyncpg.connect(args.pg_dsn or runtime_config.runtime.postgres.url)
  # Belt and braces: every transaction of this session is read-only, so even a bug here cannot write.
  await pg.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
  symbol = args.symbol.upper()
  pip = float(units.pip_size(symbol))
  is_xau = symbol in XAU_SYMBOLS
  spread = (args.spread_pips if args.spread_pips is not None else (3.0 if is_xau else 1.0)) * pip
  live, ladder_source = live_ladder(symbol)
  max_stop_pips, cap_source = args.max_stop_pips, "--max-stop-pips"
  if max_stop_pips is None:
    try:  # the symbol's own planner cap (stop_envelope.max_pips lands in execution.reaction.stop_max_pips)
      from app.core.instrument_geometry import instrument_runtime
      max_stop_pips, cap_source = float(instrument_runtime(symbol).execution.reaction.stop_max_pips), "config"
    except Exception:  # noqa: BLE001
      max_stop_pips, cap_source = (70.0 if is_xau else 20.0), "fallback"
  print(f"{symbol}: pip {pip}, spread {spread / pip:g} pips, stop cap {max_stop_pips:g} pips ({cap_source}), ladder {live.r_multiples} x {live.close_ratios} "
        f"BE={live.breakeven_after_first} ({ladder_source})")

  bars = {}
  for tf in ("M1", "M5", "M15", "H1"):
    bars[tf] = await load_bars(r, symbol, tf, _normalize_price)
    path = save_bars(bars[tf], symbol, args.out)
    span = (f"{datetime.fromtimestamp(bars[tf].t[0], timezone.utc):%Y-%m-%d %H:%M} -> "
            f"{datetime.fromtimestamp(bars[tf].t[-1], timezone.utc):%Y-%m-%d %H:%M}") if bars[tf].t else "empty"
    print(f"saved {len(bars[tf].t):5d} {tf} bars ({span}) -> {path}")

  since = int(datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp())
  rows = await pg.fetch(TRADES_SQL, symbol, args.strategy, since)
  print(f"\n{len(rows)} closed algo_auto {symbol} trades matching {args.strategy!r} since {args.since}")

  out_rows = []
  for row in rows:
    buy = str(row["direction"] or "").upper() == "BUY"
    fill_ts = _secs(row["first_fill_at"])
    entry = row["first_fill_price"]
    stop = row["final_stop_price"]
    if stop is None and entry is not None and row["result_stop_pips"]:
      stop = entry - row["result_stop_pips"] * pip if buy else entry + row["result_stop_pips"] * pip
    closed_ts = _secs(row["closed_at"])
    rec = {
      "group_id": row["group_id"], "direction": row["direction"], "closed_utc": datetime.fromtimestamp(closed_ts, timezone.utc).isoformat(),
      "actual_net_r": row["net_r"], "exit_path": row["exit_path"], "fills": row["fills"], "entry": entry, "stop": stop,
      "risk_pips": None if entry is None or stop is None else round(abs(entry - stop) / pip, 1), "quality": row["quality_overall"],
      "symbol": symbol, "setup_type": row["setup_type"], "closed_ts": closed_ts,
      "stop_widening_ratio": None if row["stop_widening_ratio"] is None else float(row["stop_widening_ratio"]),
      "technical_stop_pips": None if row["technical_stop_pips"] is None else float(row["technical_stop_pips"]),
    }
    if entry is None or stop is None or fill_ts is None:
      rec["skip"] = "no_entry_or_stop"; out_rows.append(rec); continue
    frame = next((bars[tf] for tf in ("M5", "M15") if bars[tf].t and bars[tf].t[0] <= fill_ts), None)
    if frame is None:
      rec["skip"] = "bars_rolled_off"; out_rows.append(rec); continue
    rec["tf"] = frame.tf
    horizon = fill_ts + HORIZON_SECONDS
    rec.update(excursion(frame, buy=buy, entry=entry, stop=stop, start_ts=fill_ts, end_ts=closed_ts + TF_SECONDS[frame.tf], spread=spread))

    atr = _env(row["envelope"], "payload", "technical_context", "atr")
    conf_ts = _env(row["envelope"], "payload", "technical_context", "confirmation", "confirmation_bar_time")
    rec["V0_live_ladder"], rec["V0_outcome"] = simulate(frame, buy=buy, entry=entry, stop=stop, start_ts=fill_ts, ladder=live, spread=spread, horizon_end=horizon)
    rec["V1_1R_full"], _ = simulate(frame, buy=buy, entry=entry, stop=stop, start_ts=fill_ts, ladder=Ladder((1.0,), (1.0,), False), spread=spread, horizon_end=horizon)
    rec["V2_1R_half_BE_2R"], _ = simulate(frame, buy=buy, entry=entry, stop=stop, start_ts=fill_ts, ladder=Ladder((1.0, 2.0), (0.5, 0.5), True), spread=spread, horizon_end=horizon)
    rec["V3_ladder_no_BE"], _ = simulate(frame, buy=buy, entry=entry, stop=stop, start_ts=fill_ts, ladder=Ladder(live.r_multiples, live.close_ratios, False), spread=spread, horizon_end=horizon)
    if not atr and bars["M5"].t:
      atr = atr_before(bars["M5"], fill_ts)  # the envelope carries no ATR for most FX setups: the M5 ATR(14) before the fill
    if atr:
      wide = stop - 0.5 * float(atr) if buy else stop + 0.5 * float(atr)
      if abs(entry - wide) / pip > max_stop_pips:
        rec["V4_wider_stop"], rec["V4_note"] = 0.0, "refused_over_cap"
      else:
        rec["V4_wider_stop"], _ = simulate(frame, buy=buy, entry=entry, stop=wide, start_ts=fill_ts, ladder=live, spread=spread, horizon_end=horizon)
    if conf_ts is not None and bars["M5"].t:
      ci = bars["M5"].bar_at(int(conf_ts))
      if ci is not None:
        # Bars are bid: a BUY entered at market pays the spread (ask), a SELL sells at the bid.
        e5 = bars["M5"].c[ci] + spread if buy else bars["M5"].c[ci]
        t5 = int(conf_ts) + TF_SECONDS["M5"]
        if (buy and e5 > stop) or (not buy and e5 < stop):
          rec["V5_enter_reaction_close"], _ = simulate(bars["M5"], buy=buy, entry=e5, stop=stop, start_ts=t5, ladder=live, spread=spread, horizon_end=t5 + HORIZON_SECONDS)
          rec["V5_risk_pips"] = round(abs(e5 - stop) / pip, 1)
    out_rows.append(rec)

  keys = sorted({k for rec in out_rows for k in rec}, key=lambda k: (k not in ("group_id", "closed_utc", "direction"), k))
  trades_path = os.path.join(args.out, f"trades_{symbol}.csv")
  with open(trades_path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys)
    w.writeheader()
    for rec in out_rows:
      w.writerow(rec)

  used = [x for x in out_rows if "skip" not in x]
  lines = [f"# Replay: {symbol} {args.strategy} closed since {args.since}", "",
           f"Trades: {len(out_rows)}; replayed: {len(used)} (M5: {sum(1 for x in used if x.get('tf') == 'M5')}, "
           f"M15: {sum(1 for x in used if x.get('tf') == 'M15')}); skipped: {len(out_rows) - len(used)} "
           f"({', '.join(sorted({x['skip'] for x in out_rows if 'skip' in x})) or 'none'})", ""]

  lines += ["## 1. Wrong from the start, or stopped before 1R? (MFE before the stop, live trades that were full stops)", "",
            "| MFE before stop | n | actual net R |", "|---|---|---|"]
  stops = [x for x in used if str(x.get("exit_path") or "").startswith("full_stop") or (x.get("actual_net_r") or 0) <= -0.9]
  for name, lo, hi in (("< 0.3R", -1, 0.3), ("0.3-0.6R", 0.3, 0.6), ("0.6-1R", 0.6, 1.0), (">= 1R", 1.0, 99)):
    cell = [x for x in stops if x.get("mfe_before_stop_r") is not None and lo <= x["mfe_before_stop_r"] < hi]
    lines.append(f"| {name} | {len(cell)} | {round(sum(x['actual_net_r'] for x in cell), 2)} |")

  v0_pairs = [(x["actual_net_r"], x["V0_live_ladder"]) for x in used if x.get("V0_live_ladder") is not None and not math.isnan(x["V0_live_ladder"])]
  agree = sum(1 for a, b in v0_pairs if (a > 0) == (b > 0))
  lines += ["", "## 2. Fidelity: V0 (live rules, single entry) vs actual", "",
            f"Actual net R {round(sum(a for a, _ in v0_pairs), 2)} vs V0 {round(sum(b for _, b in v0_pairs), 2)} over {len(v0_pairs)} trades; "
            f"same sign on {agree}/{len(v0_pairs)}. If these are far apart, read section 3 as direction only.", ""]

  lines += ["## 3. The same trades under each alternative", "",
            "Each variant is compared with the ACTUAL result of exactly the trades it could replay (V4 needs an ATR, V5 the confirmation bar).", "",
            "| variant | rule | n | net R | expectancy | win | actual net R, same trades | difference |", "|---|---|---|---|---|---|---|---|"]
  actual = summarize(used, "actual_net_r")
  lines.append(f"| actual | what happened | {actual.get('n')} | {actual.get('net_r')} | {actual.get('exp_r')} | {actual.get('win')} | | |")
  for key, desc in variants(live):
    subset = [x for x in used if x.get(key) is not None and not (isinstance(x[key], float) and math.isnan(x[key]))]
    s = summarize(subset, key)
    base = round(sum(x["actual_net_r"] for x in subset), 2)
    diff = round(s["net_r"] - base, 2) if s.get("n") else None
    lines.append(f"| {key} | {desc} | {s.get('n')} | {s.get('net_r')} | {s.get('exp_r')} | {s.get('win')} | {base} | {diff} |")
  lines += [""] + rule_report(used, f"{symbol}", [symbol], max_stop_pips)
  lines += ["", "Caveats: one position at the first fill (the live trade scaled in); bid bars with the SELL spread added; a bar touching stop and",
            "target counts as a stop; M15-replayed trades are coarse; small n, so a variant must beat the actual by a wide margin to mean anything."]
  summary_path = os.path.join(args.out, f"summary_{symbol}.md")
  with open(summary_path, "w") as f:
    f.write("\n".join(lines) + "\n")
  print("\n" + "\n".join(lines))
  print(f"\nfiles: {trades_path}, {summary_path}, bars_{symbol}_*.csv in {args.out}")
  await pg.close()
  await r.aclose()


def selftest() -> None:
  b = Bars("M5", t=[0, 300, 600, 900], o=[100] * 4, h=[101, 103, 105, 110], l=[99, 99.5, 101, 104], c=[100, 102, 104, 109])
  # BUY at 100, stop 98 (risk 2): bar 300 reaches 103 (TP1 at 102), bar 600 reaches 105 (TP2 104), bar 900 reaches 110 (TP3 106, TP4 108).
  r, outcome = simulate(b, buy=True, entry=100, stop=98, start_ts=1, ladder=LIVE_XAU, spread=0, horizon_end=10_000)
  assert outcome == "tp4_full" and abs(r - (0.4 * 1 + 0.2 * 2 + 0.2 * 3 + 0.2 * 4)) < 1e-9, (r, outcome)
  # Stop first on a bar touching both.
  b2 = Bars("M5", t=[0, 300], o=[100, 100], h=[100, 103], l=[100, 97], c=[100, 100])
  r, outcome = simulate(b2, buy=True, entry=100, stop=98, start_ts=1, ladder=LIVE_XAU, spread=0, horizon_end=10_000)
  assert outcome == "stop" and abs(r + 1) < 1e-9, (r, outcome)
  # BE after TP1, then back to entry: +0.4R.
  b3 = Bars("M5", t=[0, 300, 600], o=[100] * 3, h=[100, 102.5, 100.5], l=[100, 100.5, 99.5], c=[100, 102, 100])
  r, outcome = simulate(b3, buy=True, entry=100, stop=98, start_ts=1, ladder=LIVE_XAU, spread=0, horizon_end=10_000)
  assert outcome == "tp1_then_stop" and abs(r - 0.4) < 1e-9, (r, outcome)
  # SELL mirror with spread: entry 100, stop 102, spread 0.5. Bar high 101.6 + 0.5 = 102.1 >= stop -> stop.
  b4 = Bars("M5", t=[0, 300], o=[100, 100], h=[100, 101.6], l=[100, 99], c=[100, 100])
  r, outcome = simulate(b4, buy=False, entry=100, stop=102, start_ts=1, ladder=LIVE_XAU, spread=0.5, horizon_end=10_000)
  assert outcome == "stop" and abs(r + 1) < 1e-9, (r, outcome)
  # Excursion: MFE before stop counts only bars before the stop bar.
  ex = excursion(b2, buy=True, entry=100, stop=98, start_ts=1, end_ts=10_000, spread=0)
  assert ex["mfe_before_stop_r"] == 0 and ex["stop_touched"] is True and abs(ex["mfe_r"] - 1.5) < 1e-9, ex
  # Fill bar excluded.
  ex2 = excursion(b, buy=True, entry=100, stop=98, start_ts=1, end_ts=400, spread=0)
  assert ex2["bars"] == 1 and abs(ex2["mfe_r"] - 1.5) < 1e-9, ex2
  # --- decision rule -------------------------------------------------------------------------------------------------------------------
  def mk(i, actual, v0, **kw):
    return {"closed_ts": i, "actual_net_r": actual, "V0_live_ladder": v0, **kw}
  good = [mk(i, -1.0 if i % 2 else 1.0, -1.0 if i % 2 else 1.0) for i in range(40)]
  assert fidelity(good)["passes"]
  bad = [mk(i, 1.0, -1.0) for i in range(40)]
  assert not fidelity(bad)["passes"]
  # gap gate: same sign everywhere but V0 +0.5R per trade above the actual (limit 0.25R per trade)
  assert not fidelity([mk(i, 0.5, 1.0) for i in range(40)])["passes"]
  # variant: +0.3R per trade in both halves with positive expectancy is eligible; a gain only in one half is not; n < 30 is not
  rows = [mk(i, 0.0, 0.0, V3_ladder_no_BE=0.3) for i in range(40)]
  assert variant_check(rows, "V3_ladder_no_BE")["eligible"]
  rows = [mk(i, 0.0, 0.0, V3_ladder_no_BE=0.6 if i < 20 else -0.1) for i in range(40)]
  assert not variant_check(rows, "V3_ladder_no_BE")["eligible"]
  assert not variant_check([mk(i, 0.0, 0.0, V3_ladder_no_BE=0.3) for i in range(29)], "V3_ladder_no_BE")["eligible"]
  assert not variant_check([mk(i, -1.0, -1.0, V3_ladder_no_BE=-0.7) for i in range(40)], "V3_ladder_no_BE")["eligible"]  # +0.3R better but expectancy negative
  # tie-break: equal gains go to V3, then V4, then V1; a clearly better V1 wins
  e = lambda k, g: {"key": k, "eligible": True, "gain": g}
  assert choose_variant([e("V1_1R_full", 0.3), e("V4_wider_stop", 0.3), e("V3_ladder_no_BE", 0.29)]) == "V3_ladder_no_BE"
  assert choose_variant([e("V1_1R_full", 0.5), e("V3_ladder_no_BE", 0.3)]) == "V1_1R_full"
  assert choose_variant([{"key": "V5_enter_reaction_close", "eligible": True, "gain": 9.0}]) is None
  # widening buckets
  wr = [{"stop_widening_ratio": 2.5, "actual_net_r": -0.5} for _ in range(30)] + [{"stop_widening_ratio": 1.0, "actual_net_r": 0.1} for _ in range(15)]
  assert widening_check(wr)["ship"]
  assert not widening_check(wr[:29] + wr[30:])["ship"]  # 29 widened trades
  assert not widening_check([dict(r, actual_net_r=-0.1) if r["stop_widening_ratio"] == 2.5 else r for r in wr])["ship"]  # -0.1R is not <= -0.2R
  # cells
  cells = losing_cells([{"symbol": "EURUSD", "setup_type": "Key Level", "actual_net_r": -0.5}] * 20 + [{"symbol": "EURUSD", "setup_type": "FVG", "actual_net_r": -0.5}] * 19)
  assert [(c["symbol"], c["strategy"]) for c in cells] == [("EURUSD", "Key Level")]
  assert strategy_id("Supply Demand") == "supply_demand"
  print("selftest ok")


if __name__ == "__main__":
  p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  p.add_argument("--symbol", default="XAU")
  p.add_argument("--strategy", default="%key%level%", help="ILIKE pattern on auto_trade_results.setup_type ('%%' = every strategy)")
  p.add_argument("--decide", action="store_true", help="pool the trades_*.csv in --out into the FX packs and apply the pre-registered rule per pack")
  p.add_argument("--since", default="2026-11-02")
  p.add_argument("--spread-pips", type=float, default=None, help="added to bid bars for SELL stops/targets (default: XAU 3, everything else 1)")
  p.add_argument("--max-stop-pips", type=float, default=None, help="instrument stop cap for the wider-stop variant (default: the symbol's stop_envelope.max_pips from config)")
  p.add_argument("--out", default="/var/log/apexvoid/kl_replay")
  p.add_argument("--pg-dsn", default=None)
  p.add_argument("--redis-url", default=None)
  p.add_argument("--selftest", action="store_true")
  a = p.parse_args()
  if a.selftest:
    selftest()
  elif a.decide:
    decide(a.out)
  else:
    asyncio.run(main(a))
