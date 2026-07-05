"""Once-daily ForexFactory calendar ingestion and local event consumers."""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from app.config import settings
from app.dedup import (
  events_between,
  get_meta,
  set_meta,
  upsert_events,
)
from app.symbols import channels_for
from app.telegram import _send_with_retry

log = logging.getLogger(__name__)

_UTC = ZoneInfo("UTC")
_CACHE_THISWEEK = Path("/data/ff_thisweek.json")
_CACHE_NEXTWEEK = Path("/data/ff_nextweek.json")
_PIPS_PATTERN = re.compile(r"([+-])\s*(\d+)\s*pips?", re.IGNORECASE)


def _configured_values(raw: str, *, lowercase: bool = False) -> set[str]:
  values = {part.strip() for part in raw.split(",") if part.strip()}
  return {value.lower() for value in values} if lowercase else values


def _field(item: dict, *names: str) -> Any:
  folded = {str(key).casefold(): value for key, value in item.items()}
  for name in names:
    if name.casefold() in folded:
      return folded[name.casefold()]
  return None


def _text(value: Any) -> str | None:
  if value is None:
    return None
  value = str(value).strip()
  return value or None


def _event_id(ts_utc: int, currency: str, title: str) -> str:
  raw = f"{ts_utc}\0{currency}\0{title}".encode("utf-8")
  return hashlib.sha256(raw).hexdigest()


def _parse_date(value: Any) -> tuple[int, int] | None:
  raw = _text(value)
  if not raw:
    return None
  date_only = len(raw) == 10
  try:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
  except ValueError:
    return None
  if parsed.tzinfo is None:
    if not date_only:
      return None
    parsed = parsed.replace(tzinfo=_UTC)
  all_day = int(date_only or (
    parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0
  ))
  return int(parsed.astimezone(_UTC).timestamp()), all_day


def _parse_feed(payload: Any, synced_at: int | None = None) -> list[dict]:
  """Normalize valid FF rows; filtering is deliberately a separate step."""
  if isinstance(payload, (str, bytes)):
    try:
      payload = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
      log.warning("ForexFactory feed could not be decoded")
      return []
  if isinstance(payload, dict):
    payload = _field(payload, "events", "data")
  if not isinstance(payload, list):
    log.warning("ForexFactory feed root is not a list")
    return []
  synced_at = synced_at or int(time.time())
  rows = []
  for item in payload:
    if not isinstance(item, dict):
      continue
    title = _text(_field(item, "title", "name", "event"))
    currency = _text(_field(item, "country", "currency"))
    impact = _text(_field(item, "impact"))
    parsed_date = _parse_date(_field(item, "date", "datetime", "time"))
    if not title or not currency or not impact or not parsed_date:
      log.warning("Skipping malformed ForexFactory event: %r", item)
      continue
    ts_utc, midnight = parsed_date
    currency = currency.upper()
    impact = impact.title()
    all_day = int(impact == "Holiday" or midnight)
    rows.append({
      "event_id": _event_id(ts_utc, currency, title),
      "ts_utc": ts_utc,
      "currency": currency,
      "title": title,
      "impact": impact,
      "forecast": _text(_field(item, "forecast")),
      "previous": _text(_field(item, "previous")),
      "actual": _text(_field(item, "actual")),
      "all_day": all_day,
      "source": "ff",
      "synced_at": synced_at,
    })
  return rows


def _valid_feed_root(payload: Any) -> bool:
  if isinstance(payload, list):
    return True
  if isinstance(payload, dict):
    return isinstance(_field(payload, "events", "data"), list)
  return False


def _filter_events(rows: list[dict]) -> list[dict]:
  currencies = {
    value.upper()
    for value in _configured_values(settings.calendar_currencies)
  }
  oil_keywords = _configured_values(settings.oil_keywords, lowercase=True)
  return [
    row for row in rows
    if row["impact"] == "High" and (
      row["currency"] in currencies
      or any(keyword in row["title"].lower() for keyword in oil_keywords)
    )
  ]


async def _fetch_feed(url: str, cache_path: Path) -> Any | None:
  """Fetch and cache one JSON feed, preserving the prior cache on failure."""
  try:
    timeout = aiohttp.ClientTimeout(total=15)
    headers = {"User-Agent": settings.calendar_user_agent}
    async with aiohttp.ClientSession(
      timeout=timeout,
      headers=headers,
    ) as session:
      async with session.get(url) as response:
        body = await response.text()
        content_type = response.headers.get("Content-Type", "").lower()
        looks_html = (
          "html" in content_type
          or body.lstrip().lower().startswith(("<!doctype html", "<html"))
        )
        if (
          response.status != 200
          or "request denied" in body.lower()
          or looks_html
        ):
          log.warning(
            "ForexFactory feed rejected (%s, status %s); keeping cache",
            url,
            response.status,
          )
          return None
        try:
          payload = json.loads(body)
        except json.JSONDecodeError:
          log.warning(
            "ForexFactory feed returned invalid JSON (%s); keeping cache",
            url,
          )
          return None
        if not _valid_feed_root(payload):
          log.warning(
            "ForexFactory feed has an invalid root (%s); keeping cache",
            url,
          )
          return None
        source_rows = (
          payload
          if isinstance(payload, list)
          else _field(payload, "events", "data")
        )
        if source_rows and not _parse_feed(payload):
          log.warning(
            "ForexFactory feed has no valid events (%s); keeping cache",
            url,
          )
          return None
    try:
      cache_path.parent.mkdir(parents=True, exist_ok=True)
      temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
      temporary.write_text(body, encoding="utf-8")
      temporary.replace(cache_path)
    except OSError:
      log.warning("Could not update ForexFactory cache %s", cache_path)
    return payload
  except Exception as exc:
    log.warning("ForexFactory fetch failed (%s): %s; keeping cache", url, exc)
    return None


