"""Fixture helpers for tests that need a closed manual signal row."""

from __future__ import annotations

import time

from app.persistence import store


async def close_manual_signal(row_id: int, result_pips: int) -> None:
  """Mark an open manual signal closed with a pip result."""
  async with store._connect() as db:
    await db.execute(
      "UPDATE manual_signals SET status = 'closed', result_pips = $1, "
      "closed_at = $2 WHERE id = $3 AND status = 'open'",
      result_pips, int(time.time()), row_id,
    )
