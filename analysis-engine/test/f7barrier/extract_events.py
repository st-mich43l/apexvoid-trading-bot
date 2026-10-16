#!/usr/bin/env python3
"""Turn a read-only production snapshot into the admission events the F-7 replay evaluates.

Inputs (all produced by read-only queries; nothing here talks to production):
  --lifecycle   redis dump of ``auto_trade:lifecycle:go_opp_*`` lists: ``__K__<key>`` then one JSON entry per line
  --opps        analysis_opportunity_events creation envelopes flattened to one JSON object per line (see the report)
  --fills       auto_trade_fills, one JSON object per line
  --results     auto_trade_results, one JSON object per line
Output: ``events.jsonl`` (one admission decision per line) and ``bars.json`` (symbol -> M5 bar-open times to dump).

An *event* is one place the production target-room check ran and left a trace:
  * ``rejected``: the lifecycle entry the worker wrote when ``evaluate_structural_target_room`` hard-blocked a plan. Its
    ``measured`` block carries the exact planned entry, candidate band, ATR-derived buffer and the barrier that decided it.
  * ``published``: a TradePlan V8 was published, i.e. the check let the plan through. The geometry the check saw is not
    logged for these, so it is rebuilt from the opportunity envelope and the fill (``geometry_source`` says which).
"""
import argparse
import collections
import datetime
import json
import sys

BARRIER_REASONS = {
  "opposing_entry_overlap", "opposing_entry_contained", "opposing_major_no_room",
  "opposing_barrier_no_target", "opposing_barrier_room_below_cost",
}
PIP = {"XAU": 0.1, "EURUSD": 1e-4, "GBPUSD": 1e-4, "USDJPY": 0.01, "GBPJPY": 0.01}


def jsonl(path):
  with open(path) as handle:
    for line in handle:
      line = line.strip()
      if line.startswith("{"):
        yield json.loads(line)


def lifecycle(path):
  key = None
  with open(path) as handle:
    for line in handle:
      if line.startswith("__K__"):
        key = line.strip()[5:]
      elif line.startswith("{"):
        yield key, json.loads(line)


