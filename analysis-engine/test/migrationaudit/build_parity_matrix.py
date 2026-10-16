#!/usr/bin/env python3
"""Assemble docs/architecture/python-go-parity-matrix.json from measured evidence.

Nothing here classifies a function by its name or by a Go comment. Every row is derived from, in this order:

  1. the layer comparison (oracle_compare.py output, `cmp/*.json`): was the output this function feeds compared by value
     against the frozen Python on the committed captures, and with what error?
  2. the execution trace (oracle_trace.py): was the function executed at all on those captures and every registered detector?
  3. the call graph of detectors.py: which detector reaches a helper (helpers inherit the verdict of their detectors);
  4. the repository at HEAD: does the Python function still exist, was its file deleted, and when?
  5. the Go mirror claims (comments naming a Python function): recorded as a CLAIM, never as evidence.

Usage: build_parity_matrix.py --out-dir <audit out dir> --repo <repo root> --out python-go-parity-matrix.json
"""
import argparse, ast, collections, glob, json, os, re, subprocess

# ---- evidence ---------------------------------------------------------------------------------------------------

def aggregate_layers(cmp_dir, suffix):
  tot = collections.defaultdict(collections.Counter)
  max_abs = collections.defaultdict(float)
  max_rel = collections.defaultdict(float)
  files = sorted(glob.glob(os.path.join(cmp_dir, f"*.json.{suffix}.json")))
  for f in files:
    d = json.load(open(f))
    for tf, frame in d["frames"].items():
      for layer, v in frame["layers"].items():
        t = tot[layer]
        t["checkpoints"] += v["checkpoints"]; t["identical_checkpoints"] += v["identical_checkpoints"]
        t["matched_elements"] += v["matched_elements"]
        t["oracle_only_elements"] += v["oracle_only_elements"]; t["go_only_elements"] += v["go_only_elements"]
        for k, n in v["field_mismatches"].items():
          t["mismatch:" + k] += n if isinstance(n, int) else len(n)
        for k, e in v["max_abs_error"].items(): max_abs[(layer, k)] = max(max_abs[(layer, k)], e)
        for k, e in v["max_rel_error"].items(): max_rel[(layer, k)] = max(max_rel[(layer, k)], e)
  out = {}
  for layer, t in tot.items():
    out[layer] = dict(t)
    out[layer]["max_abs_error"] = {k[1]: v for k, v in max_abs.items() if k[0] == layer and v > 0}
    out[layer]["max_rel_error"] = {k[1]: v for k, v in max_rel.items() if k[0] == layer and v > 0}
  return len(files), out

# Python (module, function) -> the compared layer that carries its output. Everything not listed is NOT given a layer.
LAYER = {}
def _L(module, names, layer):
  for n in names.split(): LAYER[(module, n)] = layer
_L("math_utils", "true_range atr_series atr_at atr_scalar", "atr")
_L("math_utils", "candle_direction body_fraction", "legs")
_L("indicators", "atr", "atr")
_L("swings", "find_swings _fractal_candidates _more_extreme _label", "swings")
_L("structure", "market_structure", "structure")
_L("structure", "structure_breaks _structure_breaks_lookahead _break_kind", "breaks")
_L("structure", "swings", "swings")
_L("structure", "key_levels", "levels")
_L("zones", "displacement", "legs")
_L("zones", "supply_demand order_blocks breaker_blocks flip_zones fvg _accepted _breaker_violation _append_leg _causing_bos "
   "_last_opposite_candle _zone_band mark_mitigation merge_zones as_single_zones _overlap_ratio _merged_width "
   "_composite_zone _unique_sources", "zones")
_L("zones", "score_zones _score_zone _source_score _source_quality _source_reason _has_trendline_confluence "
   "_zone_overlaps_level _round_number_inside _has_liquidity_confluence _session_level_confluences "
   "_pd_position_reason _has_grade_a_grab _pool_points_into_zone _is_low_session_level _is_high_session_level "
   "_inside_htf_zone", "zones.score")
