"""Thin Telegram facade: exports legacy names and wires routers in order."""

import time

from app import broadcast as _broadcast
from app import parsing as _parsing
from app.handlers import callbacks as _callbacks
from app.handlers import channel as _channel
from app.handlers import dm as _dm
from app.handlers import fallback as _fallback
from app.config import settings
from app.dedup import (
  event_in_window,
  get_all_signals,
  get_manual_signal,
  get_open_signals,
  get_pips_records,
  get_pips_summary,
  get_signal_by_post,
  get_signal_cluster,
  store_manual_signal,
  store_pips,
)
from app.keyboards import build_close_kb, build_tp_close_kb, _partial_kb
from app.parsing import (
  _ACTIVE_RE,
  _CANCEL_RE,
  _CLOSE_RE,
  _CLOSEBE_RE,
  _MANUAL_RE,
  _NOTE_RE,
  _PIPS_RE,
  _REOPEN_RE,
  _SCALP_SUFFIX_RE,
  _SETUP_SUFFIX_RE,
  _SL_RE,
  _TAG_RE,
  _TP_RE,
  _command_args,
  _expand_entry_endpoint,
  _expand_tp,
  _is_owner,
  _is_owner_cb,
  _num,
  _parse_close,
  _parse_manual,
  _period_range,
  _seq_token,
  _stats_range,
  _take_symbol,
)
from app.reports import build_stats, format_review, format_stats
from app.broadcast import broadcast_entry, render_entry
from app.pips_format import wing_icons
from app.symbols import (
  SYMBOLS,
  channel_for_symbol,
  symbol_for_channel,
  tier_for_channel,
)
from app.tg_core import (
  OWNER_COMMANDS,
  bot,
  dp,
  send_with_retry,
  setup_commands,
)
from app.trade_ops import (
  do_active,
  do_cancel,
  do_close,
  do_delete,
  do_note,
  do_reopen,
  do_sl,
  do_tag,
  do_tp,
  do_uncclose,
  post_result,
  render_result,
)

_send_with_retry = send_with_retry
_ORIGINAL_DELETE_COMMAND = _channel._delete_command
_ORIGINAL_HANDLE_PIPS = _fallback._handle_pips
_ORIGINAL_TODAY_STR = _parsing._today_str

# Include order is load-bearing: command routers before generic catch-alls.
dp.include_router(_callbacks.router)
dp.include_router(_dm.router)
dp.include_router(_channel.router)
dp.include_router(_fallback.router)


def _mirror_router_handlers_for_legacy_observers() -> None:
  """Keep old tests/inspection paths that read dp.observers directly working."""
  for router in (_callbacks.router, _dm.router, _channel.router, _fallback.router):
    for update_name, observer in router.observers.items():
      if observer.handlers:
        dp.observers[update_name].handlers.extend(observer.handlers)


_mirror_router_handlers_for_legacy_observers()


def _sync_legacy_patches() -> None:
  """Mirror monkeypatched facade globals into split modules for old tests."""
  _parsing.get_open_signals = get_open_signals
  _parsing.get_all_signals = get_all_signals
  _parsing.get_signal_by_post = get_signal_by_post
  _parsing.channel_for_symbol = channel_for_symbol

  _broadcast.send_with_retry = _send_with_retry

  _callbacks.get_manual_signal = get_manual_signal
  _callbacks.symbol_for_channel = symbol_for_channel
  _callbacks.do_close = do_close
  _callbacks.render_result = render_result
  _callbacks._is_owner_cb = _is_owner_cb
  _callbacks.build_tp_close_kb = build_tp_close_kb
  _callbacks._partial_kb = _partial_kb

  _dm.get_open_signals = get_open_signals
  _dm.get_all_signals = get_all_signals
  _dm.get_manual_signal = get_manual_signal
  _dm.get_pips_records = get_pips_records
  _dm.get_pips_summary = get_pips_summary
  _dm.get_signal_cluster = get_signal_cluster
  _dm.channel_for_symbol = channel_for_symbol
  _dm.do_active = do_active
  _dm.do_cancel = do_cancel
  _dm.do_close = do_close
  _dm.do_delete = do_delete
  _dm.do_note = do_note
  _dm.do_reopen = do_reopen
  _dm.do_sl = do_sl
  _dm.do_tag = do_tag
  _dm.do_tp = do_tp
  _dm.do_uncclose = do_uncclose
  _dm.post_result = post_result
  _dm.render_result = render_result
  _dm._is_owner = _is_owner
  _dm._resolve_sid = _resolve_sid
  _dm._resolve_any_sid = _resolve_any_sid
  _dm.send_with_retry = _send_with_retry

  _channel.symbol_for_channel = symbol_for_channel
  _channel.tier_for_channel = tier_for_channel
  _channel.do_active = do_active
  _channel.do_cancel = do_cancel
  _channel.do_close = do_close
  _channel.do_note = do_note
  _channel.do_reopen = do_reopen
  _channel.do_sl = do_sl
  _channel.do_tag = do_tag
  _channel.post_result = post_result
  _channel.render_result = render_result
  _channel._resolve_sid = _resolve_sid
  _channel._resolve_any_sid = _resolve_any_sid
  _channel._delete_command = (
    _delete_command
    if _delete_command is not _legacy_delete_command
    else _ORIGINAL_DELETE_COMMAND
  )
  _channel.send_with_retry = _send_with_retry

  _fallback.event_in_window = event_in_window
  _fallback.store_manual_signal = store_manual_signal
  _fallback.get_manual_signal = get_manual_signal
  _fallback.store_pips = store_pips
  _fallback.broadcast_entry = broadcast_entry
  _fallback.tier_for_channel = tier_for_channel
  _fallback._is_owner = _is_owner
  _fallback._parse_manual = _parse_manual
  _fallback._handle_pips = (
    _handle_pips
    if _handle_pips is not _legacy_handle_pips
    else _ORIGINAL_HANDLE_PIPS
  )


