"""Price-action scanner over closed Redis OHLC bars."""

import json
import logging
import math
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Awaitable, Callable, Iterable

from app.persistence import redis_state
from app.core.config import settings
from app.analysis.detectors import (
  DEFAULT_DETECTORS,
  DetectionContext,
  DetectionResult,
  SetupDetector,
  build_context,
  detector_settings_from,
)
from app.analysis.actionability import (
  ActionabilityDecision,
  resolve_actionability,
)
from app.analysis.execution_eligibility import (
  ANALYSIS_ONLY,
  EXECUTION_ELIGIBILITY_VERSION,
  STATIC_ELIGIBLE,
  ExecutionEligibility,
)
from app.analysis.market_map import (
  MarketMap,
  build_map,
  map_reference,
  market_map_payload,
  rail_reference,
)
from app.analysis.market_map_delivery import cache_analysis
from app.analysis.ohlc_source import RedisOHLCSource, window_for_timeframe
from app.analysis.structure import Zone
from app.analysis.confluence_zone import (
  ConfluenceMember,
  confluence_setup_id,
  merge_confluence_zones,
  validate_zone_width,
)
from app.analysis.zones import ZONE_RECONCILED_TAG_PREFIX
from app.autotrade.range_targets import select_range_target
from app.autotrade.strategy_match import (
  STRATEGY_MATCH_VERSION,
  StrategyMatch,
  strategy_match_id,
  strategy_match_key,
  strategy_range_id,
)
from app.analysis.structural_reaction_support import (
  STRUCTURAL_SETUPS,
  structural_thesis_id,
  v7_thesis_id,
)
from app.autotrade.execution_policy import (
  FAMILY_UNKNOWN,
  classify_tier,
  evaluate_execution_policy,
  risk_multiplier_for_tier,
  strategy_family,
)
from app.autotrade.multi_match import (
  dedupe_matches,
  deserialize_matches,
  select_primary,
  serialize_matches,
  strategy_matches_key,
)
from app.autotrade.setup_lifecycle import (
  ARMED_WAITING_TRIGGER,
  CONFIRMED,
  DISCOVERED,
  FORMING,
  INVALIDATED,
  READY_EVENT_ENQUEUED,
  TOUCHED,
  WATCHING,
  WORKER_ACKNOWLEDGED,
  SetupLifecycleError,
  create_setup,
  load_setup,
  transition_setup,
)
from app.autotrade.strategy_match_ready import enqueue_strategy_match_ready
from app.autotrade.setup_card import kill_setup_card
from app.autotrade import worker as autotrade_worker

_PRE_CONFIRMED_CHAIN = (DISCOVERED, WATCHING, TOUCHED, FORMING, CONFIRMED)
from app.autotrade.lifecycle import emit_lifecycle, increment_metric
from app.autotrade.route_outcome import record_route_outcome
from app.autotrade.range_context import (
  SCANNER_SNAPSHOT_TTL_SECONDS,
  SCANNER_SOURCE_MAX_AGE_SECONDS,
  RangeContext,
  continue_range_episode,
  persist_scanner_range_observation,
  range_context_source_key,
  scanner_range_context,
)
from app.autotrade import units
from app.autotrade.map_strategy import market_map_display_key, market_map_key
from app.core.symbols import SYMBOLS, canonical_symbol, pip_for
from app.bot.client import (
  delete_scanner_message,
  edit_scanner_message_text,
  send_scanner_with_retry,
)
from app.autotrade.setup_card import post_or_edit_forming_card

log = logging.getLogger(__name__)

NotifyFn = Callable[..., Awaitable[Any]]

_STRUCTURAL_REASON_RE = re.compile(
  r"(?<![a-z0-9])(?:ob|fvg|breaker|sweep|demand|supply|swing|zone)"
  r"(?![a-z0-9])",
  re.IGNORECASE,
)
_COUNTER_BIAS_RANGE_STATES = {
  "provisional_range",
  "post_impulse_range",
}


class SpotSnapshot:
  def __init__(self, price: float, ts: int, fresh: bool) -> None:
    self.price = price
    self.ts = ts
    self.fresh = fresh


def _csv(value: str) -> list[str]:
  return [
    item.strip().upper()
    for item in value.split(",")
    if item.strip()
  ]


def _watched_symbols() -> set[str]:
  return set(_csv(settings.scanner_symbols))


def _htf_tfs() -> list[str]:
  return _csv(settings.scanner_htf)


def _all_tfs(exec_tf: str, htf_tfs: Iterable[str]) -> list[str]:
  result = [exec_tf.upper()]
  for tf in htf_tfs:
    tf = tf.upper()
    if tf not in result:
      result.append(tf)
  return result


def _detector_settings():
  return detector_settings_from(settings)


def _parse_bar_event(data: object) -> tuple[str, str, str] | None:
  text = data.decode() if isinstance(data, bytes) else str(data)
  parts = text.strip().split(":")
  if len(parts) < 3:
    return None
  symbol, tf = parts[0].upper(), parts[1].upper()
  return symbol, tf, ":".join(parts[2:])


def _price_text(value: float, symbol: str, *, grouped: bool = False) -> str:
  digits = int(SYMBOLS.get(canonical_symbol(symbol), {}).get("digits", 2))
  spec = f",.{digits}f" if grouped else f".{digits}f"
  return f"{value:{spec}}".rstrip("0").rstrip(".")


def _pip_size(symbol: str) -> float:
  try:
    return pip_for(symbol)
  except KeyError:
    return 1.0


def _level_bucket(symbol: str, level: float, bucket_pips: int) -> str:
  pip = _pip_size(symbol)
  unit = max(1, int(bucket_pips)) * pip
  bucket = round(float(level) / unit) * unit
  return _price_text(bucket, symbol)


def _dedup_key(symbol: str, tf: str, result: DetectionResult) -> str:
  if result.confluence_zone_id:
    return (
      f"scanner:alerted:{symbol}:{tf}:confluence:"
      f"{result.direction}:{result.confluence_zone_id}"
    )
  if result.structural_id:
    return (
      f"scanner:alerted:{symbol}:{tf}:{result.setup}:"
      f"{result.structural_id}:{result.touch_bar_ts or ''}:"
      f"{result.confirmation_bar_ts or ''}"
    )
  bucket = _level_bucket(
    symbol,
    result.key_level,
    settings.scanner_level_bucket,
  )
  return f"scanner:alerted:{symbol}:{tf}:{result.setup}:{bucket}"


def _band_dedup_key(symbol: str, result: DetectionResult) -> str:
  if result.confluence_zone_id:
    return (
      f"scanner:alerted_band:{symbol}:{result.direction}:"
      f"confluence:{result.confluence_zone_id}"
    )
  if result.structural_id:
    low = float(result.entry_zone.low)
    high = float(result.entry_zone.high)
    midpoint = (
      (low + high) / 2
      if math.isfinite(low) and math.isfinite(high) and high > low
      else float(result.key_level)
    )
    bucket = _level_bucket(
      symbol,
      midpoint,
      settings.scanner_level_bucket,
    )
    return (
      f"scanner:alerted_band:{symbol}:{result.direction}:"
      f"{result.structural_source or result.setup}:{bucket}"
    )
  midpoint = (result.entry_zone.low + result.entry_zone.high) / 2
  bucket = _level_bucket(
    symbol,
    midpoint,
    settings.scanner_level_bucket,
  )
  return (
    f"scanner:alerted_band:{symbol}:{result.direction}:"
    f"{result.mode}:{result.setup}:{bucket}"
  )


def _configured_strategy_targets() -> tuple[int, ...]:
  values = {
    int(item.strip())
    for item in settings.auto_trade_tp_pips.split(",")
    if item.strip().isdigit() and int(item.strip()) > 0
  }
  return tuple(sorted(values))


def _build_strategy_match(
  symbol: str,
  tf: str,
  event_ts: str,
  ctx: DetectionContext,
  results: list[DetectionResult],
  *,
  now: int | None = None,
) -> tuple[StrategyMatch | None, str | None, dict[str, Any]]:
  """Transport scanner strategy matches to Algo.

  Builds typed matches for every detection result, dedupes same-thesis
  setups, and returns the primary match for the legacy single-key contract.
  All matches are persisted under strategy_matches:{symbol}.
  """
  if not results:
    return None, "no_detection_result", {}
  built: list[StrategyMatch] = []
  last_reason = "no_detection_result"
  last_measured: dict[str, Any] = {}
  for result in sorted(results, key=_result_rank):
    match, reason, measured = _build_one_strategy_match(
      symbol, tf, event_ts, ctx, result, now=now,
    )
    if match is None:
      last_reason = reason or "match_build_failed"
      last_measured = measured
      continue
    built.append(match)
  if not built:
    return None, last_reason, last_measured
  atr = built[0].atr
  deduped, _events = dedupe_matches(built, atr=atr, cfg=settings)
  primary = select_primary(deduped)
  if primary is None:
    return None, "all_matches_tier_c", {"count": len(built)}
  # Stash multi-match payload for _sync_strategy_match via measured.
  return primary, None, {
    "matches": len(deduped),
    "raw": len(built),
    "all_matches": deduped,
  }


