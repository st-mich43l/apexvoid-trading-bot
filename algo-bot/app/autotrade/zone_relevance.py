"""Market relevance for ZoneWatch records - orthogonal to structural
validity/lifecycle state.

Owner 2026-09-17: `zone_watch.list_active_zone_watches` only ever checked
grade and lifecycle state (see `_index_wants_record`), so a structurally
valid zone could sit 40-80 price points from current XAU price for 9-12h+
and still be returned as "active". Two independent questions were being
conflated:

  structural validity: does this zone still technically exist?
    (already answered by ZoneWatch.state - non-terminal/non-locked)
  market relevance: does this zone matter to CURRENT price right now?
    (answered here - was never computed at all before this module)

Everything in this module is a pure, stateless read: it never mutates a
ZoneWatch record or touches Redis. Relevance is recomputed fresh from
current price/ATR on every call, per the owner's explicit requirement that
distance must never be persisted and reused - a DORMANT zone is always
free to become NEARBY/IMMEDIATE again the instant price returns, without
needing any "reactivation" write.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.autotrade.zone_watch import ZoneWatch
from app.core.config import runtime_config

IMMEDIATE = "immediate"
NEARBY = "nearby"
REMOTE = "remote"
DORMANT = "dormant"

BELOW_MARKET = "below_market"
OVERLAPPING_MARKET = "overlapping_market"
ABOVE_MARKET = "above_market"

ACTIVE_WATCHLIST_RELEVANCE = frozenset({IMMEDIATE, NEARBY})
STRUCTURAL_MEMORY_RELEVANCE = frozenset({REMOTE, DORMANT})

GOOD = "good"
THIN = "thin"
POOR = "poor"
GAP = "gap"


@dataclass(frozen=True)
class ZoneRelevance:
  relevance: str
  distance_price: float
  distance_atr: float | None
  relative_position: str


def _relative_position(low: float, high: float, mid_price: float) -> str:
  if mid_price < low:
    return BELOW_MARKET
  if mid_price > high:
    return ABOVE_MARKET
  return OVERLAPPING_MARKET


def _distance_price(low: float, high: float, mid_price: float) -> float:
  if mid_price < low:
    return low - mid_price
  if mid_price > high:
    return mid_price - high
  return 0.0


def classify_zone_relevance(
  record: ZoneWatch, mid_price: float, atr: float | None,
) -> ZoneRelevance:
  """How relevant `record` is to CURRENT price, independent of lifecycle
  state. `atr` should be the current ATR for the record's own
  `source_timeframe` where available - pass `None` only when no ATR
  context exists at all (falls back to a coarse inside/outside read,
  since distance cannot be meaningfully banded without a volatility unit).
  """
  distance_price = _distance_price(record.low, record.high, mid_price)
  relative_position = _relative_position(record.low, record.high, mid_price)
  if atr is None or atr <= 0:
    relevance = IMMEDIATE if distance_price <= 0 else DORMANT
    return ZoneRelevance(relevance, distance_price, None, relative_position)
  distance_atr = distance_price / atr
  bands = runtime_config.analysis.zone_relevance
  if distance_atr <= bands.immediate_atr:
    relevance = IMMEDIATE
  elif distance_atr <= bands.nearby_atr:
    relevance = NEARBY
  elif distance_atr <= bands.remote_atr:
    relevance = REMOTE
  else:
    relevance = DORMANT
  return ZoneRelevance(relevance, distance_price, distance_atr, relative_position)


def is_dead_zone(relevance: ZoneRelevance) -> bool:
  """True once price is so far from a zone it is dead, not just dormant.

  Dormant (beyond ``remote_atr``) zones are skipped but kept in case price
  drifts back. Beyond twice that band the zone is removed outright (owner
  2026-09-21: a BUY zone 34 points - 8 ATR - below a falling market was
  still listed). The doubled band is hysteresis so price oscillating near
  the dormant boundary cannot churn zones in and out of existence. Needs a
  real ATR: without one distance cannot be judged, so nothing is retired.
  """
  if relevance.distance_atr is None:
    return False
  return relevance.distance_atr > 2.0 * runtime_config.analysis.zone_relevance.remote_atr


def partition_by_relevance(
  records: list[ZoneWatch], mid_price: float, atr: float | None,
) -> tuple[list[tuple[ZoneWatch, ZoneRelevance]], list[tuple[ZoneWatch, ZoneRelevance]]]:
  """Split already-listed (structurally valid) zone_watch records into
  (active_watchlist, structural_memory) by current market relevance.
  Nearest-first within each group.
  """
  active: list[tuple[ZoneWatch, ZoneRelevance]] = []
  memory: list[tuple[ZoneWatch, ZoneRelevance]] = []
  for record in records:
    relevance = classify_zone_relevance(record, mid_price, atr)
    bucket = active if relevance.relevance in ACTIVE_WATCHLIST_RELEVANCE else memory
    bucket.append((record, relevance))
  sort_key = lambda pair: (  # noqa: E731
    pair[1].distance_atr if pair[1].distance_atr is not None else pair[1].distance_price,
    -pair[0].score,
  )
  active.sort(key=sort_key)
  memory.sort(key=sort_key)
  return active, memory


def coverage_state(
  active: list[tuple[ZoneWatch, ZoneRelevance]],
) -> str:
  """First-pass coverage heuristic (Phase 10) - a tuning candidate, not a
  fixed truth, same as the ATR band defaults. GOOD needs both directions
  represented with more than one candidate; a lone side or single
  candidate is THIN; the nearest active entry sitting well outside
  immediate range is POOR; nothing in range at all is GAP.
  """
  if not active:
    return GAP
  bands = runtime_config.analysis.zone_relevance
  nearest_atr = next(
    (r.distance_atr for _, r in active if r.distance_atr is not None), None,
  )
  if nearest_atr is not None and nearest_atr > bands.immediate_atr and len(active) < 2:
    return POOR
  directions = {record.direction for record, _ in active}
  if len(active) >= 2 and len(directions) >= 2:
    return GOOD
  return THIN


def age_since_discovery_seconds(record: ZoneWatch, now: int) -> int:
  return max(0, now - int(record.discovered_at or now))


def age_since_last_touch_seconds(record: ZoneWatch, now: int) -> int | None:
  if record.last_touch_at is None:
    return None
  return max(0, now - int(record.last_touch_at))
