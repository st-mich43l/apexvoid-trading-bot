"""Capture real closed bars from Redis into the immutable S14C replay format.

Read-only: it issues ZREVRANGE reads through ``RedisOHLCSource`` and writes one
JSON file. Nothing is modified in Redis. Run it on the host that owns the feed:

  docker compose exec -T bot python -m app.scripts.capture_bars \\
      --symbol XAU --m5 1500 --m15 600 --h1 300 --out /tmp/xau-capture.json

The file records when and from where it was taken; it never contains a
credential (only the Redis host is stored, without user info). Feed it to both
engines unchanged:

  go run ./cmd/replay -capture /tmp/xau-capture.json -config ../config/apexvoid.yml -envelopes-out /tmp/go.jsonl
  python -m app.scripts.policy_replay python-observations --capture /tmp/xau-capture.json --out /tmp/py.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.analysis.ohlc_source import RedisOHLCSource
from app.autotrade import policy_replay as pr
from app.persistence import redis_state


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
  out.write_text(json.dumps(document, separators=(",", ":")) + "\n")
  try:
    loaded = pr.load_capture(out)             # refuse to leave a file the replay would reject
  except pr.ReplayError as exc:
    out.unlink(missing_ok=True)
    print(json.dumps({"refused": str(exc)}), file=sys.stderr)
    return 2
  print(json.dumps({"out": str(out), "bars": {tf: len(df) for tf, df in loaded.frames.items()}, "sha256": loaded.sha256}))
  return 0


if __name__ == "__main__":
  sys.exit(main())
