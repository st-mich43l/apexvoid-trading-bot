"""S13C: Go opportunity -> existing Algo Bot policy pipeline, one scope at a time.

A durable, decoded Go opportunity (``analysis_client``) becomes an ordinary
``StrategyMatch`` in the *same* Redis store the legacy scanner writes, so every
existing gate downstream — arbitration, killzone/news guards, execution policy
(min R:R, stop, routing), sizing, duplicate protection, the TradePlan V8 build
and its publish-time authority fence — runs unchanged. This module decides
nothing about risk and duplicates no detector; it only translates facts.

Hard rules encoded here:

* **Reviewed scopes only.** ``REVIEWED_SCOPES`` is the allowlist; anything else
  is recorded as a decision and dropped, never guessed at.
* **Fence first.** No match is written unless the authority fence says Go owns
  the scope *now*; the match carries the accepted epoch, and the plan-publish
  guard re-checks it (a rollback between here and publish blocks the plan).
* **Fail closed on missing facts.** No ``technical_context``, no observed
  timeframe, unknown instrument, expired opportunity, non-directional
  geometry, or ``multiple_matches_enabled`` off (which would make the shared
  single-match key ambiguous between publishers) -> rejected with a reason.
* **No auto-trade via Manual Algo's analysis-gate bypass.** Matches take the
  normal automatic path; nothing here touches ``bypass_analysis_gates``.

Known, documented semantic gaps (each must be reconciled in replay/shadow
*before* any acceptance is recorded — they are not papered over):

* ``htf_bias`` is left empty: Go's bias is primary-timeframe structural bias,
  not the H1/H4 bias the legacy field means. ``strategy_mode`` /
  ``bias_relationship`` use it, tagged ``bias_source:go_primary_tf``.
* ``confluence`` is the count of Go evidence codes (Go has no legacy integer).
* The worker's opposing-barrier / target-room checks still recompute Python
  zones from Redis OHLC; that duplicate technical computation is a *retained*
  dependency, blocking deletion of those modules, not a Go fact.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.analysis_client.authority import (
  CATALOG_TAG,
  EPOCH_TAG,
  GO_ORIGIN_TAG,
  AuthorityFence,
  get_fence,
)
from app.analysis_client.freshness import FreshnessLimits, evaluate_freshness
from app.analysis_client.models import InvalidationEnvelope, OpportunityEnvelope
from app.analysis_client.repository import LifecycleResult, PostgresAnalysisOpportunityRepository
from app.autotrade import units
from app.autotrade.go_plan_cancel import SOURCE_EXPIRED, SOURCE_INVALIDATED, plan_id_for_match, request_plan_cancel
from app.autotrade.execution_policy import classify_tier, risk_multiplier_for_tier, strategy_family
from app.autotrade.multi_match import deserialize_matches, serialize_matches, strategy_matches_key
from app.autotrade.setup_lifecycle import (
  CONFIRMED,
  DISCOVERED,
  EXPIRED,
  FORMING,
  INVALIDATED,
  TOUCHED,
  WATCHING,
  SetupLifecycleError,
  create_setup,
  load_setup,
  transition_setup,
)
from app.autotrade.strategy_match import STRATEGY_MATCH_VERSION, StrategyMatch
from app.analysis.execution_eligibility import (
  EXECUTION_ELIGIBILITY_VERSION,
  STATIC_ELIGIBLE,
  ExecutionEligibility,
)

log = logging.getLogger(__name__)

MODE = "go"
_PRE_CONFIRMED = (DISCOVERED, WATCHING, TOUCHED, FORMING, CONFIRMED)
_TERMINAL_OR_LIVE = {"plan_built", "plan_published", "invalidated", "expired", "cancelled"}


@dataclass(frozen=True, slots=True)
class ScopeProfile:
  """Reviewed translation of one catalog strategy into legacy policy terms."""

  catalog_id: str
  legacy_strategy: str      # canonical legacy name policy/taxonomy keys on
  structural_kind: str      # legacy structural_kind for the V8 source_structure
  direction: str            # the only direction this scope may produce


# Zone-anchored primitives whose entry band *is* the zone: the translation is
# exact. Level/trendline/range/scalp scopes are deliberately absent until each
# has a reviewed profile (their legacy semantics are not derivable from the
# current contract).
REVIEWED_SCOPES: dict[str, ScopeProfile] = {
  "supply": ScopeProfile("supply", "Supply Demand", "supply", "SELL"),
  "demand": ScopeProfile("demand", "Supply Demand", "demand", "BUY"),
}


class AdapterRejection(Exception):
  def __init__(self, code: str, message: str = ""):
    super().__init__(f"{code}: {message}" if message else code)
    self.code = code
    self.message = message


def _thesis_id(symbol: str, catalog_id: str, direction: str, opportunity_id: str) -> str:
  digest = hashlib.sha256(f"go|{symbol}|{catalog_id}|{direction}|{opportunity_id}".encode()).hexdigest()
  return f"go-thesis-{digest[:24]}"


def match_id_for(opportunity_id: str) -> str:
  return f"go_{opportunity_id}"


def build_strategy_match(
  event: OpportunityEnvelope, *, profile: ScopeProfile, epoch: int, now: int,
) -> StrategyMatch:
  """Pure translation. Raises AdapterRejection rather than approximating."""
  payload = event.payload
  tech = payload.technical_context
  if tech is None:
    raise AdapterRejection("technical_context_unavailable", "Go supplied no policy inputs; Python must not recompute them")
  if not payload.timeframe:
    raise AdapterRejection("missing_observed_timeframe")
  if payload.timeframe.upper() != "M5":
    raise AdapterRejection("confirmation_timeframe_unreviewed")
  reaction = tech.confirmation
  if reaction is None:
    raise AdapterRejection("reaction_confirmation_unavailable", "resting zones are technical observations, not confirmed trades")
  if reaction.confirmation_bar_time != tech.reference_time:
    raise AdapterRejection("reaction_not_current_observation")
  if payload.direction != profile.direction:
    raise AdapterRejection("direction_scope_mismatch", f"{profile.catalog_id} produces {profile.direction}, got {payload.direction}")
  if now >= payload.expires_at:
    raise AdapterRejection("opportunity_expired")
  symbol = payload.symbol.upper()
  try:
    pip = float(units.pip_size(symbol))
  except KeyError:
    raise AdapterRejection("unknown_instrument", symbol) from None
  if not (pip > 0 and math.isfinite(pip)):
    raise AdapterRejection("bad_pip_size", symbol)

  low, high = float(payload.entry.low), float(payload.entry.high)
  if not high > low:
    raise AdapterRejection("degenerate_entry_band")
  direction = payload.direction
  proximal = low if direction == "SELL" else high     # edge price reaches first
  mid = (low + high) / 2.0
  targets_pips: list[int] = []
  for target in payload.targets:
    distance = (proximal - target.price.price) if direction == "SELL" else (target.price.price - proximal)
    pips = round(distance / pip)
    if pips < 1:
      raise AdapterRejection("target_not_beyond_entry", f"{target.price.price}")
    targets_pips.append(int(pips))
  targets_pips = sorted(set(targets_pips))
  stop = float(payload.invalidation.price)
  stop_pips = abs(proximal - stop) / pip
  if stop_pips <= 0:
    raise AdapterRejection("degenerate_stop")

  bias = tech.bias.direction if tech.bias else None
  relation = "neutral" if bias is None else ("with_bias" if bias == direction else "counter_bias")
  higher = next((item for tf in ("H1", "H4") for item in tech.higher_timeframes if item.timeframe == tf), None)
  htf_bias = ("up" if higher.direction == "BUY" else "down") if higher is not None else ""
  if higher is None:
    raise AdapterRejection("higher_timeframe_bias_unavailable", "no fresh confirmed H1/H4 structure")
  reasons = tuple(item.code for item in payload.evidence)
  confluence = len(reasons)
  legacy = profile.legacy_strategy
  family = strategy_family(legacy)
  tier = classify_tier(confluence=confluence, strategy=legacy)
  risk_multiplier = risk_multiplier_for_tier(tier)
  prices = [t.price.price for t in payload.targets]
  farthest = max(prices) if direction == "BUY" else min(prices)
  thesis = _thesis_id(symbol, profile.catalog_id, direction, payload.id)
  tags = (
    GO_ORIGIN_TAG,
    f"{CATALOG_TAG}{profile.catalog_id}",
    f"{EPOCH_TAG}{epoch}",
    f"go_opportunity:{payload.id}",
    f"kind:{profile.structural_kind}",
    f"bias:{relation}",
    "bias_source:go_primary_tf",
    f"go_reaction:{reaction.reaction_type}",
    f"htf_bias_source:go_{higher.timeframe}" if higher is not None else "htf_bias_unavailable",
  )
  eligibility = ExecutionEligibility(
    version=EXECUTION_ELIGIBILITY_VERSION,
    allowed=True,
    state=STATIC_ELIGIBLE,
    reason_code="go_geometry_static_eligible",
    message="Go entry/invalidation/targets are directionally consistent; min R:R and barriers are enforced by execution policy",
    hard_block=False,
    direction=direction,
    entry_low=low,
    entry_high=high,
    planned_entry_price=mid,
    fitted_targets_pips=tuple(targets_pips),
    effective_target_pips=float(targets_pips[-1]),
    reward_risk=round(targets_pips[-1] / stop_pips, 4),
    bias_relationship=relation,
    calculated_at=now,
    measured={"source": "go", "stop_pips": round(stop_pips, 3)},
  )
  return StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id=match_id_for(payload.id),
    symbol=symbol,
    source_tf=payload.timeframe.upper(),
    event_ts=str(tech.reference_time),
    issued_at=payload.created_at,
    expires_at=payload.expires_at,
    strategy=legacy,
    strategy_mode=relation,
    direction=direction,
    key_level=mid,
    entry_low=low,
    entry_high=high,
    current_price=float(tech.reference_price),
    confluence=confluence,
    reasons=reasons,
    atr=float(tech.atr),
    structure_swing=stop,
    targets_pips=tuple(targets_pips),
    tags=tags,
    absolute_target_price=float(farthest),
    tier=tier,
    risk_multiplier=risk_multiplier,
    family=family,
    structural_source=f"go:{profile.catalog_id}",
    zone_id=reaction.zone_id,
    structural_zone_id=reaction.zone_id,
    structural_zone_low=low,
    structural_zone_high=high,
    structural_kind=profile.structural_kind,
    structural_timeframe=payload.timeframe.upper(),
    touch_bar_ts=str(reaction.touch_bar_time),
    confirmation_bar_ts=str(reaction.confirmation_bar_time),
    reaction_type=reaction.reaction_type,
    m5_confirmation_bar_ts=str(reaction.confirmation_bar_time),
    m5_reaction_type=reaction.reaction_type,
    htf_bias=htf_bias,
    regime_kind="",
    bias_relationship=relation,
    execution_eligibility=eligibility,
    thesis_id=thesis,
  )


class GoOpportunityPolicy:
  """Consumer hook: durable Go lifecycle events -> matches, under the fence."""

  def __init__(
    self,
    repository: PostgresAnalysisOpportunityRepository,
    *,
    fence: AuthorityFence | None = None,
    client_factory: Callable[[], Any] | None = None,
    clock: Callable[[], float] = time.time,
    multiple_matches_enabled: Callable[[], bool] | None = None,
    freshness_limits: Callable[[], FreshnessLimits] | None = None,
  ):
    self._repository = repository
    self._fence = fence
    self._client_factory = client_factory
    self._clock = clock
    self._multiple = multiple_matches_enabled
    self._limits = freshness_limits

  def _client(self):
    if self._client_factory is not None:
      return self._client_factory()
    from app.persistence import redis_state
    return redis_state.get_client()

  def _fence_instance(self) -> AuthorityFence:
    return self._fence or get_fence()

  def _multiple_enabled(self) -> bool:
    if self._multiple is not None:
      return self._multiple()
    from app.core.config import runtime_config
    return bool(runtime_config.strategies.matching.multiple_matches_enabled)

  def _freshness_limits(self) -> FreshnessLimits:
    if self._limits is not None:
      return self._limits()
    from app.core.config import runtime_config
    authority = runtime_config.analysis.technical_authority
    return FreshnessLimits(authority.max_event_age_seconds, authority.max_delivery_lag_seconds)

  async def _decide(self, event: OpportunityEnvelope, outcome: str, reason: str, **details: Any) -> None:
    await self._repository.record_shadow_decision(
      opportunity_id=event.payload.id, event_id=event.event_id, outcome=outcome, reason=reason,
      details={"strategy": event.payload.strategy, "symbol": event.payload.symbol, **details}, mode=MODE,
    )

  async def on_creation(
    self, event: OpportunityEnvelope, result: LifecycleResult, *, published_at: int | None = None,
  ) -> str:
    """Idempotent: safe to re-run on a redelivered creation event.

    ``published_at`` is the Kafka record's publish time (epoch seconds) when the
    broker supplied one; the S14B freshness gate falls back to the envelope's
    ``produced_at``. Only a *new* confirmed observation made after the scope's
    durable go-effective boundary, still inside its event-age, delivery-lag and
    technical-expiry limits, may become a match: a restart or backlog never
    trades history.

    Handles ``created`` and ``duplicate_delivery`` (a redelivery after a failed
    earlier attempt — exceptions propagate so the Kafka offset is not
    committed and the event returns), but only while the durable ledger still
    says the opportunity is active: a terminated opportunity is never
    re-adapted by a late redelivery. Match write, setup creation and decision
    rows are all idempotent by identity.
    """
    if result.disposition not in {"created", "duplicate_delivery"}:
      return "ignored_" + result.disposition
    payload = event.payload
    if await self._repository.opportunity_state(payload.id) != "active":
      return "ignored_not_active"
    profile = REVIEWED_SCOPES.get(payload.strategy)
    if profile is None:
      await self._decide(event, "not_adapted", "scope_not_reviewed")
      return "not_adapted"
    try:
      decision = await self._fence_instance().authorize_go_publication(payload.symbol, profile.catalog_id)
    except Exception as exc:  # noqa: BLE001 - fail closed
      await self._decide(event, "not_adapted", f"authority_unavailable:{type(exc).__name__}")
      return "not_adapted"
    if not decision.allowed:
      await self._decide(event, "not_adapted", f"not_go_owner:{decision.reason}", owner=decision.owner, epoch=decision.epoch)
      return "not_adapted"
    if not self._multiple_enabled():
      await self._decide(event, "not_adapted", "multiple_matches_disabled")
      return "not_adapted"
    now = int(self._clock())
    client = self._client()
    # A redelivery after this opportunity was already adapted (e.g. the process
    # died between the match write and its decision row) finishes the same
    # idempotent write; it is not "stale" merely because time has passed.
    already_adapted = any(
      m.match_id == match_id_for(payload.id)
      for m in deserialize_matches(await client.get(strategy_matches_key(payload.symbol)))
    )
    verdict = evaluate_freshness(
      observed_at=payload.created_at, expires_at=payload.expires_at, produced_at=event.produced_at,
      published_at=published_at, consumed_at=now, boundary=decision.boundary, limits=self._freshness_limits(),
    )
    if not verdict.ok and not already_adapted:
      await self._decide(event, "not_adapted", verdict.code, owner=decision.owner, epoch=decision.epoch, **verdict.details())
      return "not_adapted"
    try:
      match = build_strategy_match(event, profile=profile, epoch=decision.epoch, now=now)
    except AdapterRejection as exc:
      await self._decide(event, "rejected", exc.code, message=exc.message)
      return "rejected"

    await self._advance_setup(client, match)
    await self._store_match(client, match, now)
    await self._decide(
      event, "match_written", "go_owned_scope", match_id=match.match_id, epoch=decision.epoch, **verdict.details(),
    )
    log.info("Go opportunity adapted opportunity=%s match=%s epoch=%s", payload.id, match.match_id, decision.epoch)
    return "match_written"

  async def on_terminal(self, event: InvalidationEnvelope, result: LifecycleResult) -> str:
    # Withdrawal is idempotent, so it runs for every disposition — including a
    # redelivery after an earlier attempt failed part-way.
    payload = event.payload
    if payload.strategy not in REVIEWED_SCOPES:
      return "not_adapted"
    client = self._client()
    match_id = match_id_for(payload.opportunity_id)
    key = strategy_matches_key(payload.symbol)
    matches = deserialize_matches(await client.get(key))
    kept = [m for m in matches if m.match_id != match_id]
    if len(kept) != len(matches):
      if kept:
        await client.set(key, serialize_matches(kept), ex=max(60, max(m.expires_at for m in kept) - int(self._clock())))
      else:
        await client.delete(key)
    record = await load_setup(client, match_id)
    if record is not None and record.state not in _TERMINAL_OR_LIVE:
      target = EXPIRED if payload.reason_code.upper() == "SETUP_EXPIRED" else INVALIDATED
      try:
        await transition_setup(client, match_id, target, reason_code=f"go_{payload.reason_code.lower()}")
      except SetupLifecycleError:
        log.exception("Go terminal could not advance setup %s to %s", match_id, target)
    # S14B: an invalidated/expired opportunity must not keep a queued or
    # unfilled plan alive. The cancel intent is a tombstone (also stops a plan
    # being published concurrently); open positions are never touched.
    if len(kept) != len(matches) or record is not None:
      expired = payload.reason_code.upper() == "SETUP_EXPIRED"
      await request_plan_cancel(
        client, plan_id_for_match(match_id), reason=payload.reason_code.lower(),
        source=SOURCE_EXPIRED if expired else SOURCE_INVALIDATED,
        requested_at=int(self._clock()), opportunity_id=payload.opportunity_id,
      )
    return "match_withdrawn"

  @staticmethod
  async def _advance_setup(client: Any, match: StrategyMatch) -> None:
    record, _ = await create_setup(
      client, setup_id=match.match_id, thesis_id=match.thesis_id, symbol=match.symbol,
      source_structure_id=match.structural_zone_id, formation_timeframe=match.structural_timeframe,
      expires_at=match.expires_at,
    )
    if record.state not in _PRE_CONFIRMED:
      return
    for state in _PRE_CONFIRMED[_PRE_CONFIRMED.index(record.state) + 1:]:
      record, _ = await transition_setup(client, match.match_id, state, reason_code="go_opportunity")

  @staticmethod
  async def _store_match(client: Any, match: StrategyMatch, now: int) -> None:
    key = strategy_matches_key(match.symbol)
    current = deserialize_matches(await client.get(key))
    merged = [m for m in current if m.match_id != match.match_id] + [match]
    await client.set(key, serialize_matches(merged), ex=max(60, max(m.expires_at for m in merged) - now))
