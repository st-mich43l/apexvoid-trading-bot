"""Regenerate testdata/scalp-breakout-retest-oracle.json from the profitable-week Python.

The golden is the behaviour of the Python Breakout Retest Scalp V2
(app.scalping.strategies.discover_breakout_retest at commit a1c77584, the end of
the profitable XAU week of 14-18 Sep 2026) over the committed real XAU capture
that carries M1 bars, driven the way app.scalping.runtime.process_m1_bar drove it
on every closed M1 bar: the M5 setup window of 120 closed bars, the M1
confirmation window of 60, the scalp context rebuilt at every M5 boundary and on
every cycle once older than 420 s, and spread 0 (spread guards are execution
owned in the Go design). The Go test in this directory replays the same capture
through the Go engine and compares the two bar by bar, so the golden is only an
input: it is never produced by Go.

This tool is not part of the build and imports nothing from the engine. Check the
commit out in a temporary worktree and use the algo-bot image as the interpreter:

    git worktree add /tmp/oracle-profit a1c77584
    docker run --rm --entrypoint python \
      -e APEXVOID_CONFIG_FILE=/oracle/config/trading-bot.yml \
      -v /tmp/oracle-profit:/oracle:ro -v "$PWD":/engine \
      apexvoid-trading-bot:latest /engine/test/scalpparity/generate_oracle_golden.py
    git worktree remove --force /tmp/oracle-profit
"""

import json
import os
import sys
from dataclasses import replace

for key, value in {
  "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
  "TELEGRAM_CHAT_ID": "-100123456789",
  "DATABASE_URL": "postgresql://apexvoid:apexvoid@localhost:5/signals",
  "POSTGRES_PASSWORD": "x",
}.items():
  os.environ.setdefault(key, value)
sys.path.insert(0, os.environ.get("ORACLE", "/oracle") + "/algo-bot")
import pandas as pd

from app.analysis.engine import AnalysisSettings, scalp_structure
from app.runtime.instrument_config import instrument_runtime_view
from app.runtime.price_identity import pip_price_digits
from app.scalping.microstructure import build_micro_structure
from app.scalping.strategies import discover_breakout_retest, discover_impulse_pullback, discover_range_sweep
from app.scalping.unified_context import build_scalp_context_and_micro, scalp_structure_payload

ENGINE = os.environ.get("ENGINE_DIR", "/engine")
CAPTURE = "replay-xau-m1-production-capture-20261006.json"
OUT = os.path.join(ENGINE, "testdata/scalp-breakout-retest-oracle.json")
OUT_RANGE_SWEEP = os.path.join(ENGINE, "testdata/range-sweep-oracle.json")
OUT_IMPULSE = os.path.join(ENGINE, "testdata/impulse-pullback-oracle.json")
MINUTES = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}
FIELDS = ("direction", "zone_low", "zone_high", "key_level", "invalidation", "target", "target_pips", "stop_pips",
          "source", "subtype", "quality", "confirmation_type", "break_time")


