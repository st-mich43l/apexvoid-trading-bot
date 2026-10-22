"""What the engine's cross-side reconciliation does to admission decisions.

``barrier.Build`` merges same-side live zones into bands and then, of two opposite-side bands that overlap by at least half of either
width, drops the weaker whole (tier, then the band's strongest member), unless that would drop more than a third of the book. This
rebuilds the book from the raw live zones ``dump_test.go`` records at each bar (width gate, same-side union, reconciliation: the same
rules), checks the rebuild against the book the engine published, and then runs the worker's own target-room decision on every
admissible replayed opportunity with and without the reconciliation.

Run from ``algo-bot`` (zones: dump_test.go with F7_BARS; decisions: decisions_test.go + decide_dump.py):

    python ../analysis-engine/test/f7barrier/crossside_sensitivity.py --zones /tmp/zones --decisions /tmp/decisions.base.jsonl --out crossside.json
"""
import argparse
import collections
import glob
import json
import os
import statistics
import sys

PIP = {"XAU": 0.1, "EURUSD": 1e-4, "GBPUSD": 1e-4, "USDJPY": 0.01, "GBPJPY": 0.01}
LIVE = {"fresh", "touched", "partially_mitigated"}
MAX_ATR, MAX_PIPS = 2.0, 100.0  # config/execution.yml execution.policy.execution_zone_max_width_{atr,pips}


