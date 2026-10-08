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

from app.core.config import runtime_config
from tests.support.canonical_fixtures import install_runtime_overrides, leaf
from app.core import symbols
from app.signals import weekly_report
from app.signals.reports import build_stats, format_stats


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
  install_runtime_overrides(monkeypatch, legacy_overrides={"weekly_report_dow": 6})
  install_runtime_overrides(monkeypatch, legacy_overrides={"weekly_report_hour": 8})
  install_runtime_overrides(monkeypatch, legacy_overrides={"weekly_report_skip_empty": skip_empty,})


@pytest.mark.asyncio
async def test_sessions_are_classified_in_utc_not_seq_reset_tz(monkeypatch):
  # 08:00 UTC = 15:00 Asia/Ho_Chi_Minh. True session is London
  # (07:00-13:00 UTC); the seq_reset_tz bug this regresses would have
  # classified it as NY (13:00-22:00) using the ICT-shifted hour instead.
  utc_ts = int(datetime(2026, 7, 1, 8, tzinfo=ZoneInfo("UTC")).timestamp())
  records = [{
    "id": 1,
    "ts": utc_ts,
    "sign": "+",
    "pips": 50,
    "signal_id": 1,
    "signal_ts": utc_ts,
    "setup_type": "ob-retest",
    "daily_seq": 1,
    "symbol": "XAU",
  }]
  _configure(monkeypatch)
  monkeypatch.setattr(weekly_report, "get_meta", AsyncMock(return_value=None))
  monkeypatch.setattr(weekly_report, "set_meta", AsyncMock())
  monkeypatch.setattr(
    weekly_report, "get_pips_records", AsyncMock(return_value=records),
  )
  monkeypatch.setattr(
    weekly_report, "get_all_signals", AsyncMock(return_value=_signals(count=1)),
  )
  monkeypatch.setattr(
    weekly_report,
    "channels_for",
    lambda symbol, visibility: [{
      "symbol": symbol, "tier": "vip", "channel_id": -1001,
    }],
  )
  send = AsyncMock()
  monkeypatch.setattr(weekly_report, "_send_recap", send)

  assert await weekly_report._weekly_report_tick(SUNDAY)

  text = send.await_args.args[0]
  assert "🌍 London" in text
  assert "🌎 NY" not in text


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

  assert "📊 STATS — XAU/USD WEEK" in interactive
  assert "💰 Net" in interactive
  assert "+40p" in interactive
  assert "🤖 Apex Void · stats" in interactive
  assert "📊 WEEKLY RECAP — XAU/USD" in recap
  assert "CHART / SIGNAL" in recap
  assert "💰 Net" in recap
  assert "+40p" in recap
  assert "🤖 Apex Void · weekly recap" in recap
  assert "2W" not in recap
  assert "1W / 1L" in recap
  assert not re.search(r"\d+\s*pips?", recap, re.IGNORECASE)
  spark = re.search(r"📈 Equity \(combined unique\)\n([▁▂▃▄▅▆▇]+)\n", recap)
  assert spark is not None
  assert len(spark.group(1)) <= 22


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


