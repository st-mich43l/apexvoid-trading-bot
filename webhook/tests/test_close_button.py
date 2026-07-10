import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")

from app import telegram

OWNER = 424242


def _cb(data: str, uid: int = OWNER, html: str = "🎯 TP HIT | #2"):
  msg = SimpleNamespace(
    chat=SimpleNamespace(id=-100123),
    html_text=html,
    text=html,
    edit_text=AsyncMock(),
    edit_reply_markup=AsyncMock(),
  )
  return SimpleNamespace(
    data=data,
    from_user=SimpleNamespace(id=uid),
    message=msg,
    answer=AsyncMock(),
  )


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
  monkeypatch.setattr(telegram.settings, "telegram_owner_id", OWNER)
  monkeypatch.setattr(telegram, "symbol_for_channel", lambda _cid: "XAU")


def _codes(kb) -> list[str]:
  return [btn.callback_data for row in kb.inline_keyboard for btn in row]


@pytest.mark.asyncio
async def test_non_owner_is_rejected(monkeypatch):
  do_close = AsyncMock()
  monkeypatch.setattr(telegram, "do_close", do_close)
  cb = _cb("c1:3:1:90:100", uid=999)

  await telegram.handle_close_book(cb)

  do_close.assert_not_awaited()
  cb.answer.assert_awaited_once()
  assert cb.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_menu_shows_only_valid_fractions(monkeypatch):
  # 50% already booked -> remaining 50% -> only 25% + Full are offered.
  monkeypatch.setattr(
    telegram,
    "get_manual_signal",
    AsyncMock(return_value={"status": "open", "legs": [{"frac": 0.5}]}),
  )
  cb = _cb("c0:3:1:90")

  await telegram.handle_close_menu(cb)

  kb = cb.message.edit_reply_markup.await_args.kwargs["reply_markup"]
  codes = _codes(kb)
  assert "c1:3:1:90:100" in codes          # Full
  assert "c1:3:1:90:25" in codes           # 25% < 50% remaining
  assert not any(
    c.startswith("c1:") and c.rsplit(":", 1)[-1] in {"50", "75", "90"}
    for c in codes
  )


@pytest.mark.asyncio
async def test_menu_offers_90_percent_for_full_open_signal(monkeypatch):
  monkeypatch.setattr(
    telegram,
    "get_manual_signal",
    AsyncMock(return_value={"status": "open", "legs": []}),
  )
  cb = _cb("c0:3:1:90")

  await telegram.handle_close_menu(cb)

  kb = cb.message.edit_reply_markup.await_args.kwargs["reply_markup"]
  codes = _codes(kb)
  assert "c1:3:1:90:90" in codes


@pytest.mark.asyncio
async def test_menu_includes_cancel_that_restores_close(monkeypatch):
  monkeypatch.setattr(
    telegram,
    "get_manual_signal",
    AsyncMock(return_value={"status": "open", "legs": []}),
  )
  # Open the submenu -> it must offer a Cancel back-out.
  menu = _cb("c0:3:1:90")
  await telegram.handle_close_menu(menu)
  submenu = menu.message.edit_reply_markup.await_args.kwargs["reply_markup"]
  assert "cx:3:1:90" in _codes(submenu)

  # Pressing Cancel restores the single Close button and closes nothing.
  do_close = AsyncMock()
  monkeypatch.setattr(telegram, "do_close", do_close)
  cancel = _cb("cx:3:1:90")
  await telegram.handle_close_cancel(cancel)

  do_close.assert_not_awaited()
  kb = cancel.message.edit_reply_markup.await_args.kwargs["reply_markup"]
  assert _codes(kb) == ["c0:3:1:90"]


@pytest.mark.asyncio
async def test_menu_on_closed_signal_removes_buttons(monkeypatch):
  monkeypatch.setattr(
    telegram, "get_manual_signal", AsyncMock(return_value=None)
  )
  cb = _cb("c0:3:1:90")

  await telegram.handle_close_menu(cb)

  cb.answer.assert_awaited_once()
  assert cb.message.edit_reply_markup.await_args.kwargs["reply_markup"] is None


@pytest.mark.asyncio
async def test_full_close_books_and_finalizes(monkeypatch):
  do_close = AsyncMock(return_value={
    "ok": True, "row": {"closed": True, "net": 90},
  })
  monkeypatch.setattr(telegram, "do_close", do_close)
  cb = _cb("c1:3:1:90:100")

  await telegram.handle_close_book(cb)

  args = do_close.await_args.args[0]
  assert args["sid"] == 3 and args["pips"] == 90 and args["frac"] is None
  text, kwargs = cb.message.edit_text.await_args.args[0], cb.message.edit_text.await_args.kwargs
  assert "Closed" in text and "+90 pips" in text
  assert kwargs["reply_markup"] is None


@pytest.mark.asyncio
async def test_partial_close_keeps_close_button(monkeypatch):
  do_close = AsyncMock(return_value={
    "ok": True, "row": {"closed": False, "remaining": 0.5},
  })
  monkeypatch.setattr(telegram, "do_close", do_close)
  cb = _cb("c1:3:1:90:50")

  await telegram.handle_close_book(cb)

  assert do_close.await_args.args[0]["frac"] == 0.5
  text = cb.message.edit_text.await_args.args[0]
  assert "50%" in text and "remaining <b>50%</b>" in text
  kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
  assert _codes(kb) == ["c0:3:1:90"]


@pytest.mark.asyncio
async def test_manual_tp_attaches_close_button(monkeypatch):
  from app import trade_ops
  captured = {}

  async def _fanout(sig, render_fn, sticker=None, markup_fn=None):
    captured["markup_fn"] = markup_fn
    return []

  monkeypatch.setattr(trade_ops, "fanout_update", _fanout)
  monkeypatch.setattr(
    trade_ops, "get_manual_signal", AsyncMock(return_value={"id": 3})
  )
  result = {
    "action": "tp", "ok": True, "sid": 3, "seq": 2,
    "tp_number": 1, "pips": 90,
  }

  await trade_ops.post_result(result, "XAU")

  markup_fn = captured["markup_fn"]
  assert markup_fn("public") is None
  assert markup_fn("vip").inline_keyboard[0][0].callback_data == "c0:3:1:90"


@pytest.mark.asyncio
async def test_book_on_stale_signal_answers_alert(monkeypatch):
  monkeypatch.setattr(
    telegram, "do_close", AsyncMock(return_value={"ok": False})
  )
  cb = _cb("c1:3:1:90:100")

  await telegram.handle_close_book(cb)

  cb.answer.assert_awaited_once()
  assert cb.answer.await_args.kwargs.get("show_alert") is True
  assert cb.message.edit_reply_markup.await_args.kwargs["reply_markup"] is None
