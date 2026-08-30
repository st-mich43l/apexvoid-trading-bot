"""Backfill pre-PR-K manual_algo_charts to live lookback windows when Redis still holds them.

Default is ``--dry-run`` (report only). ``--apply`` upserts recoverable rows
to ``capture_version = 2``. Shortfalls leave the existing row untouched.

Usage::

  python -m app.scripts.backfill_manual_algo_charts --dry-run
  python -m app.scripts.backfill_manual_algo_charts --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import asyncpg

from app.signals.manual_algo_chart import CAPTURE_VERSION
from app.signals.manual_algo_chart import TIMEFRAMES
from app.signals.manual_algo_chart import _BAR_SECONDS
from app.signals.manual_algo_chart import _window
from app.signals.manual_algo_chart import fetch_ohlc_window
from app.persistence import store


async def _load_v1_targets(pool: asyncpg.Pool) -> list[dict[str, Any]]:
  rows = await pool.fetch(
    """
    SELECT DISTINCT c.signal_id, c.event, c.captured_at, c.symbol
    FROM manual_algo_charts c
    WHERE COALESCE(c.capture_version, 1) < $1
    ORDER BY c.signal_id, c.event
    """,
    CAPTURE_VERSION,
  )
  return [dict(row) for row in rows]


def _covers_requested(
  bars: list[dict[str, Any]],
  *,
  start: int,
  captured_at: int,
  bars_requested: int,
  step: int,
) -> bool:
  """True when Redis returned a span that reaches the full lookback to event."""
  if len(bars) < int(0.9 * bars_requested):
    return False
  # Earliest bar should reach near the requested start (one bar of slack).
  earliest = min(int(bar["t"]) for bar in bars)
  return earliest <= start + step


async def run(*, apply: bool) -> dict[str, Any]:
  dsn = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DSN")
  if not dsn:
    raise SystemExit("DATABASE_URL required")
  dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
  await store.init_db()
  pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
  assert pool is not None
  summary: dict[str, Any] = {
    "apply": apply,
    "targets": 0,
    "by_tf": {
      tf: {"recoverable": 0, "unrecoverable": 0, "written": 0}
      for tf in TIMEFRAMES
    },
    "signals_fully_recovered": 0,
    "signals_partial": 0,
    "signals_none": 0,
  }
  try:
    targets = await _load_v1_targets(pool)
    summary["targets"] = len(targets)

    for target in targets:
      signal_id = int(target["signal_id"])
      event = str(target["event"])
      captured_at = int(target["captured_at"])
      symbol = str(target["symbol"] or "XAU")
      recovered = 0
      attempted = 0
      for tf in TIMEFRAMES:
        attempted += 1
        start, end, bars_requested = _window(
          tf, event, captured_at, symbol=symbol,
        )
        step = _BAR_SECONDS[tf]
        try:
          bars = await fetch_ohlc_window(symbol, tf, start, end)
        except Exception as exc:
          summary["by_tf"][tf]["unrecoverable"] += 1
          print(
            f"fetch_failed signal={signal_id} event={event} tf={tf}: {exc}",
            file=sys.stderr,
          )
          continue
        if not _covers_requested(
          bars,
          start=start,
          captured_at=captured_at,
          bars_requested=bars_requested,
          step=step,
        ):
          summary["by_tf"][tf]["unrecoverable"] += 1
          continue
        summary["by_tf"][tf]["recoverable"] += 1
        recovered += 1
        if not apply:
          continue
        bars_after = sum(1 for bar in bars if int(bar["t"]) > captured_at)
        await store.upsert_manual_algo_chart(
          signal_id=signal_id,
          event=event,
          captured_at=captured_at,
          symbol=symbol.upper(),
          timeframe=tf,
          window_start=start,
          window_end=end,
          bars=bars,
          bars_requested=int(bars_requested),
          bars_stored=len(bars),
          bars_after_event=bars_after,
          capture_version=CAPTURE_VERSION,
        )
        summary["by_tf"][tf]["written"] += 1
      if recovered == attempted:
        summary["signals_fully_recovered"] += 1
      elif recovered == 0:
        summary["signals_none"] += 1
      else:
        summary["signals_partial"] += 1
  finally:
    await pool.close()
  return summary


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument(
    "--apply",
    action="store_true",
    help="Write recoverable version-2 upserts (default is dry-run)",
  )
  args = parser.parse_args()
  apply = bool(args.apply)
  summary = asyncio.run(run(apply=apply))
  print(
    f"backfill mode={'apply' if apply else 'dry-run'} "
    f"targets={summary['targets']} "
    f"full={summary['signals_fully_recovered']} "
    f"partial={summary['signals_partial']} "
    f"none={summary['signals_none']}",
  )
  print(f"{'TF':4} {'recoverable':>12} {'unrecoverable':>14} {'written':>8}")
  for tf in TIMEFRAMES:
    cell = summary["by_tf"][tf]
    print(
      f"{tf:4} {cell['recoverable']:12d} {cell['unrecoverable']:14d} "
      f"{cell['written']:8d}",
    )


if __name__ == "__main__":
  main()
