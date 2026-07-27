"""Pure-ish command parsing and signal resolution helpers."""

import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.persistence.store import get_all_signals, get_open_signals, get_signal_by_post
from app.core.symbols import SYMBOLS, channel_for_symbol

# Matches: +100 pips / -50 pips / +1500Pips / -30 PIPS
_PIPS_RE = re.compile(r'([+-])\s*(\d+)\s*pips?', re.IGNORECASE)

# Manual signal template (DM to bot):
#   gold sell entry zone (4100-4105)
#   sl 4110
#   tp 95/90/80   (absolute or 2-digit shorthand, any count)
_MANUAL_RE = re.compile(
  r'gold\s+(buy|sell)\s+(?:entry\s+zone\s*)?\(?\s*([\d.]+)\s*[-–—]\s*([\d.]+)\s*\)?\s*[\r\n]+'
  r'\s*sl\s+([\d.]+)\s*[\r\n]+'
  r'\s*tp\s+([\d./]+)',
  re.IGNORECASE,
)
SETUP_RESERVED_WORDS = frozenset({
  "setup",
  "vip",
  "scalp",
  "scalp-nhanh",
  "quick-scalp",
  "algo",
  "sl",
  "tp",
  "entry",
  "buy",
  "sell",
  "gold",
  "xau",
  "xauusd",
})
_SETUP_SUFFIX_RE = re.compile(
  r'(?i)(?<=\d)(?:\s*/\s*|\s+)(?:setup\s+)?'
  r'([a-z][a-z0-9_-]*)'
  r'(?:\s+(\*{1,3}|[1-3]))?\s*$'
)
_SCALP_SUFFIX_RE = re.compile(
  r'(?i)\s*/\s*(?:scalp|scalp[-_\s]*nhanh|quick[-_\s]*scalp)'
  r'(?=\s*(?:/|$))'
)
# Owner opt-in to arm broker-side execution for this signal (see
# app.signals.manual_intent). Composes with /vip and /scalp exactly like
# they compose with each other — stripped independently, order-agnostic.
_ALGO_SUFFIX_RE = re.compile(
  r'(?i)\s*/\s*algo(?=\s*(?:/|$))'
)
_ACTIVE_RE = re.compile(r'(?i)^\s*active(?:\s+#?(\d+))?\s*$')
_CLOSE_RE = re.compile(
  r'(?i)^\s*close(?:\s+#?(\d+))?\s+([+-]\d+)\s*'
  r'(?:pips?)?(?:\s+(\d{1,3})\s*%)?\s*$'
)
_CLOSEBE_RE = re.compile(r'(?i)^\s*close(?:\s+#?(\d+))?\s+be\s*$')
_CANCEL_RE = re.compile(r'(?i)^\s*cancel(?:\s+#?(\d+))?\s*$')
_SL_RE = re.compile(
  r'(?i)^\s*sl(?:\s+#?(\d+))?\s+(be|\d+(?:\.\d+)?)\s*$'
)
_REOPEN_RE = re.compile(
  r'(?i)^\s*reopen(?:\s+#?(\d+))?'
  r'(?:\s+([\d.]+)\s*[-–]\s*([\d.]+))?\s*$'
)
_TAG_RE = re.compile(
  r'(?i)^\s*tag\s+(?:(?:#?(\d+))|(?:id:(\d+)))\s+'
  r'([a-z0-9][a-z0-9_-]*)'
  r'(?:\s+(\*{1,3}|[1-3]))?\s*$'
)
_NOTE_RE = re.compile(r'(?is)^\s*note\s+#?(\d+)\s+(.+?)\s*$')
_TP_RE = re.compile(
  r'(?i)^\s*tp\s+#?(\d+)\s+(?:tp)?(\d+)\s+\+(\d+)\s*(?:pips?)?\s*$'
)


def _expand_entry_endpoint(value: float, anchor: float) -> float:
  """Expand a short zone endpoint to the closest price around the anchor."""
  if value >= 100:
    return value
  base = int(anchor / 100) * 100
  candidates = (base + value - 100, base + value, base + value + 100)
  return min(candidates, key=lambda price: abs(price - anchor))


def _expand_tp(val: float, entry: float, action: str) -> float:
  """Expand a 2-digit shorthand TP (e.g. 35) to a full price using entry's base."""
  if val >= 100:
    return val
  base = int(entry / 100) * 100
  price = base + val
  if action == 'SELL' and price >= entry:
    price -= 100
  elif action == 'BUY' and price <= entry:
    price += 100
  return price


def _parse_manual(text: str) -> Optional[dict]:
  raw = text.strip()
  raw, vip_count = re.subn(
    r'(?i)\s*/\s*vip(?=\s*(?:/|$))',
    "",
    raw,
  )
  raw, scalp_count = _SCALP_SUFFIX_RE.subn("", raw)
  raw, algo_count = _ALGO_SUFFIX_RE.subn("", raw)
  setup_type = None
  confluence = None
  setup_match = _SETUP_SUFFIX_RE.search(raw)
  if (
    setup_match
    and setup_match.group(1).lower() not in SETUP_RESERVED_WORDS
  ):
    setup_type = setup_match.group(1).lower()
    grade = setup_match.group(2)
    if grade:
      confluence = len(grade) if grade.startswith("*") else int(grade)
    raw = raw[:setup_match.start()].rstrip()
  elif scalp_count:
    setup_type = "scalp"
  raw = re.sub(
    r'\s*/\s*(?=(?:sl|tp)\b)',
    "\n",
    raw,
    flags=re.IGNORECASE,
  )
  m = _MANUAL_RE.search(raw)
  if not m:
    return None
  action, entry_a, entry_b, sl, tp_raw = m.groups()
  action = action.upper()
  entry_anchor = float(entry_a)
  entry_other = _expand_entry_endpoint(float(entry_b), entry_anchor)
  entry_low, entry_high = sorted((entry_anchor, entry_other))
  sl = float(sl)
  rr_entry = entry_low if action == 'SELL' else entry_high
  tps = [
    _expand_tp(float(v), rr_entry, action)
    for v in tp_raw.strip().split('/') if v.strip()
  ]
  if not tps:
    return None
  risk = abs(rr_entry - sl)
  return {
    'action': action,
    'entry': entry_low,
    'entry_end': entry_high,
    'rr_entry': rr_entry,
    'sl': sl,
    'tps': tps,
    'risk': risk,
    'setup_type': setup_type,
    'confluence': confluence,
    'visibility': 'vip' if vip_count else 'both',
    'execution_mode': 'algo' if algo_count else 'notify',
  }


