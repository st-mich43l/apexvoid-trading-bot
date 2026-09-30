"""Reads the opposing-barrier book the Go analysis-engine publishes (analysis-
engine/internal/transport/redis/zonebook.go, ``barriers``) for the worker's
execution-time opposing-barrier/target-room recheck (structural_target_room.py)
on a Go-origin match.

Go owns technical structure. It computes the barrier set - live zones only,
M5/M15/H1 scope, the execution-width gate, same-side merge and cross-side
reconciliation (analysis-engine/internal/barrier) - and this module only
adapts that published list into the ``ZoneOpposingEntry`` shape the shared
target-room check reads. Algo Bot never re-derives structure for a Go-origin
match: there is no Python fallback and no raw-zone normalization here.

A missing or unparseable zone book, or one without a published ``barriers``
list, is reported as unavailable (``None``), never as an empty tuple: "Go has
not published anything yet/recently" and "Go published that there is
genuinely no opposing structure" (``barriers == []``) are different facts.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from app.autotrade.structural_target_room import ZoneOpposingEntry

log = logging.getLogger(__name__)

# Must match analysis-engine/internal/transport/redis/zonebook.go::ZoneBookKey
# exactly - a naming drift here silently makes this module read nothing.
_KEY_PREFIX = "analysis:zone_book"


def go_zone_book_key(symbol: str) -> str:
  return f"{_KEY_PREFIX}:{symbol.upper()}"


def parse_zone_book(raw: str) -> tuple[ZoneOpposingEntry, ...]:
  """Pure parse: Go's published ``barriers`` list -> ZoneOpposingEntry.

  Raises on malformed JSON or when the document carries no published barrier
  list (older publisher, or a symbol Go has no barrier policy for) so the
  caller reports "unavailable" instead of mistaking it for "no barriers".
  """
  doc: dict[str, Any] = json.loads(raw)
  published = doc.get("barriers")
  if not isinstance(published, list):
    raise ValueError("zone book has no published barriers list")
  entries = []
  for item in published:
    side = str(item["side"])
    if side not in ("buy", "sell"):
      continue
    low = float(item["low"])
    high = float(item["high"])
    if not (math.isfinite(low) and math.isfinite(high)) or high < low:
      continue
    entries.append(ZoneOpposingEntry(
      side=side,
      lo=low,
      hi=high,
      tier=str(item.get("tier") or "zone"),
      score=float(item.get("score", 0.0) or 0.0),
      touches=int(item.get("touches", 0) or 0),
      mitigated=False,  # Go only publishes live standing barriers
    ))
  return tuple(entries)


async def go_zone_opposing_entries(client: Any, symbol: str) -> tuple[ZoneOpposingEntry, ...] | None:
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
    return parse_zone_book(raw)
  except Exception as exc:  # noqa: BLE001 - a malformed publish must not crash the plan build
    log.warning("go zone book parse failed symbol=%s error=%s", symbol, exc)
    return None


async def opposing_entries_for_go_match(client: Any, symbol: str) -> tuple[ZoneOpposingEntry, ...]:
  """Go's published barriers for symbol. When Go's book is unavailable the
  worker continues with no barriers (and the warning above): Go-origin
  execution never substitutes a Python-derived structure.
  """
  go_entries = await go_zone_opposing_entries(client, symbol)
  return () if go_entries is None else go_entries
