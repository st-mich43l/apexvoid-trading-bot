"""Canonical configuration fixtures for tests.

Helpers build or override typed ``PythonRuntimeConfig`` trees. There is no flat
Settings adapter. Short override names are resolved through the frozen Catalog
V1 identity map for test convenience only; production code uses dotted paths.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import Any, Iterable, Mapping

from app.configuration.catalog import iter_catalog_entries
from app.configuration.models.python_runtime import PythonRuntimeConfig
from app.core import config as config_module


@lru_cache(maxsize=1)
def _short_name_to_path() -> Mapping[str, tuple[str, ...]]:
  """Map unique leaf names to canonical paths for test convenience."""
  mapping: dict[str, tuple[str, ...]] = {}
  ambiguous: set[str] = set()
  for entry in iter_catalog_entries():
    leaf = entry.path.split(".")[-1]
    path = tuple(entry.path.split("."))
    if leaf in mapping and mapping[leaf] != path:
      ambiguous.add(leaf)
    elif leaf not in mapping:
      mapping[leaf] = path
    # Keep test fixtures convenient for callers that still use the
    # environment-shaped name. This is test-only resolution; production
    # configuration accepts canonical dotted paths only.
    env_name = getattr(entry, "canonical_env", None)
    if env_name:
      env_key = env_name.lower()
      if env_key in mapping and mapping[env_key] != path:
        ambiguous.add(env_key)
      elif env_key not in mapping:
        mapping[env_key] = path
  for name in ambiguous:
    mapping.pop(name, None)
  return mapping


def _get_path(root: Any, path: tuple[str, ...]) -> Any:
  node = root
  for part in path:
    node = getattr(node, part)
  return node


def apply_path_overrides(
  config: PythonRuntimeConfig,
  overrides: Mapping[str, Any],
) -> PythonRuntimeConfig:
  """Return a new config with dotted canonical-path overrides applied."""
  current: Any = config
  nested: dict[str, Any] = {}
  for dotted, value in overrides.items():
    parts = tuple(dotted.split(".")) if isinstance(dotted, str) else tuple(dotted)
    if not parts:
      raise ValueError("empty override path")
    cursor = nested
    for part in parts[:-1]:
      cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value

  def _merge(model: Any, updates: Mapping[str, Any]) -> Any:
    payload: dict[str, Any] = {}
    for key, value in updates.items():
      child = getattr(model, key)
      if isinstance(value, Mapping) and hasattr(child, "model_copy"):
        payload[key] = _merge(child, value)
      else:
        payload[key] = value
    return model.model_copy(update=payload)

  return _merge(current, nested)


def apply_named_overrides(
  config: PythonRuntimeConfig,
  overrides: Mapping[str, Any],
) -> PythonRuntimeConfig:
  """Translate unique short names / historical flat names into path overrides."""
  mapping = _short_name_to_path()
  path_values: dict[str, Any] = {}
  for name, value in overrides.items():
    if "." in name:
      path_values[name] = value
      continue
    path = mapping.get(name)
    if path is None:
      raise KeyError(f"unknown catalog override name: {name}")
    path_values[".".join(path)] = value
  return apply_path_overrides(config, path_values)


def leaf(config: Any, name: str) -> Any:
  """Read one catalog leaf by dotted path or unique short name."""
  if "." in name:
    return _get_path(config, tuple(name.split(".")))
  path = _short_name_to_path().get(name)
  if path is None:
    raise KeyError(f"unknown catalog leaf name: {name}")
  return _get_path(config, path)


def install_runtime_overrides(
  monkeypatch: Any,
  overrides: Mapping[str, Any] | None = None,
  *,
  legacy_overrides: Mapping[str, Any] | None = None,
  base: PythonRuntimeConfig | None = None,
) -> PythonRuntimeConfig:
  """Install a model_copy of runtime_config across imported module bindings.

  ``overrides`` keys are dotted canonical paths.
  ``legacy_overrides`` accepts unique short / historical flat names for tests.
  Does not mutate the original frozen config object.
  """
  current = base if base is not None else config_module.runtime_config
  if not isinstance(current, PythonRuntimeConfig):
    current = PythonRuntimeConfig.model_validate(current.model_dump())
  updated = current
  if overrides:
    updated = apply_path_overrides(updated, overrides)
  if legacy_overrides:
    updated = apply_named_overrides(updated, legacy_overrides)
  old = config_module.runtime_config
  monkeypatch.setattr(config_module, "runtime_config", updated)
  for module in list(sys.modules.values()):
    if module is None:
      continue
    try:
      bound = getattr(module, "runtime_config", None)
    except Exception:
      continue
    if bound is old:
      monkeypatch.setattr(module, "runtime_config", updated, raising=False)
  return updated


def runtime_cfg(**legacy_overrides: Any) -> PythonRuntimeConfig:
  """Build an overridden PythonRuntimeConfig from the process runtime root."""
  return apply_named_overrides(
    config_module.runtime_config,
    legacy_overrides,
  )


def execution_cfg(**overrides: Any) -> PythonRuntimeConfig:
  cleaned = dict(overrides)
  if "auto_trade_opposing_zone_push_enabled" in cleaned:
    cleaned["auto_trade_stop_push_beyond_zone"] = cleaned.pop(
      "auto_trade_opposing_zone_push_enabled",
    )
  cleaned.pop("auto_trade_opposing_zone_buffer_atr", None)
  return runtime_cfg(**cleaned)


def map_strategy_cfg(**overrides: Any) -> PythonRuntimeConfig:
  cleaned = dict(overrides)
  if "auto_trade_targets_pips" in cleaned and "auto_trade_tp_pips" not in cleaned:
    cleaned["auto_trade_tp_pips"] = cleaned.pop("auto_trade_targets_pips")
  else:
    cleaned.pop("auto_trade_targets_pips", None)
  cleaned.pop("pip_size", None)
  defaults = {
    "auto_trade_mapped_zone_enabled": True,
    "auto_trade_max_entry_distance_pips": 10,
    "auto_trade_strategy_match_max_age_seconds": 420,
    "auto_trade_tp_pips": "30,60,90,120,200",
    "auto_trade_map_zone_min_width_atr": 0.15,
    "auto_trade_map_zone_min_width_abs": 1.0,
    "auto_trade_map_counter_bias_enabled": True,
    "auto_trade_map_counter_bias_min_score": 6.0,
    "auto_trade_map_counter_bias_min_confluence": 2,
    "auto_trade_map_track_distance_atr": 8.0,
    "auto_trade_map_execute_distance_atr": 1.5,
    "auto_trade_map_execute_tolerance_pips": 0.0,
    "auto_trade_map_execute_tolerance_atr": 0.0,
    "auto_trade_map_reaction_lookback_bars": 5,
    "auto_trade_map_thesis_lock_enabled": True,
    "auto_trade_map_reaction_rearm_bars": 3,
    "auto_trade_map_reaction_rearm_atr": 0.50,
    "auto_trade_map_max_entry_drift_atr": 0.40,
    "auto_trade_allow_counter_bias": False,
    "atr_length": 14,
    "proximal_band_atr": 0.5,
  }
  defaults.update(cleaned)
  return runtime_cfg(**defaults)


def scale_context_cfg(**overrides: Any) -> PythonRuntimeConfig:
  defaults = {
    "atr_length": 14,
    "swing_fractal_n": 2,
    "zigzag_pct": 0.0,
    "zigzag_atr_mult": 1.0,
    "displacement_atr_mult": 1.5,
    "momentum_body_frac": 0.6,
  }
  defaults.update(overrides)
  return runtime_cfg(**defaults)


def trend_cfg(overrides: Mapping[str, Any] | None = None) -> PythonRuntimeConfig:
  defaults = {
    "atr_length": 14,
    "swing_fractal_n": 2,
    "zigzag_pct": 0.0,
    "zigzag_atr_mult": 1.0,
    "displacement_atr_mult": 1.5,
    "momentum_body_frac": 0.6,
    "trend_min_bos": 2,
    "trend_min_height_atr": 2.0,
    "auto_trade_allow_counter_bias": False,
    "trend_atr_expansion": 1.2,
    "trend_regime_direction_enabled": True,
    "trend_regime_direction_lookback": 60,
    "trend_regime_min_directional_swings": 2,
    "trend_regime_min_displacement_atr": 1.0,
    "auto_trade_measurements_tp_min_spacing_atr": 0.75,
    "trend_breakout_max_age_bars": 6,
    "trend_breakout_accept_bars": 2,
    "trend_breakout_min_room_pips": 20,
    "reactions_max_atr": 3.0,
    "trend_allow_chase": True,
    "trend_level_buffer_atr": 0.2,
    "trend_atr_baseline_bars": 100,
  }
  if overrides:
    defaults.update(dict(overrides))
  return runtime_cfg(**defaults)


def market_map_cfg(**overrides: Any) -> PythonRuntimeConfig:
  defaults = {
    "map_max_per_side": 4,
    "map_major_score": 12.0,
    "map_max_touches": 4,
    "map_min_zone_score": 6.0,
    "map_min_level_touches": 4,
    "map_max_distance_atr": 15.0,
    "map_band_max_atr": 2.0,
    "map_min_per_side": 2,
    "map_fallback_radius": 30.0,
    "map_scalp_radius": 15.0,
    "map_change_min": 1.0,
    "round_step": 5.0,
    "range_scalp_min_touches": 3,
    "range_scalp_min_width_atr": 1.2,
    "range_scalp_max_width_atr": 6.0,
    "range_scalp_min_room_atr": 1.0,
    "range_scalp_break_closes": 2,
    "scanner_exec_tf": "M5",
    "proximal_band_atr": 0.5,
    "session_asia_start": 22,
    "session_london_start": 7,
    "session_ny_start": 13,
  }
  defaults.update(overrides)
  return runtime_cfg(**defaults)


def scalp_ranges_cfg(**overrides: Any) -> PythonRuntimeConfig:
  defaults = {
    "range_scalp_lookback": 36,
    "range_scalp_cluster_atr": 0.20,
    "range_scalp_cluster_min_abs": 0.0,
    "range_scalp_min_touches": 3,
    "range_scalp_min_wick_frac": 0.35,
    "range_scalp_entry_tol_atr": 0.15,
    "range_scalp_max_edge_width_atr": 0.75,
    "range_scalp_min_width_atr": 1.2,
    "range_scalp_max_width_atr": 6.0,
    "range_scalp_min_room_atr": 1.0,
    "range_scalp_min_inside_closes": 3,
    "range_scalp_break_closes": 2,
    "scalp_barrier_fallback_enabled": True,
    "scalp_barrier_fallback_min_confirmations": 1,
    "scalp_range_provisional_enabled": True,
    "scalp_post_impulse_range_enabled": True,
    "round_step": 5.0,
  }
  defaults.update(overrides)
  return runtime_cfg(**defaults)


def actionability_cfg(**overrides: Any) -> PythonRuntimeConfig:
  defaults = {
    "contested_corridor_gap_atr": 0.5,
    "auto_trade_allow_counter_bias": True,
    "auto_trade_structural_guard_mode": "balanced",
    "auto_trade_opposing_barrier_atr": 0.5,
    "scanner_actionability_gate_enabled": True,
    "key_level_role_ambiguity_gate_enabled": True,
    "auto_trade_execution_cost_pips": 1.0,
    "auto_trade_min_capped_target_pips": 15.0,
    "breakout_accept_bars": 2,
  }
  defaults.update(overrides)
  return runtime_cfg(**defaults)


def modules_with_runtime_config() -> Iterable[Any]:
  for module in list(sys.modules.values()):
    if module is None:
      continue
    if getattr(module, "runtime_config", None) is not None:
      yield module
