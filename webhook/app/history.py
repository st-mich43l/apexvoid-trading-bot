import logging
import re
from datetime import datetime, timezone
from pyrogram import Client
from app.config import settings

log = logging.getLogger(__name__)

_PIPS_RE = re.compile(r'([+-])\s*(\d+)\s*pips?', re.IGNORECASE)

pyro: Client | None = None


async def start_pyro() -> None:
  global pyro
  if not (settings.telegram_api_id and settings.telegram_api_hash):
    log.info("Pyrogram disabled: TELEGRAM_API_ID/TELEGRAM_API_HASH not set")
    return
  pyro = Client(
    name="/data/pyro_session",
    api_id=settings.telegram_api_id,
    api_hash=settings.telegram_api_hash,
  )
  await pyro.start()
  log.info("Pyrogram client started")


async def stop_pyro() -> None:
  global pyro
  if pyro:
    await pyro.stop()
    pyro = None
    log.info("Pyrogram client stopped")


async def fetch_channel_pips(start_ts: int, end_ts: int) -> dict:
  if pyro is None:
    raise RuntimeError("Channel history unavailable (Pyrogram not configured)")

  wins = losses = win_pips = loss_pips = 0
  offset_date = datetime.fromtimestamp(end_ts, tz=timezone.utc)

  async for msg in pyro.get_chat_history(
    int(settings.telegram_chat_id), offset_date=offset_date
  ):
    msg_ts = int(msg.date.timestamp())
    if msg_ts < start_ts:
      break
    text = msg.text or msg.caption or ""
    m = _PIPS_RE.search(text)
    if not m:
      continue
    sign, pips = m.group(1), int(m.group(2))
    if sign == "+":
      wins += 1
      win_pips += pips
    else:
      losses += 1
      loss_pips += pips

  return {
    "wins": wins,
    "losses": losses,
    "win_pips": win_pips,
    "loss_pips": loss_pips,
    "net": win_pips - loss_pips,
    "total": wins + losses,
  }
