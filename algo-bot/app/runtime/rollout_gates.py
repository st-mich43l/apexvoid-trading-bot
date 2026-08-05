"""Centralized instrument rollout gates.

Rollout is runtime policy, not a silent mapping onto global dry-run.
"""

from __future__ import annotations

from app.configuration.models.instruments import InstrumentRollout


def permits_feed(rollout: InstrumentRollout) -> bool:
  return rollout is not InstrumentRollout.DISABLED


def permits_analysis(rollout: InstrumentRollout) -> bool:
  return rollout in {
    InstrumentRollout.ANALYSIS_ONLY,
    InstrumentRollout.PAPER,
    InstrumentRollout.LIVE,
  }


def permits_public_delivery(rollout: InstrumentRollout) -> bool:
  """Public Telegram / signal cards are live-only for this PR."""
  return rollout is InstrumentRollout.LIVE


def permits_candidate_publication(rollout: InstrumentRollout) -> bool:
  return rollout in {InstrumentRollout.PAPER, InstrumentRollout.LIVE}


def permits_broker_execution(rollout: InstrumentRollout) -> bool:
  """Live broker order placement. Paper must never place broker orders."""
  return rollout is InstrumentRollout.LIVE
