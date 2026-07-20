"""Signal delivery chokepoint for VIP/public channel fan-out."""

import logging
from html import escape
from typing import Callable

from app.dedup import (
  get_signal_posts,
  insert_signal_post,
)
from app.pips_format import rr_entry
from app.symbols import SYMBOLS, channels_for
from app.tg_core import delete_message, send_sticker, send_with_retry

log = logging.getLogger(__name__)


def _price(value: float, symbol: str) -> str:
  digits = int(SYMBOLS[symbol]["digits"])
  return f"{value:,.{digits}f}".rstrip("0").rstrip(".")


def _rr(tp: float, entry: float, risk: float) -> str:
  return f"{abs(tp - entry) / risk:.1f}R" if risk > 0 else "-"


def render_entry(sig: dict, tier: str) -> str:
  symbol = sig["symbol"]
  action = sig["action"]
  entry_end = sig.get("entry_end")
  if entry_end is None:
    entry_end = sig["entry"]
  entry_reference = rr_entry(sig)
  risk = abs(entry_reference - sig["sl"])
  seq = f"  #{sig['daily_seq']}" if tier == "vip" else ""
  action_icon = "📈" if action == "BUY" else "📉"
  lines = [
    (
      f"📍 {action_icon} <b>{escape(symbol)} "
      f"{escape(action)}{seq}</b>  🔔"
    ),
    "",
    (
      f"⚡️ Entry Zone:  <b>{_price(sig['entry'], symbol)} - "
      f"{_price(entry_end, symbol)}</b>"
    ),
    (
      f"🛡 SL:     <b>{_price(sig['sl'], symbol)}</b>  ·  "
      f"risk <b>{_price(risk, symbol)}</b>"
    ),
  ]
  for index, tp in enumerate(sig.get("tps") or []):
    lines.append(
      f"💰 TP{index + 1}:   <b>{_price(tp, symbol)}</b>  ·  "
      f"<b>{_rr(tp, entry_reference, risk)}</b>"
    )
  if sig.get("guard_text"):
    lines.extend(["", sig["guard_text"]])
  return "\n".join(lines)


async def _send_message(
  text: str,
  channel_id: int,
  reply_to: int | None = None,
  reply_markup=None,
):
  return await send_with_retry(
    text,
    reply_to=reply_to,
    chat_id=channel_id,
    reply_markup=reply_markup,
  )


async def _send_sticker(
  sticker: str,
  channel_id: int,
  reply_to: int | None = None,
):
  return await send_sticker(sticker, channel_id, reply_to)


async def delete_posts(posts: list[dict]) -> None:
  """Remove already-delivered channel messages for a hard-deleted signal.

  Best-effort: a post may already be gone or older than Telegram's 48h delete
  window, so per-message failures are swallowed rather than aborting the rest.
  """
  for post in posts:
    try:
      await delete_message(post["channel_id"], post["message_id"])
    except Exception:
      log.warning(
        "could not delete post %s/%s",
        post.get("channel_id"), post.get("message_id"),
      )


async def broadcast_entry(
  sig: dict,
  render_fn: Callable[[str], str] | None = None,
  sticker: str | None = None,
) -> list[dict]:
  """Post a new signal to its visibility targets and persist each post."""
  delivered = {
    int(post["channel_id"])
    for post in await get_signal_posts(sig["id"])
  }
  posts = []
  for target in channels_for(
    sig["symbol"],
    sig.get("visibility", "both"),
  ):
    channel_id = int(target["channel_id"])
    if channel_id in delivered:
      continue
    text = (
      render_fn(target["tier"])
      if render_fn
      else render_entry(sig, target["tier"])
    )
    sent = await _send_message(text, channel_id)
    await insert_signal_post(
      sig["id"],
      channel_id,
      sent.message_id,
      target["tier"],
    )
    posts.append({
      "signal_id": sig["id"],
      "channel_id": channel_id,
      "message_id": sent.message_id,
      "tier": target["tier"],
    })
    if sticker:
      await _send_sticker(sticker, channel_id, sent.message_id)
  return posts


async def fanout_update(
  sig: dict,
  render_fn: Callable[[str], str | None],
  sticker: str | None = None,
  markup_fn: Callable[[str], object] | None = None,
) -> list:
  """Reply only to persisted entry posts; never recompute visibility.

  ``markup_fn(tier)`` may return an inline keyboard to attach per tier (e.g. an
  owner-only action button on the VIP post but nothing on the public one).
  """
  sent_messages = []
  for post in await get_signal_posts(sig["id"]):
    text = render_fn(post["tier"])
    if text is None:
      continue
    sent = await _send_message(
      text,
      int(post["channel_id"]),
      int(post["message_id"]),
      reply_markup=markup_fn(post["tier"]) if markup_fn else None,
    )
    sent_messages.append(sent)
    if sticker:
      await _send_sticker(
        sticker,
        int(post["channel_id"]),
        sent.message_id,
      )
  return sent_messages
