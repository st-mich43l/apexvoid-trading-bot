"""S14B freshness gate: a stale or pre-activation Go event never trades.

A Kafka consumer that restarts, rebalances or falls behind will happily deliver
an hours-old creation event. Replaying it into the live matcher would trade a
setup nobody observed *now*. This gate is pure (no I/O, no clock of its own) so
it can be replayed deterministically and tested exhaustively.

Four independent limits, checked in this order so the recorded reason is the
most fundamental one:

* ``pre_activation_event``   the confirmed observation predates the durable
                             go-effective boundary of the scope (the moment the
                             fence made Go the effective publisher). History
                             replayed after an activation is never a new plan.
* ``opportunity_expired``    the engine's own technical expiry has passed.
* ``event_too_old``          the confirmed observation is older than
                             ``max_event_age_seconds`` at consumption.
* ``delivery_lag_exceeded``  Kafka publish -> consume lag exceeds
                             ``max_delivery_lag_seconds`` (a backlog signal that
                             is independent of how old the *setup* is).

Events failing the gate are still recorded in the durable ledger (so history
and invalidations stay correct); they are just never adapted into a match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PRE_ACTIVATION = "pre_activation_event"
EXPIRED = "opportunity_expired"
TOO_OLD = "event_too_old"
LAG_EXCEEDED = "delivery_lag_exceeded"
FRESH = "fresh"


@dataclass(frozen=True)
class FreshnessLimits:
  max_event_age_seconds: int
  max_delivery_lag_seconds: int


@dataclass(frozen=True)
class FreshnessVerdict:
  ok: bool
  code: str
  observed_at: int
  expires_at: int
  produced_at: int
  published_at: int
  consumed_at: int
  boundary: int
  event_age_seconds: int
  delivery_lag_seconds: int
  limits: FreshnessLimits = field(repr=False, default=FreshnessLimits(0, 0))

  def details(self) -> dict[str, Any]:
    """Audit fields recorded with every decision (ok or not)."""
    return {
      "observed_at": self.observed_at,
      "expires_at": self.expires_at,
      "produced_at": self.produced_at,
      "published_at": self.published_at,
      "consumed_at": self.consumed_at,
      "activation_boundary": self.boundary,
      "event_age_seconds": self.event_age_seconds,
      "delivery_lag_seconds": self.delivery_lag_seconds,
      "max_event_age_seconds": self.limits.max_event_age_seconds,
      "max_delivery_lag_seconds": self.limits.max_delivery_lag_seconds,
    }


def evaluate_freshness(
  *,
  observed_at: int,
  expires_at: int,
  produced_at: int,
  published_at: int | None,
  consumed_at: int,
  boundary: int,
  limits: FreshnessLimits,
) -> FreshnessVerdict:
  """``observed_at`` is the engine's confirmed-observation time (``created_at``);
  ``published_at`` is the Kafka record's publish time when the broker supplied
  one, else the envelope's ``produced_at``. All values are epoch seconds."""
  published = int(published_at) if published_at else int(produced_at)
  age = int(consumed_at) - int(observed_at)
  lag = int(consumed_at) - published
  if observed_at < boundary:
    code = PRE_ACTIVATION
  elif consumed_at >= expires_at:
    code = EXPIRED
  elif age > limits.max_event_age_seconds:
    code = TOO_OLD
  elif lag > limits.max_delivery_lag_seconds:
    code = LAG_EXCEEDED
  else:
    code = FRESH
  return FreshnessVerdict(
    ok=code == FRESH, code=code, observed_at=int(observed_at), expires_at=int(expires_at),
    produced_at=int(produced_at), published_at=published, consumed_at=int(consumed_at),
    boundary=int(boundary), event_age_seconds=age, delivery_lag_seconds=lag, limits=limits,
  )
