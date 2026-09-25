"""S12B shadow policy: intentionally no execution side effects."""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis_client.models import OpportunityEnvelope
from app.analysis_client.repository import PostgresAnalysisOpportunityRepository


_EXECUTION_CONTRACT_GAPS = (
  "current_price",
  "atr",
  "confluence",
  "source_structure",
  "execution_confirmation",
  "strategy_routing",
)


@dataclass(frozen=True, slots=True)
class ShadowDecision:
  outcome: str
  reason: str
  missing_fields: tuple[str, ...]


class AnalysisShadowEvaluator:
  """Record what a Go opportunity needs before it can enter existing policy.

  It deliberately does not build, publish, reserve, or execute a TradePlan.
  Re-deriving these facts in Python would recreate the retired detector path.
  """

  def __init__(self, repository: PostgresAnalysisOpportunityRepository):
    self._repository = repository

  async def evaluate_creation(self, event: OpportunityEnvelope) -> ShadowDecision:
    decision = ShadowDecision(
      outcome="contract_gap",
      reason="go_opportunity_v1_lacks_execution_policy_inputs",
      missing_fields=_EXECUTION_CONTRACT_GAPS,
    )
    await self._repository.record_shadow_decision(
      opportunity_id=event.payload.id,
      event_id=event.event_id,
      outcome=decision.outcome,
      reason=decision.reason,
      missing_fields=decision.missing_fields,
      details={
        "strategy": event.payload.strategy,
        "symbol": event.payload.symbol,
        "direction": event.payload.direction,
        "quality": event.payload.quality.overall,
      },
    )
    return decision
