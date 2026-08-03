"""Canonical cross-service auto-trade configuration and compatibility health."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from typing import Any, Iterable
from urllib.parse import urlparse

from app.autotrade.range_targets import configured_range_targets
from app.autotrade.trade_plan import TRADE_PLAN_VERSION
from app.core.config import runtime_config
from app.core.environment_options import (
  canonical_option_health,
  deprecated_option_warnings,
)


CONTRACT_MODES = ("v7_only",)


CONFIG_MANIFEST_VERSION = 2
PYTHON_MANIFEST_KEY = "auto_trade:config_manifest:python"
CTRADER_MANIFEST_KEY = "auto_trade:config_manifest:ctrader"
CONFIG_HEALTH_KEY = "auto_trade:config_health"
EXECUTOR_READINESS_KEY = "auto_trade:executor_readiness"

_LEGACY_ENV_ALIASES = {
  "AUTO_TRADE_CANDIDATE_STREAM": ("AUTO_TRADE_STREAM",),
  "AUTO_TRADE_XAU_PIP_SIZE": ("AUTO_TRADE_PIP_SIZE",),
  "AUTO_TRADE_XAU_CONTRACT_SIZE": ("AUTO_TRADE_CONTRACT_SIZE",),
  "AUTO_TRADE_TARGET_PLANS_PIPS": ("AUTO_TRADE_TP_PIPS",),
  "AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS": (
    "AUTO_TRADE_CANDIDATE_MAX_AGE",
  ),
  "AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS": (
    "AUTO_TRADE_CANDIDATE_TTL",
  ),
  "AUTO_TRADE_SPOT_MAX_AGE_SECONDS": ("AUTO_TRADE_SPOT_MAX_AGE",),
  "AUTO_TRADE_MAPPED_ZONE_ENABLED": (
    "AUTO_TRADE_MARKET_MAP_STRATEGY_ENABLED",
  ),
  "AUTO_TRADE_STRATEGY_MATCH_ENABLED": (
    "AUTO_TRADE_STRATEGY_BRIDGE_ENABLED",
    "AUTO_TRADE_FORMING_GATE_ENABLED",
  ),
  "AUTO_TRADE_BE_BUFFER_TICKS": (
    "AUTO_TRADE_BE_BUFFER_PIPS",
  ),
}

_PROFILE_DEFAULT_FIELDS = {
  "AUTO_TRADE_ENABLED",
  "AUTO_TRADE_DRY_RUN",
  "AUTO_TRADE_REQUIRE_DEMO_ACCOUNT",
  "AUTO_TRADE_RANGE_FLIP_ENABLED",
  "AUTO_TRADE_RANGE_TWO_SIDED_ENABLED",
  "AUTO_TRADE_ALLOW_CONCURRENT_STRATEGIES",
  "AUTO_TRADE_ALLOW_COUNTER_BIAS",
  "AUTO_TRADE_ZONE_FILL_ENABLED",
  "AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS",
  "AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS",
  "AUTO_TRADE_NON_HEDGED_OPPOSITE_POLICY",
  "AUTO_TRADE_STRUCTURAL_GUARD_MODE",
  "AUTO_TRADE_ZONE_COOLDOWN_ENABLED",
  "AUTO_TRADE_ZONE_RECONCILE_MODE",
  "AUTO_TRADE_MAPPED_ZONE_ENABLED",
  "AUTO_TRADE_MARKET_MAP_GUARD_ENABLED",
  "AUTO_TRADE_STRATEGY_MATCH_ENABLED",
  "AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_ATR",
  "AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_PIPS",
  "AUTO_TRADE_ADD_STOP_BUFFER_ATR",
  "AUTO_TRADE_ADD_MIN_STOP_PIPS",
  "AUTO_TRADE_SL_DISTANCE",
  "AUTO_TRADE_WICK_STOP_BUFFER_ATR",
  "AUTO_TRADE_TREND_STOP_MIN_PIPS",
  "AUTO_TRADE_TREND_STOP_MAX_PIPS",
}

_CANONICAL_ENV_NAMES = {
  "AUTO_TRADE_PROFILE",
  "AUTO_TRADE_ENABLED",
  "AUTO_TRADE_DRY_RUN",
  "AUTO_TRADE_CANDIDATE_STREAM",
  "AUTO_TRADE_EVENT_STREAM",
  "AUTO_TRADE_CANDIDATE_CONTRACT_VERSION",
  "AUTO_TRADE_SYMBOLS",
  "AUTO_TRADE_CANONICAL_SYMBOL",
  "AUTO_TRADE_XAU_PIP_SIZE",
  "AUTO_TRADE_XAU_CONTRACT_SIZE",
  "AUTO_TRADE_TARGET_PLANS_PIPS",
  "AUTO_TRADE_RANGE_TARGETS_PIPS",
  "AUTO_TRADE_RANGE_TP_BUFFER_PIPS",
  "AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS",
  "AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS",
  "AUTO_TRADE_SPOT_MAX_AGE_SECONDS",
  "AUTO_TRADE_RANGE_FLIP_ENABLED",
  "AUTO_TRADE_RANGE_TWO_SIDED_ENABLED",
  "AUTO_TRADE_ALLOW_CONCURRENT_STRATEGIES",
  "AUTO_TRADE_ALLOW_COUNTER_BIAS",
  "AUTO_TRADE_ZONE_FILL_ENABLED",
  "AUTO_TRADE_MIN_CONFLUENCE",
  "AUTO_TRADE_REQUIRE_DEMO_ACCOUNT",
  "AUTO_TRADE_NON_HEDGED_OPPOSITE_POLICY",
  "AUTO_TRADE_STRUCTURAL_GUARD_MODE",
  "AUTO_TRADE_ZONE_COOLDOWN_ENABLED",
  "AUTO_TRADE_ZONE_RECONCILE_MODE",
  "AUTO_TRADE_RANGE_BOX_SCALE_OUT_ENABLED",
  "AUTO_TRADE_RANGE_BOX_SCALE_OUT_THRESHOLD_PIPS",
  "AUTO_TRADE_RANGE_BOX_SCALE_OUT_TRIGGER_PIPS",
  "AUTO_TRADE_RANGE_BOX_SCALE_OUT_FRACTION",
  "AUTO_TRADE_RANGE_BOX_MOVE_SL_TO_BE_AFTER_SCALE_OUT",
  "AUTO_TRADE_MAPPED_ZONE_ENABLED",
  "AUTO_TRADE_STRATEGY_MATCH_ENABLED",
  "AUTO_TRADE_KEY_LEVEL_REACTION_ENABLED",
  "AUTO_TRADE_DEMAND_REACTION_ENABLED",
  "AUTO_TRADE_SUPPLY_REACTION_ENABLED",
  "AUTO_TRADE_SESSION_LEVEL_REACTION_ENABLED",
  "AUTO_TRADE_TRENDLINE_REACTION_ENABLED",
  "AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_ATR",
  "AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_PIPS",
  "AUTO_TRADE_ADD_STOP_BUFFER_ATR",
  "AUTO_TRADE_ADD_MIN_STOP_PIPS",
  "AUTO_TRADE_SL_DISTANCE",
  "AUTO_TRADE_WICK_STOP_BUFFER_ATR",
  "AUTO_TRADE_TREND_STOP_MIN_PIPS",
  "AUTO_TRADE_TREND_STOP_MAX_PIPS",
}


def canonicalize_int_set(values: Iterable[Any]) -> list[int]:
  """Return a stable manifest representation independent of runtime order."""
  canonical: set[int] = set()
  for value in values:
    parsed = Decimal(str(value))
    if parsed != parsed.to_integral_value():
      raise ValueError(f"non-integer target plan: {value}")
    canonical.add(int(parsed))
  return sorted(canonical)


def canonicalize_symbols(values: Iterable[Any]) -> list[str]:
  return sorted({
    str(value).strip().upper()
    for value in values
    if str(value).strip()
  })


def _broker_identity(value: Any) -> str:
  return "".join(
    char for char in str(value or "").strip().lower()
    if char.isalnum()
  )


def canonicalize_broker(value: Any) -> str:
  raw = _broker_identity(value)
  if raw in {"fpmarkets", "fpmarketssc"}:
    return "fpmarkets"
  return raw


def canonicalize_account_mode(value: Any) -> str:
  raw = str(value or "").strip().lower().replace("_", "-")
  if raw in {"demo", "demo-only", "demo-required"}:
    return "demo"
  if raw in {"live", "live-only", "live-required"}:
    return "live"
  return raw


def deprecated_environment_variables() -> list[str]:
  deprecated = [
    warning.removeprefix("deprecated_variable:")
    for warning in deprecated_option_warnings()
  ]
  for canonical, aliases in _LEGACY_ENV_ALIASES.items():
    deprecated.extend(alias for alias in aliases if os.getenv(alias) is not None)
  return sorted(set(deprecated))


def resolved_config_sources() -> dict[str, str]:
  sources: dict[str, str] = {}
  for canonical, aliases in _LEGACY_ENV_ALIASES.items():
    if os.getenv(canonical) is not None:
      sources[canonical] = "explicit_env"
      continue
    legacy = next(
      (alias for alias in aliases if os.getenv(alias) is not None),
      None,
    )
    if legacy:
      sources[canonical] = f"deprecated_env:{legacy}"
    elif (
      runtime_config.runtime.profile == "demo_eval"
      and canonical in _PROFILE_DEFAULT_FIELDS
    ):
      sources[canonical] = "profile_demo_eval"
    else:
      sources[canonical] = "application_default"
  for canonical in _PROFILE_DEFAULT_FIELDS:
    if canonical in sources:
      continue
    sources[canonical] = (
      "explicit_env"
      if os.getenv(canonical) is not None
      else "profile_demo_eval"
      if runtime_config.runtime.profile == "demo_eval"
      else "application_default"
    )
  for canonical in _CANONICAL_ENV_NAMES:
    sources.setdefault(
      canonical,
      "explicit_env"
      if os.getenv(canonical) is not None
      else "application_default",
    )
  return dict(sorted(sources.items()))


def _redis_identity(url: str) -> tuple[str, int]:
  parsed = urlparse(url)
  database_text = parsed.path.strip("/") or "0"
  try:
    database = int(database_text)
  except ValueError:
    database = 0
  endpoint = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 6379}/{database}"
  fingerprint = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:16]
  return fingerprint, database


def _int_values(raw: str) -> list[int]:
  return canonicalize_int_set(
    item.strip() for item in raw.split(",") if item.strip()
  )


def python_manifest() -> dict[str, Any]:
  fingerprint, database = _redis_identity(runtime_config.bootstrap.redis.url)
  symbols = canonicalize_symbols(runtime_config.contract.instrument.symbols.split(","))
  now = datetime.now(timezone.utc)
  raw_broker = os.getenv("AUTO_TRADE_EXPECTED_BROKER", "")
  required_strategy_options = {
    "AUTO_TRADE_STRATEGY_MATCH_ENABLED",
    "AUTO_TRADE_KEY_LEVEL_REACTION_ENABLED",
    "AUTO_TRADE_DEMAND_REACTION_ENABLED",
    "AUTO_TRADE_SUPPLY_REACTION_ENABLED",
    "AUTO_TRADE_SESSION_LEVEL_REACTION_ENABLED",
    "AUTO_TRADE_TRENDLINE_REACTION_ENABLED",
    "AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_ATR",
    "AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_PIPS",
  }
  sources = resolved_config_sources()
  return {
    "config_manifest_version": CONFIG_MANIFEST_VERSION,
    "service": "algo-bot",
    "service_version": os.getenv("SERVICE_VERSION", "dev"),
    "git_sha": os.getenv("GIT_SHA", "unknown"),
    "profile": runtime_config.runtime.profile,
    "auto_trade_enabled": runtime_config.runtime.auto_trade.enabled,
    "dry_run": runtime_config.runtime.auto_trade.dry_run,
    "manual_algo_enabled": runtime_config.manual_algo.runtime.enabled,
    "manual_algo_dry_run": runtime_config.manual_algo.runtime.dry_run,
    "redis_fingerprint": fingerprint,
    "redis_database": database,
    "candidate_stream": runtime_config.contract.streams.candidates,
    "event_stream": runtime_config.contract.streams.events,
    "symbols": symbols,
    "canonical_symbol": runtime_config.contract.instrument.canonical_symbol.upper(),
    "pip_size": runtime_config.contract.instrument.pip_size,
    "price_digits": runtime_config.contract.instrument.price_digits,
    "max_entry_distance_pips": runtime_config.execution.entry.maximum_chase_distance_pips,
    "entry_contract_tolerance_pips": (
      runtime_config.execution.entry.contract_tolerance_pips
    ),
    "break_even_buffer_ticks": runtime_config.execution.stops.be_buffer_ticks,
    "symbol_tick_size": float(
      Decimal("1") / (Decimal("10") ** int(runtime_config.contract.instrument.price_digits))
    ),
    "post_fill_target_fallback": runtime_config.execution.targeting.post_fill_target_fallback,
    "entry_plan_version": 1,
    "stop_plan_version": 3,
    "contract_size": runtime_config.contract.instrument.contract_units_per_lot,
    "structure_stop_buffer_atr": runtime_config.execution.scaling.add.stop_buffer_atr,
    "ordinary_stop_min_pips": runtime_config.execution.scaling.add.min_stop_pips,
    "ordinary_stop_max_distance": runtime_config.execution.stops.sl_distance,
    "wick_stop_buffer_atr": runtime_config.execution.stops.wick_stop_buffer_atr,
    "trend_stop_min_pips": runtime_config.execution.stops.trend.minimum_pips,
    "trend_stop_max_pips": runtime_config.execution.trend.stop_max_pips,
    "target_plans": _int_values(runtime_config.execution.targeting.default_ladder_pips),
    "range_target_plans": canonicalize_int_set(
      configured_range_targets()
    ),
    "range_tp_buffer": runtime_config.execution.range.tp_buffer_pips,
    "candidate_storage_ttl_seconds": (
      runtime_config.lifecycle.candidate.storage_ttl_seconds
    ),
    "candidate_execution_max_age_seconds": (
      runtime_config.lifecycle.candidate.execution_maximum_age_seconds
    ),
    "spot_max_age_seconds": runtime_config.market_data.spot.maximum_age_seconds,
    "range_flip": runtime_config.strategies.range_reversion.flip_enabled,
    "two_sided_range": (
      runtime_config.strategies.range_reversion.two_sided_enabled
    ),
    "concurrent_strategies": runtime_config.risk.exposure.allow_concurrent_strategies,
    "hedging_policy": runtime_config.risk.exposure.allow_hedged_xau,
    "broker_hedging_capability": None,
    "zone_fill": runtime_config.execution.zone_scaling.fill_enabled,
    "trend_enabled": runtime_config.strategies.trend.enabled,
    "range_enabled": runtime_config.strategies.range_reversion.enabled,
    "mapped_zone_enabled": runtime_config.strategies.mapped_zone.enabled,
    "market_map_guard_enabled": (
      runtime_config.actionability.gates.market_map_guard_enabled
    ),
    "map_thesis_lock_enabled": runtime_config.execution.mapped_zone.thesis_lock_enabled,
    "strategy_match_enabled": runtime_config.runtime.auto_trade.strategy_match_enabled,
    "execution_zone_max_width_atr": (
      runtime_config.execution.policy.execution_zone_max_width_atr
    ),
    "execution_zone_max_width_pips": (
      runtime_config.execution.policy.execution_zone_max_width_pips
    ),
    "breakout_enabled": runtime_config.strategies.breakout.breakout_enabled,
    "retest_enabled": runtime_config.strategies.selection.retest_enabled,
    "reaction_enabled": runtime_config.strategies.reaction.enabled,
    "liquidity_reversal_enabled": (
      runtime_config.strategies.reaction.liquidity_reversal.enabled
    ),
    "allow_counter_bias": runtime_config.actionability.counter_bias.allowed,
    "min_confluence": runtime_config.actionability.gates.min_confluence,
    "account_mode": "demo"
    if runtime_config.contract.account.require_demo else "live",
    "require_demo_account": runtime_config.contract.account.require_demo,
    "broker": canonicalize_broker(raw_broker),
    "broker_configured": raw_broker,
    "non_hedged_opposite_policy": (
      runtime_config.risk.exposure.non_hedged_opposite_policy
    ),
    "structural_guard_mode": (
      runtime_config.actionability.structural_guard.guard_mode
    ),
    "zone_cooldown_enabled": runtime_config.lifecycle.zone.cooldown_enabled,
    "zone_reconcile_mode": runtime_config.actionability.zone_reconciliation.mode,
    "range_box_scale_out_enabled": (
      runtime_config.strategies.range_reversion.box_scale_out_enabled
    ),
    "range_box_scale_out_threshold_pips": (
      runtime_config.execution.range.box_scale_out_threshold_pips
    ),
    "range_box_scale_out_trigger_pips": (
      runtime_config.execution.range.box_scale_out_trigger_pips
    ),
    "range_box_scale_out_fraction": (
      runtime_config.execution.range.box_scale_out_fraction
    ),
    "range_box_move_sl_to_be_after_scale_out": (
      runtime_config.execution.range.box_move_sl_to_be_after_scale_out
    ),
    "candidate_contract_version": (
      runtime_config.contract.versions.candidate
    ),
    "contract_mode": runtime_config.contract.mode,
    "trade_plan_version": TRADE_PLAN_VERSION,
    "trade_plan_stream": runtime_config.contract.streams.trade_plans,
    "sizing_mode": runtime_config.risk.sizing.mode,
    "equity_table_version": runtime_config.risk.sizing.equity_table_version,
    "zone_scale_undersized_policy": (
      runtime_config.execution.zone_scaling.scale_undersized_policy
    ),
    "group_close_allocation": runtime_config.execution.policy.group_close_allocation,
    "unfilled_leg_after_tp_policy": (
      runtime_config.execution.targeting.unfilled_leg_after_tp_policy
    ),
    "entry_leg_ratios": "0.70,0.30",
    "deprecated_variables": deprecated_environment_variables(),
    "canonical_options": canonical_option_health(),
    "config_sources": sources,
    "required_options_missing": sorted(
      name
      for name in required_strategy_options
      if sources.get(name) == "application_default"
    ),
    "generated_at": int(now.timestamp()),
    "generated_at_iso": now.isoformat(),
  }


def _numeric_equal(left: Any, right: Any) -> bool:
  try:
    return Decimal(str(left)) == Decimal(str(right))
  except (InvalidOperation, TypeError, ValueError):
    return False


def _canonical_field(field: str, value: Any) -> Any:
  if field in {"target_plans", "range_target_plans"}:
    try:
      return canonicalize_int_set(value or [])
    except (InvalidOperation, TypeError, ValueError):
      return None
  if field == "symbols":
    return canonicalize_symbols(value or [])
  if field == "broker":
    return canonicalize_broker(value)
  if field == "account_mode":
    return canonicalize_account_mode(value)
  if field == "canonical_symbol":
    return str(value or "").strip().upper()
  return value


def _different(field: str, left: Any, right: Any) -> bool:
  if left is None and right is None:
    return False
  left = _canonical_field(field, left)
  right = _canonical_field(field, right)
  if field in {
    "pip_size",
    "price_digits",
    "max_entry_distance_pips",
    "entry_contract_tolerance_pips",
    "break_even_buffer_ticks",
    "symbol_tick_size",
    "entry_plan_version",
    "stop_plan_version",
    "contract_size",
    "range_tp_buffer",
    "candidate_execution_max_age_seconds",
    "candidate_storage_ttl_seconds",
    "spot_max_age_seconds",
    "min_confluence",
    "candidate_contract_version",
    "trade_plan_version",
    "config_manifest_version",
    "range_box_scale_out_threshold_pips",
    "range_box_scale_out_trigger_pips",
    "range_box_scale_out_fraction",
    "execution_zone_max_width_atr",
    "execution_zone_max_width_pips",
    "structure_stop_buffer_atr",
    "ordinary_stop_min_pips",
    "ordinary_stop_max_distance",
    "wick_stop_buffer_atr",
    "trend_stop_min_pips",
    "trend_stop_max_pips",
  }:
    return not _numeric_equal(left, right)
  return left != right


def compare_manifests(
  python: dict[str, Any],
  ctrader: dict[str, Any] | None,
) -> dict[str, Any]:
  if ctrader is None:
    return {
      "state": "warning",
      "fatal": [],
      "warnings": ["ctrader_manifest_missing"],
    }
  fatal_fields = (
    "config_manifest_version",
    "auto_trade_enabled",
    "dry_run",
    "candidate_stream",
    "event_stream",
    "redis_database",
    "redis_fingerprint",
    "symbols",
    "canonical_symbol",
    "pip_size",
    "price_digits",
    "max_entry_distance_pips",
    "entry_contract_tolerance_pips",
    "break_even_buffer_ticks",
    "symbol_tick_size",
    "entry_plan_version",
    "stop_plan_version",
    "contract_size",
    "candidate_contract_version",
    "contract_mode",
    "trade_plan_version",
    "trade_plan_stream",
    "sizing_mode",
    "equity_table_version",
    "zone_scale_undersized_policy",
    "group_close_allocation",
    "unfilled_leg_after_tp_policy",
    "entry_leg_ratios",
    "target_plans",
    "range_target_plans",
    "range_tp_buffer",
    "candidate_execution_max_age_seconds",
    "spot_max_age_seconds",
    "require_demo_account",
    "execution_zone_max_width_atr",
    "execution_zone_max_width_pips",
    "structure_stop_buffer_atr",
    "ordinary_stop_min_pips",
    "ordinary_stop_max_distance",
    "wick_stop_buffer_atr",
    "trend_stop_min_pips",
    "trend_stop_max_pips",
  )
  fatal = [
    field for field in fatal_fields
    if _different(field, python.get(field), ctrader.get(field))
  ]
  fatal.extend(
    f"required_strategy_key_missing:{name}"
    for name in python.get("required_options_missing") or []
  )
  if (
    python.get("profile") == "demo_eval"
    and canonicalize_account_mode(ctrader.get("account_mode")) == "live"
  ):
    fatal.append("demo_eval_live_account")
  warning_fields = (
    "candidate_storage_ttl_seconds",
    "manual_algo_enabled",
    "manual_algo_dry_run",
    "range_flip",
    "two_sided_range",
    "concurrent_strategies",
    "hedging_policy",
    "zone_fill",
    "trend_enabled",
    "range_enabled",
    "mapped_zone_enabled",
    "map_thesis_lock_enabled",
    "strategy_match_enabled",
    "breakout_enabled",
    "retest_enabled",
    "reaction_enabled",
    "liquidity_reversal_enabled",
    "allow_counter_bias",
    "min_confluence",
    "profile",
    "non_hedged_opposite_policy",
    "structural_guard_mode",
    "zone_cooldown_enabled",
    "zone_reconcile_mode",
    "range_box_scale_out_enabled",
    "range_box_scale_out_threshold_pips",
    "range_box_scale_out_trigger_pips",
    "range_box_scale_out_fraction",
    "range_box_move_sl_to_be_after_scale_out",
  )
  warnings = [
    field
    for field in warning_fields
    if _different(field, python.get(field), ctrader.get(field))
  ]
  if not bool(ctrader.get("broker_hedging_capability", True)):
    warnings.append("broker_non_hedged")
  if (
    canonicalize_broker(python.get("broker"))
    != canonicalize_broker(ctrader.get("broker"))
  ):
    warnings.append("broker")
  for manifest in (python, ctrader):
    reported = (
      manifest.get("broker_configured")
      or manifest.get("broker_reported")
    )
    if (
      reported
      and _broker_identity(reported) != canonicalize_broker(reported)
    ):
      warnings.append("broker_alias_normalized")
  for manifest in (python, ctrader):
    for variable in manifest.get("deprecated_variables") or []:
      warnings.append(f"deprecated_variable:{variable}")
  if python.get("git_sha") in {None, "", "unknown"}:
    warnings.append("python_git_sha_unknown")
  if ctrader.get("git_sha") in {None, "", "unknown"}:
    warnings.append("ctrader_git_sha_unknown")
  if not bool(python.get("map_thesis_lock_enabled", True)):
    warnings.append("map_thesis_lock_disabled")
  if not bool(ctrader.get("map_thesis_lock_enabled", True)):
    warnings.append("map_thesis_lock_disabled")
  return {
    "state": "fatal" if fatal else "healthy",
    "fatal": sorted(set(fatal)),
    "warnings": sorted(set(warnings)),
  }


async def publish_python_manifest(client: Any) -> dict[str, Any]:
  manifest = python_manifest()
  await client.set(
    PYTHON_MANIFEST_KEY,
    json.dumps(manifest, separators=(",", ":"), sort_keys=True),
  )
  raw = await client.get(CTRADER_MANIFEST_KEY)
  ctrader = None
  if raw:
    try:
      ctrader = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
      ctrader = None
  health = compare_manifests(manifest, ctrader)
  payload = {
    **health,
    "profile": runtime_config.runtime.profile,
    "checked_at": datetime.now(timezone.utc).isoformat(),
  }
  encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
  await client.set(CONFIG_HEALTH_KEY, encoded)
  await client.xadd(
    runtime_config.contract.streams.events,
    {"payload": json.dumps({
      "type": "config_health",
      "timestamp": int(datetime.now(timezone.utc).timestamp()),
      "message": f"configuration health: {health['state']}",
      "profile": runtime_config.runtime.profile,
      "health": health,
    }, separators=(",", ":"))},
    maxlen=max(100, runtime_config.contract.streams.candidate_maximum_length),
    approximate=True,
  )
  return payload