def _period_range(period: str) -> tuple[int, int]:
  now = datetime.now(timezone.utc)
  today = now.replace(hour=0, minute=0, second=0, microsecond=0)
  p = period.lower().replace('  ', ' ')
  if p == "week":
    p = "this week"
  if p == 'today':
    return int(today.timestamp()), int(now.timestamp())
  if p == 'yesterday':
    return int((today - timedelta(days=1)).timestamp()), int(today.timestamp())
  if p == 'this week':
    monday = today - timedelta(days=now.weekday())
    return int(monday.timestamp()), int(now.timestamp())
  if p == 'last week':
    this_monday = today - timedelta(days=now.weekday())
    last_monday = this_monday - timedelta(weeks=1)
    return int(last_monday.timestamp()), int(this_monday.timestamp())
  return int(today.timestamp()), int(now.timestamp())


def _stats_range(period: str) -> tuple[int, int]:
  tz = ZoneInfo(settings.seq_reset_tz)
  now = datetime.now(tz)
  today = now.replace(hour=0, minute=0, second=0, microsecond=0)
  if period == "today":
    start = today
  elif period == "week":
    start = today - timedelta(days=today.weekday())
  elif period == "month":
    start = today.replace(day=1)
  else:
    return 0, int(now.timestamp())
  return int(start.timestamp()), int(now.timestamp())


def _is_owner(msg) -> bool:
  if not settings.telegram_owner_id:
    return False
  return msg.from_user is not None and msg.from_user.id == settings.telegram_owner_id


def _is_owner_cb(cb) -> bool:
  if not settings.telegram_owner_id:
    return False
  return cb.from_user is not None and cb.from_user.id == settings.telegram_owner_id


def _command_args(msg) -> str:
  return (msg.text or "").partition(" ")[2].strip()


def _take_symbol(
  raw: str,
  *,
  default: str | None = "XAU",
) -> tuple[str | None, str]:
  parts = raw.split(maxsplit=1)
  if parts and parts[0].upper() in SYMBOLS:
    return parts[0].upper(), parts[1] if len(parts) > 1 else ""
  return default, raw


def _seq_token(value: str) -> int | None:
  value = value.strip().lstrip("#")
  return int(value) if value.isdigit() else None


def _today_str() -> str:
  tz = ZoneInfo(settings.seq_reset_tz)
  return datetime.now(tz).date().isoformat()


async def _resolve_sid(
  explicit_seq: int | None,
  reply_to_id: int | None,
  symbol: str = "XAU",
) -> int | None:
  """Resolve a daily display number or reply target to a primary key."""
  opens = await get_open_signals(symbol)
  if explicit_seq is not None:
    todays = [
      s for s in opens
      if s["daily_seq"] == explicit_seq and s["trade_date"] == _today_str()
    ]
    if todays:
      return todays[-1]["id"]
    any_seq = [s for s in opens if s["daily_seq"] == explicit_seq]
    return any_seq[-1]["id"] if any_seq else None
  if reply_to_id is not None:
    row = await get_signal_by_post(
      channel_for_symbol(symbol),
      reply_to_id,
      open_only=True,
    )
    return (
      row["id"]
      if row and row.get("symbol", "XAU") == symbol
      else None
    )
  return opens[0]["id"] if len(opens) == 1 else None


async def _resolve_any_sid(
  explicit_seq: int | None,
  reply_to_id: int | None,
  symbol: str = "XAU",
) -> int | None:
  """Resolve a display number or reply across all lifecycle states."""
  signals = await get_all_signals(symbol)
  if explicit_seq is not None:
    todays = [
      signal for signal in signals
      if (
        signal["daily_seq"] == explicit_seq
        and signal["trade_date"] == _today_str()
      )
    ]
    if todays:
      return todays[-1]["id"]
    matching = [
      signal for signal in signals
      if signal["daily_seq"] == explicit_seq
    ]
    return matching[-1]["id"] if matching else None
  if reply_to_id is not None:
    row = await get_signal_by_post(
      channel_for_symbol(symbol),
      reply_to_id,
    )
    return (
      row["id"]
      if row and row.get("symbol", "XAU") == symbol
      else None
    )
  return signals[0]["id"] if len(signals) == 1 else None


def _parse_close(text: str) -> tuple[int | None, int, float | None] | None:
  match = _CLOSE_RE.match(text)
  if match:
    seq = int(match.group(1)) if match.group(1) else None
    frac = int(match.group(3)) / 100 if match.group(3) else None
    return seq, int(match.group(2)), frac
  match = _CLOSEBE_RE.match(text)
  if match:
    seq = int(match.group(1)) if match.group(1) else None
    return seq, 0, None
  return None


def _num(value: float | int) -> str:
  return f"{value:g}"