def _build_one_strategy_match(
  symbol: str,
  tf: str,
  event_ts: str,
  ctx: DetectionContext,
  result: DetectionResult,
  *,
  now: int | None = None,
) -> tuple[StrategyMatch | None, str | None, dict[str, Any]]:
  indicators = getattr(ctx, "indicators", None)
  if not isinstance(indicators, dict):
    return None, "missing_indicators", {}
  indicator = indicators.get(tf.upper())
  if indicator is None or indicator.atr.empty:
    return None, "missing_atr_series", {}
  atr = float(indicator.atr.iloc[-1])
  if not math.isfinite(atr) or atr <= 0:
    return None, "invalid_atr", {"atr": atr}
  issued_at = (
    int(datetime.now(timezone.utc).timestamp())
    if now is None else int(now)
  )
  ttl = max(60, int(settings.auto_trade_strategy_match_max_age_seconds))
  entry_low = float(result.entry_zone.low)
  entry_high = float(result.entry_zone.high)
  direction = result.direction.upper()
  structure_swing = entry_low if direction == "BUY" else entry_high
  targets_pips = _configured_strategy_targets()
  range_id = None
  range_low = None
  range_high = None
  full_take_profit_pips = None
  range_state = None
  one_sided = result.setup == "One-Sided Range Reaction"
  post_impulse = False
  fallback_edge = False
  if result.setup in {"Range Edge Scalp", "One-Sided Range Reaction"} and (
    result.mode in {"range_scalp", "one_sided_range"}
  ):
    structures = getattr(ctx, "structures", None)
    structure = (
      structures.get(tf.upper()) if isinstance(structures, dict) else None
    )
    scalp_range = None if structure is None else structure.scalp_range
    if scalp_range is None and not one_sided:
      return None, "missing_scalp_range", {}
    if scalp_range is not None:
      range_low = float(scalp_range.lower.level)
      range_high = float(scalp_range.upper.level)
      range_state = getattr(scalp_range, "state", None)
      post_impulse = bool(getattr(scalp_range, "post_impulse", False))
      fallback_edge = bool(
        getattr(scalp_range.lower, "fallback", False)
        or getattr(scalp_range.upper, "fallback", False)
      )
      room = (
        range_high - float(result.current_price)
        if direction == "BUY"
        else float(result.current_price) - range_low
      )
      eq_room = abs(float(scalp_range.eq) - float(result.current_price))
      room = max(room, eq_room)
      room_pips = room / units.pip_size(symbol)
      full_take_profit_pips = select_range_target(room_pips)
      if full_take_profit_pips is None:
        return None, "insufficient_target_room", {
          "room_pips": round(room_pips, 1),
          "range_low": range_low,
          "range_high": range_high,
        }
      targets_pips = (full_take_profit_pips,)
      range_id = strategy_range_id(symbol, range_low, range_high)
  if result.target_cap_pips is not None:
    target_cap = float(result.target_cap_pips)
    targets_pips = tuple(
      target for target in targets_pips
      if float(target) <= target_cap + 1e-9
    )
    if not targets_pips:
      return None, "opposing_barrier_no_target", {
        "effective_target_pips": target_cap,
      }
    if full_take_profit_pips is not None:
      full_take_profit_pips = max(targets_pips)
  if not targets_pips:
    return None, "empty_target_config", {}
  family = strategy_family(result.setup)
  if family == FAMILY_UNKNOWN:
    return None, "unknown_strategy_policy", {"strategy": result.setup}
  tier = classify_tier(
    confluence=int(result.confluence),
    strategy=result.setup,
    range_state=range_state,
    fallback_edge=fallback_edge,
    post_impulse=post_impulse,
    one_sided=one_sided,
  )
  if tier == "C":
    return None, "tier_c_analysis_only", {
      "strategy": result.setup,
      "confluence": int(result.confluence),
    }
  risk_mult = risk_multiplier_for_tier(
    tier,
    settings,
    post_impulse=post_impulse,
    one_sided=one_sided,
  )
  structural_source = result.structural_source or ""
  structural_id = result.structural_id
  if result.confluence_zone_id:
    match_id = confluence_setup_id(
      result.confluence_zone_id,
      direction,
    )
    zone_id = result.confluence_zone_id
    level_id = result.confluence_zone_id
  elif range_id is not None:
    # Range Edge Scalp has no structural_id/confluence_zone_id (it isn't
    # zone-reaction evidence), so it used to fall all the way through to
    # strategy_match_id below - which, unlike confluence_setup_id, DOES
    # fold in event_ts, making the edge's identity re-roll every bar
    # instead of staying stable for "one range episode owns both edges,
    # one setup per edge" (range_id itself is already stable: just
    # symbol + range bounds, no timestamp - reuse confluence_setup_id's
    # hash shape rather than inventing a second one).
    match_id = confluence_setup_id(range_id, direction)
    zone_id = range_id
    level_id = range_id
  elif structural_id:
    match_id = structural_thesis_id(
      symbol=symbol,
      strategy=result.setup,
      direction=direction,
      structural_source=structural_source or result.setup,
      structural_id=structural_id,
      touch_bar_ts=str(result.touch_bar_ts or ""),
      confirmation_bar_ts=str(result.confirmation_bar_ts or ""),
    )
    zone_id = structural_id
    level_id = structural_id
  else:
    match_id = strategy_match_id(
      symbol,
      tf,
      event_ts,
      result.setup,
      result.direction,
      entry_low,
      entry_high,
    )
    zone_id = (
      f"{symbol.upper()}:{tf.upper()}:{direction}:"
      f"{entry_low:.5f}:{entry_high:.5f}"
    )
    level_id = (
      f"{symbol.upper()}:{tf.upper()}:level:{float(result.key_level):.5f}"
    )
  tags = []
  structural_tags = result.confluence_tags or (
    (result.structural_kind,) if result.structural_kind else ()
  )
  tags.extend(f"kind:{item}" for item in structural_tags)
  if result.bias_relationship:
    tags.append(f"bias:{result.bias_relationship}")
  if result.confirmation_type or result.confirmation:
    tags.append(f"confirm:{result.confirmation_type or result.confirmation}")
  if result.source_touches is not None:
    tags.append(f"touches:{result.source_touches}")
  match = StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id=match_id,
    symbol=symbol.upper(),
    source_tf=tf.upper(),
    event_ts=str(event_ts),
    issued_at=issued_at,
    expires_at=issued_at + ttl,
    strategy=result.setup,
    strategy_mode=result.mode,
    direction=direction,
    key_level=float(result.key_level),
    entry_low=entry_low,
    entry_high=entry_high,
    current_price=float(result.current_price),
    confluence=int(result.confluence),
    reasons=tuple(result.reasons),
    atr=atr,
    structure_swing=structure_swing,
    targets_pips=targets_pips,
    range_id=range_id,
    range_low=range_low,
    range_high=range_high,
    full_take_profit_pips=full_take_profit_pips,
    tags=tuple(tags),
    tier=tier,
    risk_multiplier=risk_mult,
    family=family,
    range_state=range_state,
    structural_source=structural_source or result.setup,
    zone_id=zone_id,
    confluence_zone_id=result.confluence_zone_id,
    level_id=level_id,
    structural_zone_id=structural_id,
    structural_zone_low=(
      None if result.structural_low is None else float(result.structural_low)
    ),
    structural_zone_high=(
      None if result.structural_high is None else float(result.structural_high)
    ),
    touch_bar_ts=result.touch_bar_ts,
    confirmation_bar_ts=result.confirmation_bar_ts,
    reaction_type=result.confirmation_type or result.confirmation,
    structural_kind=result.structural_kind,
    structural_timeframe=result.structural_timeframe,
    htf_bias=str(getattr(ctx, "htf_bias", "") or ""),
    regime_kind=str(getattr(getattr(ctx, "regime", None), "kind", "") or ""),
    execution_eligibility=result.execution_eligibility,
  )
  return match, None, {}


async def _record_match_build_rejected(
  client: Any,
  symbol: str,
  reason: str,
  measured: dict[str, Any],
) -> None:
  """Persist why a detected setup never became an executable StrategyMatch.

  Mirrors worker.py's _record_gate_reject key convention so operators check
  one counter family (auto_trade:gate_reject:{symbol}:{reason}) regardless
  of which stage rejected the setup, plus a last-outcome snapshot for
  /auto_status - see auto_trade:last_match_build:{symbol}.
  """
  try:
    await client.hincrby(
      f"auto_trade:gate_reject:{symbol.upper()}:{reason}", "count", 1,
    )
    await client.set(
      f"auto_trade:last_match_build:{symbol.upper()}",
      json.dumps({
        "stage": "match_build_rejected",
        "reason": reason,
        "measured": measured,
        "checked_at": datetime.now(timezone.utc).isoformat(),
      }, separators=(",", ":")),
      ex=3600,
    )
  except Exception:
    log.exception(
      "match-build-rejected telemetry failed symbol=%s reason=%s",
      symbol,
      reason,
    )


async def _record_match_build_outcome(
  client: Any,
  symbol: str,
  match: StrategyMatch,
) -> None:
  try:
    await client.set(
      f"auto_trade:last_match_build:{symbol.upper()}",
      json.dumps({
        "stage": "match_ready",
        "strategy": match.strategy,
        "direction": match.direction,
        "full_take_profit_pips": match.full_take_profit_pips,
        "checked_at": datetime.now(timezone.utc).isoformat(),
      }, separators=(",", ":")),
      ex=3600,
    )
  except Exception:
    log.exception("match-build-outcome telemetry failed symbol=%s", symbol)


async def _advance_setup_to_confirmed(
  client: Any,
  match: StrategyMatch,
  symbol: str,
  tf: str,
) -> tuple[str, str] | None:
  """Run a successfully-built match through setup_lifecycle up to CONFIRMED.

  Scanner detection is synchronous - by the time `_build_one_strategy_match`
  returns a match, the underlying detector has already validated touch,
  formation, and confirmation in one pass (touch_bar_ts/confirmation_bar_ts/
  confirmation_type are already set). setup_lifecycle.py's value here isn't
  re-discovering those states over multiple bars; it's a durable, idempotent
  record of "this exact detection instance has already reached CONFIRMED",
  so a repeated scan of the same event (or the same structure re-confirming
  on a later bar) can never re-emit a FORMING card or silently create a
  second thesis. Returns (setup_id, thesis_id), or None if this match has no
  stable structural identity to build a setup/thesis from (analysis-only
  detections - e.g. round-number fallbacks - never enter the lifecycle at
  all, matching "missing_stable_thesis_id fails closed" for the plan
  builder downstream).
  """
  if not match.structural_zone_id or not match.family:
    return None
  thesis_id = v7_thesis_id(
    symbol=symbol,
    strategy_family=match.family,
    direction=match.direction,
    structural_id=match.structural_zone_id,
  )
  setup_id = match.match_id
  record, _created = await create_setup(
    client,
    setup_id=setup_id,
    thesis_id=thesis_id,
    symbol=symbol,
    source_structure_id=match.structural_zone_id,
    formation_timeframe=match.structural_timeframe or tf,
    expires_at=match.expires_at,
  )
  if record.state not in _PRE_CONFIRMED_CHAIN:
    # Already advanced past CONFIRMED (or terminal) - a repeated scan of the
    # same detection instance must not attempt to move it "backwards".
    return setup_id, thesis_id
  start = _PRE_CONFIRMED_CHAIN.index(record.state)
  try:
    for state in _PRE_CONFIRMED_CHAIN[start + 1:]:
      record, _changed = await transition_setup(
        client, setup_id, state, reason_code="scanner_detection",
      )
  except SetupLifecycleError:
    log.exception(
      "setup lifecycle advance failed symbol=%s setup_id=%s state=%s",
      symbol, setup_id, record.state,
    )
    return None
  return setup_id, thesis_id