async def _resolve_sid(
  explicit_seq: int | None,
  reply_to_id: int | None,
  symbol: str = "XAU",
) -> int | None:
  _parsing.get_open_signals = get_open_signals
  _parsing.get_signal_by_post = get_signal_by_post
  _parsing.channel_for_symbol = channel_for_symbol
  _parsing._today_str = (
    _today_str
    if _today_str is not _legacy_today_str
    else _ORIGINAL_TODAY_STR
  )
  return await _parsing._resolve_sid(explicit_seq, reply_to_id, symbol)


async def _resolve_any_sid(
  explicit_seq: int | None,
  reply_to_id: int | None,
  symbol: str = "XAU",
) -> int | None:
  _parsing.get_all_signals = get_all_signals
  _parsing.get_signal_by_post = get_signal_by_post
  _parsing.channel_for_symbol = channel_for_symbol
  _parsing._today_str = (
    _today_str
    if _today_str is not _legacy_today_str
    else _ORIGINAL_TODAY_STR
  )
  return await _parsing._resolve_any_sid(explicit_seq, reply_to_id, symbol)


def _legacy_today_str() -> str:
  return _ORIGINAL_TODAY_STR()


_today_str = _legacy_today_str


def _format_manual_signal(
  sig: dict,
  daily_seq: int,
  symbol: str = "XAU",
) -> str:
  return _dm._format_manual_signal(sig, daily_seq, symbol)


async def _book_leg(
  sid: int,
  pips: int,
  frac: float | None,
  chat_id: str | int,
) -> tuple[dict, str] | None:
  _sync_legacy_patches()
  return await _callbacks._book_leg(sid, pips, frac, chat_id)


async def _reopen_signal(
  source_id: int,
  entry_a: float | None,
  entry_b: float | None,
) -> tuple[dict, str] | None:
  _sync_legacy_patches()
  return await _dm._reopen_signal(source_id, entry_a, entry_b)


async def _move_stop(
  sid: int,
  target: str,
) -> tuple[dict, str] | None:
  _sync_legacy_patches()
  return await _dm._move_stop(sid, target)


def _event_guard_timing(ts_utc: int, now: int) -> str:
  return _fallback._event_guard_timing(ts_utc, now)


async def _legacy_handle_pips(msg, text: str, has_photo: bool) -> None:
  return await _ORIGINAL_HANDLE_PIPS(msg, text, has_photo)


_handle_pips = _legacy_handle_pips


async def _legacy_delete_command(msg) -> None:
  return await _ORIGINAL_DELETE_COMMAND(msg)


_delete_command = _legacy_delete_command


def _channel_symbol(msg) -> str | None:
  return _channel._channel_symbol(msg)


async def handle_close_menu(cb) -> None:
  _sync_legacy_patches()
  return await _callbacks.handle_close_menu(cb)


async def handle_close_cancel(cb) -> None:
  _sync_legacy_patches()
  return await _callbacks.handle_close_cancel(cb)


async def handle_close_book(cb) -> None:
  _sync_legacy_patches()
  return await _callbacks.handle_close_book(cb)


async def handle_trade_pips(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_pips(msg)


async def handle_help(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_help(msg)


async def handle_trade_open(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_open(msg)


async def handle_trade_active(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_active(msg)


async def handle_trade_close(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_close(msg)


async def handle_trade_tp(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_tp(msg)


async def handle_trade_uncclose(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_uncclose(msg)


async def handle_trade_cancel(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_cancel(msg)


async def handle_trade_delete(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_delete(msg)


async def handle_trade_sl(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_sl(msg)


async def handle_trade_reopen(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_reopen(msg)


async def handle_trade_tag(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_tag(msg)


async def handle_trade_note(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_note(msg)


async def handle_trade_review(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_review(msg)


async def handle_trade_stats(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_trade_stats(msg)


async def handle_chart_photo(msg) -> None:
  _sync_legacy_patches()
  return await _dm.handle_chart_photo(msg)


async def handle_channel_active(msg) -> None:
  _sync_legacy_patches()
  return await _channel.handle_channel_active(msg)


async def handle_channel_close(msg) -> None:
  _sync_legacy_patches()
  return await _channel.handle_channel_close(msg)


async def handle_channel_cancel(msg) -> None:
  _sync_legacy_patches()
  return await _channel.handle_channel_cancel(msg)


async def handle_channel_sl(msg) -> None:
  _sync_legacy_patches()
  return await _channel.handle_channel_sl(msg)


async def handle_channel_reopen(msg) -> None:
  _sync_legacy_patches()
  return await _channel.handle_channel_reopen(msg)


async def handle_channel_tag(msg) -> None:
  _sync_legacy_patches()
  return await _channel.handle_channel_tag(msg)


async def handle_channel_note(msg) -> None:
  _sync_legacy_patches()
  return await _channel.handle_channel_note(msg)


async def handle_private_signal(msg) -> None:
  _sync_legacy_patches()
  return await _fallback.handle_private_signal(msg)


async def handle_profit_screenshot(msg) -> None:
  _sync_legacy_patches()
  return await _fallback.handle_profit_screenshot(msg)


async def handle_profit_text(msg) -> None:
  _sync_legacy_patches()
  return await _fallback.handle_profit_text(msg)
