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
raw OHLC for a Go-origin match (worker.py's own Python recompute,
``_htf_zones``/``_structural_barrier_entries``, remains the fallback -
see ``opposing_entries_for_go_match`` below - and the only path for a
Python-origin match, which this module never touches).

A missing or unparseable zone book is reported as unavailable (``None``),
never as an empty tuple: "Go has not published anything yet/recently" and
"Go published that there is genuinely no opposing zone" are different
facts, and only the second one may make the recheck a real pass.
"""

from __future__ import annotations

import json
import logging
from typing import Any

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


def parse_zone_book(raw: str) -> tuple[ZoneOpposingEntry, ...]:
  """Pure parse: Go's published JSON -> the same ZoneOpposingEntry shape
  structural_target_room.py's own zone_opposing_entries() builds from a
  Python Zone. Raises on malformed JSON/missing fields; callers decide
  what "unavailable" means for their own code path.
  """
  doc: dict[str, Any] = json.loads(raw)
  entries = []
  for item in doc["entries"]:
    kind = str(item["kind"])
    if kind not in ("supply", "demand"):
      continue
    if str(item["state"]) not in _LIVE_STATES:
      continue
    entries.append(ZoneOpposingEntry(
      side="sell" if kind == "supply" else "buy",
      lo=float(item["low"]),
      hi=float(item["high"]),
      tier="zone",
      score=float(item.get("strength", 0.0) or 0.0),
      touches=int(item.get("touch_count", 0) or 0),
      mitigated=False,  # already filtered to live states above
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


async def opposing_entries_for_go_match(
  client: Any, symbol: str, *, python_fallback: tuple[ZoneOpposingEntry, ...],
) -> tuple[ZoneOpposingEntry, ...]:
  """Go's own live zone book when available; otherwise the caller's own
  Python-recomputed entries (worker.py already builds these from real OHLC
  for every match regardless of origin - this only prefers the fresher,
  single-source-of-truth read when Go has actually published one).
  """
  go_entries = await go_zone_opposing_entries(client, symbol)
  if go_entries is not None:
    return go_entries
  return python_fallback
