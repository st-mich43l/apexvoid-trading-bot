"""Layer-by-layer comparison of the historical Python oracle dump and the Go dump of the same checkpoints.

    python oracle_compare.py --oracle <capture>.py-<commit>.jsonl --go <capture>.go.jsonl --out report.json [--go-field swing_levels ...]

For each checkpoint and timeframe the layers are compared in dependency order (ATR, swings, structure, breaks, levels,
legs, zones, technique zones, technique instances, liquidity, sessions, dealing range, regime, momentum, scalp
structure). The first layer that differs is the *earliest divergence* of that checkpoint: a strategy decision is never
blamed for a difference that already exists in a lower layer.

Matching of list elements is by value (price and time, never by position), so an element is reported as matched, as
oracle-only or as Go-only, and matched elements are compared field by field. Nothing here reads production data.
"""

import argparse
import collections
import json
import math

REL = 1e-9  # numerical parity tolerance (relative, with an absolute floor of 1e-9)
LAYER_ORDER = [
  "atr", "swings", "structure", "breaks", "levels", "legs", "zones", "technique_zones", "technique_instances",
  "pools", "grabs", "sessions", "dealing_range", "regime", "momentum", "scalp_barriers", "scalp_range",
]


def close(a, b, rel=REL):
  if a is None or b is None:
    return a is None and b is None
  if isinstance(a, bool) or isinstance(b, bool):
    return bool(a) == bool(b)
  if isinstance(a, (int, float)) and isinstance(b, (int, float)):
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))
  return a == b


def err(a, b):
  if a is None or b is None or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
    return None
  diff = abs(a - b)
  return diff, diff / max(1e-12, abs(a), abs(b))


class Acc:
  """Counts and maximum numerical errors of one layer."""

  def __init__(self):
    self.checkpoints = 0
    self.identical = 0
    self.matched = 0
    self.oracle_only = 0
    self.go_only = 0
    self.field_diffs = collections.Counter()
    self.max_abs = collections.defaultdict(float)
    self.max_rel = collections.defaultdict(float)
    self.examples = collections.defaultdict(list)

  def note_field(self, name, a, b, context):
    if not close(a, b):
      self.field_diffs[name] += 1
      e = err(a, b)
      if e:
        self.max_abs[name] = max(self.max_abs[name], e[0])
        self.max_rel[name] = max(self.max_rel[name], e[1])
      if len(self.examples[name]) < 3:
        self.examples[name].append({"oracle": a, "go": b, **context})
    else:
      e = err(a, b)
      if e:
        self.max_abs[name] = max(self.max_abs[name], e[0])
        self.max_rel[name] = max(self.max_rel[name], e[1])

  def out(self):
    return {
      "checkpoints": self.checkpoints, "identical_checkpoints": self.identical, "matched_elements": self.matched,
      "oracle_only_elements": self.oracle_only, "go_only_elements": self.go_only,
      "field_mismatches": dict(self.field_diffs),
      "max_abs_error": {k: v for k, v in self.max_abs.items() if v > 0},
      "max_rel_error": {k: v for k, v in self.max_rel.items() if v > 0},
      "examples": {k: v for k, v in self.examples.items()},
    }


def zone_key(z, bottom="bottom", top="top", origin="origin_index_t"):
  return (z.get("side"), round(float(z[bottom]), 6), round(float(z[top]), 6), z.get(origin))


def compare_lists(acc, ora, go, key_fn, fields, context, fuzzy=None):
  """Match by key; compare ``fields`` of the matched. Returns True when the two lists are identical."""
  a = collections.defaultdict(list)
  b = collections.defaultdict(list)
  for item in ora:
    a[key_fn(item)].append(item)
  for item in go:
    b[key_fn(item)].append(item)
  identical = True
  for key in set(a) | set(b):
    la, lb = a.get(key, []), b.get(key, [])
    n = min(len(la), len(lb))
    for i in range(n):
      acc.matched += 1
      for name, (fa, fb) in fields.items():
        va, vb = fa(la[i]), fb(lb[i])
        if not close(va, vb):
          identical = False
        acc.note_field(name, va, vb, {**context, "key": str(key)})
    if len(la) > n:
      acc.oracle_only += len(la) - n
      identical = False
      if len(acc.examples["oracle_only"]) < 5:
        acc.examples["oracle_only"].append({**context, "item": la[n]})
    if len(lb) > n:
      acc.go_only += len(lb) - n
      identical = False
      if len(acc.examples["go_only"]) < 5:
        acc.examples["go_only"].append({**context, "item": lb[n]})
  return identical


