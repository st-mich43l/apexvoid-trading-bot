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

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest

from app.autotrade.setup_lifecycle import TERMINAL_STATES, load_setup
from app.core.config import settings

log = logging.getLogger(__name__)

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
    if string.find(current, 'PLAN PUBLISHED', 1, true) then
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
  if "EXECUTOR ARMED" in upper:
    return "executor_armed"
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


def forming_message_key(setup_id: str) -> str:
  return f"auto_trade:forming_message:{setup_id}"


def forming_status_key(setup_id: str) -> str:
  return f"auto_trade:forming_status:{setup_id}"


def apply_forming_card_status(text: str, status_line: str) -> str:
  lines = text.splitlines()
  if len(lines) < 2 or not status_line:
    return text
  lines[1] = status_line
  return "\n".join(lines)


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
  effective_ttl = ttl or max(86400, settings.auto_trade_candidate_ttl)
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
      return {"chat_id": settings.telegram_owner_id, "message_id": int(text)}
    except (TypeError, ValueError):
      return None
  message_id = data.get("message_id")
  if message_id is None:
    return None
  try:
    return {
      "chat_id": data.get("chat_id") or settings.telegram_owner_id,
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
  effective_ttl = ttl or max(86400, settings.auto_trade_candidate_ttl)
  await client.set(
    forming_message_key(setup_id),
    json.dumps(
      {"chat_id": chat_id, "message_id": message_id, "text": text},
      separators=(",", ":"),
    ),
    ex=effective_ttl,
  )


async def clear_forming_card(client, setup_id: str) -> None:
  await client.delete(
    forming_message_key(setup_id),
    forming_status_key(setup_id),
  )


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
  ttl: int | None = None,
) -> int | None:
  """Post the forming card once per setup_id; every later call edits it.

  Returns the card's message_id, or None if the setup is already terminal
  (a rejected/invalidated/expired setup is never re-carded, checked here so
  every caller gets this guard for free) or Telegram would not accept the
  edit and a fresh send also failed.
  """
  if await is_setup_terminal(client, setup_id):
    return None
  current_status = await load_forming_card_status(client, setup_id)
  if current_status is not None:
    text = apply_forming_card_status(text, current_status)
  existing = await load_forming_card(client, setup_id)
  if existing is not None:
    if existing.get("text") == text:
      return existing["message_id"]
    try:
      await edit_fn(existing["chat_id"], existing["message_id"], text)
      await save_forming_card(
        client,
        setup_id,
        chat_id=existing["chat_id"],
        message_id=existing["message_id"],
        text=text,
        ttl=ttl,
      )
      return existing["message_id"]
    except TelegramBadRequest as exc:
      if "message is not modified" in str(exc).casefold():
        await save_forming_card(
          client,
          setup_id,
          chat_id=existing["chat_id"],
          message_id=existing["message_id"],
          text=text,
          ttl=ttl,
        )
        return existing["message_id"]
      log.info(
        "forming card edit failed setup_id=%s, sending a fresh one instead",
        setup_id,
      )
  sent = await send_fn(text, chat_id=chat_id)
  message_id = int(sent.message_id)
  await save_forming_card(
    client,
    setup_id,
    chat_id=chat_id,
    message_id=message_id,
    text=text,
    ttl=ttl,
  )
  return message_id


async def edit_forming_card_status(
  client,
  setup_id: str,
  status_line: str,
  *,
  state: str | None = None,
  edit_fn: EditFn,
) -> bool:
  """Replace only the lifecycle line on the setup's existing card."""
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
  if card is None or not card.get("text"):
    return False
  text = apply_forming_card_status(str(card["text"]), status_line)
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
  return True


def _terminal_card_text(reason_code: str) -> str:
  return "🤖 <b>ApexVoid Algo</b>\n· <i>setup closed</i>"


async def kill_setup_card(
  client,
  setup_id: str,
  *,
  reason_code: str,
  delete_fn: DeleteFn,
  edit_fn: EditFn,
) -> None:
  """Delete the forming card on reject/invalidate/expire - post nothing.

  Falls back to editing the card to a neutral, non-actionable terminal state
  if the delete itself fails (eg. Telegram's deletion window has passed),
  rather than leaving a stale actionable-looking card in the chat.
  """
  if not settings.delivery_delete_on_terminal:
    await clear_forming_card(client, setup_id)
    return
  card = await load_forming_card(client, setup_id)
  if card is None:
    await clear_forming_card(client, setup_id)
    return
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
      await edit_fn(
        card["chat_id"], card["message_id"], _terminal_card_text(reason_code),
      )
    except TelegramBadRequest:
      log.exception(
        "forming card terminal edit also failed setup_id=%s", setup_id,
      )
  await clear_forming_card(client, setup_id)
