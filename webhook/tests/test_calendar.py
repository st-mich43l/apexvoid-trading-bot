import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")

from app import calendar, dedup, telegram


@pytest.fixture
def feed():
  path = Path(__file__).parent / "fixtures" / "ff_calendar.json"
  return json.loads(path.read_text(encoding="utf-8"))


def test_parse_timezone_all_day_and_filter(feed, monkeypatch):
  monkeypatch.setattr(calendar.settings, "calendar_currencies", "USD")
  monkeypatch.setattr(
    calendar.settings,
    "oil_keywords",
    "crude oil inventories,opec,cushing,api weekly crude",
  )

  parsed = calendar._parse_feed(feed, synced_at=123)
  nfp = next(row for row in parsed if row["title"] == "Non-Farm Payrolls")
  holiday = next(row for row in parsed if row["title"] == "Bank Holiday")
  filtered = calendar._filter_events(parsed)

  assert nfp["ts_utc"] == int(
    datetime.fromisoformat("2026-07-03T12:30:00+00:00").timestamp()
  )
  assert nfp["all_day"] == 0
  assert holiday["all_day"] == 1
  assert {row["title"] for row in filtered} == {
    "Non-Farm Payrolls",
    "Crude Oil Inventories",
  }


@pytest.mark.asyncio
async def test_event_id_upsert_updates_actual_without_duplicate(
  tmp_path,
  monkeypatch,
  feed,
):
  monkeypatch.setattr(dedup.settings, "db_path", str(tmp_path / "events.db"))
  await dedup.init_db()
  first = calendar._filter_events(calendar._parse_feed(feed, synced_at=1))
  await dedup.upsert_events(first)

  updated_feed = json.loads(json.dumps(feed))
  updated_feed[0]["actual"] = "210K"
  second = calendar._filter_events(
    calendar._parse_feed(updated_feed, synced_at=2)
  )
  assert first[0]["event_id"] == second[0]["event_id"]
  await dedup.upsert_events(second)

  db = sqlite3.connect(dedup.settings.db_path)
  count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
  actual = db.execute(
    "SELECT actual FROM events WHERE title = 'Non-Farm Payrolls'"
  ).fetchone()[0]
  db.close()
  assert count == 2
  assert actual == "210K"


@pytest.mark.asyncio
async def test_event_window_uses_local_database_only(tmp_path, monkeypatch):
  monkeypatch.setattr(dedup.settings, "db_path", str(tmp_path / "guard.db"))
  await dedup.init_db()
  now = 1_800_000_000
  base = {
    "currency": "USD",
    "impact": "High",
    "forecast": None,
    "previous": None,
    "actual": None,
    "all_day": 0,
    "source": "ff",
    "synced_at": now,
  }
  await dedup.upsert_events([
    {
      **base,
      "event_id": "soon",
      "ts_utc": now + 7200,
      "title": "CPI m/m",
    },
    {
      **base,
      "event_id": "later",
      "ts_utc": now + 20_000,
      "title": "Outside horizon",
    },
  ])

  assert (await dedup.event_in_window(now, 4 * 3600))["title"] == "CPI m/m"
  assert await dedup.event_in_window(now + 10_000, 60) is None


class _DeniedResponse:
  status = 200
  headers = {"Content-Type": "text/html"}

  async def __aenter__(self):
    return self

  async def __aexit__(self, *_):
    return None

  async def text(self):
    return "<html>Request Denied</html>"


class _DeniedSession:
  def __init__(self, **_):
    pass

  async def __aenter__(self):
    return self

  async def __aexit__(self, *_):
    return None

  def get(self, _url):
    return _DeniedResponse()


@pytest.mark.asyncio
async def test_request_denied_keeps_last_good_cache(tmp_path, monkeypatch):
  cache = tmp_path / "ff.json"
  cache.write_text('[{"title": "last good"}]', encoding="utf-8")
  monkeypatch.setattr(
    calendar.aiohttp,
    "ClientSession",
    _DeniedSession,
  )

  result = await calendar._fetch_feed("https://example.test/feed", cache)

  assert result is None
  assert json.loads(cache.read_text(encoding="utf-8")) == [
    {"title": "last good"}
  ]


def test_digest_renders_local_times_and_skips_empty_days():
  tz = ZoneInfo("Asia/Ho_Chi_Minh")
  rows = [
    {
      "ts_utc": int(
        datetime.fromisoformat("2026-07-03T13:30:00+00:00").timestamp()
      ),
      "currency": "USD",
      "title": "Non-Farm Payrolls",
      "forecast": "180K",
      "previous": "206K",
      "all_day": 0,
    },
    {
      "ts_utc": int(
        datetime.fromisoformat("2026-07-03T14:30:00+00:00").timestamp()
      ),
      "currency": "CAD",
      "title": "Crude Oil Inventories",
      "forecast": None,
      "previous": None,
      "all_day": 0,
    },
  ]
  text = calendar._format_brief(rows, tz)

  assert "20:30  USD · Non-Farm Payrolls" in text
  assert "21:30  Oil · Crude Oil Inventories" in text
  assert calendar._format_brief([], tz) is None