def same(name):
  return (lambda x: x.get(name), lambda x: x.get(name))


def f(*names):
  return {n: same(n) for n in names}


def compare_frame(accs, ora, go, ctx, go_zone_field="zones", go_level_field="levels", primary_tf="M5"):
  """Returns the list of layers that differ for this checkpoint/timeframe."""
  bad = []

  # --- ATR conventions: simple (rolling mean), the median scalar the detectors take, and Wilder
  a = accs["atr"]
  ident = True
  for name, ga, gb in (
    ("atr_simple_last", ora.get("atr_simple_last"), go.get("atr_simple_last")),
    ("atr_scalar_median", ora.get("atr_scalar_median"), go.get("atr_scalar_median")),
    ("atr_wilder_last", ora.get("atr_wilder_last"), go.get("detector_atr")),
  ):
    a.note_field(name, ga, gb, ctx)
    ident &= close(ga, gb)
  ta, tb = ora.get("atr_simple_tail") or [], go.get("atr_simple_tail") or []
  for x, y in zip(ta, tb):
    a.note_field("atr_simple_tail", x, y, ctx)
    ident &= close(x, y)
  a.checkpoints += 1
  a.identical += int(ident)
  if not ident:
    bad.append("atr")

  # --- swings
  a = accs["swings"]
  a.checkpoints += 1
  ok = compare_lists(
    a, ora.get("swings", []), go.get("swings", []),
    lambda s: (s["kind"], s["index_t"], round(s["price"], 6)),
    {"label": same("label"), "confirmed_index_t": same("confirmed_index_t")}, ctx,
  )
  a.identical += int(ok)
  if not ok:
    bad.append("swings")

  # --- structure bias
  a = accs["structure"]
  a.checkpoints += 1
  a.note_field("structure", ora.get("structure"), go.get("structure"), ctx)
  ok = ora.get("structure") == go.get("structure")
  a.identical += int(ok)
  if not ok:
    bad.append("structure")

  # --- breaks
  a = accs["breaks"]
  a.checkpoints += 1
  ok = compare_lists(
    a, ora.get("breaks", []), go.get("breaks", []),
    lambda b: (b["kind"], b["direction"], b["index_t"], round(b["level"], 6)), {}, ctx,
  )
  a.identical += int(ok)
  if not ok:
    bad.append("breaks")

  # --- key levels (population differs by reference; see go_level_field)
  a = accs["levels"]
  a.checkpoints += 1
  ok = compare_lists(
    a, ora.get("levels", []), go.get(go_level_field, []),
    lambda l: (l["kind"], round(l["price"], 6)),
    f("touches", "band", "strength"), ctx,
  )
  a.identical += int(ok)
  if not ok:
    bad.append("levels")

  # --- displacement legs
  a = accs["legs"]
  a.checkpoints += 1
  ok = compare_lists(
    a, ora.get("legs", []), go.get("legs", []),
    lambda l: (l["direction"], l["start_t"], l["end_t"]), {"size": same("size")}, ctx,
  )
  a.identical += int(ok)
  if not ok:
    bad.append("legs")

  # --- zones (merged, scored) and the unmerged technique population
  zfields = {
    "touches": same("touches"), "mitigated": same("mitigated"), "source": same("source"),
    "sources": (lambda z: list(z.get("sources") or []), lambda z: list(z.get("sources") or [])),
    "score": same("score"), "break_kind": same("break_kind"), "break_index_t": same("break_index_t"),
    "score_reasons": (lambda z: list(z.get("score_reasons") or []), lambda z: list(z.get("score_reasons") or [])),
  }
  for layer, ofield, gfield in (("zones", "zones", go_zone_field), ("technique_zones", "technique_zones", "technique_zones")):
    a = accs[layer]
    a.checkpoints += 1
    ok = compare_lists(a, ora.get(ofield, []), go.get(gfield, []), zone_key, zfields, ctx)
    a.identical += int(ok)
    if not ok:
      bad.append(layer)

  # --- technique instances (the population the technique publishers actually read)
  a = accs["technique_instances"]
  a.checkpoints += 1
  # Go yields every technique only for the execution frame; every other frame yields supply/demand alone
  # (a strategy binds those to the execution frame). CRT is built from H1 plus the execution window and is not a
  # per-frame list, so it is compared separately (see crt_instances).
  only_sd = ctx["tf"] != primary_tf
  o_inst = []
  for i in ora.get("technique_instances", []):
    m = i.get("measured") or {}
    if i["technique"] == "crt" or (only_sd and i["technique"] != "supply_demand"):
      continue
    # a measured fact the oracle does not record (iFVG) is the default, not a disagreement
    o_inst.append({
      "technique": i["technique"], "side": i["side"], "low": i["low"], "high": i["high"], "origin_index_t": i.get("origin_index_t"),
      "touches": m.get("touches") if m.get("touches") is not None else 0,
      "mitigated": bool(m.get("mitigated")), "sources": list(i.get("sources") or []),
    })
  g_inst = [
    {"technique": i["technique"], "side": i["side"], "low": i["low"], "high": i["high"], "origin_index_t": i.get("origin_index_t"),
     "touches": i.get("touches") or 0, "mitigated": bool(i.get("mitigated")), "sources": list(i.get("sources") or [])}
    for i in go.get("technique_instances", []) if i.get("technique") != "crt" and not (only_sd and i["technique"] != "supply_demand")
  ]
  ok = compare_lists(
    a, o_inst, g_inst,
    lambda i: (i["technique"], i["side"], round(i["low"], 6), round(i["high"], 6), i["origin_index_t"]),
    f("touches", "mitigated", "sources"), ctx,
  )
  a.identical += int(ok)
  if not ok:
    bad.append("technique_instances")

  # --- liquidity
  a = accs["pools"]
  a.checkpoints += 1
  ok = compare_lists(a, ora.get("pools", []), go.get("pools", []),
                     lambda p: (p["side"], round(p["level"], 6)), f("band", "touches"), ctx)
  a.identical += int(ok)
  if not ok:
    bad.append("pools")
  a = accs["grabs"]
  a.checkpoints += 1
  ok = compare_lists(
    a, [dict(g, pool_level=round(g["pool"]["level"], 6)) for g in ora.get("grabs", [])],
    [dict(g, pool_level=round(g["pool"]["level"], 6)) for g in go.get("grabs", [])],
    lambda g: (g["direction"], g["index_t"], g["pool_level"]), f("grade", "displacement", "inducement"), ctx,
  )
  a.identical += int(ok)
  if not ok:
    bad.append("grabs")

  # --- session levels, dealing range, regime, momentum
  a = accs["sessions"]
  a.checkpoints += 1
  ok = compare_lists(a, ora.get("sessions", []), go.get("sessions", []),
                     lambda s: (s["name"], round(s["price"], 6)), f("swept"), ctx)
  a.identical += int(ok)
  if not ok:
    bad.append("sessions")

  a = accs["dealing_range"]
  a.checkpoints += 1
  od, gd = ora.get("dealing_range"), go.get("dealing_range")
  ok = (od is None and gd is None) or (od is not None and gd is not None and all(close(od.get(k), gd.get(k)) for k in ("high", "low", "eq", "position", "zone", "fib_zone")))
  if od is not None and gd is not None:
    for k in ("high", "low", "eq", "position", "zone", "fib_zone"):
      a.note_field(k, od.get(k), gd.get(k), ctx)
  else:
    a.field_diffs["presence"] += int(not ok)
  a.identical += int(ok)
  if not ok:
    bad.append("dealing_range")

  a = accs["regime"]
  a.checkpoints += 1
  orr, gr = ora.get("regime") or {}, go.get("regime") or {}
  ok = True
  for k in ("kind", "range_high", "range_low", "height_atr", "coiling", "legacy_kind", "new_kind"):
    a.note_field(k, orr.get(k), gr.get(k), ctx)
    ok &= close(orr.get(k), gr.get(k))
  a.identical += int(ok)
  if not ok:
    bad.append("regime")

  a = accs["momentum"]
  a.checkpoints += 1
  a.note_field("momentum", ora.get("momentum"), go.get("momentum"), ctx)
  ms = ora.get("momentum_state") or {}
  a.note_field("velocity", ms.get("velocity"), go.get("momentum_velocity"), ctx)
  a.note_field("acceleration", ms.get("acceleration"), go.get("momentum_acceleration"), ctx)
  ok = ora.get("momentum") == go.get("momentum") and close(ms.get("velocity"), go.get("momentum_velocity")) and close(ms.get("acceleration"), go.get("momentum_acceleration"))
  a.identical += int(ok)
  if not ok:
    bad.append("momentum")

  # --- scalp structure
  a = accs["scalp_barriers"]
  a.checkpoints += 1
  ok = compare_lists(
    a, ora.get("scalp_barriers", []), go.get("scalp_barriers", []),
    lambda b: (b["side"], round(b["level"], 6)), f("touches", "wick_rejections", "accepted_closes", "score", "grade"), ctx,
  )
  a.identical += int(ok)
  if not ok:
    bad.append("scalp_barriers")
  a = accs["scalp_range"]
  a.checkpoints += 1
  orange, grange = ora.get("scalp_range"), go.get("scalp_range")
  if orange is None or grange is None:
    ok = orange is None and grange is None
    a.field_diffs["presence"] += int(not ok)
  else:
    ok = True
    for k, ok_key, gk in (("eq", "eq", "eq"), ("width_atr", "width_atr", "width_atr"), ("quality", "quality", "quality"), ("state", "state", "state")):
      a.note_field(k, orange.get(ok_key), grange.get(gk), ctx)
      ok &= close(orange.get(ok_key), grange.get(gk))
  a.identical += int(ok)
  if not ok:
    bad.append("scalp_range")
  return bad


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--oracle", required=True)
  parser.add_argument("--go", required=True)
  parser.add_argument("--out", required=True)
  parser.add_argument("--go-zones", default="zones")
  parser.add_argument("--go-levels", default="levels")
  args = parser.parse_args()
  ora = {}
  for line in open(args.oracle):
    r = json.loads(line)
    ora[r["bar"]] = r
  go = {}
  for line in open(args.go):
    r = json.loads(line)
    go[r["bar"]] = r
  common = sorted(set(ora) & set(go))
  report = {"oracle": args.oracle, "go": args.go, "oracle_checkpoints": len(ora), "go_checkpoints": len(go), "common_checkpoints": len(common), "frames": {}}
  first_bad = collections.defaultdict(collections.Counter)
  for tf in ("M5", "M15", "H1"):
    accs = {name: Acc() for name in LAYER_ORDER}
    n = 0
    for bar in common:
      o = ora[bar]["frames"].get(tf)
      g = go[bar]["frames"].get(tf)
      if o is None or g is None:
        continue
      n += 1
      bad = compare_frame(accs, o, g, {"tf": tf, "bar": bar, "symbol": ora[bar]["symbol"]}, args.go_zones, args.go_levels)
      first = next((layer for layer in LAYER_ORDER if layer in bad), "none")
      first_bad[tf][first] += 1
    report["frames"][tf] = {"checkpoints": n, "earliest_divergence": dict(first_bad[tf]), "layers": {k: v.out() for k, v in accs.items()}}
  with open(args.out, "w") as h:
    json.dump(report, h, indent=1, sort_keys=True, default=str)
  print(json.dumps({tf: {"n": v["checkpoints"], "earliest_divergence": v["earliest_divergence"]} for tf, v in report["frames"].items()}))


if __name__ == "__main__":
  main()
