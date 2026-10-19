"""Run the worker's own opposing-barrier admission over every replayed opportunity and write the decisions.

Run from ``algo-bot`` of the checkout under test, once per (code revision, book dump):

    python ../analysis-engine/test/f7barrier/decide_dump.py --books /tmp/dec-after --out /tmp/decisions.old.jsonl

One line per opportunity: whether it could be admitted at all (a resting zone candidate is never admitted: the adapter requires a
confirmed reaction), the decision (allowed, reason, room), the barrier that decided and its decisive member, the barriers set aside
as lying behind the entry, and the geometry needed to simulate it. ``compare_corrections.py`` joins several of these files.
"""
import argparse
import glob
import json
import os
import sys
import time


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--books", required=True)
  ap.add_argument("--out", required=True)
  a = ap.parse_args()
  for k, v in {"TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi", "TELEGRAM_CHAT_ID": "-100123456789",
               "DATABASE_URL": "postgresql://apexvoid:apexvoid@localhost:5/signals", "POSTGRES_PASSWORD": "x"}.items():
    os.environ.setdefault(k, v)
  sys.path.insert(0, os.getcwd())
  from app.autotrade import units
  from app.autotrade.go_opportunity_policy import REVIEWED_SCOPES
  from app.autotrade.go_zone_book import parse_zone_book
  from app.autotrade.strategy_taxonomy import bypasses_opposing_structure_gates, is_technique_or_confluence
  from app.autotrade.structural_target_room import (
    describe_blocking_barrier, evaluate_structural_target_room, filter_shared_boundary_opposing_entries, zone_proximal_room_reference)
  from app.core import instrument_geometry
  from app.core.config import runtime_config

  min_capped = float(runtime_config.auto_algo.actionability.target_room.minimum_capped_target_pips)
  cost = float(runtime_config.execution.policy.execution_cost_pips)
  now = int(time.time())
  with open(a.out, "w") as out:
    for path in sorted(glob.glob(os.path.join(a.books, "*.decisions.jsonl"))):
      capture = os.path.basename(path)[: -len(".decisions.jsonl")]
      for line in open(path):
        rec = json.loads(line)
        symbol, price = rec["symbol"], float(rec["price"])
        book = parse_zone_book(json.dumps({"schema_version": 1, "symbol": symbol, "generated_at": now, "entries": [], "barriers": rec["barriers"]}),
                               symbol=symbol, now=now)
        if not book.available:
          raise SystemExit(f"{capture} {rec['bar']}: {book.reason}")
        for c in rec["candidates"]:
          profile = REVIEWED_SCOPES[c["strategy"]]
          if bypasses_opposing_structure_gates(profile.legacy_strategy):
            continue
          atr = float(c.get("atr") or 0.0)
          if atr <= 0:
            continue
          pip = units.pip_size(symbol)
          low, high = float(c["entry_low"]), float(c["entry_high"])
          planned, source = zone_proximal_room_reference(direction=c["direction"].upper(), spot_price=price, candidate_entry_low=low,
                                                         candidate_entry_high=high, pip_size=pip, atr=atr)
          proximal = low if c["direction"].upper() == "SELL" else high
          targets = sorted({max(0, round(abs(t - proximal) / pip)) for t in c["targets"]})
          entries, shared = filter_shared_boundary_opposing_entries(book.entries, direction=c["direction"], candidate_entry_low=low, candidate_entry_high=high,
                                                                    pip_size=pip, atr=atr, planned_entry=planned)
          d = evaluate_structural_target_room(
            direction=c["direction"], planned_entry_price=planned, candidate_entry_low=low, candidate_entry_high=high, configured_target_pips=targets,
            actionable_entries=entries, atr=atr, pip_size=pip, barrier_buffer_atr=instrument_geometry.structural_barrier_buffer_atr(symbol),
            min_capped_target_pips=min_capped, execution_cost_pips=cost, room_reference_source=source, executable_entry_price=price,
            shared_boundary_state=shared, allow_same_wall_overlap=is_technique_or_confluence(profile.legacy_strategy))
          row = {"capture": capture, "bar": rec["bar"], "symbol": symbol, "id": c["id"], "strategy": c["strategy"], "direction": c["direction"],
                 "admissible": (not profile.requires_reaction) or bool(c.get("confirmed_reaction")), "allowed": bool(d.allowed), "reason": d.reason_code,
                 "planned": planned, "price": price, "entry_low": low, "entry_high": high, "targets_pips": targets, "invalidation": c["invalidation"],
                 "stop_envelope": c.get("stop_envelope"), "atr": atr, "usable_room_pips": (d.measured or {}).get("usable_room_pips"),
                 "raw_room_pips": (d.measured or {}).get("raw_room_pips"),
                 "behind_excluded": ((d.measured or {}).get("shared_boundary_state") or {}).get("behind_entry_excluded", 0)}
          if d.opposing_entry is not None:
            desc = describe_blocking_barrier(d.opposing_entry, direction=c["direction"], planned_entry=planned, candidate_entry_low=low,
                                             candidate_entry_high=high, reason_code=d.reason_code, measured=d.measured)
            row["barrier"] = [desc["barrier_low"], desc["barrier_high"]]
            row["member_count"] = desc["member_count"]
            row["decisive_member"] = desc["decisive_member"]
          if c["strategy"] == "range_edge":
            row["range"] = c.get("range")
          out.write(json.dumps(row, separators=(",", ":")) + "\n")


main()