def build(rec, reconcile=True):
  pip = PIP[rec["symbol"]]
  raw = []
  for z in rec["raw_zones"]:
    if z["kind"] not in ("supply", "demand") or z["state"] not in LIVE:
      continue
    width = z["high"] - z["low"]
    if width <= 0 or z["atr"] <= 0 or width / z["atr"] > MAX_ATR or width / pip > MAX_PIPS:
      continue
    raw.append({"side": "buy" if z["kind"] == "demand" else "sell", "low": z["low"], "high": z["high"], "score": z["strength"], "touches": z["touches"], "members": [z]})
  merged = []
  for side in ("buy", "sell"):
    cur = None
    for b in sorted((r for r in raw if r["side"] == side), key=lambda r: (r["low"], r["high"])):
      if cur is not None and min(cur["high"], b["high"]) >= max(cur["low"], b["low"]):
        cur = {"side": side, "low": min(cur["low"], b["low"]), "high": max(cur["high"], b["high"]), "score": max(cur["score"], b["score"]),
               "touches": min(cur["touches"], b["touches"]), "members": cur["members"] + b["members"]}
      else:
        if cur is not None:
          merged.append(cur)
        cur = b
    if cur is not None:
      merged.append(cur)
  if not reconcile:
    return merged, set()
  drop = set()
  for i, first in enumerate(merged):
    if i in drop:
      continue
    for j in range(i + 1, len(merged)):
      second = merged[j]
      if j in drop or second["side"] == first["side"]:
        continue
      overlap = max(0.0, min(first["high"], second["high"]) - max(first["low"], second["low"]))

      def ratio_of(width):
        width = max(0.0, width)
        return overlap / width if width > 0 else (1.0 if overlap > 0 else 0.0)

      if max(ratio_of(first["high"] - first["low"]), ratio_of(second["high"] - second["low"])) < 0.5:
        continue
      drop.add(j if first["score"] >= second["score"] else i)
      if i in drop:
        break
  if not merged or len(drop) / len(merged) > 0.34:
    return merged, set()
  return [b for k, b in enumerate(merged) if k not in drop], drop


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--zones", required=True)
  ap.add_argument("--decisions", required=True)
  ap.add_argument("--out", required=True)
  a = ap.parse_args()
  for k, v in {"TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi", "TELEGRAM_CHAT_ID": "-100123456789",
               "DATABASE_URL": "postgresql://apexvoid:apexvoid@localhost:5/signals", "POSTGRES_PASSWORD": "x"}.items():
    os.environ.setdefault(k, v)
  sys.path.insert(0, os.getcwd())
  from app.autotrade import units
  from app.autotrade.go_opportunity_policy import REVIEWED_SCOPES
  from app.autotrade.strategy_taxonomy import is_technique_or_confluence
  from app.autotrade.structural_target_room import ZoneOpposingEntry, evaluate_structural_target_room, filter_shared_boundary_opposing_entries
  from app.core import instrument_geometry
  from app.core.config import runtime_config

  min_capped = float(runtime_config.auto_algo.actionability.target_room.minimum_capped_target_pips)
  cost = float(runtime_config.execution.policy.execution_cost_pips)
  zones = {}
  for path in glob.glob(os.path.join(a.zones, "*.go.jsonl")):
    for line in open(path):
      r = json.loads(line)
      key = (r["symbol"], r["bar"])
      if key not in zones or r["history_bars_m5"] > zones[key]["history_bars_m5"]:
        zones[key] = r

  report = collections.Counter()
  dropped_members, dropped_independent, width_ratio = [], [], []
  for rec in zones.values():
    unreconciled, _ = build(rec, reconcile=False)
    reconciled, drop = build(rec)
    near = lambda b: b["high"] >= rec["price"] - 6 * rec["atr"] and b["low"] <= rec["price"] + 6 * rec["atr"]
    published = {(x["side"], round(x["low"], 5), round(x["high"], 5)) for x in rec["barriers"] if near(x)}
    rebuilt = {(x["side"], round(x["low"], 5), round(x["high"], 5)) for x in reconciled if near(x)}
    report["bars"] += 1
    report["rebuild_equals_published"] += published == rebuilt
    if drop:
      report["bars_with_a_drop"] += 1
    for j in drop:
      d = unreconciled[j]
      if not near(d):
        continue
      report["near_price_barriers_dropped"] += 1
      winner = next((o for o in reconciled if o["side"] != d["side"] and min(o["high"], d["high"]) > max(o["low"], d["low"])), None)
      if winner is None:
        continue
      dropped_members.append(len(d["members"]))
      dropped_independent.append(sum(1 for m in d["members"] if min(m["high"], winner["high"]) <= max(m["low"], winner["low"])) / len(d["members"]))
      width_ratio.append((d["high"] - d["low"]) / max(1e-12, winner["high"] - winner["low"]))

  def entries(book):
    return tuple(ZoneOpposingEntry(side=b["side"], lo=b["low"], hi=b["high"], tier="zone", score=b["score"], touches=b["touches"]) for b in book)

  def decide(row, book):
    symbol, pip, atr = row["symbol"], units.pip_size(row["symbol"]), row["atr"]
    low, high = row["entry_low"], row["entry_high"]
    kept, shared = filter_shared_boundary_opposing_entries(entries(book), direction=row["direction"], candidate_entry_low=low, candidate_entry_high=high, pip_size=pip, atr=atr, planned_entry=row["planned"])
    d = evaluate_structural_target_room(
      direction=row["direction"], planned_entry_price=row["planned"], candidate_entry_low=low, candidate_entry_high=high, configured_target_pips=row["targets_pips"],
      actionable_entries=kept, atr=atr, pip_size=pip, barrier_buffer_atr=instrument_geometry.structural_barrier_buffer_atr(symbol), min_capped_target_pips=min_capped,
      execution_cost_pips=cost, room_reference_source=None, executable_entry_price=row["price"], shared_boundary_state=shared,
      allow_same_wall_overlap=is_technique_or_confluence(REVIEWED_SCOPES[row["strategy"]].legacy_strategy))
    return d.allowed, d.reason_code

  changed = collections.Counter()
  by_strategy = collections.Counter()
  for line in open(a.decisions):
    row = json.loads(line)
    if not row["admissible"]:
      continue
    rec = zones.get((row["symbol"], row["bar"]))
    if rec is None:
      report["decisions_without_zone_record"] += 1
      continue
    with_reconcile, without = decide(row, build(rec)[0]), decide(row, build(rec, reconcile=False)[0])
    if with_reconcile != (row["allowed"], row["reason"]):
      report["decisions_where_the_rebuild_differs_from_the_shipped_decision"] += 1
      continue
    report["decisions_compared"] += 1
    if with_reconcile != without:
      changed["admitted->blocked" if with_reconcile[0] and not without[0] else "blocked->admitted" if not with_reconcile[0] and without[0] else "reason_only"] += 1
      by_strategy[row["strategy"]] += 1
  report = dict(report)
  report["changed_without_reconciliation"] = dict(changed)
  report["changed_pct"] = round(100.0 * sum(changed.values()) / max(1, report.get("decisions_compared", 0)), 1)
  report["changed_by_strategy"] = dict(by_strategy.most_common())
  if dropped_members:
    report["dropped_barrier_median_members"] = statistics.median(dropped_members)
    report["dropped_barrier_mean_share_of_members_outside_the_winner"] = round(statistics.mean(dropped_independent), 3)
    report["dropped_over_winner_width_median"] = round(statistics.median(width_ratio), 2)
    report["dropped_over_winner_width_p90"] = round(sorted(width_ratio)[int(0.9 * len(width_ratio))], 2)
  json.dump(report, open(a.out, "w"), indent=1)
  print(json.dumps(report, indent=1))


main()
