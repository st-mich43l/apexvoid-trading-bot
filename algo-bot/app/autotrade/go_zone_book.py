"""Reads the Go analysis-engine's own published zone book (analysis-engine/
internal/transport/redis/zonebook.go) for the worker's execution-time
opposing-barrier/target-room recheck (structural_target_room.py) on a
Go-origin match.

Why this exists: the check needs a real, standing structural fact -
"is there a competing supply/demand zone between this entry and its
target" - that the Go engine's own opportunity contract does not carry
(``technical_context`` has ATR/bias/confirmation, no zone list; see
docs/analysis/s14-operator-report.md and the owner conversation that led
here). Go already computes real Supply/Demand zones per symbol/timeframe
continuously (the same ``zone.Zone`` supply.go/demand.go and Key Level's own
``opposing.go`` already read); this module reads the flattened snapshot Go
publishes of that live state, rather than Python re-detecting zones from
raw OHLC for a Go-origin match. The adapter accepts an explicit fallback for
isolated callers/tests, but production passes an empty fallback so Go-origin
execution never silently reintroduces Python technical detection. Python-
origin matches use worker.py's separate StructuralBarrierBook path and never
touch this module.

A missing or unparseable zone book is reported as unavailable (``None``),
never as an empty tuple: "Go has not published anything yet/recently" and
"Go published that there is genuinely no opposing zone" are different
facts, and only the second one may make the recheck a real pass.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from app.autotrade.structural_barriers import (
  DEFAULT_STRUCTURAL_TIMEFRAMES,
  StructuralBarrier,
  canonicalize_structural_barriers,
  to_opposing_entries,
)
from app.autotrade.structural_target_room import ZoneOpposingEntry

log = logging.getLogger(__name__)

# Must match analysis-engine/internal/transport/redis/zonebook.go::ZoneBookKey
# exactly - a naming drift here silently makes this module read nothing.
_KEY_PREFIX = "analysis:zone_book"

# Zone lifecycle states this side still treats as a real, standing barrier -
# mirrors keylevel/opposing.go's identical "not Invalidated/Mitigated" filter
# on the Go side, so a Go-origin match's opposing check and Key Level's own
# detection-time opposing check never disagree about what counts as live.
_LIVE_STATES = frozenset({"fresh", "touched", "partially_mitigated"})


def go_zone_book_key(symbol: str) -> str:
  return f"{_KEY_PREFIX}:{symbol.upper()}"


def parse_zone_book(
  raw: str,
  *,
  pip_size: float | None = None,
  max_width_atr: float | None = None,
  max_width_pips: float | None = None,
  timeframes: tuple[str, ...] = DEFAULT_STRUCTURAL_TIMEFRAMES,
) -> tuple[ZoneOpposingEntry, ...]:
  """Pure parse: Go's published JSON -> the same ZoneOpposingEntry shape
  structural_target_room.py's own zone_opposing_entries() builds from a
  Python Zone. The production caller supplies the shared execution width
  limits, after which this adapter applies the same M5/M15/H1 scope,
  same-side merge and cross-side reconciliation as StructuralBarrierBook.
  Raises on malformed JSON/missing fields; callers decide what
  "unavailable" means for their own code path.
  """
  doc: dict[str, Any] = json.loads(raw)
  allowed_timeframes = {str(tf).upper() for tf in timeframes}
  barriers: list[StructuralBarrier] = []
  for item in doc["entries"]:
    kind = str(item["kind"])
    if kind not in ("supply", "demand"):
      continue
    if str(item["state"]) not in _LIVE_STATES:
      continue
    timeframe = str(item["timeframe"]).upper()
    if timeframe not in allowed_timeframes:
      continue
    low = float(item["low"])
    high = float(item["high"])
    atr = float(item.get("atr", 0.0) or 0.0)
    width = high - low
    if not all(math.isfinite(value) for value in (low, high, atr)) or width <= 0:
      continue
    if (
      pip_size is not None
      and max_width_pips is not None
      and float(pip_size) > 0
      and width / float(pip_size) > float(max_width_pips)
    ):
      continue
    # ``atr`` is additive in the Redis contract. During a rolling upgrade an
    # older book may omit it; retain the absolute-pip safety gate above and
    # begin enforcing the ATR gate as soon as the v2 publisher is live.
    if (
      max_width_atr is not None
      and atr > 0
      and width / atr > float(max_width_atr)
    ):
      continue
    barriers.append(StructuralBarrier(
      side="sell" if kind == "supply" else "buy",
      low=low,
      high=high,
      tier="zone",
      score=float(item.get("strength", 0.0) or 0.0),
      touches=int(item.get("touch_count", 0) or 0),
      mitigated=False,  # already filtered to live states above
      source_timeframes=(timeframe,),
    ))
  return to_opposing_entries(canonicalize_structural_barriers(barriers))


async def go_zone_opposing_entries(
  client: Any,
  symbol: str,
  *,
  pip_size: float | None = None,
  max_width_atr: float | None = None,
  max_width_pips: float | None = None,
) -> tuple[ZoneOpposingEntry, ...] | None:
  """The Go-published opposing-structure book for symbol, or None if it is
  missing/unreadable (the engine has not published yet, the key expired, or
  the payload failed to parse) - never a silent empty tuple in that case.
  """
  try:
    raw = await client.get(go_zone_book_key(symbol))
  except Exception as exc:  # noqa: BLE001 - Redis errors must not crash the plan build
    log.warning("go zone book read failed symbol=%s error=%s", symbol, exc)
    return None
  if not raw:
    return None
  try:
    return parse_zone_book(
      raw,
      pip_size=pip_size,
      max_width_atr=max_width_atr,
      max_width_pips=max_width_pips,
    )
  except Exception as exc:  # noqa: BLE001 - a malformed publish must not crash the plan build
    log.warning("go zone book parse failed symbol=%s error=%s", symbol, exc)
    return None


async def opposing_entries_for_go_match(
  client: Any,
  symbol: str,
  *,
  python_fallback: tuple[ZoneOpposingEntry, ...],
  pip_size: float | None = None,
  max_width_atr: float | None = None,
  max_width_pips: float | None = None,
) -> tuple[ZoneOpposingEntry, ...]:
  """Go's own live zone book when available; otherwise an explicit caller
  fallback. Production supplies ``()`` to preserve Go-only technical
  ownership; the fallback parameter remains for compatibility and isolated
  callers that deliberately opt into another source.
  """
  go_entries = await go_zone_opposing_entries(
    client,
    symbol,
    pip_size=pip_size,
    max_width_atr=max_width_atr,
    max_width_pips=max_width_pips,
  )
  if go_entries is not None:
    return go_entries
  return python_fallback