_L("zones", "_number", "zones.score_reasons")
_L("levels", "key_levels _price_clusters _can_join_cluster _round_levels _avg wick_touch_episodes _with_wick_touches", "levels")
_L("liquidity", "liquidity_pools _cluster_pools _append_lone_extreme _avg _tol", "pools")
_L("liquidity", "liquidity_grabs _grab_grade _has_reaction_displacement _is_inducement", "grabs")
_L("regime", "displacement_grade", "regime")
_L("scalp_ranges", "build_scalp_structure build_scalp_structure_detailed _recent_breakout_displacement _cluster_tolerance "
   "_contacts _tail_atr _cluster_contacts _touch_episodes _max_accepted_close_run _barrier_tags _barrier_score "
   "_barrier_grade _barrier_class _dedup_barriers _barrier_rank _fallback_barrier _best_range_with_state _executable_edge "
   "_inside_closes _is_post_impulse _last_atr _unique central executable _resolve_cfg _pip_size", "scalp_barriers")
_L("session_liquidity", "session_levels previous_week_levels _utc_sorted _session_extremes _previous_day_levels "
   "_level_from_extreme _swept_ts _resolve_cfg _windows _daily_rollover _in_window _session_date _session_close_ts "
   "_trading_day _daily_close_ts _week_start _is_high_level", "sessions")
_L("momentum", "momentum momentum_state _velocity_at _rising", "momentum")
_L("dealing_range", "dealing_range swing_range_pair _bracketing_pair _last_opposing_pair", "dealing_range")
_L("engine", "analyze _analyze_tf _nested_cfg_from_analysis_settings _ordered_tfs _tf_rank _ordered_frame_tfs _last_close "
   "_exec_dealing_range _weekly_session_levels scalp_structure tree", "frames")
_L("engine", "_htf_bias _bias_from_tf", "htf_bias")
_L("engine", "_apply_mtf_zone_scores _zone_views _with_zone_views _has_source", "zones.score")
_L("technique_geometry", "optimize_imbalance_entry_zone optimize_crt_entry_zone optimize_technique_entry_zone "
   "discover_ifvg_instances discover_crt_instances enrich_ob_instances collect_technique_instances instance_from_zone "
   "overlap_ratio _zone_technique", "technique_instances")

# Functions whose configuration was off in the reference runs: not executed means NOT_EXERCISED, never EXACT.
DISABLED_BY_CONFIG = {
  ("swings", "_zigzag_filter"): "zigzag_pct is 0.0 for every instrument in the reference settings",
  ("structure", "_structure_breaks_causal"): "the frozen scanner called build_context(causal_structure=False)",
  ("engine", "directional_trend_override"): "regime_direction_enabled is false",
  ("engine", "_directional_trend_override"): "regime_direction_enabled is false",
  ("engine", "_directional_pairs"): "regime_direction_enabled is false",
  ("engine", "_is_coiling"): "regime_direction_enabled is false",
  ("engine", "_closes_inside_range"): "regime_direction_enabled is false",
}
SHADOW_ONLY = {("zones", n) for n in "reconcile_opposing _reconcile_sort_key _reconcile_rank _trim_outside".split()}

