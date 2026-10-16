"""Replay the real Algo Bot opposing-barrier admission against Models A, B and C for each production admission event.

Run from ``algo-bot`` of the checkout under test (it imports the shipped ``structural_target_room`` rules; nothing is
re-implemented here):

    python ../analysis-engine/test/f7barrier/replay_admission.py --events events.jsonl --books-dir out/f7 \
        --oracle-tag a1c77584 --out replay.jsonl

For each event the worker's own sequence is applied to each book with identical planned-entry geometry:
``filter_shared_boundary_opposing_entries`` then ``evaluate_structural_target_room`` (same-wall overlap allowed for the
technique/confluence strategies, buffer / cost floor / minimum-capped-target from the live configuration).

Books:  A = frozen scanner book, B = frozen TradePlan-time book (oracle_books.py), C = Go book (dump_test.go).
``logged`` re-runs the rules with only the barrier the production log named, which checks that the reconstructed geometry
reproduces the production decision before any book is trusted.
"""
import argparse
import collections
import glob
import json
import math
import os
import sys


def load_books(books_dir, tag):
  go, py = {}, {}
  for path in sorted(glob.glob(os.path.join(books_dir, "*.go.jsonl"))):
    for line in open(path):
      rec = json.loads(line)
      key = (rec["symbol"], rec["bar"])
      if key not in go or rec["history_bars_m5"] > go[key]["history_bars_m5"]:
        go[key] = rec
  for path in sorted(glob.glob(os.path.join(books_dir, f"*.{tag}.jsonl"))):
    for line in open(path):
      rec = json.loads(line)
      key = (rec["symbol"], rec["bar"])
      if key not in py or rec["history_bars_m5"] > py[key]["history_bars_m5"]:
        py[key] = rec
  return go, py


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--events", required=True)
  parser.add_argument("--books-dir", required=True)
  parser.add_argument("--oracle-tag", default="a1c77584")
  parser.add_argument("--extra-books", default="", help="credibility.py output: {symbol, bar, books:{name: barriers}} per line")
  parser.add_argument("--out", required=True)
  args = parser.parse_args()
  for key, value in {
    "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "DATABASE_URL": "postgresql://apexvoid:apexvoid@localhost:5/signals",
    "POSTGRES_PASSWORD": "x",
  }.items():
    os.environ.setdefault(key, value)
  sys.path.insert(0, os.getcwd())
  from app.autotrade import units
  from app.autotrade.go_opportunity_policy import REVIEWED_SCOPES
  from app.autotrade.strategy_taxonomy import bypasses_opposing_structure_gates, is_technique_or_confluence
  from app.autotrade.structural_target_room import (
    ZoneOpposingEntry,
    evaluate_structural_target_room,
    filter_shared_boundary_opposing_entries,
  )
  from app.core import instrument_geometry
  from app.core.config import runtime_config

  go, py = load_books(args.books_dir, args.oracle_tag)
  extra = {}
  if args.extra_books:
    for line in open(args.extra_books):
      row = json.loads(line)
      extra[(row["symbol"], row["bar"])] = row["books"]
  min_capped = float(runtime_config.auto_algo.actionability.target_room.minimum_capped_target_pips)
  cost_cfg = float(runtime_config.execution.policy.execution_cost_pips)

  def entries(rows):
    return tuple(
      ZoneOpposingEntry(side=r["side"], lo=float(r["low"]), hi=float(r["high"]), tier=r.get("tier") or "zone",
                        score=float(r.get("score") or 0.0), touches=int(r.get("touches") or 0), mitigated=bool(r.get("mitigated")))
      for r in rows
    )

  def decide(ev, rows):
    sym, pip = ev["symbol"], units.pip_size(ev["symbol"])
    profile = REVIEWED_SCOPES[ev["strategy"]]
    name = profile.legacy_strategy
    atr = float(ev["atr"])
    planned = float(ev["planned_entry"])
    low, high = float(ev["entry_low"]), float(ev["entry_high"])
    room_entries, shared = filter_shared_boundary_opposing_entries(
      entries(rows), direction=ev["direction"], candidate_entry_low=low, candidate_entry_high=high, pip_size=pip, atr=atr, planned_entry=planned)
    decision = evaluate_structural_target_room(
      direction=ev["direction"], planned_entry_price=planned, candidate_entry_low=low, candidate_entry_high=high,
      configured_target_pips=ev.get("targets_pips") or [], actionable_entries=room_entries, atr=atr, pip_size=pip,
      barrier_buffer_atr=instrument_geometry.structural_barrier_buffer_atr(sym), min_capped_target_pips=min_capped,
      execution_cost_pips=cost_cfg, room_reference_source=ev.get("room_reference_source"),
      executable_entry_price=ev.get("executable_entry"), shared_boundary_state=shared,
      allow_same_wall_overlap=is_technique_or_confluence(name))
    b = decision.opposing_entry
    measured = decision.measured or {}
    return {
      "allowed": bool(decision.allowed), "hard_block": bool(decision.hard_block), "reason": decision.reason_code,
      "barrier": None if b is None else {"side": b.side, "low": b.lo, "high": b.hi, "tier": b.tier, "score": b.score, "touches": b.touches},
      "raw_room_pips": measured.get("raw_room_pips"), "usable_room_pips": measured.get("usable_room_pips"),
      "n_book": len(rows),
    }

  def locate(logged, rows, pip):
    """How the barrier the production log named relates to a book: exact, near, overlapping or absent."""
    if not logged or logged.get("low") is None:
      return None
    best = "absent"
    for r in rows:
      if r["side"] != logged["side"]:
        continue
      if abs(r["low"] - logged["low"]) <= 0.5 * pip and abs(r["high"] - logged["high"]) <= 0.5 * pip:
        return "exact"
      ov = min(r["high"], logged["high"]) - max(r["low"], logged["low"])
      if ov > 0:
        narrow = min(r["high"] - r["low"], logged["high"] - logged["low"])
        if narrow > 0 and ov / narrow >= 0.5:
          best = "overlap"
    return best

  counts = collections.Counter()
  with open(args.out, "w") as out:
    for line in open(args.events):
      ev = json.loads(line)
      key = (ev["symbol"], ev["bar"])
      if ev["strategy"] not in REVIEWED_SCOPES:
        counts[("retired_strategy", False, False)] += 1  # purged strategies no longer exist; their events are not replayed
        continue
      record = {"match_id": ev["match_id"], "kind": ev["kind"], "symbol": ev["symbol"], "strategy": ev["strategy"],
                "direction": ev["direction"], "ts": ev["ts"], "bar": ev["bar"], "logged_reason": ev["reason_code"],
                "geometry_source": ev["geometry_source"]}
      pip = units.pip_size(ev["symbol"])
      if ev["kind"] == "rejected":
        lb = ev["logged_barrier"]
        try:
          record["logged"] = decide(ev, [{"side": lb["side"], "low": lb["low"], "high": lb["high"], "tier": lb["tier"],
                                          "score": lb.get("zone_score"), "touches": lb.get("touches")}])
        except Exception as exc:  # a geometry the rules reject outright is itself a finding
          record["logged"] = {"error": repr(exc)}
      scope = REVIEWED_SCOPES[ev["strategy"]]
      if bypasses_opposing_structure_gates(scope.legacy_strategy, strategy_mode=scope.strategy_mode):
        record["bypasses_opposing_structure"] = True
      g, p = go.get(key), py.get(key)
      record["has_c"], record["has_ab"] = g is not None, bool(p and "error" not in p and "barriers_a" in p)
      if g is not None:
        record["c"] = decide(ev, g["barriers"] or [])
        record["c_history_bars"] = g["history_bars_m5"]
        record["price"] = g["price"]
        if ev["kind"] == "rejected":
          record["logged_in_c"] = locate(ev["logged_barrier"], g["barriers"] or [], pip)
      if p is not None and "barriers_a" in p:
        record["a"] = decide(ev, p["barriers_a"])
        if "barriers_b" in p:
          record["b"] = decide(ev, p["barriers_b"])
        if ev["kind"] == "rejected":
          record["logged_in_a"] = locate(ev["logged_barrier"], p["barriers_a"], pip)
          if "barriers_b" in p:
            record["logged_in_b"] = locate(ev["logged_barrier"], p["barriers_b"], pip)
      for name, rows in (extra.get(key) or {}).items():
        record[name] = decide(ev, rows)
      counts[(ev["kind"], record["has_c"], record["has_ab"])] += 1
      out.write(json.dumps(record, separators=(",", ":")) + "\n")
  print(json.dumps({f"{k}|c={c}|ab={a}": v for (k, c, a), v in counts.items()}, indent=1))


if __name__ == "__main__":
  main()