async def _sync_strategy_match(
  client: Any,
  symbol: str,
  tf: str,
  event_ts: str,
  ctx: DetectionContext,
  results: list[DetectionResult],
  *,
  require_static_eligibility: bool = False,
) -> StrategyMatch | None:
  key = strategy_match_key(symbol)
  matches_key = strategy_matches_key(symbol)
  structures = getattr(ctx, "structures", None)
  indicators = getattr(ctx, "indicators", None)
  structure = (
    structures.get(tf.upper()) if isinstance(structures, dict) else None
  )
  indicator = (
    indicators.get(tf.upper()) if isinstance(indicators, dict) else None
  )
  range_context = None
  if (
    structure is not None
    and indicator is not None
    and not indicator.atr.empty
  ):
    atr = float(indicator.atr.iloc[-1])
    range_context = scanner_range_context(
      symbol=symbol,
      timeframe=tf,
      structure=structure,
      atr=atr,
      pip_size=_pip_size(symbol),
      generated_at=int(datetime.now(timezone.utc).timestamp()),
      ttl=SCANNER_SOURCE_MAX_AGE_SECONDS,
    )
    previous = await client.get(range_context_source_key(symbol, "scanner"))
    range_context = continue_range_episode(
      RangeContext.from_json(previous),
      range_context,
    )
    await persist_scanner_range_observation(
      client,
      symbol=symbol,
      context=range_context,
    )
    if range_context is not None:
      await increment_metric(client, "scanner_range_observed", symbol=symbol)
      if range_context.lower_barrier.fallback:
        await increment_metric(
          client, "fallback_support_created", symbol=symbol,
        )
      if range_context.upper_barrier.fallback:
        await increment_metric(
          client, "fallback_resistance_created", symbol=symbol,
        )
    elif previous is not None:
      await increment_metric(client, "scanner_range_withdrawn", symbol=symbol)
  else:
    previous = await client.get(range_context_source_key(symbol, "scanner"))
    await persist_scanner_range_observation(
      client,
      symbol=symbol,
      context=None,
    )
    if previous is not None:
      await increment_metric(client, "scanner_range_withdrawn", symbol=symbol)
  if not settings.auto_trade_strategy_match_enabled:
    await client.delete(key)
    await client.delete(matches_key)
    return None
  executable_results = (
    [
      result for result in results
      if result.execution_eligibility is not None
      and result.execution_eligibility.allowed
    ]
    if require_static_eligibility else results
  )
  match, reason, measured = _build_strategy_match(
    symbol,
    tf,
    event_ts,
    ctx,
    executable_results,
  )
  if match is None:
    if not settings.auto_trade_multi_match_enabled:
      await client.delete(key)
      await client.delete(matches_key)
    if reason is not None:
      await _record_match_build_rejected(client, symbol, reason, measured)
      if reason == "insufficient_target_room":
        await increment_metric(
          client, "insufficient_target_room", symbol=symbol,
        )
      await emit_lifecycle(
        client,
        "analysis_only",
        symbol=symbol,
        correlation_id=f"{symbol}:{tf}:{event_ts}",
        timeframe=tf,
        reason_code=reason,
        message="detected structure is analysis-only",
        measured=measured,
      )
    return None
  all_matches = measured.get("all_matches") if isinstance(measured, dict) else None
  current = (
    deserialize_matches(await client.get(matches_key))
    if settings.auto_trade_multi_match_enabled
    else []
  )
  incoming = all_matches if isinstance(all_matches, list) and all_matches else [match]
  if range_context is not None:
    incoming = [
      replace(item, range_id=range_context.range_id)
      if item.is_range_edge else item
      for item in incoming
    ]
  lifecycle_ready = []
  for item in incoming:
    lifecycle_ids = await _advance_setup_to_confirmed(
      client,
      item,
      symbol,
      tf,
    )
    if lifecycle_ids is not None:
      _setup_id, thesis_id = lifecycle_ids
      item = replace(item, thesis_id=thesis_id)
    lifecycle_ready.append(item)
    if item.strategy in STRUCTURAL_SETUPS or item.structural_source in {
      "key_level", "supply_demand", "session_level", "trendline",
    }:
      await increment_metric(
        client, "structural_reaction_match_built", symbol=symbol,
      )
  incoming = lifecycle_ready
  now = int(datetime.now(timezone.utc).timestamp())
  active = [item for item in current if item.expires_at >= now]
  combined, events = dedupe_matches(
    [*active, *incoming],
    atr=match.atr,
    cfg=settings,
  )
  if not settings.auto_trade_track_all_structural_matches:
    top_n = int(getattr(settings, "scanner_top_n", 3))
    if top_n > 0:
      combined = combined[:top_n]
  primary = select_primary(combined) or match
  await _record_match_build_outcome(client, symbol, primary)
  ttl = max(60, primary.expires_at - primary.issued_at)
  await client.set(key, primary.to_json(), ex=ttl)
  await client.set(matches_key, serialize_matches(combined), ex=ttl)
  await increment_metric(
    client,
    "multi_match_count",
    symbol=symbol,
    dimensions={"count": str(len(combined))},
  )
  canonical_ids = {item.match_id for item in combined}
  for tracked in incoming:
    if tracked.match_id not in canonical_ids:
      continue
    if (
      tracked.execution_eligibility is None
      or not tracked.execution_eligibility.allowed
    ):
      continue
    setup_record = await load_setup(client, tracked.match_id)
    if setup_record is not None and setup_record.state == CONFIRMED:
      if settings.auto_trade_direct_publish_enabled:
        direct_result = await autotrade_worker.try_publish_executable_signal(
          client,
          tracked,
          symbol=symbol,
          event_ts=tracked.event_ts,
        )
        if (
          direct_result.status
          != autotrade_worker.PUBLISH_STATUS_REMAINED_WATCHING
        ):
          # Already resolved synchronously in this same scanner cycle
          # (published, invalidated, or rejected) - never touch the
          # durable ready-stream for an outcome that is already final.
          continue
      ready_event_id = await enqueue_strategy_match_ready(
        client,
        tracked,
        market_map_id=(
          ""
          if tracked.execution_eligibility is None
          else tracked.execution_eligibility.market_map_id
        ),
      )
      if ready_event_id is not None:
        latest_setup = await load_setup(client, tracked.match_id)
        if latest_setup is not None and latest_setup.state == CONFIRMED:
          await transition_setup(
            client,
            tracked.match_id,
            READY_EVENT_ENQUEUED,
            reason_code="strategy_match_ready_enqueued",
          )
        await record_route_outcome(
          client,
          tracked,
          stage="scanner",
          status="queued",
          reason_code="strategy_match_ready_enqueued",
          message="durable worker-ready event persisted",
          measured={
            "ready_event_id": ready_event_id,
            "scanner_event_ts": tracked.event_ts,
            "entry_low": tracked.entry_low,
            "entry_high": tracked.entry_high,
          },
          retained=True,
          publish_status=False,
        )
    await emit_lifecycle(
      client,
      "detected",
      symbol=symbol,
      candidate_id=tracked.match_id,
      match_id=tracked.match_id,
      range_id=tracked.range_id,
      strategy=tracked.strategy,
      strategy_family=tracked.family,
      direction=tracked.direction,
      timeframe=tracked.source_tf,
      entry_zone={"low": tracked.entry_low, "high": tracked.entry_high},
      current_price=tracked.current_price,
      target_plan=list(tracked.targets_pips),
      message="structural opportunity detected",
    )
    latest_setup = await load_setup(client, tracked.match_id)
    worker_owns_status = bool(
      latest_setup is not None
      and latest_setup.state not in _PRE_CONFIRMED_CHAIN
    )
    if not worker_owns_status:
      await record_route_outcome(
        client,
        tracked,
        stage="scanner",
        status="detected",
        reason_code="strategy_match_detected",
        message="structural opportunity detected",
        measured={
          "spot_price": tracked.current_price,
          "entry_low": tracked.entry_low,
          "entry_high": tracked.entry_high,
          "guard_mode": settings.auto_trade_structural_guard_mode,
        },
        retained=True,
        publish_status=False,
      )
      await record_route_outcome(
        client,
        tracked,
        stage="scanner",
        status="checking",
        reason_code="execution_preflight_pending",
        message="worker execution preflight pending",
        measured={
          "spot_price": tracked.current_price,
          "entry_low": tracked.entry_low,
          "entry_high": tracked.entry_high,
          "guard_mode": settings.auto_trade_structural_guard_mode,
        },
        retained=True,
        publish_status=False,
      )
    await emit_lifecycle(
      client,
      "tracked",
      symbol=symbol,
      candidate_id=tracked.match_id,
      match_id=tracked.match_id,
      range_id=tracked.range_id,
      strategy=tracked.strategy,
      strategy_family=tracked.family,
      direction=tracked.direction,
      timeframe=tracked.source_tf,
      entry_zone={"low": tracked.entry_low, "high": tracked.entry_high},
      current_price=tracked.current_price,
      target_plan=list(tracked.targets_pips),
      message="strategy match retained in multi-match routing",
    )
  for event in events:
    if event.get("event") == "merged_confluence":
      await increment_metric(client, "duplicate_suppressed", symbol=symbol)
  log.info(
    "strategy match synced symbol=%s id=%s strategy=%s direction=%s "
    "tier=%s matches=%s",
    symbol,
    primary.match_id[:12],
    primary.strategy,
    primary.direction,
    primary.tier,
    measured.get("matches", 1) if isinstance(measured, dict) else 1,
  )
  return primary


# --- B3: setup invalidation --------------------------------------------------
# Mirrors the *pattern* of worker.py's _apply_box_retirement (autotrade path):
# a retirement-flag key with a TTL, checked on every subsequent scan, cleared
# once fired so a broken setup is never re-announced as invalidated twice.
# Cannot reuse that function directly - it operates on AutoScalpDecision/
# auto_trade:box:* state, a different pipeline entirely (see its docstring).
_ACTIVE_SETUP_TTL_SECONDS = 4 * 3600
_INVALIDATION_BREAK_BUFFER_ATR = 0.1


def _active_setup_band_key(
  symbol: str,
  tf: str,
  result: DetectionResult,
) -> str:
  low = float(result.entry_zone.low)
  high = float(result.entry_zone.high)
  midpoint = (
    (low + high) / 2
    if math.isfinite(low) and math.isfinite(high) and high > low
    else float(result.key_level)
  )
  bucket = _level_bucket(
    symbol,
    midpoint,
    settings.scanner_level_bucket,
  )
  return (
    f"scanner:setup:active_band:{symbol.upper()}:{tf.upper()}:"
    f"{result.direction.upper()}:{bucket}"
  )


def _legacy_active_setup_key(
  symbol: str,
  tf: str,
  setup: str,
  direction: str,
) -> str:
  slug = setup.lower().replace(" ", "_")
  return f"scanner:setup:active:{symbol.upper()}:{tf.upper()}:{slug}:{direction.upper()}"


# Backward-compatible alias for tests and one-off cleanup.
_active_setup_key = _legacy_active_setup_key


def _normalize_trade_direction(value: object) -> str | None:
  if value is None:
    return None
  if isinstance(value, int):
    if value == 0:
      return "BUY"
    if value == 1:
      return "SELL"
    return None
  text = str(value).strip().upper()
  if text in {"BUY", "B", "0"}:
    return "BUY"
  if text in {"SELL", "S", "1"}:
    return "SELL"
  return None


async def _autonomous_direction_active(
  client: Any,
  symbol: str,
  direction: str,
) -> bool:
  raw_ids = await client.smembers("auto_trade:positions")
  if not raw_ids:
    return False
  wanted = _normalize_trade_direction(direction)
  if wanted is None:
    return False
  for raw_id in raw_ids:
    token = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
    try:
      position_id = int(token)
    except (TypeError, ValueError):
      continue
    raw = await client.get(f"auto_trade:position:{position_id}")
    if not raw:
      continue
    try:
      payload = json.loads(
        raw.decode() if isinstance(raw, bytes) else str(raw)
      )
    except (TypeError, ValueError, json.JSONDecodeError):
      continue
    if not isinstance(payload, dict):
      continue
    if str(payload.get("symbol") or symbol).upper() != symbol.upper():
      continue
    if str(payload.get("parent_group_id") or "").strip():
      continue
    remaining = payload.get("remaining_volume")
    if remaining is not None:
      try:
        if int(remaining) <= 0:
          continue
      except (TypeError, ValueError):
        pass
    pos_dir = _normalize_trade_direction(payload.get("direction"))
    if pos_dir == wanted:
      return True
  return False


async def clear_active_setup_tracking(
  client: Any,
  symbol: str,
  *,
  tf: str | None = None,
  direction: str | None = None,
) -> None:
  """Drop invalidation watch state once a setup is entered or no longer relevant."""
  symbol_token = symbol.upper()
  if tf:
    patterns = (
      f"scanner:setup:active_band:{symbol_token}:{tf.upper()}:*",
      f"scanner:setup:active:{symbol_token}:{tf.upper()}:*",
    )
  else:
    patterns = (
      f"scanner:setup:active_band:{symbol_token}:*",
      f"scanner:setup:active:{symbol_token}:*",
    )
  wanted = str(direction or "").upper() or None
  for pattern in patterns:
    async for key in client.scan_iter(match=pattern):
      key_text = key.decode() if isinstance(key, bytes) else str(key)
      if wanted:
        parts = key_text.split(":")
        if ":active_band:" in key_text:
          if len(parts) < 2 or parts[-2] != wanted:
            continue
        elif parts[-1] != wanted:
          continue
      await client.delete(key)


async def _track_active_setups(
  client: Any,
  symbol: str,
  tf: str,
  sent: list[DetectionResult],
  match_ids_by_card: dict[int, str] | None = None,
) -> None:
  match_ids_by_card = match_ids_by_card or {}
  for index, result in enumerate(sent):
    payload = json.dumps({
      "setup": result.setup,
      "direction": result.direction,
      "zone_low": result.entry_zone.low,
      "zone_high": result.entry_zone.high,
      "confluence": result.confluence,
      # One forming card per setup (P4): carried through so
      # _check_setup_invalidations can delete this setup's card instead of
      # posting a standalone notification. None for setups that never
      # produced an execution match; those are silently retired from scanner
      # watch state and remain visible only in logs/telemetry.
      "match_id": match_ids_by_card.get(index),
    }, separators=(",", ":"))
    await client.set(
      _active_setup_band_key(symbol, tf, result),
      payload,
      ex=_ACTIVE_SETUP_TTL_SECONDS,
    )
    await client.delete(
      _legacy_active_setup_key(symbol, tf, result.setup, result.direction),
    )


async def _check_setup_invalidations(
  client: Any,
  symbol: str,
  tf: str,
  df: Any,
  _notify: NotifyFn,
  atr: float,
) -> None:
  if df.empty:
    return
  close = float(df["close"].iloc[-1])
  if not math.isfinite(close):
    return
  buffer = max(0.0, _INVALIDATION_BREAK_BUFFER_ATR) * max(0.0, atr)
  patterns = (
    f"scanner:setup:active_band:{symbol.upper()}:{tf.upper()}:*",
    f"scanner:setup:active:{symbol.upper()}:{tf.upper()}:*",
  )
  broken: list[dict[str, Any]] = []
  for pattern in patterns:
    async for key in client.scan_iter(match=pattern):
      raw = await client.get(key)
      if not raw:
        continue
      try:
        state = json.loads(raw)
      except (TypeError, json.JSONDecodeError):
        continue
      direction = state.get("direction")
      try:
        zone_low = float(state["zone_low"])
        zone_high = float(state["zone_high"])
      except (KeyError, TypeError, ValueError):
        await client.delete(key)
        continue
      invalidated = (
        close > zone_high + buffer if direction == "SELL"
        else close < zone_low - buffer if direction == "BUY"
        else False
      )
      if not invalidated:
        continue
      await client.delete(key)
      broken.append(state)
  if not broken:
    return
  for state in broken:
    direction = str(state.get("direction") or "")
    if await _autonomous_direction_active(client, symbol, direction):
      continue
    match_id = str(state.get("match_id") or "").strip()
    setup_record = await load_setup(client, match_id) if match_id else None
    if setup_record is not None and setup_record.state in (
      CONFIRMED, READY_EVENT_ENQUEUED, WORKER_ACKNOWLEDGED,
      ARMED_WAITING_TRIGGER,
    ):
      # One forming card per setup (P4): a setup still waiting to publish
      # gets its card deleted without a standalone Telegram notification and
      # is never re-carded afterwards. This covers the full durable
      # CONFIRMED -> READY_EVENT_ENQUEUED -> WORKER_ACKNOWLEDGED ->
      # ARMED_WAITING_TRIGGER handoff window (P1-1) - a setup can sit in
      # any of these for real wall-clock time waiting on the worker/ready-
      # stream round trip, and structure can break while it waits. A setup
      # that has ALREADY published a plan (PLAN_BUILT/PLAN_PUBLISHED/ARMED)
      # is left alone entirely here - it may have a live position, and its
      # card is still the anchor future FILLED/SL-moved/closed replies
      # thread to.
      try:
        await transition_setup(
          client, match_id, INVALIDATED, reason_code="structure_broke",
        )
        await kill_setup_card(
          client, match_id, reason_code="structure_broke",
          delete_fn=delete_scanner_message, edit_fn=edit_scanner_message_text,
        )
      except SetupLifecycleError:
        log.exception(
          "scanner could not invalidate setup symbol=%s tf=%s match_id=%s",
          symbol, tf, match_id,
        )
    else:
      log.info(
        "scanner setup silently retired symbol=%s tf=%s setup=%s "
        "direction=%s match_id=%s lifecycle_state=%s reason=structure_broke",
        symbol,
        tf,
        state.get("setup") or "setup",
        direction or "unknown",
        match_id or "none",
        getattr(setup_record, "state", "none"),
      )


