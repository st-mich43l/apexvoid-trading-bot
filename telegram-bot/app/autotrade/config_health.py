"""Cross-service startup configuration manifests and compatibility health."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from typing import Any
from urllib.parse import urlparse

from app.autotrade import units
from app.autotrade.range_targets import configured_range_targets
from app.core.config import settings


PYTHON_MANIFEST_KEY = "auto_trade:config_manifest:python"
CTRADER_MANIFEST_KEY = "auto_trade:config_manifest:ctrader"
CONFIG_HEALTH_KEY = "auto_trade:config_health"


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


def python_manifest() -> dict[str, Any]:
  fingerprint, database = _redis_identity(settings.redis_url)
  symbols = sorted({
    item.strip().upper()
    for item in settings.auto_trade_symbols.split(",")
    if item.strip()
  })
  now = datetime.now(timezone.utc)
  return {
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
    "pip_size": units.pip_size("XAU"),
    "contract_size": settings.auto_trade_contract_size,
    "target_plans": [
      int(item)
      for item in settings.auto_trade_tp_pips.split(",")
      if item.strip()
    ],
    "range_target_plans": list(configured_range_targets()),
    "range_tp_buffer": settings.auto_trade_range_tp_buffer_pips,
    "candidate_ttl": settings.auto_trade_candidate_ttl,
    "candidate_max_age": settings.auto_trade_strategy_match_max_age_seconds,
    "spot_max_age": settings.auto_trade_spot_max_age,
    "range_flip": settings.auto_trade_range_flip_enabled,
    "two_sided_range": settings.auto_trade_range_two_sided_enabled,
    "concurrent_strategies": settings.auto_trade_allow_concurrent_strategies,
    "hedging_policy": settings.auto_trade_allow_hedged_xau,
    "hedging_capability": settings.auto_trade_allow_hedged_xau,
    "zone_fill": settings.auto_trade_market_map_strategy_enabled,
    "trend_enabled": settings.auto_trade_trend_enabled,
    "range_enabled": settings.auto_trade_range_enabled,
    "mapped_zone_enabled": settings.auto_trade_market_map_strategy_enabled,
    "strategy_match_enabled": settings.auto_trade_strategy_bridge_enabled,
    "breakout_enabled": settings.auto_trade_breakout_enabled,
    "retest_enabled": settings.auto_trade_retest_enabled,
    "reaction_enabled": settings.auto_trade_reaction_enabled,
    "liquidity_reversal_enabled": (
      settings.auto_trade_liquidity_reversal_enabled
    ),
    "allow_counter_bias": settings.auto_trade_allow_counter_bias,
    "min_confluence": settings.auto_trade_min_confluence,
    "account_mode": "demo_required"
    if settings.auto_trade_require_demo_account else "unspecified",
    "broker": os.getenv("AUTO_TRADE_EXPECTED_BROKER", ""),
    "candidate_contract_version": (
      settings.auto_trade_candidate_contract_version
    ),
    "generated_at": int(now.timestamp()),
    "generated_at_iso": now.isoformat(),
  }


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
    "auto_trade_enabled",
    "dry_run",
    "manual_algo_enabled",
    "manual_algo_dry_run",
    "candidate_stream",
    "event_stream",
    "redis_database",
    "redis_fingerprint",
    "symbols",
    "canonical_symbol",
    "pip_size",
    "candidate_contract_version",
  )
  fatal = [
    field for field in fatal_fields
    if python.get(field) != ctrader.get(field)
  ]
  if python.get("target_plans") != ctrader.get("target_plans"):
    fatal.append("target_plans")
  if python.get("range_target_plans") != ctrader.get("range_target_plans"):
    fatal.append("range_target_plans")
  warnings = [
    field
    for field in (
      "range_flip",
      "two_sided_range",
      "concurrent_strategies",
      "hedging_policy",
      "zone_fill",
      "hedging_capability",
      "trend_enabled",
      "range_enabled",
      "mapped_zone_enabled",
      "strategy_match_enabled",
      "breakout_enabled",
      "retest_enabled",
      "reaction_enabled",
      "liquidity_reversal_enabled",
      "allow_counter_bias",
      "min_confluence",
      "profile",
    )
    if python.get(field) != ctrader.get(field)
  ]
  state = "fatal" if fatal else "warning" if warnings else "healthy"
  return {"state": state, "fatal": fatal, "warnings": warnings}


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
