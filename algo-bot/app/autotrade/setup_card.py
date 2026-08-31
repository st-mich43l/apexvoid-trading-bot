"""One Telegram forming card per setup: reply-threaded lifecycle, delete on
terminal (Codex Prompt P4).

Every setup gets exactly one card, anchored to its `setup_lifecycle.py`
`setup_id`. Re-detection of the same setup edits this card - it never posts
a second one. When a setup reaches a terminal state (REJECTED/INVALIDATED/
EXPIRED, folded onto setup_lifecycle's existing INVALIDATED/EXPIRED/
CANCELLED states - see worker.py/scanner.py call sites), the card is
deleted and the setup is never re-carded; no terminal notification is ever
posted, per the owner's explicit "remove message luôn, đừng spam" ask.

Lives in its own module, not in `delivery.py` or `scanner.py`, specifically
to avoid a circular import: `delivery.py` already imports from `scanner.py`
(`clear_active_setup_tracking`), so a new `scanner.py -> delivery.py` import
for card helpers would cycle. Both modules import from here instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from html import escape
from typing import Any, Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest

from app.autotrade.setup_execution_aggregate import (
  STATUS_LINE_BY_PROJECTION_STATE,
  resolve_setup_execution_aggregate,
)
from app.autotrade.setup_lifecycle import TERMINAL_STATES, load_setup
from app.autotrade.strategy_match import StrategyMatch
from app.core.config import runtime_config
from app.core.symbols import digits_for, pip_for

log = logging.getLogger(__name__)


def card_price_digits(symbol: str) -> int:
  """Instrument price digits for Telegram root-card Trade area.

  Live 2026-08-21 GBPUSD cards rendered every price as ``1.36`` because
  the formatter hard-coded two decimals (XAU). Unknown symbols stay at 2.
  """
  try:
    return int(digits_for(symbol))
  except KeyError:
    return 2


def card_min_price_move(symbol: str) -> float:
  """Minimum mid move before refreshing live Price now.

  The forming-price loop used ``min_move=0.5`` (fine for XAU, ~5000 pips on
  EURUSD) so FX root cards never updated after the first render.
  """
  try:
    pip = float(pip_for(symbol))
  except KeyError:
    pip = 0.1
  return max(pip, 1e-9)

# P0-6: a durable status write (save_forming_card_status/edit_forming_card_status)
# can arrive before the card itself has been created (the scanner send and the
# worker's status write race - see docs). Rather than silently dropping the
# visual update (and letting the delivery cursor advance past it), stash a
# reconciliation intent here; post_or_edit_forming_card applies and clears it
# the moment the card actually exists. TTL only needs to outlive the
# scanner/Telegram race window, not the setup itself.
_RECONCILE_PENDING_TTL_SECONDS = 900
# Prod 2026-08-10 13:07 UTC: two concurrent ensure/post paths both saw
# load_forming_card=None and both mode=send for the same setup_id (~3ms),
# creating Telegram roots 3946 then 3947 (WAITING FILL + full POSITION
# ACTIVATED orphan). Claim create before send; losers wait for the winner.
_FORMING_CARD_CREATE_LOCK_TTL_SECONDS = 30
_FORMING_CARD_CREATE_WAIT_SECONDS = 2.0
_FORMING_CARD_CREATE_POLL_SECONDS = 0.05

SendFn = Callable[..., Awaitable[Any]]
EditFn = Callable[[int, int, str], Awaitable[Any]]
DeleteFn = Callable[[int, int], Awaitable[Any]]

CARD_STATUS_PRIORITY = {
  "analysis_only": 0,
  "queued": 10,
  "preflight": 20,
  "waiting_retest": 30,
  "trigger_ready": 40,
  "plan_published": 100,
  "executor_armed": 120,
  "order_filled": 150,
  "sl_moved": 155,
  "tp_booked": 160,
  # Unfilled plan outcomes must outrank PLAN PUBLISHED so the root card
  # can leave WAITING FILL / PLAN PUBLISHED after expire/cancel/reject.
  "plan_expired": 200,
  "plan_cancelled": 200,
  "plan_rejected": 200,
  "terminal": 200,
}

_SAVE_STATUS_LUA = """
local current = redis.call('GET', KEYS[1])
local current_priority = -1
local current_line = ''
if current then
  local ok, decoded = pcall(cjson.decode, current)
  if ok and type(decoded) == 'table' then
    current_priority = tonumber(decoded['priority']) or -1
    current_line = tostring(decoded['status_line'] or '')
  else
    current_line = current
    if string.find(current, 'POSITION ACTIVATED', 1, true)
      or string.find(current, 'ORDER FILLED', 1, true) then
      current_priority = 150
    elseif string.find(current, 'PLAN EXPIRED', 1, true)
      or string.find(current, 'PLAN CANCELLED', 1, true)
      or string.find(current, 'PLAN REJECTED', 1, true)
      or string.find(current, 'TERMINAL', 1, true) then
      current_priority = 200
    elseif string.find(current, 'PLAN PUBLISHED', 1, true) then
      current_priority = 100
    elseif string.find(current, 'WAITING RETEST', 1, true) then
      current_priority = 30
    elseif string.find(current, 'PREFLIGHT', 1, true) then
      current_priority = 20
    elseif string.find(current, 'QUEUED', 1, true) then
      current_priority = 10
    elseif string.find(current, 'ANALYSIS ONLY', 1, true) then
      current_priority = 0
    end
  end
end
local next_priority = tonumber(ARGV[2])
if current_priority > next_priority then
  return 0
