"""S12B/S14A shadow policy: a real dry run, never a side effect.

Historically this recorded a static ``contract_gap`` because Go opportunities
lacked the policy inputs. Since the S13B ``technical_context`` block the real
question can be answered: what would the live pipeline do with this event? That
answer comes from an injected ``dry_run`` (``autotrade.go_shadow_policy``), which
runs the actual worker cycle against an in-memory Redis overlay and a read-only
PostgreSQL. This module stays free of autotrade imports (the S13 boundary) and
only decides whether a dry run is configured.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.analysis_client.models import OpportunityEnvelope
from app.analysis_client.repository import PostgresAnalysisOpportunityRepository


@dataclass(frozen=True, slots=True)
class ShadowDecision:
  outcome: str
  reason: str
  missing_fields: tuple[str, ...] = ()


DryRun = Callable[..., Awaitable[ShadowDecision]]


class AnalysisShadowEvaluator:
  """Records the outcome of a real dry run for each durable creation event."""

  def __init__(self, repository: PostgresAnalysisOpportunityRepository, *, dry_run: DryRun | None = None):
    self._repository = repository
    self._dry_run = dry_run

  async def evaluate_creation(self, event: OpportunityEnvelope, *, published_at: int | None = None) -> ShadowDecision:
    if self._dry_run is not None:
      return await self._dry_run(event, published_at=published_at)
    # Honest about what happened: nothing was evaluated. (Not a "contract gap":
    # the contract now carries the inputs; the composition root simply did not
    # wire a policy dry run.)
    decision = ShadowDecision("shadow_unavailable", "dry_run_policy_not_configured")
    await self._repository.record_shadow_decision(
      opportunity_id=event.payload.id, event_id=event.event_id, outcome=decision.outcome, reason=decision.reason,
      details={"strategy": event.payload.strategy, "symbol": event.payload.symbol, "direction": event.payload.direction},
    )
    return decision
