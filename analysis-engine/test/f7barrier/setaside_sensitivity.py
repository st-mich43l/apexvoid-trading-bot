"""How much of the admission book rests on the rules that set an opposing barrier aside, measured, not argued.

Four rules remove a barrier from consideration before the nearest one is chosen: the shared-boundary glue (all strategies), the
same-wall overlap exemption (technique and confluence strategies), the exclusion of a barrier wholly behind the entry, and (in
the engine) the cross-side reconciliation. Each is documented design, none is a demonstrated defect, and each moves a double-digit
share of decisions. This replays the worker's own target-room decision over a decisions dump (``decisions_test.go``) with one rule
varied at a time, through the real ``evaluate_structural_target_room``, and reports how many admissible decisions change and how.

Run from ``algo-bot``:

    python ../analysis-engine/test/f7barrier/setaside_sensitivity.py --books /tmp/dec-base --out sensitivity.json

Variants: ``base`` (the worker as shipped), ``no_same_wall`` (never set a barrier aside because it overlaps the candidate band),
``members_same_wall`` (set aside only the member zones that overlap, keep the rest of the merged chain), ``no_glue`` (never set a
barrier aside because it is glued to the proximal edge).
"""
import argparse
import collections
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
  import app.autotrade.structural_target_room as room
  from app.autotrade import units
  from app.autotrade.go_opportunity_policy import REVIEWED_SCOPES
  from app.autotrade.go_zone_book import parse_zone_book
  from app.autotrade.strategy_taxonomy import bypasses_opposing_structure_gates, is_technique_or_confluence
  from app.core import instrument_geometry
  from app.core.config import runtime_config

  glue, overlap = room.filter_shared_boundary_opposing_entries, room.filter_overlapping_opposing_entries

  def surviving_members(entry, excluded):
    members = tuple(getattr(entry, "members", ()) or ())
    if len(members) < 2:
      return []
    kept = []
    for m in members:
      if excluded(float(m["low"]), float(m["high"])):
        continue
      kept.append(room.ZoneOpposingEntry(side=entry.side, lo=float(m["low"]), hi=float(m["high"]), tier=entry.tier,
                                         score=float(m.get("strength", entry.score)), touches=int(m.get("touches", entry.touches)), members=(m,)))
    return kept

  def members_overlap(entries, *, direction, candidate_entry_low, candidate_entry_high, pip_size, atr):
    low, high = min(candidate_entry_low, candidate_entry_high), max(candidate_entry_low, candidate_entry_high)
    threshold = room.overlap_exclusion_threshold(pip_size=pip_size, atr=atr)
    opposing = "sell" if str(direction).upper() == "BUY" else "buy"
    out = []
    for e in entries:
      if str(e.side).casefold() != opposing:
        out.append(e)
        continue
      hit = lambda lo, hi: room._overlap(low, high, lo, hi)[0] >= threshold
      if hit(e.lo, e.hi):
        out.extend(surviving_members(e, hit))
      else:
        out.append(e)
    return out, {"applied": True, "overlap_excluded": 0, "dropped_bounds": []}

  variants = {
    "base": {},
    "no_same_wall": {"same_wall": False},
    "members_same_wall": {"overlap_fn": members_overlap},
    "no_glue": {"glue_fn": lambda entries, **kw: (list(entries), {"applied": False})},
  }
  min_capped = float(runtime_config.auto_algo.actionability.target_room.minimum_capped_target_pips)
  cost = float(runtime_config.execution.policy.execution_cost_pips)
  now = int(time.time())
  rows = []
  for path in sorted(glob.glob(os.path.join(a.books, "*.decisions.jsonl"))):
    for line in open(path):
      rec = json.loads(line)
      symbol, price = rec["symbol"], float(rec["price"])
      book = parse_zone_book(json.dumps({"schema_version": 1, "symbol": symbol, "generated_at": now, "entries": [], "barriers": rec["barriers"]}), symbol=symbol, now=now)
      for c in rec["candidates"]:
        profile = REVIEWED_SCOPES[c["strategy"]]
        if bypasses_opposing_structure_gates(profile.legacy_strategy) or float(c.get("atr") or 0) <= 0:
          continue
        if profile.requires_reaction and not c.get("confirmed_reaction"):
          continue
        rows.append((symbol, price, c, profile, book))
  print(f"{len(rows)} admissible replayed decisions", file=sys.stderr)
  results = {}
  for name, cfg in variants.items():
    room.filter_shared_boundary_opposing_entries = cfg.get("glue_fn", glue)
    room.filter_overlapping_opposing_entries = cfg.get("overlap_fn", overlap)
    out = []
    for symbol, price, c, profile, book in rows:
      pip, atr = units.pip_size(symbol), float(c["atr"])
      low, high, direction = float(c["entry_low"]), float(c["entry_high"]), c["direction"].upper()
      planned, source = room.zone_proximal_room_reference(direction=direction, spot_price=price, candidate_entry_low=low, candidate_entry_high=high, pip_size=pip, atr=atr)
      proximal = low if direction == "SELL" else high
      targets = sorted({max(0, round(abs(t - proximal) / pip)) for t in c["targets"]})
      same_wall = cfg.get("same_wall", is_technique_or_confluence(profile.legacy_strategy))
      entries, shared = room.filter_shared_boundary_opposing_entries(book.entries, direction=direction, candidate_entry_low=low, candidate_entry_high=high, pip_size=pip, atr=atr, planned_entry=planned)
      d = room.evaluate_structural_target_room(
        direction=direction, planned_entry_price=planned, candidate_entry_low=low, candidate_entry_high=high, configured_target_pips=targets, actionable_entries=entries,
        atr=atr, pip_size=pip, barrier_buffer_atr=instrument_geometry.structural_barrier_buffer_atr(symbol), min_capped_target_pips=min_capped, execution_cost_pips=cost,
        room_reference_source=source, executable_entry_price=price, shared_boundary_state=shared, allow_same_wall_overlap=same_wall)
      out.append((d.allowed, d.reason_code))
    results[name] = out
  report = {"admissible_decisions": len(rows), "variants": {}}
  base = results["base"]
  for name, out in results.items():
    changed = collections.Counter()
    by_strategy = collections.Counter()
    for (symbol, price, c, profile, book), b, n in zip(rows, base, out):
      if b != n:
        changed[("admitted->blocked" if b[0] and not n[0] else "blocked->admitted" if not b[0] and n[0] else "reason_only") + ":" + str(n[1])] += 1
        by_strategy[c["strategy"]] += 1
    report["variants"][name] = {"blocked": sum(1 for allowed, _ in out if not allowed), "changed_vs_base": sum(changed.values()),
                                "changed_pct": round(100.0 * sum(changed.values()) / len(rows), 1), "by_change": dict(changed.most_common(8)), "by_strategy": dict(by_strategy.most_common())}
  json.dump(report, open(a.out, "w"), indent=1)
  print(json.dumps({k: {"blocked": v["blocked"], "changed_pct": v["changed_pct"]} for k, v in report["variants"].items()}, indent=1))


main()