def _load_cache(cache_path: Path) -> Any | None:
  try:
    return json.loads(cache_path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError) as exc:
    log.warning("No usable ForexFactory cache at %s: %s", cache_path, exc)
    return None


def _local_day_window(now: datetime) -> tuple[int, int]:
  start = now.replace(hour=0, minute=0, second=0, microsecond=0)
  return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


def _is_oil_event(event: dict) -> bool:
  keywords = _configured_values(settings.oil_keywords, lowercase=True)
  return any(keyword in event["title"].lower() for keyword in keywords)


def _bot_safe(value: Any) -> str:
  return _PIPS_PATTERN.sub(r"\1\2p", str(value))


def _format_brief(events: list[dict], tz: ZoneInfo) -> str | None:
  if not events:
    return None
  lines = ["🗓 Today · high-impact (USD / gold / oil)"]
  for event in events:
    local = datetime.fromtimestamp(event["ts_utc"], _UTC).astimezone(tz)
    when = "All day" if event["all_day"] else local.strftime("%H:%M")
    label = "Oil" if _is_oil_event(event) else event["currency"]
    values = []
    if event.get("forecast"):
      values.append(f"f: {_bot_safe(event['forecast'])}")
    if event.get("previous"):
      values.append(f"p: {_bot_safe(event['previous'])}")
    suffix = f"   ({' · '.join(values)})" if values else ""
    lines.append(
      f"{when}  {escape(label)} · "
      f"{escape(_bot_safe(event['title']))}{escape(suffix)}"
    )
  return "\n".join(lines)


async def _post_brief(now: datetime) -> None:
  day = now.date().isoformat()
  if await get_meta("last_brief_date") == day:
    return
  start, end = _local_day_window(now)
  text = _format_brief(
    await events_between(start, end),
    ZoneInfo(settings.seq_reset_tz),
  )
  if text is not None:
    for target in channels_for("XAU", "both"):
      await _send_with_retry(
        text,
        chat_id=int(target["channel_id"]),
      )
  await set_meta("last_brief_date", day)


async def _sync_day(now: datetime | None = None) -> None:
  tz = ZoneInfo(settings.seq_reset_tz)
  now = now.astimezone(tz) if now else datetime.now(tz)
  day = now.date().isoformat()
  if await get_meta("last_sync_date") != day:
    payloads = []
    feeds = [
      (settings.calendar_feed_thisweek, _CACHE_THISWEEK),
    ]
    if now.weekday() >= 3:
      feeds.append((settings.calendar_feed_nextweek, _CACHE_NEXTWEEK))
    can_fetch = await get_meta("last_fetch_date") != day
    if can_fetch:
      # Reserve before I/O so a crash/restart cannot over-fetch today.
      await set_meta("last_fetch_date", day)
    for url, cache_path in feeds:
      payload = (
        await _fetch_feed(url, cache_path)
        if can_fetch
        else None
      )
      payloads.append(
        payload if payload is not None else _load_cache(cache_path)
      )
    rows = []
    synced_at = int(time.time())
    for payload in payloads:
      if payload is not None:
        rows.extend(_parse_feed(payload, synced_at))
    await upsert_events(_filter_events(rows))
    await set_meta("last_sync_date", day)
  await _post_brief(now)


async def calendar_sync_loop() -> None:
  """Run at most one calendar sync per local day at the configured hour."""
  if not settings.calendar_enabled:
    log.info("Economic calendar disabled")
    return
  while True:
    try:
      tz = ZoneInfo(settings.seq_reset_tz)
      now = datetime.now(tz)
      target = now.replace(
        hour=settings.news_brief_hour,
        minute=0,
        second=0,
        microsecond=0,
      )
      if now >= target:
        await _sync_day(now)
        target += timedelta(days=1)
      delay = max(1.0, (target - datetime.now(tz)).total_seconds())
      await asyncio.sleep(delay)
    except asyncio.CancelledError:
      raise
    except Exception:
      log.exception("Economic calendar daily sync failed")
      await asyncio.sleep(3600)
