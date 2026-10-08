"""Regenerate testdata/synthetic-xau-m1-capture-<seed>.json and
testdata/synthetic-scalp-oracle-<seed>.json.

The real XAU capture with M1 bars covers 33 hours and yields only a handful of
Range Sweep opportunities, so it cannot exercise every gate. This tool builds a
SYNTHETIC XAU-like M1/M5/M15/H1 series (an Ornstein-Uhlenbeck price whose centre
drifts and whose mean-reversion strength switches between range and trend
regimes — it is labelled synthetic everywhere and is never presented as market
data) and runs the same Python scalp lane over it as generate_oracle_golden.py:
Breakout Retest Scalp V2 and Range Sweep at commit a1c77584. The Go test replays
the synthetic capture through the Go engine and requires the same opportunities
on the same bars.

    docker run --rm --entrypoint python \
      -e APEXVOID_CONFIG_FILE=/oracle/config/trading-bot.yml -e SEED=7 \
      -v /tmp/oracle-profit:/oracle:ro -v "$PWD":/engine \
      apexvoid-trading-bot:latest /engine/test/scalpparity/generate_synthetic_golden.py
"""

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_oracle_golden as lane  # noqa: E402

ENGINE = lane.ENGINE
SEED = int(os.environ.get("SEED", "7"))
MINUTES = int(os.environ.get("MINUTES", "2700"))
START = 1_790_000_000 - (1_790_000_000 % 3600)


def build():
  rng = random.Random(SEED)
  price, center, theta, drift = 4100.0, 4100.0, 0.05, 0.0
  m1 = []
  for i in range(MINUTES):
    if i % 240 == 0:
      # Regime switch: range (mean-reverting) or trend (drifting centre).
      if rng.random() < 0.55:
        theta, drift = rng.uniform(0.04, 0.10), 0.0
      else:
        theta, drift = rng.uniform(0.0, 0.02), rng.choice([-1, 1]) * rng.uniform(0.015, 0.05)
    center += drift + rng.gauss(0, 0.02)
    opened = price
    price = opened + theta * (center - opened) + rng.gauss(0, 0.30)
    closed = round(price, 2)
    high = round(max(opened, closed) + abs(rng.gauss(0, 0.10)), 2)
    low = round(min(opened, closed) - abs(rng.gauss(0, 0.10)), 2)
    m1.append([START + 60 * i, round(opened, 2), high, low, closed, 100.0])
    price = closed

  def aggregate(minutes):
    out, bucket = [], []
    for row in m1:
      if row[0] % (minutes * 60) == 0 and bucket:
        out.append(bucket)
        bucket = []
      bucket.append(row)
    if len(bucket) == minutes:
      out.append(bucket)
    return [
      [b[0][0], b[0][1], max(r[2] for r in b), min(r[3] for r in b), b[-1][4], sum(r[5] for r in b)]
      for b in out if len(b) == minutes
    ]

  return {
    "version": 1,
    "symbol": "XAU",
    "description": "SYNTHETIC XAU-like closed-bar series (Ornstein-Uhlenbeck with regime switches), generated to exercise the scalp gates. Not market data.",
    "provenance": {"source": "generate_synthetic_golden.py", "seed": SEED, "synthetic": True},
    "columns": ["t", "open", "high", "low", "close", "volume"],
    "timeframes": {"M1": m1, "M5": aggregate(5), "M15": aggregate(15), "H1": aggregate(60)},
  }


def main():
  name = f"synthetic-xau-m1-capture-{SEED}.json"
  with open(os.path.join(ENGINE, "testdata", name), "w") as handle:
    json.dump(build(), handle, separators=(",", ":"))
  cycles, breakouts, sweeps, impulses = lane.run(name)
  with open(os.path.join(ENGINE, "testdata", f"synthetic-scalp-oracle-{SEED}.json"), "w") as handle:
    json.dump({
      "oracle_commit": "a1c77584", "capture": name, "synthetic": True,
      "cycles": cycles, "breakouts": breakouts, "range_sweeps": sweeps, "impulse_pullbacks": impulses,
    }, handle, separators=(",", ":"))
  print(SEED, len(cycles), "cycles,", len(breakouts), "breakout,", len(sweeps), "range sweep,", len(impulses), "impulse pullback")


if __name__ == "__main__":
  main()
