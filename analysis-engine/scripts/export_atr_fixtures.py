"""Golden-master fixture exporter: Python's own ATR formulas vs Go's port.

Run from algo-bot/ (needs its venv — pandas/pandas_ta, nothing else app-
specific; these two modules are pure functions with no config/DB/Redis
dependency):

  PYTHONPATH=. .venv/bin/python \
    ../analysis-engine/scripts/export_atr_fixtures.py

Writes analysis-engine/testdata/atr_fixtures.json. See
docs/go-analysis-migration-audit.md §2.1/§5 (rebuild-analysis-engine.md's
own §15/§34): a golden-master fixture set from real Python output, which
the Go tests in internal/indicator compare against exactly, not a
hand-derived expectation that could itself be wrong.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import pandas as pd

from app.analysis.indicators import atr as wilder_atr
from app.analysis.math_utils import atr_series, true_range

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RAW_BARS = _REPO_ROOT / "analysis-engine" / "testdata" / "raw_xau_m5_snapshot.jsonl"
_OUT = _REPO_ROOT / "analysis-engine" / "testdata" / "atr_fixtures.json"


def _series_to_list(series: pd.Series) -> list[float | None]:
  return [
    None if (v is None or (isinstance(v, float) and np.isnan(v))) else float(v)
    for v in series.tolist()
  ]


def _build_case(name: str, rows: list[dict], length: int) -> dict:
  df = pd.DataFrame(rows)
  case = {"name": name, "length": length, "candles": rows}
  case["true_range"] = _series_to_list(true_range(df))
  case["simple_atr"] = _series_to_list(atr_series(df, length))
  w = wilder_atr(df, length)
  case["wilder_atr"] = _series_to_list(w) if w is not None else None
  return case


def main() -> None:
  cases: list[dict] = []

  # Real XAU M5 bars — a Redis `bars:XAU:M5` snapshot taken 2026-09-22, kept
  # as raw_xau_m5_snapshot.jsonl so this exporter is reproducible without a
  # live connection. Real market data, not synthetic — covers realistic
  # gap/volatility shape a hand-built case wouldn't.
  raw = [json.loads(line) for line in _RAW_BARS.read_text().splitlines() if line.strip()]
  ohlc = [
    {"open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"], "volume": r.get("v", 0)}
    for r in raw
  ]
  cases.append(_build_case("xau_m5_live_300_length14", ohlc, 14))
  cases.append(_build_case("xau_m5_live_300_length5", ohlc[:40], 5))

  # Insufficient warmup: fewer than length+1 bars — Wilder must be null
  # (pandas_ta's own v_series(length+1) check; see indicator/atr.go).
  cases.append(_build_case("insufficient_warmup_length14", ohlc[:10], 14))

  # Exactly length+1 bars: Wilder's minimum to produce its first value.
  cases.append(_build_case("exact_minimum_length14", ohlc[:15], 14))

  cases.append(_build_case("single_candle", ohlc[:1], 14))

  # Flat market: identical OHLC every bar, zero true range throughout.
  flat = [
    {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0}
    for _ in range(20)
  ]
  cases.append(_build_case("flat_market_zero_range", flat, 14))

  # Identical high==low==open==close per bar, but the flat price itself
  # steps between bars (range is zero every bar; the divergence between
  # SimpleATR/WilderATR here is purely from true range's close-to-close
  # gap terms, not from any intrabar range).
  same_hl: list[dict] = []
  close = 100.0
  for i in range(20):
    close += 0.1 if i % 2 == 0 else -0.05
    same_hl.append({"open": close, "high": close, "low": close, "close": close, "volume": 1.0})
  cases.append(_build_case("identical_high_low_moving_close", same_hl, 14))

  # Extreme volatility / gapping bars.
  random.seed(7)
  extreme: list[dict] = []
  price = 4000.0
  for _ in range(30):
    price += random.choice([-50, 50, -80, 80, 0])
    high = price + random.uniform(5, 40)
    low = price - random.uniform(5, 40)
    o = price + random.uniform(-10, 10)
    c = price + random.uniform(-10, 10)
    extreme.append({"open": o, "high": high, "low": low, "close": c, "volume": 1.0})
  cases.append(_build_case("extreme_volatility_gaps", extreme, 14))

  _OUT.write_text(json.dumps({"cases": cases}, indent=2))
  print(f"wrote {len(cases)} cases to {_OUT}")


if __name__ == "__main__":
  main()
