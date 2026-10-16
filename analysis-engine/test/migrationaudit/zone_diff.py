#!/usr/bin/env python3
"""Zone-level diff: Go zone populations vs the frozen Python technique instances.

Usage: zone_diff.py --out-dir <dir with <capture>.go.jsonl / .py-<ref>.jsonl> --ref 1c9f323 --out zone-diff.json [--captures a,b]

For every hourly checkpoint and timeframe the Go populations are matched with the Python oracle by value
(technique, side, low, high, origin time), never by position:
  production_zones  = what the engine publishes (M5: techniqueZoneState, 400-bar window; M15/H1: V2 canonical zones)
  v2_zones_m5       = the V2 canonical M5 zone book
  Python technique_instances = the frozen technique publishers (150/250/400-bar frame windows)
"""
import argparse, collections, json, os

KIND = {"supply": "supply_demand", "demand": "supply_demand", "order_block": "order_block", "fvg": "fvg", "ifvg": "ifvg"}
SIDE = {"supply": "sell", "demand": "buy"}
TOL = 1e-9

def rnd(x):
  return round(float(x), 6)

def gkey(z):
  return (KIND.get(z["kind"], z["kind"]), SIDE.get(z["side"], z["side"]), rnd(z["low"]), rnd(z["high"]), int(z["origin_time"]))

def pkey(z):
  return (z["technique"], z["side"], rnd(z["low"]), rnd(z["high"]), int(z["origin_ts"]))