def bar_open(ts, minutes=5):
  """Open time of the last M5 bar already closed at ``ts`` (the book Redis held)."""
  return ((int(ts) // (minutes * 60)) * minutes * 60) - minutes * 60


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--lifecycle", required=True)
  parser.add_argument("--opps", required=True)
  parser.add_argument("--fills", required=True)
  parser.add_argument("--results", required=True)
  parser.add_argument("--out", required=True)
  parser.add_argument("--bars", required=True)
  parser.add_argument("--since", type=int, default=0, help="drop events before this Unix time")
  parser.add_argument("--until", type=int, default=0, help="drop events at or after this Unix time (0 = none)")
  args = parser.parse_args()

  opps = {row["id"]: row for row in jsonl(args.opps)}
  fills = collections.defaultdict(list)
  for row in jsonl(args.fills):
    fills[row["group_id"]].append(row)
  results = {row["group_id"]: row for row in jsonl(args.results)}

  events, seen = [], set()
  skipped = collections.Counter()
  for _, entry in lifecycle(args.lifecycle):
    match_id = entry.get("correlation_id") or entry.get("match_id")
    if not match_id or not match_id.startswith("go_opp_"):
      continue
    opp = opps.get(match_id[3:])
    ts = int(entry.get("timestamp") or 0)
    if ts < args.since or (args.until and ts >= args.until):
      continue
    state, reason = entry.get("state"), entry.get("reason_code") or ""
    if state == "rejected" and reason in BARRIER_REASONS:
      m = entry.get("measured") or {}
      ev = m.get("opposing_evidence") or {}
      if opp is None or "planned_entry_price" not in m:
        skipped["rejection_without_opportunity_or_geometry"] += 1
        continue
      dedupe = ("rej", match_id, ts, reason)
      if dedupe in seen:
        continue
      seen.add(dedupe)
      events.append({
        "kind": "rejected", "match_id": match_id, "symbol": entry["symbol"], "ts": ts, "bar": bar_open(ts),
        "strategy": opp["strategy"], "direction": opp["direction"], "opportunity_created_at": opp["created_at"],
        "reason_code": reason, "geometry_source": "logged",
        "planned_entry": m["planned_entry_price"], "entry_low": m["candidate_entry_low"], "entry_high": m["candidate_entry_high"],
        "executable_entry": m.get("executable_entry_price"), "room_reference_source": m.get("room_reference_source"),
        "atr": opp["atr"], "buffer_price": m.get("barrier_buffer_price"), "cost_pips": m.get("execution_cost_pips"),
        "min_capped_target_pips": m.get("min_capped_target_pips"), "targets_pips": m.get("configured_target_pips"),
        "logged_barrier": {
          "side": ev.get("zone_side"), "low": m.get("opposing_low"), "high": m.get("opposing_high"), "tier": m.get("opposing_tier"),
          "touches": ev.get("touches"), "zone_score": ev.get("zone_score"), "strength_score": ev.get("strength_score"),
        },
        "logged_room": {"raw_pips": m.get("raw_room_pips"), "room_pips": m.get("room_pips"), "usable_room_pips": m.get("usable_room_pips")},
        "tags": opp.get("evidence"), "bias": opp.get("bias"), "structure_timeframe": opp.get("structure_timeframe"),
      })
    elif state == "candidate_published":
      m = entry.get("measured") or {}
      plan_id = m.get("plan_id") or ""
      group = plan_id if plan_id in fills else plan_id
      if opp is None:
        skipped["published_without_opportunity"] += 1
        continue
      fill_rows = fills.get(group, [])
      entry_price = None
      if fill_rows:
        entry_price = sum(r["entry_price"] * r["volume"] for r in fill_rows) / sum(r["volume"] for r in fill_rows)
      targets = [float(x) for x in (m.get("targets") or [])]
      stop = float(m["stop_price"]) if m.get("stop_price") not in (None, "") else None
      mid = (opp["entry_low"] + opp["entry_high"]) / 2
      planned = entry_price if entry_price is not None else mid
      pip = PIP.get(entry["symbol"], 1e-4)
      direction = opp["direction"]
      pips = sorted({int(round(((t - planned) if direction == "BUY" else (planned - t)) / pip)) for t in targets} - {0})
      pips = [p for p in pips if p > 0]
      dedupe = ("pub", match_id)
      if dedupe in seen:
        continue
      seen.add(dedupe)
      result = results.get(group)
      events.append({
        "kind": "published", "match_id": match_id, "symbol": entry["symbol"], "ts": ts, "bar": bar_open(ts),
        "strategy": opp["strategy"], "direction": direction, "opportunity_created_at": opp["created_at"],
        "reason_code": "published", "geometry_source": "fill" if entry_price is not None else "band_mid",
        "planned_entry": planned, "entry_low": opp["entry_low"], "entry_high": opp["entry_high"], "executable_entry": entry_price,
        "atr": opp["atr"], "targets_pips": pips, "stop_price": stop, "entry_type": m.get("entry_type"),
        "filled": bool(fill_rows), "result_pips": None if result is None else result.get("result_pips"),
        "exit_path": None if result is None else result.get("exit_path"),
        "tags": opp.get("evidence"), "bias": opp.get("bias"), "structure_timeframe": opp.get("structure_timeframe"),
      })

  events.sort(key=lambda e: (e["ts"], e["match_id"], e["kind"]))
  with open(args.out, "w") as out:
    for ev in events:
      out.write(json.dumps(ev, separators=(",", ":")) + "\n")
  bars = collections.defaultdict(set)
  for ev in events:
    bars[ev["symbol"]].add(ev["bar"])
  with open(args.bars, "w") as out:
    json.dump({sym: sorted(v) for sym, v in bars.items()}, out)
  by = collections.Counter((e["kind"], e["reason_code"]) for e in events)
  span = (datetime.datetime.fromtimestamp(events[0]["ts"], datetime.UTC), datetime.datetime.fromtimestamp(events[-1]["ts"], datetime.UTC)) if events else None
  print(json.dumps({"events": len(events), "by_kind_reason": {f"{k}/{r}": v for (k, r), v in by.items()},
                    "skipped": dict(skipped), "span": [str(s) for s in span] if span else None,
                    "bars": {k: len(v) for k, v in bars.items()}}, indent=1), file=sys.stderr)


if __name__ == "__main__":
  main()
