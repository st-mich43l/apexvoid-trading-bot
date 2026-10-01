"""Capture real closed bars from Redis into the immutable S14C replay format.

Read-only: it issues ZREVRANGE reads through ``RedisOHLCSource`` and writes one
JSON file. Nothing is modified in Redis. Run it on the host that owns the feed:

  docker compose exec -T bot python -m app.scripts.capture_bars \\
      --symbol XAU --m5 1500 --m15 600 --h1 300 --out /tmp/xau-capture.json

The file records when and from where it was taken; it never contains a
credential (only the Redis host is stored, without user info). Feed it to the Go
replay tool unchanged:

  go run ./cmd/replay -capture /tmp/xau-capture.json -config ../config/apexvoid.yml -envelopes-out /tmp/go.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.analysis.ohlc_source import RedisOHLCSource
from app.persistence import redis_state


TF_MINUTES = {"M5": 5, "M15": 15, "H1": 60, "H4": 240}


class CaptureError(ValueError):
  """The capture is malformed; never repaired."""


def validate_capture(document: dict) -> dict[str, int]:
  """Bars per timeframe, or CaptureError if the Go replay would reject the file."""
  if document.get("version") != 1 or not document.get("symbol") or not document.get("timeframes"):
    raise CaptureError("capture needs version 1, a symbol and timeframes")
  if document.get("columns") != ["t", "open", "high", "low", "close", "volume"]:
    raise CaptureError("capture columns must be t,open,high,low,close,volume")
  counts: dict[str, int] = {}
  for tf, rows in document["timeframes"].items():
    if tf not in TF_MINUTES:
      raise CaptureError(f"unknown timeframe {tf!r}")
    previous = None
    for i, row in enumerate(rows):
      if len(row) != 6:
        raise CaptureError(f"{tf} row {i} has {len(row)} columns")
      t, o, h, l, c, _ = row
      if h < l or h < o or h < c or l > o or l > c or l <= 0:
        raise CaptureError(f"{tf} bar {i} at t={int(t)} is not OHLC-consistent")
      if int(t) % (TF_MINUTES[tf] * 60) != 0:
        raise CaptureError(f"{tf} bar {i} at t={int(t)} is not aligned to its timeframe")
      if previous is not None and int(t) <= previous:
        raise CaptureError(f"{tf} bars must be strictly increasing (t={int(t)} after t={previous})")
      previous = int(t)
    counts[tf] = len(rows)
  if "M5" not in counts:
    raise CaptureError("capture has no M5 bars")
  return counts


def _host() -> str:
  try:
    from app.core.config import runtime_config
    parsed = urlparse(str(runtime_config.bootstrap.redis.url))
    return f"{parsed.hostname or 'unknown'}:{parsed.port or ''}".rstrip(":")
  except Exception:  # noqa: BLE001 - provenance only
    return "unknown"


async def capture(symbol: str, counts: dict[str, int], client=None, *, now: datetime | None = None) -> dict:
  source = RedisOHLCSource(client or redis_state.get_client())
  timeframes: dict[str, list[list[float]]] = {}
  for tf, count in counts.items():
    if count <= 0:
      continue
    frame = await source.window(symbol, tf, count)
    timeframes[tf] = [
      [int(ts.timestamp()), float(row.open), float(row.high), float(row.low), float(row.close), float(row.volume)]
      for ts, row in frame.iterrows()
    ]
  stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
  return {
    "version": 1,
    "description": "Real closed-bar capture for the S14C Go-vs-Python policy replay. Nothing here is synthetic or edited.",
    "symbol": symbol.upper(),
    "provenance": {
      "source": "Redis bars:{SYMBOL}:{TF} read through app.analysis.ohlc_source.RedisOHLCSource (closed bars only)",
      "captured_at_utc": stamp,
      "redis_host": _host(),
      "bars_requested": {tf: n for tf, n in counts.items() if n > 0},
      "git_sha": os.getenv("GIT_SHA", "unknown"),
      "derived": "none",
    },
    "columns": ["t", "open", "high", "low", "close", "volume"],
    "timeframes": timeframes,
  }


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--symbol", default="XAU")
  parser.add_argument("--m5", type=int, default=1500)
  parser.add_argument("--m15", type=int, default=600)
  parser.add_argument("--h1", type=int, default=300)
  parser.add_argument("--out", required=True)
  args = parser.parse_args(argv)
  document = asyncio.run(capture(args.symbol, {"M5": args.m5, "M15": args.m15, "H1": args.h1}))
  out = Path(args.out)
  try:
    bars = validate_capture(document)         # refuse to leave a file the replay would reject
  except CaptureError as exc:
    print(json.dumps({"refused": str(exc)}), file=sys.stderr)
    return 2
  payload = (json.dumps(document, separators=(",", ":")) + "\n").encode()
  out.write_bytes(payload)
  print(json.dumps({"out": str(out), "bars": bars, "sha256": hashlib.sha256(payload).hexdigest()}))
  return 0


if __name__ == "__main__":
  sys.exit(main())
