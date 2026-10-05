"""Regenerate testdata/detector-parity-oracle.json from the frozen Python oracle.

The golden is the behaviour of the legacy Python detectors, at the frozen
commit below, over the committed real closed-bar captures. The Go test in this
directory replays the same captures through the Go engine and compares the two
bar by bar, so the golden is only ever an input: it is never produced by Go.

This tool is not part of the build and imports nothing from the engine. To
run it, check the frozen commit out in a temporary worktree, mount it
read-only next to the analysis-engine directory and use the algo-bot image as
the interpreter (Python 3.12 with the frozen dependencies):

    git worktree add /tmp/apexvoid-python-oracle 1c9f32303b7e9f15fc8e3767b5045ac1e8a37adc
    docker run --rm \
      -e APEXVOID_CONFIG_FILE=/oracle/config/trading-bot.yml \
      -v /tmp/apexvoid-python-oracle:/oracle:ro \
      -v "$PWD":/engine \
      apexvoid-trading-bot:latest \
      python /engine/test/detectorparity/generate_oracle_golden.py
    git worktree remove --force /tmp/apexvoid-python-oracle

The frames are exactly what the frozen scanner loads: M5, H1 and M15 only,
windowed to the configured lookbacks, at each closed M5 bar.
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
}
DETECTORS = ("break_retest", "range_edge", "snap_back", "momentum_ride", "fade_scalp")
ENGINE = os.environ.get("ENGINE_DIR", "/engine")
OUT = os.path.join(ENGINE, "testdata/detector-parity-oracle.json")
MINUTES = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}


def epoch(value):
  import pandas as pd
  return None if value in (None, "") else int(pd.Timestamp(value).timestamp())


def run(symbol):
  # Placeholders keep the oracle's settings loader from requiring a live
  # environment; none of them is read by the detectors.
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
  from app.analysis.detectors import build_context
  from app.analysis.scanner import _detector_settings, _htf_tfs
  from app.marketdata.ohlc import window_for_timeframe

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
  functions = {
    "break_retest": D.break_retest,
    "range_edge": D.range_edge_scalp,
    "snap_back": D.snap_back,
    "momentum_ride": D.momentum_ride,
    "fade_scalp": D.fade_scalp,
  }

  def frames_at(close_at):
    out = {}
    for tf, df in frames_all.items():
      closed = df[(df.index + pd.Timedelta(minutes=MINUTES[tf])) <= pd.Timestamp(close_at, unit="s", tz="UTC")]
      closed = closed.tail(look[tf])
      if not closed.empty:
        out[tf] = closed
    return out

  m5 = frames_all["M5"]
  bars = []
  for ts in m5.index:
    close_at = int(ts.timestamp()) + 300
    frames = frames_at(close_at)
    if len(frames.get("M5", [])) < look["M5"]:
      continue
    ctx = build_context(symbol, "M5", frames, settings, htf, causal_structure=False)
    structure = ctx.structures["M5"]
    record = {
      "bar": int(ts.timestamp()),
      "htf_bias": ctx.htf_bias,
      "local_bias": structure.bias,
      "regime": ctx.regime.kind if ctx.regime is not None else None,
      "pd_zone": structure.dealing_range.zone if structure.dealing_range is not None else None,
      "detectors": {},
    }
    for name in DETECTORS:
      result = functions[name](ctx)
      if result is None:
        continue
      zone = result.entry_zone
      record["detectors"][name] = {
        "direction": result.direction,
        "entry_low": float(zone.low),
        "entry_high": float(zone.high),
        "confluence": int(result.confluence),
        "key_level": float(result.key_level),
        "structural_source": result.structural_source,
        "structural_kind": result.structural_kind,
        "confirmation_type": result.confirmation_type,
        "touch_bar": epoch(result.touch_bar_ts),
        "confirmation_bar": epoch(result.confirmation_bar_ts),
        "source_touches": result.source_touches,
        "source_score": result.source_score,
        "mode": result.mode,
      }
    bars.append(record)
  return symbol, {"capture": CAPTURES[symbol], "bars": bars}


def main():
  with ProcessPoolExecutor(max_workers=len(CAPTURES)) as pool:
    results = dict(pool.map(run, CAPTURES))
  golden = {
    "oracle_commit": ORACLE_COMMIT,
    "description": (
      "Detector decisions of the frozen Python oracle over the committed real captures. "
      "Per closed M5 bar: higher-timeframe bias, local structure bias, regime and "
      "premium/discount zone, plus every decision of break_retest, range_edge, snap_back, "
      "momentum_ride and fade_scalp. Regenerate with generate_oracle_golden.py."
    ),
    "symbols": results,
  }
  with open(OUT, "w") as handle:
    json.dump(golden, handle, separators=(",", ":"), sort_keys=True)
  counts = {
    symbol: {name: sum(1 for bar in data["bars"] if name in bar["detectors"]) for name in DETECTORS}
    for symbol, data in results.items()
  }
  print(json.dumps(counts))


if __name__ == "__main__":
  main()
