"""Read-only execution-quality audit (P3).

Measures how a technically valid opportunity became an actual trade, from records the
system already keeps; nothing is simulated and nothing is written to production.

Inputs (all exported read-only from production):

* ``--plans``    JSONL of the ``execution:trade_plans`` Redis stream, one
                 ``{"id": stream_id, "f": {"payload": "<TradePlan JSON>"}}`` per line.
* ``--fills``    CSV of ``auto_trade_fills`` (one row per plan group: the volume-weighted
                 entry price and the summed volume, so per-leg fills are NOT available).
* ``--results``  CSV of ``auto_trade_results`` (closed trades).
* ``--opps``     CSV of ``analysis_opportunities`` (opportunity_id, symbol, strategy, ...).
* ``--algo-log`` gzip of the algo-bot log lines for execution confirmation, plan build
                 rejections, plan publication and Go adaptation.

Records that are missing are reported as unavailable, never filled in.

    python -m tools.execution_audit --plans plans.jsonl --fills fills.csv \
      --results results.csv --opps opps.csv --algo-log algo.log.gz --out audit.json
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PIP = {"XAU": 0.1, "EURUSD": 0.0001, "GBPUSD": 0.0001, "GBPJPY": 0.01, "USDJPY": 0.01}
STRATEGY_NAME = {
  "key_level": "Key Level", "supply": "Supply Demand", "demand": "Supply Demand",
  "order_block": "Order Block", "fvg": "FVG", "ifvg": "iFVG", "crt": "CRT",
  "confluence_zone": "Confluence Zone", "flip_zone": "Flip Zone",
  "session_level": "Session Level", "trendline": "Trendline", "box_breakout": "Box Breakout",
  "break_retest": "Break & Retest", "momentum_ride": "Momentum Ride", "snap_back": "Snap-Back",
  "liquidity_sweep": "Liquidity Sweep", "range_edge": "Range Edge Scalp",
  "fade_scalp": "Fade Scalp", "range_sweep": "Range Sweep Scalp",
  "impulse_pullback": "Impulse Pullback Scalp", "scalp_breakout_retest": "Breakout Retest Scalp",
}
_TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3})")
_KV = re.compile(r"(\w+)=([^\s]+)")


def _epoch(line: str) -> float | None:
  match = _TS.match(line)
  if not match:
    return None
  moment = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
  return moment.timestamp() + int(match.group(2)) / 1000.0


def _stats(values: Iterable[float]) -> dict[str, Any]:
  data = sorted(v for v in values if v is not None)
  if not data:
    return {"n": 0}
  return {
    "n": len(data),
    "mean": round(statistics.fmean(data), 3),
    "median": round(statistics.median(data), 3),
    "p90": round(data[int(0.9 * (len(data) - 1))], 3),
    "max": round(data[-1], 3),
    "min": round(data[0], 3),
  }


def load_plans(path: Path) -> dict[str, dict]:
  plans: dict[str, dict] = {}
  for line in path.read_text().splitlines():
    if not line.strip():
      continue
    row = json.loads(line)
    plan = json.loads(row["f"]["payload"])
    plan["_stream_ts"] = int(str(row["id"]).split("-")[0]) / 1000.0
    plans[plan["plan_id"]] = plan
  return plans


def load_csv(path: Path) -> list[dict[str, str]]:
  with path.open() as handle:
    return list(csv.DictReader(handle))


def parse_algo_log(path: Path) -> dict[str, dict]:
  """Per setup: adaptation time, first in-zone/trigger times, publication, rejections."""
  setups: dict[str, dict] = defaultdict(lambda: {
    "adapted": None, "in_zone": None, "trigger": None, "published": None,
    "rejections": Counter(), "terminal_rejection": None, "withdrawn": None, "strategy": None,
    "symbol": None,
  })
  opener = gzip.open if str(path).endswith(".gz") else open
  with opener(path, "rt", errors="replace") as handle:
    for line in handle:
      stamp = _epoch(line)
      if stamp is None:
        continue
      if "Go opportunity adapted" in line:
        found = dict(_KV.findall(line))
        match_id = found.get("match", "")
        if match_id:
          setups[match_id]["adapted"] = stamp
      elif "v8 execution confirmation" in line:
        found = dict(_KV.findall(line))
        setup = found.get("setup_id", "")
        if not setup:
          continue
        record = setups[setup]
        record["symbol"] = found.get("symbol")
        phase = found.get("phase")
        if phase == "in_zone_waiting_m1" and record["in_zone"] is None:
          record["in_zone"] = stamp
        elif phase == "trigger_ready" and record["trigger"] is None:
          record["trigger"] = stamp
        elif phase == "published" and record["published"] is None:
          record["published"] = stamp
      elif "v8 build rejected" in line:
        found = dict(_KV.findall(line))
        setup = found.get("setup_id", "")
        reason = found.get("reason", "unknown")
        if setup:
          setups[setup]["rejections"][reason] += 1
          setups[setup]["symbol"] = found.get("symbol")
          if "terminal=" in line and not re.search(r"terminal=(none|None|False)", line):
            setups[setup]["terminal_rejection"] = reason
      elif "match_withdrawn" in line:
        found = dict(_KV.findall(line))
        opportunity = found.get("opportunity", "")
        if opportunity.startswith("opp_"):
          setups["go_" + opportunity]["withdrawn"] = stamp
  return setups


def funnel(setups: dict[str, dict], opps: dict[str, dict], plans: dict[str, dict],
           fills: dict[str, list[dict]]) -> dict[str, Any]:
  table: dict[str, Counter] = defaultdict(Counter)
  rejection_reasons: dict[str, Counter] = defaultdict(Counter)
  for setup, record in setups.items():
    if record["adapted"] is None and record["in_zone"] is None and not record["rejections"]:
      continue
    opp = opps.get(setup[3:], {})
    strategy = STRATEGY_NAME.get(opp.get("strategy", ""), opp.get("strategy") or "unknown")
    row = table[strategy]
    row["adapted_or_seen"] += 1
    if record["in_zone"] is not None or record["trigger"] is not None:
      row["reached_entry_zone"] += 1
    plan_id = "v8:" + setup
    if record["published"] is not None or plan_id in plans:
      row["plan_published"] += 1
      if plan_id in fills:
        row["plan_filled"] += 1
      else:
        row["plan_never_filled"] += 1
    for reason, count in record["rejections"].items():
      rejection_reasons[strategy][reason] += 1
    if record["terminal_rejection"]:
      row["terminally_rejected"] += 1
    if (record["in_zone"] is None and record["trigger"] is None and record["published"] is None
        and not record["rejections"]):
      row["waited_for_zone_only"] += 1
  return {
    "by_strategy": {k: dict(v) for k, v in sorted(table.items())},
    "rejection_reasons_by_strategy": {k: dict(v.most_common()) for k, v in sorted(rejection_reasons.items())},
  }


def latencies(setups: dict[str, dict], plans: dict[str, dict], fills: dict[str, list[dict]]) -> dict[str, Any]:
  adapt_to_trigger, trigger_to_publish, publish_to_fill = [], [], []
  by_strategy: dict[str, list[float]] = defaultdict(list)
  for setup, record in setups.items():
    if record["adapted"] and record["trigger"]:
      adapt_to_trigger.append(record["trigger"] - record["adapted"])
    if record["trigger"] and record["published"]:
      trigger_to_publish.append(record["published"] - record["trigger"])
    plan = plans.get("v8:" + setup)
    rows = fills.get("v8:" + setup)
    if plan and rows:
      first_fill = min(int(r["filled_at"]) for r in rows)
      delay = first_fill - plan["_stream_ts"]
      publish_to_fill.append(delay)
      by_strategy[plan["analysis"]["strategy"]].append(delay)
  return {
    "adapted_to_entry_trigger_s": _stats(adapt_to_trigger),
    "trigger_to_plan_published_s": _stats(trigger_to_publish),
    "plan_published_to_first_fill_s": _stats(publish_to_fill),
    "plan_published_to_first_fill_by_strategy_s": {k: _stats(v) for k, v in sorted(by_strategy.items())},
  }


def plan_vs_fill(plans: dict[str, dict], fills: dict[str, list[dict]]) -> dict[str, Any]:
  """Entry price versus the plan, and fills versus declared legs. Fills are per group."""
  slippage: dict[str, list[float]] = defaultdict(list)
  outside_zone: dict[str, list[float]] = defaultdict(list)
  extra: list[dict] = []
  for plan_id, plan in plans.items():
    rows = fills.get(plan_id)
    if not rows:
      continue
    entry = plan["entry"]
    legs = entry.get("legs") or []
    declared = max(1, len(legs)) + (1 if entry.get("risk_leg") else 0)
    if len(rows) > declared:
      extra.append({"plan_id": plan_id, "declared": declared, "fills": len(rows)})
    pip = PIP[plan["symbol"]]
    buy = plan["analysis"]["direction"] == "BUY"
    low, high = entry.get("zone_low"), entry.get("zone_high")
    if legs:
      references = [float(leg["price"]) for leg in legs if leg.get("price")]
    elif entry.get("order_price"):
      references = [float(entry["order_price"])]
    elif low is not None:
      references = [float(high) if buy else float(low)]
    else:
      references = []
    for row in rows:
      fill = float(row["entry_price"])
      key = f"{plan['symbol']}|{entry['type']}"
      if references:
        reference = min(references, key=lambda value: abs(value - fill))
        slippage[key].append((fill - reference) / pip * (1 if buy else -1))
      if low is not None:
        low_f, high_f = float(low), float(high)
        outside_zone[key].append(((low_f - fill) if fill < low_f else (fill - high_f) if fill > high_f else 0.0) / pip)
  return {
    "fills_beyond_declared_legs": extra,
    "slippage_pips_positive_is_worse_nearest_declared_price": {k: _stats(v) for k, v in sorted(slippage.items())},
    "fill_distance_outside_declared_zone_pips": {k: _stats(v) for k, v in sorted(outside_zone.items())},
    "note": "auto_trade_fills holds one volume-weighted row per plan group; leg-level fills and "
            "ladder partial fills are not recoverable from it.",
  }


def stop_geometry(plans: dict[str, dict]) -> dict[str, Any]:
  """How far the executed stop sits from Go's technical invalidation, by strategy and symbol."""
  widen: dict[str, list[float]] = defaultdict(list)
  sources: dict[str, Counter] = defaultdict(Counter)
  risk: dict[str, list[float]] = defaultdict(list)
  for plan in plans.values():
    pip = PIP[plan["symbol"]]
    strategy = plan["analysis"]["strategy"]
    key = f"{strategy}|{plan['symbol']}"
    stop = float(plan["stop"]["price"])
    sources[strategy][plan["stop"]["source"]] += 1
    invalidation = (plan.get("source_structure") or {}).get("invalidation_price")
    if invalidation:
      widen[key].append(abs(stop - float(invalidation)) / pip)
    entry = plan["entry"]
    legs = [float(leg["price"]) for leg in entry.get("legs") or [] if leg.get("price")]
    prices = legs or ([float(entry["order_price"])] if entry.get("order_price") else
                      [float(entry["zone_low"]) if plan["analysis"]["direction"] == "SELL" else float(entry["zone_high"])]
                      if entry.get("zone_low") is not None else [])
    if prices:
      buy = plan["analysis"]["direction"] == "BUY"
      worst = max(prices) if buy else min(prices)
      risk[key].append(abs(worst - stop) / pip)
  return {
    "stop_distance_beyond_go_invalidation_pips": {k: _stats(v) for k, v in sorted(widen.items())},
    "planned_risk_pips_nearest_leg_to_stop": {k: _stats(v) for k, v in sorted(risk.items())},
    "stop_sources_by_strategy": {k: dict(v) for k, v in sorted(sources.items())},
  }


