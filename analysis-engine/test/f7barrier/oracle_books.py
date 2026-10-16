"""Rebuild the frozen Python opposing-barrier books at requested M5 bars of one committed capture.

Model A  the book the frozen scanner built: ``build_structural_barrier_book`` over the merged, HTF-boosted ``zones`` of every
         timeframe (``build_context`` with ``causal_structure=False``, the exact call the production scanner made).
Model B  the book the frozen worker built at TradePlan time: ``_structural_barrier_zone_book`` (supply/demand zones of each
         timeframe, mitigated dropped, no HTF boost) through the same ``build_structural_barrier_book``.

This is a source-to-source oracle: it runs the frozen code of a git worktree, imports nothing from the Go engine, never reads
Go output, and modifies none of the reference algorithms. Revisions without ``_structural_barrier_zone_book`` (the October
slimmed worker) emit Model A only.

    git worktree add /tmp/py-oracle <commit>
    python oracle_books.py --oracle /tmp/py-oracle --capture <capture.json> --bars bars.json --out out.jsonl

Run it from a working directory that is NOT inside ``app/analysis`` (its ``types.py`` shadows the standard library).
"""

import argparse
import json
import math
import os
import sys

MINUTES = {"M5": 5, "M15": 15, "H1": 60}


def _num(value):
  if isinstance(value, float):
    return value if math.isfinite(value) else None
  return value


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--oracle", required=True)
  parser.add_argument("--capture", required=True)
  parser.add_argument("--bars", required=True, help="JSON {symbol: [M5 bar-open unix seconds]}")
  parser.add_argument("--out", required=True)
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
  from app.analysis.scanner import _detector_settings, _htf_tfs
  from app.autotrade import units as _units
  from app.autotrade.structural_barriers import build_structural_barrier_book
  from app.core.config import runtime_config as _rc
  try:
    from app.autotrade.worker import _structural_barrier_zone_book as recheck_zone_book
  except Exception as exc:  # the slimmed October worker no longer has it
    recheck_zone_book = None
    print("plan-time recheck book unavailable:", repr(exc), file=sys.stderr)
  try:
    from app.marketdata.ohlc import window_for_timeframe
  except ModuleNotFoundError:
    from app.analysis.ohlc_source import window_for_timeframe

  raw = json.load(open(args.capture))
  symbol = raw["symbol"]
  wanted = sorted(set(json.load(open(args.bars)).get(symbol, [])))
  frames_all = {}
  for tf, rows in raw["timeframes"].items():
    if tf not in MINUTES:
      continue
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("t").astype("int64"), unit="s", utc=True), name="time")
    frames_all[tf] = df
  settings = _detector_settings(symbol)
  htf = _htf_tfs()
  look = {tf: window_for_timeframe(tf) for tf in ("M5", *[t.upper() for t in htf])}
  policy = _rc.execution.policy
  book_kwargs = dict(
    major_score=float(_rc.analysis.market_map.major_score),
    pip_size=float(_units.pip_size(symbol)),
    max_width_atr=float(policy.execution_zone_max_width_atr),
    max_width_pips=float(policy.execution_zone_max_width_pips),
    proximal_band_atr=float(_rc.actionability.gates.proximal_band_atr),
  )

  def barrier_rows(book):
    return [
      {"side": b.side, "low": b.low, "high": b.high, "tier": b.tier, "score": _num(float(b.score)), "touches": b.touches,
       "mitigated": b.mitigated, "source_timeframes": list(getattr(b, "source_timeframes", ()))}
      for b in book
    ]

  m5 = frames_all["M5"]
  first, last = int(m5.index[0].timestamp()), int(m5.index[-1].timestamp())
  done, skipped = 0, 0
  with open(args.out, "w") as out:
    for bar in wanted:
      if bar < first or bar > last:
        continue
      close_at = bar + 300
      frames = {}
      for tf, df in frames_all.items():
        closed = df[(df.index + pd.Timedelta(minutes=MINUTES[tf])) <= pd.Timestamp(close_at, unit="s", tz="UTC")]
        frames[tf] = closed.tail(look[tf])
      if len(frames["M5"]) < look["M5"] or len(frames["H1"]) < 50 or len(frames["M15"]) < 60:
        skipped += 1
        continue
      record = {"symbol": symbol, "bar": bar, "price": float(frames["M5"]["close"].iloc[-1]), "history_bars_m5": int((m5.index <= pd.Timestamp(bar, unit="s", tz="UTC")).sum())}
      try:
        ctx = build_context(symbol, "M5", frames, settings, htf, causal_structure=False)
        record["htf_bias"] = ctx.htf_bias
        record["barriers_a"] = barrier_rows(build_structural_barrier_book(dict(ctx.analysis.per_tf), **book_kwargs))
        if recheck_zone_book is not None:
          record["barriers_b"] = barrier_rows(build_structural_barrier_book(recheck_zone_book(dict(frames), _rc, symbol=symbol), **book_kwargs))
      except Exception as exc:
        record["error"] = repr(exc)
      out.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
      done += 1
  print(json.dumps({"symbol": symbol, "bars": done, "skipped_short_history": skipped, "oracle": args.oracle}))


if __name__ == "__main__":
  main()
