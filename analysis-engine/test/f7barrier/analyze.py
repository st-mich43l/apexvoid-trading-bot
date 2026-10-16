"""Aggregate the F-7 replay into the tables of docs/architecture/f7-opposing-barrier-ground-truth.md.

Inputs are the outputs of extract_events.py, oracle_books.py, dump_test.go, credibility.py, replay_admission.py, simulate.py and
anatomy.py. Pure Python 3, deterministic (the bootstrap uses a fixed seed).

    analyze.py --replay replay.jsonl --events events.jsonl --books-dir out/f7 --oracle-tag a1c77584 --sim sim.jsonl \
        --anatomy anatomy.json --labels credibility.jsonl --out results.json --csv disagreement.csv
"""
import argparse
import collections
import csv
import glob
import json
import math
import os
import random
import statistics

PIP = {"XAU": 0.1, "EURUSD": 1e-4, "GBPUSD": 1e-4, "USDJPY": 0.01, "GBPJPY": 0.01}


def blk(d):
  return d is not None and not d["allowed"]


def pct(n, d):
  return None if not d else round(100.0 * n / d, 1)


def overlap(a, lo, hi):
  return min(a["high"], hi) - max(a["low"], lo)


def load_py_rows(books_dir, tag):
  rows = {}
  for path in sorted(glob.glob(os.path.join(books_dir, f"*.{tag}.jsonl"))):
    for line in open(path):
      rec = json.loads(line)
      key = (rec["symbol"], rec["bar"])
      if key not in rows or rec["history_bars_m5"] > rows[key]["history_bars_m5"]:
        rows[key] = rec
  return rows


def divergence(r, ev, model, py_rows):
  """First point at which Python book `model` ('a'|'b') and the Go book part ways for this event."""
  c, p = r.get("c"), r.get(model)
  if p is None or c is None:
    return "no_comparison"
  if blk(c) and blk(p):
    cb, pb = c.get("barrier"), p.get("barrier")
    if cb and pb and overlap(pb, cb["low"], cb["high"]) <= 0 or (cb and pb and pb["side"] != cb["side"]):
      return "both_block_different_barrier"
    return "agree_blocked"
  if not blk(c) and not blk(p):
    return "agree_allowed"
  if not blk(c) and blk(p):
    return "python_blocks_go_allows"
  cb = c["barrier"]
  rows = (py_rows.get((r["symbol"], r["bar"])) or {}).get("barriers_" + model) or []
  same = [x for x in rows if x["side"] == cb["side"]]
  hits = [x for x in same if overlap(x, cb["low"], cb["high"]) > 0]
  if p["reason"] == "weak_opposing_level_ignored":
    return "python_tier_level_weak_ignored"
  if not hits:
    return "barrier_absent_in_python_book"
  planned = ev["planned_entry"]
  if not any(x["low"] <= planned <= x["high"] for x in hits) and cb["low"] <= planned <= cb["high"]:
    return "python_band_narrower_entry_outside"
  return "rule_difference:" + str(p["reason"])


def bootstrap(clusters, stat, n=1000, seed=7):
  rng = random.Random(seed)
  keys = list(clusters)
  vals = []
  for _ in range(n):
    sample = [x for k in (rng.choice(keys) for _ in keys) for x in clusters[k]]
    vals.append(stat(sample))
  vals.sort()
  return round(vals[int(0.025 * n)], 3), round(vals[int(0.975 * n)], 3)