def declared_legs(plans: dict[str, dict]) -> dict[str, Any]:
  """What each strategy's plans declare: order type, legs, and risk leg."""
  table: dict[str, Counter] = defaultdict(Counter)
  for plan in plans.values():
    entry = plan["entry"]
    label = f"{plan['symbol']}|{entry['type']}|legs={len(entry.get('legs') or [])}|risk_leg={'yes' if entry.get('risk_leg') else 'no'}"
    table[plan["analysis"]["strategy"]][label] += 1
  return {k: dict(v) for k, v in sorted(table.items())}


def target_ladders(plans: dict[str, dict]) -> dict[str, Any]:
  table: dict[str, Counter] = defaultdict(Counter)
  for plan in plans.values():
    table[plan["analysis"]["strategy"]][f"{plan['symbol']}|targets={len(plan['targets'])}"] += 1
  return {k: dict(v) for k, v in sorted(table.items())}


def outcomes(results: list[dict[str, str]]) -> dict[str, Any]:
  by: dict[str, list[dict]] = defaultdict(list)
  for row in results:
    if row.get("trade_stream") != "algo_auto":
      continue
    by[f"{row['setup_type']}|{row['symbol']}"].append(row)
  out = {}
  for key, rows in sorted(by.items()):
    stops = [float(r["stop_pips"]) for r in rows if r.get("stop_pips")]
    realized = [float(r["realized_pips"]) for r in rows if r.get("realized_pips") not in ("", None) and float(r["realized_pips"]) != 0]
    out[key] = {
      "closed": len(rows),
      "stop_pips": _stats(stops),
      "with_realized_pips": len(realized),
      "exit_paths": dict(Counter(r.get("exit_path") or "unknown" for r in rows)),
      "booked_tp_count": dict(Counter(r.get("booked_tp_count") or "0" for r in rows)),
    }
  return out