def _htf_bias_text(ctx: DetectionContext, htf_order: list[str]) -> str:
  for tf in htf_order:
    structure = ctx.structures.get(tf)
    if structure and structure.bias == ctx.htf_bias and structure.bias != "range":
      return f"{ctx.htf_bias} ({tf})"
  if ctx.htf_bias != "range":
    return f"{ctx.htf_bias} ({ctx.tf})"
  return "range"


def _zone_text(zone: Zone, symbol: str, *, grouped: bool = False) -> str:
  return (
    f"{_price_text(zone.low, symbol, grouped=grouped)}"
    f"–{_price_text(zone.high, symbol, grouped=grouped)}"
  )


def _copy_draft(symbol: str, result: DetectionResult) -> str | None:
  """Build an editable one-line command without inventing SL/TP levels."""
  if symbol.upper() != "XAU":
    return None
  setup = re.sub(r"[^a-z0-9]+", "-", result.setup.lower()).strip("-")
  grade = "*" * max(1, min(3, int(result.confluence)))
  entry = (
    f"{_price_text(result.entry_zone.low, symbol)}-"
    f"{_price_text(result.entry_zone.high, symbol)}"
  )
  return (
    f"gold {result.direction.lower()} entry zone ({entry}) "
    f"/ sl SL / tp TP1/TP2/TP3 / setup {setup} {grade}"
  )


def _format_detection(
  symbol: str,
  tf: str,
  ctx: DetectionContext,
  result: DetectionResult,
  htf_order: list[str],
  also: list[DetectionResult] | None = None,
  market_map: MarketMap | None = None,
  execution_match: StrategyMatch | None = None,
) -> str:
  executable = bool(
    settings.auto_trade_enabled
    and execution_match is not None
    and (
      result.execution_eligibility is None
      or result.execution_eligibility.allowed
    )
  )
  stars = "⭐" * max(1, min(3, int(result.confluence)))
  direction_icon = "🟢" if result.direction.upper() == "BUY" else "🔴"
  confluence_label = " + ".join(
    _confluence_tag_label(tag) for tag in result.confluence_tags
  )
  setup_label = (
    f"{confluence_label} · {result.setup}"
    if len(result.confluence_tags) > 1
    else result.setup
  )
  extra_reasons = [
    reason for reason in result.reasons
    if not reason.lower().startswith("htf bias")
  ][:6 if result.setup in {"Box Breakout", "Range Edge Scalp"} else 2]
  lines = [
    (
      f"🔎 <b>{escape(symbol)} {escape(tf)} · SETUP FORMING</b>"
      if executable
      else f"🔵 <b>{escape(symbol)} {escape(tf)} · MARKET OBSERVATION</b>"
    ),
    (
      "🟡 <b>QUEUED</b> · worker acknowledgement pending"
      if executable
      else "🔵 <b>ANALYSIS ONLY</b> · no executable StrategyMatch"
      if settings.auto_trade_enabled
      else "🔵 <b>ANALYSIS ONLY</b> · autonomous execution disabled"
    ),
    (
      f"{direction_icon} <b>{escape(result.direction)} · "
      f"{escape(setup_label)}</b> · {stars}"
    ),
  ]
  if (
    not executable
    and result.execution_eligibility is not None
    and result.execution_eligibility.reason_code
  ):
    lines.append(
      "<b>Reason:</b> "
      f"{escape(result.execution_eligibility.reason_code)} · "
      f"{escape(result.execution_eligibility.message)}"
    )
  if result.mode == "range_scalp":
    lines.append("↔️ <b>Mode:</b> RANGE SCALP · two-sided local range")
  elif result.mode == "counter_bias":
    lines.append("⚠️ <b>Bias:</b> counter_bias")
  elif result.mode == "with_bias":
    lines.append("🧭 <b>Bias:</b> with_bias")
  elif result.mode == "neutral":
    lines.append("🧭 <b>Bias:</b> neutral")
  elif result.mode != "with_trend":
    label = "reaction scalp" if result.mode == "counter_reaction" else "counter swing"
    lines.append(
      f"⚠️ <b>Mode:</b> Counter-trend · {label}"
    )
  if result.structural_source:
    lines.append(
      f"🧱 <b>Structural source:</b> {escape(result.structural_source)}"
    )
  if result.confluence_tags:
    lines.append(
      f"🏷️ <b>Identity:</b> {escape(confluence_label)}"
    )
  elif result.structural_kind:
    lines.append(
      f"🏷️ <b>Identity:</b> {escape(str(result.structural_kind))}"
    )
  if result.confirmation_type or result.confirmation:
    lines.append(
      "✅ <b>Confirmation:</b> "
      f"{escape(str(result.confirmation_type or result.confirmation))}"
    )
  if result.structural_timeframe:
    lines.append(
      f"⏱ <b>Source TF:</b> {escape(str(result.structural_timeframe))}"
    )
  lines.extend([
    "",
    "📍 <b>Trade area</b>",
    _price_line(symbol, tf, ctx, result),
    (
      "• <b>Entry zone:</b> "
      f"<b>{_zone_text(result.entry_zone, symbol, grouped=True)}</b>"
    ),
    (
      "• <b>Key level:</b> "
      f"<b>{_price_text(result.key_level, symbol, grouped=True)}</b>"
    ),
    "",
    "🧭 <b>Context</b>",
    f"• <b>HTF bias:</b> {escape(_htf_bias_text(ctx, htf_order))}",
  ])
  regime_line = _regime_line(symbol, tf, ctx)
  if regime_line:
    lines.append(f"• {regime_line}")
  if market_map is not None:
    reference = map_reference(
      market_map,
      result.direction,
      result.entry_zone.low,
      result.entry_zone.high,
    )
    if reference:
      lines.append(f"• {escape(reference)}")
    rail = rail_reference(
      market_map,
      result.entry_zone.low,
      result.entry_zone.high,
    )
    if rail:
      lines.append(f"• {escape(rail)}")
  lines.extend(f"• {escape(reason)}" for reason in extra_reasons)
  for extra in also or []:
    extra_stars = "⭐" * max(1, min(3, int(extra.confluence)))
    lines.append(
      "• <b>Also:</b> "
      f"{escape(_compact_setup(extra.setup))} · "
      f"{escape(_zone_text(extra.entry_zone, symbol, grouped=True))} "
      f"{extra_stars}"
    )
  draft = _copy_draft(symbol, result) if executable else None
  if draft is not None:
    lines.extend([
      "",
      "📋 <b>Copy draft</b> <i>· fill SL/TP</i>",
      f"<code>{escape(draft)}</code>",
    ])
  if executable:
    lines.append("→ Review confirmation, SL &amp; TP before posting.")
  return "\n".join(lines)


def _regime_line(symbol: str, tf: str, ctx: DetectionContext) -> str | None:
  regime = getattr(ctx, "regime", None)
  if regime is None or getattr(regime, "kind", None) != "chop":
    return None
  low = _price_text(float(regime.range_low), symbol, grouped=True)
  high = _price_text(float(regime.range_high), symbol, grouped=True)
  return f"≈ range-bound {low}-{high} ({escape(tf.upper())}) · fading edge"


def _price_line(
  symbol: str,
  tf: str,
  ctx: DetectionContext,
  result: DetectionResult,
) -> str:
  if ctx.spot_price is not None:
    return (
      "• <b>Price now:</b> "
      f"<b>{_price_text(result.current_price, symbol, grouped=True)}</b> "
      "<i>(live)</i>"
    )
  return (
    "• <b>Trigger close:</b> "
    f"<b>{_price_text(result.current_price, symbol, grouped=True)}</b> "
    f"<i>({tf.upper()} · {_trigger_close_text(ctx, tf)})</i>"
  )


def _trigger_close_text(ctx: DetectionContext, tf: str) -> str:
  try:
    frame = ctx.frames[ctx.tf]
    ts = frame.index[-1]
    close_ts = ts.to_pydatetime() + timedelta(seconds=_tf_seconds(tf))
    close_ts = close_ts.astimezone(timezone.utc)
    return close_ts.strftime("%H:%M UTC")
  except Exception:
    return "trigger bar"


def _tf_seconds(tf: str) -> int:
  tf = tf.upper()
  if tf.startswith("M") and tf[1:].isdigit():
    return int(tf[1:]) * 60
  if tf.startswith("H") and tf[1:].isdigit():
    return int(tf[1:]) * 3600
  return 0


def _compact_setup(setup: str) -> str:
  return setup.replace(" & ", "&").replace(" ", "")


def _confluence_tag_label(tag: str) -> str:
  labels = {
    "key_level": "Key Level",
    "demand": "Demand",
    "supply": "Supply",
    "ob": "OB",
    "fvg": "FVG",
    "breaker": "Breaker",
    "session_level": "Session Level",
    "trendline": "Trendline",
  }
  normalized = str(tag).casefold()
  return labels.get(normalized, normalized.replace("_", " ").title())


async def _load_frames(
  source: RedisOHLCSource,
  symbol: str,
  exec_tf: str,
  htf_order: list[str],
  window: int | None = None,
) -> dict[str, Any]:
  frames = {}
  for tf in _all_tfs(exec_tf, htf_order):
    count = (
      max(50, int(window)) if window is not None
      else window_for_timeframe(tf)
    )
    df = await source.window(symbol, tf, count)
    if not df.empty:
      frames[tf] = df
  return frames


async def _load_spot_snapshot(client: Any, symbol: str) -> SpotSnapshot | None:
  raw = await client.get(f"price:{symbol.upper()}:spot")
  if raw is None:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    payload = json.loads(text)
    bid = float(payload["bid"])
    ask = float(payload["ask"])
    ts = int(payload["ts"])
  except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    return None
  price = (bid + ask) / 2
  now = int(datetime.now(timezone.utc).timestamp())
  return SpotSnapshot(
    price=price,
    ts=ts,
    fresh=now - ts <= max(0, settings.spot_fresh_secs),
  )


def _attach_price_context(
  ctx: DetectionContext,
  spot: SpotSnapshot | None,
  event_ts: str,
  df: Any,
) -> DetectionContext:
  price, ts = _trusted_spot_values(spot, df)
  try:
    return replace(ctx, spot_price=price, spot_ts=ts, trigger_ts=event_ts)
  except TypeError:
    setattr(ctx, "spot_price", price)
    setattr(ctx, "spot_ts", ts)
    setattr(ctx, "trigger_ts", event_ts)
    return ctx


def _trusted_spot_values(
  spot: SpotSnapshot | None,
  df: Any,
) -> tuple[float | None, int | None]:
  if spot is None or not spot.fresh:
    return None, None

  close = float(df["close"].iloc[-1])
  gate = max(0.0, settings.spot_max_deviation_pct) / 100.0
  bad = (
    not math.isfinite(spot.price)
    or spot.price <= 0
    or not math.isfinite(close)
    or close <= 0
    or abs(spot.price - close) / close > gate
  )
  if bad:
    log.warning(
      "spot %s implausible vs close %s (deviation gate %.1f%%) - "
      "falling back to bar close",
      spot.price,
      close,
      settings.spot_max_deviation_pct,
    )
    return None, None
  return spot.price, spot.ts


