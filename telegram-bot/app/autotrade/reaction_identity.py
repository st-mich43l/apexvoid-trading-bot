"""Stable identity for Mapped Zone Reaction theses.

A single structural reaction sequence must map to one match, one candidate,
one group, and one broker initial order — across tick replay, lookback memory,
and minor zone-coordinate drift.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable


REACTION_ID_VERSION = 1
REACTION_CLAIM_KEY_PREFIX = "auto_trade:reaction_claim"
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
) -> str:
  """Thesis family for a mapped zone (independent of a specific reaction)."""
  return _sha(
    f"thesis|{symbol.upper()}|{strategy}|{direction.upper()}|{structural_zone_id}"
  )


def mapped_group_id(
  *,
  symbol: str,
  strategy_family: str,
  direction: str,
  reaction_id: str,
) -> str:
  return _sha(
    f"group|{symbol.upper()}|{strategy_family}|{direction.upper()}|{reaction_id}"
  )


def reaction_claim_key(reaction_id: str) -> str:
  return f"{REACTION_CLAIM_KEY_PREFIX}:{reaction_id}"


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
) -> str:
  return json.dumps({
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
  }, separators=(",", ":"), sort_keys=True)


def parse_reaction_claim(raw: object) -> dict[str, Any] | None:
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
