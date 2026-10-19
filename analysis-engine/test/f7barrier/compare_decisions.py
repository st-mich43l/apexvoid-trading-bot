"""Prove that a change moved no trading decision: compare two ``TestDumpDecisionInputs`` runs (before / after).

Run from ``algo-bot`` of the checkout under test (the decisions are made by its shipped code, nothing is re-implemented):

    python ../analysis-engine/test/f7barrier/compare_decisions.py --before /tmp/before --after /tmp/after --out compare.json

Per capture and per M5 bar that produced an opportunity it checks, in this order:

1. the opportunities are identical: same ids, strategies, directions, entry bands, invalidation, targets, quality, stop envelope and the
   sha256 of the complete serialized opportunity;
2. the published barriers are identical once the additive ``members`` provenance is set aside: bands, tiers, scores, touches,
   source timeframes and order;
3. the book is read by the real ``parse_zone_book`` in both runs and the real worker sequence (``filter_shared_boundary_opposing_entries``
   then ``evaluate_structural_target_room``) decides every opportunity on both books; the decisions (allowed, hard block, reason, the
   complete ``measured`` dict) must be equal;
4. the provenance is self-consistent: the members of a barrier tile its band exactly, and the member the diagnostic names as decisive
   is a member of the barrier that blocked.

Exit status 1 when any check fails.
"""
import argparse
import collections
import glob
import json
import os
import sys
import time


def load(directory):
  runs = {}
  for path in sorted(glob.glob(os.path.join(directory, "*.decisions.jsonl"))):
    name = os.path.basename(path)[: -len(".decisions.jsonl")]
    runs[name] = [json.loads(line) for line in open(path)]
  return runs