def _confluence_member_kind(result: DetectionResult) -> str:
  source = str(result.structural_source or "").casefold()
  kind = re.sub(
    r"[^a-z0-9]+",
    "_",
    str(result.structural_kind or "").casefold(),
  ).strip("_")
  if source == "key_level":
    return "key_level"
  if source == "supply_demand":
    if kind in {"demand", "supply", "ob", "fvg", "breaker"}:
      return kind
    return "demand" if result.direction.upper() == "BUY" else "supply"
  if source in {"session_level", "trendline"}:
    return source
  return kind or source or re.sub(
    r"[^a-z0-9]+",
    "_",
    result.setup.casefold(),
  ).strip("_")


def _confluence_member_band(
  symbol: str,
  result: DetectionResult,
) -> tuple[float, float]:
  low = float(result.entry_zone.low)
  high = float(result.entry_zone.high)
  if math.isfinite(low) and math.isfinite(high) and high > low:
    return low, high
  digits = int(SYMBOLS.get(canonical_symbol(symbol), {}).get("digits", 2))
  tick = 10 ** -max(0, digits)
  level = float(result.key_level)
  return level - tick, level + tick


def _merge_detection_confluence(
  symbol: str,
  tf: str,
  results: list[DetectionResult],
  *,
  atr: float,
) -> list[DetectionResult]:
  """Collapse same-side structural detector results before digest/cards."""
  indexed_structural = [
    (index, result)
    for index, result in enumerate(results)
    if result.structural_id
    and result.direction.upper() in {"BUY", "SELL"}
  ]
  if not indexed_structural:
    return list(results)

  members = []
  for _index, result in indexed_structural:
    low, high = _confluence_member_band(symbol, result)
    members.append(ConfluenceMember(
      member_id=str(result.structural_id),
      side="buy" if result.direction.upper() == "BUY" else "sell",
      low=low,
      high=high,
      kind=_confluence_member_kind(result),
      score=float(result.confluence),
    ))

  zones = merge_confluence_zones(
    members,
    symbol=symbol,
    atr=atr,
    pip_size=_pip_size(symbol),
    source_tf=tf,
    max_width=float(settings.zone_merge_max_width),
    gap=float(settings.zone_merge_gap),
  )
  merged_by_index: list[tuple[int, DetectionResult]] = []
  consumed: set[int] = set()
  for zone in zones:
    provenance = set(zone.provenance)
    group = [
      (index, result)
      for index, result in indexed_structural
      if str(result.structural_id) in provenance
      and (
        (result.direction.upper() == "BUY" and zone.side == "buy")
        or (result.direction.upper() == "SELL" and zone.side == "sell")
      )
    ]
    if not group:
      continue
    representative = min(
      (result for _index, result in group),
      key=_result_rank,
    )
    reasons = list(dict.fromkeys(
      reason
      for _index, result in group
      for reason in result.reasons
    ))
    score_reasons = list(dict.fromkeys(
      reason
      for _index, result in group
      for reason in (
        getattr(result.entry_zone, "score_reasons", None) or []
      )
    ))
    merged_entry = replace(
      representative.entry_zone,
      bottom=zone.low,
      top=zone.high,
      side="demand" if zone.side == "buy" else "supply",
      sources=list(zone.tags),
      score=zone.score,
      score_reasons=score_reasons,
    )
    merged = replace(
      representative,
      entry_zone=merged_entry,
      # Detector quality and structural diversity are separate dimensions.
      # Tags explain provenance; their count must not overwrite setup quality.
      confluence=max(int(result.confluence) for _index, result in group),
      reasons=reasons,
      structural_id=zone.zone_id,
      structural_low=zone.low,
      structural_high=zone.high,
      source_score=zone.score,
      confluence_zone_id=zone.zone_id,
      confluence_tags=zone.tags,
    )
    first_index = min(index for index, _result in group)
    consumed.update(index for index, _result in group)
    if settings.scanner_zone_width_gate_enabled:
      raw_low = representative.structural_low
      raw_high = representative.structural_high
      if raw_low is None or raw_high is None:
        raw_low = getattr(representative.entry_zone, "bottom", zone.low)
        raw_high = getattr(representative.entry_zone, "top", zone.high)
      is_major = (
        str(representative.structural_timeframe or "").upper() == "H1"
      )
      width_result = validate_zone_width(
        raw_width=float(raw_high) - float(raw_low),
        merged_width=zone.high - zone.low,
        merge_sources=zone.tags,
        is_major=is_major,
      )
      if not width_result.eligible:
        log.info(
          "confluence zone rejected by XAU width contract symbol=%s tf=%s "
          "reason=%s raw_width=%.3f merged_width=%.3f min=%.3f max=%.3f "
          "sources=%s",
          symbol, tf, width_result.rejection_reason,
          width_result.raw_zone_width, width_result.merged_zone_width,
          width_result.min_required_width, width_result.max_allowed_width,
          ",".join(width_result.merge_sources),
        )
        # Fails closed: a zone rejected for width is dropped entirely, not
        # left to fall through as its unmerged individual members.
        continue
    merged_by_index.append((first_index, merged))

  merged_by_index.extend(
    (index, result)
    for index, result in enumerate(results)
    if index not in consumed
  )
  return [
    result
    for _index, result in sorted(merged_by_index, key=lambda item: item[0])
  ]


def _reward_risk_pre_gate(
  symbol: str,
  tf: str,
  event_ts: str,
  ctx: DetectionContext,
  result: DetectionResult,
) -> tuple[bool, dict[str, Any]]:
  """Apply only the shared policy's provisional R/R check before lifecycle."""
  match, reason, build_measured = _build_one_strategy_match(
    symbol,
    tf,
    event_ts,
    ctx,
    result,
  )
  if match is None:
    measured = {
      **build_measured,
      "static_rejection_reason": reason or "match_build_failed",
    }
    static_build_blocks = {
      "tier_c_analysis_only",
      "insufficient_target_room",
      "opposing_barrier_no_target",
      "empty_target_config",
      "unknown_strategy_policy",
    }
    if reason in static_build_blocks:
      return False, measured
    # Incomplete analysis context (for example ATR warmup) prevents match
    # construction but remains visible as a non-executable observation.
    measured["static_rejection_reason"] = None
    measured["match_build_observation"] = reason or "match_build_failed"
    return True, measured
  regime = str(getattr(getattr(ctx, "regime", None), "kind", "") or "")
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    regime=regime or None,
    pip_size=_pip_size(symbol),
    cfg=settings,
  )
  measured = {
    **(result.target_room_measured or {}),
    **dict(evaluation.measured),
    "policy_reason_code": evaluation.reason_code,
    "policy_message": evaluation.message,
    "policy_hard_block": bool(evaluation.terminal),
  }
  if not evaluation.allowed:
    return False, measured
  reward_risk = measured.get("reward_risk")
  policy = evaluation.policy
  if (
    policy is None
    or reward_risk is None
    or measured.get("planned_stop_price") is None
  ):
    return True, measured
  try:
    value = float(reward_risk)
  except (TypeError, ValueError):
    return True, measured
  return (
    math.isfinite(value) and value >= float(policy.min_reward_risk),
    measured,
  )


def _static_execution_eligibility(
  result: DetectionResult,
  market_map: MarketMap | None,
  *,
  allowed: bool,
  reason_code: str,
  message: str,
  measured: dict[str, Any],
  hard_block: bool,
) -> ExecutionEligibility:
  def number(name: str) -> float | None:
    try:
      value = float(measured[name])
    except (KeyError, TypeError, ValueError):
      return None
    return value if math.isfinite(value) else None

  fitted = tuple(result.provisional_targets_pips)
  if result.target_cap_pips is not None:
    fitted = tuple(
      target for target in fitted
      if target <= float(result.target_cap_pips) + 1e-9
    )
  opposing = None
  if measured.get("opposing_low") is not None:
    opposing = {
      "low": measured.get("opposing_low"),
      "high": measured.get("opposing_high"),
      "tier": measured.get("opposing_tier"),
      "tags": measured.get("opposing_tags") or [],
    }
  return ExecutionEligibility(
    version=EXECUTION_ELIGIBILITY_VERSION,
    allowed=allowed,
    state=STATIC_ELIGIBLE if allowed else ANALYSIS_ONLY,
    reason_code=reason_code,
    message=message,
    hard_block=hard_block,
    direction=result.direction.upper(),
    entry_low=float(result.entry_zone.low),
    entry_high=float(result.entry_zone.high),
    planned_entry_price=float(
      result.planned_entry_price
      if result.planned_entry_price is not None
      else result.current_price
    ),
    fitted_targets_pips=fitted,
    effective_target_pips=(
      float(result.target_cap_pips)
      if result.target_cap_pips is not None
      else number("effective_target_pips")
    ),
    reward_risk=number("reward_risk"),
    minimum_reward_risk=number("min_reward_risk"),
    opposing_entry=opposing,
    opposing_room_pips=number("room_pips"),
    key_level_role=result.key_level_role,
    bias_relationship=result.bias_relationship or result.mode,
    market_map_id="" if market_map is None else market_map.map_id,
    calculated_at=int(datetime.now(timezone.utc).timestamp()),
    measured=dict(measured),
  )


def _annotate_actionability_geometry(
  symbol: str,
  tf: str,
  event_ts: str,
  ctx: DetectionContext,
  result: DetectionResult,
) -> DetectionResult:
  """Attach the same planned entry and targets later consumed by policy."""
  if not result.structural_id and result.setup not in STRUCTURAL_SETUPS:
    return result
  match, _reason, _measured = _build_one_strategy_match(
    symbol,
    tf,
    event_ts,
    ctx,
    result,
  )
  if match is None:
    return replace(
      result,
      planned_entry_price=float(result.current_price),
      provisional_targets_pips=_configured_strategy_targets(),
    )
  regime = str(getattr(getattr(ctx, "regime", None), "kind", "") or "")
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    regime=regime or None,
    pip_size=_pip_size(symbol),
    cfg=settings,
  )
  planned_entry = evaluation.measured.get("planned_entry_price")
  try:
    planned_entry_price = float(planned_entry)
  except (TypeError, ValueError):
    planned_entry_price = float(match.current_price)
  return replace(
    result,
    planned_entry_price=planned_entry_price,
    provisional_targets_pips=tuple(match.targets_pips),
  )


def _is_digest_primary(result: DetectionResult) -> bool:
  if result.structural_source or result.setup in STRUCTURAL_SETUPS:
    return True
  return result.mode in {"with_trend", "range_scalp"}


def _digest_results(
  results: list[DetectionResult],
) -> tuple[list[DetectionResult], list[dict[str, Any]]]:
  if settings.auto_trade_track_all_structural_matches:
    candidates, conflicts = _suppress_overlaps(results)
    return sorted(candidates, key=_result_rank), conflicts
  primary, primary_conflicts = _suppress_overlaps([
    result for result in results if _is_digest_primary(result)
  ])
  if primary:
    candidates, conflicts = primary, primary_conflicts
  else:
    candidates, conflicts = _suppress_overlaps([
      result for result in results if not _is_digest_primary(result)
    ])
  ordered = sorted(candidates, key=_result_rank)
  top_n = int(settings.scanner_top_n)
  return (ordered if top_n <= 0 else ordered[:top_n]), conflicts


def _structure_card_gate(
  result: DetectionResult,
  ctx: DetectionContext,
) -> str | None:
  if (
    settings.scanner_gate_require_structural_anchor
    and (result.structural_kind or "").casefold() == "round"
    and not any(_STRUCTURAL_REASON_RE.search(reason) for reason in result.reasons)
  ):
    return "round_without_structural_anchor"

  maximum_touches = int(settings.scanner_gate_max_source_touches)
  if (
    maximum_touches > 0
    and int(result.source_touches or 0) >= maximum_touches
  ):
    return "source_level_exhausted"

  counter_bias = any(
    str(value or "").casefold() == "counter_bias"
    for value in (result.mode, result.bias_relationship)
  )
  if (
    settings.scanner_gate_suppress_counter_bias_in_range
    and counter_bias
  ):
    structures = getattr(ctx, "structures", None)
    structure = (
      structures.get(str(getattr(ctx, "tf", "")).upper())
      if isinstance(structures, dict)
      else None
    )
    scalp_range = getattr(structure, "scalp_range", None)
    range_state = str(getattr(scalp_range, "state", "")).casefold()
    regime = getattr(ctx, "regime", None)
    fading_edge = (
      range_state in _COUNTER_BIAS_RANGE_STATES
      or str(getattr(regime, "kind", "")).casefold() == "chop"
    )
    minimum_confluence = int(
      settings.scanner_gate_counter_bias_min_confluence
    )
    if (
      fading_edge
      and result.confluence < minimum_confluence
    ):
      return "low_confluence_counter_bias_in_range"

  return None


