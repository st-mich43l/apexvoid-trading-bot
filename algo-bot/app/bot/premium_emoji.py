"""Telegram Premium (custom) emoji for the root card, applied only where the card leaves for Telegram.

The card text the bot builds, stores and later edits stays plain: the edit logic finds the Entry, SL and TP rows and the direction line by their
ordinary emoji. This converts those emoji into ``<tg-emoji emoji-id="...">`` tags on the way out, so nothing that reads the card back changes.

Telegram renders a custom emoji only for a bot that owns an additional username bought on Fragment, and only for viewers with Premium; every other
viewer sees the plain emoji kept inside the tag. Off by default (``telegram.presentation.premium_emoji.enabled``). A slot with no id stays plain.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

# slot -> the plain emoji the root card prints for it
SLOT_EMOJI: dict[str, str] = {
  "brand": "🤖",
  "forming": "🔎",
  "in_zone": "⏳",
  "activated": "✅",
  "buy": "📈",
  "sell": "📉",
  "entry": "⚡",
  "stop": "🛡",
  "target": "💰",
  "star": "⭐",
}
_VARIATION_SELECTOR = "️"
_ID = re.compile(r"[0-9]{5,32}")
_EXISTING = re.compile(r"(<tg-emoji\b[^>]*>.*?</tg-emoji>)", re.DOTALL)


def clean_ids(ids: Mapping[str, Any] | None) -> dict[str, str]:
  """Slot -> custom emoji id, keeping only known slots with a well-formed numeric id (YAML may hand over an int)."""
  out: dict[str, str] = {}
  for slot in SLOT_EMOJI:
    raw = (ids or {}).get(slot) if hasattr(ids, "get") else None
    text = "" if raw is None else str(raw).strip()
    if _ID.fullmatch(text):
      out[slot] = text
  return out


def apply_premium_emoji(html: str, ids: Mapping[str, Any] | None) -> str:
  """Wrap every configured emoji of ``html`` in a custom-emoji tag whose content is the plain emoji. Text already inside a tag is left alone."""
  chosen = clean_ids(ids)
  if not chosen or not html:
    return html
  by_char = {SLOT_EMOJI[slot]: emoji_id for slot, emoji_id in chosen.items()}
  pattern = re.compile("(" + "|".join(re.escape(c) for c in by_char) + ")" + _VARIATION_SELECTOR + "?")

  def wrap(match: re.Match) -> str:
    return f'<tg-emoji emoji-id="{by_char[match.group(1)]}">{match.group(0)}</tg-emoji>'

  parts = _EXISTING.split(html)
  # even parts are text outside an existing tag, odd parts are existing tags
  return "".join(part if i % 2 else pattern.sub(wrap, part) for i, part in enumerate(parts))


def configured_ids(telegram_config: Any) -> dict[str, str]:
  """The ids to use now: empty unless premium emoji are switched on in ``telegram.presentation.premium_emoji``."""
  try:
    node = telegram_config.presentation.get("premium_emoji")
  except AttributeError:
    return {}
  if node is None or not bool(node.get("enabled", False)):
    return {}
  return clean_ids(node.get("ids"))
