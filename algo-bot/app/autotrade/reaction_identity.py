"""Stable identity for Mapped Zone Reaction theses.

A single structural reaction sequence must map to one match, one candidate,
one group, and one broker initial order — across tick replay, lookback memory,
and minor zone-coordinate drift.

Separately, one structural *thesis* (symbol + strategy + direction + zone)
may have at most one active initial group at a time. A newer M1 touch with a
different reaction_id must not open a second group while that thesis is live.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable


REACTION_ID_VERSION = 1
THESIS_ID_VERSION = 1
REACTION_CLAIM_KEY_PREFIX = "auto_trade:reaction_claim"
THESIS_CLAIM_KEY_PREFIX = "auto_trade:thesis_claim"

# Non-terminal thesis occupancy — another initial order is forbidden.
ACTIVE_THESIS_STATES = frozenset({
  "claimed",
  "candidate_published",
  "order_submitted",
  "order_accepted",
  "filled",
  "managing",
})

# Closed group, but rearm exit/re-entry tracking still owns the thesis.
POST_TERMINAL_THESIS_STATES = frozenset({
  "terminal_waiting_exit",
  "outside_zone",
})

# Freely reusable after a full rearm cycle (or explicit cancel paths).
REARM_READY_THESIS_STATES = frozenset({
  "rearm_ready",
  "closed",
  "cancelled",
  "rejected",
  "expired",
})

_STRUCTURAL_TAGS = {
  "breaker",
  "breakout-retest",
  "demand",
  "flip",
  "fvg",
  "ob",
  "supply",
}


def _sha(raw: str) -> str:
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def canonicalize_zone_bucket(
  lo: float,
  hi: float,
  *,
  atr: float,
  pip_size: float,
) -> tuple[float, float]:
  """Bucket mid/width so minor map jitter shares one structural identity."""
  width = max(0.0, float(hi) - float(lo))
  mid = (float(lo) + float(hi)) / 2.0
  bucket = max(float(pip_size) * 10.0, max(0.0, float(atr)) * 0.25, 1.0)
  mid_bucket = round(mid / bucket) * bucket
  width_bucket = round(width / bucket) * bucket
  return mid_bucket, width_bucket


def structural_zone_id(
  symbol: str,
  direction: str,
  lo: float,
  hi: float,
  *,
  atr: float,
  pip_size: float,
  tags: Iterable[str] | None = None,
  source_tf: str = "M5",
  source_ids: Iterable[str] | None = None,
) -> str:
  """Stable zone identity; prefers explicit source IDs when available."""
  side = direction.upper()
  if source_ids:
    sources = ",".join(sorted({str(item).strip() for item in source_ids if str(item).strip()}))
    if sources:
      return _sha(
        f"sz|{symbol.upper()}|{side}|{source_tf.upper()}|{sources}"
      )
  mid_b, width_b = canonicalize_zone_bucket(lo, hi, atr=atr, pip_size=pip_size)
  structural = ",".join(
    sorted({
      str(tag).casefold()
      for tag in (tags or ())
      if str(tag).casefold() in _STRUCTURAL_TAGS
    })
  )
  return _sha(
    f"sz|{symbol.upper()}|{side}|{source_tf.upper()}|"
    f"{mid_b:.2f}|{width_b:.2f}|{structural}"
  )


def zones_materially_equivalent(
  left_lo: float,
  left_hi: float,
  right_lo: float,
  right_hi: float,
  *,
  atr: float,
  overlap_ratio: float = 0.80,
  center_atr: float = 0.25,
) -> bool:
  """Fallback equivalence when hashed structural IDs are unavailable."""
  left_width = max(0.0, left_hi - left_lo)
  right_width = max(0.0, right_hi - right_lo)
  if left_width <= 0 or right_width <= 0:
    return False
  overlap = min(left_hi, right_hi) - max(left_lo, right_lo)
  if overlap <= 0:
    return False
  ratio = overlap / min(left_width, right_width)
  left_mid = (left_lo + left_hi) / 2.0
  right_mid = (right_lo + right_hi) / 2.0
  center_limit = max(0.0, float(atr)) * max(0.0, float(center_atr))
  width_tol = max(left_width, right_width) * 0.25
  return (
    ratio + 1e-9 >= overlap_ratio
    and abs(left_mid - right_mid) <= center_limit + 1e-9
    and abs(left_width - right_width) <= width_tol + 1e-9
  )


def mapped_reaction_id(
  *,
  symbol: str,
  strategy: str,
  direction: str,
  structural_zone_id: str,
  touch_bar_ts: str,
  confirmation_bar_ts: str,
  reaction_type: str,
  version: int = REACTION_ID_VERSION,
) -> str:
  raw = (
    f"v{version}|{symbol.upper()}|{strategy}|{direction.upper()}|"
    f"{structural_zone_id}|{touch_bar_ts}|{confirmation_bar_ts}|"
    f"{str(reaction_type).casefold()}"
  )
  return _sha(raw)


def mapped_thesis_id(
  *,
  symbol: str,
  strategy: str,
  direction: str,
  structural_zone_id: str,
  version: int = THESIS_ID_VERSION,
) -> str:
  """Structural trading thesis (independent of a specific reaction sequence)."""
  return _sha(
    f"v{version}|{symbol.upper()}|{strategy}|{direction.upper()}|"
    f"{structural_zone_id}"
  )


def mapped_group_id(
  *,
  symbol: str,
  strategy_family: str,
  direction: str,
  thesis_id: str,
  thesis_cycle: int = 1,
  reaction_id: str | None = None,
) -> str:
  """One active group per thesis cycle.

  ``reaction_id`` remains accepted for legacy callers / tests; preferred
  identity is thesis_id + cycle so a rearmed thesis can open a new group.
  """
  if thesis_id:
    return _sha(
      f"group|{symbol.upper()}|{strategy_family}|{direction.upper()}|"
      f"{thesis_id}|{int(thesis_cycle)}"
    )
  if reaction_id:
    return _sha(
      f"group|{symbol.upper()}|{strategy_family}|{direction.upper()}|{reaction_id}"
    )
  raise ValueError("mapped_group_id requires thesis_id or reaction_id")


def reaction_claim_key(reaction_id: str) -> str:
  return f"{REACTION_CLAIM_KEY_PREFIX}:{reaction_id}"


def thesis_claim_key(thesis_id: str) -> str:
  return f"{THESIS_CLAIM_KEY_PREFIX}:{thesis_id}"


def reaction_claim_payload(
  *,
  reaction_id: str,
  thesis_id: str,
  candidate_id: str,
  group_id: str,
  touch_bar_ts: str,
  confirmation_bar_ts: str,
  state: str,
  claimed_at: int,
  structural_zone_id: str,
  symbol: str,
  direction: str,
  structural_zone_low: float | None = None,
  structural_zone_high: float | None = None,
) -> str:
  payload: dict[str, Any] = {
    "reaction_id": reaction_id,
    "thesis_id": thesis_id,
    "candidate_id": candidate_id,
    "group_id": group_id,
    "touch_bar_ts": touch_bar_ts,
    "confirmation_bar_ts": confirmation_bar_ts,
    "state": state,
    "claimed_at": int(claimed_at),
    "structural_zone_id": structural_zone_id,
    "symbol": symbol.upper(),
    "direction": direction.upper(),
  }
  if structural_zone_low is not None:
    payload["structural_zone_low"] = float(structural_zone_low)
  if structural_zone_high is not None:
    payload["structural_zone_high"] = float(structural_zone_high)
  return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def thesis_claim_payload(
  *,
  thesis_id: str,
  strategy: str,
  strategy_family: str,
  symbol: str,
  direction: str,
  structural_zone_id: str,
  structural_zone_low: float | None,
  structural_zone_high: float | None,
  active_reaction_id: str,
  candidate_id: str,
  group_id: str,
  state: str,
  claimed_at: int,
  touch_bar_ts: str,
  confirmation_bar_ts: str,
  thesis_cycle: int = 1,
  terminal_at: int | None = None,
  exit_detected_at: int | None = None,
  first_outside_bar_ts: str | None = None,
  latest_outside_bar_ts: str | None = None,
  outside_bar_count: int = 0,
  reentry_bar_ts: str | None = None,
  rearm_ready: bool = False,
  version: int = 1,
) -> str:
  return json.dumps({
    "version": int(version),
    "thesis_id": thesis_id,
    "strategy": strategy,
    "strategy_family": strategy_family,
    "symbol": symbol.upper(),
    "direction": direction.upper(),
    "structural_zone_id": structural_zone_id,
    "structural_zone_low": (
      None if structural_zone_low is None else float(structural_zone_low)
    ),
    "structural_zone_high": (
      None if structural_zone_high is None else float(structural_zone_high)
    ),
    "active_reaction_id": active_reaction_id,
    "candidate_id": candidate_id,
    "group_id": group_id,
    "state": state,
    "claimed_at": int(claimed_at),
    "touch_bar_ts": touch_bar_ts,
    "confirmation_bar_ts": confirmation_bar_ts,
    "thesis_cycle": int(thesis_cycle),
    "terminal_at": terminal_at,
    "exit_detected_at": exit_detected_at,
    "first_outside_bar_ts": first_outside_bar_ts,
    "latest_outside_bar_ts": latest_outside_bar_ts,
    "outside_bar_count": int(outside_bar_count),
    "reentry_bar_ts": reentry_bar_ts,
    "rearm_ready": bool(rearm_ready),
  }, separators=(",", ":"), sort_keys=True)


def parse_reaction_claim(raw: object) -> dict[str, Any] | None:
  return _parse_claim_json(raw)


def parse_thesis_claim(raw: object) -> dict[str, Any] | None:
  return _parse_claim_json(raw)


def _parse_claim_json(raw: object) -> dict[str, Any] | None:
  if raw is None:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    payload = json.loads(text)
  except (TypeError, ValueError, json.JSONDecodeError):
    return None
  if not isinstance(payload, dict):
    return None
  return payload


def dump_claim(payload: dict[str, Any]) -> str:
  return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def price_left_zone(
  price: float,
  lo: float,
  hi: float,
  *,
  atr: float,
  rearm_atr: float,
) -> bool:
  if not math.isfinite(price) or atr <= 0:
    return False
  band = max(0.0, float(rearm_atr)) * float(atr)
  if price < lo:
    return (lo - price) >= band
  if price > hi:
    return (price - hi) >= band
  return False


def exit_distance_atr(
  price: float,
  lo: float,
  hi: float,
  *,
  atr: float,
) -> float:
  if not math.isfinite(price) or atr <= 0:
    return 0.0
  if price < lo:
    return (lo - price) / atr
  if price > hi:
    return (price - hi) / atr
  return 0.0


def price_inside_or_touching_zone(
  price: float,
  lo: float,
  hi: float,
  *,
  bar_low: float | None = None,
  bar_high: float | None = None,
) -> bool:
  """True when spot or the bar range touches the raw structural zone."""
  if lo > hi:
    lo, hi = hi, lo
  if math.isfinite(price) and lo - 1e-9 <= price <= hi + 1e-9:
    return True
  if bar_low is None or bar_high is None:
    return False
  if not (math.isfinite(bar_low) and math.isfinite(bar_high)):
    return False
  return not (bar_high < lo - 1e-9 or bar_low > hi + 1e-9)


def thesis_state_blocks_new_initial(state: str | None) -> bool:
  value = str(state or "").casefold()
  return value in ACTIVE_THESIS_STATES or value in POST_TERMINAL_THESIS_STATES


@dataclass(frozen=True)
class ThesisRearmDecision:
  allowed: bool
  state: str
  reason_code: str
  outside_bar_count: int
  required_outside_bars: int
  exit_distance_atr: float
  required_exit_atr: float
  previous_confirmation_ts: str
  new_touch_ts: str
  new_confirmation_ts: str
  claim_updates: dict[str, Any] | None = None


def evaluate_thesis_rearm_for_publish(
  claim: dict[str, Any],
  *,
  new_touch_ts: str,
  new_confirmation_ts: str,
  price: float,
  atr: float,
  rearm_atr: float,
  rearm_bars: int,
) -> ThesisRearmDecision:
  """Decide whether a *new* reaction may start a thesis cycle.

  Only ``rearm_ready`` (or fully reusable closed/cancelled/...) allows publish.
  Outside-bar progression is advanced by :func:`advance_thesis_rearm_on_bar`.
  """
  state = str(claim.get("state") or "").casefold()
  previous_confirm = str(claim.get("confirmation_bar_ts") or "")
  required_bars = max(1, int(rearm_bars))
  required_atr = max(0.0, float(rearm_atr))
  lo = claim.get("structural_zone_low")
  hi = claim.get("structural_zone_high")
  try:
    zone_lo = float(lo) if lo is not None else float("nan")
    zone_hi = float(hi) if hi is not None else float("nan")
  except (TypeError, ValueError):
    zone_lo = float("nan")
    zone_hi = float("nan")
  distance = (
    exit_distance_atr(price, zone_lo, zone_hi, atr=atr)
    if math.isfinite(zone_lo) and math.isfinite(zone_hi)
    else 0.0
  )
  outside_count = int(claim.get("outside_bar_count") or 0)

  if state in ACTIVE_THESIS_STATES:
    return ThesisRearmDecision(
      False,
      state,
      "active_thesis_group",
      outside_count,
      required_bars,
      distance,
      required_atr,
      previous_confirm,
      new_touch_ts,
      new_confirmation_ts,
    )

  if state in POST_TERMINAL_THESIS_STATES:
    return ThesisRearmDecision(
      False,
      state,
      "thesis_waiting_rearm",
      outside_count,
      required_bars,
      distance,
      required_atr,
      previous_confirm,
      new_touch_ts,
      new_confirmation_ts,
    )

  if state not in REARM_READY_THESIS_STATES and state != "rearm_ready":
    return ThesisRearmDecision(
      False,
      state or "unknown",
      "thesis_not_rearm_ready",
      outside_count,
      required_bars,
      distance,
      required_atr,
      previous_confirm,
      new_touch_ts,
      new_confirmation_ts,
    )

  if not previous_confirm or not new_touch_ts or not new_confirmation_ts:
    return ThesisRearmDecision(
      False,
      state,
      "missing_reaction_timestamps",
      outside_count,
      required_bars,
      distance,
      required_atr,
      previous_confirm,
      new_touch_ts,
      new_confirmation_ts,
    )
  if new_touch_ts <= previous_confirm or new_confirmation_ts <= previous_confirm:
    return ThesisRearmDecision(
      False,
      state,
      "reaction_not_newer",
      outside_count,
      required_bars,
      distance,
      required_atr,
      previous_confirm,
      new_touch_ts,
      new_confirmation_ts,
    )
  if not bool(claim.get("rearm_ready")) and state != "rearm_ready":
    # cancelled/rejected/expired without a completed exit cycle still need
    # rearm_ready=True unless they never filled (no structural occupancy).
    if state in {"cancelled", "rejected", "expired"} and claim.get("terminal_at"):
      pass
    elif state == "closed" and not claim.get("rearm_ready"):
      return ThesisRearmDecision(
        False,
        state,
        "thesis_waiting_rearm",
        outside_count,
        required_bars,
        distance,
        required_atr,
        previous_confirm,
        new_touch_ts,
        new_confirmation_ts,
      )

  return ThesisRearmDecision(
    True,
    "rearm_ready",
    "rearm_allowed",
    outside_count,
    required_bars,
    distance,
    required_atr,
    previous_confirm,
    new_touch_ts,
    new_confirmation_ts,
  )


def advance_thesis_rearm_on_bar(
  claim: dict[str, Any],
  *,
  bar_ts: str,
  bar_low: float,
  bar_high: float,
  close: float,
  atr: float,
  rearm_atr: float,
  rearm_bars: int,
  now_ts: int,
) -> tuple[dict[str, Any], str | None]:
  """Advance exit / outside-bar / re-entry tracking for one closed M1 bar.

  Returns (updated_claim, metric_name_or_None).
  """
  state = str(claim.get("state") or "").casefold()
  if state not in POST_TERMINAL_THESIS_STATES and state != "closed":
    if state == "rearm_ready":
      return claim, None
    return claim, None

  lo_raw = claim.get("structural_zone_low")
  hi_raw = claim.get("structural_zone_high")
  try:
    lo = float(lo_raw)
    hi = float(hi_raw)
  except (TypeError, ValueError):
    return claim, None
  if not (math.isfinite(lo) and math.isfinite(hi)):
    return claim, None

  required_bars = max(1, int(rearm_bars))
  required_atr = max(0.0, float(rearm_atr))
  updated = dict(claim)
  metric: str | None = None
  latest = str(updated.get("latest_outside_bar_ts") or "")

  outside = price_left_zone(
    float(close),
    lo,
    hi,
    atr=atr,
    rearm_atr=required_atr,
  )
  # Prefer bar extremes for leave detection so wicks count as exit.
  if not outside:
    outside = (
      price_left_zone(float(bar_low), lo, hi, atr=atr, rearm_atr=required_atr)
      or price_left_zone(float(bar_high), lo, hi, atr=atr, rearm_atr=required_atr)
    )

  touching = price_inside_or_touching_zone(
    float(close),
    lo,
    hi,
    bar_low=float(bar_low),
    bar_high=float(bar_high),
  )

  if state in {"terminal_waiting_exit", "closed"} and outside:
    updated["state"] = "outside_zone"
    updated["exit_detected_at"] = int(now_ts)
    updated["rearm_ready"] = False
    metric = "mapped_thesis_exit_detected"
    state = "outside_zone"

  if state == "outside_zone":
    if outside:
      if bar_ts and bar_ts != latest:
        count = int(updated.get("outside_bar_count") or 0) + 1
        updated["outside_bar_count"] = count
        updated["latest_outside_bar_ts"] = bar_ts
        if not updated.get("first_outside_bar_ts"):
          updated["first_outside_bar_ts"] = bar_ts
        metric = "mapped_thesis_outside_bar_counted"
    elif touching:
      # Returned inside before completing outside count — reset unless ready.
      if int(updated.get("outside_bar_count") or 0) < required_bars:
        updated["outside_bar_count"] = 0
        updated["first_outside_bar_ts"] = None
        updated["latest_outside_bar_ts"] = None
        updated["exit_detected_at"] = None
        updated["state"] = "terminal_waiting_exit"
        updated["rearm_ready"] = False
      elif int(updated.get("outside_bar_count") or 0) >= required_bars:
        updated["state"] = "rearm_ready"
        updated["rearm_ready"] = True
        updated["reentry_bar_ts"] = bar_ts
        metric = "mapped_thesis_rearm_ready"

  if (
    state == "outside_zone"
    and int(updated.get("outside_bar_count") or 0) >= required_bars
    and touching
    and not updated.get("rearm_ready")
  ):
    updated["state"] = "rearm_ready"
    updated["rearm_ready"] = True
    updated["reentry_bar_ts"] = bar_ts
    metric = "mapped_thesis_rearm_ready"

  return updated, metric


# Lua: acquire thesis claim only when absent or rearm_ready/reusable.
THESIS_CLAIM_ACQUIRE_LUA = """
local key = KEYS[1]
local payload = ARGV[1]
local existing = redis.call('GET', key)
if not existing then
  redis.call('SET', key, payload)
  return 1
end
local ok, claim = pcall(cjson.decode, existing)
if not ok or type(claim) ~= 'table' then
  return 0
end
local state = string.lower(tostring(claim['state'] or ''))
local rearm = claim['rearm_ready'] == true or claim['rearm_ready'] == 1
if state == 'rearm_ready' or ((state == 'closed' or state == 'cancelled'
    or state == 'rejected' or state == 'expired') and rearm) then
  redis.call('SET', key, payload)
  return 1
end
if state == 'cancelled' or state == 'rejected' or state == 'expired' then
  -- Never-filled / rejected paths may recycle without a full exit cycle.
  if claim['terminal_at'] ~= nil and (claim['group_id'] == nil or claim['group_id'] == '') then
    redis.call('SET', key, payload)
    return 1
  end
  if claim['filled'] ~= true and (state == 'cancelled' or state == 'rejected'
      or state == 'expired') and claim['outside_bar_count'] == nil then
    redis.call('SET', key, payload)
    return 1
  end
end
return 0
"""
