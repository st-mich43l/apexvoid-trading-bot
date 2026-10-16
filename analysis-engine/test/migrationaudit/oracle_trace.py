"""Which Python functions does the frozen analysis + detector chain actually execute on the committed captures?

For docs/architecture/python-go-parity-matrix.json: a Python function that is never executed on 11 real captures x
every registered detector is reported as NOT_EXERCISED instead of being given a verdict by name. Also records, per detector,
how often it fires (non-None result) at the traced checkpoints.

    python oracle_trace.py --oracle <worktree> --captures a.json,b.json --out trace.json [--every 12]

Run from a working directory outside app/analysis.
"""
import argparse, collections, json, os, sys


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--oracle", required=True)
  ap.add_argument("--captures", required=True)
  ap.add_argument("--out", required=True)
  ap.add_argument("--every", type=int, default=12)
  a = ap.parse_args()
  for k, v in {"TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi", "TELEGRAM_CHAT_ID": "-100123456789",
               "DATABASE_URL": "postgresql://apexvoid:apexvoid@localhost:5/signals", "POSTGRES_PASSWORD": "x"}.items():
    os.environ.setdefault(k, v)
  os.environ.setdefault("APEXVOID_CONFIG_FILE", os.path.join(a.oracle, "config", "trading-bot.yml"))
  root = os.path.join(a.oracle, "algo-bot")
  sys.path.insert(0, root)
  import pandas as pd
  from app.analysis import detectors as D
  from app.analysis.detectors import build_context
  from app.analysis.scanner import _detector_settings, _htf_tfs
  try:
    from app.marketdata.ohlc import window_for_timeframe
  except ModuleNotFoundError:
    from app.analysis.ohlc_source import window_for_timeframe
  minutes = {"M5": 5, "M15": 15, "H1": 60}
  executed = set()
  prefix = os.path.join(root, "app") + os.sep

  def profiler(frame, event, arg):
    if event == "call":
      f = frame.f_code.co_filename
      if f.startswith(prefix):
        executed.add((f[len(root) + 1:], frame.f_code.co_name, frame.f_code.co_firstlineno))

  fired = collections.Counter(); evaluated = collections.Counter(); errors = collections.Counter()
  for name in a.captures.split(","):
    raw = json.load(open(name))
    symbol = raw["symbol"]
    frames_all = {}
    for tf, rows in raw["timeframes"].items():
      if tf not in minutes: continue
      df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"])
      df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("t").astype("int64"), unit="s", utc=True), name="time")
      frames_all[tf] = df
    settings = _detector_settings(symbol)
    htf = _htf_tfs()
    look = {tf: window_for_timeframe(tf) for tf in ("M5", *[t.upper() for t in htf])}
    registry = list(D.LIVE_DETECTOR_REGISTRY)
    for ts in frames_all["M5"].index:
      if int(ts.timestamp()) % (300 * a.every) != 0: continue
      close_at = int(ts.timestamp()) + 300
      frames = {}
      for tf, df in frames_all.items():
        closed = df[(df.index + pd.Timedelta(minutes=minutes[tf])) <= pd.Timestamp(close_at, unit="s", tz="UTC")]
        frames[tf] = closed.tail(look[tf])
      if len(frames["M5"]) < look["M5"] or len(frames.get("H1", [])) < 50 or len(frames.get("M15", [])) < 60: continue
      sys.setprofile(profiler)
      try:
        ctx = build_context(symbol, "M5", frames, settings, htf, causal_structure=False)
        for reg in registry:
          evaluated[reg.name] += 1
          try:
            if reg.detector(ctx) is not None: fired[reg.name] += 1
          except Exception as exc:  # a detector that needs live services (redis, db) is recorded, not hidden
            errors[reg.name + ":" + type(exc).__name__] += 1
      finally:
        sys.setprofile(None)
  by_file = collections.defaultdict(list)
  for f, fn, line in sorted(executed):
    by_file[f].append([fn, line])
  json.dump({"oracle": a.oracle, "captures": a.captures.split(","), "every": a.every, "executed": by_file,
             "detector_evaluations": dict(evaluated), "detector_fired": dict(fired), "detector_errors": dict(errors)},
            open(a.out, "w"), indent=1, sort_keys=True)
  print(json.dumps({"executed_functions": len(executed), "evaluations": dict(evaluated), "fired": dict(fired), "errors": dict(errors)}))


if __name__ == "__main__":
  main()
