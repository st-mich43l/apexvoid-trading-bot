import os
import re
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")

from app import symbols, weekly_report
from app.reports import build_stats, format_stats


TZ = ZoneInfo("Asia/Ho_Chi_Minh")
SUNDAY = datetime(2026, 7, 5, 8, 0, tzinfo=TZ)


def _records(symbol="XAU", values=(70, -30)):
  rows = []
  for index, value in enumerate(values, 1):
    rows.append({
      "id": index,
      "ts": int(datetime(2026, 7, index, 12, tzinfo=TZ).timestamp()),
      "sign": "+" if value >= 0 else "-",
      "pips": abs(value),
      "signal_id": index,
      "signal_ts": int(
        datetime(2026, 7, index, 12, tzinfo=TZ).timestamp()
      ),
      "setup_type": "ob-retest" if index == 1 else "breakout",
      "daily_seq": index,
      "symbol": symbol,
    })
  return rows


def _signals(symbol="XAU", count=2):
  return [
    {
      "id": index,
      "parent_id": None,
      "entry": 3300.0,
      "entry_end": 3302.0,
      "action": "BUY",
      "symbol": symbol,
    }
    for index in range(1, count + 1)
  ]


def _stats(values=(70, -30)):
  return build_stats(
    _records(values=values),
    _signals(count=len(values)),
    "Asia/Ho_Chi_Minh",
    22,
    7,
    13,
  )


def _configure(monkeypatch, skip_empty=False):
  monkeypatch.setattr(weekly_report.settings, "weekly_report_dow", 6)
  monkeypatch.setattr(weekly_report.settings, "weekly_report_hour", 8)
  monkeypatch.setattr(
    weekly_report.settings,
    "weekly_report_skip_empty",
    skip_empty,
  )
  monkeypatch.setattr(weekly_report, "SYMBOLS", {"XAU": {}})


@pytest.mark.asyncio
async def test_sunday_tick_posts_once_and_survives_restart(
  monkeypatch,
):
  _configure(monkeypatch)
  meta = {}

  async def get_meta(key):
    return meta.get(key)

  async def set_meta(key, value):
    meta[key] = value

  monkeypatch.setattr(weekly_report, "get_meta", get_meta)
  monkeypatch.setattr(weekly_report, "set_meta", set_meta)
  monkeypatch.setattr(
    weekly_report,
    "get_pips_records",
    AsyncMock(return_value=_records()),
  )
  monkeypatch.setattr(
    weekly_report,
    "get_all_signals",
    AsyncMock(return_value=_signals()),
  )
  monkeypatch.setattr(
    weekly_report,
    "channels_for",
    lambda symbol, visibility: [{
      "symbol": symbol,
      "tier": "vip",
      "channel_id": -1001,
    }],
  )
  send = AsyncMock()
  monkeypatch.setattr(weekly_report, "_send_recap", send)

  assert await weekly_report._weekly_report_tick(SUNDAY)
  assert not await weekly_report._weekly_report_tick(SUNDAY)

  send.assert_awaited_once()
  assert meta["last_weekly_report_date"] == "2026-06-29"


@pytest.mark.asyncio
@pytest.mark.parametrize("now", [
  datetime(2026, 7, 4, 9, tzinfo=TZ),
  datetime(2026, 7, 5, 7, 59, tzinfo=TZ),
])
async def test_tick_ignores_wrong_day_or_early_hour(monkeypatch, now):
  _configure(monkeypatch)
  meta = AsyncMock()
  send = AsyncMock()
  monkeypatch.setattr(weekly_report, "get_meta", meta)
  monkeypatch.setattr(weekly_report, "_send_recap", send)

  assert not await weekly_report._weekly_report_tick(now)
  meta.assert_not_awaited()
  send.assert_not_awaited()


def test_weekly_uses_shared_stats_and_safe_format():
  stats = _stats((70, -30))
  start, end = weekly_report._closed_week_window(SUNDAY)
  interactive = format_stats(stats, "XAU week")
  recap = weekly_report.format_weekly_recap(
    stats,
    "XAU",
    start,
    end,
  )

  assert "Net: <b>+40 pips</b>" in interactive
  assert "📊 WEEKLY RECAP — XAU/USD" in recap
  assert "💰 Net" in recap
  assert "+40p" in recap
  assert "🤖 Apex Void · weekly recap" in recap
  assert "2W" not in recap
  assert "1W / 1L" in recap
  assert not re.search(r"\d+\s*pips?", recap, re.IGNORECASE)


def test_losing_and_empty_week_rendering():
  start, end = weekly_report._closed_week_window(SUNDAY)
  losing = weekly_report.format_weekly_recap(
    _stats((-42,)),
    "XAU",
    start,
    end,
  )
  empty = weekly_report.format_weekly_recap(
    _stats(()),
    "XAU",
    start,
    end,
  )

  assert "−42p" in losing
  assert "🔴" in losing
  assert "capital preserved" in empty


def test_recap_delivery_resolution_is_vip_only(monkeypatch):
  monkeypatch.setattr(symbols, "CHANNELS", [
    {"symbol": "XAU", "tier": "vip", "channel_id": -1001},
    {"symbol": "XAU", "tier": "public", "channel_id": -1002},
  ])

  targets = symbols.channels_for("XAU", "vip")

  assert [target["channel_id"] for target in targets] == [-1001]
  assert all(target["tier"] == "vip" for target in targets)


@pytest.mark.asyncio
async def test_empty_skip_and_multi_symbol_delivery(monkeypatch):
  _configure(monkeypatch, skip_empty=True)
  monkeypatch.setattr(
    weekly_report,
    "get_meta",
    AsyncMock(return_value=None),
  )
  set_meta = AsyncMock()
  monkeypatch.setattr(weekly_report, "set_meta", set_meta)
  monkeypatch.setattr(
    weekly_report,
    "get_pips_records",
    AsyncMock(return_value=[]),
  )
  monkeypatch.setattr(
    weekly_report,
    "get_all_signals",
    AsyncMock(return_value=[]),
  )
  send = AsyncMock()
  monkeypatch.setattr(weekly_report, "_send_recap", send)

  assert await weekly_report._weekly_report_tick(SUNDAY)
  send.assert_not_awaited()
  set_meta.assert_awaited_once()

  monkeypatch.setattr(
    weekly_report.settings,
    "weekly_report_skip_empty",
    False,
  )
  monkeypatch.setattr(
    weekly_report,
    "SYMBOLS",
    {"XAU": {}, "US30": {}},
  )
  monkeypatch.setattr(
    weekly_report,
    "get_meta",
    AsyncMock(return_value=None),
  )

  async def records(start, end, symbol):
    return _records(symbol, (70,))

  monkeypatch.setattr(weekly_report, "get_pips_records", records)
  monkeypatch.setattr(
    weekly_report,
    "get_all_signals",
    AsyncMock(return_value=_signals(count=1)),
  )
  monkeypatch.setattr(
    weekly_report,
    "channels_for",
    lambda symbol, visibility: [{
      "symbol": symbol,
      "tier": "vip",
      "channel_id": -1001 if symbol == "XAU" else -1003,
    }],
  )
  send.reset_mock()

  assert await weekly_report._weekly_report_tick(SUNDAY)
  assert [call.args[1] for call in send.await_args_list] == [-1001, -1003]
