"""One-shot backfill of auto_trade_fills / auto_trade_results from Redis events.

Replays ``auto_trade:events`` through ``record_auto_trade_event`` so TradePlan
``order_filled`` / ``position_closed`` rows that were skipped before the
trade_stats fix appear in ``/trade_stats``.

Then reconciles orphan fills (fill row, no result) from retained
``execution:plan_runtime:{group_id}`` JSON — needed when the close event was
trimmed from the maxlen stream or never published (unknown_leg_close).

Usage (inside the bot container or with env pointed at prod Redis/Postgres):

  python -m app.scripts.backfill_auto_trade_stats
  python -m app.scripts.backfill_auto_trade_stats --count 5000
  python -m app.scripts.backfill_auto_trade_stats --no-runtime-reconcile
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


def _text(value: object) -> str:
  return value.decode() if isinstance(value, bytes) else str(value)


def _parse_event(fields: dict) -> dict | None:
  raw = fields.get("payload")
  if raw is None:
    raw = fields.get(b"payload")
  if raw is None:
    return None
  try:
    event = json.loads(_text(raw))
  except (TypeError, json.JSONDecodeError):
    return None
  return event if isinstance(event, dict) else None


async def _read_stream_entries(client, stream: str, *, count: int | None) -> list:
  """Read retained stream oldest→newest.

  ``count`` caps how many of the *newest* retained entries to keep (then
  replayed oldest-first). ``None`` reads the full retained window.
  """
  if count is not None and count > 0:
    newest = await client.xrevrange(stream, count=count)
    return list(reversed(newest))

  entries: list = []
  cursor = "-"
  while True:
    batch = await client.xrange(stream, min=cursor, max="+", count=500)
    if not batch:
      break
    if cursor != "-":
      # xrange min is inclusive; skip the boundary we already have.
      batch = [item for item in batch if _text(item[0]) != cursor]
      if not batch:
        break
    entries.extend(batch)
    cursor = _text(batch[-1][0])
    if len(batch) < 500:
      break
  return entries


async def _load_json(client, key: str) -> dict | None:
  raw = await client.get(key)
  if raw is None:
    return None
  try:
    payload = json.loads(_text(raw))
  except (TypeError, json.JSONDecodeError):
    return None
  return payload if isinstance(payload, dict) else None


async def reconcile_from_plan_runtimes(client) -> dict[str, int]:
  """Fill→result gaps using retained V8 plan_runtime blobs."""
  stats = {"orphan_groups": 0, "runtime_hits": 0, "reconciled": 0, "skipped": 0}
  group_ids = await store.list_orphan_auto_trade_group_ids()
  stats["orphan_groups"] = len(group_ids)
  for group_id in group_ids:
    runtime = await _load_json(client, f"execution:plan_runtime:{group_id}")
    if runtime is None:
      stats["skipped"] += 1
      continue
    stats["runtime_hits"] += 1
    recovery = await _load_json(client, f"execution:plan_recovery:{group_id}")
    closed_at = None
    async with store._connect() as db:
      closed_at = await db.fetchval(
        "SELECT MAX(filled_at) FROM auto_trade_fills WHERE group_id = $1",
        group_id,
      )
    wrote = await store.reconcile_orphan_auto_trade_result(
      group_id,
      runtime,
      recovery=recovery,
      closed_at=int(closed_at) if closed_at is not None else None,
    )
    if wrote:
      stats["reconciled"] += 1
      log.info("reconciled orphan group_id=%s from plan_runtime", group_id)
    else:
      stats["skipped"] += 1
  return stats


async def backfill(
  *,
  count: int | None,
  stream: str,
  runtime_reconcile: bool,
) -> dict[str, int]:
  client = redis_state.get_client()
  await store.init_db()
  entries = await _read_stream_entries(client, stream, count=count)
  before_fills = await _count("auto_trade_fills")
  before_results = await _count("auto_trade_results")
  stats: dict[str, int] = {
    "seen": 0,
    "fill_events": 0,
    "result_events": 0,
    "errors": 0,
    "other_events": 0,
  }

  parsed: list[dict] = []
  for _entry_id, fields in entries:
    stats["seen"] += 1
    event = _parse_event(fields if isinstance(fields, dict) else {})
    if event is None:
      stats["errors"] += 1
      log.warning("skip invalid event id=%s", _entry_id)
      continue
    parsed.append(event)

  # Fills first, then closes — close-before-fill used to drop results forever.
  for event in parsed:
    event_type = str(event.get("type") or "")
    if event_type in _FILL_TYPES:
      await store.record_auto_trade_event(event)
      stats["fill_events"] += 1
    elif event_type in _RESULT_TYPES:
      continue
    else:
      stats["other_events"] += 1

  for event in parsed:
    event_type = str(event.get("type") or "")
    if event_type in _RESULT_TYPES:
      await store.record_auto_trade_event(event)
      stats["result_events"] += 1

  stats["fills_delta"] = await _count("auto_trade_fills") - before_fills
  stats["results_delta"] = await _count("auto_trade_results") - before_results

  if runtime_reconcile:
    before_reconcile = await _count("auto_trade_results")
    runtime_stats = await reconcile_from_plan_runtimes(client)
    stats.update({f"runtime_{key}": value for key, value in runtime_stats.items()})
    stats["results_delta"] = await _count("auto_trade_results") - before_results
    stats["runtime_results_delta"] = (
      await _count("auto_trade_results") - before_reconcile
    )
  return stats


async def _count(table: str) -> int:
  async with store._connect() as db:
    return int(await db.fetchval(f"SELECT COUNT(*) FROM {table}") or 0)


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--count",
    type=int,
    default=None,
    help=(
      "Replay only the newest N retained stream entries (oldest→newest). "
      "Default: full retained window."
    ),
  )
  parser.add_argument("--stream", default="auto_trade:events")
  parser.add_argument(
    "--no-runtime-reconcile",
    action="store_true",
    help="Skip orphan fill recovery from execution:plan_runtime:*",
  )
  args = parser.parse_args()
  logging.basicConfig(level="INFO", format="%(levelname)s: %(message)s")
  stats = asyncio.run(
    backfill(
      count=args.count,
      stream=args.stream,
      runtime_reconcile=not args.no_runtime_reconcile,
    )
  )
  log.info("backfill done %s", stats)


if __name__ == "__main__":
  main()
