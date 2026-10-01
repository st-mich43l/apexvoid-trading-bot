"""Reads the set of opportunity IDs the Go analysis-engine currently holds live
(analysis-engine/internal/transport/redis/liveopportunities.go).

Go rebuilds its opportunity book by replaying stored history under the
current rules on every restart, so an opportunity an earlier build created
and the current rules would not is absent from this set. Algo Bot executes a
Go-origin match only while Go still vouches for it: an old event that Go no
longer holds cannot open a plan.

A missing, expired or unreadable set is reported as unavailable (``None``),
never as an empty set: "Go has not published yet" and "Go holds nothing live"
are different facts. The worker fails open on ``None`` exactly as it does for
the zone book, so an engine outage never halts execution.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

# Must match analysis-engine/internal/transport/redis/liveopportunities.go::
# LiveOpportunitiesKey exactly - a naming drift here silently disables the
# check (the worker would read nothing and fail open).
_KEY_PREFIX = "analysis:live_opportunities"


def go_live_opportunities_key(symbol: str) -> str:
  return f"{_KEY_PREFIX}:{symbol.upper()}"


def parse_live_opportunities(raw: str | bytes) -> frozenset[str]:
  """Pure parse: Go's published ``ids`` list -> a set of opportunity IDs.

  Raises on malformed JSON or when the document carries no ``ids`` list so
  the caller reports "unavailable" instead of "nothing is live".
  """
  doc: dict[str, Any] = json.loads(raw)
  ids = doc.get("ids")
  if not isinstance(ids, list):
    raise ValueError("live opportunities document has no ids list")
  return frozenset(str(item) for item in ids if isinstance(item, str) and item)


async def go_live_opportunity_ids(
  client: Any,
  symbol: str,
  *,
  minimum_generated_at: int | None = None,
) -> frozenset[str] | None:
  """The IDs Go currently holds live for symbol, or None when unavailable.

  ``minimum_generated_at`` protects the Kafka/Redis boundary during an engine
  restart.  A lifecycle or arbitration event can reach Kafka before the
  corresponding Redis projection is written.  Treating an older projection as
  an authoritative empty set permanently suppresses a valid Go opportunity;
  an older projection is therefore unavailable until Go publishes a current
  one.
  """
  try:
    raw = await client.get(go_live_opportunities_key(symbol))
  except Exception as exc:  # noqa: BLE001 - Redis errors must not crash the cycle
    log.warning("go live opportunities read failed symbol=%s error=%s", symbol, exc)
    return None
  if not raw:
    return None
  try:
    document = json.loads(raw)
    ids = parse_live_opportunities(raw)
    if minimum_generated_at is not None:
      generated_at = document.get("generated_at")
      # V1 live-set documents did not carry a projection timestamp. Preserve
      # their existing compatibility behavior; only a present, older Go V2
      # timestamp is evidence that Kafka outran Redis.
      if (
        generated_at is not None
        and not isinstance(generated_at, bool)
        and isinstance(generated_at, (int, float))
        and int(generated_at) < int(minimum_generated_at)
      ):
        log.info(
          "Go live opportunities projection is behind event symbol=%s generated_at=%s required_at=%s",
          symbol, generated_at, minimum_generated_at,
        )
        return None
    return ids
  except Exception as exc:  # noqa: BLE001 - a malformed publish must not crash the cycle
    log.warning("go live opportunities parse failed symbol=%s error=%s", symbol, exc)
    return None
