"""Regenerate testdata/key-level-oracle.json from the profitable-week Python.

The golden is the behaviour of the Python Key Level detector (key_level_reaction)
as it stood at commit a1c77584, the end of the profitable XAU week
(14-18 Sep 2026: +567 pips, 6W/3L), over the committed real closed-bar captures.
The Go test in this directory replays the same captures through the Go engine
and compares the two bar by bar, so the golden is only ever an input: it is
never produced by Go.

This tool is not part of the build and imports nothing from the engine. Check the
commit out in a temporary worktree, mount it read-only next to the
analysis-engine directory and use the algo-bot image as the interpreter
(Python 3.12 with the frozen dependencies):

    git worktree add /tmp/oracle-profit a1c77584
    docker run --rm --entrypoint python \
      -e APEXVOID_CONFIG_FILE=/oracle/config/trading-bot.yml \
      -v /tmp/oracle-profit:/oracle:ro -v "$PWD":/engine \
      apexvoid-trading-bot:latest /engine/test/keylevelparity/generate_oracle_golden.py
    git worktree remove --force /tmp/oracle-profit

The frames are exactly what the scanner loaded then: M5, H1 and M15 only,
windowed to the configured lookbacks, at each closed M5 bar.
"""

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

ORACLE_COMMIT = "a1c77584"
CAPTURES = {
  "XAU": "replay-xau-production-capture-20260921.json",
  "GBPUSD": "replay-gbpusd-production-capture-20261005.json",
  "USDJPY": "replay-usdjpy-production-capture-20261005.json",
  "EURUSD": "replay-eurusd-production-capture-20261006.json",
  "GBPJPY": "replay-gbpjpy-production-capture-20261006.json",
  "XAU_LATER": "replay-xau-m1-production-capture-20261006.json",
}
ENGINE = os.environ.get("ENGINE_DIR", "/engine")
OUT = os.path.join(ENGINE, "testdata/key-level-oracle.json")
MINUTES = {"M5": 5, "M15": 15, "H1": 60}


def epoch(value):
  import pandas as pd
  return None if value in (None, "") else int(pd.Timestamp(value).timestamp())


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
  from app.analysis.detectors import build_context
  from app.analysis.scanner import _detector_settings, _htf_tfs
  from app.analysis.ohlc_source import window_for_timeframe

  raw = json.load(open(os.path.join(ENGINE, "testdata", CAPTURES[symbol])))
  instrument = raw["symbol"]
  frames_all = {}
  for tf, rows in raw["timeframes"].items():
    if tf not in MINUTES:
      continue
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex(
      pd.to_datetime(df.pop("t").astype("int64"), unit="s", utc=True), name="time",
    )
    frames_all[tf] = df
  settings = _detector_settings(instrument)
  htf = _htf_tfs()
  look = {tf: window_for_timeframe(tf) for tf in ("M5", *[t.upper() for t in htf])}
  bars, decisions = [], {}
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
    ctx = build_context(instrument, "M5", frames, settings, htf, causal_structure=False)
    bar = int(ts.timestamp())
    bars.append(bar)
    result = D.key_level_reaction(ctx)
    if result is None:
      continue
    zone = result.entry_zone
    decisions[str(bar)] = {
      "direction": result.direction,
      "entry_low": float(zone.low),
      "entry_high": float(zone.high),
      "confluence": int(result.confluence),
      "key_level": float(result.key_level),
      "structural_kind": result.structural_kind,
      "confirmation_type": result.confirmation_type,
      "touch_bar": epoch(result.touch_bar_ts),
      "confirmation_bar": epoch(result.confirmation_bar_ts),
      "source_touches": result.source_touches,
      "role": getattr(result, "key_level_role", None),
    }
  return symbol, {"capture": CAPTURES[symbol], "bars": bars, "decisions": decisions}


def main():
  with ProcessPoolExecutor(max_workers=len(CAPTURES)) as pool:
    results = dict(pool.map(run, CAPTURES))
  golden = {
    "oracle_commit": ORACLE_COMMIT,
    "description": (
      "Key Level decisions of the Python detector at the end of the profitable XAU week "
      "(14-18 Sep 2026) over the committed real captures: per closed M5 bar, whether "
      "key_level_reaction fired and, if so, its direction, entry band, confluence, level, "
      "role, reaction type and touch/confirmation bars. Regenerate with generate_oracle_golden.py."
    ),
    "symbols": results,
  }
  with open(OUT, "w") as handle:
    json.dump(golden, handle, separators=(",", ":"), sort_keys=True)
  print(json.dumps({s: len(d["decisions"]) for s, d in results.items()}))


if __name__ == "__main__":
  main()
