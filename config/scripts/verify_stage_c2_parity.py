#!/usr/bin/env python3
"""Stage C2 divergence-aware parity check.

Stage C1 (verify_stage_c1_parity.py) proved a pure mechanical split: every
leaf in the new categorized YAML equaled the old trading-bot.yml value at
the corresponding path, no exceptions. Stage C2 (rebuild-configuration-
architecture.md §3-§11: derive symbol/feed/timezone duplicates, move
price-denominated geometry to instruments.yml alone, native YAML types
instead of CSV strings, nested overrides instead of dotted keys) is no
longer a pure split — it deliberately changes some representations and
removes some duplicated leaves.

Every one of those changes is a *documented, individually justified*
divergence, per this prompt's own §41 ("For every divergence: old value,
new value, reason, affected consumer... must be documented"). This script
is that documentation made executable: DIVERGENCES below is the complete,
exhaustive list. Note most of instruments.yml's dotted-override ->
nested-override conversions are NOT listed here at all: flattening a
dotted key (`execution.technique.foo: 1`) and flattening the equivalent
nested mapping (`execution: {technique: {foo: 1}}`) produce the identical
joined path string with the identical value, so Stage C1's own leaf-by-
leaf comparison already passes for those transparently — they are real
edits (see instruments.yml's own comments) but not observable divergences
from the flattened old file, and listing them here would just be noise.
The ones that ARE listed below all share one real content change: they
also renamed the leaf's *category root* while un-dotting it (e.g. the old
dotted key started `actionability....`, the new nested key starts
`auto_algo.actionability....`, matching where that setting actually now
lives per docs/configuration-v3-migration-audit.md's category table) —
that one added path segment is what makes the flattened strings differ,
not the dotted-vs-nested mechanics.

Running this script proves two things at once:

  1. Every leaf NOT in DIVERGENCES still matches Stage C1's old-path
     value exactly (delegates to verify_stage_c1_parity's own logic,
     narrowed to skip only the listed exceptions).
  2. Every leaf IN DIVERGENCES matches its documented new/expected shape
     exactly — so a future accidental value change shows up here too,
     not just an accidental removal.

Run from the repository root (same requirements as verify_stage_c1_parity.py):

    cd algo-bot && PYTHONPATH=. .venv/bin/python \
      ../config/scripts/verify_stage_c2_parity.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "config"

errors: list[str] = []
checked = 0


def load(path: Path):
  with path.open() as f:
    return yaml.safe_load(f)


def flatten(value, prefix: str = "") -> dict[str, object]:
  out: dict[str, object] = {}
  if isinstance(value, dict):
    for k, v in value.items():
      key = f"{prefix}.{k}" if prefix else str(k)
      out.update(flatten(v, key))
  elif isinstance(value, list):
    out[prefix] = value
  else:
    out[prefix] = value
  return out


def csv_to_list(raw: str) -> list[str]:
  return [item.strip() for item in raw.split(",")]


def csv_to_list_int(raw: str) -> list[int]:
  return [int(item.strip()) for item in raw.split(",")]


# --- The exhaustive divergence list -----------------------------------
#
# Keyed by "<new file>/<new flattened leaf path>". `old_path` is the
# corresponding old trading-bot.yml path (dotted-key overrides flatten to
# one literal path containing dots, same as everywhere else in this repo's
# scripts). `kind` selects which check applies below.

DIVERGENCES: dict[str, dict] = {
  # analysis.yml — §3/§4: derived-from-instruments duplicates removed.
  "analysis.yml/analysis.scanner.symbols": {
    "kind": "removed", "old_path": "market_data.scanner.symbols",
    "reason": "§3 — derives from instruments.yml (instruments.*.rollout == live), never a second symbol list.",
  },
  "analysis.yml/analysis.ctrader_feed.symbol": {
    "kind": "removed", "old_path": "market_data.ctrader_feed.symbol",
    "reason": "§4 — feed subscription derives from instruments.*.broker_symbol.",
  },
  "analysis.yml/analysis.ctrader_feed.timeframes": {
    "kind": "removed", "old_path": "market_data.ctrader_feed.timeframes",
    "reason": "§4/§5 — feed subscription derives from instruments.*.timeframes.",
  },
  # analysis.yml — §7: price-absolute zone geometry, XAU-shaped global
  # default removed; every live instrument already declares its own via
  # instruments.yml (verified live consumer: app.core.instrument_geometry.
  # merge_max_width/merge_gap_price already resolve per-instrument today).
  "analysis.yml/analysis.zones.confluence.merge_gap_price": {
    "kind": "removed", "old_path": "analysis.zones.confluence.merge_gap_price",
    "reason": "§7 — instrument-scoped only now; old value (1.0) was XAU's own pack number leaking as the global default.",
  },
  "analysis.yml/analysis.zones.merge_max_width": {
    "kind": "removed", "old_path": "analysis.zones.merge_max_width",
    "reason": "§7 — instrument-scoped only now; old value (6.0) was XAU's own pack number leaking as the global default.",
  },
  # analysis.yml — §8/§9: newly surfaced (previously Python-schema-default
  # only, never a trading-bot.yml leaf at all).
  "analysis.yml/analysis.zones.merge_overlap": {
    "kind": "new_surfaced", "expected": 0.5,
    "reason": "§8 — AnalysisZonesConfig.merge_overlap Python schema default (unchanged value), now explicit.",
  },
  "analysis.yml/analysis.measurements.max_merged_zone_atr": {
    "kind": "new_surfaced", "expected": 3.0,
    "reason": "§8 — AnalysisMeasurementsConfig.max_merged_zone_atr Python schema default (unchanged value), now explicit.",
  },
  # analysis.yml — Analysis Engine V2 (docs/analysis/market-structure-v2.md):
  # genuinely new Go-only leaves, no old trading-bot.yml OR Python-schema-
  # default precedent at all (unlike merge_overlap/max_merged_zone_atr
  # above) — analysis-engine's structure/liquidity domains did not exist
  # before this task. Thresholds are grounded in real, already-shipped
  # prior art (PRs #574/#578/#579's hold-based zone validity,
  # technique_geometry.py's epsilon(), zones.py::displacement()'s
  # k/body_frac) — see each key's own comment in config/analysis.yml.
  "analysis.yml/analysis.history.depth.M1": {"kind": "new_surfaced", "expected": 2000, "reason": "Analysis Engine V2 §5 — MarketHistory stored depth, no old-config precedent."},
  "analysis.yml/analysis.history.depth.M5": {"kind": "new_surfaced", "expected": 1000, "reason": "Analysis Engine V2 §5."},
  "analysis.yml/analysis.history.depth.M15": {"kind": "new_surfaced", "expected": 1000, "reason": "Analysis Engine V2 §5."},
  "analysis.yml/analysis.history.depth.H1": {"kind": "new_surfaced", "expected": 500, "reason": "Analysis Engine V2 §5."},
  "analysis.yml/analysis.history.depth.H4": {"kind": "new_surfaced", "expected": 250, "reason": "Analysis Engine V2 §5."},
  "analysis.yml/analysis.history.depth.D1": {"kind": "new_surfaced", "expected": 150, "reason": "Analysis Engine V2 §5."},
  "analysis.yml/analysis.structure.version": {"kind": "new_surfaced", "expected": "v2", "reason": "Analysis Engine V2 §67 — versioned algorithm selection."},
  "analysis.yml/analysis.structure.pivot.left_bars": {"kind": "new_surfaced", "expected": 2, "reason": "Matches Python's common swing_fractal_n=2 default."},
  "analysis.yml/analysis.structure.pivot.right_bars": {"kind": "new_surfaced", "expected": 2, "reason": "Matches Python's common swing_fractal_n=2 default."},
  "analysis.yml/analysis.structure.swing.minimum_excursion_atr": {"kind": "new_surfaced", "expected": 0.5, "reason": "No Python precedent — new V2 layer-promotion floor, initial calibration."},
  "analysis.yml/analysis.structure.swing.promotion.internal_atr": {"kind": "new_surfaced", "expected": 1.0, "reason": "No Python precedent — new V2 layer hierarchy, initial calibration."},
  "analysis.yml/analysis.structure.swing.promotion.intermediate_atr": {"kind": "new_surfaced", "expected": 2.0, "reason": "No Python precedent — new V2 layer hierarchy, initial calibration."},
  "analysis.yml/analysis.structure.swing.promotion.major_atr": {"kind": "new_surfaced", "expected": 3.5, "reason": "No Python precedent — new V2 layer hierarchy, initial calibration."},
  "analysis.yml/analysis.structure.equal_level.tolerance_atr": {"kind": "new_surfaced", "expected": 0.05, "reason": "Matches technique_geometry.py's epsilon() exactly — 0.05 ATR."},
  "analysis.yml/analysis.structure.break.minimum_penetration_atr": {"kind": "new_surfaced", "expected": 0.5, "reason": "Matches PR #574's invalidation_tolerance_atr exactly."},
  "analysis.yml/analysis.structure.break.displacement_range_atr": {"kind": "new_surfaced", "expected": 1.5, "reason": "Matches zones.py::displacement()'s k=1.5 exactly."},
  "analysis.yml/analysis.structure.break.displacement_body_dominance": {"kind": "new_surfaced", "expected": 0.55, "reason": "Matches zones.py::displacement()'s body_frac=0.55 exactly."},
  "analysis.yml/analysis.structure.break.sweep_reclaim_bars": {"kind": "new_surfaced", "expected": 6, "reason": "Matches PR #574's sweep_reclaim_bars=6 exactly."},
  "analysis.yml/analysis.structure.break.failed_break_reclaim_bars": {"kind": "new_surfaced", "expected": 3, "reason": "No Python precedent — new V2 break-type distinction, initial calibration."},
  "analysis.yml/analysis.liquidity.version": {"kind": "new_surfaced", "expected": "v1", "reason": "Analysis Engine V2 §67 — versioned algorithm selection."},
  "analysis.yml/analysis.liquidity.equal_level_tolerance_atr": {"kind": "new_surfaced", "expected": 0.05, "reason": "Same epsilon as analysis.structure.equal_level.tolerance_atr — one canonical tolerance, not two drifting ones."},
  "analysis.yml/analysis.liquidity.pool_minimum_touches": {"kind": "new_surfaced", "expected": 2, "reason": "No Python precedent — new V2 liquidity-pool significance floor, initial calibration."},
  "analysis.yml/analysis.liquidity.sweep_reclaim_bars": {"kind": "new_surfaced", "expected": 6, "reason": "Matches PR #574's sweep_reclaim_bars=6 exactly."},
  # analysis.yml — Phase S3 canonical Zone domain. These values were
  # previously implicit in Python technique/config defaults; they are now
  # explicit authority for the Go zone lifecycle, relevance, and flip
  # primitives. They have no trading-bot.yml leaf, so pin them here.
  "analysis.yml/analysis.techniques.retest_max_touches": {"kind": "new_surfaced", "expected": 30, "reason": "Phase S3 — canonical Zone lifecycle retest budget."},
  "analysis.yml/analysis.techniques.invalidation_tolerance_atr": {"kind": "new_surfaced", "expected": 0.5, "reason": "Phase S3 — canonical Zone lifecycle invalidation tolerance."},
  "analysis.yml/analysis.techniques.sweep_reclaim_bars": {"kind": "new_surfaced", "expected": 6, "reason": "Phase S3 — canonical Zone lifecycle sweep reclaim window."},
  "analysis.yml/analysis.techniques.max_break_episodes": {"kind": "new_surfaced", "expected": 2, "reason": "Phase S3 — canonical Zone lifecycle break episode budget."},
  "analysis.yml/analysis.zone_relevance.immediate_atr": {"kind": "new_surfaced", "expected": 0.25, "reason": "Phase S3 — canonical Zone relevance classification threshold."},
  "analysis.yml/analysis.zone_relevance.nearby_atr": {"kind": "new_surfaced", "expected": 1.25, "reason": "Phase S3 — canonical Zone relevance classification threshold."},
  "analysis.yml/analysis.zone_relevance.remote_atr": {"kind": "new_surfaced", "expected": 3.0, "reason": "Phase S3 — canonical Zone relevance classification threshold."},
  "analysis.yml/analysis.flip_zone.accept_bars": {"kind": "new_surfaced", "expected": 2, "reason": "Phase S3 — canonical Flip zone close-acceptance window."},
  "analysis.yml/analysis.flip_zone.band_body_fraction": {"kind": "new_surfaced", "expected": 0.5, "reason": "Phase S3 — canonical Flip zone confirmation body threshold."},
  "analysis.yml/analysis.zones.version": {"kind": "new_surfaced", "expected": "v1", "reason": "Phase S3 — versioned canonical Zone domain contract."},
  # analysis.yml — §10: CSV string -> native list, same content.
  "analysis.yml/analysis.triggers.m1.patterns": {
    "kind": "csv_to_list", "old_path": "analysis.triggers.m1.patterns",
  },
  "analysis.yml/analysis.calendar.currencies": {
    "kind": "csv_to_list", "old_path": "market_data.calendar.currencies",
  },
  "analysis.yml/analysis.calendar.oil_keywords": {
    "kind": "csv_to_list", "old_path": "market_data.calendar.oil_keywords",
  },
  "analysis.yml/analysis.scanner.htf": {
    "kind": "csv_to_list", "old_path": "market_data.scanner.htf",
  },
  # auto-algo.yml — §8/§9: newly surfaced. Found by the JSON Schema
  # itself: demo_eval.yml's overlay set these with no base value to merge
  # onto until this pass added one (see auto-algo.yml's own comments).
  "auto-algo.yml/auto_algo.risk.exposure.allow_hedged_xau": {
    "kind": "new_surfaced", "expected": False, "old_path": "risk.exposure.allow_hedged_xau",
    "reason": "§8 — RiskExposureConfig.allow_hedged_xau Python schema default (unchanged value).",
  },
  "auto-algo.yml/auto_algo.risk.exposure.require_flat_for_range": {
    "kind": "new_surfaced", "expected": True, "old_path": "risk.exposure.require_flat_for_range",
    "reason": "§8 — RiskExposureConfig.require_flat_for_range Python schema default (unchanged value).",
  },
  "auto-algo.yml/auto_algo.strategies.range_reversion.enabled": {
    "kind": "new_surfaced", "expected": True, "old_path": "strategies.range_reversion.enabled",
    "reason": "§8 — StrategiesRangeReversionConfig.enabled Python schema default (unchanged value).",
  },
  # auto-algo.yml — §7, same reasoning as the analysis.yml zone fields.
  "auto-algo.yml/auto_algo.risk.exposure.opposing_minimum_separation_price": {
    "kind": "removed", "old_path": "risk.exposure.opposing_minimum_separation_price",
    "reason": "§7 — instrument-scoped only now (app.core.instrument_geometry.opposing_minimum_separation_price already resolves per-instrument); old value (15.0) was XAU's own pack number.",
  },
  "auto-algo.yml/auto_algo.strategies.scalping.target.preferred_ladder_pips": {
    "kind": "csv_to_list_int", "old_path": "strategies.scalping.target.preferred_ladder_pips",
  },
  # execution.yml — §10.
  "execution.yml/execution.technique.strict_premium_discount_archetypes": {
    "kind": "csv_to_list", "old_path": "execution.technique.strict_premium_discount_archetypes",
  },
  "execution.yml/execution.targeting.default_ladder_pips": {
    "kind": "csv_to_list_int", "old_path": "execution.targeting.default_ladder_pips",
  },
  "execution.yml/execution.targeting.range_ladder_pips": {
    "kind": "csv_to_list_int", "old_path": "execution.targeting.range_ladder_pips",
  },
  # telegram.yml — §6.
  "telegram.yml/telegram.presentation.seq_reset_tz": {
    "kind": "removed", "old_path": "delivery.presentation.seq_reset_tz",
    "reason": "§6 — DERIVED from runtime.timezone (identical value, Asia/Ho_Chi_Minh; confirmed the same one operational timezone via a 13-call-site grep, not narrowly Telegram-scoped as first assumed in Stage C1).",
  },
  # instruments.yml — §11 conversions that ALSO renamed the leaf's category
  # root (actionability./strategies./risk. -> auto_algo.actionability./
  # auto_algo.strategies./auto_algo.risk.), so the flattened path genuinely
  # differs from the old one, unlike the plain dotted->nested conversions
  # (execution.technique.*, analysis.levels.*) which are not listed here at
  # all — see this file's own docstring.
  "instruments.yml/instrument_packs.xau_fixed_4r_v1.overrides.auto_algo.actionability.target_room.barrier_buffer_atr": {
    "kind": "renested", "old_path": "instrument_packs.xau_fixed_4r_v1.overrides.actionability.target_room.barrier_buffer_atr",
  },
  "instruments.yml/instruments.GBPJPY.overrides.auto_algo.actionability.gates.event_cluster_guard_enabled": {
    "kind": "renested", "old_path": "instruments.GBPJPY.overrides.actionability.gates.event_cluster_guard_enabled",
  },
  "instruments.yml/instruments.GBPJPY.overrides.auto_algo.strategies.reaction.key_level.require_explicit_role": {
    "kind": "renested", "old_path": "instruments.GBPJPY.overrides.strategies.reaction.key_level.require_explicit_role",
  },
  "instruments.yml/instruments.GBPJPY.overrides.auto_algo.strategies.reaction.key_level.min_grade": {
    "kind": "renested", "old_path": "instruments.GBPJPY.overrides.strategies.reaction.key_level.min_grade",
  },
  "instruments.yml/instruments.USDJPY.overrides.auto_algo.risk.exposure.defended_level_buffer_price": {
    "kind": "renested", "old_path": "instruments.USDJPY.overrides.risk.exposure.defended_level_buffer_price",
  },
  "instruments.yml/instruments.USDJPY.overrides.auto_algo.risk.exposure.defended_levels": {
    "kind": "new_shape", "old_path": "instruments.USDJPY.overrides.risk.exposure.defended_levels", "expected": [160.0],
    "reason": "§10/§19 — native float list, was the string '160'; category root also renamed to auto_algo.risk.* (see the 'renested' entries above). Python field is currently str (comma-parsed), app/configuration/models/risk.py — model update to list[float] tracked for C3, not done here.",
  },
}


def verify_divergences() -> None:
  """Each documented divergence must have exactly the expected new shape."""
  global checked
  files = {
    "analysis.yml": load(CONFIG / "analysis.yml"),
    "auto-algo.yml": load(CONFIG / "auto-algo.yml"),
    "execution.yml": load(CONFIG / "execution.yml"),
    "telegram.yml": load(CONFIG / "telegram.yml"),
    "instruments.yml": load(CONFIG / "instruments.yml"),
  }
  old_flat = flatten(load(CONFIG / "trading-bot.yml"))
  new_flat_by_file = {name: flatten(doc) for name, doc in files.items()}

  for key, spec in DIVERGENCES.items():
    checked += 1
    file_name, new_path = key.split("/", 1)
    new_flat = new_flat_by_file[file_name]
    kind = spec["kind"]
    old_path = spec.get("old_path")

    if old_path is not None and old_path not in old_flat and kind not in ("new_surfaced", "new_shape"):
      errors.append(f"{key}: old_path {old_path!r} not found in trading-bot.yml (documentation is stale)")
      continue

    if kind == "removed":
      if new_path in new_flat:
        errors.append(f"{key}: expected REMOVED, still present as {new_flat[new_path]!r}")
      continue

    if kind == "new_surfaced":
      if new_path not in new_flat:
        errors.append(f"{key}: expected new value {spec['expected']!r}, leaf is missing")
      elif new_flat[new_path] != spec["expected"]:
        errors.append(f"{key}: expected {spec['expected']!r}, got {new_flat[new_path]!r}")
      continue

    if kind in ("csv_to_list", "csv_to_list_int"):
      converter = csv_to_list if kind == "csv_to_list" else csv_to_list_int
      want = converter(old_flat[old_path])
      got = new_flat.get(new_path)
      if got != want:
        errors.append(f"{key}: csv->list mismatch: old CSV {old_flat[old_path]!r} -> want {want!r}, got {got!r}")
      continue

    if kind == "renested":
      want = old_flat[old_path]
      got = new_flat.get(new_path)
      if got != want:
        errors.append(f"{key}: re-nest mismatch: old[{old_path}]={want!r} != new[{new_path}]={got!r}")
      continue

    if kind == "new_shape":
      got = new_flat.get(new_path)
      if got != spec["expected"]:
        errors.append(f"{key}: expected {spec['expected']!r}, got {got!r}")
      continue

    errors.append(f"{key}: unknown divergence kind {kind!r}")


def verify_unlisted_leaves_still_match_stage_c1() -> None:
  """Every leaf NOT in DIVERGENCES must still equal Stage C1's mapping."""
  spec = importlib.util.spec_from_file_location(
    "verify_stage_c1_parity", Path(__file__).with_name("verify_stage_c1_parity.py"),
  )
  c1 = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(c1)

  divergence_old_paths = {
    d["old_path"] for d in DIVERGENCES.values() if d.get("old_path") is not None
  }
  # A newly surfaced leaf can live inside an otherwise parity-checked
  # subtree (for example analysis.techniques.*). Its literal path is also
  # the path Stage C1 would try to compare against, but there is deliberately
  # no old value to compare. Skip that path here; verify_divergences() above
  # still pins the new value exactly.
  divergence_new_surfaced_paths = {
    key.split("/", 1)[1] for key, d in DIVERGENCES.items()
    if d.get("kind") == "new_surfaced"
  }
  # compare_subtree("instrument_packs"/"instruments", ...) flattens the NEW
  # (already-renested) overrides subtree and prefixes it with the OLD
  # section name, producing a hybrid path that is neither the true old path
  # nor a new_path key — e.g. "instrument_packs.xau_fixed_4r_v1.overrides.
  # auto_algo.actionability...". The six renested/new_shape divergences
  # that also renamed their category root produce exactly that hybrid
  # string; skip those too, by their literal new_path (which IS that
  # hybrid string for this specific pair of call sites).
  divergence_new_paths_under_instruments = {
    key.split("/", 1)[1] for key in DIVERGENCES
    if key.startswith("instruments.yml/instrument")
  }

  original_compare = c1.compare

  def patched_compare(old_flat, old_path, new_value, label):
    if (
      old_path in divergence_old_paths
      or old_path in divergence_new_surfaced_paths
      or old_path in divergence_new_paths_under_instruments
    ):
      return
    original_compare(old_flat, old_path, new_value, label)

  c1.compare = patched_compare
  c1.errors = errors

  # Stage C1's verify_base_files() assumes ctrader_feed/scanner.symbols
  # still exist and iterates a fixed market_data key list; §3/§4 removed
  # two of those keys entirely. Reimplement just the parts that changed
  # shape; delegate everything else (execution.yml, telegram.yml,
  # instruments.yml, runtime.yml, transport.yml, auto-algo.yml,
  # manual-algo.yml) to c1's own function unchanged.
  old = load(CONFIG / "trading-bot.yml")
  old_flat = flatten(old)

  analysis_new = load(CONFIG / "analysis.yml")["analysis"]
  core = {
    k: v for k, v in analysis_new.items()
    if k not in (
      "indicators", "calendar", "ctrader_feed", "scanner", "sessions", "spot",
      "watcher", "measurements", "zones",
      # Analysis Engine V2 (docs/analysis/market-structure-v2.md): whole new
      # top-level sections with no old trading-bot.yml namespace at all —
      # same treatment as measurements/zones above. Each leaf's value is
      # still individually pinned and change-detected via its own
      # new_surfaced DIVERGENCES entry (verify_divergences(), above), so
      # this exclusion only means "no old path to compare against", not
      # "unchecked".
      "history", "structure", "liquidity",
    )
  }
  c1.compare_subtree(old_flat, "analysis", core, "analysis.yml/analysis")
  for new_key, old_prefix in {
    "calendar": "market_data.calendar", "scanner": "market_data.scanner",
    "sessions": "market_data.sessions", "spot": "market_data.spot",
    "watcher": "market_data.watcher",
  }.items():
    c1.compare_subtree(old_flat, old_prefix, analysis_new[new_key], f"analysis.yml/market_data.{new_key}")

  c1.compare_subtree(old_flat, "execution", load(CONFIG / "execution.yml")["execution"], "execution.yml")

  telegram_new = load(CONFIG / "telegram.yml")["telegram"]
  flattened_keys = {
    "delete_root_on_terminal", "owner_dm_daily_wipe_enabled", "public_show_pips",
    "telegram_channel_id", "signal_public_channel_id",
  }
  for k, v in telegram_new.items():
    prefix = f"delivery.telegram.{k}" if k in flattened_keys else f"delivery.{k}"
    c1.compare_subtree(old_flat, prefix, v, f"telegram.yml/{k}")

  instruments_file = load(CONFIG / "instruments.yml")
  c1.compare_subtree(old_flat, "instrument_packs", instruments_file["instrument_packs"], "instruments.yml/instrument_packs")
  c1.compare_subtree(old_flat, "instruments", instruments_file["instruments"], "instruments.yml/instruments")

  runtime_new = load(CONFIG / "runtime.yml")["runtime"]
  c1.compare_subtree(old_flat, "contract.account", runtime_new["broker"], "runtime.yml/broker")
  c1.compare(old_flat, "contract.mode", runtime_new["execution_contract"]["mode"], "runtime.yml/execution_contract.mode")

  streams = load(CONFIG / "transport.yml")["transport"]["redis_streams"]
  c1.compare(old_flat, "contract.streams.candidates", streams["candidates"], "transport.yml/redis_streams.candidates")
  c1.compare(old_flat, "contract.streams.events", streams["events"], "transport.yml/redis_streams.events")
  c1.compare(old_flat, "contract.streams.trade_plans", streams["trade_plans"], "transport.yml/redis_streams.trade_plans")
  c1.compare(old_flat, "contract.streams.candidate_maximum_length", streams["candidate_maximum_length"], "transport.yml/redis_streams.candidate_maximum_length")
  c1.compare(old_flat, "contract.versions.candidate", streams["candidate_version"], "transport.yml/redis_streams.candidate_version")

  auto_algo_new = load(CONFIG / "auto-algo.yml")["auto_algo"]
  c1.compare(old_flat, "runtime.auto_trade.enabled", auto_algo_new["enabled"], "auto-algo.yml/enabled")
  c1.compare(old_flat, "runtime.auto_trade.dry_run", auto_algo_new["dry_run"], "auto-algo.yml/dry_run")
  c1.compare(old_flat, "runtime.auto_trade.direct_publish_enabled", auto_algo_new["direct_publish_enabled"], "auto-algo.yml/direct_publish_enabled")
  c1.compare(old_flat, "runtime.auto_trade.strategy_match_enabled", auto_algo_new["strategy_match_enabled"], "auto-algo.yml/strategy_match_enabled")
  c1.compare(old_flat, "runtime.scanner.enabled", auto_algo_new["scanner"]["enabled"], "auto-algo.yml/scanner.enabled")
  for section in ("actionability", "lifecycle", "risk", "strategies"):
    c1.compare_subtree(old_flat, section, auto_algo_new[section], f"auto-algo.yml/{section}")

  c1.compare_subtree(old_flat, "manual_algo", load(CONFIG / "manual-algo.yml")["manual_algo"], "manual-algo.yml")

  global checked
  checked += c1.checked


def main() -> int:
  verify_divergences()
  verify_unlisted_leaves_still_match_stage_c1()
  print(f"checked {checked} leaves ({len(DIVERGENCES)} documented divergences)")
  if errors:
    print(f"\n{len(errors)} ERRORS:")
    for e in errors:
      print(" -", e)
    return 1
  print("ALL MATCH — every leaf is either identical to Stage C1, or an explicitly documented, verified Stage C2 divergence.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
