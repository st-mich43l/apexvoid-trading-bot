"""Dump the historical Python analysis chain (the oracle) at hourly checkpoints of one committed capture.

This is an independent source-to-source oracle for docs/architecture/python-go-migration-forensic-audit.md. It runs the
frozen Python ``build_context`` (the exact call the frozen scanner made, ``causal_structure=False``) on a git worktree of
a historical commit and serialises every layer of ``TimeframeAnalysis`` with absolute bar timestamps, so a Go dump of the
same checkpoints can be compared layer by layer. It imports nothing from the Go engine, never reads Go output, and never
modifies the reference algorithms.

    git worktree add /tmp/py-oracle <commit>
    python oracle_dump.py --oracle /tmp/py-oracle --capture <capture.json> --out /tmp/oracle.jsonl [--every 12]

Run it from a working directory that is NOT inside ``app/analysis`` (its ``types.py`` shadows the standard library).
"""

import argparse
import dataclasses
import json
import math
import os
import sys

MINUTES = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}


def _num(value):
  if isinstance(value, float):
    return value if math.isfinite(value) else None
  return value


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--oracle", required=True)
  parser.add_argument("--capture", required=True)
  parser.add_argument("--out", required=True)
  parser.add_argument("--every", type=int, default=12, help="checkpoint every N closed M5 bars (12 = hourly)")
  parser.add_argument("--max", type=int, default=0)
  args = parser.parse_args()
  for key, value in {
    "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "DATABASE_URL": "postgresql://apexvoid:apexvoid@localhost:5/signals",
    "POSTGRES_PASSWORD": "x",
  }.items():
    os.environ.setdefault(key, value)
  os.environ.setdefault("APEXVOID_CONFIG_FILE", os.path.join(args.oracle, "config", "trading-bot.yml"))
  sys.path.insert(0, os.path.join(args.oracle, "algo-bot"))
  import pandas as pd
  from app.analysis.detectors import build_context
  from app.analysis.indicators import atr as atr_wilder
  from app.analysis.math_utils import atr_scalar, atr_series
  from app.analysis.scanner import _detector_settings, _htf_tfs
  barrier_book = None
  try:
    from app.autotrade.structural_barriers import build_structural_barrier_book, to_opposing_entries
    from app.autotrade import units as _units
    from app.core.config import runtime_config as _rc
    try:
      from app.autotrade.worker import _structural_barrier_zone_book as _recheck_zone_book
    except Exception as exc:
      _recheck_zone_book = None
      print("plan-time recheck book unavailable:", repr(exc), file=sys.stderr)
    barrier_book = (build_structural_barrier_book, to_opposing_entries, _units, _rc, _recheck_zone_book)
  except Exception as exc:  # the barrier book is optional evidence
    print("barrier book unavailable:", repr(exc), file=sys.stderr)
  try:
    from app.marketdata.ohlc import window_for_timeframe
  except ModuleNotFoundError:
    from app.analysis.ohlc_source import window_for_timeframe

  raw = json.load(open(args.capture))
  symbol = raw["symbol"]
  frames_all = {}
  for tf, rows in raw["timeframes"].items():
    if tf not in ("M5", "M15", "H1"):
      continue
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("t").astype("int64"), unit="s", utc=True), name="time")
    frames_all[tf] = df
  settings = _detector_settings(symbol)
  htf = _htf_tfs()
  look = {tf: window_for_timeframe(tf) for tf in ("M5", *[t.upper() for t in htf])}

  def frames_at(close_at):
    out = {}
    for tf, df in frames_all.items():
      closed = df[(df.index + pd.Timedelta(minutes=MINUTES[tf])) <= pd.Timestamp(close_at, unit="s", tz="UTC")]
      out[tf] = closed.tail(look[tf])
    return out

  def _wilder(fn, df, length):
    try:
      series = fn(df, length)
      return None if series is None else _num(float(series.iloc[-1]))
    except Exception:
      return None

  def epoch(value):
    if value is None:
      return None
    try:
      return int(pd.Timestamp(value).timestamp())
    except Exception:
      return None

  def stamp(df, index):
    if index is None:
      return None
    try:
      i = int(index)
    except Exception:
      return epoch(index)
    if 0 <= i < len(df):
      return int(df.index[i].timestamp())
    return None

  def obj(item, df, index_fields=()):
    if dataclasses.is_dataclass(item):
      data = {}
      for f in dataclasses.fields(item):
        v = getattr(item, f.name)
        if dataclasses.is_dataclass(v):
          v = obj(v, df)
        elif isinstance(v, (list, tuple)):
          v = [obj(x, df) if dataclasses.is_dataclass(x) else (x if not hasattr(x, "timestamp") else epoch(x)) for x in v]
        elif hasattr(v, "timestamp") and not isinstance(v, (int, float, str)):
          v = epoch(v)
        else:
          v = _num(v)
        data[f.name] = v
      for name in index_fields:
        data[name + "_t"] = stamp(df, getattr(item, name, None))
      return data
    return item

  m5 = frames_all["M5"]
  out = open(args.out, "w")
  count = 0
  for k, ts in enumerate(m5.index):
    if int(ts.timestamp()) % (300 * args.every) != 0:
      continue
    close_at = int(ts.timestamp()) + 300
    frames = frames_at(close_at)
    if len(frames.get("M5", [])) < look["M5"] or len(frames.get("H1", [])) < 50 or len(frames.get("M15", [])) < 60:
      continue
    ctx = build_context(symbol, "M5", frames, settings, htf, causal_structure=False)
    record = {"symbol": symbol, "bar": int(ts.timestamp()), "htf_bias": ctx.htf_bias, "frames": {}}
    for tf, df in frames.items():
      a = ctx.analysis.per_tf[tf]
      atr = atr_series(df, settings.atr_length)
      record["frames"][tf] = {
        "n": len(df), "last_t": int(df.index[-1].timestamp()),
        "atr_simple_last": _num(float(atr.iloc[-1])),
        "atr_simple_tail": [_num(float(x)) for x in atr.tail(5)],
        "atr_scalar_median": _num(float(atr_scalar(atr))),
        "atr_wilder_last": _wilder(atr_wilder, df, settings.atr_length),
        "structure": a.structure,
        "swings": [obj(s, df, ("index", "confirmed_index")) for s in a.swings],
        "breaks": [obj(b, df, ("index",)) for b in a.breaks],
        "levels": [obj(x, df) for x in a.key_levels],
        "legs": [obj(x, df, ("start", "end")) for x in a.legs],
        "sd": [obj(x, df, ("origin_index", "break_index")) for x in a.supply_demand_zones],
        "ob": [obj(x, df, ("origin_index", "break_index")) for x in a.order_blocks],
        "flip": [obj(x, df, ("origin_index", "break_index")) for x in a.flip_zones],
        "fvg": [obj(x, df, ("origin_index", "break_index")) for x in a.fvg_zones],
        "zones": [obj(x, df, ("origin_index", "break_index")) for x in a.zones],
        "technique_zones": [obj(x, df, ("origin_index", "break_index")) for x in getattr(a, "technique_zones", [])],
        "technique_instances": [obj(x, df, ("origin_index",)) for x in getattr(a, "technique_instances", [])],
        "pools": [obj(x, df) for x in a.liquidity_pools],
        "grabs": [obj(x, df, ("index",)) for x in a.liquidity_grabs],
        "sessions": [obj(x, df) for x in a.session_levels],
        "dealing_range": obj(a.dealing_range, df) if a.dealing_range is not None else None,
        "regime": obj(a.regime, df) if a.regime is not None else None,
        "momentum": a.momentum,
        "momentum_state": obj(a.momentum_state, df) if a.momentum_state is not None else None,
        "scalp_barriers": [obj(x, df, ("last_touch_index", "first_touch_index")) for x in a.scalp_barriers],
        "scalp_range": obj(a.scalp_range, df) if a.scalp_range is not None else None,
        "box_break": obj(a.box_break, df, ("accept_index",)) if a.box_break is not None else None,
      }
    if barrier_book is not None:
      build_book, to_entries, _units, _rc, _recheck_zone_book = barrier_book
      try:
        policy = _rc.execution.policy
        book = build_book(
          dict(ctx.analysis.per_tf),
          major_score=float(_rc.analysis.market_map.major_score),
          pip_size=float(_units.pip_size(symbol)),
          max_width_atr=float(policy.execution_zone_max_width_atr),
          max_width_pips=float(policy.execution_zone_max_width_pips),
          proximal_band_atr=float(_rc.actionability.gates.proximal_band_atr),
        )
        record["barriers"] = [
          {"side": b.side, "low": b.low, "high": b.high, "tier": b.tier, "score": _num(float(b.score)), "touches": b.touches,
           "mitigated": b.mitigated, "source_timeframes": list(getattr(b, "source_timeframes", ()))}
          for b in book
        ]
        record["price"] = float(frames["M5"]["close"].iloc[-1])
        if _recheck_zone_book is not None:
          # The book the frozen worker built at TradePlan time (supply/demand zones of each timeframe, mitigated dropped,
          # no HTF boost): what a Python-origin plan was checked against.
          recheck = build_book(
            _recheck_zone_book(dict(frames), _rc, symbol=symbol),
            major_score=float(_rc.analysis.market_map.major_score),
            pip_size=float(_units.pip_size(symbol)),
            max_width_atr=float(policy.execution_zone_max_width_atr),
            max_width_pips=float(policy.execution_zone_max_width_pips),
            proximal_band_atr=float(_rc.actionability.gates.proximal_band_atr),
          )
          record["barriers_recheck"] = [
            {"side": b.side, "low": b.low, "high": b.high, "tier": b.tier, "score": _num(float(b.score)), "touches": b.touches,
             "mitigated": b.mitigated, "source_timeframes": list(getattr(b, "source_timeframes", ()))}
            for b in recheck
          ]
      except Exception as exc:
        record["barriers_error"] = repr(exc)
    out.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
    count += 1
    if args.max and count >= args.max:
      break
  out.close()
  print(json.dumps({"symbol": symbol, "checkpoints": count, "oracle": args.oracle}))


if __name__ == "__main__":
  main()
