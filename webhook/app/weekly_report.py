"""Restart-safe VIP-only weekly performance recap."""

import asyncio
import logging
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from app.config import settings
from app.dedup import (
  get_all_signals,
  get_meta,
  get_pips_records,
  set_meta,
)
from app.reports import build_stats, sparkline
from app.symbols import SYMBOLS, channels_for
from app.tg_core import send_with_retry

log = logging.getLogger(__name__)

_META_KEY = "last_weekly_report_date"
_WEEKLY_INTERVAL = 1800


def _closed_week_window(now: datetime) -> tuple[datetime, datetime]:
  """Return Monday 00:00 through Saturday 00:00 for the last closed week."""
  monday = now.replace(
    hour=0,
    minute=0,
    second=0,
    microsecond=0,
  ) - timedelta(days=now.weekday())
  end = monday + timedelta(days=5)
  if now < end:
    monday -= timedelta(days=7)
    end -= timedelta(days=7)
  return monday, end


def _signed(value: float | int) -> str:
  rounded = round(value)
  if rounded > 0:
    return f"+{rounded}p"
  if rounded < 0:
    return f"−{abs(rounded)}p"
  return "0p"


def _setup_label(value: str | None) -> str:
  words = (value or "untagged").replace("_", " ").replace("-", " ").split()
  return " ".join(
    word.upper() if word.lower() in {"ob", "fvg", "ny"} else word.title()
    for word in words
  )


def _symbol_label(symbol: str) -> str:
  return "XAU/USD" if symbol == "XAU" else symbol


def _date_range(start: datetime, end: datetime) -> str:
  finish = end - timedelta(days=1)
  start_text = start.strftime("%d %b").lstrip("0")
  end_text = finish.strftime("%d %b %Y").lstrip("0")
  return f"{start_text} – {end_text}"


def _branch_lines(groups: list[dict], kind: str) -> list[str]:
  if not groups:
    return ["└ —"]
  lines = []
  icons = {
    "Asia": "🌏",
    "London": "🌍",
    "NY": "🌎",
    "Legacy": "🕐",
  }
  for index, group in enumerate(groups):
    branch = "└" if index == len(groups) - 1 else "├"
    if kind == "setup":
      label = _setup_label(group["label"])
      lines.append(
        f"{branch} {label:<15} {_signed(group['net']):>6} · "
        f"{group['wins']}W/{group['losses']}L"
      )
    else:
      icon = icons.get(group["label"], "🕐")
      lines.append(
        f"{branch} {icon} {group['label']:<7} "
        f"{_signed(group['net']):>6}"
      )
  return lines


def format_weekly_recap(
  stats: dict,
  symbol: str,
  start: datetime,
  end: datetime,
) -> str:
  """Render one HTML monospace digest without pips-trigger phrasing."""
  if not stats["trades"]:
    text = (
      f"📊 Weekly recap — {_symbol_label(symbol)}\n"
      "no trades this week · capital preserved"
    )
    return f"<pre>{escape(text)}</pre>"

  best = stats["best"]
  worst = stats["worst"]
  best_seq = best.get("daily_seq") or best.get("signal_id") or "?"
  worst_seq = worst.get("daily_seq") or worst.get("signal_id") or "?"
  net_icon = "🟢" if stats["net"] >= 0 else "🔴"
  lines = [
    f"📊 WEEKLY RECAP — {_symbol_label(symbol)}",
    f"🗓 {_date_range(start, end)}",
    "━━━━━━━━━━━━━━━━━━━━",
    f"💰 Net        {_signed(stats['net']):>6}  {net_icon}",
    (
      f"🎯 Winrate    {stats['win_rate']:.0f}%   "
      f"({stats['wins']}W / {stats['losses']}L)"
    ),
    f"📦 Trades     {stats['trades']}",
    f"🟢 Avg win    {_signed(stats['average_win'])}",
    f"🔴 Avg loss   {_signed(stats['average_loss'])}",
    f"⚖️ Expectancy {_signed(stats['expectancy'])} / trade",
    (
      f"🏆 Best       {_signed(best['value'])}  · "
      f"#{best_seq} {_setup_label(best.get('setup_type'))}"
    ),
    (
      f"🩸 Worst      {_signed(worst['value'])}  · "
      f"#{worst_seq} {_setup_label(worst.get('setup_type'))}"
    ),
    "",
    "📐 By setup",
    *_branch_lines(stats["by_setup"], "setup"),
    "",
    "🕐 By session",
    *_branch_lines(stats["by_session"], "session"),
    "",
    "📈 Equity",
    f"{sparkline(stats['cumulative'])}  {_signed(stats['net'])}",
    "━━━━━━━━━━━━━━━━━━━━",
    "🤖 st_mich43l · weekly recap",
  ]
  return f"<pre>{escape(chr(10).join(lines))}</pre>"


async def _send_recap(text: str, channel_id: int) -> None:
  await send_with_retry(text, chat_id=channel_id)


async def _weekly_report_tick(now: datetime | None = None) -> bool:
  tz = ZoneInfo(settings.seq_reset_tz)
  now = now.astimezone(tz) if now else datetime.now(tz)
  if (
    now.weekday() != settings.weekly_report_dow
    or now.hour < settings.weekly_report_hour
  ):
    return False

  start, end = _closed_week_window(now)
  week_key = start.date().isoformat()
  if await get_meta(_META_KEY) == week_key:
    return False

  for symbol in SYMBOLS:
    records = await get_pips_records(
      int(start.timestamp()),
      int(end.timestamp()) - 1,
      symbol,
    )
    if not records and settings.weekly_report_skip_empty:
      continue
    stats = build_stats(
      records,
      await get_all_signals(symbol),
      settings.seq_reset_tz,
      settings.session_asia_start,
      settings.session_london_start,
      settings.session_ny_start,
    )
    text = format_weekly_recap(stats, symbol, start, end)
    for target in channels_for(symbol, "vip"):
      await _send_recap(text, int(target["channel_id"]))

  await set_meta(_META_KEY, week_key)
  return True


async def weekly_report_loop() -> None:
  """Check every 30 minutes and post at most once per closed week."""
  if not settings.weekly_report_enabled:
    log.info("Weekly performance recap disabled")
    return
  while True:
    try:
      await _weekly_report_tick()
    except asyncio.CancelledError:
      raise
    except Exception:
      log.exception("Weekly performance recap failed")
    await asyncio.sleep(_WEEKLY_INTERVAL)
