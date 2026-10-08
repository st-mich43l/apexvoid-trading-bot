"""Regenerate testdata/legacy-strategies-oracle.json from the frozen Python oracle.

The golden is the behaviour of the frozen Python detectors that the Go
session_level, flip_zone, trendline and box_breakout strategies replaced
(detectors.py: session_level_reaction, flip_demand_zone_reaction,
flip_supply_zone_reaction, trendline_reaction, box_breakout), at the commit
below, over the committed real closed-bar captures. The Go test in this directory
replays the same captures through the Go engine and compares bar by bar; the
golden is only ever an input, never produced by Go.

Run it like techniqueparity/generate_oracle_golden.py (frozen commit checked out
as a worktree; any interpreter with the algo-bot requirements, run from a
directory that is not inside app/analysis because types.py shadows the stdlib):

    git worktree add /tmp/apexvoid-python-oracle 1c9f32303b7e9f15fc8e3767b5045ac1e8a37adc
    ORACLE=/tmp/apexvoid-python-oracle ENGINE_DIR=$PWD \
      APEXVOID_CONFIG_FILE=/tmp/apexvoid-python-oracle/config/trading-bot.yml \
      python test/legacyparity/generate_oracle_golden.py
"""

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

ORACLE_COMMIT = "1c9f32303b7e9f15fc8e3767b5045ac1e8a37adc"
CAPTURES = {
  "XAU": "replay-xau-production-capture-20260921.json",
  "GBPUSD": "replay-gbpusd-production-capture-20261005.json",
  "USDJPY": "replay-usdjpy-production-capture-20261005.json",
  "EURUSD": "replay-eurusd-production-capture-20261006.json",
  "GBPJPY": "replay-gbpjpy-production-capture-20261006.json",
}
DETECTORS = (
  "session_level_reaction",
  "flip_demand_zone_reaction",
  "flip_supply_zone_reaction",
  "trendline_reaction",
  "box_breakout",
)
ENGINE = os.environ.get("ENGINE_DIR", "/engine")
OUT = os.path.join(ENGINE, "testdata/legacy-strategies-oracle.json")
def epoch(value):
  import pandas as pd
  return None if value in (None, "") else int(pd.Timestamp(value).timestamp())


MINUTES = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}


def run(symbol):
  for key, value in {
    "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "DATABASE_URL": "postgresql://apexvoid:apexvoid@localhost:5/signals",
    "POSTGRES_PASSWORD": "x",
  }.items():
    os.environ.setdefault(key, value)
  sys.path.insert(0, os.environ.get("ORACLE", "/oracle") + "/algo-bot")
  import pandas as pd
  from app.analysis import detectors as D
  from app.analysis.detectors import build_context
  from app.analysis.scanner import _detector_settings, _htf_tfs
  try:
    from app.marketdata.ohlc import window_for_timeframe
  except ImportError:
    from app.analysis.ohlc_source import window_for_timeframe

  raw = json.load(open(os.path.join(ENGINE, "testdata", CAPTURES[symbol])))
  frames_all = {}
  for tf, rows in raw["timeframes"].items():
    if tf not in ("M5", "M15", "H1"):
      continue
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex(
      pd.to_datetime(df.pop("t").astype("int64"), unit="s", utc=True), name="time",
    )
    frames_all[tf] = df
  settings = _detector_settings(symbol)
  htf = _htf_tfs()
  look = {tf: window_for_timeframe(tf) for tf in ("M5", *[t.upper() for t in htf])}

  bars = []
  first = last = None
  for ts in frames_all["M5"].index:
    close_at = int(ts.timestamp()) + 300
    frames = {}
    for tf, df in frames_all.items():
      closed = df[(df.index + pd.Timedelta(minutes=MINUTES[tf])) <= pd.Timestamp(close_at, unit="s", tz="UTC")]
      closed = closed.tail(look[tf])
      if not closed.empty:
        frames[tf] = closed
    if len(frames.get("M5", [])) < look["M5"]:
      continue
    ctx = build_context(symbol, "M5", frames, settings, htf, causal_structure=False)
    bar = int(ts.timestamp())
    first = bar if first is None else first
    last = bar
    decisions = {}
    for name in DETECTORS:
      result = getattr(D, name)(ctx)
      if result is None:
        continue
      zone = result.entry_zone
      decisions[name] = {
        "direction": result.direction,
        "entry_low": float(zone.low),
        "entry_high": float(zone.high),
        "confluence": int(result.confluence),
        "structural_kind": result.structural_kind,
        "structural_source": result.structural_source,
        "key_level": float(result.key_level),
        "confirmation_type": result.confirmation_type,
        "touch_bar": epoch(result.touch_bar_ts),
        "confirmation_bar": epoch(result.confirmation_bar_ts),
      }
    if decisions:
      bars.append({"bar": bar, "detectors": decisions})
  return symbol, {"capture": CAPTURES[symbol], "first_bar": first, "last_bar": last, "bars": bars}


def main():
  with ProcessPoolExecutor(max_workers=len(CAPTURES)) as pool:
    results = dict(pool.map(run, CAPTURES))
  golden = {
    "oracle_commit": ORACLE_COMMIT,
    "description": (
      "Decisions of the frozen Python technique publishers over the committed real captures: "
      "per closed M5 bar from first_bar to last_bar, every supply/demand, order block, FVG, iFVG, "
      "CRT and confluence-zone decision (bars without a decision are omitted). Regenerate with "
      "generate_oracle_golden.py."
    ),
    "symbols": results,
  }
  with open(OUT, "w") as handle:
    json.dump(golden, handle, separators=(",", ":"), sort_keys=True)
  print(json.dumps({
    symbol: {name: sum(1 for bar in data["bars"] if name in bar["detectors"]) for name in DETECTORS}
    for symbol, data in results.items()
  }))


if __name__ == "__main__":
  main()
