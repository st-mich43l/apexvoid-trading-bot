"""Restart-safe VIP-only weekly performance recap."""

import asyncio
import logging
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.persistence.store import (
  get_all_signals,
  get_meta,
  get_pips_records,
  set_meta,
)
from app.signals.reports import _stream_lines, build_stats, sparkline
from app.core.symbols import SYMBOLS, channels_for
from app.bot.client import send_with_retry

log = logging.getLogger(__name__)

_META_KEY = "last_weekly_report_date"
_WEEKLY_INTERVAL = 1800
_SEP = "━━━━━━━━━━━━━━━━━━━━━━"
_METRIC_LABEL_WIDTH = 11


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


def _metric_line(
  icon: str,
  label: str,
  value: str,
  suffix: str = "",
) -> str:
  line = f"{icon} {label:<{_METRIC_LABEL_WIDTH}} {value:>7}"
  if suffix:
    line = f"{line}  {suffix}"
  return line


def _best_worst_line(icon: str, label: str, row: dict) -> str:
  seq = row.get("daily_seq") or row.get("signal_id") or "?"
  return _metric_line(
    icon,
    label,
    _signed(row["value"]),
    f"· #{seq} {_setup_label(row.get('setup_type'))}",
  )


def _branch_lines(groups: list[dict], kind: str) -> list[str]:
  if not groups:
    return ["└─ —"]
  lines = []
  icons = {
    "Asia": "🌏",
    "London": "🌍",
    "NY": "🌎",
    "Legacy": "🕐",
  }
  for index, group in enumerate(groups):
    branch = "└─" if index == len(groups) - 1 else "├─"
    if kind == "setup":
      label = _setup_label(group["label"])
      lines.append(
        f"{branch} {label:<16} {_signed(group['net']):>7} · "
        f"{group['wins']}W/{group['losses']}L"
      )
    else:
      icon = icons.get(group["label"], "🕐")
      lines.append(
        f"{branch} {icon} {group['label']:<8} "
        f"{_signed(group['net']):>7}"
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
    text = "\n".join([
      f"📊 WEEKLY RECAP — {_symbol_label(symbol)}",
      f"🗓 {_date_range(start, end)}",
      _SEP,
      "🧘 No closed trades",
      "capital preserved · no stats to report",
      _SEP,
      "🤖 Apex Void · weekly recap",
    ])
    return f"<pre>{escape(text)}</pre>"

  best = stats["best"]
  worst = stats["worst"]
  net_icon = "🟢" if stats["net"] >= 0 else "🔴"
  lines = [
    f"📊 WEEKLY RECAP — {_symbol_label(symbol)}",
    f"🗓 {_date_range(start, end)}",
    _SEP,
    _metric_line("💰", "Net", _signed(stats["net"]), net_icon),
    _metric_line(
      "🎯",
      "Winrate",
      f"{stats['win_rate']:.0f}%",
      f"({stats['wins']}W / {stats['losses']}L)",
    ),
    _metric_line("📦", "Trades", str(stats["trades"])),
    _metric_line("🟢", "Avg win", _signed(stats["average_win"])),
    _metric_line("🔴", "Avg loss", _signed(stats["average_loss"])),
    _metric_line("⚖", "Expectancy", _signed(stats["expectancy"]), "/ trade"),
    _best_worst_line("🏆", "Best", best),
    _best_worst_line("🩸", "Worst", worst),
    "",
    "🧬 By stream",
    *_stream_lines(stats["by_stream"]),
    "",
    "📐 By setup",
    *_branch_lines(stats["by_setup"], "setup"),
    "",
    "🕐 By session",
    *_branch_lines(stats["by_session"], "session"),
    "",
    "📈 Equity",
    f"{sparkline(stats['cumulative'])}  {_signed(stats['net'])}",
    _SEP,
    "🤖 Apex Void · weekly recap",
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
