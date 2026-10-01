"""The real-bar capture tool: read-only, and it never leaves a file the Go replay would reject."""

from __future__ import annotations

import json

import pytest

from app.scripts import capture_bars

pytestmark = pytest.mark.no_database


async def _seed_bars(client, rows, tf="M5"):
  for t, o, h, l, c, v in rows:
    await client.zadd(f"bars:XAU:{tf}", {json.dumps({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}): t})


@pytest.mark.asyncio
async def test_capture_tool_reads_redis_writes_nothing_and_produces_a_valid_capture():
  from datetime import datetime, timezone

  from app.persistence import redis_state

  client = redis_state.get_client()
  rows = [[1_789_900_200 + 300 * i, 4300 + i, 4301 + i, 4299 + i, 4300.5 + i, 100 + i] for i in range(12)]
  await _seed_bars(client, rows)
  before = {k: await client.zrange(k, 0, -1, withscores=True) async for k in client.scan_iter("*")}

  document = await capture_bars.capture("XAU", {"M5": 10, "M15": 0}, client, now=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc))

  assert {k: await client.zrange(k, 0, -1, withscores=True) async for k in client.scan_iter("*")} == before   # read-only
  assert document["provenance"]["captured_at_utc"] == "2026-09-26T12:00:00Z" and document["provenance"]["derived"] == "none"
  assert list(document["timeframes"]) == ["M5"] and len(document["timeframes"]["M5"]) == 10
  assert document["timeframes"]["M5"] == rows[-10:]                          # exactly the newest ten, untouched
  assert capture_bars.validate_capture(document) == {"M5": 10}


def test_capture_tool_refuses_to_leave_a_file_the_replay_would_reject(tmp_path, monkeypatch, capsys):
  async def fake_capture(symbol, counts, client=None, *, now=None):
    return {"version": 1, "symbol": symbol, "provenance": {}, "columns": ["t", "open", "high", "low", "close", "volume"],
            "timeframes": {"M5": [[301, 1, 2, 1, 2, 1]]}}          # not aligned to the timeframe

  monkeypatch.setattr(capture_bars, "capture", fake_capture)
  out = tmp_path / "bad.json"
  assert capture_bars.main(["--out", str(out)]) == 2
  assert not out.exists() and "aligned" in capsys.readouterr().err