def run(args: argparse.Namespace) -> dict[str, Any]:
  plans = load_plans(Path(args.plans))
  fill_rows = load_csv(Path(args.fills))
  fills: dict[str, list[dict]] = defaultdict(list)
  for row in fill_rows:
    fills[row["group_id"]].append(row)
  opps = {row["opportunity_id"]: row for row in load_csv(Path(args.opps))} if args.opps else {}
  setups = parse_algo_log(Path(args.algo_log)) if args.algo_log else {}
  results = load_csv(Path(args.results)) if args.results else []
  report = {
    "inputs": {
      "plans": len(plans), "fill_groups": len(fills), "opportunities": len(opps),
      "setups_in_log": len(setups), "closed_results": len(results),
    },
    "declared_entry_shapes": declared_legs(plans),
    "target_ladders": target_ladders(plans),
    "stop_geometry": stop_geometry(plans),
    "plan_vs_fill": plan_vs_fill(plans, fills),
    "latency": latencies(setups, plans, fills),
    "funnel": funnel(setups, opps, plans, fills),
    "closed_outcomes": outcomes(results),
  }
  return report


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--plans", required=True)
  parser.add_argument("--fills", required=True)
  parser.add_argument("--results", default="")
  parser.add_argument("--opps", default="")
  parser.add_argument("--algo-log", default="")
  parser.add_argument("--out", default="")
  args = parser.parse_args()
  report = run(args)
  text = json.dumps(report, indent=2, sort_keys=True)
  if args.out:
    Path(args.out).write_text(text + "\n")
  else:
    print(text)


if __name__ == "__main__":
  main()