def outcome_stats(rs, sim):
  clusters = collections.defaultdict(list)
  for r in rs:
    s = sim.get(r["match_id"] + str(r["ts"]))
    if s is None:
      continue
    t, v = s["r1"]
    clusters[(r["symbol"], r["direction"], r["ts"] // 3600)].append((t, v))

  def win(sample):
    w = sum(1 for t, _ in sample if t == "win")
    l = sum(1 for t, _ in sample if t == "loss")
    return w / (w + l) if w + l else float("nan")

  def er(sample):
    return sum(1.0 if t == "win" else -1.0 if t == "loss" else v for t, v in sample) / len(sample) if sample else float("nan")

  flat = [x for v in clusters.values() for x in v]
  if not flat:
    return {"events": 0}
  gains = sum(v if t == "win" else max(v, 0.0) if t == "censored" else 0.0 for t, v in flat)
  losses = sum(1.0 if t == "loss" else max(-v, 0.0) if t == "censored" else 0.0 for t, v in flat)
  out = {"events": len(flat), "episodes": len(clusters), "win1": round(win(flat), 3), "expectancy_r": round(er(flat), 3),
         "profit_factor": round(gains / losses, 3) if losses else None, "censored": sum(1 for t, _ in flat if t == "censored")}
  if len(clusters) >= 8:
    out["win1_ci95"] = bootstrap(clusters, win)
    out["expectancy_r_ci95"] = bootstrap(clusters, er)
  return out


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--replay", required=True)
  parser.add_argument("--events", required=True)
  parser.add_argument("--books-dir", required=True)
  parser.add_argument("--oracle-tag", default="a1c77584")
  parser.add_argument("--sim", required=True)
  parser.add_argument("--anatomy", required=True)
  parser.add_argument("--labels", required=True)
  parser.add_argument("--out", required=True)
  parser.add_argument("--csv", required=True)
  args = parser.parse_args()

  replay = [json.loads(l) for l in open(args.replay)]
  events = {}
  for l in open(args.events):
    e = json.loads(l)
    events[e["match_id"] + "|" + str(e["ts"]) + "|" + e["kind"]] = e
  sim = {}
  for l in open(args.sim):
    s = json.loads(l)
    sim[s["match_id"] + str(s["ts"])] = s
  py_rows = load_py_rows(args.books_dir, args.oracle_tag)
  rej = [r for r in replay if r["kind"] == "rejected"]
  pub = [r for r in replay if r["kind"] == "published"]
  res = {"population": {"replayed_events": len(replay), "rejected": len(rej), "published": len(pub),
                        "rejected_with_c": sum("c" in r for r in rej), "rejected_with_abc": sum("a" in r and "c" in r for r in rej),
                        "published_with_abc": sum("a" in r and "c" in r for r in pub)}}

  # 1. fidelity of the reconstruction
  res["fidelity"] = {
    "harness_reproduces_logged_reason_pct": pct(sum(1 for r in rej if r.get("logged", {}).get("reason") == r["logged_reason"]), len(rej)),
    "logged_barrier_in_replayed_go_book": dict(collections.Counter(r.get("logged_in_c") for r in rej if "c" in r)),
    "replayed_go_blocks_pct": pct(sum(blk(r["c"]) for r in rej if "c" in r), sum("c" in r for r in rej)),
    "replayed_go_same_reason_pct": pct(sum(1 for r in rej if "c" in r and r["c"]["reason"] == r["logged_reason"]), sum("c" in r for r in rej)),
  }

  # 2. what each book does with the same rejected / published event
  abc_rej = [r for r in rej if "a" in r and "c" in r]
  abc_pub = [r for r in pub if "a" in r and "c" in r]

  def block_table(rs):
    out = {"n": len(rs)}
    for name, key in (("A_frozen_scanner", "a"), ("B_frozen_plan_time", "b"), ("C_go_book", "c"), ("D_unmerged_live", "d_unmerged"),
                      ("D_credible_reference", "d_credible"), ("D_credible_merged", "d_credible_merged"), ("C_merge_capped", "c_merge_capped")):
      have = [r for r in rs if r.get(key) is not None]
      out[name] = {"blocks": sum(blk(r[key]) for r in have), "of": len(have), "block_pct": pct(sum(blk(r[key]) for r in have), len(have))}
    out["abc_triples"] = {"|".join("block" if x else "allow" for x in k): v for k, v in collections.Counter(
      (blk(r["a"]), blk(r.get("b")), blk(r["c"])) for r in rs if "b" in r).items()}
    return out

  res["blocks_rejected_events"] = block_table(abc_rej)
  res["blocks_published_events"] = block_table(abc_pub)
  for dim in ("symbol", "strategy", "logged_reason"):
    res[f"by_{dim}"] = {k: {"n": len(v), "A_block_pct": pct(sum(blk(r["a"]) for r in v), len(v)), "B_block_pct": pct(sum(blk(r.get("b")) for r in v if "b" in r), sum("b" in r for r in v)),
                           "C_block_pct": pct(sum(blk(r["c"]) for r in v), len(v)), "credible_ref_block_pct": pct(sum(blk(r.get("d_credible")) for r in v if "d_credible" in r), sum("d_credible" in r for r in v))}
                       for k, v in sorted(collections.defaultdict(list, {k: [r for r in abc_rej if r[dim] == k] for k in {r[dim] for r in abc_rej}}).items())}

  # 3. agreement with the independent labels (reference = d_credible), with a chronological dev / held-out split
  labelled = sorted([r for r in abc_rej + abc_pub if "d_credible" in r], key=lambda r: r["ts"])
  cut = int(len(labelled) * 0.6)

  def agreement(rs, key):
    tp = fp = fn = tn = 0
    for r in rs:
      m = r.get(key)
      if m is None:
        continue
      pred, ref = blk(m), blk(r["d_credible"])
      tp += pred and ref
      fp += pred and not ref
      fn += ref and not pred
      tn += (not pred) and not ref
    n = tp + fp + fn + tn
    base = (tp + fn) / n if n else None
    return {"n": n, "blocks": tp + fp, "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None, "accuracy": round((tp + tn) / n, 3) if n else None,
            "reference_block_rate": round(base, 3) if base is not None else None}

  res["agreement_with_independent_labels"] = {
    "dev_n": cut, "heldout_n": len(labelled) - cut,
    "models": {name: {"dev": agreement(labelled[:cut], key), "heldout": agreement(labelled[cut:], key)}
               for name, key in (("A", "a"), ("B", "b"), ("C", "c"), ("C_merge_capped", "c_merge_capped"), ("D_unmerged", "d_unmerged"))
               } | {f"grid_{k[2:]}": {"dev": agreement(labelled[:cut], k), "heldout": agreement(labelled[cut:], k)}
                    for k in sorted({k for r in labelled for k in r if k.startswith("g_t")})},
  }

  # 4. earliest divergence between each Python book and the Go book
  div = {}
  for model in ("a", "b"):
    c = collections.Counter()
    for r in abc_rej + abc_pub:
      ev = events[r["match_id"] + "|" + str(r["ts"]) + "|" + r["kind"]]
      c[divergence(r, ev, model, py_rows)] += 1
    div[model] = dict(c.most_common())
  res["earliest_divergence"] = div

  # 5. outcomes by stratum (hypothetical 1R race), episode-clustered bootstrap CIs, chronological split
  split_ts = labelled[cut]["ts"] if labelled else 0
  strata = {
    "executed_published": [r for r in abc_pub],
    "rejected_all": abc_rej,
    "rejected_go_blocks_python_allows_both": [r for r in abc_rej if blk(r["c"]) and not blk(r["a"]) and not blk(r.get("b"))],
    "rejected_go_and_python_block": [r for r in abc_rej if blk(r["c"]) and (blk(r["a"]) or blk(r.get("b")))],
    "rejected_go_blocks_label_supports": [r for r in abc_rej if blk(r["c"]) and blk(r.get("d_credible"))],
    "rejected_go_blocks_label_does_not": [r for r in abc_rej if blk(r["c"]) and "d_credible" in r and not blk(r["d_credible"])],
    "rejected_reason_no_target": [r for r in abc_rej if r["logged_reason"] == "opposing_barrier_no_target"],
    "rejected_reason_room_below_cost": [r for r in abc_rej if r["logged_reason"] == "opposing_barrier_room_below_cost"],
    "rejected_reason_entry_overlap": [r for r in abc_rej if r["logged_reason"] == "opposing_entry_overlap"],
    "rejected_reason_entry_contained": [r for r in abc_rej if r["logged_reason"] == "opposing_entry_contained"],
  }
  res["outcomes_hypothetical_1r"] = {
    name: {"all": outcome_stats(rs, sim), "dev": outcome_stats([r for r in rs if r["ts"] < split_ts], sim),
           "heldout": outcome_stats([r for r in rs if r["ts"] >= split_ts], sim)}
    for name, rs in strata.items()}

  # 5b. what explains the Go-vs-Python difference: the same Go zones under progressively stricter lifecycle rules.
  # touches == 0 is the frozen Python semantic (structural_barriers drops any zone with mitigated=True, and mark_mitigation sets
  # mitigated on the first touch); the Go lifecycle keeps fresh, touched and partially-mitigated zones as live barriers.
  def lifecycle_row(rs):
    out = {"n": len(rs)}
    for name, key in (("python_A", "a"), ("python_B", "b"), ("go_C_all_live", "c"), ("go_zones_fresh_only", "g_t0_s0.0"),
                      ("go_zones_touches<=1", "g_t1_s0.0"), ("go_zones_touches<=2", "g_t2_s0.0"), ("go_zones_touches<=3", "g_t3_s0.0")):
      have = [r for r in rs if r.get(key) is not None]
      out[name] = pct(sum(blk(r[key]) for r in have), len(have))
    return out

  res["lifecycle_explains_the_difference"] = {"rejected_events": lifecycle_row([r for r in abc_rej if "g_t0_s0.0" in r]),
                                              "published_events": lifecycle_row([r for r in abc_pub if "g_t0_s0.0" in r])}
  c_blocked = sorted([r for r in replay if r["kind"] == "rejected" and "c" in r and "g_t0_s0.0" in r and blk(r["c"])], key=lambda r: r["ts"])
  cut_ts = c_blocked[int(len(c_blocked) * 0.6)]["ts"] if c_blocked else 0
  touch_strata = {
    "go_blocks_and_fresh_only_also_blocks": [r for r in c_blocked if blk(r["g_t0_s0.0"])],
    "go_blocks_only_because_of_touched_zones": [r for r in c_blocked if not blk(r["g_t0_s0.0"])],
    "go_blocks_and_touches<=1_also_blocks": [r for r in c_blocked if blk(r["g_t1_s0.0"])],
    "go_blocks_only_because_of_zones_with_2+_touches": [r for r in c_blocked if not blk(r["g_t1_s0.0"])],
  }
  res["outcomes_by_lifecycle_stratum_hypothetical_1r"] = {
    name: {"events": len(rs), "all": outcome_stats(rs, sim), "dev": outcome_stats([r for r in rs if r["ts"] < cut_ts], sim),
           "heldout": outcome_stats([r for r in rs if r["ts"] >= cut_ts], sim)} for name, rs in touch_strata.items()}
  res["outcomes_by_symbol_hypothetical_1r"] = {sym: {"rejected": outcome_stats([r for r in replay if r["kind"] == "rejected" and r["symbol"] == sym], sim),
                                                      "executed": outcome_stats([r for r in replay if r["kind"] == "published" and r["symbol"] == sym], sim)}
                                               for sym in sorted(PIP)}
  res["events_before_barrier_port_fix"] = sum(1 for r in replay if r["ts"] < 1790692860)  # 2026-09-30 14:21 UTC, 49f5ffdf

  # 6. room (how far ahead the nearest decisive barrier sits) for events the rules did not hard-block on containment
  def room(r, key):
    d = r.get(key)
    return None if d is None or d.get("raw_room_pips") is None else d["raw_room_pips"]

  deltas = collections.defaultdict(list)
  for r in abc_rej + abc_pub:
    for key in ("a", "b"):
      ra, rc = room(r, key), room(r, "c")
      if ra is not None and rc is not None:
        deltas[key].append(rc - ra)
  res["room_delta_pips_go_minus_python"] = {k: {"n": len(v), "median": round(statistics.median(v), 1), "p25": round(sorted(v)[len(v) // 4], 1), "p75": round(sorted(v)[3 * len(v) // 4], 1)} for k, v in deltas.items() if v}

  # 7. anatomy + label summary
  anatomy = json.load(open(args.anatomy))
  res["anatomy"] = anatomy["summary"]
  labels = [json.loads(l) for l in open(args.labels)]
  tf_sec = {"M5": 300, "M15": 900, "H1": 3600}

  def credible_rate(rs):
    return {"n": len(rs), "credible_pct": pct(sum(x["credible"] for x in rs), len(rs))}

  res["label_rates_by_zone_fact"] = {
    "state": {k: credible_rate([x for x in labels if x["state"] == k]) for k in ("fresh", "touched", "partially_mitigated")},
    "touches": {str(k): credible_rate([x for x in labels if min(x["touches"], 6) == k]) for k in range(7)},
    "strength": {str(k): credible_rate([x for x in labels if round(min(x["strength"], 1.0) * 5) / 5 == k]) for k in (0.4, 0.6, 0.8, 1.0)},
    "timeframe": {k: credible_rate([x for x in labels if x["tf"] == k]) for k in tf_sec},
    "components": {"reacted": dict(collections.Counter(str(x["reacted"]) for x in labels)), "oracle_origin": dict(collections.Counter(str(x["oracle_origin"]) for x in labels)),
                   "broken": dict(collections.Counter(str(x["broken"]) for x in labels))},
  }

  json.dump(res, open(args.out, "w"), indent=1, default=str)

  # 8. the disagreement table: one row per event
  with open(args.csv, "w", newline="") as handle:
    w = csv.writer(handle)
    w.writerow(["opportunity", "symbol", "strategy", "direction", "time_utc", "kind", "planned_entry", "python_A", "python_B", "go_C", "credible_reference",
                "actual_admission", "go_decisive_barrier", "go_barrier_width_pips", "go_barrier_tier", "go_barrier_touches", "go_barrier_score",
                "earliest_divergence_A", "earliest_divergence_B"])
    import datetime
    for r in sorted(abc_rej + abc_pub, key=lambda r: r["ts"]):
      ev = events[r["match_id"] + "|" + str(r["ts"]) + "|" + r["kind"]]
      cb = (r.get("c") or {}).get("barrier")

      def cell(d):
        return "" if d is None else ("allow:" if d["allowed"] else "block:") + str(d["reason"])

      w.writerow([r["match_id"][:16], r["symbol"], r["strategy"], r["direction"], datetime.datetime.fromtimestamp(r["ts"], datetime.UTC).strftime("%Y-%m-%d %H:%M:%S"),
                  r["kind"], ev["planned_entry"], cell(r.get("a")), cell(r.get("b")), cell(r.get("c")), cell(r.get("d_credible")),
                  ("blocked:" + r["logged_reason"]) if r["kind"] == "rejected" else ("published:" + str(ev.get("exit_path") or "open")),
                  "" if not cb else f"{cb['side']} {cb['low']}-{cb['high']}", "" if not cb else round((cb["high"] - cb["low"]) / PIP[r["symbol"]], 1),
                  "" if not cb else cb["tier"], "" if not cb else cb["touches"], "" if not cb else round(cb["score"], 3),
                  divergence(r, ev, "a", py_rows), divergence(r, ev, "b", py_rows)])
  print(json.dumps({k: res[k] for k in ("population", "fidelity", "blocks_rejected_events", "blocks_published_events", "earliest_divergence")}, indent=1))


if __name__ == "__main__":
  main()