def _conflict_record(
  stronger: DetectionResult,
  weaker: DetectionResult,
  outcome: str,
) -> dict[str, Any]:
  return {
    "outcome": outcome,  # "stronger_kept" | "both_dropped"
    "a": {
      "setup": stronger.setup,
      "direction": stronger.direction,
      "confluence": stronger.confluence,
    },
    "b": {
      "setup": weaker.setup,
      "direction": weaker.direction,
      "confluence": weaker.confluence,
    },
  }


def _suppress_overlaps(
  results: list[DetectionResult],
) -> tuple[list[DetectionResult], list[dict[str, Any]]]:
  """Same-direction overlap is a duplicate - keep the higher-ranked, drop
  the other.

  P0 zone/M1 simplification: this used to ALSO re-run its own opposing-
  direction confluence-margin tiebreak here, duplicating (with a slightly
  different overlap-ratio implementation) what
  actionability.py::resolve_actionability's contested-corridor rule
  already resolved earlier in the same request - by the time results
  reach this function they have already survived that check, so a second,
  independent cross-side gate here could only ever produce a different
  answer than the authoritative one upstream. Deleted, not duplicated.
  """
  ordered = sorted(results, key=_result_rank)
  selected: list[DetectionResult] = []
  conflicts: list[dict[str, Any]] = []
  same_threshold = max(0.0, settings.alert_overlap_suppress)
  for result in ordered:
    same_direction_duplicate = any(
      result.direction == kept.direction
      and (
        (
          bool(result.structural_id)
          and bool(kept.structural_id)
          and result.structural_id == kept.structural_id
          and result.touch_bar_ts == kept.touch_bar_ts
          and result.confirmation_bar_ts == kept.confirmation_bar_ts
        )
        or (
          result.setup == kept.setup
          and result.mode == kept.mode
          and _zone_overlap_ratio(result.entry_zone, kept.entry_zone)
            >= same_threshold
        )
      )
      for kept in selected
    )
    if same_direction_duplicate:
      continue
    selected.append(result)
  return selected, conflicts


def _structural_priority(result: DetectionResult) -> int:
  # First-class structural reactions outrank wrapper/legacy labels when ranked
  # together; among wrappers, confluence/score still decide.
  if result.setup in STRUCTURAL_SETUPS:
    return 0
  return 1


def _result_rank(result: DetectionResult) -> tuple[float, float, float, float]:
  return (
    float(_structural_priority(result)),
    -float(result.confluence),
    -float(getattr(result.entry_zone, "score", 0.0)),
    _result_zone_distance(result),
  )


def _result_zone_distance(result: DetectionResult) -> float:
  zone = result.entry_zone
  price = result.current_price
  if zone.low <= price <= zone.high:
    return 0.0
  return min(abs(price - zone.low), abs(price - zone.high))


def _zone_overlap_ratio(first: Zone, second: Zone) -> float:
  overlap = min(first.high, second.high) - max(first.low, second.low)
  if overlap <= 0:
    return 0.0
  smaller = min(first.high - first.low, second.high - second.low)
  if smaller <= 0:
    return 1.0
  return overlap / smaller


async def _notify_digest_once(
  client: Any,
  symbol: str,
  tf: str,
  ctx: DetectionContext,
  results: list[DetectionResult],
  notify: NotifyFn,
  htf_order: list[str],
  market_map: MarketMap | None = None,
  execution_match: StrategyMatch | None = None,
  edit: NotifyFn | None = None,
  execution_matches: Iterable[StrategyMatch] | None = None,
) -> list[DetectionResult]:
  if not results:
    return []
  if not settings.telegram_owner_id:
    log.info(
      "scanner detection suppressed: TELEGRAM_OWNER_ID not set "
      "symbol=%s tf=%s count=%s",
      symbol,
      tf,
      len(results),
    )
    return []

  claimed_results = []
  for result in results:
    band_key = _band_dedup_key(symbol, result)
    if await client.get(band_key) is not None:
      log.debug(
        "scanner detection suppressed by zone band TTL "
        "symbol=%s tf=%s key=%s",
        symbol,
        tf,
        band_key,
      )
      continue
    key = _dedup_key(symbol, tf, result)
    claimed = await client.set(
      key,
      "1",
      ex=settings.scanner_alert_ttl,
      nx=True,
    )
    if claimed:
      claimed_results.append(result)
  if not claimed_results:
    return []
  for result in claimed_results:
    await client.set(
      _band_dedup_key(symbol, result),
      "1",
      ex=settings.zone_alert_ttl,
    )
  if all(result.confluence_zone_id for result in claimed_results):
    # Opposing sides have distinct merged zone/setup identities. Detection
    # digest policy has already resolved whether both belong in this bar;
    # the card layer must not merge or silently discard either one.
    card_candidates = claimed_results
  else:
    card_candidates, _ = _suppress_overlaps(claimed_results)
  structural = [
    item for item in card_candidates if item.setup in STRUCTURAL_SETUPS
  ]
  cards = sorted(structural or card_candidates[:1], key=_result_rank)
  card_top_n = int(settings.scanner_card_top_n)
  if card_top_n > 0:
    cards = cards[:card_top_n]
  match_pool = list(execution_matches or [])
  if (
    execution_match is not None
    and all(item.match_id != execution_match.match_id for item in match_pool)
  ):
    match_pool.append(execution_match)
  match_ids_by_card: dict[int, str] = {}
  sent_results: list[DetectionResult] = []
  for index, result in enumerate(cards):
    also = card_candidates[1:] if not structural and index == 0 else []
    match_for_card = None
    if result.confluence_zone_id:
      match_for_card = next(
        (
          item for item in match_pool
          if item.confluence_zone_id == result.confluence_zone_id
          and item.direction == result.direction.upper()
        ),
        None,
      )
    if match_for_card is None and not result.confluence_zone_id:
      match_for_card = next(
        (
          item for item in match_pool
          if item.strategy == result.setup
          or (
            result.structural_id
            and item.structural_zone_id == result.structural_id
          )
        ),
        None,
      )
    if (
      match_for_card is None
      and not result.confluence_zone_id
      and index == 0
    ):
      match_for_card = execution_match
    # Non-negotiable Telegram requirement: a result without a resolvable
    # canonical StrategyMatch must never reach notify()/
    # post_or_edit_forming_card() - not even as a MARKET OBSERVATION/
    # ANALYSIS ONLY card.
    if match_for_card is None:
      log.info(
        "scanner card suppressed: no executable StrategyMatch "
        "symbol=%s tf=%s setup=%s direction=%s",
        symbol,
        tf,
        result.setup,
        result.direction,
      )
      continue
    text = _format_detection(
      symbol,
      tf,
      ctx,
      result,
      htf_order,
      also,
      market_map,
      match_for_card,
    )
    if not text:
      # A resolvable match with nothing card-worthy to show yet (e.g. the
      # ZoneWatch cutover deliberately renders "" for anything not yet
      # published - see _format_detection_cutover). Not the same as
      # "suppressed: no executable StrategyMatch" above; this candidate IS
      # tracked, it just has no card to send right now.
      continue
    # One forming card per setup (P4): re-detection of the same setup_id
    # edits its existing card instead of posting a new one, and a terminal
    # (rejected/invalidated/expired) setup is never re-carded - both
    # enforced inside post_or_edit_forming_card.
    await post_or_edit_forming_card(
      client,
      match_for_card.match_id,
      text,
      chat_id=settings.telegram_owner_id,
      send_fn=notify,
      edit_fn=edit or edit_scanner_message_text,
      delete_fn=delete_scanner_message,
    )
    match_ids_by_card[index] = match_for_card.match_id
    sent_results.append(result)
  await _track_active_setups(client, symbol, tf, cards, match_ids_by_card)
  # Only results that actually reached post_or_edit_forming_card count as
  # "sent" - the old `return cards` returned every card candidate
  # unconditionally, including ones the loop above explicitly skipped via
  # `continue` (no resolvable StrategyMatch). Downstream telemetry
  # (_record_status's `sent`, the detect_log's outcome="sent") took that at
  # face value, so a candidate that was never carded - never watched, never
  # published, never actually shown to the owner - still showed up logged
  # as delivered.
  return sent_results


async def _record_status(
  client: Any,
  *,
  symbol: str,
  tf: str,
  event_ts: str,
  frames: dict[str, Any],
  detected: list[DetectionResult],
  sent: list[DetectionResult],
  status: str,
  actionable: list[DetectionResult] | None = None,
  actionability_gated: list[
    tuple[DetectionResult, ActionabilityDecision]
  ] | None = None,
  market_map: MarketMap | None = None,
  scalp: dict[str, Any] | None = None,
  conflicts: list[dict[str, Any]] | None = None,
  structure_gated: list[tuple[DetectionResult, str]] | None = None,
  eligibility_gated: list[
    tuple[DetectionResult, str, dict[str, Any]]
  ] | None = None,
) -> None:
  map_counts = {
    "buys": len(market_map.buys) if market_map is not None else 0,
    "sells": len(market_map.sells) if market_map is not None else 0,
    "majors": len(market_map.majors) if market_map is not None else 0,
  }
  observed_payload = [
    {
      "setup": item.setup,
      "mode": item.mode,
      "direction": item.direction,
      "key_level": item.key_level,
      "entry_zone": {
        "low": item.entry_zone.low,
        "high": item.entry_zone.high,
        "score": getattr(item.entry_zone, "score", 0.0),
        "score_reasons": list(
          getattr(item.entry_zone, "score_reasons", []) or []
        ),
      },
      "current_price": item.current_price,
      "confluence": item.confluence,
      "confirmation": item.confirmation,
    }
    for item in detected
  ]
  actionable_results = detected if actionable is None else actionable
  payload = {
    "status": status,
    "symbol": symbol,
    "tf": tf,
    "event_ts": event_ts,
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "frames": {
      name: len(frame)
      for name, frame in sorted(frames.items())
    },
    # `detected` is the backward-compatible alias. New consumers should use
    # the explicit observation/actionability fields.
    "detected": observed_payload,
    "observed": observed_payload,
    "observed_count": len(detected),
    "actionable": [
      {
        "setup": item.setup,
        "direction": item.direction,
        "confluence": item.confluence,
        "target_cap_pips": item.target_cap_pips,
      }
      for item in actionable_results
    ],
    "actionable_count": len(actionable_results),
    "actionability_gated": [
      {
        "setup": item.setup,
        "direction": item.direction,
        "reason_code": decision.reason_code,
        "hard_block": decision.hard_block,
        "measured": decision.measured,
        "opposing_entry": (
          None
          if decision.opposing_entry is None
          else {
            "side": decision.opposing_entry.side,
            "low": decision.opposing_entry.lo,
            "high": decision.opposing_entry.hi,
            "tier": decision.opposing_entry.tier,
            "tags": list(decision.opposing_entry.tags),
          }
        ),
      }
      for item, decision in actionability_gated or []
    ],
    "conflicts": conflicts or [],
    "structure_gated": [
      {
        "setup": item.setup,
        "direction": item.direction,
        "reason": reason,
      }
      for item, reason in structure_gated or []
    ],
    "eligibility_gated": [
      {
        "setup": item.setup,
        "direction": item.direction,
        "reason": reason,
        "measured": measured,
      }
      for item, reason, measured in eligibility_gated or []
    ],
    "sent": len(sent),
    "map": map_counts,
    "map_summary": (
      f"map: buys={map_counts['buys']} sells={map_counts['sells']} "
      f"majors={map_counts['majors']}"
    ),
    "scalp": scalp or {
      "state": "unavailable",
      "barriers": 0,
      "supports": 0,
      "resistances": 0,
      "range": None,
    },
  }
  encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
  await client.set(
    "scanner:last_tick",
    encoded,
    ex=SCANNER_SNAPSHOT_TTL_SECONDS,
  )
  await client.set(
    f"scanner:last_tick:{symbol}:{tf}",
    encoded,
    ex=SCANNER_SNAPSHOT_TTL_SECONDS,
  )