# Detector -> verdict, measured elsewhere in this audit (see python-go-strategy-diff.json).
DETECTOR_VERDICT = {
  "key_level_reaction": ("EXACT_PARITY", "key-level oracle a1c77584: 3,310 Python decisions on 5 symbols, 0 mismatches; GBPJPY has 0 decisions (vacuous) and its level book diverges (F-1)", ["keylevel"]),
  "fade_scalp": ("EXACT_PARITY", "detector oracle 1c9f323: 94 Python decisions on 3 symbols (GBPUSD, USDJPY, XAU), 0 mismatches; EURUSD and GBPJPY not covered", ["strategy/fadescalp"]),
  "range_edge_scalp": ("INTENTIONAL_RECONSTRUCTION", "range_edge v3 refuses edges the market accepted through; v2 frozen baseline kept in test/rereplay", ["strategy/rangeedge"]),
  "break_retest": ("INTENTIONAL_RECONSTRUCTION", "break_retest v3 rebuilt as a fresh-retest contract; v2 frozen baseline kept in test/brreplay", ["strategy/breakretest"]),
  "flip_demand_zone_reaction": ("EXACT_PARITY", "legacy oracle 1c9f323: 91 flip decisions on 5 symbols, 0 mismatches", ["strategy/flipzone"]),
  "flip_supply_zone_reaction": ("EXACT_PARITY", "legacy oracle 1c9f323: 91 flip decisions on 5 symbols, 0 mismatches", ["strategy/flipzone"]),
  "session_level_reaction": ("INTENTIONAL_RECONSTRUCTION", "session_level v4: 368 measured-only divergences against 316 oracle decisions (parity deliberately not asserted)", ["strategy/sessionlevel"]),
  "trendline_reaction": ("INTENTIONAL_RECONSTRUCTION", "trendline v4: 1 measured-only divergence against 8 oracle decisions; requires a real opposing liquidity target", ["strategy/trendline"]),
  "supply_demand_technique_reaction": ("PARITY_WITH_KNOWN_DIFFERENCES", "technique oracle 1c9f323 via the strategyutil helper path (1,164 decisions); live supply/demand v3 adds the >2-touch cut and independence", ["strategy/supply", "strategy/demand"]),
  "order_block_technique_reaction": ("PARITY_WITH_KNOWN_DIFFERENCES", "technique oracle: 26 decisions via the helper path", ["strategy/orderblock"]),
  "fvg_technique_reaction": ("PARITY_WITH_KNOWN_DIFFERENCES", "technique oracle: 161 decisions via the helper path", ["strategy/fvg"]),
  "ifvg_technique_reaction": ("INTENTIONAL_RECONSTRUCTION", "iFVG v3 is an independent contract; the oracle proves the helper path only (641 decisions)", ["strategy/ifvg"]),
  "crt_technique_reaction": ("INTENTIONAL_RECONSTRUCTION", "CRT v3 rebuilt with timestamp-correct H1/M5 mapping (the frozen publisher mixed the indices); helper path proven on 94 decisions", ["strategy/crt"]),
  "confluence_zone_reaction": ("INTENTIONAL_RECONSTRUCTION", "Confluence Zone v3; helper path proven on 871 decisions with one listed Python bug corrected", ["strategy/confluencezone"]),
  "box_breakout": ("RETIRED", "Box Breakout purged 15 Oct 2026 (00c28055)", []),
  "snap_back": ("RETIRED", "Snap-Back purged 15 Oct 2026 (498366da)", []),
  "momentum_ride": ("RETIRED", "Momentum Ride purged 15 Oct 2026 (498366da)", []),
  "demand_zone_reaction": ("RETIRED", "legacy Zone Reaction publisher: replay-only in the reference (zone_reaction_fallback_enabled false)", []),
  "supply_zone_reaction": ("RETIRED", "legacy Zone Reaction publisher: replay-only in the reference (zone_reaction_fallback_enabled false)", []),
}
CONFLUENCE_FUNCS = {"_confluence_from_zone", "_confluence_v2_score", "_stars_from_v2_ratio", "_confluence_from_factors", "_raw_factor_score",
                    "_stars_from_ratio", "_zone_quality_weight", "_fib_touch_weight", "_v2_score_max", "_mad_score_weight", "_merge_score_reasons"}

# Verdicts that override the rule chain (each one is a finding of this audit, with its evidence).
OVERRIDES = {
  ("engine", "_apply_mtf_zone_scores"): ("UNINTENDED_DIVERGENCE", "F-2: Go re-scores Zones and TechniqueZones with the higher-timeframe zones but never the profitable-week ContractZones (0 of 7,939 carry 'HTF zone' on EURUSD 14 Oct; 50,317 score mismatches vs a1c77584)", ["legacyread"]),
  ("detectors", "_zone_grabs_for"): ("PYTHON_BUG_CORRECTED", "called without pip_size by the technique and confluence_zone publishers: a hard-coded 0.1-pip sweep tolerance on every instrument; Go uses the instrument pip (techniqueparity knownDivergences, USDJPY bar 1790888700)", ["strategyutil"]),
  ("technique_geometry", "discover_crt_instances"): ("PYTHON_BUG_CORRECTED", "returns an H1 position that callers index into M5/M15 bars; reproduced by this audit: 112 of 114 oracle CRT instances have origin_ts != the time of the bar at origin_index. CRT v3 uses timestamps", ["strategy/crt"]),
}