end
if current_priority == next_priority and current_line == ARGV[3] then
  return 2
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[4])
return 1
"""


@dataclass(frozen=True)
class FormingCardStatus:
  state: str
  status_line: str
  priority: int


def _infer_status_state(status_line: str) -> str:
  upper = status_line.upper()
  if "POSITION ACTIVATED" in upper or "ORDER FILLED" in upper:
    return "order_filled"
  if "EXECUTOR ARMED" in upper:
    return "executor_armed"
  if "PLAN EXPIRED" in upper:
    return "plan_expired"
  if "PLAN CANCELLED" in upper:
    return "plan_cancelled"
  if "PLAN REJECTED" in upper:
    return "plan_rejected"
  if "PLAN PUBLISHED" in upper:
    return "plan_published"
  if "TRIGGER READY" in upper:
    return "trigger_ready"
  if "WAITING RETEST" in upper:
    return "waiting_retest"
  if "PREFLIGHT" in upper:
    return "preflight"
  if "QUEUED" in upper:
    return "queued"
  if "ANALYSIS ONLY" in upper:
    return "analysis_only"
  if "CLOSED" in upper or "TERMINAL" in upper:
    return "terminal"
  return "analysis_only"


def should_stop_forming_price_track(
  text: str,
  *,
  status_state: str | None = None,
) -> bool:
  """Live Price now is only for pre-fill watching — stop once filled/closed.

  Continuous root-card edits after fill compete with ORDER FILLED replies for
  Telegram rate limits and make the card look 'alive' without the fill reply.
  """
  upper = (text or "").upper()
  if any(
    token in upper
    for token in (
      "POSITION ACTIVATED",
      "ORDER FILLED",
      "TERMINAL",
      "CLOSED",
      "TP BOOKED",
      "GROUP STOP",
      "STOP LOSS",
      "SL HIT",
      "PLAN EXPIRED",
      "PLAN REJECTED",
      "PLAN CANCELLED",
    )
  ):
    return True
  priority = CARD_STATUS_PRIORITY.get(str(status_state or ""), -1)
  return priority >= CARD_STATUS_PRIORITY["order_filled"]


_PRICE_NOW_LINE_RE = re.compile(
  r"^(\s*•\s*<b>Price now:</b>\s*<b>)([^<]+)(</b>\s*<i>\(live\)</i>\s*)$",
  re.IGNORECASE,
)
_CARD_HEADLINE_SYMBOL_RE = re.compile(
  r"<b>\s*([A-Z0-9]+)\s+[A-Z0-9]+\s*·",
  re.IGNORECASE,
)

FORMING_ACTIVE_INDEX_KEY = "auto_trade:forming_active"


def apply_forming_card_price(text: str, price: float, *, digits: int = 2) -> str:
  """Replace the Trade-area Price now line when present."""
  if not text or not math.isfinite(price):
    return text
  price_text = f"{price:,.{digits}f}"
  out: list[str] = []
  replaced = False
  for line in text.splitlines():
    match = _PRICE_NOW_LINE_RE.match(line)
    if match is not None:
      out.append(f"{match.group(1)}{price_text}{match.group(3)}")
      replaced = True
      continue
    out.append(line)
  return "\n".join(out) if replaced else text


def parse_forming_card_symbol(text: str) -> str | None:
  if not text:
    return None
  for line in text.splitlines():
    stripped = line.strip()
    activated = _ACTIVATED_HEADER_RE.match(stripped)
    if activated is not None:
      return str(activated.group("symbol")).upper()
    forming = _CARD_HEADER_RE.match(stripped)
    if forming is not None:
      return str(forming.group("symbol")).upper()
  match = _CARD_HEADLINE_SYMBOL_RE.search(text)
  if match is None:
    return None
  symbol = str(match.group(1)).upper()
  if symbol in {"POSITION", "TERMINAL"}:
    return None
  return symbol


def parse_forming_card_price_now(text: str) -> float | None:
  if not text:
    return None
  for line in text.splitlines():
    match = _PRICE_NOW_LINE_RE.match(line)
    if match is None:
      continue
    raw = match.group(2).replace(",", "").strip()
    try:
      value = float(raw)
    except ValueError:
      return None
    return value if math.isfinite(value) else None
  return None


def apply_forming_card_stop(text: str, stop_price: float, *, digits: int = 2) -> str:
  """Insert or replace the Trade-area Stop line and copy-draft SL value."""
  if not text or not math.isfinite(stop_price):
    return text
  stop_text = f"{stop_price:,.{digits}f}"
  plain_stop = f"{stop_price:.{digits}f}"
  lines = text.splitlines()
  stop_line = f"• <b>Stop:</b> <b>{stop_text}</b>"
  has_stop = any(
    line.strip().startswith("• <b>Stop:</b>") for line in lines
  )
  inserted = False
  out: list[str] = []
  for line in lines:
    stripped = line.strip()
    if stripped.startswith("• <b>Stop:</b>"):
      # Replace every existing Stop line with one canonical value; skip
      # duplicates so Trade area never shows Stop twice.
      if not inserted:
        out.append(stop_line)
        inserted = True
      continue
    out.append(line)
    if (
      not has_stop
      and not inserted
      and stripped.startswith("• <b>Key level:</b>")
    ):
      out.append(stop_line)
      inserted = True
  if not inserted:
    # Fall back: place before Context / Copy draft sections.
    rebuilt: list[str] = []
    placed = False
    for line in out:
      if (
        not placed
        and (
          "🧭 <b>Context</b>" in line
          or "📋 <b>Copy draft</b>" in line
        )
      ):
        rebuilt.append(stop_line)
        placed = True
      rebuilt.append(line)
    out = rebuilt if placed else [*out, stop_line]

  draft_re = re.compile(
    r"(gold\s+(?:buy|sell)\s+entry zone\s*\([^)]+\)\s*/\s*sl\s+)(\S+)",
    re.IGNORECASE,
  )
  return "\n".join(
    draft_re.sub(rf"\g<1>{plain_stop}", line) if "gold " in line.lower() else line
    for line in out
  )


def forming_message_key(setup_id: str) -> str:
  return f"auto_trade:forming_message:{setup_id}"


def forming_card_create_lock_key(setup_id: str) -> str:
  return f"auto_trade:forming_card_create_lock:{setup_id}"


def telegram_root_message_key(setup_id: str) -> str:
  """Durable setup_id → root_message_id mapping for one-root-card replies."""
  return f"auto_trade:telegram_root:{setup_id}"


def should_delete_root_on_terminal() -> bool:
  """Reject/expire/invalidate retain the root card body (never delete).

  Single-root mode used to allow delete; that orphaned reply threads.
  Close/reject now also leave the SETUP/ACTIVATED body intact — no
  TERMINAL rewrite on the root (see kill_setup_card).
  """
  return False


def forming_status_key(setup_id: str) -> str:
  return f"auto_trade:forming_status:{setup_id}"


def forming_reconcile_pending_key(setup_id: str) -> str:
  return f"auto_trade:forming_reconcile_pending:{setup_id}"


async def save_forming_reconcile_pending(
  client,
  setup_id: str,
  *,
  status_line: str,
  priority: int,
  reason_code: str,
  event_id: str | None = None,
) -> None:
  """P0-6: record that a status write arrived with no card to apply to yet.

  Idempotent - a later call for the same setup_id simply overwrites with
  the freshest requested state (created_at is preserved from any existing
  marker so the TTL reflects when reconciliation was FIRST requested, not
  each individual retry).
  """
  key = forming_reconcile_pending_key(setup_id)
  now = int(time.time())
  existing_created_at = now
  raw = await client.get(key)
  if raw:
    try:
      existing = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
      if isinstance(existing, dict) and existing.get("created_at"):
        existing_created_at = int(existing["created_at"])
    except (TypeError, ValueError, json.JSONDecodeError):
      pass
  payload = json.dumps(
    {
      "version": 1,
      "setup_id": setup_id,
      "requested_state": _infer_status_state(status_line),
      "status_line": status_line,
      "priority": priority,
      "reason_code": reason_code,
      "event_id": event_id,
      "created_at": existing_created_at,
      "updated_at": now,
    },
    separators=(",", ":"),
  )
  await client.set(key, payload, ex=_RECONCILE_PENDING_TTL_SECONDS)


async def load_forming_reconcile_pending(client, setup_id: str) -> dict | None:
  raw = await client.get(forming_reconcile_pending_key(setup_id))
  if not raw:
    return None
  try:
    data = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
  except (TypeError, ValueError, json.JSONDecodeError):
    return None
  return data if isinstance(data, dict) else None


async def clear_forming_reconcile_pending(client, setup_id: str) -> None:
  await client.delete(forming_reconcile_pending_key(setup_id))


# Matches the SETUP FORMING / MARKET OBSERVATION root-card headline (e.g.
# "🔎 <b>XAU M5 · SETUP FORMING</b>") so it can be rewritten in place once
# the position actually activates - a filled position showing a stale
# "SETUP FORMING" head reads as "still waiting" when it's already live.
_CARD_HEADER_RE = re.compile(
  r"^\S+\s*<b>(?P<symbol>[A-Za-z0-9]+)\s+(?P<tf>\S+)\s*·\s*[^<]*</b>$"
)
# Fill rewrites the headline to this shape; _CARD_HEADER_RE must not parse
# it (would read symbol=POSITION). Terminal close needs its own extract.
_ACTIVATED_HEADER_RE = re.compile(
  r"^\S+\s*<b>POSITION ACTIVATED\s*·\s*"
  r"(?P<symbol>[A-Za-z0-9]+)\s+(?P<tf>\S+)\s*</b>$",
  re.IGNORECASE,
)
_LIVE_PRICE_MARKER_RE = re.compile(r"\s*<i>\(live\)</i>", re.IGNORECASE)

# The direction/strategy body line format.py always writes right after the
# status slot (see format_plan_published_root_card) - "🔴 <b>SELL · ...</b>"
# or "🟢 <b>BUY · ...</b>". Used post-activation to tell "no status line is
# currently shown, this is body content" apart from "a real status line
# (SL MOVED/TP hit/...) is already sitting here", since several status
# texts and a BUY body line happen to share the same leading emoji (both
# TRIGGER READY and a BUY line start with 🟢).
_BODY_DIRECTION_LINE_RE = re.compile(r"^[🔴🟢]\s*<b>(BUY|SELL)\b")


def _position_activated_header(line: str) -> str | None:
  stripped = line.strip()
  if "POSITION ACTIVATED" in stripped:
    # Already rewritten by an earlier fill event on this same setup (e.g.
    # a multi-leg entry fires order_filled once per leg, then again on
    # "ENTRY GROUP FULLY FILLED"). Re-running _CARD_HEADER_RE against our
    # own prior output would parse "POSITION ACTIVATED" itself as the
    # symbol/tf tokens and mangle the header into "POSITION ACTIVATED ·
    # POSITION ACTIVATED" - confirmed live. Nothing left to rewrite.
    return None
  match = _CARD_HEADER_RE.match(stripped)
  if match is None:
    return None
  return (
    f"✅ <b>POSITION ACTIVATED · {match.group('symbol')} "
    f"{match.group('tf')}</b>"
  )


def _terminal_header(line: str) -> str | None:
  stripped = line.strip()
  if stripped.startswith("❌") and "TERMINAL" in stripped:
    return None
  activated = _ACTIVATED_HEADER_RE.match(stripped)
  if activated is not None:
    return (
      f"❌ <b>TERMINAL · {activated.group('symbol')} "
      f"{activated.group('tf')}</b>"
    )
  match = _CARD_HEADER_RE.match(stripped)
  if match is None:
    return None
  return (
    f"❌ <b>TERMINAL · {match.group('symbol')} "
    f"{match.group('tf')}</b>"
  )


def _strip_live_price_marker(text: str) -> str:
  """Terminal cards are closed — drop the live price cue."""
  return _LIVE_PRICE_MARKER_RE.sub("", text)


def forming_card_matches_strategy(text: str, match: StrategyMatch) -> bool:
  """True when the root body direction/strategy still matches ``match``.

  Confluence zones share one setup_id across detectors; publish can reuse an
  older forming card that only refreshed Stop. Stale Key Level / opposing
  direction bodies must be replaced before fill replies thread to them.
  """
  direction = str(match.direction or "").upper()
  strategy = str(match.strategy or "").strip()
  if not direction:
    return True
  for line in (text or "").splitlines():
    body = _BODY_DIRECTION_LINE_RE.match(line.strip())
    if body is None:
      continue
    if body.group(1) != direction:
      return False
    if strategy and strategy not in line:
      return False
    return True
  return False


def apply_forming_card_status(text: str, status_line: str) -> str:
  lines = text.splitlines()
  if not lines or not status_line:
    return text
  # Whether the header ALREADY says POSITION ACTIVATED tells us whether
  # line[1] is still the reserved status slot or was already folded away
  # by an earlier order_filled call below - it's the only reliable signal
  # since real status text and BUY/SELL body lines can share a leading
  # emoji (both TRIGGER READY and a BUY line start with 🟢).
  activated = "POSITION ACTIVATED" in lines[0]
  inferred = _infer_status_state(status_line)
  if inferred == "order_filled":
    rewritten_header = _position_activated_header(lines[0])
    if rewritten_header is not None:
      lines[0] = rewritten_header
    if not activated and len(lines) > 1:
      # First fill event on this setup: the header now already says
      # POSITION ACTIVATED, so a status line repeating it below is
      # redundant. A blank placeholder line was tried before, but
      # Telegram still renders an invisible-character-only line at full
      # line-height, so the card showed a stray empty line under the
      # header. Remove the line outright instead; a later real status
      # update (SL move/TP hit) re-inserts one via the branch below,
      # since by then the header will already read as activated.
      del lines[1]
    return "\n".join(lines)
  if inferred == "terminal":
    # Owner 2026-08-17: never paint TERMINAL on the autotrade root card.
    # Close / reject / expire outcomes live in threaded reply cards
    # (ORDER FILLED / TP / BE / POSITION CLOSED). Keep SETUP FORMING or
    # POSITION ACTIVATED body intact for audit; only drop the live price cue.
    return _strip_live_price_marker(text)
  if activated:
    # Post-fill status updates (SL move, then later a TP hit) must keep
    # replacing the SAME line, not stack a new one each time - only
    # insert when index 1 is still the direction/strategy body line,
    # i.e. no status has been shown here since activation yet.
    if len(lines) > 1 and not _BODY_DIRECTION_LINE_RE.match(lines[1].strip()):
      lines[1] = status_line
    else:
      lines.insert(1, status_line)
  elif len(lines) > 1:
    lines[1] = status_line
  else:
    lines.append(status_line)
  joined = "\n".join(lines)
  if inferred == "terminal":
    return _strip_live_price_marker(joined)
  return joined


async def save_forming_card_status(
  client,
  setup_id: str,
  status_line: str,
  *,
  state: str | None = None,
  ttl: int | None = None,
) -> bool:
  state = state or _infer_status_state(status_line)
  priority = CARD_STATUS_PRIORITY.get(state, 0)
  effective_ttl = ttl or max(
    86400, runtime_config.lifecycle.candidate.storage_ttl_seconds,
  )
  payload = json.dumps(
    {
      "state": state,
      "status_line": status_line,
      "priority": priority,
    },
    separators=(",", ":"),
  )
  try:
    result = await client.eval(
      _SAVE_STATUS_LUA,
      1,
      forming_status_key(setup_id),
      payload,
      priority,
      status_line,
      effective_ttl,
    )
  except Exception:
    if not getattr(
      client,
      "_apexvoid_allow_non_atomic_test_fallback",
      False,
    ):
      raise
    current = await load_forming_card_status_snapshot(client, setup_id)
    if current is not None and current.priority > priority:
      return False
    if (
      current is not None
      and current.priority == priority
      and current.status_line == status_line
    ):
      return False
    await client.set(
      forming_status_key(setup_id),
      payload,
      ex=effective_ttl,
    )
    return True
  return int(result or 0) == 1


async def load_forming_card_status_snapshot(
  client,
  setup_id: str,
) -> FormingCardStatus | None:
  raw = await client.get(forming_status_key(setup_id))
  if raw is None:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    data = json.loads(text)
  except (TypeError, ValueError, json.JSONDecodeError):
    data = None
  if isinstance(data, dict) and data.get("status_line"):
    state = str(data.get("state") or _infer_status_state(
      str(data["status_line"])
    ))
    return FormingCardStatus(
      state=state,
      status_line=str(data["status_line"]),
      priority=int(data.get("priority", CARD_STATUS_PRIORITY.get(state, 0))),
    )
  if not text:
    return None
  state = _infer_status_state(text)
  return FormingCardStatus(
    state=state,
    status_line=text,
    priority=CARD_STATUS_PRIORITY.get(state, 0),
  )


async def load_forming_card_status(
  client,
  setup_id: str,
) -> str | None:
  snapshot = await load_forming_card_status_snapshot(client, setup_id)
  return None if snapshot is None else snapshot.status_line


async def load_forming_card(client, setup_id: str) -> dict | None:
  """Return the card address and cached text, or None if unknown."""
  raw = await client.get(forming_message_key(setup_id))
  if not raw:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    data = json.loads(text)
  except (TypeError, ValueError):
    data = None
  if not isinstance(data, dict):
    # Pre-P4 scalar format: bare message_id, no chat_id stored (and a bare
    # numeric string like "7001" parses fine as JSON but isn't a dict
    # either, so this branch also catches that case). Still usable against
    # the default owner chat every existing card was sent to.
    try:
      return {
        "chat_id": runtime_config.delivery.telegram.telegram_owner_id,
        "message_id": int(text),
      }
    except (TypeError, ValueError):
      return None
  message_id = data.get("message_id")
  if message_id is None:
    return None
  try:
    return {
      "chat_id": (
        data.get("chat_id")
        or runtime_config.delivery.telegram.telegram_owner_id
      ),
      "message_id": int(message_id),
      "text": str(data.get("text") or ""),
    }
  except (TypeError, ValueError):
    return None


async def save_forming_card(
  client,
  setup_id: str,
  *,
  chat_id: int,
  message_id: int,
  text: str = "",
  ttl: int | None = None,
) -> None:
  effective_ttl = ttl or max(
    86400, runtime_config.lifecycle.candidate.storage_ttl_seconds,
  )
  payload = json.dumps(
    {"chat_id": chat_id, "message_id": message_id, "text": text},
    separators=(",", ":"),
  )
  await client.set(
    forming_message_key(setup_id),
    payload,
    ex=effective_ttl,
  )
  # message_id=0 is an in-flight create reservation only — peers must see the
  # Redis key, but price-track / reply-root must wait for a real Telegram id.
  if int(message_id) <= 0:
    await client.srem(FORMING_ACTIVE_INDEX_KEY, setup_id)
    return
  if should_stop_forming_price_track(text):
    await client.srem(FORMING_ACTIVE_INDEX_KEY, setup_id)
  else:
    await client.sadd(FORMING_ACTIVE_INDEX_KEY, setup_id)
    # Keep the index alive at least as long as the card TTL so restart
    # sweepers can still discover live cards without SCAN.
    await client.expire(FORMING_ACTIVE_INDEX_KEY, effective_ttl)
  # Explicit setup_id → root_message_id mapping for one-root-card replies.
  await client.set(
    telegram_root_message_key(setup_id),
    json.dumps(
      {
        "chat_id": chat_id,
        "root_message_id": message_id,
        "updated_at": int(time.time()),
      },
      separators=(",", ":"),
    ),
    ex=effective_ttl,
  )


async def load_telegram_root_message_id(client, setup_id: str) -> int | None:
  raw = await client.get(telegram_root_message_key(setup_id))
  if raw is None:
    card = await load_forming_card(client, setup_id)
    if card is None:
      return None
    try:
      return int(card["message_id"])
    except (KeyError, TypeError, ValueError):
      return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    data = json.loads(text)
    return int(data["root_message_id"])
  except (TypeError, ValueError, json.JSONDecodeError, KeyError):
    try:
      return int(text)
    except (TypeError, ValueError):
      return None


async def clear_forming_card(client, setup_id: str) -> None:
  await client.delete(
    forming_message_key(setup_id),
    forming_status_key(setup_id),
    forming_reconcile_pending_key(setup_id),
    telegram_root_message_key(setup_id),
  )
  await client.srem(FORMING_ACTIVE_INDEX_KEY, setup_id)


async def is_setup_terminal(client, setup_id: str) -> bool:
  record = await load_setup(client, setup_id)
  return record is not None and record.state in TERMINAL_STATES


async def post_or_edit_forming_card(
  client,
  setup_id: str,
  text: str,
  *,
  chat_id: int,
  send_fn: SendFn,
  edit_fn: EditFn,
  delete_fn: DeleteFn | None = None,
  ttl: int | None = None,
) -> int | None:
  """Post the forming card once per setup_id; every later call edits it.

  P0-4/P0-5: the setup's canonical/status state can change WHILE send_fn or
  edit_fn is awaiting Telegram - after the round-trip completes, this always
  re-reads canonical + status state and reconciles
  (reconcile_forming_card_after_send) before returning, rather than trusting
  the `text` computed before the send. `delete_fn` is optional only for
  backward compatibility with callers that cannot yet delete (it is
  required for the terminal-during-send branch to actually remove the
  message rather than merely neutralizing it via edit).

  Concurrent first-create callers (prod HFS double-send race) contend on a
  short Redis SET NX lock so only one Telegram root is posted for a setup_id.

  Returns the card's message_id, or None if the setup is already terminal
  (a rejected/invalidated/expired setup is never re-carded, checked here so
  every caller gets this guard for free - and re-checked again after the
  send/edit completes) or Telegram would not accept the edit and a fresh
  send also failed.
  """
  if await is_setup_terminal(client, setup_id):
    return None
  current_status = await load_forming_card_status(client, setup_id)
  if current_status is not None:
    text = apply_forming_card_status(text, current_status)
  existing = await load_forming_card(client, setup_id)
  message_id: int | None = None
  resolved_chat_id = chat_id
  if existing is not None and int(existing.get("message_id") or 0) > 0:
    if existing.get("text") == text:
      message_id = existing["message_id"]
      resolved_chat_id = existing["chat_id"]
    else:
      log.info("forming_card_send_started setup_id=%s mode=edit", setup_id)
      try:
        await edit_fn(existing["chat_id"], existing["message_id"], text)
        message_id = existing["message_id"]
        resolved_chat_id = existing["chat_id"]
      except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).casefold():
          message_id = existing["message_id"]
          resolved_chat_id = existing["chat_id"]
        else:
          log.info(
            "forming card edit failed setup_id=%s, sending a fresh one instead",
            setup_id,
          )
  if message_id is None:
    message_id, resolved_chat_id = await _send_forming_card_once(
      client,
      setup_id,
      text,
      chat_id=chat_id,
      send_fn=send_fn,
      edit_fn=edit_fn,
    )
    if message_id is None:
      return None
  # Persist identity immediately (P0-4 item 4) before any further
  # reconciliation, so a crash here still leaves the card discoverable.
  await save_forming_card(
    client,
    setup_id,
    chat_id=resolved_chat_id,
    message_id=message_id,
    text=text,
    ttl=ttl,
  )
  return await reconcile_forming_card_after_send(
    client,
    setup_id,
    chat_id=resolved_chat_id,
    message_id=message_id,
    originally_sent_text=text,
    edit_fn=edit_fn,
    delete_fn=delete_fn,
  )


async def _await_peer_forming_card(
  client,
  setup_id: str,
  *,
  timeout_seconds: float = _FORMING_CARD_CREATE_WAIT_SECONDS,
  poll_seconds: float = _FORMING_CARD_CREATE_POLL_SECONDS,
) -> dict | None:
  deadline = time.monotonic() + max(0.0, float(timeout_seconds))
  while time.monotonic() < deadline:
    peer = await load_forming_card(client, setup_id)
    if peer is not None and int(peer.get("message_id") or 0) > 0:
      return peer
    await asyncio.sleep(max(0.01, float(poll_seconds)))
  peer = await load_forming_card(client, setup_id)
  if peer is not None and int(peer.get("message_id") or 0) > 0:
    return peer
  return None


async def _edit_existing_forming_card(
  *,
  existing: dict,
  text: str,
  setup_id: str,
  edit_fn: EditFn,
) -> tuple[int | None, int]:
  message_id = int(existing.get("message_id") or 0)
  chat_id = int(existing["chat_id"])
  if message_id <= 0:
    return None, chat_id
  if existing.get("text") == text:
    return message_id, chat_id
  log.info("forming_card_send_started setup_id=%s mode=edit", setup_id)
  try:
    await edit_fn(chat_id, message_id, text)
    return message_id, chat_id
  except TelegramBadRequest as exc:
    if "message is not modified" in str(exc).casefold():
      return message_id, chat_id
    log.info(
      "forming card edit failed setup_id=%s error=%s",
      setup_id,
      exc,
    )
    return None, chat_id


async def _send_forming_card_once(
  client,
  setup_id: str,
  text: str,
  *,
  chat_id: int,
  send_fn: SendFn,
  edit_fn: EditFn,
) -> tuple[int | None, int]:
  """Send exactly one new Telegram root for ``setup_id`` under a create lock."""
  lock_key = forming_card_create_lock_key(setup_id)
  claimed = bool(
    await client.set(
      lock_key,
      "1",
      nx=True,
      ex=_FORMING_CARD_CREATE_LOCK_TTL_SECONDS,
    )
  )
  if not claimed:
    log.info(
      "forming_card_create_lock_wait setup_id=%s",
      setup_id,
    )
    peer = await _await_peer_forming_card(client, setup_id)
    if peer is not None:
      message_id, resolved_chat_id = await _edit_existing_forming_card(
        existing=peer,
        text=text,
        setup_id=setup_id,
        edit_fn=edit_fn,
      )
      if message_id is not None:
        return message_id, resolved_chat_id
    # Peer never published a real Telegram root — try to take over create.
    claimed = bool(
      await client.set(
        lock_key,
        "1",
        nx=True,
        ex=_FORMING_CARD_CREATE_LOCK_TTL_SECONDS,
      )
    )
    if not claimed:
      peer = await _await_peer_forming_card(
        client,
        setup_id,
        timeout_seconds=0.5,
      )
      if peer is not None:
        message_id, resolved_chat_id = await _edit_existing_forming_card(
          existing=peer,
          text=text,
          setup_id=setup_id,
          edit_fn=edit_fn,
        )
        if message_id is not None:
          return message_id, resolved_chat_id
      log.warning(
        "forming_card_create_lock_exhausted setup_id=%s",
        setup_id,
      )
      return None, chat_id

  # Won the create lock — release it even if Telegram send fails so a peer
  # (or a later fill-path ensure) can retry instead of lock_exhausted.
  try:
    # Won the create lock — re-check in case a peer saved between our first
    # load and the SET NX claim.
    peer = await load_forming_card(client, setup_id)
    if peer is not None and int(peer.get("message_id") or 0) > 0:
      message_id, resolved_chat_id = await _edit_existing_forming_card(
        existing=peer,
        text=text,
        setup_id=setup_id,
        edit_fn=edit_fn,
      )
      if message_id is not None:
        return message_id, resolved_chat_id

    # Reserve Redis identity before Telegram I/O so waiters see an in-flight
    # create and do not race a second send while our round-trip is pending.
    await save_forming_card(
      client,
      setup_id,
      chat_id=chat_id,
      message_id=0,
      text=text,
    )
    log.info("forming_card_send_started setup_id=%s mode=send", setup_id)
    try:
      sent = await send_fn(text, chat_id=chat_id)
    except Exception:
      await clear_forming_card(client, setup_id)
      raise
    message_id = int(sent.message_id)
    await save_forming_card(
      client,
      setup_id,
      chat_id=chat_id,
      message_id=message_id,
      text=text,
    )
    return message_id, chat_id
  finally:
    try:
      await client.delete(lock_key)
    except Exception:
      log.exception(
        "forming_card_create_lock_release_failed setup_id=%s",
        setup_id,
      )


async def reconcile_forming_card_after_send(
  client,
  setup_id: str,
  *,
  chat_id: int,
  message_id: int,
  originally_sent_text: str,
  edit_fn: EditFn,
  delete_fn: DeleteFn | None = None,
) -> int | None:
  """P0-4/P0-5: re-read canonical + status state after a Telegram round-trip.

  Closes the exact race the owner reported in production: scanner reads
  QUEUED, calls Telegram send, the send is in flight, the worker persists a
  newer status (or the setup goes terminal) while waiting - by the time
  send_fn/edit_fn returns, `originally_sent_text` may already be stale, or
  the card may no longer be allowed to exist at all. Re-checks terminal
  state TWICE (before AND after the reconciliation edit) to close the
  second race window where terminal transition happens during the repair
  edit itself. Never creates a second card - always operates on the
  already-sent/edited message_id.
  """
  if await is_setup_terminal(client, setup_id):
    log.info(
      "forming_card_terminal_during_send setup_id=%s stage=post_send",
      setup_id,
    )
    await _delete_or_neutralize(
      client, setup_id, chat_id=chat_id, message_id=message_id,
      edit_fn=edit_fn, delete_fn=delete_fn,
    )
    return None

  reconciled_text = originally_sent_text
  latest_status = await load_forming_card_status_snapshot(client, setup_id)
  if latest_status is not None:
    candidate_text = apply_forming_card_status(
      originally_sent_text, latest_status.status_line,
    )
    if candidate_text != originally_sent_text:
      log.info(
        "forming_card_status_changed_during_send setup_id=%s state=%s",
        setup_id, latest_status.state,
      )
      try:
        await edit_fn(chat_id, message_id, candidate_text)
        reconciled_text = candidate_text
        log.info(
          "forming_card_reconciled_after_send setup_id=%s state=%s",
          setup_id, latest_status.state,
        )
      except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).casefold():
          reconciled_text = candidate_text
        else:
          log.info(
            "forming card post-send status reconcile failed setup_id=%s "
            "error=%s",
            setup_id, exc,
          )
      await save_forming_card(
        client, setup_id, chat_id=chat_id, message_id=message_id,
        text=reconciled_text,
      )

  # Second race window (spec item 8): terminal transition could have landed
  # exactly during the reconciliation edit above.
  if await is_setup_terminal(client, setup_id):
    log.info(
      "forming_card_terminal_during_send setup_id=%s stage=post_reconcile",
      setup_id,
    )
    await _delete_or_neutralize(
      client, setup_id, chat_id=chat_id, message_id=message_id,
      edit_fn=edit_fn, delete_fn=delete_fn,
    )
    return None

  pending = await load_forming_reconcile_pending(client, setup_id)
  if pending is not None:
    pending_line = str(pending.get("status_line") or "")
    if pending_line and pending_line != reconciled_text:
      applied_text = apply_forming_card_status(reconciled_text, pending_line)
      if applied_text != reconciled_text:
        try:
          await edit_fn(chat_id, message_id, applied_text)
          reconciled_text = applied_text
        except TelegramBadRequest as exc:
          if "message is not modified" in str(exc).casefold():
            reconciled_text = applied_text
          else:
            log.info(
              "forming card pending-marker reconcile failed setup_id=%s "
              "error=%s",
              setup_id, exc,
            )
        await save_forming_card(
          client, setup_id, chat_id=chat_id, message_id=message_id,
          text=reconciled_text,
        )
    await clear_forming_reconcile_pending(client, setup_id)
    log.info("forming_card_projection_repaired setup_id=%s", setup_id)

  return message_id


async def _delete_or_neutralize(
  client,
  setup_id: str,
  *,
  chat_id: int,
  message_id: int,
  edit_fn: EditFn,
  delete_fn: DeleteFn | None,
) -> None:
  deleted = False
  if delete_fn is not None:
    try:
      await delete_fn(chat_id, message_id)
      deleted = True
    except TelegramBadRequest:
      log.info(
        "forming card delete failed during send-race cleanup setup_id=%s, "
        "editing to terminal state instead",
        setup_id,
        exc_info=True,
      )
  if not deleted:
    try:
      await edit_fn(chat_id, message_id, _terminal_card_text(""))
    except TelegramBadRequest:
      log.exception(
        "forming card terminal edit also failed during send-race cleanup "
        "setup_id=%s",
        setup_id,
      )
  await clear_forming_card(client, setup_id)


async def edit_forming_card_status(
  client,
  setup_id: str,
  status_line: str,
  *,
  state: str | None = None,
  reason_code: str = "",
  event_id: str | None = None,
  edit_fn: EditFn,
) -> bool | str:
  """Replace only the lifecycle line on the setup's existing card.

  Returns True (applied), False (a real delivery failure), or the string
  "pending_card_creation" (P0-6: the durable status was saved, but no card
  exists yet to visually reflect it - a reconciliation marker was stashed
  instead of silently dropping the update; post_or_edit_forming_card applies
  it the moment the card is created). None of these block the caller's
  delivery cursor - see delivery.py's _process_owner_entries, which never
  inspects this return value.
  """
  changed = await save_forming_card_status(
    client,
    setup_id,
    status_line,
    state=state,
  )
  if not changed:
    snapshot = await load_forming_card_status_snapshot(client, setup_id)
    if snapshot is None or snapshot.status_line != status_line:
      return True
  card = await load_forming_card(client, setup_id)
  if (
    card is None
    or not card.get("text")
    or int(card.get("message_id") or 0) <= 0
  ):
    if await is_setup_terminal(client, setup_id):
      # A terminal setup is never re-carded (P0-5) - no point stashing a
      # marker post_or_edit_forming_card will just refuse to apply anyway.
      return False
    resolved_priority = CARD_STATUS_PRIORITY.get(
      state or _infer_status_state(status_line), 0,
    )
    await save_forming_reconcile_pending(
      client,
      setup_id,
      status_line=status_line,
      priority=resolved_priority,
      reason_code=reason_code,
      event_id=event_id,
    )
    return "pending_card_creation"
  text = apply_forming_card_status(str(card["text"]), status_line)
  if text == card["text"]:
    if should_stop_forming_price_track(text, status_state=state):
      await client.srem(FORMING_ACTIVE_INDEX_KEY, setup_id)
    return True
  try:
    await edit_fn(card["chat_id"], card["message_id"], text)
  except TelegramBadRequest as exc:
    if "message is not modified" in str(exc).casefold():
      await save_forming_card(
        client,
        setup_id,
        chat_id=card["chat_id"],
        message_id=card["message_id"],
        text=text,
      )
      if should_stop_forming_price_track(text, status_state=state):
        await client.srem(FORMING_ACTIVE_INDEX_KEY, setup_id)
      return True
    log.info(
      "forming card status edit failed setup_id=%s error=%s",
      setup_id,
      exc,
    )
    return False
  await save_forming_card(
    client,
    setup_id,
    chat_id=card["chat_id"],
    message_id=card["message_id"],
    text=text,
  )
  # Filled/activated cards must leave the live Price-now index immediately so
  # the track loop cannot keep edit-flooding Telegram ahead of fill replies.
  if should_stop_forming_price_track(text, status_state=state):
    await client.srem(FORMING_ACTIVE_INDEX_KEY, setup_id)
  return True


async def edit_forming_card_stop(
  client,
  setup_id: str,
  stop_price: float,
  *,
  digits: int | None = None,
  edit_fn: EditFn,
) -> bool:
  """Patch the root card Trade-area Stop line and copy-draft SL."""
  card = await load_forming_card(client, setup_id)
  if card is None or not card.get("text"):
    return False
  if int(card.get("message_id") or 0) <= 0:
    return False
  resolved_digits = digits
  if resolved_digits is None:
    symbol = parse_forming_card_symbol(str(card["text"]))
    resolved_digits = card_price_digits(symbol) if symbol else 2
  text = apply_forming_card_stop(
    str(card["text"]), float(stop_price), digits=int(resolved_digits),
  )
  if text == card["text"]:
    return True
  try:
    await edit_fn(card["chat_id"], card["message_id"], text)
  except TelegramBadRequest as exc:
    if "message is not modified" in str(exc).casefold():
      await save_forming_card(
        client,
        setup_id,
        chat_id=card["chat_id"],
        message_id=card["message_id"],
        text=text,
      )
      return True
    log.info(
      "forming card stop edit failed setup_id=%s error=%s",
      setup_id,
      exc,
    )
    return False
  await save_forming_card(
    client,
    setup_id,
    chat_id=card["chat_id"],
    message_id=card["message_id"],
    text=text,
  )
  return True


async def edit_forming_card_price(
  client,
  setup_id: str,
  price: float,
  *,
  digits: int | None = None,
  edit_fn: EditFn,
  min_move: float | None = None,
) -> bool:
  """Patch the root card Price now line when mid has moved enough."""
  from aiogram.exceptions import TelegramRetryAfter

  card = await load_forming_card(client, setup_id)
  if card is None or not card.get("text"):
    return False
  if int(card.get("message_id") or 0) <= 0:
    # In-flight create reservation — never call Telegram with message_id=0.
    return False
  text = str(card["text"])
  symbol = parse_forming_card_symbol(text)
  resolved_digits = (
    int(digits)
    if digits is not None
    else (card_price_digits(symbol) if symbol else 2)
  )
  resolved_min_move = (
    float(min_move)
    if min_move is not None
    else (card_min_price_move(symbol) if symbol else 0.1)
  )
  snapshot = await load_forming_card_status_snapshot(client, setup_id)
  status_state = snapshot.state if snapshot is not None else None
  if should_stop_forming_price_track(text, status_state=status_state):
    await client.srem(FORMING_ACTIVE_INDEX_KEY, setup_id)
    return False
  current = parse_forming_card_price_now(text)
  if current is not None and abs(float(price) - current) < resolved_min_move:
    return False
  next_text = apply_forming_card_price(
    text, float(price), digits=resolved_digits,
  )
  if next_text == text:
    return False
  try:
    await edit_fn(card["chat_id"], card["message_id"], next_text)
  except TelegramRetryAfter as exc:
    # Pause the whole track loop so ORDER FILLED / status replies can send.
    global _PRICE_TRACK_PAUSE_UNTIL
    _PRICE_TRACK_PAUSE_UNTIL = time.monotonic() + float(
      getattr(exc, "retry_after", 5) or 5
    ) + 1.0
    log.warning(
      "forming card price edit rate-limited setup_id=%s retry_after=%s",
      setup_id,
      getattr(exc, "retry_after", None),
    )
    return False
  except TelegramBadRequest as exc:
    if "message is not modified" in str(exc).casefold():
      await save_forming_card(
        client,
        setup_id,
        chat_id=card["chat_id"],
        message_id=card["message_id"],
        text=next_text,
      )
      return True
    log.info(
      "forming card price edit failed setup_id=%s error=%s",
      setup_id,
      exc,
    )
    return False
  await save_forming_card(
    client,
    setup_id,
    chat_id=card["chat_id"],
    message_id=card["message_id"],
    text=next_text,
  )
  return True


_PRICE_TRACK_PAUSE_UNTIL = 0.0


async def list_active_forming_setup_ids(client) -> list[str]:
  members = await client.smembers(FORMING_ACTIVE_INDEX_KEY)
  out: list[str] = []
  for raw in members or ():
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    if text:
      out.append(text)
  return out


async def refresh_forming_card_prices(
  client,
  *,
  edit_fn: EditFn,
  digits: int | None = None,
  min_move: float | None = None,
) -> int:
  """Best-effort Price now refresh for all indexed forming cards."""
  updated = 0
  for setup_id in await list_active_forming_setup_ids(client):
    card = await load_forming_card(client, setup_id)
    if card is None or not card.get("text"):
      await client.srem(FORMING_ACTIVE_INDEX_KEY, setup_id)
      continue
    if int(card.get("message_id") or 0) <= 0:
      await client.srem(FORMING_ACTIVE_INDEX_KEY, setup_id)
      continue
    if await is_setup_terminal(client, setup_id):
      await client.srem(FORMING_ACTIVE_INDEX_KEY, setup_id)
      continue
    snapshot = await load_forming_card_status_snapshot(client, setup_id)
    status_state = snapshot.state if snapshot is not None else None
    if should_stop_forming_price_track(
      str(card["text"]), status_state=status_state,
    ):
      await client.srem(FORMING_ACTIVE_INDEX_KEY, setup_id)
      continue
    symbol = parse_forming_card_symbol(str(card["text"]))
    if not symbol:
      continue
    mid = await _fresh_spot_mid(client, symbol)
    if mid is None:
      continue
    ok = await edit_forming_card_price(
      client,
      setup_id,
      float(mid),
      digits=(
        int(digits) if digits is not None else card_price_digits(symbol)
      ),
      edit_fn=edit_fn,
      min_move=(
        float(min_move)
        if min_move is not None
        else card_min_price_move(symbol)
      ),
    )
    if ok:
      updated += 1
  return updated


async def _fresh_spot_mid(client, symbol: str) -> float | None:
  raw = await client.get(f"price:{symbol.upper()}:spot")
  if raw is None:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    payload = json.loads(text)
    bid = float(payload["bid"])
    ask = float(payload["ask"])
    ts = int(payload["ts"])
  except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    return None
  mid = (bid + ask) / 2.0
  if not math.isfinite(mid) or mid <= 0:
    return None
  now = int(time.time())
  max_age = max(1, int(runtime_config.market_data.spot.maximum_age_seconds))
  if not (0 <= now - ts <= max_age):
    return None
  return mid


async def forming_price_track_loop() -> None:
  """Refresh forming-card Price now ~5s until fill/activation, then stop."""
  import asyncio

  from app.bot.client import edit_scanner_price_now
  from app.persistence import redis_state

  client = redis_state.get_client()
  log.info(
    "forming price track loop started interval_s=5.0 "
    "min_move=per_instrument_pip"
  )
  while True:
    try:
      from app.bot.telegram_actor import flood_paused

      pause_for = _PRICE_TRACK_PAUSE_UNTIL - time.monotonic()
      if pause_for > 0 or flood_paused():
        await asyncio.sleep(max(pause_for, 1.0) if pause_for > 0 else 1.0)
        continue
      await refresh_forming_card_prices(
        client,
        edit_fn=edit_scanner_price_now,
      )
    except Exception:
      log.exception("forming price track refresh failed")
    await asyncio.sleep(5.0)


def _terminal_status_line(reason_code: str) -> str:
  reason = (reason_code or "closed").strip().replace("_", " ")
  return f"❌ <b>TERMINAL</b> · {reason}"


def _terminal_card_text(reason_code: str, existing_text: str | None = None) -> str:
  """Legacy terminal body used only by the force-delete fallback path."""
  status = _terminal_status_line(reason_code)
  if existing_text and existing_text.strip():
    # Prefer leaving the existing body; only used when delete fails.
    intact = apply_forming_card_status(existing_text, status)
    if "TERMINAL" not in intact:
      return intact
    return intact
  return f"🤖 <b>ApexVoid Algo</b>\n{status}"


async def kill_setup_card(
  client,
  setup_id: str,
  *,
  reason_code: str,
  delete_fn: DeleteFn,
  edit_fn: EditFn,
) -> None:
  """Close the forming/root card on reject/invalidate/expire/position close.

  Default: leave the Telegram root body intact (SETUP FORMING /
  POSITION ACTIVATED) so Fill/TP/BE/close replies still thread to it.
  Never rewrite the root to TERMINAL — that reason lives on the reply
  cards. Delete remains opt-in via should_delete_root_on_terminal().
  """
  await client.srem(FORMING_ACTIVE_INDEX_KEY, setup_id)
  card = await load_forming_card(client, setup_id)
  if card is None:
    await clear_forming_card(client, setup_id)
    return

  existing_text = str(card.get("text") or "")
  intact = _strip_live_price_marker(existing_text) if existing_text else existing_text
  if not should_delete_root_on_terminal():
    if intact and intact != existing_text:
      try:
        await edit_fn(card["chat_id"], card["message_id"], intact)
      except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).casefold():
          log.info(
            "forming card intact close edit failed setup_id=%s reason=%s",
            setup_id, reason_code,
            exc_info=True,
          )
    # Keep forming_message + telegram_root mapping for reply threading.
    await save_forming_card(
      client,
      setup_id,
      chat_id=int(card["chat_id"]),
      message_id=int(card["message_id"]),
      text=intact or existing_text,
    )
    # save_forming_card re-indexes WAITING FILL cards for live Price now.
    # Close/reject/expire must not keep that loop on a dead trade.
    await client.srem(FORMING_ACTIVE_INDEX_KEY, setup_id)
    await client.delete(
      forming_status_key(setup_id),
      forming_reconcile_pending_key(setup_id),
    )
    log.info(
      "forming_card_left_intact setup_id=%s reason=%s",
      setup_id, reason_code,
    )
    return

  # Legacy delete path retained for tests that force-delete via monkeypatch.
  terminal = _terminal_card_text(reason_code, existing_text)
  try:
    await delete_fn(card["chat_id"], card["message_id"])
  except TelegramBadRequest:
    log.info(
      "forming card delete failed setup_id=%s reason=%s, editing to "
      "terminal state instead",
      setup_id, reason_code,
      exc_info=True,
    )
    try:
      await edit_fn(card["chat_id"], card["message_id"], terminal)
    except TelegramBadRequest as exc:
      if "message is not modified" not in str(exc).casefold():
        log.exception(
          "forming card terminal edit also failed setup_id=%s", setup_id,
        )
  await clear_forming_card(client, setup_id)


async def assert_or_repair_forming_projection(
  client,
  setup_id: str,
  *,
  edit_fn: EditFn,
) -> bool:
  """P0-7: cross-check the aggregate's effective status against the
  durable forming-status snapshot and the cached card text's lifecycle
  line, repairing only the status line (never the setup body) when they
  disagree.

  Returns True if a repair was made, False if everything already agreed
  (or there is nothing to repair - no card, or the setup is terminal).
  """
  aggregate = await resolve_setup_execution_aggregate(client, setup_id)
  if aggregate.terminal or aggregate.status_line is None:
    return False
  card = await load_forming_card(client, setup_id)
  if card is None or not card.get("text"):
    return False
  snapshot = await load_forming_card_status_snapshot(client, setup_id)
  cached_line = str(card["text"]).splitlines()[1] if len(
    str(card["text"]).splitlines(),
  ) > 1 else ""
  if (
    snapshot is not None
    and snapshot.status_line == aggregate.status_line
    and cached_line == aggregate.status_line
  ):
    return False
  await save_forming_card_status(client, setup_id, aggregate.status_line)
  repaired_text = apply_forming_card_status(
    str(card["text"]), aggregate.status_line,
  )
  if repaired_text == card["text"]:
    return False
  try:
    await edit_fn(card["chat_id"], card["message_id"], repaired_text)
  except TelegramBadRequest as exc:
    if "message is not modified" not in str(exc).casefold():
      log.info(
        "forming card projection repair failed setup_id=%s error=%s",
        setup_id, exc,
      )
      return False
  await save_forming_card(
    client, setup_id, chat_id=card["chat_id"], message_id=card["message_id"],
    text=repaired_text,
  )
  log.info("forming_card_projection_repaired setup_id=%s", setup_id)
  return True


PLAN_PUBLISHED_STATUS_LINE = STATUS_LINE_BY_PROJECTION_STATE["plan_published"]
# Reserved status-slot placeholder so TERMINAL / later edits still replace
# lines[1] without showing "PLAN PUBLISHED" on the forming card.
_PLAN_PUBLISHED_STATUS_SLOT = "\u200b"


def _price_text(value: float, *, symbol: str) -> str:
  digits = card_price_digits(symbol)
  return f"{float(value):,.{digits}f}"


def _target_reference_price(match: StrategyMatch) -> float | None:
  """Reference for displayed TP pip offsets on the root card."""
  direction = str(match.direction or "").upper()
  try:
    if direction == "BUY" and match.entry_low is not None:
      return float(match.entry_low)
    if direction == "SELL" and match.entry_high is not None:
      return float(match.entry_high)
    if match.key_level is not None:
      return float(match.key_level)
    if match.entry_low is not None and match.entry_high is not None:
      return (float(match.entry_low) + float(match.entry_high)) / 2.0
    if match.current_price is not None:
      return float(match.current_price)
  except (TypeError, ValueError):
    return None
  return None


def _target_pip_offset(
  target_price: float,
  *,
  reference: float,
  direction: str,
  symbol: str,
) -> int:
  try:
    pip = float(pip_for(symbol))
    ref = float(reference)
    price = float(target_price)
  except (KeyError, TypeError, ValueError):
    return 0
  if pip <= 0 or not (math.isfinite(ref) and math.isfinite(price)):
    return 0
  side = str(direction or "").upper()
  if side == "BUY":
    delta = price - ref
  elif side == "SELL":
    delta = ref - price
  else:
    delta = abs(price - ref)
  return max(0, int(round(delta / pip)))


def quote_inside_entry_zone(
  price: float | None,
  entry_low: float | None,
  entry_high: float | None,
) -> bool:
  """True when live price sits inside the declared entry band."""
  try:
    if price is None or entry_low is None or entry_high is None:
      return False
    low = float(entry_low)
    high = float(entry_high)
    px = float(price)
  except (TypeError, ValueError):
    return False
  if not (math.isfinite(px) and math.isfinite(low) and math.isfinite(high)):
    return False
  if high < low:
    low, high = high, low
  return low <= px <= high


def forming_card_headline(
  symbol: str,
  tf: str,
  *,
  in_zone: bool,
) -> str:
  """Headline for the Telegram root card.

  SETUP FORMING lied when Price now was already inside the entry zone
  (2026-08-05 Zone Reaction card). Prefer an explicit IN ZONE signal so the
  owner is not told the setup is still "forming" after the executor already
  owns a market_watch plan with quote_inside_zone activation.
  """
  label = "IN ZONE · WAITING FILL" if in_zone else "SETUP FORMING"
  return f"🔎 <b>{escape(str(symbol))} {escape(str(tf))} · {label}</b>"


def _format_math_line(match: StrategyMatch) -> str | None:
  parts: list[str] = []
  fib = getattr(match, "math_fib_ratio", None)
  if fib is not None and math.isfinite(float(fib)):
    parts.append(f"fib {float(fib):g}")
  vel = getattr(match, "math_velocity", None)
  acc = getattr(match, "math_acceleration", None)
  if vel is not None and math.isfinite(float(vel)):
    if acc is not None and math.isfinite(float(acc)):
      parts.append(f"v={float(vel):+.2f} · a={float(acc):+.2f}")
    else:
      parts.append(f"v={float(vel):+.2f}")
  pd = getattr(match, "math_pd", None)
  if pd is not None and math.isfinite(float(pd)):
    parts.append(f"PD {float(pd):.2f}")
  if not parts:
    return None
  return " · ".join(parts)


def _trade_area_targets_line(
  match: StrategyMatch,
  *,
  symbol: str,
  target_prices: tuple[float, ...] | None = None,
) -> str | None:
  """Trade-area Targets line: absolute TP prices when known, else pip ladder."""
  prices: list[float] = []
  for raw in target_prices or ():
    try:
      price = float(raw)
    except (TypeError, ValueError):
      continue
    if math.isfinite(price):
      prices.append(price)

  pips: list[int] = []
  for raw in match.targets_pips or ():
    try:
      value = int(raw)
    except (TypeError, ValueError):
      continue
    if value > 0:
      pips.append(value)

  if prices:
    parts: list[str] = []
    reference = _target_reference_price(match)
    direction = str(match.direction or "").upper()
    for index, price in enumerate(prices):
      label = f"TP{index + 1}"
      price_text = _price_text(price, symbol=symbol)
      if reference is not None:
        offset = _target_pip_offset(
          price,
          reference=reference,
          direction=direction,
          symbol=symbol,
        )
        parts.append(f"{label} {price_text} (+{offset})")
      elif index < len(pips):
        parts.append(f"{label} {price_text} (+{pips[index]})")
      else:
        parts.append(f"{label} {price_text}")
    return f"• <b>Targets:</b> <b>{' · '.join(parts)}</b>"
  if pips:
    ladder = " / ".join(f"+{value}" for value in pips)
    return f"• <b>Targets:</b> <b>{ladder} pips</b>"
  return None


def format_plan_published_root_card(
  match: StrategyMatch,
  *,
  stop_price: float | None = None,
  target_prices: tuple[float, ...] | None = None,
) -> str:
  """Root card after publish with scanner-style trade/context detail.

  Includes bias / structure / trade area / context / stop when available on
  the StrategyMatch. No PLAN PUBLISHED status line — publication is silent on
  the card head. Trade-area Stop is the published plan stop and is not
  rewritten on BE / trailing updates.
  """
  direction = str(match.direction or "").upper()
  direction_icon = "🟢" if direction == "BUY" else "🔴"
  stars = "⭐" * max(1, min(3, int(match.confluence or 1)))
  setup_label = str(match.strategy or "").strip() or "Setup"
  mode = str(match.strategy_mode or "").strip()
  confirmation = str(match.reaction_type or "").strip()
  source_tf = str(
    match.structural_timeframe or match.source_tf or ""
  ).strip().upper()
  htf_bias = str(match.htf_bias or "").strip()
  extra_reasons = [
    reason for reason in (match.reasons or ())
    if reason and not str(reason).lower().startswith("htf bias")
  ][:2]
  in_zone = quote_inside_entry_zone(
    match.current_price, match.entry_low, match.entry_high,
  )
  symbol = str(match.symbol or "").upper() or "XAU"

  lines = [
    forming_card_headline(symbol, str(match.source_tf), in_zone=in_zone),
    (
      "⏳ <b>IN ZONE</b> · waiting market fill"
      if in_zone
      else _PLAN_PUBLISHED_STATUS_SLOT
    ),
    (
      f"{direction_icon} <b>{escape(direction)} · "
      f"{escape(setup_label)}</b> · {stars}"
    ),
  ]
  if mode == "range_scalp":
    lines.append("↔️ <b>Mode:</b> RANGE SCALP · two-sided local range")
  elif mode == "counter_bias":
    lines.append("⚠️ <b>Bias:</b> counter_bias")
  elif mode in {"with_bias", "neutral"}:
    lines.append(f"🧭 <b>Bias:</b> {escape(mode)}")
  elif mode and mode != "with_trend":
    label = (
      "reaction scalp" if mode == "counter_reaction" else "counter swing"
    )
    lines.append(f"⚠️ <b>Mode:</b> Counter-trend · {label}")
  if match.structural_source:
    lines.append(
      f"🧱 <b>Structural source:</b> {escape(str(match.structural_source))}"
    )
  if confirmation:
    lines.append(f"✅ <b>Confirmation:</b> {escape(confirmation)}")
  if source_tf:
    lines.append(f"⏱ <b>Source TF:</b> {escape(source_tf)}")

  math_line = _format_math_line(match)
  if math_line:
    lines.extend(["", "📐 <b>Math</b>", f"• {escape(math_line)}"])

  lines.extend([
    "",
    "📍 <b>Trade area</b>",
    (
      "• <b>Price now:</b> "
      f"<b>{_price_text(match.current_price, symbol=symbol)}</b> "
      "<i>(live)</i>"
    ),
    (
      "• <b>Entry zone:</b> "
      f"<b>{_price_text(match.entry_low, symbol=symbol)}–"
      f"{_price_text(match.entry_high, symbol=symbol)}</b>"
    ),
    (
      "• <b>Key level:</b> "
      f"<b>{_price_text(match.key_level, symbol=symbol)}</b>"
    ),
  ])
  if stop_price is not None and math.isfinite(float(stop_price)):
    lines.append(
      "• <b>Stop:</b> "
      f"<b>{_price_text(float(stop_price), symbol=symbol)}</b>"
    )
  else:
    lines.append("• <b>Stop:</b> <b>SL</b>")

  targets_line = _trade_area_targets_line(
    match, symbol=symbol, target_prices=target_prices,
  )
  if targets_line:
    lines.append(targets_line)

  lines.extend(["", "🧭 <b>Context</b>"])
  if htf_bias:
    lines.append(f"• <b>HTF bias:</b> {escape(htf_bias)}")
  elif mode:
    lines.append(f"• <b>HTF bias:</b> {escape(mode)}")
  lines.extend(f"• {escape(str(reason))}" for reason in extra_reasons)
  lines.append("→ Executor owns mechanical entry and risk enforcement.")
  return "\n".join(lines)


async def published_plan_stop_price(client, match_id: str) -> float | None:
  """Best-effort stop from the just-published TradePlan (if present)."""
  try:
    from app.autotrade.setup_execution_aggregate import v8_plan_id
    from app.autotrade.trade_plan_stream import read_trade_plan

    plan = await read_trade_plan(client, v8_plan_id(match_id))
  except Exception:
    log.exception(
      "plan_published_root_card_stop_lookup_failed setup_id=%s",
      match_id,
    )
    return None
  if plan is None or plan.stop is None:
    return None
  try:
    return float(plan.stop.price)
  except (TypeError, ValueError):
    return None


async def published_plan_target_prices(
  client, match_id: str,
) -> tuple[float, ...]:
  """Best-effort absolute TP prices from the published TradePlan (if present)."""
  try:
    from app.autotrade.setup_execution_aggregate import v8_plan_id
    from app.autotrade.trade_plan_stream import read_trade_plan

    plan = await read_trade_plan(client, v8_plan_id(match_id))
  except Exception:
    log.exception(
      "plan_published_root_card_targets_lookup_failed setup_id=%s",
      match_id,
    )
    return ()
  if plan is None or not plan.targets:
    return ()
  prices: list[float] = []
  for target in plan.targets:
    try:
      price = float(target.price)
    except (TypeError, ValueError):
      continue
    if math.isfinite(price):
      prices.append(price)
  return tuple(prices)


async def ensure_plan_published_root_card(
  client,
  match: StrategyMatch,
  *,
  chat_id: int | None = None,
  send_fn: SendFn | None = None,
  edit_fn: EditFn | None = None,
  delete_fn: DeleteFn | None = None,
) -> int | None:
  """Ensure the PLAN PUBLISHED root card exists after direct publication.

  ZoneWatch cutover suppresses SETUP FORMING until publish, then expects the
  first card to be PLAN PUBLISHED via scanner notify. Direct publish from the
  M1 ZoneWatch loop never calls that notify path, so without this ensure the
  owner can trade a setup with no Telegram root card at all — and later
  take_profit / stop_moved / position_closed replies have nothing to thread to.
  """
  owner_id = (
    chat_id
    if chat_id is not None
    else runtime_config.delivery.telegram.telegram_owner_id
  )
  if not owner_id:
    log.info(
      "plan_published_root_card_skipped setup_id=%s reason=telegram_owner_id_unset",
      match.match_id,
    )
    return None

  status_line = PLAN_PUBLISHED_STATUS_LINE or _PLAN_PUBLISHED_STATUS_SLOT
  from app.bot.client import (
    delete_scanner_message,
    edit_scanner_message_text,
    send_scanner_root_card_with_retry,
  )

  resolved_edit = edit_fn or edit_scanner_message_text
  resolved_send = send_fn or send_scanner_root_card_with_retry
  resolved_delete = delete_fn or delete_scanner_message
  stop_price = await published_plan_stop_price(client, match.match_id)
  target_prices = await published_plan_target_prices(client, match.match_id)

  existing = await load_forming_card(client, match.match_id)
  had_live_card = (
    existing is not None and int(existing.get("message_id") or 0) > 0
  )

  if existing is not None and int(existing.get("message_id") or 0) > 0:
    existing_text = str(existing.get("text") or "")
    if not forming_card_matches_strategy(existing_text, match):
      # Confluence setup_id is shared across detectors — an older Key Level
      # (or wrong-direction) body must not stay as the Trend Pullback root.
      replacement = format_plan_published_root_card(
        match, stop_price=stop_price, target_prices=target_prices or None,
      )
      upper_head = existing_text.splitlines()[0].upper() if existing_text else ""
      if "TERMINAL" in upper_head:
        snapshot = await load_forming_card_status_snapshot(client, match.match_id)
        status = (
          snapshot.status_line
          if snapshot is not None and snapshot.status_line
          else _terminal_status_line("closed")
        )
        replacement = apply_forming_card_status(replacement, status)
      elif "POSITION ACTIVATED" in upper_head:
        replacement = apply_forming_card_status(
          replacement, "✅ <b>POSITION ACTIVATED</b>",
        )
      message_id = await post_or_edit_forming_card(
        client,
        match.match_id,
        replacement,
        chat_id=int(existing.get("chat_id") or owner_id),
        send_fn=resolved_send,
        edit_fn=resolved_edit,
        delete_fn=resolved_delete,
      )
      log.info(
        "plan_published_root_card_body_refreshed setup_id=%s message_id=%s "
        "reason=strategy_mismatch",
        match.match_id,
        message_id,
      )
      return message_id
    # Keep existing head status (no PLAN PUBLISHED advertisement). Still
    # refresh Stop so BE/trail patches have a real Trade-area baseline.
    if stop_price is not None:
      await edit_forming_card_stop(
        client,
        match.match_id,
        stop_price,
        digits=card_price_digits(str(match.symbol)),
        edit_fn=resolved_edit,
      )
    return int(existing["message_id"])

  message_id = await post_or_edit_forming_card(
    client,
    match.match_id,
    format_plan_published_root_card(
      match, stop_price=stop_price, target_prices=target_prices or None,
    ),
    chat_id=int(owner_id),
    send_fn=resolved_send,
    edit_fn=resolved_edit,
    delete_fn=resolved_delete,
  )
  if message_id is None:
    return None
  await save_forming_card_status(
    client,
    match.match_id,
    status_line,
    state="plan_published",
  )
  # Worker may have called edit_forming_card_stop during publish before this
  # card existed; re-apply so BE / trailing SL patches start from a real Stop.
  if stop_price is not None:
    await edit_forming_card_stop(
      client,
      match.match_id,
      stop_price,
      digits=card_price_digits(str(match.symbol)),
      edit_fn=resolved_edit,
    )
  if not had_live_card:
    log.info(
      "plan_published_root_card_created setup_id=%s message_id=%s "
      "reply_anchor=forming_message+telegram_root",
      match.match_id,
      message_id,
    )
  return message_id


def strategy_match_from_trade_plan(plan: Any) -> StrategyMatch:
  """Rebuild a minimal StrategyMatch so root-card formatting can recover."""
  from app.autotrade.strategy_match import STRATEGY_MATCH_VERSION

  analysis = plan.analysis
  structure = plan.source_structure
  entry = plan.entry
  low = float(entry.zone_low if entry.zone_low is not None else structure.low)
  high = float(entry.zone_high if entry.zone_high is not None else structure.high)
  mid = (low + high) / 2.0
  now = int(time.time())
  return StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id=str(plan.setup_id),
    symbol=str(plan.symbol).upper(),
    source_tf=str(analysis.formation_timeframe or "M1").upper(),
    event_ts=str(analysis.confirmation_bar_ts or plan.created_at),
    issued_at=int(plan.created_at or now),
    expires_at=int(plan.expires_at or (now + 600)),
    strategy=str(analysis.strategy),
    strategy_mode=str(analysis.bias or ""),
    direction=str(analysis.direction).upper(),
    key_level=mid,
    entry_low=low,
    entry_high=high,
    current_price=mid,
    confluence=int(analysis.confluence or 1),
    reasons=tuple(analysis.reasons or ()),
    atr=max(0.1, high - low),
    structure_swing=float(structure.invalidation_price),
    targets_pips=tuple(
      int(round(abs(float(target.price) - mid) / 0.1))
      for target in (plan.targets or ())
      if getattr(target, "price", None) is not None
    ) or (30,),
    family=str(analysis.strategy_family or ""),
    structural_source=str(structure.kind or ""),
    structural_zone_id=str(structure.structure_id or ""),
    reaction_type="m5_authoritative",
    confirmation_bar_ts=str(analysis.confirmation_bar_ts or ""),
    touch_bar_ts=str(analysis.formation_bar_ts or ""),
    structural_timeframe=str(structure.timeframe or analysis.formation_timeframe or ""),
    htf_bias=str(analysis.bias or ""),
  )


async def load_strategy_match_for_root_card(
  client: Any,
  setup_id: str,
  *,
  symbol: str = "XAU",
) -> StrategyMatch | None:
  """Load a StrategyMatch for root-card recovery (live matches or TradePlan)."""
  from app.autotrade.multi_match import deserialize_matches, strategy_matches_key
  from app.autotrade.strategy_match import strategy_match_key
  from app.autotrade.trade_plan import TradePlan
  from app.autotrade.trade_plan_stream import plan_key

  setup_id = str(setup_id or "").strip()
  if not setup_id:
    return None
  symbol = str(symbol or "XAU").upper()

  multi_raw = await client.get(strategy_matches_key(symbol))
  for match in deserialize_matches(multi_raw):
    if str(match.match_id) == setup_id:
      return match

  legacy_raw = await client.get(strategy_match_key(symbol))
  legacy = StrategyMatch.from_json(legacy_raw) if legacy_raw else None
  if legacy is not None and str(legacy.match_id) == setup_id:
    return legacy

  plan_id = setup_id if setup_id.startswith("v8:") else f"v8:{setup_id}"
  plan_raw = await client.get(plan_key(plan_id))
  if plan_raw is None and setup_id.startswith("v8:"):
    plan_raw = await client.get(plan_key(setup_id))
  if plan_raw is None:
    return None
  try:
    payload = json.loads(
      plan_raw.decode() if isinstance(plan_raw, bytes) else str(plan_raw)
    )
    plan = TradePlan.from_dict(payload)
  except Exception:
    log.exception(
      "root_card_plan_load_failed setup_id=%s plan_id=%s",
      setup_id,
      plan_id,
    )
    return None
  return strategy_match_from_trade_plan(plan)


def format_event_recovery_root_card(event: dict) -> str:
  """Compact root when publish never posted a card (fill/close still happened)."""
  symbol = escape(str(event.get("symbol") or "XAU").upper())
  tf = escape(str(event.get("tf") or event.get("timeframe") or "M1").upper())
  message = str(event.get("message") or "")
  direction = str(event.get("direction") or "").upper()
  if direction not in {"BUY", "SELL"}:
    upper = message.upper()
    if "SELL" in upper:
      direction = "SELL"
    elif "BUY" in upper:
      direction = "BUY"
  strategy = escape(
    str(event.get("strategy") or event.get("setup") or "Algo").strip() or "Algo"
  )
  # Never paint TERMINAL on a recovered root — close lives on reply cards.
  head = f"✅ <b>POSITION ACTIVATED · {symbol} {tf}</b>"
  icon = "🟢" if direction == "BUY" else "🔴"
  lines = [head]
  if direction:
    lines.append(f"{icon} <b>{direction} · {strategy}</b>")
  if message:
    lines.append(f"• {escape(message)}")
  return "\n".join(lines)


async def ensure_root_card_for_setup_id(
  client: Any,
  setup_id: str,
  *,
  symbol: str = "XAU",
  chat_id: int | None = None,
  event: dict | None = None,
) -> int | None:
  """Create/recover the root when fill delivery finds none.

  Prefer the published StrategyMatch / TradePlan card. If those are already
  gone (fast fill, Asia scalp, Redis TTL), still post a compact activated
  card from the lifecycle event so manage replies have a thread parent.
  """
  match = await load_strategy_match_for_root_card(
    client, setup_id, symbol=symbol,
  )
  if match is not None:
    return await ensure_plan_published_root_card(
      client,
      match,
      chat_id=chat_id,
    )
  if not event:
    log.warning(
      "root_card_recovery_skipped setup_id=%s reason=match_unavailable",
      setup_id,
    )
    return None
  owner_id = (
    chat_id
    if chat_id is not None
    else runtime_config.delivery.telegram.telegram_owner_id
  )
  if not owner_id:
    return None
  from app.bot.client import (
    delete_scanner_message,
    edit_scanner_message_text,
    send_scanner_root_card_with_retry,
  )

  log.info(
    "root_card_recovery_from_event setup_id=%s type=%s",
    setup_id,
    event.get("type"),
  )
  return await post_or_edit_forming_card(
    client,
    setup_id,
    format_event_recovery_root_card(event),
    chat_id=int(owner_id),
    send_fn=send_scanner_root_card_with_retry,
    edit_fn=edit_scanner_message_text,
    delete_fn=delete_scanner_message,
  )


async def schedule_deferred_root_card_ensure(
  client: Any,
  match: StrategyMatch,
  *,
  delay_seconds: float,
) -> None:
  """Retry root-card ensure after Telegram flood / transient create failure."""
  delay = max(1.0, float(delay_seconds))

  async def _run() -> None:
    try:
      await asyncio.sleep(delay)
      message_id = await ensure_plan_published_root_card(client, match)
      log.info(
        "deferred_root_card_ensure setup_id=%s delay_s=%.1f message_id=%s",
        match.match_id,
        delay,
        message_id,
      )
    except Exception:
      log.exception(
        "deferred_root_card_ensure_failed setup_id=%s delay_s=%.1f",
        match.match_id,
        delay,
      )

  asyncio.create_task(_run(), name=f"root-card-ensure:{match.match_id}")