def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--out-dir", required=True)
  ap.add_argument("--ref", default="1c9f323")
  ap.add_argument("--out", required=True)
  ap.add_argument("--captures", default="")
  ap.add_argument("--examples", type=int, default=12)
  a = ap.parse_args()
  caps = [c for c in a.captures.split(",") if c] or sorted(f[:-len(".go.jsonl")] for f in os.listdir(a.out_dir) if f.endswith(".go.jsonl"))
  agg = collections.defaultdict(collections.Counter)
  examples = collections.defaultdict(list)
  strength = collections.defaultdict(list)
  matched_rows = collections.defaultdict(list)
  for cap in caps:
    go = {}
    for line in open(os.path.join(a.out_dir, cap + ".go.jsonl")):
      r = json.loads(line); go[r["bar"]] = r
    py = {}
    for line in open(os.path.join(a.out_dir, f"{cap}.py-{a.ref}.jsonl")):
      r = json.loads(line); py[r["bar"]] = r
    for bar in sorted(set(go) & set(py)):
      for tf in ("M5", "M15", "H1"):
        gz = {}
        for z in go[bar]["production_zones"].get(tf, []):
          if z["kind"] not in KIND:
            agg[tf]["go_native_other_kind:" + z["kind"]] += 1
            continue
          gz.setdefault(gkey(z), z)
        pframe = py[bar]["frames"][tf]
        step = {"M5": 300, "M15": 900, "H1": 3600}[tf]
        window_start = pframe["last_t"] - (pframe["n"] - 1) * step
        frame_keys = {(i["technique"], i["side"], rnd(i["low"]), rnd(i["high"]), int(i["origin_index_t"] or 0)) for i in go[bar]["frames"][tf]["technique_instances"]}
        go_by_origin = collections.defaultdict(list)
        for k, z in gz.items():
          go_by_origin[(k[0], k[1], k[4])].append(z)
        pz = {}
        for z in py[bar]["frames"][tf]["technique_instances"]:
          if z["technique"] in ("crt",):
            continue
          pz.setdefault(pkey(z), z)
        bucket = agg[tf]
        for k in set(gz) | set(pz):
          g, p = gz.get(k), pz.get(k)
          if g and p:
            bucket["matched"] += 1
            if len(matched_rows[tf]) < a.examples:
              m0 = p.get("measured") or {}
              matched_rows[tf].append({"capture": cap, "bar": bar, "tf": tf, "technique": k[0], "side": k[1], "origin_candle_time": k[4],
                "python": {"low": p["low"], "high": p["high"], "touches": m0.get("touches"), "mitigated": m0.get("mitigated"), "score": m0.get("score")},
                "go": {"low": g["low"], "high": g["high"], "state": g["state"], "touch_count": g["touch_count"], "strength": g["strength"], "relevance": g["relevance"]},
                "strength_delta_vs_python_score": None if m0.get("score") is None else round(g["strength"] - float(m0["score"]), 6),
                "root_cause": "geometry matched; Go strength is a hard-coded 1 (M5 technique population) / a V2 0-1 scale (M15, H1), Python score is the 0-25 confluence score: not comparable scales"})
            m = p.get("measured") or {}
            pm = bool(m.get("mitigated"))
            gm = g["state"] in ("invalidated", "partially_mitigated")
            if pm and not gm: bucket["python_mitigated_go_not_mitigated"] += 1
            if gm and not pm: bucket["go_mitigated_python_not"] += 1
            if m.get("touches") is not None and int(m["touches"]) != g["touch_count"]: bucket["touch_count_disagrees"] += 1
            if m.get("score") is not None:
              strength[tf].append((float(m["score"]), float(g["strength"])))
            bucket["state:" + g["state"]] += 1
          elif g:
            bucket["go_only"] += 1
            bucket["go_only_state:" + g["state"]] += 1
            bucket["go_only_origin:" + ("before_python_window" if k[4] < window_start else "inside_python_window")] += 1
            if g["state"] != "invalidated" and len(examples[(tf, "go_only")]) < a.examples:
              examples[(tf, "go_only")].append({"capture": cap, "bar": bar, "kind": k[0], "side": k[1], "go": [g["low"], g["high"]], "state": g["state"], "origin": k[4]})
          else:
            bucket["python_only"] += 1
            m = p.get("measured") or {}
            bucket["python_only_mitigated:" + str(bool(m.get("mitigated")))] += 1
            near = go_by_origin.get((k[0], k[1], k[4]))
            if near:
              bucket["python_only_same_origin_geometry_differs"] += 1
              best = min(near, key=lambda z: abs(z["low"] - p["low"]) + abs(z["high"] - p["high"]))
              if m.get("entry_clipped"):
                structural_equal = abs(best["low"] - m["structural_low"]) < 1e-6 and abs(best["high"] - m["structural_high"]) < 1e-6
                bucket["geometry_differs:entry_clipped_go_publishes_structural" if structural_equal else "geometry_differs:entry_clipped_other"] += 1
              else:
                bucket["geometry_differs:unclipped_window_effect"] += 1
              if len(examples[(tf, "geometry_differs")]) < a.examples:
                examples[(tf, "geometry_differs")].append({"capture": cap, "bar": bar, "kind": k[0], "side": k[1], "python": [p["low"], p["high"]], "go": [best["low"], best["high"]], "origin": k[4], "measured": m})
            else:
              bucket["python_only_absent_in_go"] += 1
              in_frame = k in frame_keys
              bucket["python_only_absent:present_in_faithful_frame_port" if in_frame else "python_only_absent:absent_in_faithful_frame_port"] += 1
              bucket["python_only_absent:" + ("mitigated" if m.get("mitigated") else "LIVE")] += 1
            if len(examples[(tf, "python_only")]) < a.examples:
              m = p.get("measured") or {}
              examples[(tf, "python_only")].append({"capture": cap, "bar": bar, "kind": k[0], "side": k[1], "python": [p["low"], p["high"]], "origin": k[4], "measured": m})
  out = {"description": "Zone-level diff of the production Go zone populations against the frozen Python technique instances (1c9f323). "
         "M5 production_zones = techniqueZoneState (400-bar window, hard-coded Strength 1 / Relevance Immediate / state Fresh|Touched); "
         "M15/H1 production_zones = the V2 canonical population (a different design: not a parity statement). The faithful 150-bar "
         "frame is exact against Python (see python-go-parity-matrix.json layer_evidence) and is used here only to attribute differences "
         "(python_only_absent:present_in_faithful_frame_port). Match key: (technique, side, low, high, origin candle time).",
         "oracle": a.ref, "captures": caps, "per_tf": {}}
  for tf, c in agg.items():
    sc = strength[tf]
    out["per_tf"][tf] = {
      "counts": dict(c),
      "strength_python_score_vs_go_strength": {
        "n": len(sc),
        "go_strength_distinct": sorted({s for _, s in sc})[:8],
        "python_score_min": min((p for p, _ in sc), default=None), "python_score_max": max((p for p, _ in sc), default=None),
      },
      "matched_sample": matched_rows[tf],
      "examples": {kind: examples[(tf, kind)] for kind in ("go_only", "python_only", "geometry_differs")},
    }
  json.dump(out, open(a.out, "w"), indent=1, sort_keys=True)
  for tf, v in out["per_tf"].items():
    print(tf, json.dumps(v["counts"], sort_keys=True), v["strength_python_score_vs_go_strength"])

main()
