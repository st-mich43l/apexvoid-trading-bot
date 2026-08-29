"""One-shot backfill of auto_trade_fills / auto_trade_results from Redis events.

Replays ``auto_trade:events`` through ``record_auto_trade_event`` so TradePlan
``order_filled`` / ``position_closed`` rows that were skipped before the
trade_stats fix appear in ``/trade_stats``.

Usage (inside the bot container or with env pointed at prod Redis/Postgres):

  python -m app.scripts.backfill_auto_trade_stats
  python -m app.scripts.backfill_auto_trade_stats --count 2000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from app.persistence import redis_state, store

log = logging.getLogger("backfill_auto_trade_stats")

_FILL_TYPES = frozenset({"opened", "add", "manual_opened", "order_filled"})
_RESULT_TYPES = frozenset({"group_result", "position_closed", "manual_closed"})


async def backfill(*, count: int, stream: str) -> dict[str, int]:
  client = redis_state.get_client()
  await store.init_db()
  entries = await client.xrevrange(stream, count=count)
  # Replay oldest → newest so fills land before closes.
  entries = list(reversed(entries))
  before_fills = await _count("auto_trade_fills")
  before_results = await _count("auto_trade_results")
  stats = {
    "seen": 0,
    "fill_events": 0,
    "result_events": 0,
    "errors": 0,
  }
  for _entry_id, fields in entries:
    stats["seen"] += 1
    try:
      event = json.loads(fields["payload"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
      stats["errors"] += 1
      log.warning("skip invalid event: %s", exc)
      continue
    event_type = str(event.get("type") or "")
    await store.record_auto_trade_event(event)
    if event_type in _FILL_TYPES:
      stats["fill_events"] += 1
    elif event_type in _RESULT_TYPES:
      stats["result_events"] += 1
  stats["fills_delta"] = await _count("auto_trade_fills") - before_fills
  stats["results_delta"] = await _count("auto_trade_results") - before_results
  return stats


async def _count(table: str) -> int:
  async with store._connect() as db:
    return int(await db.fetchval(f"SELECT COUNT(*) FROM {table}") or 0)


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--count", type=int, default=2000)
  parser.add_argument("--stream", default="auto_trade:events")
  args = parser.parse_args()
  logging.basicConfig(level="INFO", format="%(levelname)s: %(message)s")
  stats = asyncio.run(backfill(count=args.count, stream=args.stream))
  log.info("backfill done %s", stats)


if __name__ == "__main__":
  main()