# --- B5: per-detector reporting ---------------------------------------------
# scanner.py already builds `detected` on every scan (line ~723) but nothing
# reads it historically - `scanner:last_tick*` is overwrite-only, holding
# only the single latest snapshot. This appends a bounded, queryable history
# so the BOX_* tuning and regime-router-exclusivity questions (out of scope
# for this PR) have data to work from before anyone touches those constants.
_DETECT_LOG_MAXLEN = 5000
_DETECT_LOG_TTL_SECONDS = 8 * 24 * 3600


def _detect_log_key(symbol: str, tf: str) -> str:
  return f"scanner:detect_log:{symbol.upper()}:{tf.upper()}"


def _telemetry_result_key(result: DetectionResult) -> tuple[Any, ...]:
  return (
    result.setup,
    result.direction,
    result.structural_id,
    result.confluence_zone_id,
    round(float(result.entry_zone.low), 8),
    round(float(result.entry_zone.high), 8),
  )


async def _append_detect_log(
  client: Any,
  symbol: str,
  tf: str,
  detected: list[DetectionResult],
  sent: list[DetectionResult],
  conflicts: list[dict[str, Any]],
  structure_gated: list[tuple[DetectionResult, str]] | None = None,
  eligibility_gated: list[
    tuple[DetectionResult, str, dict[str, Any]]
  ] | None = None,
  actionability_gated: list[
    tuple[DetectionResult, ActionabilityDecision]
  ] | None = None,
) -> None:
  if (
    not detected
    and not structure_gated
    and not eligibility_gated
    and not actionability_gated
  ):
    return
  sent_keys = {(item.setup, item.direction) for item in sent}
  conflict_keys = {
    (side["setup"], side["direction"])
    for record in conflicts
    for side in (record["a"], record["b"])
  }
  entries = []
  gated_reasons = {
    _telemetry_result_key(item): reason
    for item, reason in structure_gated or []
  }
  eligibility_reasons = {
    _telemetry_result_key(item): reason
    for item, reason, _measured in eligibility_gated or []
  }
  actionability_decisions = {
    _telemetry_result_key(item): decision
    for item, decision in actionability_gated or []
  }
  detected_keys = {_telemetry_result_key(item) for item in detected}
  logged_results = [
    *detected,
    *[item for item, _ in structure_gated or []],
    *[
      item for item, _reason, _measured in eligibility_gated or []
      if _telemetry_result_key(item) not in detected_keys
    ],
    *[
      item for item, _decision in actionability_gated or []
      if _telemetry_result_key(item) not in detected_keys
    ],
  ]
  for item in logged_results:
    detection_key = (item.setup, item.direction)
    telemetry_key = _telemetry_result_key(item)
    actionability = actionability_decisions.get(telemetry_key)
    if actionability is not None and actionability.hard_block:
      outcome = "actionability_gated"
    elif telemetry_key in eligibility_reasons:
      outcome = eligibility_reasons[telemetry_key]
    elif telemetry_key in gated_reasons:
      outcome = "structure_gated"
    elif detection_key in sent_keys:
      outcome = "sent"
    elif actionability is not None:
      outcome = "actionability_observed"
    elif detection_key in conflict_keys:
      outcome = "dropped_conflict"
    else:
      outcome = "suppressed_duplicate"
    entry = {
      "setup": item.setup,
      "direction": item.direction,
      "confluence": item.confluence,
      "outcome": outcome,
    }
    if actionability is not None:
      entry["reason"] = actionability.reason_code
      entry["measured"] = actionability.measured
    elif telemetry_key in eligibility_reasons:
      entry["reason"] = eligibility_reasons[telemetry_key]
    elif telemetry_key in gated_reasons:
      entry["reason"] = gated_reasons[telemetry_key]
    entries.append(entry)
  record = json.dumps({
    "recorded_at": datetime.now(timezone.utc).timestamp(),
    "entries": entries,
  }, separators=(",", ":"))
  key = _detect_log_key(symbol, tf)
  await client.lpush(key, record)
  await client.ltrim(key, 0, _DETECT_LOG_MAXLEN - 1)
  await client.expire(key, _DETECT_LOG_TTL_SECONDS)


async def scan_report(
  client: Any,
  symbol: str,
  tf: str,
  hours: float = 24.0,
) -> dict[str, dict[str, float]]:
  """Aggregate the last ``hours`` of detections into a per-detector table:
  fire count, mean confluence, times sent (~ranked first and delivered),
  times suppressed as a same-direction duplicate, times dropped as an
  opposite-direction conflict. Read-only - does not tune any BOX_* constant.
  """
  cutoff = datetime.now(timezone.utc).timestamp() - max(0.0, hours) * 3600
  raw = await client.lrange(_detect_log_key(symbol, tf), 0, _DETECT_LOG_MAXLEN - 1)
  totals: dict[str, dict[str, float]] = {}
  for item in raw:
    try:
      record = json.loads(item)
    except (TypeError, json.JSONDecodeError):
      continue
    if float(record.get("recorded_at", 0.0)) < cutoff:
      continue
    for entry in record.get("entries", []):
      setup = str(entry.get("setup", "unknown"))
      row = totals.setdefault(setup, {
        "fires": 0.0,
        "confluence_sum": 0.0,
        "sent": 0.0,
        "suppressed_duplicate": 0.0,
        "dropped_conflict": 0.0,
        "structure_gated": 0.0,
      })
      row["fires"] += 1
      row["confluence_sum"] += float(entry.get("confluence", 0))
      outcome = entry.get("outcome")
      if outcome in row:
        row[outcome] += 1
  return {
    setup: {
      "fires": row["fires"],
      "mean_confluence": row["confluence_sum"] / row["fires"] if row["fires"] else 0.0,
      "sent": row["sent"],
      "suppressed_duplicate": row["suppressed_duplicate"],
      "dropped_conflict": row["dropped_conflict"],
      "structure_gated": row["structure_gated"],
    }
    for setup, row in totals.items()
  }


def format_scan_report(
  rows: dict[str, dict[str, float]],
  symbol: str,
  tf: str,
  hours: float,
) -> str:
  if not rows:
    return (
      f"📊 <b>Scan report · {escape(symbol)} {escape(tf)} · "
      f"{hours:.0f}h</b>\nNo detections recorded in this window."
    )
  lines = [f"📊 <b>Scan report · {escape(symbol)} {escape(tf)} · {hours:.0f}h</b>", ""]
  for setup, row in sorted(rows.items(), key=lambda item: -item[1]["fires"]):
    lines.append(
      f"<b>{escape(setup)}</b> · fires {int(row['fires'])} · "
      f"avg {row['mean_confluence']:.1f}★ · sent {int(row['sent'])} · "
      f"dup {int(row['suppressed_duplicate'])} · "
      f"conflict {int(row['dropped_conflict'])} · "
      f"gated {int(row['structure_gated'])}"
    )
  return "\n".join(lines)


def _scalp_status(ctx: DetectionContext) -> dict[str, Any]:
  st = ctx.structures.get(ctx.tf)
  if st is None:
    return {
      "state": "missing_structure",
      "barriers": 0,
      "supports": 0,
      "resistances": 0,
      "range": None,
      "range_state": "no_range",
      "fallback_barriers": 0,
      "missing_side_reason": "missing_structure",
    }
  barriers = list(st.scalp_barriers)
  scalp_range = st.scalp_range
  enabled = ctx.settings.range_scalp_enabled
  range_state = getattr(scalp_range, "state", None) if scalp_range else "no_range"
  state = "disabled" if not enabled else (range_state or "no_range")
  range_payload = None
  if scalp_range is not None:
    frame = ctx.frames.get(ctx.tf)
    touched = []
    if frame is not None and not frame.empty:
      row = frame.iloc[-1]
      if float(row["low"]) <= scalp_range.lower.high:
        touched.append("lower")
      if float(row["high"]) >= scalp_range.upper.low:
        touched.append("upper")
    if enabled and range_state in {
      "confirmed_range", "provisional_range", "post_impulse_range",
    }:
      state = "edge_touch" if touched else "waiting_edge"
    range_payload = {
      "lower": scalp_range.lower.level,
      "upper": scalp_range.upper.level,
      "eq": scalp_range.eq,
      "width_atr": scalp_range.width_atr,
      "quality": scalp_range.quality,
      "touched": touched,
      "state": range_state,
      "one_sided": bool(getattr(scalp_range, "one_sided", False)),
      "post_impulse": bool(getattr(scalp_range, "post_impulse", False)),
    }
  supports = [b for b in barriers if b.side == "support"]
  resistances = [b for b in barriers if b.side == "resistance"]
  missing = None
  if resistances and not supports:
    missing = "no_support"
  elif supports and not resistances:
    missing = "no_resistance"
  elif not supports and not resistances:
    missing = "no_barriers"
  return {
    "state": state,
    "barriers": len(barriers),
    "supports": len(supports),
    "resistances": len(resistances),
    "range": range_payload,
    "range_state": range_state or "no_range",
    "fallback_barriers": sum(1 for b in barriers if getattr(b, "fallback", False)),
    "missing_side_reason": missing,
  }


async def _load_market_context_for_symbol(
  symbol: str,
  *,
  source: RedisOHLCSource | None = None,
  client: Any | None = None,
  event_ts: str | None = None,
  exec_tf: str | None = None,
  htf_order: list[str] | None = None,
  cache_market_analysis: bool = True,
  window: int | None = None,
) -> tuple[DetectionContext | None, dict[str, Any]]:
  symbol = symbol.upper()
  client = client or redis_state.get_client()
  source = source or RedisOHLCSource(client)
  exec_tf = (exec_tf or settings.scanner_exec_tf).upper()
  htf_order = htf_order or _htf_tfs()
  spot = await _load_spot_snapshot(client, symbol)
  frames = await _load_frames(
    source,
    symbol,
    exec_tf,
    htf_order,
    window=window,
  )
  if exec_tf not in frames:
    return None, frames
  trigger = event_ts or str(frames[exec_tf].index[-1])
  ctx = build_context(
    symbol,
    exec_tf,
    frames,
    _detector_settings(),
    htf_order,
  )
  ctx = _attach_price_context(ctx, spot, trigger, frames[exec_tf])
  analysis = getattr(ctx, "analysis", None)
  if analysis is not None and cache_market_analysis:
    price = (
      float(ctx.spot_price)
      if getattr(ctx, "spot_price", None) is not None
      else float(frames[exec_tf]["close"].iloc[-1])
    )
    cache_analysis(symbol, analysis, price, frames[exec_tf].index[-1])
  return ctx, frames