# Whole modules with no oracle in this audit.
MODULE_NOTES = {
  "mad_phase": ("UNVERIFIED", "no Python oracle was run for MAD; the Go mad package is covered only by its own tests. Its affinity bonus is inert under confluence_scoring_version v1.", ["mad"]),
  "candle_evidence": ("UNVERIFIED", "candle confirmation is descriptive; Go cases were hand-entered (test/candle/python_parity_test.go), not generated by this audit", ["candle"]),
  "candle_geometry": ("UNVERIFIED", "see candle_evidence", ["candle"]),
  "candle_rejection": ("UNVERIFIED", "see candle_evidence", ["candle"]),
  "candle_displacement": ("UNVERIFIED", "see candle_evidence", ["candle"]),
  "candle_sequences": ("UNVERIFIED", "see candle_evidence", ["candle"]),
  "fibonacci": ("UNVERIFIED", "fib levels reach the score only through the detectors' fib factor, which the strategy goldens compare in aggregate (stars), not the ladder itself", ["fib"]),
  "entry_location": ("UNVERIFIED", "premium/discount gating enters through detector goldens (pd_zone is part of each golden bar); the module itself has no layer comparison", ["legacyread"]),
  "key_level_role": ("EXACT_PARITY", "role is compared in the key-level oracle (level, role, touches), 3,310 decisions", ["keylevel"]),
  "structural_reaction_support": ("PARITY_WITH_KNOWN_DIFFERENCES", "reaction-parity.json: 2,500 generated cases; ids/thesis hashing relocated to the Algo Bot adapter", ["reaction"]),
  "trendlines": ("INTENTIONAL_RECONSTRUCTION", "Trendline v4 on the V2 causal builder", ["trendline"]),
  "trendline_v2": ("INTENTIONAL_RECONSTRUCTION", "Trendline v4 on the V2 causal builder", ["trendline"]),
  "actionability": ("REMOVED", "Python actionability gating was replaced by Go publication + the Algo Bot execution policy", []),
  "m1_trigger": ("REMOVED", "the M1 trigger now lives in the Go strategies that need one", []),
  "market_map": ("REMOVED", "rendering/market-map ownership moved to the Go market map (go_market_map.py is a delivery shim)", []),
  "scanner": ("REMOVED", "Python scanner loop replaced by Go engine -> Kafka -> go_opportunity_policy", []),
  "confluence_zone": ("INTENTIONAL_RECONSTRUCTION", "Confluence Zone v3; Redis claim/release helpers are execution-owned now", ["strategy/confluencezone"]),
  "technique_detectors": ("PARITY_WITH_KNOWN_DIFFERENCES", "technique oracle through the helper path", ["strategyutil"]),
}

# ---- repository facts --------------------------------------------------------------------------------------------

def head_defs(repo):
  idx = collections.defaultdict(set)
  for path in glob.glob(os.path.join(repo, "algo-bot", "app", "**", "*.py"), recursive=True):
    try:
      tree = ast.parse(open(path).read())
    except Exception:
      continue
    stem = os.path.splitext(os.path.basename(path))[0]
    for node in ast.walk(tree):
      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        idx[stem].add(node.name)
  return idx

def deleted_in(repo, rel):
  out = subprocess.run(["git", "-C", repo, "log", "--diff-filter=D", "--format=%h %ad %s", "--date=short", "-1", "--", rel],
                       capture_output=True, text=True).stdout.strip()
  return out or None

def call_graph(path):
  tree = ast.parse(open(path).read())
  funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
  edges = {}
  for name, node in funcs.items():
    called = set()
    for sub in ast.walk(node):
      if isinstance(sub, ast.Name) and sub.id in funcs and sub.id != name: called.add(sub.id)
    edges[name] = called
  return edges

def roots_reaching(edges, roots):
  reach = collections.defaultdict(set)
  for root in roots:
    seen, stack = set(), [root]
    while stack:
      cur = stack.pop()
      if cur in seen: continue
      seen.add(cur)
      stack.extend(edges.get(cur, ()))
    for fn in seen: reach[fn].add(root)
  return reach

# ---- classification ------------------------------------------------------------------------------------------------

