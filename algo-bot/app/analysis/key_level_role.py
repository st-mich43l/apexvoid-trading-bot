"""Pure closed-bar semantic role classification for key levels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ROLE_SUPPORT = "support"
ROLE_RESISTANCE = "resistance"
ROLE_AMBIGUOUS = "ambiguous"
ROLE_BROKEN_SUPPORT = "broken_support"
ROLE_BROKEN_RESISTANCE = "broken_resistance"


@dataclass(frozen=True)
class KeyLevelRoleDecision:
  role: str
  accepted_above: int
  accepted_below: int
  required_closes: int


def _accepted_count(closes: list[float], predicate) -> int:
  count = 0
  for close in reversed(closes):
    if not predicate(close):
      break
    count += 1
  return count


def classify_key_level_role(
  *,
  kind: str | None,
  level_price: float,
  band_low: float,
  band_high: float,
  closed_bars: Any,
  breakout_accept_bars: int,
) -> KeyLevelRoleDecision:
  """Classify the level before any directional reaction is considered.

  Explicit support/resistance semantics remain authoritative until enough
  consecutive closed bars accept beyond the opposite edge. An accepted
  break is deliberately returned as a broken role so Break & Retest owns
  any later flip; Key Level Reaction must not reinterpret it in place.
  """
  required = max(1, int(breakout_accept_bars))
  closes: list[float] = []
  try:
    closes = [
      float(value)
      for value in closed_bars["close"].dropna().tolist()
    ]
  except (KeyError, TypeError, ValueError, AttributeError):
    closes = []
  above = _accepted_count(closes, lambda close: close > float(band_high))
  below = _accepted_count(closes, lambda close: close < float(band_low))
  normalized = str(kind or "").strip().casefold()
  explicit_support = "support" in normalized or "low" in normalized
  explicit_resistance = "resist" in normalized or "high" in normalized

  if explicit_support:
    role = (
      ROLE_BROKEN_SUPPORT if below >= required else ROLE_SUPPORT
    )
  elif explicit_resistance:
    role = (
      ROLE_BROKEN_RESISTANCE if above >= required else ROLE_RESISTANCE
    )
  elif above >= required:
    role = ROLE_BROKEN_RESISTANCE
  elif below >= required:
    role = ROLE_BROKEN_SUPPORT
  else:
    role = ROLE_AMBIGUOUS
  return KeyLevelRoleDecision(role, above, below, required)
