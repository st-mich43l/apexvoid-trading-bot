"""Live ZoneWatch -> executable signal cutover.

This module is deliberately installed once from ``app.main`` after scanner and
worker imports are complete.  It replaces the old scanner handoff at the
function boundary without duplicating detector logic:

  detection result -> retained ZoneWatch (no setup, card, or ready event)
  -> side-aware quote enters zone
  -> A-grade publishes immediately; B-grade requires a fresh episode-scoped M1
  -> setup lifecycle begins only for the currently executable signal

The old ready stream remains an emergency durable fallback only when an
unexpected direct-publication exception occurs after setup creation.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import asyncio
import json
import logging
import math
import time
from typing import Any

from app.analysis.confluence_zone import (
  BandKind,
  classify_band_kind,
  confluence_zone_id,
  validate_zone_width,
)
from app.analysis.m1_trigger import evaluate_m1_trigger_window, latest_eligible_m1_bar_ts
from app.analysis.ohlc_source import RedisOHLCSource, window_for_timeframe
from app.autotrade.execution_confirmation import executable_quote_in_zone
from app.autotrade.multi_match import dedupe_matches, serialize_matches, strategy_matches_key
from app.autotrade.strategy_match import StrategyMatch, strategy_match_key
from app.autotrade.strategy_match_ready import enqueue_strategy_match_ready
from app.autotrade.strategy_taxonomy import is_reaction_strategy
from app.autotrade.zone_watch import (
  DISCOVERED,
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
from app.core.config import settings
from app.persistence import redis_state


log = logging.getLogger(__name__)

_CANDIDATE_KEY_PREFIX = "analysis:zone_watch_candidate"
_WIDTH_TELEMETRY_KEY_PREFIX = "analysis:zone_width:last"
_CANDIDATE_TTL_SECONDS = 7 * 24 * 3600
_AUTHORITATIVE_REACTIONS = {
  "rejection_choch",
  "sweep_reclaim",
  "strong_reclaim",
  "wick_rejection",
  "rejection",
  "reclaim",
}
_PUBLISHED_SETUP_IDS: set[str] = set()
_INSTALLED = False
_ORIGINAL_SYNC: Any = None
_ORIGINAL_FORMAT: Any = None
_ORIGINAL_DIRECT_PUBLISH: Any = None


def candidate_key(zone_id: str) -> str:
  return f"{_CANDIDATE_KEY_PREFIX}:{zone_id}"


def width_telemetry_key(symbol: str, zone_id: str) -> str:
  return f"{_WIDTH_TELEMETRY_KEY_PREFIX}:{symbol.upper()}:{zone_id}"


def _now() -> int:
  return int(datetime.now(timezone.utc).timestamp())


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
  if _now() - ts > max(0, int(settings.spot_fresh_secs)):
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
    float(settings.auto_trade_entry_contract_tolerance_pips) * pip,
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


def _decisive_break(record: ZoneWatch, evidence: Any) -> bool:
  """True when price closed beyond the zone's far/invalidating edge.

  BUY (demand) is approached from above and fails by closing below `low`;
  SELL (supply) is approached from below and fails by closing above
  `high`. Exiting back out the *near* edge (a bounce) is not a break - see
  zone_watch.record_zone_presence's decisive_break param.
  """
  price = evidence.executable_quote
  if price is None:
    return False
  if str(record.direction).upper() == "BUY":
    return price < record.low
  return price > record.high


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
  if band_kind == BandKind.STRUCTURAL_ZONE and not settings.scanner_zone_width_gate_enabled:
    return True
  return bool(width.eligible)


async def _save_candidate(client: Any, zone_id: str, match: StrategyMatch) -> None:
  await client.set(candidate_key(zone_id), match.to_json(), ex=_CANDIDATE_TTL_SECONDS)


async def _load_candidate(client: Any, zone_id: str) -> StrategyMatch | None:
  raw = await client.get(candidate_key(zone_id))
  return None if raw is None else StrategyMatch.from_json(raw)


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
    cfg=settings,
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
    existing = await worker.resolve_existing_v7_state(client, match)
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

  existing = await worker.resolve_existing_v7_state(client, match)
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
    return None
  evidence = _quote_evidence(record, quote)
  if not evidence.inside:
    await record_zone_presence(
      client,
      record.zone_id,
      inside=False,
      now=now,
      decisive_break=_decisive_break(record, evidence),
    )
    return None
  match = replace(
    match,
    issued_at=now,
    expires_at=now + max(60, int(settings.auto_trade_strategy_match_max_age_seconds)),
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
    # Reaction strategies never use the ready stream once executable —
    # leave them watching and retry on the next M1/quote cycle.
    if is_reaction_strategy(match.strategy):
      await record_zone_presence(client, record.zone_id, inside=False, now=now)
      return None
    # Exceptional fallback only for non-reaction workflows.
    stream_id = await enqueue_strategy_match_ready(client, match, recovery=True)
    if stream_id is not None:
      return match

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
  await record_zone_presence(client, record.zone_id, inside=False, now=now)
  return None


async def _m1_trigger_for_zone(
  client: Any,
  record: ZoneWatch,
) -> Any | None:
  source = RedisOHLCSource(client)
  frame = await source.window(record.symbol, "M1", window_for_timeframe("M1"))
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
    cfg=settings,
  )
  latest = latest_eligible_m1_bar_ts(
    frame,
    earliest_bar_ts=int(record.zone_entered_at) + 1,
    after_bar_ts=record.last_evaluated_m1_ts,
  )
  if latest is not None:
    await mark_m1_evaluated(client, record.zone_id, int(latest))
  return trigger


async def _evaluate_record(
  client: Any,
  record: ZoneWatch,
  *,
  event_ts: str,
) -> StrategyMatch | None:
  if record.state in TERMINAL_ZONE_WATCH_STATES | LOCKED_ZONE_WATCH_STATES:
    return None
  quote = await _load_quote(client, record.symbol)
  if quote is None:
    return None
  evidence = _quote_evidence(record, quote)
  record, _entered = await record_zone_presence(
    client,
    record.zone_id,
    inside=evidence.inside,
    now=quote[2],
    htf_evidence=record.source_timeframe in {"H1", "M15"},
    decisive_break=_decisive_break(record, evidence),
  )
  if (
    not evidence.inside
    or record.state in TERMINAL_ZONE_WATCH_STATES | LOCKED_ZONE_WATCH_STATES
  ):
    return None
  match = await _load_candidate(client, record.zone_id)
  if match is None:
    return None
  if record.grade == GRADE_B:
    # M1 refines entry timing/anchor when a qualifying candle is already
    # there - it must never be a requirement to publish at all. A zone the
    # system has already decided is executable (quote inside, grade B or
    # better) does not stop being executable just because no M1 pattern has
    # printed yet; gating on that turned "confirm the entry timing" into
    # "confirm whether to enter", which is not M1's job.
    trigger = await _m1_trigger_for_zone(client, record)
    if trigger is not None:
      match = replace(
        match,
        confirmation_bar_ts=str(int(trigger.bar_ts)),
        reaction_type=str(trigger.pattern),
        touch_bar_ts=str(record.zone_entered_at or int(trigger.bar_ts)),
      )
  return await _activate_match(client, record, match, event_ts=event_ts)


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
      continue

    grade = _grade(result, source_tf)
    if grade not in {GRADE_A, GRADE_B}:
      continue
    match, _reason, _measured = scanner._build_one_strategy_match(
      symbol,
      tf,
      event_ts,
      ctx,
      result,
    )
    if match is None:
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
    await _save_candidate(client, zone_id, replace(
      match,
      confluence_zone_id=match.confluence_zone_id or zone_id,
    ))
    quote = await _load_quote(client, symbol)
    if quote is None:
      continue
    evidence = _quote_evidence(record, quote)
    record, _ = await record_zone_presence(
      client,
      zone_id,
      inside=evidence.inside,
      now=quote[2],
      htf_evidence=source_tf in {"H1", "M15"},
      decisive_break=_decisive_break(record, evidence),
    )
    if (
      not evidence.inside
      or record.state in TERMINAL_ZONE_WATCH_STATES | LOCKED_ZONE_WATCH_STATES
    ):
      continue
    if record.grade == GRADE_B:
      # Same reasoning as _evaluate_record below: M1 refines entry
      # timing/anchor, it must never gate whether an already-executable
      # zone gets to publish at all.
      trigger = await _m1_trigger_for_zone(client, record)
      if trigger is not None:
        match = replace(
          match,
          confirmation_bar_ts=str(int(trigger.bar_ts)),
          reaction_type=str(trigger.pattern),
          touch_bar_ts=str(record.zone_entered_at or int(trigger.bar_ts)),
        )
    ready.append((0 if record.grade == GRADE_A else 1, record, match))

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
  return text.replace(
    "🟡 <b>QUEUED</b> · worker acknowledgement pending",
    "🟢 <b>PLAN PUBLISHED</b> · TradePlan V7 sent to executor",
  ).replace(
    # Legacy pre-algo-only footer; scanner no longer emits this, but keep
    # the rewrite so any retained cached formatter text stays consistent.
    "→ Review confirmation, SL &amp; TP before posting.",
    "→ Executor owns mechanical entry and risk enforcement.",
  )


async def evaluate_active_zone_watches(
  client: Any,
  *,
  symbol: str,
  event_ts: str,
) -> StrategyMatch | None:
  for record in await list_active_zone_watches(client, symbol=symbol):
    activated = await _evaluate_record(client, record, event_ts=event_ts)
    if activated is not None:
      return activated
  return None


async def zone_watch_execution_loop() -> None:
  """M1 wake-up for retained zones; no execution queue while merely waiting."""
  if not settings.auto_trade_enabled:
    return
  client = redis_state.get_client()
  pubsub = client.pubsub()
  await pubsub.subscribe("bars:new")
  log.info("ZoneWatch direct execution loop started")
  try:
    async for message in pubsub.listen():
      if message.get("type") != "message":
        continue
      raw = message.get("data")
      text = raw.decode() if isinstance(raw, bytes) else str(raw)
      parts = text.split(":", 2)
      if len(parts) != 3 or parts[1].upper() != "M1":
        continue
      try:
        await evaluate_active_zone_watches(
          client,
          symbol=parts[0].upper(),
          event_ts=parts[2],
        )
      except Exception:
        log.exception("ZoneWatch M1 evaluation failed event=%s", text)
  finally:
    await pubsub.unsubscribe("bars:new")
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
