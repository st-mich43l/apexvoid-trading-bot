"""Go opportunity -> existing Algo Bot policy pipeline, one scope at a time.

A durable, decoded Go opportunity (``analysis_client``) becomes an ordinary
``StrategyMatch`` in Redis, so every execution gate downstream — arbitration,
killzone/news guards, execution policy (min R:R, stop, routing), sizing,
duplicate protection and TradePlan V8 — runs unchanged. This module decides
nothing about technical structure or risk; it only translates the Go-owned
facts.

Hard rules encoded here:

* **Reviewed scopes only.** ``REVIEWED_SCOPES`` is the allowlist; anything else
  is recorded as a decision and dropped, never guessed at.
* **Live Go source.** Kafka delivery is the technical-source boundary. No
  per-scope approval table is consulted by this path.
* **Fail closed on missing facts.** No ``technical_context``, no observed
  timeframe, unknown instrument, expired opportunity, non-directional
  geometry, or ``multiple_matches_enabled`` off (which would make the shared
  single-match key ambiguous between publishers) -> rejected with a reason.
* **No auto-trade via Manual Algo's analysis-gate bypass.** Matches take the
  normal automatic path; nothing here touches ``bypass_analysis_gates``.

Translation notes are explicit rather than inferred: ``htf_bias`` is selected
from the Go H1/H4 facts, ``confluence`` is the count of Go evidence codes, and
the worker's Go path does not load OHLC or run Python detectors. Python remains
responsible only for execution-time quote, spread, expiry, exposure, risk and
order checks.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from app.analysis_client.provenance import (
  CATALOG_TAG,
  GO_ORIGIN_TAG,
  CATALOG_STRATEGY_IDS,
)
from app.analysis_client.freshness import FreshnessLimits, evaluate_freshness
from app.analysis_client.models import ArbitrationEnvelope, InvalidationEnvelope, OpportunityEnvelope
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
  direction: str | None     # None means the Go strategy owns the direction
  allowed_timeframes: frozenset[str]
  strategy_mode: str
  requires_reaction: bool = False
  evidence_prefixes: tuple[str, ...] = ()


# This is deliberately an explicit adapter registry, rather than a generic
# Supply/Demand fallback.  The Go Candidate already owns each strategy's
# entry geometry, invalidation, targets, evidence, and observation timeframe;
# the profile only names the downstream policy taxonomy and the facts that
# must be present for that strategy.  Keeping one row per catalog ID makes an
# enabled Go strategy impossible to silently drop at the Kafka boundary.
REVIEWED_SCOPES: dict[str, ScopeProfile] = {
  "key_level": ScopeProfile("key_level", "Key Level", "key_level", None, frozenset({"M5"}), "go_m5_reaction", evidence_prefixes=("m5_key_level_",)),
  "confluence_zone": ScopeProfile("confluence_zone", "Confluence Zone", "confluence_zone", None, frozenset({"M5"}), "go_m5_confluence", evidence_prefixes=("m5_distinct_zone_overlap", "m5_confluence_reaction")),
  "supply": ScopeProfile("supply", "Supply Demand", "supply", "SELL", frozenset({"M5"}), "go_m5_zone", True, ("m5_supply_zone_",)),
  "demand": ScopeProfile("demand", "Supply Demand", "demand", "BUY", frozenset({"M5"}), "go_m5_zone", True, ("m5_demand_zone_",)),
  "order_block": ScopeProfile("order_block", "Order Block", "order_block", None, frozenset({"M5"}), "go_m5_order_block", evidence_prefixes=("m5_order_block_",)),
  "fvg": ScopeProfile("fvg", "FVG", "fvg", None, frozenset({"M5"}), "go_m5_fvg", evidence_prefixes=("m5_fvg_",)),
  "ifvg": ScopeProfile("ifvg", "iFVG", "ifvg", None, frozenset({"M5"}), "go_m5_ifvg", evidence_prefixes=("m5_ifvg_",)),
  "crt": ScopeProfile("crt", "CRT", "crt", None, frozenset({"M5"}), "go_h1_m5_crt", evidence_prefixes=("h1_impulse_range", "m5_range_sweep_reclaim")),
  "flip_zone": ScopeProfile("flip_zone", "Flip Zone", "flip_zone", None, frozenset({"M5"}), "go_m5_flip_zone", evidence_prefixes=("m5_flip_",)),
  "session_level": ScopeProfile("session_level", "Session Level", "session_level", None, frozenset({"M5"}), "go_m5_session_level", evidence_prefixes=("session_level_",)),
  "trendline": ScopeProfile("trendline", "Trendline", "trendline", None, frozenset({"M5"}), "go_m5_trendline", evidence_prefixes=("m5_trendline_",)),
  "range_edge": ScopeProfile("range_edge", "Range Edge Scalp", "range_edge", None, frozenset({"M5"}), "go_m5_range_edge", evidence_prefixes=("m5_canonical_range", "m5_repeated_edge_rejection")),
  "box_breakout": ScopeProfile("box_breakout", "Box Breakout", "box_breakout", None, frozenset({"M5"}), "go_m5_box_breakout", evidence_prefixes=("m5_box_compression", "m5_breakout_accepted", "m5_box_retest")),
  "momentum_ride": ScopeProfile("momentum_ride", "Momentum Ride", "momentum_ride", None, frozenset({"M5"}), "go_m5_momentum", evidence_prefixes=("m5_persistent_direction", "m5_low_overlap_displacement")),
  "snap_back": ScopeProfile("snap_back", "Snap-Back", "snap_back", None, frozenset({"M5"}), "go_m5_snap_back", evidence_prefixes=("m5_extended_from_key_level", "m5_reversal_close")),
  "liquidity_sweep": ScopeProfile("liquidity_sweep", "Liquidity Sweep", "liquidity_sweep", None, frozenset({"M5"}), "go_m5_liquidity_sweep", evidence_prefixes=("m5_liquidity_pool_swept", "m5_sweep_reclaimed", "m5_opposite_displacement")),
  # These strategies intentionally publish on the M1 observation boundary:
  # their Go implementations consume their M5 structure and own the M1
  # confirmation before Candidate creation.  Python must not run a second M1
  # detector after receiving the event.
  "range_sweep": ScopeProfile("range_sweep", "Range Sweep Scalp", "range_sweep", None, frozenset({"M1"}), "go_m5_m1_range_sweep", evidence_prefixes=("m5_range_context", "m1_edge_sweep", "m1_reclaim")),
  "impulse_pullback": ScopeProfile("impulse_pullback", "Impulse Pullback Scalp", "impulse_pullback", None, frozenset({"M1"}), "go_m5_m1_impulse_pullback", evidence_prefixes=("m5_qualified_impulse", "m1_bounded_pullback", "m1_continuation_trigger")),
  "scalp_breakout_retest": ScopeProfile("scalp_breakout_retest", "Breakout Retest Scalp", "scalp_breakout_retest", None, frozenset({"M1"}), "go_m5_m1_breakout_retest", evidence_prefixes=("m5_prebreakout_box", "m1_breakout_accepted", "m1_retest_confirmed")),
}

if frozenset(REVIEWED_SCOPES) != CATALOG_STRATEGY_IDS:
  raise RuntimeError("Go strategy catalog and Go-to-policy adapter registry are out of sync")


class AdapterRejection(Exception):
  def __init__(self, code: str, message: str = ""):
    super().__init__(f"{code}: {message}" if message else code)
    self.code = code
    self.message = message


# Incident 2026-09-29: with all 19 catalog strategies enabled at
# once, several strategies with no persistent Go-side zone object (their
# Evaluate() re-scans a sliding OHLC window on every bar rather than tracking
# a created-once Zone) produced dozens of distinct-ID opportunities for
# overlapping GBPJPY zones within minutes. Below, structural_id fell back to
# payload.id for every non-reaction strategy - unique by construction - so
# thesis_id was too, and the "one plan per structural thesis" claim this
# module's docstring promises was a silent no-op for 17 of 19 strategies.
# These two constants define how close two entry zones must be, for the same
# symbol+direction+strategy, to collapse into one thesis instead of one each.
_THESIS_BUCKET_ATR_FRACTION = 0.5
_THESIS_BUCKET_MIN_PIPS = 15


def _thesis_id(symbol: str, family: str, direction: str, zone_id: str) -> str:
  """Stable TradePlan thesis identity: what *structure* this is, not which bar
  confirmed it. Same rule and same bytes as the legacy scanner's
  ``structural_reaction_support.thesis_id`` (a parity test pins that), so a Go zone
  re-confirmed on later bars (the real replay shows up to 7 re-confirmations per
  zone, each under its own opportunity id) is ONE thesis, and the plan builder's
  active-thesis claim stops a second executable plan for it. Keying this on the
  opportunity id (the S13C behaviour) let every re-confirmation publish its own plan."""
  raw = "|".join(("thesis", "v1", symbol.upper(), family, direction.upper(), zone_id))
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def match_id_for(opportunity_id: str) -> str:
  return f"go_{opportunity_id}"


def build_strategy_match(
  event: OpportunityEnvelope, *, profile: ScopeProfile, now: int,
) -> StrategyMatch:
  """Pure translation. Raises AdapterRejection rather than approximating."""
  payload = event.payload
  tech = payload.technical_context
  if tech is None:
    raise AdapterRejection("technical_context_unavailable", "Go supplied no policy inputs; Python must not recompute them")
  if not payload.timeframe:
    raise AdapterRejection("missing_observed_timeframe")
  timeframe = payload.timeframe.upper()
  if timeframe not in profile.allowed_timeframes:
    raise AdapterRejection(
      "confirmation_timeframe_unreviewed",
      f"{profile.catalog_id} expects {sorted(profile.allowed_timeframes)}, got {timeframe}",
    )
  reaction = tech.confirmation
  if profile.requires_reaction and reaction is None:
    raise AdapterRejection("reaction_confirmation_unavailable", "this zone adapter requires Go's causal rejection confirmation")
  if reaction is not None and reaction.confirmation_bar_time != tech.reference_time:
    raise AdapterRejection("reaction_not_current_observation")
  if profile.direction is not None and payload.direction != profile.direction:
    raise AdapterRejection("direction_scope_mismatch", f"{profile.catalog_id} produces {profile.direction}, got {payload.direction}")
  evidence_codes = tuple(item.code for item in payload.evidence)
  quality = payload.quality
  quality_overall = float(quality.overall)
  quality_components = dict(quality.components)
  stop_envelope = payload.stop_envelope
  stop_envelope_floor_pips = None if stop_envelope is None else float(stop_envelope.floor_pips)
  stop_envelope_cap_pips = None if stop_envelope is None else float(stop_envelope.cap_pips)
  stop_envelope_desired_minimum_pips = (
    None if stop_envelope is None else float(stop_envelope.desired_minimum_pips)
  )
  stop_envelope_source = None if stop_envelope is None else str(stop_envelope.source)
  if profile.evidence_prefixes and not any(
    any(code.startswith(prefix) for prefix in profile.evidence_prefixes)
    for code in evidence_codes
  ):
    raise AdapterRejection(
      "strategy_evidence_mismatch",
      f"{profile.catalog_id} did not provide its own evidence contract",
    )
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
  reasons = evidence_codes
  confluence = len(reasons)
  legacy = profile.legacy_strategy
  family = strategy_family(legacy)
  tier = classify_tier(confluence=confluence, strategy=legacy)
  risk_multiplier = risk_multiplier_for_tier(tier)
  prices = [t.price.price for t in payload.targets]
  farthest = max(prices) if direction == "BUY" else min(prices)
  # Only Supply/Demand confirmed reactions have a Go zone identity that stays
  # stable across re-confirmations (reaction.zone_id is assigned once, when
  # Go's own Zone is created). Every other strategy has no such persistent
  # object, so its entry zone is bucketed to a stable, ATR-scaled granularity
  # instead: two opportunities from the same strategy, for the same
  # symbol+direction, whose entry midpoints fall in the same bucket are the
  # same real-world setup re-observed, not two independent ones - matching
  # what reaction.zone_id already guarantees for supply/demand.
  if reaction is not None:
    structural_id = reaction.zone_id
  else:
    atr = float(tech.atr) if tech.atr else 0.0
    bucket_size = max(atr * _THESIS_BUCKET_ATR_FRACTION, pip * _THESIS_BUCKET_MIN_PIPS)
    bucket = round(mid / bucket_size)
    structural_id = f"{profile.catalog_id}:{bucket}"
  thesis = _thesis_id(symbol, family, direction, structural_id)
  tags = (
    GO_ORIGIN_TAG,
    f"{CATALOG_TAG}{profile.catalog_id}",
    f"go_opportunity:{payload.id}",
    f"kind:{profile.structural_kind}",
    "go_strategy_confirmed",
    f"go_strategy_mode:{profile.strategy_mode}",
    *(f"go_evidence:{code}" for code in evidence_codes),
    f"bias:{relation}",
    "bias_source:go_primary_tf",
    *(() if reaction is None else (f"go_reaction:{reaction.reaction_type}",)),
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
    strategy_mode=(
      relation
      if profile.catalog_id in {"supply", "demand"}
      else profile.strategy_mode
    ),
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
    go_invalidation_price=stop,
    tags=tags,
    absolute_target_price=float(farthest),
    tier=tier,
    risk_multiplier=risk_multiplier,
    quality_overall=quality_overall,
    quality_components=quality_components,
    go_stop_envelope_floor_pips=stop_envelope_floor_pips,
    go_stop_envelope_cap_pips=stop_envelope_cap_pips,
    go_stop_envelope_desired_minimum_pips=stop_envelope_desired_minimum_pips,
    go_stop_envelope_source=stop_envelope_source,
    family=family,
    structural_source=f"go:{profile.catalog_id}",
    zone_id=structural_id,
    structural_zone_id=structural_id,
    structural_zone_low=low,
    structural_zone_high=high,
    structural_kind=profile.structural_kind,
    # Every reviewed strategy uses M5 structure, including the three M1
    # confirmation strategies. The event timeframe is kept separately in
    # ``source_tf`` so downstream policy never mistakes M1 confirmation for
    # the structural timeframe.
    structural_timeframe="M5",
    touch_bar_ts=None if reaction is None else str(reaction.touch_bar_time),
    confirmation_bar_ts=None if reaction is None else str(reaction.confirmation_bar_time),
    reaction_type=None if reaction is None else reaction.reaction_type,
    m5_confirmation_bar_ts=None if reaction is None else str(reaction.confirmation_bar_time),
    m5_reaction_type=None if reaction is None else reaction.reaction_type,
    htf_bias=htf_bias,
    regime_kind="",
    bias_relationship=relation,
    execution_eligibility=eligibility,
    thesis_id=thesis,
  )


class GoOpportunityPolicy:
  """Consumer hook: durable Go lifecycle events -> live matches.

  Go is the active technical producer. This adapter no longer consults a
  per-scope approval table; Kafka delivery, lifecycle idempotency,
  freshness and the normal execution checks remain active below it.
  """

  def __init__(
    self,
    repository: PostgresAnalysisOpportunityRepository,
    *,
    client_factory: Callable[[], Any] | None = None,
    clock: Callable[[], float] = time.time,
    multiple_matches_enabled: Callable[[], bool] | None = None,
    freshness_limits: Callable[[], FreshnessLimits] | None = None,
  ):
    self._repository = repository
    self._client_factory = client_factory
    self._clock = clock
    self._multiple = multiple_matches_enabled
    self._limits = freshness_limits

  def _client(self):
    if self._client_factory is not None:
      return self._client_factory()
    from app.persistence import redis_state
    return redis_state.get_client()

  def _multiple_enabled(self) -> bool:
    if self._multiple is not None:
      return self._multiple()
    from app.core.config import runtime_config
    return bool(runtime_config.strategies.matching.multiple_matches_enabled)

  def _freshness_limits(self) -> FreshnessLimits:
    if self._limits is not None:
      return self._limits()
    from app.core.config import runtime_config
    analysis_config = runtime_config.analysis.technical_authority
    return FreshnessLimits(analysis_config.max_event_age_seconds, analysis_config.max_delivery_lag_seconds)

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
    broker supplied one; the freshness check falls back to the envelope's
    ``produced_at``. Only a confirmed observation inside its event-age,
    delivery-lag and technical-expiry limits may become a match: a backlog
    never trades stale history.

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
    # The live Kafka opportunity event is the technical-source boundary.
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
      published_at=published_at, consumed_at=now, limits=self._freshness_limits(),
    )
    if not verdict.ok and not already_adapted:
      await self._decide(event, "not_adapted", verdict.code, owner="go", **verdict.details())
      return "not_adapted"
    try:
      match = build_strategy_match(event, profile=profile, now=now)
    except AdapterRejection as exc:
      await self._decide(event, "rejected", exc.code, message=exc.message)
      return "rejected"

    await self._advance_setup(client, match)
    await self._store_match(client, match, now)
    await self._decide(
      event, "match_written", "go_live", match_id=match.match_id, **verdict.details(),
    )
    log.info("Go opportunity adapted opportunity=%s match=%s", payload.id, match.match_id)
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

  async def on_arbitration_decision(self, event: ArbitrationEnvelope) -> str:
    """Idempotent: republishing the same decision is a no-op write.

    Updates the live StrategyMatch's arbitration_status/
    arbitration_reason_code — Go's cross-strategy conflict-resolution
    decision (Phase 2), read by select_go_arbitrated_intent instead of
    Python re-deriving one via arbitrate_execution_intents — and
    go_thesis_id/go_merged_with (Phase 3), Go's own structural-identity
    thesis correlation, read by multi_match.dedupe_matches in place of its
    ATR-bucket geometric heuristic once thesis_correlation_mode=go. A
    decision for a match_id with no live StrategyMatch (already withdrawn
    by on_terminal, or arrived before the match write completed) is
    simply dropped: there is nothing live left to annotate.
    """
    payload = event.payload
    match_id = match_id_for(payload.opportunity_id)
    client = self._client()
    key = strategy_matches_key(payload.symbol)
    matches = deserialize_matches(await client.get(key))
    match = next((m for m in matches if m.match_id == match_id), None)
    if match is None:
      return "ignored_unknown_match"
    go_thesis_id = None if payload.thesis_id is None else match_id_for(payload.thesis_id)
    go_merged_with = tuple(match_id_for(item) for item in payload.merged_with)
    if (
      match.arbitration_status == payload.status
      and match.arbitration_reason_code == payload.reason_code
      and match.go_thesis_id == go_thesis_id
      and match.go_merged_with == go_merged_with
    ):
      return "unchanged"
    updated = replace(
      match, arbitration_status=payload.status, arbitration_reason_code=payload.reason_code,
      go_thesis_id=go_thesis_id, go_merged_with=go_merged_with,
    )
    await self._store_match(client, updated, int(self._clock()))
    return "arbitration_updated"

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