def run(capture):
  """Replay one capture through the Python lane; returns (cycles, breakouts, sweeps)."""
  raw = json.load(open(os.path.join(ENGINE, "testdata", capture)))
  symbol = raw["symbol"]
  cfg = instrument_runtime_view(symbol)
  pip = float(cfg.units.pip_size)
  digits = int(getattr(cfg.units, "price_digits", pip_price_digits(pip)))
  frames = {}
  for tf, rows in raw["timeframes"].items():
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("t").astype("int64"), unit="s", utc=True), name="time")
    frames[tf] = df
  m1 = frames["M1"]

  def closed(tf, close_at, count):
    df = frames[tf]
    return df[(df.index + pd.Timedelta(minutes=MINUTES[tf])) <= pd.Timestamp(close_at, unit="s", tz="UTC")].tail(count)

  def build_context(bar_ts, price):
    close_at = bar_ts + 60
    windows = {"m1": closed("M1", close_at, 60), "m5": closed("M5", close_at, 120),
               "m15": closed("M15", close_at, 120), "h1": closed("H1", close_at, 120)}
    snap, _micro, _ms = build_scalp_context_and_micro(symbol=symbol, windows=windows, price=price, pip_size=pip, now=bar_ts, cfg=cfg)
    if snap is None:
      return None
    levels, zones = scalp_structure_payload(scalp_structure(windows["m5"], AnalysisSettings(pip_size=pip)))
    return replace(snap, key_levels=levels, zones=zones)

  first = 61
  start = first
  while start > 0 and int(m1.index[start].timestamp()) % 300 != 0:
    start -= 1
  context, cycles, opportunities, sweeps, impulses = None, [], [], [], []
  for i in range(start, len(m1)):
    bar_ts = int(m1.index[i].timestamp())
    price = float(m1["close"].iloc[i])
    stale = context is not None and (bar_ts - int(context.m5_bar_ts)) > 420
    if bar_ts % 300 == 0 or context is None or stale:
      context = build_context(bar_ts, price)
    if i < first or context is None:
      continue
    close_at = bar_ts + 60
    m1_window, m5_window = closed("M1", close_at, 60), closed("M5", close_at, 120)
    if len(m5_window) < 120 or len(m1_window) < 60:
      continue
    micro_m1 = build_micro_structure(m1_window, equal_tol=0.5 * pip, price_digits=digits)
    micro_m5 = build_micro_structure(m5_window, equal_tol=0.5 * pip, price_digits=digits)
    found = discover_breakout_retest(context, micro_m1, m1_window, cfg, pip_size=pip, now=bar_ts, spread_pips=0.0,
                                     idle_reasons=[], m5_df=m5_window, m5_micro=micro_m5)
    cycles.append(bar_ts)
    swept = discover_range_sweep(context, micro_m1, m1_window, cfg, pip_size=pip, now=bar_ts, spread_pips=0.0,
                                 idle_reasons=[], m5_df=m5_window, m5_micro=micro_m5)
    pulled = discover_impulse_pullback(context, micro_m1, m1_window, cfg, pip_size=pip, now=bar_ts, spread_pips=0.0,
                                       idle_reasons=[], m5_df=m5_window, m5_micro=micro_m5)
    for opp in pulled:
      impulses.append({
        "bar": bar_ts, "direction": opp.direction, "zone_low": opp.zone_low, "zone_high": opp.zone_high,
        "key_level": opp.key_level, "invalidation": opp.invalidation_price, "target": opp.expected_target_price,
        "target_pips": opp.expected_target_pips, "stop_pips": opp.expected_stop_pips, "trigger_bar": opp.trigger_bar_ts,
        "trigger_price": opp.trigger_price, "position": opp.location_position, "role": opp.key_level_role,
        "level_kind": opp.measured.get("level_kind"), "retracement": opp.measured.get("retracement"),
      })
    for opp in swept:
      sweeps.append({
        "bar": bar_ts, "direction": opp.direction, "zone_low": opp.zone_low, "zone_high": opp.zone_high,
        "key_level": opp.key_level, "invalidation": opp.invalidation_price, "target": opp.expected_target_price,
        "target_pips": opp.expected_target_pips, "stop_pips": opp.expected_stop_pips, "trigger_bar": opp.trigger_bar_ts,
        "position": opp.location_position,
      })
    for opp in found:
      v2 = opp.measured.get("v2", {})
      values = {
        "direction": opp.direction, "zone_low": opp.zone_low, "zone_high": opp.zone_high, "key_level": opp.key_level,
        "invalidation": opp.invalidation_price, "target": opp.expected_target_price, "target_pips": opp.expected_target_pips,
        "stop_pips": opp.expected_stop_pips, "source": v2.get("break_source"), "subtype": v2.get("subtype"),
        "quality": v2.get("quality_score"), "confirmation_type": v2.get("confirmation_type"), "break_time": v2.get("break_time"),
      }
      opportunities.append({"bar": bar_ts, **{k: values[k] for k in FIELDS}})
  return cycles, opportunities, sweeps, impulses


def main():
  cycles, opportunities, sweeps, impulses = run(CAPTURE)
  golden = {
    "oracle_commit": "a1c77584",
    "oracle_description": (
      "Breakout Retest Scalp V2 as it ran at the end of the profitable week (14-18 Sep 2026): "
      "discover_breakout_retest driven as process_m1_bar drove it, spread 0."
    ),
    "capture": CAPTURE, "cycles": cycles, "opportunities": opportunities,
  }
  with open(OUT, "w") as handle:
    json.dump(golden, handle, separators=(",", ":"))
  range_golden = {
    "oracle_commit": "a1c77584",
    "oracle_description": (
      "Range Sweep Scalp as it ran at the end of the profitable week (14-18 Sep 2026): "
      "discover_range_sweep driven as process_m1_bar drove it, spread 0."
    ),
    "capture": CAPTURE, "cycles": cycles, "opportunities": sweeps,
  }
  with open(OUT_RANGE_SWEEP, "w") as handle:
    json.dump(range_golden, handle, separators=(",", ":"))
  impulse_golden = {
    "oracle_commit": "a1c77584",
    "oracle_description": (
      "Impulse Pullback Scalp as it ran at the end of the profitable week (14-18 Sep 2026): "
      "discover_impulse_pullback driven as process_m1_bar drove it, spread 0."
    ),
    "capture": CAPTURE, "cycles": cycles, "opportunities": impulses,
  }
  with open(OUT_IMPULSE, "w") as handle:
    json.dump(impulse_golden, handle, separators=(",", ":"))
  print(len(impulses), "impulse pullback opportunities")
  print(len(cycles), "cycles,", len(opportunities), "breakout opportunities,", len(sweeps), "range sweep opportunities")


if __name__ == "__main__":
  main()
