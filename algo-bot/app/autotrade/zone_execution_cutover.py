"""Live ZoneWatch -> executable signal cutover.

This module is deliberately installed once from ``app.main`` after scanner and
worker imports are complete.  It replaces the old scanner handoff at the
function boundary without duplicating detector logic:

  detection result -> retained ZoneWatch (no setup, card, or ready event)
  -> side-aware quote enters zone
  -> activation evaluates location + fresh episode-scoped M1 for reaction families
  -> setup lifecycle begins only for the currently executable signal

The old ready stream remains an emergency durable fallback only when an
unexpected direct-publication exception occurs after setup creation.

Architecture follow-up (out of scope for the Minimal worker DI pass): this
cutover still lazy-imports private ``app.analysis.scanner`` helpers for match
persist/sync and monkeypatches ``worker.try_publish_executable_signal``. Peel
those scanner couplings into an injected publication / match-sync seam later;
do not reintroduce scanner imports into ``worker.py``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
import asyncio
import json
import logging
import math
import time
from typing import Any

from app.analysis.actionability import range_bounds_from_context
from app.analysis.confluence_zone import (
  BandKind,
  classify_band_kind,
  confluence_zone_id,
  validate_zone_width,
)
from app.analysis.entry_location import (
  build_entry_location_context,
  evaluate_entry_location,
)
from app.analysis.m1_trigger import evaluate_m1_trigger_window, latest_eligible_m1_bar_ts
from app.analysis.ohlc_source import RedisOHLCSource, window_for_timeframe
from app.autotrade.entry_activation import (
  apply_trigger_to_match,
  evaluate_entry_activation,
)
from app.autotrade.execution_confirmation import (
  executable_quote_in_zone,
  scalp_maximum_chase_pips,
  scalp_zone_access,
)
from app.autotrade.lifecycle import increment_metric
from app.autotrade.multi_match import dedupe_matches, serialize_matches, strategy_matches_key
from app.autotrade.strategy_match import StrategyMatch, strategy_match_key
from app.autotrade.strategy_match_ready import enqueue_strategy_match_ready
from app.autotrade.strategy_taxonomy import (
  is_range_strategy,
  is_reaction_strategy,
  is_scalp_strategy,
  is_technique_or_confluence,
)
from app.core import instrument_geometry
from app.runtime.instrument_config import instrument_runtime_view
from app.autotrade.zone_watch import (
  DISCOVERED,
  EXPIRED,
  GRADE_A,
  GRADE_B,
  INVALIDATED,
  LOCKED_ZONE_WATCH_STATES,
  PUBLISHED_LOCKED,
  TERMINAL_ZONE_WATCH_STATES,
  WATCHING_RETEST,
  ZoneWatch,
  discover_zone_watch,
  list_active_zone_watches,
  load_zone_watch,
  lock_zone_watch_published,
  mark_m1_evaluated,
  record_zone_presence,
  transition_zone_watch,
)
from app.core.config import runtime_config
from app.persistence import redis_state


log = logging.getLogger(__name__)

_CANDIDATE_KEY_PREFIX = "analysis:zone_watch_candidate"
_LOCATION_RANGES_KEY_PREFIX = "analysis:zone_watch_location_ranges"
_WIDTH_TELEMETRY_KEY_PREFIX = "analysis:zone_width:last"
_LAST_LOCATION_KEY_PREFIX = "auto_trade:last_entry_location"
_LAST_ACTIVATION_KEY_PREFIX = "auto_trade:last_entry_activation"
_CANDIDATE_TTL_SECONDS = 7 * 24 * 3600
_AUTHORITATIVE_REACTIONS = {
  "rejection_choch",
  "sweep_reclaim",
  "strong_reclaim",
  "wick_rejection",
  "rejection",
  "reclaim",
  "engulfing",
  "body_close",
  "strong_close",
  "pin_bar",
  "hammer",
}
_PUBLISHED_SETUP_IDS: set[str] = set()
_INSTALLED = False
_ORIGINAL_SYNC: Any = None
_ORIGINAL_FORMAT: Any = None
_ORIGINAL_DIRECT_PUBLISH: Any = None

# Hard stop geometry failures: never activate / thrash-retry the same zone.
# ``stop_exceeds_envelope_furthest_leg`` is not always listed in builder
# ``known_stop_errors`` (maps to protective_stop_unavailable) but must still
# pre-block here from eligibility.measured.planned_stop_error.
_STOP_HARD_ERRORS = frozenset({
  "stop_exceeds_envelope_after_wick",
  "stop_exceeds_envelope_furthest_leg",
  "stop_exceeds_max_envelope",
  "stop_inside_opposing_zone",
  "stop_inside_entry_zone",
  "stop_not_beyond_planned_entries",
  "protective_stop_unavailable",
})


def candidate_key(zone_id: str) -> str:
  return f"{_CANDIDATE_KEY_PREFIX}:{zone_id}"


def location_ranges_key(zone_id: str) -> str:
  return f"{_LOCATION_RANGES_KEY_PREFIX}:{zone_id}"


def _ranges_usable(ranges: dict[str, float | None] | None) -> bool:
  if not ranges:
    return False
  for prefix in ("m15", "h1", "m5"):
    low = ranges.get(f"{prefix}_range_low")
    high = ranges.get(f"{prefix}_range_high")
    if low is None or high is None:
      continue
    try:
      if float(high) > float(low) > 0:
        return True
    except (TypeError, ValueError):
      continue
  return False


def _empty_range_bounds() -> dict[str, float | None]:
  return {
    "m15_range_low": None,
    "m15_range_high": None,
    "h1_range_low": None,
    "h1_range_high": None,
    "m5_range_low": None,
    "m5_range_high": None,
  }


async def _save_location_ranges(
  client: Any,
  zone_id: str,
  ranges: dict[str, float | None],
) -> None:
  if not _ranges_usable(ranges):
    return
  await client.set(
    location_ranges_key(zone_id),
    json.dumps(ranges, separators=(",", ":"), sort_keys=True),
    ex=_CANDIDATE_TTL_SECONDS,
  )


async def _load_location_ranges(
  client: Any,
  zone_id: str,
) -> dict[str, float | None]:
  raw = await client.get(location_ranges_key(zone_id))
  if raw is None:
    return _empty_range_bounds()
  try:
    payload = json.loads(raw)
  except (TypeError, ValueError, json.JSONDecodeError):
    return _empty_range_bounds()
  if not isinstance(payload, dict):
    return _empty_range_bounds()
  out = _empty_range_bounds()
  for key in out:
    value = payload.get(key)
    if value is None:
      continue
    try:
      out[key] = float(value)
    except (TypeError, ValueError):
      continue
  return out


async def _resolve_location_range_bounds(
  client: Any,
  *,
  symbol: str,
  zone_id: str,
  analysis_context: Any | None = None,
) -> dict[str, float | None]:
  """Resolve dealing-range bounds the same way discovery does.

  Preference order:
  1. Live analysis / detection context passed by the scanner sync path
  2. In-process market-map analysis cache
  3. Snapshot written when the ZoneWatch candidate was saved
  4. Fresh market-context load (M1 path when cache is cold)

  Never use StrategyMatch.range_low/high — those are scalp box edges.
  """
  if analysis_context is not None:
    ranges = range_bounds_from_context(analysis_context)
    if _ranges_usable(ranges):
      return ranges

  try:
    from app.analysis.market_map_delivery import get_cached_analysis

    cached = get_cached_analysis(symbol)
  except Exception:
    cached = None
  if cached is not None and getattr(cached, "analysis", None) is not None:
    ranges = range_bounds_from_context(
      SimpleNamespace(analysis=cached.analysis),
    )
    if _ranges_usable(ranges):
      return ranges

  snap = await _load_location_ranges(client, zone_id)
  if _ranges_usable(snap):
    return snap

  try:
    from app.analysis.scanner import _load_market_context_for_symbol

    ctx, _frames = await _load_market_context_for_symbol(symbol)
  except Exception:
    log.exception(
      "entry_location_range_reload_failed symbol=%s zone_id=%s",
      symbol,
      zone_id,
    )
    return _empty_range_bounds()
  if ctx is None:
    return _empty_range_bounds()
  return range_bounds_from_context(ctx)


def width_telemetry_key(symbol: str, zone_id: str) -> str:
  return f"{_WIDTH_TELEMETRY_KEY_PREFIX}:{symbol.upper()}:{zone_id}"


def _now() -> int:
  return int(datetime.now(timezone.utc).timestamp())


def _is_stop_hard_error(code: str | None) -> bool:
  text = str(code or "").strip()
  if not text:
    return False
  if text in _STOP_HARD_ERRORS:
    return True
  if text.startswith("stop_exceeds_envelope"):
    return True
  # Publish path prefixes builder rejects: v8_protective_stop_unavailable,
  # v8_stop_inside_opposing_zone, v8_stop_exceeds_max_envelope, ...
  if text.startswith("v8_"):
    return _is_stop_hard_error(text[3:])
  return False


def _match_planned_stop_hard_error(match: StrategyMatch) -> str | None:
  """Return a durable stop geometry error already known on the candidate.

  Activation must not start (and thrash) when scanner eligibility already
  measured an unplannable protective stop — including the prod case where
  ``allowed`` looked true while ``measured.planned_stop_error`` was set.
  """
  eligibility = getattr(match, "execution_eligibility", None)
  if eligibility is None:
    return None
  measured = getattr(eligibility, "measured", None) or {}
  for raw in (
    measured.get("planned_stop_error"),
    getattr(eligibility, "reason_code", None),
  ):
    code = str(raw or "").strip()
    if _is_stop_hard_error(code):
      return code
  return None


def _publish_should_terminalize_zone_watch(
  *,
  status: str,
  reason_code: str | None,
) -> bool:
  """True when a failed publish must stop ZoneWatch re-activation thrash."""
  if status == "invalidated":
    return True
  return _is_stop_hard_error(reason_code)


async def _terminalize_zone_watch(
  client: Any,
  zone_id: str,
  *,
  reason_code: str,
) -> None:
  latest = await load_zone_watch(client, zone_id)
  if latest is None or latest.state in TERMINAL_ZONE_WATCH_STATES:
    return
  if latest.state in LOCKED_ZONE_WATCH_STATES:
    return
  try:
    await transition_zone_watch(
      client,
      zone_id,
      INVALIDATED,
      reason_code=reason_code,
    )
  except Exception:
    log.exception(
      "could not terminalize zone watch zone_id=%s reason=%s",
      zone_id,
      reason_code,
    )


def _zone_id(symbol: str, tf: str, result: Any, atr: float, pip_size: float) -> str:
  existing = str(
    getattr(result, "confluence_zone_id", None)
    or getattr(result, "structural_id", None)
    or ""
  ).strip()
  if existing:
    return existing
  tags = tuple(getattr(result, "confluence_tags", None) or ())
  if not tags:
    tags = (str(getattr(result, "structural_source", None) or result.setup),)
  return confluence_zone_id(
    symbol,
    "buy" if str(result.direction).upper() == "BUY" else "sell",
    float(result.entry_zone.low),
    float(result.entry_zone.high),
    tags,
    atr=max(float(atr), pip_size),
    pip_size=pip_size,
  )


def _grade(result: Any, source_tf: str) -> str:
  """Small explainable A/B policy; C never enters the active watchlist."""
  if getattr(result, "execution_eligibility", None) is not None:
    if not bool(result.execution_eligibility.allowed):
      return "C"
  setup = str(getattr(result, "setup", ""))
  if setup == "Range Edge Scalp":
    return GRADE_B
  tags = set(str(item).casefold() for item in getattr(result, "confluence_tags", ()) or ())
  reaction = str(
    getattr(result, "confirmation_type", None)
    or getattr(result, "confirmation", None)
    or ""
  ).casefold().replace(" ", "_")
  confluence = int(getattr(result, "confluence", 0) or 0)
  htf = source_tf.upper() in {"H1", "M15"}
  multi_source = len(tags) >= 2
  authoritative = reaction in _AUTHORITATIVE_REACTIONS
  if htf or confluence >= 3 or multi_source or (authoritative and confluence >= 2):
    return GRADE_A
  return GRADE_B if confluence >= 2 or authoritative else "C"


async def _load_quote(client: Any, symbol: str) -> tuple[float, float, int] | None:
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
  if not all(math.isfinite(value) and value > 0 for value in (bid, ask)):
    return None
  if _now() - ts > max(0, int(runtime_config.market_data.spot.fresh_secs)):
    return None
  return bid, ask, ts


def _quote_evidence(
  record: ZoneWatch,
  quote: tuple[float, float, int],
):
  from app.autotrade import units

  bid, ask, _ts = quote
  pip = units.pip_size(record.symbol)
  tolerance = max(
    0.0,
    float(runtime_config.execution.entry.contract_tolerance_pips) * pip,
  )
  return executable_quote_in_zone(
    record.direction,
    bid,
    ask,
    record.low,
    record.high,
    tolerance,
    pip_size=pip,
  )


def _technique_chase_pips() -> float:
  """Chase budget for technique / confluence ZoneWatch activation."""
  try:
    value = float(runtime_config.execution.entry.maximum_chase_distance_pips)
  except (TypeError, ValueError, AttributeError):
    return 40.0
  return max(0.0, value)


def _scalp_access(
  record: ZoneWatch,
  quote: tuple[float, float, int],
  *,
  strategy: str | None = None,
):
  """Inside / chase access for range scalp + techniques; strict inside otherwise."""
  from app.autotrade import units

  bid, ask, _ts = quote
  pip = units.pip_size(record.symbol)
  tolerance = max(
    0.0,
    float(runtime_config.execution.entry.contract_tolerance_pips) * pip,
  )
  if strategy and is_range_strategy(strategy):
    return scalp_zone_access(
      record.direction,
      bid,
      ask,
      record.low,
      record.high,
      tolerance,
      pip_size=pip,
      maximum_chase_pips=scalp_maximum_chase_pips(runtime_config),
    )
  if strategy and is_technique_or_confluence(strategy):
    return scalp_zone_access(
      record.direction,
      bid,
      ask,
      record.low,
      record.high,
      tolerance,
      pip_size=pip,
      maximum_chase_pips=_technique_chase_pips(),
    )
  evidence = executable_quote_in_zone(
    record.direction,
    bid,
    ask,
    record.low,
    record.high,
    tolerance,
    pip_size=pip,
  )
  from app.autotrade.execution_confirmation import ScalpZoneAccess
  return ScalpZoneAccess(
    evidence,
    "inside" if evidence.inside else "approach_wait",
    evidence.distance_pips,
    0.0,
  )


def _decisive_break_from_close(record: ZoneWatch, close: float | None) -> bool:
  """True when a closed bar finished beyond the zone's far/invalidating edge.

  BUY (demand) fails by closing below ``low``; SELL (supply) fails by closing
  above ``high``. Live spot wicks must not invalidate — callers load the latest
  closed bar on ``record.source_timeframe`` (fallback M5).
  """
  if close is None:
    return False
  try:
    price = float(close)
  except (TypeError, ValueError):
    return False
  if not math.isfinite(price):
    return False
  if str(record.direction).upper() == "BUY":
    return price < record.low
  return price > record.high


def _ohlc_source(
  client: Any,
  source: RedisOHLCSource | None = None,
) -> RedisOHLCSource:
  return source if source is not None else RedisOHLCSource(client)


async def _closed_bar_decisive_break(
  client: Any,
  record: ZoneWatch,
  *,
  source: RedisOHLCSource | None = None,
) -> bool:
  """Load latest closed bar and test far-edge invalidation."""
  tf = str(record.source_timeframe or "M5").upper() or "M5"
  try:
    ohlc = _ohlc_source(client, source)
    frame = await ohlc.window(record.symbol, tf, max(3, window_for_timeframe(tf)))
  except Exception:
    log.exception(
      "closed-bar decisive_break load failed symbol=%s zone_id=%s tf=%s",
      record.symbol, record.zone_id, tf,
    )
    return False
  if frame.empty:
    return False
  try:
    close = float(frame["close"].iloc[-1])
  except (TypeError, ValueError, KeyError, IndexError):
    return False
  return _decisive_break_from_close(record, close)


async def _record_policy_telemetry(
  client: Any,
  *,
  symbol: str,
  kind: str,
  reason_code: str,
  payload: dict[str, Any],
) -> None:
  counter_key = f"auto_trade:entry_{kind}:{symbol.upper()}:{reason_code}"
  try:
    await client.incr(counter_key)
  except Exception:
    log.exception(
      "entry %s counter failed symbol=%s reason=%s",
      kind, symbol, reason_code,
    )
  await increment_metric(
    client,
    f"entry_{kind}",
    symbol=symbol,
    dimensions={"reason": reason_code},
  )
  key = (
    f"{_LAST_LOCATION_KEY_PREFIX}:{symbol.upper()}"
    if kind == "location"
    else f"{_LAST_ACTIVATION_KEY_PREFIX}:{symbol.upper()}"
  )
  await client.set(
    key,
    json.dumps(payload, separators=(",", ":"), sort_keys=True),
  )


def _location_and_activation_for_record(
  *,
  match: StrategyMatch,
  record: ZoneWatch,
  quote: tuple[float, float, int],
  evidence: Any,
  trigger: Any,
  now: int,
  range_bounds: dict[str, float | None] | None = None,
  chase_pips: float | None = None,
  maximum_chase_pips: float | None = None,
  decisive_break: bool = False,
  impulse_bars: list | None = None,
):
  bid, ask, _ts = quote
  ranges = range_bounds or _empty_range_bounds()
  context = build_entry_location_context(
    execution_price=float(evidence.executable_quote or match.current_price),
    direction=record.direction,
    ask=ask,
    bid=bid,
    # Deal with discovery-identical dealing ranges. Do NOT feed
    # StrategyMatch.range_* here — those are scalp box edges and are null
    # for Zone Reaction / key-level families (prod: entry_location_context_missing).
    m15_range_low=ranges.get("m15_range_low"),
    m15_range_high=ranges.get("m15_range_high"),
    h1_range_low=ranges.get("h1_range_low"),
    h1_range_high=ranges.get("h1_range_high"),
    m5_range_low=ranges.get("m5_range_low"),
    m5_range_high=ranges.get("m5_range_high"),
    zone_low=record.low,
    zone_high=record.high,
  )
  inst = instrument_geometry.instrument_runtime(record.symbol)
  location = evaluate_entry_location(
    strategy=match.strategy,
    direction=record.direction,
    context=context,
    cfg=inst,
  )
  from app.autotrade.execution_confirmation import confirmation_policy_for

  confirmation = confirmation_policy_for(match)
  activation = evaluate_entry_activation(
    strategy=match.strategy,
    direction=record.direction,
    zone_entered_at=(
      None if record.zone_entered_at is None else int(record.zone_entered_at)
    ),
    quote_inside=bool(evidence.inside),
    decisive_break=bool(decisive_break),
    trigger=trigger,
    location_decision=location,
    now=int(now),
    cfg=inst,
    chase_pips=chase_pips,
    maximum_chase_pips=maximum_chase_pips,
    m5_authoritative=bool(confirmation.m5_authoritative),
    impulse_bars=impulse_bars,
    zone_low=record.low,
    zone_high=record.high,
    execution_price=float(
      getattr(evidence, "executable_quote", None) or match.current_price
    ),
  )
  return location, activation, context


async def _prepare_activation(
  client: Any,
  *,
  record: ZoneWatch,
  match: StrategyMatch,
  quote: tuple[float, float, int],
  evidence: Any,
  analysis_context: Any | None = None,
  chase_pips: float | None = None,
  maximum_chase_pips: float | None = None,
  source: RedisOHLCSource | None = None,
) -> StrategyMatch | None:
  """Return a stamped match ready to activate, or None while waiting/blocked."""
  now = quote[2]
  from app.autotrade.killzone import (
    evaluate_killzone_gate,
    evaluate_reaction_publish_window,
    technique_enforce,
  )

  inst = instrument_geometry.instrument_runtime(record.symbol)
  tech = getattr(inst.execution, "technique", None)
  enforce_pack = technique_enforce(inst)
  candidate_is_scalp = is_scalp_strategy(
    str(getattr(match, "strategy", "") or ""),
    family=str(getattr(match, "strategy_family", "") or getattr(match, "family", "") or "")
    or None,
    strategy_mode=str(getattr(match, "strategy_mode", "") or "") or None,
  )
  if candidate_is_scalp:
    require_kz = True if tech is None else bool(
      getattr(tech, "hfs_require_killzone", True),
    )
    kz = evaluate_killzone_gate(
      ts=now,
      cfg=inst,
      require=require_kz and enforce_pack,
    )
    if not kz.allowed:
      log.info(
        "entry activation blocked outside killzone symbol=%s zone_id=%s "
        "utc_hour=%s killzone=%s reason=%s",
        record.symbol,
        record.zone_id,
        kz.utc_hour,
        kz.killzone_name,
        kz.reason_code,
      )
      await _record_policy_telemetry(
        client,
        symbol=record.symbol,
        kind="activation",
        reason_code=kz.reason_code,
        payload={
          "symbol": record.symbol,
          "zone_id": record.zone_id,
          "strategy": match.strategy,
          "direction": record.direction,
          "killzone_name": kz.killzone_name,
          "utc_hour": kz.utc_hour,
          **kz.measured,
        },
      )
      return None
  else:
    win = evaluate_reaction_publish_window(
      ts=now,
      cfg=inst,
      require=enforce_pack,
    )
    if not win.allowed:
      log.info(
        "entry activation waiting outside_reaction_publish_window "
        "symbol=%s zone_id=%s utc_hour=%s reason=%s",
        record.symbol,
        record.zone_id,
        win.utc_hour,
        win.reason_code,
      )
      await _record_policy_telemetry(
        client,
        symbol=record.symbol,
        kind="activation",
        reason_code=win.reason_code,
        payload={
          "symbol": record.symbol,
          "zone_id": record.zone_id,
          "strategy": match.strategy,
          "direction": record.direction,
          "utc_hour": win.utc_hour,
          **win.measured,
        },
      )
      return None
    require_kz = True if tech is None else bool(
      getattr(tech, "reaction_require_killzone", True),
    )
    kz = evaluate_killzone_gate(
      ts=now,
      cfg=inst,
      require=require_kz and enforce_pack,
    )
    if not kz.allowed:
      log.info(
        "entry activation blocked outside killzone symbol=%s zone_id=%s "
        "utc_hour=%s killzone=%s reason=%s",
        record.symbol,
        record.zone_id,
        kz.utc_hour,
        kz.killzone_name,
        kz.reason_code,
      )
      await _record_policy_telemetry(
        client,
        symbol=record.symbol,
        kind="activation",
        reason_code=kz.reason_code,
        payload={
          "symbol": record.symbol,
          "zone_id": record.zone_id,
          "strategy": match.strategy,
          "direction": record.direction,
          "killzone_name": kz.killzone_name,
          "utc_hour": kz.utc_hour,
          **kz.measured,
        },
      )
      return None

  stop_error = _match_planned_stop_hard_error(match)
  if stop_error is not None:
    log.info(
      "entry activation blocked planned_stop_error symbol=%s strategy=%s "
      "zone_id=%s reason=%s",
      record.symbol,
      match.strategy,
      record.zone_id,
      stop_error,
    )
    await _record_policy_telemetry(
      client,
      symbol=record.symbol,
      kind="activation",
      reason_code=stop_error,
      payload={
        "symbol": record.symbol,
        "zone_id": record.zone_id,
        "strategy": match.strategy,
        "direction": record.direction,
        "planned_stop_error": stop_error,
      },
    )
    await _terminalize_zone_watch(
      client,
      record.zone_id,
      reason_code=stop_error,
    )
    return None

  trigger = await _m1_trigger_for_zone(client, record, source=source)
  impulse_bars = await _recent_m1_impulse_bars(client, record, source=source)
  range_bounds = await _resolve_location_range_bounds(
    client,
    symbol=record.symbol,
    zone_id=record.zone_id,
    analysis_context=analysis_context,
  )
  decisive_break = await _closed_bar_decisive_break(
    client, record, source=source,
  )
  location, activation, context = _location_and_activation_for_record(
    match=match,
    record=record,
    quote=quote,
    evidence=evidence,
    trigger=trigger,
    now=now,
    range_bounds=range_bounds,
    chase_pips=chase_pips,
    maximum_chase_pips=maximum_chase_pips,
    decisive_break=decisive_break,
    impulse_bars=impulse_bars,
  )
  checked_at = datetime.now(timezone.utc).isoformat()
  location_payload = {
    "symbol": record.symbol,
    "strategy": match.strategy,
    "direction": record.direction,
    "mode": location.measured.get("mode"),
    "allowed": location.allowed,
    "would_block": location.would_block,
    "reason_code": location.reason_code,
    "effective_range_source": context.effective_range_source,
    "effective_range_low": context.effective_range_low,
    "effective_range_high": context.effective_range_high,
    "effective_range_position": context.effective_range_position_raw,
    "zone_low": record.low,
    "zone_high": record.high,
    "executable_quote": evidence.executable_quote,
    "trigger": None if trigger is None else str(trigger.pattern),
    "checked_at": checked_at,
    "range_bounds_usable": _ranges_usable(range_bounds),
  }
  await _record_policy_telemetry(
    client,
    symbol=record.symbol,
    kind="location",
    reason_code=location.reason_code,
    payload=location_payload,
  )
  activation_payload = {
    **location_payload,
    "mode": activation.measured.get("mode"),
    "allowed": activation.allowed,
    "would_block": activation.would_block,
    "reason_code": activation.reason_code,
    "requires_trigger": activation.requires_trigger,
  }
  await _record_policy_telemetry(
    client,
    symbol=record.symbol,
    kind="activation",
    reason_code=activation.reason_code,
    payload=activation_payload,
  )
  from app.autotrade.reaction_funnel import (
    STAGE_ACTIVATION_ALLOWED,
    STAGE_WAITING_REACTION_TRIGGER,
    bump_funnel,
  )

  if activation.allowed:
    await bump_funnel(
      client,
      symbol=record.symbol,
      stage=STAGE_ACTIVATION_ALLOWED,
      strategy=match.strategy,
      family=str(getattr(match, "family", "") or ""),
      strategy_mode=str(getattr(match, "strategy_mode", "") or ""),
      reason_code=activation.reason_code,
      once_key=f"{record.zone_id}:activation_allowed",
    )
  elif activation.reason_code in {
    "reaction_trigger_missing",
    "reaction_trigger_stale",
    "reaction_trigger_before_zone_touch",
    "reaction_trigger_wrong_direction",
    "confirmation_requires_sweep_body",
  }:
    await bump_funnel(
      client,
      symbol=record.symbol,
      stage=STAGE_WAITING_REACTION_TRIGGER,
      strategy=match.strategy,
      family=str(getattr(match, "family", "") or ""),
      strategy_mode=str(getattr(match, "strategy_mode", "") or ""),
      reason_code=activation.reason_code,
      once_key=f"{record.zone_id}:waiting_reaction_trigger",
    )
  # Spot-driven ZoneWatch re-checks ~0.5s while waiting for M1 reaction.
  # Keep INFO for allow / decisive break; soft waits stay DEBUG or the
  # owner log scrolls one Key Level line per tick.
  _log = log.info if (
    activation.allowed
    or activation.reason_code == "zone_decisively_broken"
  ) else log.debug
  _log(
    "entry activation decision symbol=%s strategy=%s direction=%s "
    "location=%s activation=%s allowed=%s would_block=%s grade=%s "
    "range_source=%s range_usable=%s",
    record.symbol,
    match.strategy,
    record.direction,
    location.reason_code,
    activation.reason_code,
    activation.allowed,
    activation.would_block,
    record.grade,
    context.effective_range_source,
    _ranges_usable(range_bounds),
  )
  if not activation.allowed:
    if activation.reason_code == "zone_decisively_broken":
      latest = await load_zone_watch(client, record.zone_id)
      if latest is not None and latest.state not in TERMINAL_ZONE_WATCH_STATES:
        await transition_zone_watch(
          client,
          record.zone_id,
          INVALIDATED,
          reason_code="zone_decisively_broken",
        )
    return None

  stamped = apply_trigger_to_match(match, trigger)
  return replace(
    stamped,
    entry_location_source=context.effective_range_source,
    entry_location_position=context.effective_range_position_raw,
    entry_location_reason=location.reason_code,
  )


async def _record_width_telemetry(
  client: Any,
  *,
  symbol: str,
  zone_id: str,
  result: Any,
  source_tf: str,
) -> bool:
  low = float(result.entry_zone.low)
  high = float(result.entry_zone.high)
  raw_low = getattr(result, "structural_low", None)
  raw_high = getattr(result, "structural_high", None)
  raw_width = (
    high - low
    if raw_low is None or raw_high is None
    else float(raw_high) - float(raw_low)
  )
  tags = tuple(getattr(result, "confluence_tags", None) or ())
  band_kind = classify_band_kind(getattr(result, "structural_source", None))
  width = validate_zone_width(
    raw_width=raw_width,
    merged_width=high - low,
    merge_sources=tags,
    is_major=source_tf.upper() == "H1",
    # Section 4: a level/range-edge/breakout-retest band is a tolerance
    # around a price or an already-validated barrier, not a merged
    # structural zone - it must never be rejected only for being narrower
    # than XAU_ZONE_MIN_WIDTH_PRICE.
    min_width=0.0 if band_kind != BandKind.STRUCTURAL_ZONE else None,
    symbol=symbol,
  )
  payload = {
    "symbol": symbol.upper(),
    "zone_id": zone_id,
    "source_tf": source_tf.upper(),
    "direction": str(result.direction).upper(),
    "raw_zone_width": width.raw_zone_width,
    "merged_zone_width": width.merged_zone_width,
    "min_required_width": width.min_required_width,
    "max_allowed_width": width.max_allowed_width,
    "merge_sources": list(width.merge_sources),
    "eligible": width.eligible,
    "rejection_reason": width.rejection_reason,
    "recorded_at": _now(),
  }
  await client.set(
    width_telemetry_key(symbol, zone_id),
    json.dumps(payload, separators=(",", ":"), sort_keys=True),
    ex=_CANDIDATE_TTL_SECONDS,
  )
  try:
    from app.autotrade.lifecycle import increment_metric

    await increment_metric(
      client,
      "zone_width_evaluated",
      symbol=symbol,
      dimensions={
        "eligible": str(bool(width.eligible)).lower(),
        "reason": str(width.rejection_reason or "accepted"),
      },
    )
  except Exception:
    log.exception("zone width metric failed symbol=%s zone_id=%s", symbol, zone_id)
  # Telemetry above is recorded unconditionally regardless of outcome - it
  # stays useful even when this check cannot itself reject anything. A
  # level/range-edge/breakout-retest band is never rejectable on width at
  # all (min_width=0.0 above already guarantees width.eligible for those).
  # A genuine STRUCTURAL_ZONE candidate additionally requires
  # scanner_zone_width_gate_enabled to be true before its own width result
  # can actually reject it - SCANNER_ZONE_WIDTH_GATE_ENABLED=false must
  # disable structural width rejection everywhere, not only on the legacy
  # scanner merge path (this function used to enforce the contract as
  # unconditionally canonical, silently ignoring that flag).
  if (
    band_kind == BandKind.STRUCTURAL_ZONE
    and not runtime_config.actionability.scanner_gates.zone_width_gate_enabled
  ):
    return True
  return bool(width.eligible)


async def _save_candidate(client: Any, zone_id: str, match: StrategyMatch) -> None:
  await client.set(
    candidate_key(zone_id),
    _refresh_candidate_risk(match).to_json(),
    ex=_CANDIDATE_TTL_SECONDS,
  )


async def _load_candidate(client: Any, zone_id: str) -> StrategyMatch | None:
  raw = await client.get(candidate_key(zone_id))
  if raw is None:
    return None
  match = StrategyMatch.from_json(raw)
  if match is None:
    return None
  return _refresh_candidate_risk(match)


def _refresh_candidate_risk(match: StrategyMatch) -> StrategyMatch:
  """Re-stamp volume multiplier so stale Redis candidates cannot half-size."""
  from app.autotrade.execution_policy import (
    TIER_B,
    risk_multiplier_for_tier,
  )
  from app.autotrade.strategy_taxonomy import is_scalp_strategy

  mult = risk_multiplier_for_tier(
    str(getattr(match, "tier", None) or TIER_B),
    post_impulse=bool(match.range_state == "post_impulse_range"),
    one_sided=match.strategy == "One-Sided Range Reaction",
    range_scalp=is_scalp_strategy(
      match.strategy,
      family=str(getattr(match, "family", "") or ""),
      strategy_mode=str(getattr(match, "strategy_mode", "") or ""),
    ),
  )
  if abs(float(match.risk_multiplier) - float(mult)) < 1e-9:
    return match
  return replace(match, risk_multiplier=float(mult))


async def _persist_match(client: Any, match: StrategyMatch) -> StrategyMatch:
  """Begin execution lifecycle only for a signal that is executable now."""
  from app.analysis import scanner

  lifecycle = await scanner._advance_setup_to_confirmed(
    client,
    match,
    match.symbol,
    match.source_tf,
  )
  if lifecycle is not None:
    _setup_id, thesis_id = lifecycle
    match = replace(match, thesis_id=thesis_id)
  current_raw = await client.get(strategy_matches_key(match.symbol))
  from app.autotrade.multi_match import deserialize_matches

  current = deserialize_matches(current_raw) if current_raw else []
  active = [item for item in current if item.expires_at >= _now()]
  combined, _events = dedupe_matches(
    [*active, match],
    atr=match.atr,
  )
  ttl = max(60, match.expires_at - _now())
  await client.set(strategy_match_key(match.symbol), match.to_json(), ex=ttl)
  await client.set(strategy_matches_key(match.symbol), serialize_matches(combined), ex=ttl)
  return match


def _published_result_statuses():
  from app.autotrade import worker

  return frozenset({
    worker.PUBLISH_STATUS_EXECUTION_HANDOFF_CREATED,
    worker.PUBLISH_STATUS_PUBLISHED,
    worker.PUBLISH_STATUS_DUPLICATE_RECONCILED,
  })


async def _ensure_published_root_card(client: Any, match: StrategyMatch) -> None:
  """Create PLAN PUBLISHED root card when cutover skipped SETUP FORMING."""
  try:
    from app.autotrade.setup_card import ensure_plan_published_root_card

    await ensure_plan_published_root_card(client, match)
  except Exception:
    # Publication must not roll back because Telegram carding failed.
    log.exception(
      "plan_published_root_card_ensure_failed setup_id=%s symbol=%s",
      match.match_id,
      match.symbol,
    )


async def _safe_direct_publish(
  client: Any,
  match: StrategyMatch,
  *,
  symbol: str,
  event_ts: str | None = None,
  source: RedisOHLCSource | None = None,
):
  """Reconcile all active plan states and fail over instead of stranding."""
  from app.autotrade import worker

  published_statuses = _published_result_statuses()

  try:
    result = await _ORIGINAL_DIRECT_PUBLISH(
      client,
      match,
      symbol=symbol,
      event_ts=event_ts,
      source=source,
    )
  except Exception as exc:
    log.exception(
      "direct publication failed; durable fallback requested symbol=%s setup=%s",
      symbol,
      match.match_id,
    )
    existing = await worker.resolve_existing_v8_state(client, match)
    if existing.already_published:
      _PUBLISHED_SETUP_IDS.add(match.match_id)
      await _ensure_published_root_card(client, match)
      return worker.PublishResult(
        status=worker.PUBLISH_STATUS_DUPLICATE_RECONCILED,
        plan_id=existing.plan_id,
        reason_code="direct_publish_exception_after_publication",
        zone_id=str(match.confluence_zone_id or match.structural_zone_id or ""),
        setup_id=match.match_id,
        measured={"exception_type": type(exc).__name__},
      )
    return worker.PublishResult(
      status=worker.PUBLISH_STATUS_REMAINED_WATCHING,
      plan_id=existing.plan_id,
      reason_code="direct_publish_failed_durable_fallback",
      zone_id=str(match.confluence_zone_id or match.structural_zone_id or ""),
      setup_id=match.match_id,
      measured={"exception_type": type(exc).__name__},
    )

  existing = await worker.resolve_existing_v8_state(client, match)
  if existing.already_published:
    _PUBLISHED_SETUP_IDS.add(match.match_id)
    await _ensure_published_root_card(client, match)
    status = (
      worker.PUBLISH_STATUS_PUBLISHED
      if result.status == worker.PUBLISH_STATUS_PUBLISHED
      else worker.PUBLISH_STATUS_DUPLICATE_RECONCILED
    )
    return worker.PublishResult(
      status=status,
      plan_id=existing.plan_id,
      reason_code=(
        result.reason_code
        if result.reason_code
        else "active_plan_state_reconciled"
      ),
      zone_id=result.zone_id,
      setup_id=result.setup_id,
      measured=result.measured,
      executable_quote=result.executable_quote,
      quote_side=result.quote_side,
    )
  if result.status in published_statuses:
    _PUBLISHED_SETUP_IDS.add(match.match_id)
    await _ensure_published_root_card(client, match)
  return result


async def _activate_match(
  client: Any,
  record: ZoneWatch,
  match: StrategyMatch,
  *,
  event_ts: str,
  source: RedisOHLCSource | None = None,
):
  from app.autotrade import worker
  from app.autotrade.setup_lifecycle import (
    CONFIRMED,
    INVALIDATED as SETUP_INVALIDATED,
    load_setup,
    transition_setup,
  )

  now = _now()
  quote = await _load_quote(client, record.symbol)
  if quote is None:
    log.info(
      "zone activate remain symbol=%s strategy=%s zone_id=%s reason=quote_unavailable",
      record.symbol, match.strategy, record.zone_id,
    )
    return None
  access = _scalp_access(record, quote, strategy=match.strategy)
  evidence = access.evidence
  if not access.executable:
    decisive_break = await _closed_bar_decisive_break(
      client, record, source=source,
    )
    log.info(
      "zone activate remain symbol=%s strategy=%s zone_id=%s "
      "reason=%s mode=%s distance_pips=%s max_chase=%s decisive_break=%s",
      record.symbol,
      match.strategy,
      record.zone_id,
      (
        "scalp_missed_chase"
        if access.status == "chase_missed"
        else "quote_outside_zone"
      ),
      access.status,
      access.chase_pips,
      access.maximum_chase_pips,
      decisive_break,
    )
    await record_zone_presence(
      client,
      record.zone_id,
      inside=False,
      now=now,
      decisive_break=decisive_break,
    )
    return None
  match = replace(
    match,
    issued_at=now,
    expires_at=now + max(
      60, int(runtime_config.lifecycle.strategy_match.maximum_age_seconds),
    ),
    current_price=float(evidence.executable_quote or match.current_price),
  )
  match = await _persist_match(client, match)
  result = await _safe_direct_publish(
    client,
    match,
    symbol=record.symbol,
    event_ts=event_ts,
  )
  if result.status in {
    worker.PUBLISH_STATUS_EXECUTION_HANDOFF_CREATED,
    worker.PUBLISH_STATUS_PUBLISHED,
    worker.PUBLISH_STATUS_DUPLICATE_RECONCILED,
  }:
    _PUBLISHED_SETUP_IDS.add(match.match_id)
    from app.autotrade.reaction_funnel import (
      STAGE_PLAN_PUBLISHED,
      bump_funnel,
    )

    await bump_funnel(
      client,
      symbol=record.symbol,
      stage=STAGE_PLAN_PUBLISHED,
      strategy=match.strategy,
      family=str(getattr(match, "family", "") or ""),
      strategy_mode=str(getattr(match, "strategy_mode", "") or ""),
      reason_code=str(result.reason_code or result.status),
      once_key=f"{record.zone_id}:plan_published",
    )
    latest = await load_zone_watch(client, record.zone_id)
    if latest is not None and latest.state not in (
      TERMINAL_ZONE_WATCH_STATES | LOCKED_ZONE_WATCH_STATES
    ):
      await lock_zone_watch_published(
        client,
        record.zone_id,
        plan_id=result.plan_id,
        reason_code=result.reason_code or "execution_handoff_created",
      )
    elif latest is not None and latest.state == PUBLISHED_LOCKED:
      pass  # already locked for this episode
    return match

  if result.reason_code == "direct_publish_failed_durable_fallback":
    # Reaction + technique/confluence never use the ready stream once
    # executable — leave them watching and retry on the next M1/quote cycle.
    if (
      is_reaction_strategy(match.strategy)
      or is_technique_or_confluence(match.strategy)
    ):
      log.info(
        "zone activate remain symbol=%s strategy=%s zone_id=%s "
        "reason=direct_publish_failed_durable_fallback status=%s "
        "mode=%s distance_pips=%s max_chase=%s",
        record.symbol,
        match.strategy,
        record.zone_id,
        result.status,
        access.status,
        access.chase_pips,
        access.maximum_chase_pips,
      )
      await record_zone_presence(client, record.zone_id, inside=False, now=now)
      return None
    # Exceptional fallback only for non-reaction / non-technique workflows.
    stream_id = await enqueue_strategy_match_ready(client, match, recovery=True)
    if stream_id is not None:
      return match

  reject_reason = result.reason_code or "zone_no_longer_executable"
  log.info(
    "zone activate reject symbol=%s strategy=%s zone_id=%s "
    "reason=%s status=%s mode=%s distance_pips=%s max_chase=%s",
    record.symbol,
    match.strategy,
    record.zone_id,
    reject_reason,
    result.status,
    access.status,
    access.chase_pips,
    access.maximum_chase_pips,
  )
  setup = await load_setup(client, match.match_id)
  if setup is not None and setup.state not in {SETUP_INVALIDATED, *worker.TERMINAL_STATES}:
    try:
      await transition_setup(
        client,
        match.match_id,
        SETUP_INVALIDATED,
        reason_code="zone_no_longer_executable",
      )
    except Exception:
      log.exception("could not retire non-executable setup=%s", match.match_id)
  if _publish_should_terminalize_zone_watch(
    status=result.status,
    reason_code=reject_reason,
  ):
    await _terminalize_zone_watch(
      client,
      record.zone_id,
      reason_code=reject_reason,
    )
  else:
    await record_zone_presence(client, record.zone_id, inside=False, now=now)
  return None


async def _m1_trigger_for_zone(
  client: Any,
  record: ZoneWatch,
  *,
  source: RedisOHLCSource | None = None,
) -> Any | None:
  ohlc = _ohlc_source(client, source)
  frame = await ohlc.window(record.symbol, "M1", window_for_timeframe("M1"))
  if frame.empty or record.zone_entered_at is None:
    return None
  trigger = evaluate_m1_trigger_window(
    frame,
    zone_low=record.low,
    zone_high=record.high,
    key_level=(record.low + record.high) / 2,
    direction=record.direction,
    earliest_bar_ts=int(record.zone_entered_at) + 1,
    after_bar_ts=record.last_evaluated_m1_ts,
    cfg=instrument_runtime_view(record.symbol),
  )
  latest = latest_eligible_m1_bar_ts(
    frame,
    earliest_bar_ts=int(record.zone_entered_at) + 1,
    after_bar_ts=record.last_evaluated_m1_ts,
  )
  if latest is not None:
    await mark_m1_evaluated(client, record.zone_id, int(latest))
  return trigger


async def _recent_m1_impulse_bars(
  client: Any,
  record: ZoneWatch,
  *,
  source: RedisOHLCSource | None = None,
) -> list[dict[str, float]]:
  ohlc = _ohlc_source(client, source)
  frame = await ohlc.window(record.symbol, "M1", window_for_timeframe("M1"))
  if frame is None or getattr(frame, "empty", True):
    return []
  tail = frame.tail(5)
  bars: list[dict[str, float]] = []
  for _, row in tail.iterrows():
    try:
      bars.append({
        "h": float(row["high"]),
        "l": float(row["low"]),
        "c": float(row["close"]),
      })
    except (TypeError, ValueError, KeyError):
      continue
  return bars


async def _evaluate_record(
  client: Any,
  record: ZoneWatch,
  *,
  event_ts: str,
  quote: tuple[float, float, int] | None = None,
  source: RedisOHLCSource | None = None,
) -> StrategyMatch | None:
  if record.state in TERMINAL_ZONE_WATCH_STATES | LOCKED_ZONE_WATCH_STATES:
    return None
  quote = quote or await _load_quote(client, record.symbol)
  if quote is None:
    return None
  match = await _load_candidate(client, record.zone_id)
  if match is None:
    return None
  access = _scalp_access(record, quote, strategy=match.strategy)
  evidence = access.evidence
  decisive_break = await _closed_bar_decisive_break(
    client, record, source=source,
  )
  record, _entered = await record_zone_presence(
    client,
    record.zone_id,
    inside=bool(evidence.inside or access.status == "chase"),
    now=quote[2],
    htf_evidence=record.source_timeframe in {"H1", "M15"},
    decisive_break=decisive_break,
  )
  if (
    not access.executable
    or record.state in TERMINAL_ZONE_WATCH_STATES | LOCKED_ZONE_WATCH_STATES
  ):
    return None
  prepared = await _prepare_activation(
    client,
    record=record,
    match=match,
    quote=quote,
    evidence=evidence,
    analysis_context=None,
    chase_pips=access.chase_pips,
    maximum_chase_pips=access.maximum_chase_pips,
    source=source,
  )
  if prepared is None:
    # Keep ZoneWatch active; do not persist / publish / create lifecycle.
    return None
  return await _activate_match(
    client, record, prepared, event_ts=event_ts, source=source,
  )


async def _sync_strategy_match_cutover(
  client: Any,
  symbol: str,
  tf: str,
  event_ts: str,
  ctx: Any,
  results: list[Any],
  *,
  require_static_eligibility: bool = False,
) -> StrategyMatch | None:
  """Retain every valid zone; create setup only when executable now."""
  from app.analysis import scanner

  indicators = getattr(ctx, "indicators", {})
  indicator = indicators.get(tf.upper()) if isinstance(indicators, dict) else None
  atr = (
    float(indicator.atr.iloc[-1])
    if indicator is not None and not indicator.atr.empty
    else 0.0
  )
  pip = scanner._pip_size(symbol)
  ready: list[tuple[int, ZoneWatch, StrategyMatch]] = []

  # This function replaced scanner._sync_strategy_match at the module-name
  # level (see install_zone_execution_cutover) - it is the only code that
  # actually runs for every live "why did my setup vanish" complaint. Every
  # `continue`/early `return None` below used to be silent: nothing in
  # scanner.py's own logging (actionability-observed, gate_reject, etc.)
  # covers what happens *after* a result is already eligible and handed to
  # this function, so a setup could pass every upstream check and still
  # never become an executable match with zero trace of why.
  for result in sorted(results, key=scanner._result_rank):
    if require_static_eligibility:
      eligibility = getattr(result, "execution_eligibility", None)
      if eligibility is None or not eligibility.allowed:
        continue
    source_tf = str(getattr(result, "structural_timeframe", None) or tf).upper()
    zone_id = _zone_id(symbol, tf, result, atr, pip)
    if not await _record_width_telemetry(
      client,
      symbol=symbol,
      zone_id=zone_id,
      result=result,
      source_tf=source_tf,
    ):
      existing = await load_zone_watch(client, zone_id)
      if existing is not None and existing.state not in TERMINAL_ZONE_WATCH_STATES:
        await transition_zone_watch(
          client,
          zone_id,
          INVALIDATED,
          reason_code="zone_width_contract_rejected",
        )
      log.info(
        "zone watch cutover rejected symbol=%s tf=%s setup=%s direction=%s "
        "reason=zone_width_contract_rejected zone_id=%s",
        symbol, tf, result.setup, result.direction, zone_id,
      )
      continue

    grade = _grade(result, source_tf)
    if grade not in {GRADE_A, GRADE_B}:
      log.info(
        "zone watch cutover rejected symbol=%s tf=%s setup=%s direction=%s "
        "reason=grade_excluded grade=%s zone_id=%s",
        symbol, tf, result.setup, result.direction, grade, zone_id,
      )
      continue
    match, build_reason, _measured = scanner._build_one_strategy_match(
      symbol,
      tf,
      event_ts,
      ctx,
      result,
    )
    if match is None:
      log.info(
        "zone watch cutover rejected symbol=%s tf=%s setup=%s direction=%s "
        "reason=match_build_failed build_reason=%s zone_id=%s",
        symbol, tf, result.setup, result.direction, build_reason, zone_id,
      )
      continue
    record, created = await discover_zone_watch(
      client,
      zone_id=zone_id,
      symbol=symbol,
      direction=result.direction,
      low=float(result.entry_zone.low),
      high=float(result.entry_zone.high),
      source_timeframe=source_tf,
      structural_sources=(str(getattr(result, "structural_source", "") or result.setup),),
      confluence_tags=tuple(getattr(result, "confluence_tags", None) or ()),
      technique_tags=tuple(getattr(result, "confluence_tags", None) or ()),
      grade=grade,
      score=float(getattr(result, "source_score", None) or result.confluence),
      market_map_id=(
        ""
        if getattr(result, "execution_eligibility", None) is None
        else str(result.execution_eligibility.market_map_id or "")
      ),
      structure_signature=str(getattr(result, "structural_id", None) or zone_id),
    )
    if created and record.state == DISCOVERED:
      record, _ = await transition_zone_watch(
        client,
        zone_id,
        WATCHING_RETEST,
        reason_code="zone_discovered",
      )
      from app.autotrade.reaction_funnel import (
        STAGE_ZONE_DISCOVERED,
        bump_funnel,
      )

      await bump_funnel(
        client,
        symbol=symbol,
        stage=STAGE_ZONE_DISCOVERED,
        strategy=str(result.setup),
        family=str(getattr(match, "family", "") or ""),
        strategy_mode=str(getattr(match, "strategy_mode", "") or ""),
        reason_code="zone_discovered",
        once_key=f"{zone_id}:zone_discovered",
      )
    await _save_candidate(client, zone_id, replace(
      match,
      confluence_zone_id=match.confluence_zone_id or zone_id,
    ))
    # Snapshot discovery dealing ranges onto the zone so M1 cutover can
    # evaluate location without StrategyMatch.range_* (scalp-box-only fields).
    await _save_location_ranges(client, zone_id, range_bounds_from_context(ctx))
    quote = await _load_quote(client, symbol)
    if quote is None:
      log.info(
        "zone watch cutover rejected symbol=%s tf=%s setup=%s direction=%s "
        "reason=quote_unavailable zone_id=%s",
        symbol, tf, result.setup, result.direction, zone_id,
      )
      continue
    access = _scalp_access(record, quote, strategy=str(result.setup))
    evidence = access.evidence
    decisive_break = await _closed_bar_decisive_break(client, record)
    record, _ = await record_zone_presence(
      client,
      zone_id,
      inside=bool(evidence.inside or access.status == "chase"),
      now=quote[2],
      htf_evidence=source_tf in {"H1", "M15"},
      decisive_break=decisive_break,
    )
    if record.state in TERMINAL_ZONE_WATCH_STATES | LOCKED_ZONE_WATCH_STATES:
      log.info(
        "zone watch cutover rejected symbol=%s tf=%s setup=%s direction=%s "
        "reason=zone_watch_locked_or_terminal zone_id=%s state=%s",
        symbol, tf, result.setup, result.direction, zone_id, record.state,
      )
      continue
    if not access.executable:
      if access.status == "chase_missed":
        await transition_zone_watch(
          client,
          zone_id,
          INVALIDATED,
          reason_code="scalp_missed_chase",
        )
        log.info(
          "zone watch cutover rejected symbol=%s tf=%s setup=%s direction=%s "
          "reason=scalp_missed_chase zone_id=%s chase_pips=%s max_chase=%s",
          symbol, tf, result.setup, result.direction, zone_id,
          access.chase_pips, access.maximum_chase_pips,
        )
        continue
      log.info(
        "zone watch cutover waiting symbol=%s tf=%s setup=%s direction=%s "
        "reason=%s zone_id=%s state=%s chase_pips=%s max_chase=%s",
        symbol, tf, result.setup, result.direction,
        "quote_outside_zone",
        zone_id, record.state, access.chase_pips, access.maximum_chase_pips,
      )
      continue
    prepared = await _prepare_activation(
      client,
      record=record,
      match=match,
      quote=quote,
      evidence=evidence,
      analysis_context=ctx,
      chase_pips=access.chase_pips,
      maximum_chase_pips=access.maximum_chase_pips,
    )
    if prepared is None:
      log.info(
        "zone watch cutover waiting symbol=%s tf=%s setup=%s direction=%s "
        "reason=entry_activation_not_ready grade=%s zone_id=%s",
        symbol, tf, result.setup, result.direction, record.grade, zone_id,
      )
      continue
    ready.append((0 if record.grade == GRADE_A else 1, record, prepared))

  if not ready:
    return None
  _grade_rank, record, match = sorted(
    ready,
    key=lambda item: (item[0], -item[1].score, item[1].zone_id),
  )[0]
  return await _activate_match(client, record, match, event_ts=event_ts)


def _format_detection_cutover(*args: Any, **kwargs: Any) -> str:
  execution_match = kwargs.get("execution_match")
  if execution_match is None and len(args) >= 8:
    execution_match = args[7]
  if execution_match is None or execution_match.match_id not in _PUBLISHED_SETUP_IDS:
    # A ZoneWatch not yet published has nothing to show: there is no
    # worker acknowledging it, no preflight, no armed-waiting-trigger queue
    # under the cutover - it is either silently watching_retest/evaluating
    # or it does not exist as a card-worthy thing yet. The old formatter's
    # "SETUP FORMING"/"QUEUED - worker acknowledgement pending" text
    # describes a pipeline stage that no longer runs for this path; sending
    # it is not cautious, it is just wrong. Suppress the card entirely -
    # _notify_digest_once skips post_or_edit_forming_card on empty text -
    # and let the first real card be PLAN PUBLISHED.
    return ""
  text = _ORIGINAL_FORMAT(*args, **kwargs)
  # Do not rewrite QUEUED → PLAN PUBLISHED; plan publish is silent on the card.
  return text.replace(
    # Legacy pre-algo-only footer; scanner no longer emits this, but keep
    # the rewrite so any retained cached formatter text stays consistent.
    "→ Review confirmation, SL &amp; TP before posting.",
    "→ Executor owns mechanical entry and risk enforcement.",
  )


def _outside_distance(record: ZoneWatch, mid_price: float) -> float:
  if mid_price > record.high:
    return float(mid_price - record.high)
  if mid_price < record.low:
    return float(record.low - mid_price)
  return 0.0


def _eval_skip_outside_price(symbol: str) -> float:
  """Furthest outside distance that could still be executable.

  Reaction zones need inside (chase=0); range scalp may chase up to the
  configured HFS maximum. Anything beyond that cannot activate — skip the
  Redis presence write storm that was starving Telegram on prod.
  """
  from app.autotrade import units

  pip = units.pip_size(symbol)
  tolerance = max(
    0.0,
    float(runtime_config.execution.entry.contract_tolerance_pips) * pip,
  )
  chase = max(0.0, float(scalp_maximum_chase_pips(runtime_config))) * pip
  # Small cushion so near-edge approaches still get presence updates.
  return max(tolerance, chase) + max(pip * 5.0, 0.5)


# Prod dig 2026-08-12: 118/137 watches were >12h old and far from market,
# re-evaluated every 2s. Expire those so the active index stays near price.
_ZONE_STALE_AGE_SECONDS = 12 * 3600
_ZONE_STALE_OUTSIDE_PRICE = 25.0
_SPOT_ZONE_BANDS: dict[str, list[tuple[float, float]]] = {}
_SPOT_IN_ZONE_EVALUATED: dict[str, bool] = {}
SPOT_MIN_INTERVAL_S = 3.0


def _cache_spot_zone_bands(symbol: str, records: list) -> None:
  bands: list[tuple[float, float]] = []
  for record in records:
    try:
      low = float(record.low)
      high = float(record.high)
    except (TypeError, ValueError):
      continue
    if high < low:
      low, high = high, low
    bands.append((low, high))
  _SPOT_ZONE_BANDS[symbol] = bands


def quote_inside_cached_spot_zone(symbol: str, mid: float) -> bool:
  if not math.isfinite(mid):
    return False
  for low, high in _SPOT_ZONE_BANDS.get(symbol, ()):
    if low <= mid <= high:
      return True
  return False


async def evaluate_active_zone_watches(
  client: Any,
  *,
  symbol: str,
  event_ts: str,
  source: RedisOHLCSource | None = None,
) -> StrategyMatch | None:
  records = await list_active_zone_watches(client, symbol=symbol)
  _cache_spot_zone_bands(symbol, records)
  if not records:
    return None
  quote = await _load_quote(client, symbol)
  if quote is None:
    return None
  bid, ask, _ts = quote
  mid = (bid + ask) / 2.0
  skip_outside = _eval_skip_outside_price(symbol)
  now = _now()
  # Near-first so an executable zone is tried before far junk.
  ranked = sorted(
    records,
    key=lambda item: (_outside_distance(item, mid), -item.score, item.zone_id),
  )
  for index, record in enumerate(ranked):
    if index and index % 8 == 0:
      await asyncio.sleep(0)
    outside = _outside_distance(record, mid)
    age_s = now - int(record.discovered_at or now)
    if (
      outside >= _ZONE_STALE_OUTSIDE_PRICE
      and age_s >= _ZONE_STALE_AGE_SECONDS
      and record.state not in TERMINAL_ZONE_WATCH_STATES | LOCKED_ZONE_WATCH_STATES
    ):
      try:
        await transition_zone_watch(
          client,
          record.zone_id,
          EXPIRED,
          reason_code="stale_far_from_market",
        )
      except Exception:
        log.exception(
          "failed expiring stale zone_id=%s outside=%.2f age_s=%s",
          record.zone_id,
          outside,
          age_s,
        )
      continue
    if outside > skip_outside:
      continue
    activated = await _evaluate_record(
      client,
      record,
      event_ts=event_ts,
      quote=quote,
      source=source,
    )
    if activated is not None:
      return activated
  return None


async def evaluate_spot_zone_watches(
  client: Any,
  *,
  symbol: str,
  event_ts: str,
) -> StrategyMatch | None:
  """Spot-loop eval: one OHLC cache so every active zone shares M1/M5."""
  source = RedisOHLCSource(client)
  source.begin_closed_bar_cache()
  try:
    return await evaluate_active_zone_watches(
      client,
      symbol=symbol,
      event_ts=event_ts,
      source=source,
    )
  finally:
    source.end_closed_bar_cache()


async def zone_watch_execution_loop() -> None:
  """Wake retained zones on sub-second spot updates.

  Closed M1 bars are handled by ``bar_event_dispatcher_loop`` so this loop
  does not subscribe to ``bars:new``.
  """
  if not runtime_config.runtime.auto_trade.enabled:
    return
  client = redis_state.get_client()
  pubsub = client.pubsub()
  await pubsub.subscribe("spots:new")
  last_spot_eval_monotonic: dict[str, float] = {}
  log.info(
    "ZoneWatch spot loop started channel=spots:new spot_min_interval_s=%.2f",
    SPOT_MIN_INTERVAL_S,
  )
  try:
    async for message in pubsub.listen():
      if message.get("type") != "message":
        continue
      raw = message.get("data")
      text = raw.decode() if isinstance(raw, bytes) else str(raw)
      try:
        parts = text.split(":", 1)
        if len(parts) != 2:
          continue
        symbol = parts[0].upper()
        now = time.monotonic()
        last = last_spot_eval_monotonic.get(symbol, 0.0)
        if now - last < SPOT_MIN_INTERVAL_S:
          quote = await _load_quote(client, symbol)
          if quote is None:
            continue
          bid, ask, _ts = quote
          mid = (bid + ask) / 2.0
          inside = quote_inside_cached_spot_zone(symbol, mid)
          if not inside:
            _SPOT_IN_ZONE_EVALUATED[symbol] = False
            continue
          if _SPOT_IN_ZONE_EVALUATED.get(symbol):
            continue
          _SPOT_IN_ZONE_EVALUATED[symbol] = True
        last_spot_eval_monotonic[symbol] = now
        await evaluate_spot_zone_watches(
          client,
          symbol=symbol,
          event_ts=parts[1],
        )
      except Exception:
        log.exception(
          "ZoneWatch evaluation failed channel=spots:new event=%s",
          text,
        )
  finally:
    await pubsub.unsubscribe("spots:new")
    await pubsub.close()


def install_zone_execution_cutover() -> None:
  """Install once after scanner/worker imports; safe for tests and reloads."""
  global _INSTALLED, _ORIGINAL_SYNC, _ORIGINAL_FORMAT, _ORIGINAL_DIRECT_PUBLISH
  if _INSTALLED:
    return
  from app.analysis import scanner
  from app.autotrade import worker

  _ORIGINAL_SYNC = scanner._sync_strategy_match
  _ORIGINAL_FORMAT = scanner._format_detection
  _ORIGINAL_DIRECT_PUBLISH = worker.try_publish_executable_signal
  scanner._sync_strategy_match = _sync_strategy_match_cutover
  scanner._format_detection = _format_detection_cutover
  worker.try_publish_executable_signal = _safe_direct_publish
  _INSTALLED = True
  log.info(
    "ZoneWatch cutover installed: retained-zone waiting, A/B trigger policy, "
    "active width contract, safe direct publication"
  )
