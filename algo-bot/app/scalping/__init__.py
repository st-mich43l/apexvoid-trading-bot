"""Event-driven M1 high-frequency scalping engine for XAU."""

from app.scalping.models import (
  ARCHETYPE_BREAKOUT_RETEST,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_RANGE_SWEEP,
  ScalpContextSnapshot,
  ScalpDecision,
  ScalpOpportunity,
  ScalpScore,
)

__all__ = [
  "ARCHETYPE_BREAKOUT_RETEST",
  "ARCHETYPE_IMPULSE_PULLBACK",
  "ARCHETYPE_RANGE_SWEEP",
  "ScalpContextSnapshot",
  "ScalpDecision",
  "ScalpOpportunity",
  "ScalpScore",
  "process_m1_bar",
  "scalp_m1_event_loop",
]


def __getattr__(name: str):
  if name in {"process_m1_bar", "scalp_m1_event_loop"}:
    from app.scalping import runtime as _runtime
    return getattr(_runtime, name)
  raise AttributeError(name)