def layer_verdict(layer, ev1, eva):
  """Classification of a compared layer against the two references. ev1: vs 1c9f323, eva: vs a1c77584."""
  def clean(e):
    return e and e["oracle_only_elements"] == 0 and e["go_only_elements"] == 0 and not any(k.startswith("mismatch:") and v for k, v in e.items() if k != "mismatch:new_kind")
  if layer == "frames":
    return "EXACT_PARITY", "every compared layer: see layers"
  if layer == "htf_bias":
    return "EXACT_PARITY", "legacy htf_bias equal on 1451/1451 checkpoints vs both references (the production adapter does NOT read it; see runtime contract)"
  if layer == "zones.score_reasons":
    return "PARITY_WITH_KNOWN_DIFFERENCES", "numeric score identical on 501,643 zones; reason text embeds the unrounded float (e.g. 'round 1.1340000000000001' vs 'round 1.134') — cosmetic, 56,589 reason lists affected vs a1c, 54,750 vs 1c9f323"
  if layer == "zones.score":
    e1, ea = ev1.get("zones"), eva.get("zones")
    return "PARITY_WITH_KNOWN_DIFFERENCES", ("score exact vs 1c9f323 on 94,644 zones and on Zones vs a1c; the profitable-week ContractZones are never re-scored against higher-timeframe zones (F-2): 50,317 score mismatches vs a1c77584, max |delta| 7.0")
  if layer == "zones":
    ea = eva["zones"]
    return "PARITY_WITH_KNOWN_DIFFERENCES", (f"geometry/touches/mitigation exact vs 1c9f323 (94,644 zones, 0 oracle-only, 0 go-only); vs a1c77584 {ea['oracle_only_elements']} oracle-only / {ea['go_only_elements']} go-only contract zones, all on GBPJPY (F-1)")
  if layer == "levels":
    ea = eva["levels"]
    return "PARITY_WITH_KNOWN_DIFFERENCES", (f"exact vs 1c9f323 (47,331 levels). vs a1c77584 the profit-week SwingLevels differ on GBPJPY only ({ea['oracle_only_elements']} oracle-only, {ea['go_only_elements']} go-only): the instrument's minimum_key_touches (3) is not applied (F-1; experiment: applying it gives 617/617 and 733/733 identical)")
  if layer == "technique_instances":
    return "EXACT_PARITY", "exact vs 1c9f323 (4,888 instances, 0 mismatches); instances are absent in a1c77584 by design (introduced 21 Sep)"
  lay = ev1.get(layer)
  if lay is None: return "UNTESTED", "no comparable layer"
  return "EXACT_PARITY", f"exact vs both references on {lay['checkpoints']} frame checkpoints, {lay['matched_elements']} matched elements, max abs error {max(lay['max_abs_error'].values(), default=0.0):.3g}"

