"""Regenerate testdata/technique-parity-oracle.json from the frozen Python oracle.

The golden is the behaviour of the frozen technique publishers
(technique_detectors.py: supply_demand / order_block / fvg / ifvg / crt
_technique_reaction and confluence_zone_reaction), at the commit below, over the
committed real closed-bar captures. The Go test in this directory replays the
same captures through the Go engine and compares the two bar by bar, so the
golden is only ever an input: it is never produced by Go.

This tool is not part of the build and imports nothing from the engine. Run it
like detectorparity/generate_oracle_golden.py: check the frozen commit out in a
temporary worktree, mount it read-only at /oracle, mount this analysis-engine
directory at /engine, and use the algo-bot image as the interpreter:

    git worktree add /tmp/apexvoid-python-oracle 1c9f32303b7e9f15fc8e3767b5045ac1e8a37adc
    docker run --rm \
      -e APEXVOID_CONFIG_FILE=/oracle/config/trading-bot.yml \
      -v /tmp/apexvoid-python-oracle:/oracle:ro \
      -v "$PWD":/engine \
      apexvoid-trading-bot:latest \
      python /engine/test/techniqueparity/generate_oracle_golden.py
    git worktree remove --force /tmp/apexvoid-python-oracle

Frames are exactly what the frozen scanner loads (M5, M15, H1, windowed to the
configured lookbacks, at each closed M5 bar), with one deliberate adjustment:
discover_crt_instances skips the last H1 row as "the forming bar". The production
scanner always had a forming H1 bar; a closed-bar replay does not, so without
help the oracle would discard a real closed H1 candle. A placeholder row is
appended to the H1 frame for CRT discovery only, so the oracle sees the same
closed candles the Go engine does.
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
  "supply_demand_technique_reaction",
  "order_block_technique_reaction",
  "fvg_technique_reaction",
  "ifvg_technique_reaction",
  "crt_technique_reaction",
  "confluence_zone_reaction",
)
ENGINE = os.environ.get("ENGINE_DIR", "/engine")
OUT = os.path.join(ENGINE, "testdata/technique-parity-oracle.json")
MINUTES = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}


def run(symbol):
  for key, value in {
    "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "DATABASE_URL": "postgresql://apexvoid:apexvoid@localhost:5/signals",
    "POSTGRES_PASSWORD": "x",
  }.items():
    os.environ.setdefault(key, value)
  sys.path.insert(0, "/oracle/algo-bot")
  import pandas as pd
  from app.analysis import detectors as D
  from app.analysis import technique_geometry as TG
  from app.analysis.detectors import build_context
  from app.analysis.scanner import _detector_settings, _htf_tfs
  try:
    from app.marketdata.ohlc import window_for_timeframe
  except ImportError:
    from app.analysis.ohlc_source import window_for_timeframe

  original = TG.discover_crt_instances

  def with_forming_placeholder(h1_df, exec_df, **kwargs):
    if len(h1_df):
      h1_df = pd.concat([h1_df, h1_df.tail(1)])
    return original(h1_df, exec_df, **kwargs)

  TG.discover_crt_instances = with_forming_placeholder

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
        "confirmation_type": result.confirmation_type,
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