async def _handle_event(
  data: object,
  *,
  source: RedisOHLCSource | None = None,
  client: Any | None = None,
  detectors: Iterable[SetupDetector] | None = None,
  notify: NotifyFn | None = None,
  edit: NotifyFn | None = None,
) -> list[DetectionResult]:
  parsed = _parse_bar_event(data)
  if parsed is None:
    return []
  symbol, tf, event_ts = parsed
  exec_tf = settings.scanner_exec_tf.upper()
  if symbol not in _watched_symbols():
    return []

  if tf != exec_tf:
    return []

  client = client or redis_state.get_client()
  notify = notify or send_scanner_with_retry
  edit = edit or edit_scanner_message_text
  htf_order = _htf_tfs()
  ctx, frames = await _load_market_context_for_symbol(
    symbol,
    source=source,
    client=client,
    event_ts=event_ts,
  )
  if ctx is None:
    await persist_scanner_range_observation(
      client,
      symbol=symbol,
      context=None,
    )
    await client.set(
      f"auto_trade:range_source_status:scanner:{symbol.upper()}",
      json.dumps({
        "state": "data_gap",
        "reason": "missing_exec_frame",
        "event_ts": event_ts,
        "checked_at": datetime.now(timezone.utc).isoformat(),
      }, separators=(",", ":"), sort_keys=True),
      ex=SCANNER_SOURCE_MAX_AGE_SECONDS,
    )
    await _record_status(
      client,
      symbol=symbol,
      tf=exec_tf,
      event_ts=event_ts,
      frames=frames,
      detected=[],
      sent=[],
      status="missing_exec_frame",
    )
    return []

  exec_indicators = getattr(ctx, "indicators", {}).get(exec_tf)
  invalidation_atr = (
    float(exec_indicators.atr.iloc[-1])
    if exec_indicators is not None and not exec_indicators.atr.empty
    else 0.0
  )
  if not math.isfinite(invalidation_atr):
    invalidation_atr = 0.0
  await _check_setup_invalidations(
    client, symbol, exec_tf, frames[exec_tf], notify, invalidation_atr,
  )

  analysis = getattr(ctx, "analysis", None)
  current_map = None
  if analysis is not None:
    price = (
      float(ctx.spot_price)
      if getattr(ctx, "spot_price", None) is not None
      else float(frames[exec_tf]["close"].iloc[-1])
    )
    current_map = build_map(analysis, price, settings)
    map_payload = market_map_payload(current_map)
    map_ttl = max(
      900,
      int(settings.auto_trade_strategy_match_max_age_seconds) * 2,
    )
    await client.set(
      market_map_key(symbol),
      map_payload,
      ex=map_ttl,
    )
    # Strategy and Telegram must share the same map_id snapshot.
    await client.set(
      market_map_display_key(symbol),
      map_payload,
      ex=map_ttl,
    )
    reconciled = sum(
      1 for entry in current_map.entries
      if any(tag.startswith(ZONE_RECONCILED_TAG_PREFIX) for tag in entry.tags)
    )
    if reconciled:
      await client.incrby(f"auto_trade:zone_reconciled:{symbol.upper()}", reconciled)
    exec_analysis = analysis.per_tf.get(exec_tf.upper())
    if exec_analysis is not None:
      await client.hset(
        f"auto_trade:zone_reconcile:{symbol.upper()}",
        mapping={
          "mode": settings.auto_trade_zone_reconcile_mode,
          "zones_input": getattr(
            exec_analysis, "zone_reconcile_input", 0,
          ),
          "zones_shadow_output": (
            getattr(exec_analysis, "zone_reconcile_shadow_output", 0)
          ),
          "zones_trimmed": getattr(
            exec_analysis, "zone_reconcile_trimmed", 0,
          ),
          "zones_dropped": exec_analysis.zone_reconcile_dropped,
          "reconcile_aborted": int(
            exec_analysis.zone_reconcile_aborted
          ),
          "candidate_difference_count": (
            getattr(
              exec_analysis,
              "zone_reconcile_candidate_difference_count",
              0,
            )
          ),
          "updated_at": int(datetime.now(timezone.utc).timestamp()),
        },
      )
      if exec_analysis.zone_reconcile_dropped:
        await client.incrby(
          f"auto_trade:zone_dropped:{symbol.upper()}",
          exec_analysis.zone_reconcile_dropped,
        )
      if exec_analysis.zone_reconcile_aborted:
        await client.incr(f"auto_trade:zone_reconcile_aborted:{symbol.upper()}")
      if exec_analysis.regime is not None:
        regime = exec_analysis.regime
        await client.hincrby(
          f"auto_trade:regime_compare:{symbol.upper()}",
          f"{regime.legacy_kind}:{regime.new_kind}",
          1,
        )
        if regime.new_kind != regime.legacy_kind:
          lookback = int(
            getattr(settings, "auto_trade_regime_direction_lookback", 120)
          )
          log.debug(
            "regime: legacy=%s new=%s (%s) height=%.2fATR lookback=%s",
            regime.legacy_kind,
            regime.new_kind,
            regime.directional_detail or "directional override",
            regime.height_atr,
            lookback,
          )
  raw_detector_results = []
  structure_gated: list[tuple[DetectionResult, str]] = []
  for detector in detectors or DEFAULT_DETECTORS:
    result = detector(ctx)
    if result is None:
      continue
    metric_name = {
      "Key Level Reaction": "key_level_reaction_detected",
      "Demand Zone Reaction": "demand_zone_reaction_detected",
      "Supply Zone Reaction": "supply_zone_reaction_detected",
      "Session Level Reaction": "session_level_reaction_detected",
      "Trendline Reaction": "trendline_reaction_detected",
    }.get(result.setup)
    if metric_name:
      await increment_metric(client, metric_name, symbol=symbol)
    gate_reason = _structure_card_gate(result, ctx)
    if gate_reason is not None:
      structure_gated.append((result, gate_reason))
      await increment_metric(client, "structure_gated", symbol=symbol)
      log.info(
        "scanner result structure-gated symbol=%s tf=%s setup=%s "
        "direction=%s reason=%s",
        symbol,
        exec_tf,
        result.setup,
        result.direction,
        gate_reason,
      )
      continue
    raw_detector_results.append(result)
  observed_results = _merge_detection_confluence(
    symbol,
    exec_tf,
    raw_detector_results,
    atr=invalidation_atr,
  )
  observed_results = [
    _annotate_actionability_geometry(
      symbol,
      exec_tf,
      event_ts,
      ctx,
      result,
    )
    for result in observed_results
  ]
  actionability = resolve_actionability(
    symbol=symbol,
    observed_results=observed_results,
    market_map=current_map,
    context=ctx,
    atr=invalidation_atr,
    pip_size=_pip_size(symbol),
    cfg=settings,
  )
  actionable_results = list(actionability.actionable)
  actionability_decisions = list(actionability.decisions)
  public_results_by_key = {
    _telemetry_result_key(result): result for result in observed_results
  }
  displayed_by_key = {
    _telemetry_result_key(result): result for result in observed_results
  }
  for result, decision in actionability_decisions:
    await increment_metric(
      client,
      (
        "scanner_actionability_gated"
        if decision.hard_block else "scanner_actionability_observed"
      ),
      symbol=symbol,
    )
    await increment_metric(client, decision.reason_code, symbol=symbol)
    log.info(
      "scanner result actionability-%s symbol=%s tf=%s setup=%s "
      "direction=%s reason=%s measured=%s",
      "gated" if decision.hard_block else "observed",
      symbol,
      exec_tf,
      result.setup,
      result.direction,
      decision.reason_code,
      decision.measured,
    )
    if decision.hard_block:
      blocked = replace(
        result,
        execution_eligibility=_static_execution_eligibility(
          result,
          current_map,
          allowed=False,
          reason_code=decision.reason_code,
          message=decision.message,
          measured=decision.measured,
          hard_block=True,
        ),
      )
      displayed_by_key[_telemetry_result_key(result)] = blocked
  eligibility_gated: list[
    tuple[DetectionResult, str, dict[str, Any]]
  ] = []
  reward_risk_eligible_results = []
  for result in actionable_results:
    eligible, measured = _reward_risk_pre_gate(
      symbol,
      exec_tf,
      event_ts,
      ctx,
      result,
    )
    build_observation = measured.get("match_build_observation")
    if build_observation:
      blocked = replace(
        result,
        execution_eligibility=_static_execution_eligibility(
          result,
          current_map,
          allowed=False,
          reason_code=str(build_observation),
          message="analysis context cannot construct an executable match",
          measured=measured,
          hard_block=True,
        ),
      )
      # Keep observations in the normal digest so card capping/dedup remains
      # unchanged. _sync_strategy_match filters on eligibility.allowed.
      reward_risk_eligible_results.append(blocked)
      displayed_by_key[_telemetry_result_key(result)] = blocked
      await increment_metric(
        client,
        "static_eligibility_blocked",
        symbol=symbol,
        dimensions={"reason": str(build_observation)},
      )
      continue
    if eligible:
      ready = replace(
        result,
        execution_eligibility=_static_execution_eligibility(
          result,
          current_map,
          allowed=True,
          reason_code="static_eligibility_passed",
          message="scanner static execution eligibility passed",
          measured=measured,
          hard_block=False,
        ),
      )
      reward_risk_eligible_results.append(ready)
      displayed_by_key[_telemetry_result_key(result)] = ready
      continue
    reason = str(
      measured.get("static_rejection_reason")
      or measured.get("policy_reason_code")
      or (
        "opposing_barrier_rr_insufficient"
        if result.target_cap_pips is not None
        else "rr_pre_gate"
      )
    )
    eligibility_gated.append((result, reason, measured))
    displayed_by_key[_telemetry_result_key(result)] = replace(
      result,
      execution_eligibility=_static_execution_eligibility(
        result,
        current_map,
        allowed=False,
        reason_code=reason,
        message=(
          "setup is statically analysis-only"
          if measured.get("static_rejection_reason")
          else str(measured.get("policy_message"))
          if measured.get("policy_reason_code")
          else "provisional reward/risk is below the strategy minimum"
        ),
        measured=measured,
        hard_block=bool(measured.get("policy_hard_block", True)),
      ),
    )
    await increment_metric(
      client, "static_eligibility_blocked", symbol=symbol,
    )
    await increment_metric(client, reason, symbol=symbol)
    log.info(
      "scanner result eligibility-gated symbol=%s tf=%s setup=%s "
      "direction=%s reason=%s reward_risk=%s minimum=%s",
      symbol,
      exec_tf,
      result.setup,
      result.direction,
      reason,
      measured.get("reward_risk"),
      measured.get("min_reward_risk"),
    )
  for _result, decision in actionability.gated:
    await increment_metric(
      client,
      "static_eligibility_blocked",
      symbol=symbol,
      dimensions={"reason": decision.reason_code},
    )
  observed_results = [
    displayed_by_key.get(_telemetry_result_key(result), result)
    for result in observed_results
  ]
  digest, digest_conflicts = _digest_results(
    reward_risk_eligible_results,
  )
  conflicts = [*actionability.conflicts, *digest_conflicts]
  execution_match = await _sync_strategy_match(
    client,
    symbol,
    exec_tf,
    event_ts,
    ctx,
    digest,
    require_static_eligibility=True,
  )
  execution_matches = (
    deserialize_matches(await client.get(strategy_matches_key(symbol)))
    if execution_match is not None
    else []
  )
  # Non-negotiable Telegram requirement: an observation with no executable
  # StrategyMatch must never reach Telegram, in any form. There used to be a
  # `notification_results = digest or analysis_only_results` fallback here
  # that substituted hard-blocked/gated results (ANALYSIS ONLY / MARKET
  # OBSERVATION cards) whenever `digest` was empty - deleted, not
  # weakened. Analysis-only observations remain fully visible in
  # telemetry/scan reports/metrics via observed_results/actionability
  # below; they are simply never candidates for a Telegram send.
  sent = await _notify_digest_once(
    client,
    symbol,
    exec_tf,
    ctx,
    digest,
    notify,
    htf_order,
    market_map=current_map,
    execution_match=execution_match,
    edit=edit,
    execution_matches=execution_matches,
  )
  public_sent = [
    public_results_by_key.get(_telemetry_result_key(result), result)
    for result in sent
  ]
  await _record_status(
    client,
    symbol=symbol,
    tf=exec_tf,
    event_ts=event_ts,
    frames=frames,
    detected=observed_results,
    sent=public_sent,
    status="ok",
    actionable=actionable_results,
    actionability_gated=actionability_decisions,
    market_map=current_map,
    scalp=_scalp_status(ctx),
    conflicts=conflicts,
    structure_gated=structure_gated,
    eligibility_gated=eligibility_gated,
  )
  await _append_detect_log(
    client,
    symbol,
    exec_tf,
    observed_results,
    public_sent,
    conflicts,
    structure_gated,
    eligibility_gated,
    actionability_decisions,
  )
  return public_sent


async def scanner_loop() -> None:
  """Subscribe to closed-bar events and analyze scanner detections."""
  if not settings.scanner_enabled:
    log.info("Price-action scanner disabled: SCANNER_ENABLED=false")
    return
  if not settings.telegram_owner_id:
    log.info(
      "Price-action scanner notifications disabled: TELEGRAM_OWNER_ID not set"
    )

  client = redis_state.get_client()
  source = RedisOHLCSource(client)
  pubsub = client.pubsub()
  await pubsub.subscribe("bars:new")
  log.info(
    "Price-action scanner watching %s on %s (%s)",
    ",".join(sorted(_watched_symbols())),
    settings.scanner_exec_tf.upper(),
    "owner DM enabled" if settings.telegram_owner_id else "analysis only",
  )
  try:
    async for message in pubsub.listen():
      if message.get("type") != "message":
        continue
      try:
        await _handle_event(message.get("data"), source=source, client=client)
      except Exception:
        log.exception("scanner tick failed")
        try:
          await increment_metric(client, "lifecycle_error")
        except Exception:
          log.exception("scanner lifecycle_error metric failed")
  finally:
    await pubsub.unsubscribe("bars:new")
    await pubsub.close()
