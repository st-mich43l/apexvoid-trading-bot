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
from typing import Any, Iterable

from app.runtime.price_identity import price_token


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


def _stable_atr(atr: float, pip_size: float) -> float:
  """Preserves the stable ATR identity quantization used by Go matches.

  Quantizes ATR into a coarse step before it can affect bucket size, so the
  bucket grid only moves on a genuine regime-scale ATR change rather than
  routine bar-to-bar noise in a rolling per-bar indicator - see the fuller
  explanation and the live incident this fixed in confluence_zone.py.
  """
  step = float(pip_size) * 40.0
  return round(max(0.0, float(atr)) / step) * step


def canonicalize_zone_bucket(
  lo: float,
  hi: float,
  *,
  atr: float,
  pip_size: float,
) -> float:
  """Bucket mid so minor map jitter shares one structural identity.

  Width is deliberately NOT part of this bucket. The location bucket ensures
  two detections of the same structural area measured a few tenths of a price
  unit apart in width can
  straddle the width bucket's own rounding boundary and produce two
  different ids for one zone, each publishing its own TradePlan. Only
  location (the bucketed mid) determines identity here.
  """
  mid = (float(lo) + float(hi)) / 2.0
  bucket = max(
    float(pip_size) * 10.0,
    _stable_atr(atr, pip_size) * 0.25,
  )
  return round(mid / bucket) * bucket


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
  mid_b = canonicalize_zone_bucket(lo, hi, atr=atr, pip_size=pip_size)
  structural = ",".join(
    sorted({
      str(tag).casefold()
      for tag in (tags or ())
      if str(tag).casefold() in _STRUCTURAL_TAGS
    })
  )
  return _sha(
    f"sz|{symbol.upper()}|{side}|{source_tf.upper()}|"
    f"{price_token(mid_b, pip_size=pip_size)}|{structural}"
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