def strip(barriers):
  return [{k: v for k, v in b.items() if k != "members"} for b in barriers]


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--before", required=True)
  ap.add_argument("--after", required=True)
  ap.add_argument("--out", required=True)
  ap.add_argument("--examples", type=int, default=6)
  args = ap.parse_args()
  for key, value in {"TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi", "TELEGRAM_CHAT_ID": "-100123456789",
                     "DATABASE_URL": "postgresql://apexvoid:apexvoid@localhost:5/signals", "POSTGRES_PASSWORD": "x"}.items():
    os.environ.setdefault(key, value)
  sys.path.insert(0, os.getcwd())
  from app.autotrade import units
  from app.autotrade.go_opportunity_policy import REVIEWED_SCOPES
  from app.autotrade.go_zone_book import parse_zone_book
  from app.autotrade.strategy_taxonomy import bypasses_opposing_structure_gates, is_technique_or_confluence
  from app.autotrade.structural_target_room import (
    barrier_log_summary,
    describe_blocking_barrier,
    evaluate_structural_target_room,
    filter_shared_boundary_opposing_entries,
    zone_proximal_room_reference,
  )
  from app.core import instrument_geometry
  from app.core.config import runtime_config

  min_capped = float(runtime_config.auto_algo.actionability.target_room.minimum_capped_target_pips)
  cost = float(runtime_config.execution.policy.execution_cost_pips)
  now = int(time.time())

  def read(symbol, barriers):
    doc = json.dumps({"schema_version": 1, "symbol": symbol, "generated_at": now, "entries": [], "barriers": barriers})
    book = parse_zone_book(doc, symbol=symbol, now=now)
    if not book.available:
      raise SystemExit(f"{symbol}: the book was unreadable: {book.reason}")
    return book

  def decide(c, symbol, price, book):
    pip = units.pip_size(symbol)
    profile = REVIEWED_SCOPES[c["strategy"]]
    atr = float(c.get("atr") or 0.0)
    if atr <= 0:
      return None
    low, high = float(c["entry_low"]), float(c["entry_high"])
    planned, source = zone_proximal_room_reference(direction=c["direction"].upper(), spot_price=price, candidate_entry_low=low,
                                                   candidate_entry_high=high, pip_size=pip, atr=atr)
    proximal = low if c["direction"].upper() == "SELL" else high
    targets = sorted({max(0, round(abs(t - proximal) / pip)) for t in c["targets"]})
    entries, shared = filter_shared_boundary_opposing_entries(book.entries, direction=c["direction"], candidate_entry_low=low,
                                                              candidate_entry_high=high, pip_size=pip, atr=atr, planned_entry=planned)
    decision = evaluate_structural_target_room(
      direction=c["direction"], planned_entry_price=planned, candidate_entry_low=low, candidate_entry_high=high,
      configured_target_pips=targets, actionable_entries=entries, atr=atr, pip_size=pip,
      barrier_buffer_atr=instrument_geometry.structural_barrier_buffer_atr(symbol), min_capped_target_pips=min_capped,
      execution_cost_pips=cost, room_reference_source=source, executable_entry_price=price, shared_boundary_state=shared,
      allow_same_wall_overlap=is_technique_or_confluence(profile.legacy_strategy))
    return decision, planned, low, high

  before, after = load(args.before), load(args.after)
  fail = collections.Counter()
  total = collections.Counter()
  examples = []
  by_reason = collections.Counter()
  provenance = collections.Counter()
  attribution = collections.Counter()
  member_counts = []
  examples_attr = []
  sample_count = collections.Counter()
  for name in sorted(set(before) | set(after)):
    if name not in before or name not in after:
      fail["capture_missing_in_one_run"] += 1
      continue
    b_rows, a_rows = before[name], after[name]
    total["captures"] += 1
    if len(b_rows) != len(a_rows):
      fail["bar_count_differs"] += 1
    for b, a in zip(b_rows, a_rows):
      total["bars"] += 1
      if (b["bar"], b["symbol"]) != (a["bar"], a["symbol"]):
        fail["bar_order_differs"] += 1
        continue
      if b["candidates"] != a["candidates"]:
        fail["opportunities_differ"] += 1
      else:
        total["opportunities"] += len(a["candidates"])
      if strip(b["barriers"]) != strip(a["barriers"]):
        fail["barriers_differ"] += 1
      total["barriers"] += len(a["barriers"])
      for barrier in a["barriers"]:
        members = barrier.get("members") or []
        if not members:
          provenance["barrier_without_members"] += 1
          continue
        ordered = sorted(members, key=lambda m: (m["low"], m["high"]))
        lo, hi = min(m["low"] for m in members), max(m["high"] for m in members)
        tiles = abs(lo - barrier["low"]) < 1e-9 and abs(hi - barrier["high"]) < 1e-9
        gapless = all(ordered[i + 1]["low"] <= max(m["high"] for m in ordered[: i + 1]) + 1e-9 for i in range(len(ordered) - 1))
        in_order = [m["low"] for m in members] == sorted(m["low"] for m in members)
        provenance["barriers_with_members"] += 1
        provenance["members"] += len(members)
        provenance["multi_member"] += len(members) > 1
        if not (tiles and gapless and in_order):
          fail["members_do_not_tile_the_barrier"] += 1
        if any(not m.get("id") or not m.get("state") for m in members):
          fail["member_without_id_or_state"] += 1
      symbol, price = a["symbol"], float(a["price"])
      book_b, book_a = read(symbol, b["barriers"]), read(symbol, a["barriers"])
      for c in a["candidates"]:
        if bypasses_opposing_structure_gates(REVIEWED_SCOPES[c["strategy"]].legacy_strategy):
          total["bypassed_m1_scalp"] += 1
          continue
        got_b, got_a = decide(c, symbol, price, book_b), decide(c, symbol, price, book_a)
        if got_b is None or got_a is None:
          total["no_atr"] += 1
          continue
        (d_b, *_), (d_a, planned, low, high) = got_b, got_a
        total["decisions"] += 1
        same = (d_b.allowed, d_b.hard_block, d_b.reason_code, d_b.effective_target_pips, d_b.fitted_targets_pips, d_b.measured) == \
               (d_a.allowed, d_a.hard_block, d_a.reason_code, d_a.effective_target_pips, d_a.fitted_targets_pips, d_a.measured)
        if not same:
          fail["decisions_differ"] += 1
          if len(examples) < args.examples:
            examples.append({"capture": name, "bar": a["bar"], "candidate": c["id"], "before": d_b.reason_code, "after": d_a.reason_code})
        if not d_a.allowed and d_a.opposing_entry is not None:
          total["blocked"] += 1
          by_reason[d_a.reason_code] += 1
          described = describe_blocking_barrier(
            d_a.opposing_entry, direction=c["direction"], planned_entry=planned, candidate_entry_low=low, candidate_entry_high=high,
            reason_code=d_a.reason_code, measured=d_a.measured)
          if described["members_published"]:
            provenance["blocked_with_members"] += 1
            members_all = d_a.opposing_entry.members
            member_counts.append(len(members_all))
            attribution["blocks"] += 1
            attribution["merged_wall" if len(members_all) > 1 else "single_zone"] += 1
            if d_a.reason_code in ("opposing_entry_contained", "opposing_entry_overlap"):
              holders = [m for m in members_all if m["low"] <= planned <= m["high"]]
              attribution["entry_block"] += 1
              attribution["entry_held_by_one_member" if holders else "entry_held_only_by_the_union"] += 1
            dm = described["decisive_member"] or {}
            attribution["decisive_state:" + str(dm.get("state"))] += 1
            touches = dm.get("touches", 0)
            attribution["decisive_touches:" + ("0" if touches == 0 else "1" if touches == 1 else "2" if touches == 2 else "3+")] += 1
            if c["strategy"] == "range_edge":
              attribution["range_edge_blocks"] += 1
              tgt = max(c["targets"]) if c["direction"].upper() == "BUY" else min(c["targets"])
              lo_p, hi_p = min(planned, tgt), max(planned, tgt)
              on_path = dm and dm["high"] >= lo_p and dm["low"] <= hi_p
              attribution["range_edge_decisive_member_on_the_path_to_its_target" if on_path else "range_edge_decisive_member_beyond_its_target"] += 1
            if sample_count[symbol] < 60 and len(members_all) > 1:
              sample_count[symbol] += 1
              examples_attr.append({"capture": name, "bar": a["bar"], "symbol": symbol, "strategy": c["strategy"], "direction": c["direction"],
                                    "candidate": c["id"], "planned_entry": planned, "entry_low": low, "entry_high": high, "description": described})
            decisive = described["decisive_member"]
            if decisive is None or decisive.get("id") not in described["member_ids"]:
              fail["decisive_member_not_a_member"] += 1
            if len(described["member_ids"]) > 1:
              provenance["blocked_by_merged_wall"] += 1
          else:
            provenance["blocked_without_members"] += 1
          barrier_log_summary(described)
  result = {"before": args.before, "after": args.after, "totals": dict(total), "blocked_by_reason": dict(by_reason),
            "provenance": dict(provenance), "attribution": dict(attribution),
            "members_per_blocking_barrier": {"n": len(member_counts), "median": sorted(member_counts)[len(member_counts) // 2] if member_counts else None,
                                             "max": max(member_counts) if member_counts else None},
            "multi_member_block_samples": examples_attr, "failures": dict(fail), "failure_examples": examples,
            "identical": not fail}
  json.dump(result, open(args.out, "w"), indent=1)
  print(json.dumps(result, indent=1))
  sys.exit(0 if not fail else 1)


main()