@pytest.mark.asyncio
async def test_empty_day_records_guard_without_broadcast(monkeypatch):
  monkeypatch.setattr(
    calendar,
    "get_meta",
    AsyncMock(return_value=None),
  )
  monkeypatch.setattr(
    calendar,
    "events_between",
    AsyncMock(return_value=[]),
  )
  set_meta = AsyncMock()
  send = AsyncMock()
  channels = AsyncMock()
  monkeypatch.setattr(calendar, "set_meta", set_meta)
  monkeypatch.setattr(calendar, "_send_with_retry", send)
  monkeypatch.setattr(calendar, "channels_for", channels)
  now = datetime(2026, 7, 5, 8, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))

  await calendar._post_brief(now)

  send.assert_not_awaited()
  channels.assert_not_awaited()
  set_meta.assert_awaited_once_with("last_brief_date", "2026-07-05")


def _private_message():
  return SimpleNamespace(
    text="gold sell 4100-4105 / sl 4110 / tp 95/90/80",
    from_user=SimpleNamespace(id=42),
    answer=AsyncMock(),
  )


@pytest.mark.asyncio
async def test_guard_tags_entry_and_no_event_does_not(monkeypatch):
  now = 1_800_000_000
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(telegram.settings, "news_guard_block", False)
  monkeypatch.setattr(telegram.time, "time", lambda: now)
  guard = AsyncMock(return_value={
    "title": "CPI m/m",
    "ts_utc": now + 7200,
  })
  store = AsyncMock(return_value={"id": 1, "daily_seq": 1})
  signal = {
    "id": 1,
    "daily_seq": 1,
    "symbol": "XAU",
    "action": "SELL",
    "entry": 4100.0,
    "entry_end": 4105.0,
    "sl": 4110.0,
    "tps": [4095.0],
    "visibility": "both",
  }
  broadcast = AsyncMock()
  monkeypatch.setattr(telegram, "event_in_window", guard)
  monkeypatch.setattr(telegram, "store_manual_signal", store)
  monkeypatch.setattr(
    telegram,
    "get_manual_signal",
    AsyncMock(return_value=signal),
  )
  monkeypatch.setattr(telegram, "broadcast_entry", broadcast)

  await telegram.handle_private_signal(_private_message())
  assert "⚠️ CPI m/m in 2h 0m — expect volatility" in (
    broadcast.await_args.args[0]["guard_text"]
  )

  guard.return_value = None
  broadcast.reset_mock()
  await telegram.handle_private_signal(_private_message())
  assert broadcast.await_args.args[0]["guard_text"] is None


@pytest.mark.asyncio
async def test_guard_block_refuses_post(monkeypatch):
  now = 1_800_000_000
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(telegram.settings, "news_guard_block", True)
  monkeypatch.setattr(telegram.time, "time", lambda: now)
  monkeypatch.setattr(telegram, "event_in_window", AsyncMock(return_value={
    "title": "CPI m/m",
    "ts_utc": now + 7200,
  }))
  store = AsyncMock()
  broadcast = AsyncMock()
  monkeypatch.setattr(telegram, "store_manual_signal", store)
  monkeypatch.setattr(telegram, "broadcast_entry", broadcast)
  msg = _private_message()

  await telegram.handle_private_signal(msg)

  store.assert_not_awaited()
  broadcast.assert_not_awaited()
  assert "Signal not posted" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_same_day_restart_skips_fetch_and_digest(
  tmp_path,
  monkeypatch,
  feed,
):
  monkeypatch.setattr(dedup.settings, "db_path", str(tmp_path / "restart.db"))
  monkeypatch.setattr(
    calendar,
    "_CACHE_THISWEEK",
    tmp_path / "thisweek.json",
  )
  monkeypatch.setattr(
    calendar,
    "_CACHE_NEXTWEEK",
    tmp_path / "nextweek.json",
  )
  await dedup.init_db()
  fetch = AsyncMock(return_value=feed)
  send = AsyncMock()
  monkeypatch.setattr(calendar, "_fetch_feed", fetch)
  monkeypatch.setattr(calendar, "_send_with_retry", send)
  monkeypatch.setattr(
    calendar,
    "channels_for",
    lambda symbol, visibility: [
      {"symbol": symbol, "tier": "vip", "channel_id": -1001},
      {"symbol": symbol, "tier": "public", "channel_id": -1002},
    ],
  )
  now = datetime(2026, 7, 3, 8, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))

  await calendar._sync_day(now)
  await calendar._sync_day(now)

  assert fetch.await_count == 2
  assert [call.kwargs["chat_id"] for call in send.await_args_list] == [
    -1001,
    -1002,
  ]
  assert await dedup.get_meta("last_sync_date") == "2026-07-03"
  assert await dedup.get_meta("last_brief_date") == "2026-07-03"
