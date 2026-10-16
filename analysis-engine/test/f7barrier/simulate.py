"""Hypothetical, causal triple-barrier outcome of each admission event. NOT a backtest of Algo Bot.

For a trade that was (or was not) admitted, what would a plain stop / +1R / +2R race have done from the moment the check ran?
It deliberately does not re-implement the TradePlan ladder, partial closes or break-even: those depend on live quotes the
captures do not hold. It is used to compare *groups of events against each other* (blocked vs admitted, blocked by a
fabricated-looking barrier vs a credible one), never to claim a profit figure.

Conservative choices (each one makes a blocked event look WORSE than it may have been, i.e. favors the barrier):
  * entry at the logged executable price shifted against the trade by the configured execution cost,
  * the first bar after the event is the first bar that can fill or stop (the bar containing the event is ignored),
  * a bar that touches both the stop and a target counts as the stop (intrabar ambiguity resolved against the trade),
  * the stop is the Go invalidation clamped to the opportunity's own stop envelope (floor/cap pips), as the planner does.

    simulate.py --events events.jsonl --opps opportunities.jsonl --testdata analysis-engine/testdata --out sim.jsonl
"""
import argparse
import glob
import json
import os

PIP = {"XAU": 0.1, "EURUSD": 1e-4, "GBPUSD": 1e-4, "USDJPY": 0.01, "GBPJPY": 0.01}
HORIZON_BARS = 144  # 12 h of M5


def load_candles(testdata):
  series = {}
  for path in sorted(glob.glob(os.path.join(testdata, "replay-*-production-capture-2026*.json"))):
    raw = json.load(open(path))
    rows = series.setdefault(raw["symbol"], {})
    for t, o, h, l, c, *_ in raw["timeframes"]["M5"]:
      rows[int(t)] = (float(o), float(h), float(l), float(c))
  return {s: sorted(rows.items()) for s, rows in series.items()}


def race(direction, entry, stop_dist, bars, targets):
  """First of stop / each target in R multiples over bars (open, high, low, close)."""
  out = {}
  for k in targets:
    out[k] = None
  stop = entry - stop_dist if direction == "BUY" else entry + stop_dist
  alive = set(targets)
  last_close = entry
  for (_, (o, h, l, c)) in bars:
    last_close = c
    stop_hit = l <= stop if direction == "BUY" else h >= stop
    for k in list(alive):
      level = entry + k * stop_dist if direction == "BUY" else entry - k * stop_dist
      target_hit = h >= level if direction == "BUY" else l <= level
      if stop_hit:  # ambiguity resolved against the trade
        out[k] = ("loss", -1.0)
        alive.discard(k)
      elif target_hit:
        out[k] = ("win", float(k))
        alive.discard(k)
    if not alive:
      break
  mtm = ((last_close - entry) if direction == "BUY" else (entry - last_close)) / stop_dist
  for k in alive:
    out[k] = ("censored", mtm)
  return out


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--events", required=True)
  parser.add_argument("--opps", required=True)
  parser.add_argument("--testdata", required=True)
  parser.add_argument("--out", required=True)
  parser.add_argument("--cost-pips", type=float, default=1.0)
  args = parser.parse_args()
  candles = load_candles(args.testdata)
  times = {s: [t for t, _ in rows] for s, rows in candles.items()}
  opps = {}
  for line in open(args.opps):
    if line.startswith("{"):
      row = json.loads(line)
      opps[row["id"]] = row
  import bisect
  n = 0
  with open(args.out, "w") as out:
    for line in open(args.events):
      ev = json.loads(line)
      opp = opps.get(ev["match_id"][3:])
      sym = ev["symbol"]
      if opp is None or sym not in candles:
        continue
      entry = ev.get("executable_entry") or ev["planned_entry"]
      pip = PIP[sym]
      env = opp.get("stop_envelope") or {}
      floor, cap = float(env.get("floor_pips") or 0), float(env.get("cap_pips") or 0)
      inv = opp.get("invalidation")
      if not inv or not floor or not cap:
        continue
      stop_pips = min(cap, max(floor, abs(entry - inv) / pip))
      eff = entry + args.cost_pips * pip if ev["direction"] == "BUY" else entry - args.cost_pips * pip
      first = bisect.bisect_right(times[sym], ev["ts"])  # first bar opening after the event
      horizon = candles[sym][first:first + HORIZON_BARS]
      if len(horizon) < 6:
        continue
      res = race(ev["direction"], eff, stop_pips * pip, horizon, (1.0, 2.0))
      out.write(json.dumps({"match_id": ev["match_id"], "ts": ev["ts"], "symbol": sym, "stop_pips": round(stop_pips, 2),
                            "bars": len(horizon), "r1": res[1.0], "r2": res[2.0]}, separators=(",", ":")) + "\n")
      n += 1
  print(json.dumps({"simulated": n}))


if __name__ == "__main__":
  main()