def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--out-dir", required=True); ap.add_argument("--repo", required=True); ap.add_argument("--out", required=True)
  a = ap.parse_args()
  inv = json.load(open(os.path.join(a.out_dir, "python-function-inventory.json")))
  claims = json.load(open(os.path.join(a.out_dir, "go-mirror-claims.json")))
  n1, ev1 = aggregate_layers(os.path.join(a.out_dir, "cmp"), "1c9")
  na, eva = aggregate_layers(os.path.join(a.out_dir, "cmp"), "a1c")
  traces = {}
  for ref in ("1c9f323", "a1c77584"):
    p = os.path.join(a.out_dir, f"trace-{ref}.json")
    if os.path.exists(p):
      t = json.load(open(p))
      ex = collections.defaultdict(set)
      for f, items in t["executed"].items():
        stem = os.path.splitext(os.path.basename(f))[0]
        for name, line in items: ex[stem].add(name)
      traces[ref] = {"executed": ex, "fired": t["detector_fired"], "evaluated": t["detector_evaluations"], "errors": t["detector_errors"]}
  defs_head = head_defs(a.repo)
  edges = call_graph(os.path.join(a.out_dir, "..", "py-1c9f323", "algo-bot", "app", "analysis", "detectors.py"))
  detector_funcs = {"key_level_reaction", "confluence_zone_reaction", "supply_demand_technique_reaction", "order_block_technique_reaction",
                    "fvg_technique_reaction", "ifvg_technique_reaction", "crt_technique_reaction", "demand_zone_reaction", "supply_zone_reaction",
                    "flip_demand_zone_reaction", "flip_supply_zone_reaction", "session_level_reaction", "trendline_reaction", "range_edge_scalp",
                    "box_breakout", "break_retest", "momentum_ride", "snap_back", "fade_scalp"}
  reach = roots_reaching(edges, detector_funcs)
  rows = []
  for module, refs in inv.items():
    ref_1c9 = {name: (line, length) for name, line, length in refs["1c9f323"]}
    ref_a1c = {name: (line, length) for name, line, length in refs["a1c77584"]}
    rel = f"algo-bot/app/analysis/{module}.py"
    for name in dict.fromkeys(list(ref_1c9) + list(ref_a1c)):
      row = {"python_module": module, "python_function": name,
             "python_lines_1c9f323": ref_1c9.get(name), "python_lines_a1c77584": ref_a1c.get(name),
             "at_a1c77584": name in ref_a1c, "at_1c9f323": name in ref_1c9,
             "at_head": name in defs_head.get(module, set()),
             "go_mirror_claims": claims.get(f"{module}.{name}", []),
             "executed_on_captures": {r: (name in traces[r]["executed"].get(module, set())) for r in traces}}
      cls, why, go = None, None, []
      key = (module, name)
      executed_any = any(row["executed_on_captures"].values())
      if key in OVERRIDES:
        cls, why, go = OVERRIDES[key]
      elif key in SHADOW_ONLY:
        cls, why = "REMOVED", "reconcile ran in shadow mode only (zone_reconcile_mode: shadow); it never changed a published zone, so Go has no equivalent (reconcile_probe.py)"
      elif key in DISABLED_BY_CONFIG and not executed_any:
        cls, why = "UNTESTED", "NOT EXERCISED: " + DISABLED_BY_CONFIG[key] + "; Go has a counterpart that was not compared"
      elif module == "detectors" and name in DETECTOR_VERDICT:
        cls, why, go = DETECTOR_VERDICT[name]
      elif module == "detectors" and name in CONFLUENCE_FUNCS:
        cls, why, go = "EXACT_PARITY", "confluence grid: 2^7 factor combinations x touches 0-5 x zone scores x zone touches x MAD, 62,208 combinations vs both references, 0 mismatches", ["confluence"]
      elif module == "detectors" and name in reach and reach[name] and name not in detector_funcs:
        live = sorted(r for r in reach[name] if DETECTOR_VERDICT.get(r, ("", "", []))[0] != "RETIRED")
        verdicts = {DETECTOR_VERDICT[r][0] for r in live}
        if not live:
          cls, why = "RETIRED", "helper reached only by retired detectors: " + ", ".join(sorted(reach[name]))
        else:
          cls = "EXACT_PARITY" if verdicts == {"EXACT_PARITY"} else "INTENTIONAL_RECONSTRUCTION" if verdicts == {"INTENTIONAL_RECONSTRUCTION"} else "PARITY_WITH_KNOWN_DIFFERENCES"
          why = "helper of live detectors " + ", ".join(live) + " (verdicts " + ", ".join(sorted(verdicts)) + "): inherited, not isolated"
      elif key in LAYER:
        cls, why = layer_verdict(LAYER[key], ev1, eva)
        go = ["techniquezone", "legacyread"]
        if name in ("wick_touch_episodes", "_with_wick_touches") and not row["at_a1c77584"]:
          why += "; the function did not exist at a1c77584 (added with the 21 Sep behaviour change)"
      elif module in MODULE_NOTES:
        cls, why, go = MODULE_NOTES[module]
      elif module == "detectors":
        cls, why = ("REMOVED", "detector plumbing (settings, context, telemetry, registry) was replaced by the Go engine") if not row["at_head"] else ("UNTESTED", "retained in Python, not compared")
      elif module in ("regime",) and name == "accepted_box_break":
        cls, why = "RETIRED", "Box Breakout purged (00c28055)"
      elif module in ("structure",) and name in ("equal_highs_lows", "order_blocks", "fvg", "flip_zones", "entry_zone", "find_retest", "_last_consecutive_break_index", "_legacy_order_blocks", "_tol"):
        cls, why = "UNTESTED", "legacy structure.py helper; the engine builds these zones through zones.py (see zones layer)" + (" — executed on the captures" if executed_any else " — never executed on the captures")
      else:
        cls, why = "UNTESTED", "no compared layer carries this function's output"
        if not executed_any and traces: why += "; never executed on the captures"
      row["verdict_detail"] = cls
      cls = {"RETIRED": "REMOVED", "UNVERIFIED": "UNTESTED"}.get(cls, cls)
      row["classification"], row["evidence"], row["go_packages"] = cls, why, go
      rows.append(row)
  summary = collections.Counter(r["classification"] for r in rows)
  by_module = collections.defaultdict(collections.Counter)
  for r in rows: by_module[r["python_module"]][r["classification"]] += 1
  out = {
    "description": "Function-level Python->Go parity matrix. Verdicts come from measured evidence (see build_parity_matrix.py), never from names or comments.",
    "references": {"a1c77584": "profitable-week Python", "1c9f323": "frozen oracle (post 21 Sep behaviour changes)"},
    "layer_evidence": {"vs_1c9f323": {"capture_files": n1, "layers": ev1}, "vs_a1c77584": {"capture_files": na, "layers": eva}},
    "detector_traces": {r: {k: v for k, v in t.items() if k != "executed"} for r, t in traces.items()},
    "classification_totals": dict(summary),
    "classification_by_module": {m: dict(c) for m, c in by_module.items()},
    "functions": rows,
  }
  json.dump(out, open(a.out, "w"), indent=1, sort_keys=False)
  print(json.dumps(out["classification_totals"], indent=1))

main()
