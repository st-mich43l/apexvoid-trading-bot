"""Native YAML test overrides.

These helpers operate on the same tree that the service loads.  The short
names remain only as test-call-site conveniences while production accepts
native dotted paths exclusively.
"""

from __future__ import annotations

import sys
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from app.core import config as config_module
from app.core.config_schema import ConfigNode, native_config


def _set_path(root: dict[str, Any], dotted: str, value: Any) -> None:
  parts = dotted.split(".")
  current = root
  for part in parts[:-1]:
    current = current.setdefault(part, {})
  current[parts[-1]] = deepcopy(value)


def apply_path_overrides(config: ConfigNode, overrides: Mapping[str, Any]) -> ConfigNode:
  values = config.to_dict()
  for dotted, value in overrides.items():
    path = str(dotted)
    legacy_prefixes = (
      ("runtime.auto_trade", "auto_algo"),
      ("actionability", "auto_algo.actionability"),
      ("lifecycle", "auto_algo.lifecycle"),
      ("risk", "auto_algo.risk"),
      ("strategies", "auto_algo.strategies"),
    )
    for prefix, native_prefix in legacy_prefixes:
      if path == prefix:
        path = native_prefix
        break
      if path.startswith(f"{prefix}."):
        path = f"{native_prefix}{path[len(prefix):]}"
        break
    _set_path(values, path, value)
  return native_config(values)


_LEGACY_PATHS = {
  "delivery_thread_lifecycle": "telegram.lifecycle.thread_lifecycle",
  "telegram_owner_id": "telegram.telegram_owner_id",
  "telegram_bot_token": "telegram.bot_token",
  "scanner_telegram_bot_token": "telegram.scanner_telegram_bot_token",
  "signal_public_channel_id": "telegram.signal_public_channel_id",
  "public_show_pips": "telegram.public_show_pips",
  "auto_trade_enabled": "auto_algo.enabled",
  "auto_trade_dry_run": "auto_algo.dry_run",
  "auto_trade_symbols": "analysis.scanner.symbols",
  "auto_trade_stream_maxlen": "runtime.redis_streams.stream_maximum_length",
  "auto_trade_event_stream": "runtime.redis_streams.events",
  "contract.streams.trade_plans": "runtime.redis_streams.trade_plans",
  "auto_trade_strategy_match_enabled": "auto_algo.strategy_match_enabled",
  "auto_trade_strategy_match_max_age_seconds": "auto_algo.lifecycle.candidate.execution_maximum_age_seconds",
  "auto_trade_news_guard_minutes": "auto_algo.actionability.gates.news_guard_minutes",
  "auto_trade_mapped_zone_enabled": "auto_algo.strategies.mapped_zone.enabled",
  "auto_trade_map_reaction_rearm_atr": "auto_algo.lifecycle.mapped_zone.reaction_rearm_atr",
  "auto_trade_map_reaction_rearm_bars": "auto_algo.lifecycle.mapped_zone.reaction_rearm_bars",
  "auto_trade_map_thesis_lock_enabled": "execution.mapped_zone.thesis_lock_enabled",
  "auto_trade_max_entry_distance_pips": "execution.entry.maximum_chase_distance_pips",
  "auto_trade_tp_pips": "execution.targeting.default_ladder_pips",
  "auto_trade_zone_fill_enabled": "execution.zone_scaling.fill_enabled",
  "auto_trade_zone_fill_fallback_enabled": "execution.zone_scaling.fill_fallback_enabled",
  "auto_trade_zone_fill_min_atr": "execution.zone_scaling.fill_min_atr",
  "auto_trade_zone_scale_first_leg_fraction": "execution.zone_scaling.first_leg_fraction",
  "auto_trade_zone_scale_step_atr": "execution.zone_scaling.scale_step_atr",
  "auto_trade_reaction_scale_enabled": "auto_algo.strategies.reaction.scale_enabled",
  "auto_trade_reaction_market_fraction": "execution.reaction.market_fraction",
  "auto_trade_reaction_scale_fraction": "execution.reaction.scale_fraction",
  "auto_trade_reaction_scale_step_atr": "execution.reaction.scale_step_atr",
  "auto_trade_reaction_scale_invalid_policy": "execution.reaction.scale_invalid_policy",
  "auto_trade_reaction_stop_min_pips": "execution.reaction.stop_min_pips",
  "auto_trade_reaction_stop_max_pips": "execution.reaction.stop_max_pips",
  "auto_trade_reaction_room_stop_min_rr": "execution.reaction.room_stop_min_rr",
  "auto_trade_reaction_room_stop_floor_pips": "execution.stops.reaction.room_floor_pips",
  "auto_trade_trend_stop_min_pips": "execution.stops.trend.minimum_pips",
  "auto_trade_trend_stop_max_pips": "execution.trend.stop_max_pips",
  "auto_trade_range_min_rr": "execution.range.min_rr",
  "auto_trade_range_room_stop_floor_pips": "execution.range.room_stop_floor_pips",
  "auto_trade_range_max_risk_multiplier": "auto_algo.risk.sizing.range_max_risk_multiplier",
  "auto_trade_add_min_stop_pips": "execution.scaling.add.min_stop_pips",
  "auto_trade_add_stop_buffer_atr": "execution.scaling.add.stop_buffer_atr",
  "auto_trade_sl_distance": "execution.stops.sl_distance",
  "auto_trade_wick_stop_buffer_atr": "execution.stops.wick_stop_buffer_atr",
  "auto_trade_opposing_zone_push_enabled": "execution.stops.stop_push_beyond_zone",
  "auto_trade_opposing_zone_buffer_atr": "execution.scaling.add.stop_buffer_atr",
  "auto_trade_inside_zone_market_entry_enabled": "execution.entry.inside_zone_market_entry_enabled",
  "auto_trade_xau_price_digits": "instruments.XAU.contract.price_digits",
  "auto_trade_telegram_delete_root_on_terminal": "telegram.delete_root_on_terminal",
  "auto_trade_telegram_single_root_card": "telegram.lifecycle.thread_lifecycle",
  "manual_algo_enabled": "manual_algo.runtime.enabled",
  "manual_algo_dry_run": "manual_algo.runtime.dry_run",
  "manual_algo_owner_execution_dm_enabled": "manual_algo.runtime.owner_execution_dm_enabled",
  "manual_trade_intent_stream": "manual_algo.streams.intents",
  "manual_trade_intent_stream_maxlen": "manual_algo.streams.manual_trade_intent_stream_maxlen",
  "manual_trade_command_stream": "manual_algo.streams.manual_trade_command_stream",
  "manual_trade_command_stream_maxlen": "manual_algo.streams.manual_trade_command_stream_maxlen",
  "auto_book_bare_pips": "telegram.presentation.auto_book_bare_pips",
  "calendar_currencies": "analysis.calendar.currencies",
  "oil_keywords": "analysis.calendar.oil_keywords",
  "news_guard_block": "analysis.calendar.news_guard_block",
  "tiingo_api_key": "analysis.tiingo.api_key",
  "weekly_report_dow": "telegram.reports.weekly.day_of_week",
  "weekly_report_hour": "telegram.reports.weekly.utc_hour",
  "weekly_report_skip_empty": "telegram.reports.weekly.skip_empty",
  "delivery_lag_seconds": "analysis.technical_authority.max_delivery_lag_seconds",
  "watcher_ctrader_stale_seconds": "analysis.watcher.ctrader_stale_seconds",
}


