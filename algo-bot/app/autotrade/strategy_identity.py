"""Stable automatic-trade identities independent of legacy detectors.

These hashes are persistence contracts.  They are deliberately kept in the
execution package so reading or validating a Go-owned StrategyMatch never
imports Python zone construction code.
"""

from __future__ import annotations

import hashlib


STRUCTURAL_SETUPS = frozenset({
  "Key Level",
  "Zone Reaction",
  "Flip Zone",
  "Demand Zone Reaction",
  "Supply Zone Reaction",
  "Session Level",
  "Trendline",
  "Supply Demand",
  "Order Block",
  "FVG",
  "iFVG",
  "CRT",
  "Confluence Zone",
})

_ZONE_REACTION_ALIASES = frozenset({
  "Zone Reaction",
  "Demand Zone Reaction",
  "Supply Zone Reaction",
})


def canonical_structural_setup(setup: str) -> str:
  key = str(setup or "")
  return "Zone Reaction" if key in _ZONE_REACTION_ALIASES else key


def bias_relationship(htf_bias: str, direction: str) -> str:
  bias = str(htf_bias or "").casefold()
  side = str(direction or "").upper()
  if bias not in {"up", "down"}:
    return "neutral"
  aligned = (bias == "up" and side == "BUY") or (
    bias == "down" and side == "SELL"
  )
  return "with_bias" if aligned else "counter_bias"


def structural_hash(*parts: object) -> str:
  raw = "|".join(str(part) for part in parts)
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def confluence_setup_id(zone_id: str, direction: str) -> str:
  return hashlib.sha256(
    f"confluence-setup|{zone_id}|{direction.upper()}".encode("utf-8"),
  ).hexdigest()[:32]


def structural_thesis_id(
  *,
  symbol: str,
  strategy: str,
  direction: str,
  structural_source: str,
  structural_id: str,
  touch_bar_ts: str,
  confirmation_bar_ts: str,
  version: int = 1,
) -> str:
  return structural_hash(
    f"v{version}",
    symbol.upper(),
    canonical_structural_setup(strategy),
    direction.upper(),
    structural_source,
    structural_id,
    touch_bar_ts or "",
    confirmation_bar_ts or "",
  )


def thesis_id(
  *,
  symbol: str,
  strategy_family: str,
  direction: str,
  structural_id: str,
  version: int = 1,
) -> str:
  """Stable execution thesis identity for one Go-owned structure."""
  return structural_hash(
    "thesis",
    f"v{version}",
    symbol.upper(),
    strategy_family,
    direction.upper(),
    structural_id,
  )
