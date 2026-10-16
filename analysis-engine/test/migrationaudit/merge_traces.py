#!/usr/bin/env python3
"""Merge per-capture oracle_trace.py outputs into trace-<ref>.json. Usage: merge_traces.py --dir <tr dir> --out-dir <out dir>"""
import argparse, collections, glob, json, os

ap = argparse.ArgumentParser()
ap.add_argument("--dir", required=True); ap.add_argument("--out-dir", required=True)
a = ap.parse_args()
for ref in ("1c9f323", "a1c77584"):
  executed = collections.defaultdict(set); fired = collections.Counter(); ev = collections.Counter(); err = collections.Counter(); caps = []
  for f in sorted(glob.glob(os.path.join(a.dir, f"{ref}.*.json"))):
    d = json.load(open(f)); caps += d["captures"]
    for fn, items in d["executed"].items():
      for name, line in items: executed[fn].add((name, line))
    fired.update(d["detector_fired"]); ev.update(d["detector_evaluations"]); err.update(d["detector_errors"])
  json.dump({"oracle": ref, "captures": caps, "executed": {k: sorted(v) for k, v in executed.items()},
             "detector_evaluations": dict(ev), "detector_fired": dict(fired), "detector_errors": dict(err)},
            open(os.path.join(a.out_dir, f"trace-{ref}.json"), "w"), indent=1)
  print(ref, len(caps), "captures", sum(len(v) for v in executed.values()), "functions", dict(fired))