def apply_named_overrides(config: ConfigNode, overrides: Mapping[str, Any]) -> ConfigNode:
  translated: dict[str, Any] = {}
  for name, value in overrides.items():
    if "." in name:
      translated[name] = value
    elif name in _LEGACY_PATHS:
      translated[_LEGACY_PATHS[name]] = value
    elif name == "auto_trade_profile":
      translated["runtime.environment"] = value
    else:
      raise KeyError(f"unknown test override: {name}")
  return apply_path_overrides(config, translated)


def _get_path(root: Any, dotted: str) -> Any:
  current = root
  for part in dotted.split("."):
    current = getattr(current, part)
  return current


def leaf(config: Any, name: str) -> Any:
  path = _LEGACY_PATHS.get(name, name)
  return _get_path(config, path)


def install_runtime_overrides(
  monkeypatch: Any,
  overrides: Mapping[str, Any] | None = None,
  *,
  legacy_overrides: Mapping[str, Any] | None = None,
  base: ConfigNode | None = None,
) -> ConfigNode:
  current = base if base is not None else config_module.runtime_config
  updated = current
  if overrides:
    updated = apply_path_overrides(updated, overrides)
  if legacy_overrides:
    updated = apply_named_overrides(updated, legacy_overrides)
  old = config_module.runtime_config
  monkeypatch.setattr(config_module, "runtime_config", updated)
  for module in list(sys.modules.values()):
    if module is not None and getattr(module, "runtime_config", None) is old:
      monkeypatch.setattr(module, "runtime_config", updated, raising=False)
  return updated


def runtime_cfg(**overrides: Any) -> ConfigNode:
  return apply_named_overrides(config_module.runtime_config, overrides)


def execution_cfg(**overrides: Any) -> ConfigNode:
  return runtime_cfg(**overrides)


def map_strategy_cfg(**overrides: Any) -> ConfigNode:
  return runtime_cfg(**overrides)


def scale_context_cfg(**overrides: Any) -> ConfigNode:
  return runtime_cfg(**overrides)


def trend_cfg(overrides: Mapping[str, Any] | None = None) -> ConfigNode:
  return runtime_cfg(**(dict(overrides or {})))


def market_map_cfg(**overrides: Any) -> ConfigNode:
  return runtime_cfg(**overrides)


def scalp_ranges_cfg(**overrides: Any) -> ConfigNode:
  return runtime_cfg(**overrides)


def actionability_cfg(**overrides: Any) -> ConfigNode:
  return runtime_cfg(**overrides)


def modules_with_runtime_config() -> Iterable[Any]:
  return tuple(
    module for module in sys.modules.values()
    if module is not None and getattr(module, "runtime_config", None) is not None
  )


def _load_production_example() -> SimpleNamespace:
  return SimpleNamespace(config=config_module.runtime_config)
