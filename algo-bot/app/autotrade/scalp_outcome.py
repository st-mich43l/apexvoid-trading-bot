"""Terminal outcome of every scalp-lane Go opportunity: one structured log line and one counter.

Production 2026-11-03: ten Impulse Pullback opportunities (seven invalidated by the Go engine within 2-14 minutes,
three expired at 15) left no order attempt and no rejection line in the Algo Bot, so their path was invisible. Every
Go opportunity of a scalp-lane strategy (catalog ``m1_scalp`` trait, see ``strategy_catalog.exposure_lane``) now ends
in exactly one of these outcomes, written once as

    scalp_outcome strategy=<id> opportunity_id=<opp_...> terminal=<outcome[:reason]> reason=<reason|-> elapsed_s=<n>

and counted in Redis at ``scalp:metric:outcome:{strategy}:{terminal}`` (plus the existing ``increment_metric`` family):

- ``plan_published``
- ``admission_rejected:<reason_code>``  (the worker refused to build or publish a plan)
- ``waiting_retest_expired``            (the quote never made the entry executable before expiry)
- ``invalidated_before_entry:<reason>`` (the Go engine invalidated it first, e.g. IMPULSE_REFERENCE_LOST)
- ``activation_rejected:<reason_code>`` (the executor refused the published plan)
- ``stale_on_arrival``                  (the Kafka event was already too old / expired when consumed)
- ``adapter_rejected:<reason>``         (the adapter refused it, or the instrument contains the strategy)

First writer wins (``SET NX``), so a redelivered event or a later Go terminal event never adds a second outcome. The one
supersession is ``plan_published`` -> ``activation_rejected``: a plan the executor then refuses is no longer an
outcome of "published". Observability only: nothing here may fail the path it observes.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.autotrade.strategy_catalog import SCALP_LANE, exposure_lane

log = logging.getLogger("bot")

PLAN_PUBLISHED = "plan_published"
ADMISSION_REJECTED = "admission_rejected"
WAITING_RETEST_EXPIRED = "waiting_retest_expired"
INVALIDATED_BEFORE_ENTRY = "invalidated_before_entry"
ACTIVATION_REJECTED = "activation_rejected"
STALE_ON_ARRIVAL = "stale_on_arrival"
ADAPTER_REJECTED = "adapter_rejected"

SCALP_OUTCOMES = (
  PLAN_PUBLISHED,
  ADMISSION_REJECTED,
  WAITING_RETEST_EXPIRED,
  INVALIDATED_BEFORE_ENTRY,
  ACTIVATION_REJECTED,
  STALE_ON_ARRIVAL,
  ADAPTER_REJECTED,
)

# Long enough to outlive any opportunity (technical expiry is hours), short enough not to accrete.
OUTCOME_MARKER_TTL_SECONDS = 14 * 24 * 3600


def is_scalp_lane(strategy: object) -> bool:
  """True when the strategy's catalog profile carries the ``m1_scalp`` trait."""
  return exposure_lane(str(strategy or "")) == SCALP_LANE


def strategy_key(strategy: object) -> str:
  """Stable metric label: ``Impulse Pullback Scalp`` -> ``impulse_pullback``, ``Range Scalp`` -> ``range_scalp``."""
  text = re.sub(r"[^a-z0-9]+", "_", str(strategy or "").lower()).strip("_")
  if text == "range_scalp":
    return text  # the suffix is part of the id: stripping it would label Range Scalp "range"
  return text.removesuffix("_scalp") or "unknown"


def marker_key(opportunity_id: str) -> str:
  return f"scalp:outcome:{opportunity_id}"


def metric_key(strategy: object, terminal: str) -> str:
  return f"scalp:metric:outcome:{strategy_key(strategy)}:{terminal}"


def label(terminal: str, reason: str | None) -> str:
  """``admission_rejected`` + ``xau_scalp_opposite_too_close`` -> ``admission_rejected:xau_scalp_opposite_too_close``."""
  reason_text = str(reason or "").strip()
  return f"{terminal}:{reason_text}" if reason_text else terminal


def _text(raw: Any) -> str:
  return raw.decode() if isinstance(raw, bytes) else str(raw)


async def record_scalp_outcome(
  client: Any,
  *,
  strategy: object,
  opportunity_id: str,
  terminal: str,
  reason: str | None = None,
  started_at: int | float | None = None,
  symbol: str = "XAU",
  now: int | float | None = None,
) -> bool:
  """Write the single terminal outcome of one scalp-lane opportunity. True when this call wrote it.

  A strategy outside the scalp lane is ignored (False). Never raises.
  """
  try:
    if terminal not in SCALP_OUTCOMES:
      raise ValueError(f"unknown scalp outcome {terminal!r}")
    if not is_scalp_lane(strategy) or not opportunity_id:
      return False
    outcome = label(terminal, reason)
    marker = marker_key(str(opportunity_id))
    written = bool(await client.set(marker, outcome, nx=True, ex=OUTCOME_MARKER_TTL_SECONDS))
    supersedes = None
    if not written:
      previous = await client.get(marker)
      previous_text = "" if previous is None else _text(previous)
      if terminal == ACTIVATION_REJECTED and previous_text == PLAN_PUBLISHED:
        await client.set(marker, outcome, ex=OUTCOME_MARKER_TTL_SECONDS)
        supersedes = PLAN_PUBLISHED
      else:
        return False
    stamp = int(now if now is not None else time.time())
    elapsed = "-" if started_at is None else str(max(0, stamp - int(started_at)))
    log.info(
      "scalp_outcome strategy=%s opportunity_id=%s terminal=%s reason=%s elapsed_s=%s%s",
      strategy_key(strategy), opportunity_id, outcome, str(reason or "-"), elapsed,
      f" supersedes={supersedes}" if supersedes else "",
    )
    await client.incr(metric_key(strategy, outcome))
    try:
      from app.autotrade.lifecycle import increment_metric

      await increment_metric(
        client, "scalp_outcome", symbol=symbol,
        dimensions={"strategy": strategy_key(strategy), "terminal": outcome},
      )
    except Exception:
      log.debug("scalp_outcome shared metric failed", exc_info=True)
    return True
  except Exception:
    log.exception(
      "scalp_outcome record failed strategy=%s opportunity_id=%s terminal=%s",
      strategy, opportunity_id, terminal,
    )
    return False


async def read_scalp_outcome_counters(client: Any) -> dict[str, int]:
  """Every ``scalp:metric:outcome:*`` counter (operator/report helper; SCAN over a small key family)."""
  counters: dict[str, int] = {}
  async for raw in client.scan_iter(match="scalp:metric:outcome:*", count=200):
    key = _text(raw)
    value = await client.get(key)
    counters[key.removeprefix("scalp:metric:outcome:")] = int(_text(value)) if value is not None else 0
  return counters
