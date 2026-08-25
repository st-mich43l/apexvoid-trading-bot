"""Build Liquidity Sweep LabEvents from point-in-time M1/M5 OHLC.

Offline-first: load dumped bars from disk. Redis dump is a short-window smoke
helper only (feed lookback is too short for 60/20/20 calibration).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.analysis.math_utils import atr_series
from app.scalping.math_features import (
  classify_session_utc_hour,
  classify_volatility_regime,
  volatility_ratio,
)
from app.scalping.replay import aggregate_report, calibration_report
from app.scalping.replay_lab import LabEvent, replay_lab_event


ACTIVE_RANGE_LOOKBACK_M5 = 24
ATR_LENGTH = 14
ATR_SHORT_LENGTH = 7
ATR_LONG_LENGTH = 21
DEFAULT_BARS_AFTER = 45
DEFAULT_SPREAD = 0.2
DEFAULT_SLIPPAGE = 0.1
DEFAULT_PIP_SIZE = 0.1
DEFAULT_TARGET_MIN_PRICE = 1.0


def _ensure_ohlc_frame(df: pd.DataFrame) -> pd.DataFrame:
  if df is None or df.empty:
    return pd.DataFrame(
      columns=["open", "high", "low", "close", "volume"],
      index=pd.DatetimeIndex([], tz="UTC", name="time"),
    )
  out = df.copy()
  if not isinstance(out.index, pd.DatetimeIndex):
    if "time" in out.columns:
      out = out.set_index(pd.to_datetime(out["time"], utc=True))
      out = out.drop(columns=["time"], errors="ignore")
    elif "t" in out.columns:
      out = out.set_index(pd.to_datetime(out["t"], unit="s", utc=True))
      out = out.drop(columns=["t"], errors="ignore")
    else:
      raise ValueError("OHLC frame needs DatetimeIndex or time/t column")
  if out.index.tz is None:
    out.index = out.index.tz_localize("UTC")
  else:
    out.index = out.index.tz_convert("UTC")
  out.index.name = "time"
  rename = {}
  for src, dst in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume")):
    if src in out.columns and dst not in out.columns:
      rename[src] = dst
  if rename:
    out = out.rename(columns=rename)
  for col in ("open", "high", "low", "close"):
    if col not in out.columns:
      raise ValueError(f"OHLC missing column {col!r}")
  if "volume" not in out.columns:
    out["volume"] = 0.0
  out = out.sort_index()
  return out[["open", "high", "low", "close", "volume"]]


def load_ohlc_path(path: Path) -> pd.DataFrame:
  """Load OHLC from JSONL or CSV into a UTC-indexed DataFrame."""
  suffix = path.suffix.lower()
  if suffix == ".csv":
    raw = pd.read_csv(path)
    return _ensure_ohlc_frame(raw)
  rows: list[dict[str, Any]] = []
  for line in path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
      continue
    rows.append(json.loads(line))
  if not rows:
    return _ensure_ohlc_frame(pd.DataFrame())
  return _ensure_ohlc_frame(pd.DataFrame(rows))


def write_ohlc_jsonl(df: pd.DataFrame, path: Path) -> int:
  frame = _ensure_ohlc_frame(df)
  path.parent.mkdir(parents=True, exist_ok=True)
  count = 0
  with path.open("w", encoding="utf-8") as fh:
    for ts, row in frame.iterrows():
      payload = {
        "t": int(pd.Timestamp(ts).timestamp()),
        "o": float(row["open"]),
        "h": float(row["high"]),
        "l": float(row["low"]),
        "c": float(row["close"]),
        "v": float(row.get("volume") or 0.0),
      }
      fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
      count += 1
  return count


def _bar_ts_seconds(ts: Any) -> int:
  return int(pd.Timestamp(ts).timestamp())


def _bar_dict(row: pd.Series) -> dict[str, float]:
  return {
    "open": float(row["open"]),
    "high": float(row["high"]),
    "low": float(row["low"]),
    "close": float(row["close"]),
  }


def active_range_as_of(
  m5: pd.DataFrame,
  *,
  as_of_ts: pd.Timestamp,
  lookback: int = ACTIVE_RANGE_LOOKBACK_M5,
) -> tuple[float, float] | None:
  """Point-in-time active range: last ``lookback`` M5 bars with time <= as_of."""
  hist = m5.loc[m5.index <= as_of_ts]
  if hist.empty:
    return None
  window = hist.tail(lookback)
  if window.empty:
    return None
  low = float(window["low"].min())
  high = float(window["high"].max())
  if high <= low:
    return None
  return low, high


def atr_vr_as_of(
  m5: pd.DataFrame,
  *,
  as_of_ts: pd.Timestamp,
) -> tuple[float, str, float | None]:
  """ATR(14) plus VR regime from short/long ATR on M5 as-of ``as_of_ts``."""
  hist = m5.loc[m5.index <= as_of_ts]
  if hist.empty:
    return 1.0, classify_volatility_regime(None), None
  atr14 = atr_series(hist, ATR_LENGTH)
  atr_s = atr_series(hist, ATR_SHORT_LENGTH)
  atr_l = atr_series(hist, ATR_LONG_LENGTH)
  atr = float(atr14.iloc[-1]) if len(atr14) else 1.0
  if not (atr > 0):
    atr = 1.0
  short = float(atr_s.iloc[-1]) if len(atr_s) else atr
  long = float(atr_l.iloc[-1]) if len(atr_l) else atr
  vr_val = volatility_ratio(short, long)
  return atr, classify_volatility_regime(vr_val), vr_val


def bars_after_from(
  m1: pd.DataFrame,
  *,
  after_ts: pd.Timestamp,
  count: int,
) -> list[dict[str, float]]:
  future = m1.loc[m1.index > after_ts].head(count)
  return [_bar_dict(row) for _, row in future.iterrows()]


def build_liquidity_sweep_events(
  m1: pd.DataFrame,
  m5: pd.DataFrame,
  *,
  symbol: str = "XAU",
  pip_size: float = DEFAULT_PIP_SIZE,
  spread: float = DEFAULT_SPREAD,
  slippage: float = DEFAULT_SLIPPAGE,
  target_min_price: float = DEFAULT_TARGET_MIN_PRICE,
  bars_after: int = DEFAULT_BARS_AFTER,
  active_lookback: int = ACTIVE_RANGE_LOOKBACK_M5,
) -> list[dict[str, Any]]:
  """Emit Liquidity Sweep LabEvent dicts for M1 bars that pierce the PIT range edge.

  No HFS discover required — math gates decide reclaim / location / room.
  """
  m1f = _ensure_ohlc_frame(m1)
  m5f = _ensure_ohlc_frame(m5)
  events: list[dict[str, Any]] = []
  if m1f.empty or m5f.empty:
    return events

  for ts, row in m1f.iterrows():
    as_of = pd.Timestamp(ts)
    rng = active_range_as_of(m5f, as_of_ts=as_of, lookback=active_lookback)
    if rng is None:
      continue
    range_low, range_high = rng
    atr, vr, vr_val = atr_vr_as_of(m5f, as_of_ts=as_of)
    bar_low = float(row["low"])
    bar_high = float(row["high"])
    bar_open = float(row["open"])
    bar_close = float(row["close"])
    price = bar_close
    ts_sec = _bar_ts_seconds(as_of)
    utc_hour = datetime.fromtimestamp(ts_sec, tz=timezone.utc).hour
    session = classify_session_utc_hour(utc_hour)
    forward = bars_after_from(m1f, after_ts=as_of, count=bars_after)

    pierces: list[tuple[str, float, float]] = []
    if bar_low < range_low:
      pierces.append(("BUY", range_low, range_high))
    if bar_high > range_high:
      pierces.append(("SELL", range_high, range_low))

    for direction, liquidity_level, barrier in pierces:
      payload = {
        "timestamp": ts_sec,
        "direction": direction,
        "price": price,
        "atr": atr,
        "range_low": range_low,
        "range_high": range_high,
        "strategy": "liquidity_sweep_reversal",
        "liquidity_level": liquidity_level,
        "barrier": barrier,
        "bar": {
          "open": bar_open,
          "high": bar_high,
          "low": bar_low,
          "close": bar_close,
        },
        "spread": spread,
        "slippage": slippage,
        "target_min_price": target_min_price,
        "pip_size": pip_size,
        "session": session,
        "vr": vr,
        "symbol": symbol.upper(),
        "utc_hour": utc_hour,
        "bars_after": forward,
        "measured": {
          "vr": vr,
          "vr_ratio": vr_val,
          "active_lookback_m5": active_lookback,
          "source": "lab_event_builder",
        },
      }
      events.append(payload)
  return events


def write_events_jsonl(events: list[dict[str, Any]], path: Path) -> int:
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("w", encoding="utf-8") as fh:
    for event in events:
      fh.write(json.dumps(event, separators=(",", ":")) + "\n")
  return len(events)


def calibrate_events(events: list[dict[str, Any]]) -> dict[str, Any]:
  rows = [replay_lab_event(LabEvent.from_dict(event)) for event in events]
  traded = [r for r in rows if r.get("outcome") != "blocked"]
  return {
    "events": rows,
    "aggregate_all": aggregate_report(rows),
    "aggregate_traded": aggregate_report(traded),
    "calibration_traded": calibration_report(traded),
    "blocked_count": sum(1 for r in rows if r.get("outcome") == "blocked"),
    "allowed_count": len(traded),
    "event_count": len(events),
  }


async def dump_redis_ohlc(
  *,
  symbol: str,
  out_m1: Path,
  out_m5: Path,
  m1_count: int = 500,
  m5_count: int = 200,
) -> dict[str, int]:
  """Dump currently retained Redis bars (smoke window — not full history)."""
  from app.analysis.ohlc_source import RedisOHLCSource

  source = RedisOHLCSource()
  m1 = await source.window(symbol, "M1", m1_count)
  m5 = await source.window(symbol, "M5", m5_count)
  return {
    "m1": write_ohlc_jsonl(m1, out_m1),
    "m5": write_ohlc_jsonl(m5, out_m5),
  }


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    description="Build Liquidity Sweep LabEvents from M1/M5 OHLC dumps",
  )
  parser.add_argument("--m1", type=Path, default=None, help="M1 JSONL/CSV path")
  parser.add_argument("--m5", type=Path, default=None, help="M5 JSONL/CSV path")
  parser.add_argument("--out-events", type=Path, default=None)
  parser.add_argument("--out-report", type=Path, default=None)
  parser.add_argument("--symbol", default="XAU")
  parser.add_argument("--pip-size", type=float, default=DEFAULT_PIP_SIZE)
  parser.add_argument("--spread", type=float, default=DEFAULT_SPREAD)
  parser.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE)
  parser.add_argument("--target-min-price", type=float, default=DEFAULT_TARGET_MIN_PRICE)
  parser.add_argument("--bars-after", type=int, default=DEFAULT_BARS_AFTER)
  parser.add_argument(
    "--dump-redis",
    action="store_true",
    help="Dump Redis bars:XAU:M1/M5 to --out-m1/--out-m5 (short lookback smoke)",
  )
  parser.add_argument("--out-m1", type=Path, default=None)
  parser.add_argument("--out-m5", type=Path, default=None)
  parser.add_argument("--redis-m1-count", type=int, default=500)
  parser.add_argument("--redis-m5-count", type=int, default=200)
  args = parser.parse_args(argv)

  if args.dump_redis:
    if args.out_m1 is None or args.out_m5 is None:
      parser.error("--dump-redis requires --out-m1 and --out-m5")
    counts = asyncio.run(
      dump_redis_ohlc(
        symbol=args.symbol,
        out_m1=args.out_m1,
        out_m5=args.out_m5,
        m1_count=args.redis_m1_count,
        m5_count=args.redis_m5_count,
      )
    )
    print(json.dumps({"dumped": counts, "note": "redis_lookback_smoke_only"}, indent=2))
    if args.m1 is None and args.m5 is None and args.out_events is None:
      return 0
    # Allow dump-then-build in one invocation.
    if args.m1 is None:
      args.m1 = args.out_m1
    if args.m5 is None:
      args.m5 = args.out_m5

  if args.m1 is None or args.m5 is None:
    parser.error("--m1 and --m5 are required unless only --dump-redis")
  if args.out_events is None:
    parser.error("--out-events is required when building")

  m1 = load_ohlc_path(args.m1)
  m5 = load_ohlc_path(args.m5)
  events = build_liquidity_sweep_events(
    m1,
    m5,
    symbol=args.symbol,
    pip_size=args.pip_size,
    spread=args.spread,
    slippage=args.slippage,
    target_min_price=args.target_min_price,
    bars_after=args.bars_after,
  )
  write_events_jsonl(events, args.out_events)
  summary: dict[str, Any] = {
    "event_count": len(events),
    "out_events": str(args.out_events),
    "discipline": "never_tune_thresholds_on_holdout",
  }
  if args.out_report is not None:
    report = calibrate_events(events)
    args.out_report.parent.mkdir(parents=True, exist_ok=True)
    args.out_report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary["out_report"] = str(args.out_report)
    summary["blocked_count"] = report["blocked_count"]
    summary["allowed_count"] = report["allowed_count"]
  print(json.dumps(summary, indent=2))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
