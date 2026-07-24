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
from app.core.config import settings
from app.core.environment_options import (
  canonical_option_health,
  deprecated_option_warnings,
)


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
  "AUTO_TRADE_STRATEGY_MATCH_ENABLED",
  "AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_ATR",
  "AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_PIPS",
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
      settings.auto_trade_profile == "demo_eval"
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
      if settings.auto_trade_profile == "demo_eval"
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
  fingerprint, database = _redis_identity(settings.redis_url)
  symbols = canonicalize_symbols(settings.auto_trade_symbols.split(","))
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
    "service": "telegram-bot",
    "service_version": os.getenv("SERVICE_VERSION", "dev"),
    "git_sha": os.getenv("GIT_SHA", "unknown"),
    "profile": settings.auto_trade_profile,
    "auto_trade_enabled": settings.auto_trade_enabled,
    "dry_run": settings.auto_trade_dry_run,
    "manual_algo_enabled": settings.manual_algo_enabled,
    "manual_algo_dry_run": settings.manual_algo_dry_run,
    "redis_fingerprint": fingerprint,
    "redis_database": database,
    "candidate_stream": settings.auto_trade_stream,
    "event_stream": settings.auto_trade_event_stream,
    "symbols": symbols,
    "canonical_symbol": settings.auto_trade_canonical_symbol.upper(),
    "pip_size": settings.auto_trade_xau_pip_size,
    "contract_size": settings.auto_trade_contract_size,
    "target_plans": _int_values(settings.auto_trade_tp_pips),
    "range_target_plans": canonicalize_int_set(
      configured_range_targets()
    ),
    "range_tp_buffer": settings.auto_trade_range_tp_buffer_pips,
    "candidate_storage_ttl_seconds": settings.auto_trade_candidate_ttl,
    "candidate_execution_max_age_seconds": (
      settings.auto_trade_candidate_max_age_seconds
    ),
    "spot_max_age_seconds": settings.auto_trade_spot_max_age,
    "range_flip": settings.auto_trade_range_flip_enabled,
    "two_sided_range": settings.auto_trade_range_two_sided_enabled,
    "concurrent_strategies": settings.auto_trade_allow_concurrent_strategies,
    "hedging_policy": settings.auto_trade_allow_hedged_xau,
    "broker_hedging_capability": None,
    "zone_fill": settings.auto_trade_zone_fill_enabled,
    "trend_enabled": settings.auto_trade_trend_enabled,
    "range_enabled": settings.auto_trade_range_enabled,
    "mapped_zone_enabled": settings.auto_trade_mapped_zone_enabled,
    "map_thesis_lock_enabled": settings.auto_trade_map_thesis_lock_enabled,
    "strategy_match_enabled": settings.auto_trade_strategy_match_enabled,
    "execution_zone_max_width_atr": (
      settings.auto_trade_execution_zone_max_width_atr
    ),
    "execution_zone_max_width_pips": (
      settings.auto_trade_execution_zone_max_width_pips
    ),
    "breakout_enabled": settings.auto_trade_breakout_enabled,
    "retest_enabled": settings.auto_trade_retest_enabled,
    "reaction_enabled": settings.auto_trade_reaction_enabled,
    "liquidity_reversal_enabled": (
      settings.auto_trade_liquidity_reversal_enabled
    ),
    "allow_counter_bias": settings.auto_trade_allow_counter_bias,
    "min_confluence": settings.auto_trade_min_confluence,
    "account_mode": "demo"
    if settings.auto_trade_require_demo_account else "live",
    "require_demo_account": settings.auto_trade_require_demo_account,
    "broker": canonicalize_broker(raw_broker),
    "broker_configured": raw_broker,
    "non_hedged_opposite_policy": (
      settings.auto_trade_non_hedged_opposite_policy
    ),
    "structural_guard_mode": settings.auto_trade_structural_guard_mode,
    "zone_cooldown_enabled": settings.auto_trade_zone_cooldown_enabled,
    "zone_reconcile_mode": settings.auto_trade_zone_reconcile_mode,
    "range_box_scale_out_enabled": (
      settings.auto_trade_range_box_scale_out_enabled
    ),
    "range_box_scale_out_threshold_pips": (
      settings.auto_trade_range_box_scale_out_threshold_pips
    ),
    "range_box_scale_out_trigger_pips": (
      settings.auto_trade_range_box_scale_out_trigger_pips
    ),
    "range_box_scale_out_fraction": (
      settings.auto_trade_range_box_scale_out_fraction
    ),
    "range_box_move_sl_to_be_after_scale_out": (
      settings.auto_trade_range_box_move_sl_to_be_after_scale_out
    ),
    "candidate_contract_version": (
      settings.auto_trade_candidate_contract_version
    ),
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
    "contract_size",
    "range_tp_buffer",
    "candidate_execution_max_age_seconds",
    "candidate_storage_ttl_seconds",
    "spot_max_age_seconds",
    "min_confluence",
    "candidate_contract_version",
    "config_manifest_version",
    "range_box_scale_out_threshold_pips",
    "range_box_scale_out_trigger_pips",
    "range_box_scale_out_fraction",
    "execution_zone_max_width_atr",
    "execution_zone_max_width_pips",
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
    "contract_size",
    "candidate_contract_version",
    "target_plans",
    "range_target_plans",
    "range_tp_buffer",
    "candidate_execution_max_age_seconds",
    "spot_max_age_seconds",
    "require_demo_account",
    "execution_zone_max_width_atr",
    "execution_zone_max_width_pips",
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
    "profile": settings.auto_trade_profile,
    "checked_at": datetime.now(timezone.utc).isoformat(),
  }
  encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
  await client.set(CONFIG_HEALTH_KEY, encoded)
  await client.xadd(
    settings.auto_trade_event_stream,
    {"payload": json.dumps({
      "type": "config_health",
      "timestamp": int(datetime.now(timezone.utc).timestamp()),
      "message": f"configuration health: {health['state']}",
      "profile": settings.auto_trade_profile,
      "health": health,
    }, separators=(",", ":"))},
    maxlen=max(100, settings.auto_trade_stream_maxlen),
    approximate=True,
  )
  return payload
