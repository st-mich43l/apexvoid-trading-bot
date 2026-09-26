"""S14A: what would the live pipeline do with this Go opportunity? (zero side effects)

Replaces the static ``contract_gap`` shadow. In ``go_shadow`` mode each durable
creation event is translated by the *same* adapter the live cutover uses
(``go_opportunity_policy.build_strategy_match``) and then run through the *real*
worker cycle (``worker._handle_event``: admission, arbitration, killzone/news,
execution policy, sizing, the TradePlan V8 build and publish) against an
in-memory overlay of Redis (``analysis_client.shadow_overlay``) with a read-only
PostgreSQL and no Telegram. The outcome is recorded as an auditable decision in
``analysis_shadow_decisions``; nothing else is written anywhere.

Decision vocabulary (``outcome``; ``reason`` is the gate's own code):

* ``would_publish``   the live pipeline would have published this TradePlan
* ``would_wait``      valid, but the pipeline waits (retest, spot, entry contract)
* ``would_reject``    the pipeline blocked it (policy/guard reason recorded)
* ``would_not_route`` the cycle recorded no route for the match
* ``not_adapted``     stopped before policy (scope, freshness, config)
* ``rejected``        the adapter refused (missing facts; never guessed)
* ``dry_run_error``   the dry run itself failed (recorded; the consumer goes on)

The authority fence protects *live* publication and is deliberately not applied
inside the dry run (see ``worker._publish_trade_plan_v8``); the fence's actual
state for the scope is recorded in ``details.fence_actual`` instead.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from app.analysis_client.freshness import FreshnessLimits, evaluate_freshness
from app.analysis_client.models import OpportunityEnvelope
from app.analysis_client.repository import PostgresAnalysisOpportunityRepository
from app.analysis_client.shadow import ShadowDecision
from app.analysis_client.shadow_overlay import OverlayRedis, dry_run_context
from app.autotrade.go_opportunity_policy import (
  REVIEWED_SCOPES,
  AdapterRejection,
  GoOpportunityPolicy,
  build_strategy_match,
)

log = logging.getLogger(__name__)

# Decision-relevant plan fields: what a Python-vs-Go comparison must agree on.
_PLAN_DIGEST_FIELDS = ("entry", "stop", "targets", "risk", "sizing", "management", "execution_policy")


def plan_digest(plan: dict[str, Any]) -> str:
  """Stable hash over the decision-relevant plan fields (timestamps excluded)."""
  subset = {k: plan.get(k) for k in _PLAN_DIGEST_FIELDS}
  return hashlib.sha256(json.dumps(subset, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class GoShadowPolicy:
  def __init__(
    self,
    repository: PostgresAnalysisOpportunityRepository,
    *,
    real_client_factory: Callable[[], Any] | None = None,
    fence: Any | None = None,
    clock: Callable[[], float] = time.time,
    multiple_matches_enabled: Callable[[], bool] | None = None,
    freshness_limits: Callable[[], FreshnessLimits] | None = None,
  ):
    self._repository = repository
    self._real_client_factory = real_client_factory
    self._fence = fence
    self._clock = clock
    self._multiple = multiple_matches_enabled
    self._limits = freshness_limits

  # ---- collaborators (lazy, so importing this module has no side effects) ------
  def _real_client(self) -> Any:
    if self._real_client_factory is not None:
      return self._real_client_factory()
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
    authority = runtime_config.analysis.technical_authority
    return FreshnessLimits(authority.max_event_age_seconds, authority.max_delivery_lag_seconds)

  async def _fence_actual(self, symbol: str, scope: str) -> dict[str, Any]:
    try:
      from app.analysis_client.authority import get_fence
      decision = await (self._fence or get_fence()).authorize_go_publication(symbol, scope)
      return {"go_allowed": decision.allowed, "reason": decision.reason, "owner": decision.owner, "epoch": decision.epoch}
    except Exception as exc:  # noqa: BLE001 - informational only
      return {"error": f"{type(exc).__name__}: {exc}"}

  # ---- the dry run --------------------------------------------------------------
  async def dry_run_creation(self, event: OpportunityEnvelope, *, published_at: int | None = None) -> ShadowDecision:
    payload = event.payload
    base: dict[str, Any] = {
      "strategy": payload.strategy, "symbol": payload.symbol, "direction": payload.direction,
      "quality": payload.quality.overall, "dry_run": True,
    }
    profile = REVIEWED_SCOPES.get(payload.strategy)
    if profile is None:
      return await self._record(event, ShadowDecision("not_adapted", "scope_not_reviewed"), base)
    base["scope"] = profile.catalog_id
    if await self._repository.opportunity_state(payload.id) != "active":
      return await self._record(event, ShadowDecision("not_adapted", "ignored_not_active"), base)
    base["fence_actual"] = await self._fence_actual(payload.symbol, profile.catalog_id)
    if not self._multiple_enabled():
      return await self._record(event, ShadowDecision("not_adapted", "multiple_matches_disabled"), base)

    now = int(self._clock())
    # No activation exists in shadow (boundary 0): only expiry, age and lag apply.
    verdict = evaluate_freshness(
      observed_at=payload.created_at, expires_at=payload.expires_at, produced_at=event.produced_at,
      published_at=published_at, consumed_at=now, boundary=0, limits=self._freshness_limits(),
    )
    base.update(verdict.details())
    if not verdict.ok:
      return await self._record(event, ShadowDecision("not_adapted", verdict.code), base)
    try:
      match = build_strategy_match(event, profile=profile, epoch=0, now=now)
    except AdapterRejection as exc:
      return await self._record(event, ShadowDecision("rejected", exc.code), {**base, "message": exc.message})
    base["match_id"] = match.match_id

    try:
      decision, evidence = await self._run(match, now)
    except Exception as exc:  # noqa: BLE001 - never wedge the consumer on a dry-run bug
      log.exception("Go shadow dry run failed opportunity=%s", payload.id)
      return await self._record(
        event, ShadowDecision("dry_run_error", type(exc).__name__), {**base, "error": f"{type(exc).__name__}: {exc}"},
      )
    return await self._record(event, decision, {**base, **evidence})

  async def _run(self, match: Any, now: int) -> tuple[ShadowDecision, dict[str, Any]]:
    from app.autotrade import worker
    from app.autotrade.route_outcome import route_outcome_key
    from app.autotrade.trade_plan_stream import plan_key

    symbol = match.symbol
    if symbol.upper() not in worker._symbols():
      return ShadowDecision("not_adapted", "symbol_not_auto_trade_enabled"), {}
    overlay = OverlayRedis(self._real_client(), clock=self._clock)
    with dry_run_context(overlay):
      await GoOpportunityPolicy._advance_setup(overlay, match)
      await GoOpportunityPolicy._store_match(overlay, match, now)
      await worker._handle_event(
        f"{symbol.upper()}:{worker.EXECUTION_TIMEFRAME}:{now}", client=overlay, ready_match_id=match.match_id,
      )
      route_raw = await overlay.get(route_outcome_key(symbol, match.match_id))
      plan_id = worker._v8_plan_id(match)
      plan_raw = await overlay.get(plan_key(plan_id))
    evidence: dict[str, Any] = {
      "overlay": {
        "written_keys": len(overlay.dirty_keys), "writes": dict(overlay.writes),
        "real_reads": dict(overlay.real_read_calls),
      },
    }
    route = json.loads(route_raw) if route_raw else None
    if route is not None:
      evidence["route"] = {k: route.get(k) for k in ("stage", "status", "reason_code", "message", "measured")}
    if plan_raw:
      plan = json.loads(plan_raw)
      evidence["plan"] = plan
      evidence["plan_id"] = plan_id
      evidence["plan_digest"] = plan_digest(plan)
      return ShadowDecision("would_publish", "candidate_published"), evidence
    if route is None:
      return ShadowDecision("would_not_route", "no_route_outcome"), evidence
    reason = str(route.get("reason_code") or route.get("status") or "unspecified")
    status = str(route.get("status") or "")
    if status in {"waiting", "checking"}:
      return ShadowDecision("would_wait", reason), evidence
    return ShadowDecision("would_reject", reason), evidence

  async def _record(self, event: OpportunityEnvelope, decision: ShadowDecision, details: dict[str, Any]) -> ShadowDecision:
    await self._repository.record_shadow_decision(
      opportunity_id=event.payload.id, event_id=event.event_id, outcome=decision.outcome, reason=decision.reason,
      missing_fields=decision.missing_fields, details=details, mode="go_shadow",
    )
    return decision
