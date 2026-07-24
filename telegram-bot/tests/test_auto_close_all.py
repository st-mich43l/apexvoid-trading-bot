"""Owner /auto_close_all flatten command."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.autotrade import delivery
from app.bot.handlers import dm
from app.signals import manual_execution


def _owner_msg(text: str = "/auto_close_all"):
  return SimpleNamespace(
    chat=SimpleNamespace(type="private"),
    from_user=SimpleNamespace(id=42),
    text=text,
    answer=AsyncMock(),
  )


@pytest.mark.asyncio
async def test_auto_close_all_requires_confirm(monkeypatch):
  monkeypatch.setattr(dm.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(
    dm, "auto_trade_status_text", AsyncMock(return_value="status body")
  )
  pause = AsyncMock()
  close_all = AsyncMock()
  monkeypatch.setattr(dm, "set_auto_trade_paused", pause)
  monkeypatch.setattr(dm, "request_close_all", close_all)

  msg = _owner_msg("/auto_close_all")
  await dm.handle_auto_close_all(msg)

  pause.assert_not_awaited()
  close_all.assert_not_awaited()
  text = msg.answer.await_args.args[0]
  assert "confirm" in text
  assert "status body" in text


@pytest.mark.asyncio
async def test_auto_close_all_confirm_pauses_and_requests_flatten(monkeypatch):
  monkeypatch.setattr(dm.settings, "telegram_owner_id", 42)
  pause = AsyncMock()
  close_all = AsyncMock()
  monkeypatch.setattr(dm, "set_auto_trade_paused", pause)
  monkeypatch.setattr(dm, "request_close_all", close_all)

  msg = _owner_msg("/auto_close_all confirm")
  await dm.handle_auto_close_all(msg)

  pause.assert_awaited_once_with(True)
  close_all.assert_awaited_once()
  text = msg.answer.await_args.args[0]
  assert "Flatten requested" in text
  assert "Total net" in text


@pytest.mark.asyncio
async def test_request_close_all_xadds_close_all_command(monkeypatch):
  client = AsyncMock()
  monkeypatch.setattr(manual_execution.redis_state, "get_client", lambda: client)
  monkeypatch.setattr(
    manual_execution.settings,
    "manual_trade_command_stream",
    "manual_trade:commands",
  )
  monkeypatch.setattr(
    manual_execution.settings,
    "manual_trade_command_stream_maxlen",
    100,
  )

  await manual_execution.request_close_all()

  client.xadd.assert_awaited_once()
  args = client.xadd.await_args
  assert args.args[0] == "manual_trade:commands"
  payload = args.args[1]["payload"]
  assert '"type":"close_all"' in payload.replace(" ", "")


def test_owner_flatten_and_closed_cards_show_total_net_from_fill():
  flatten = delivery.render_auto_trade_event({
    "type": "owner_flatten",
    "message": "owner flatten: closing 2 position(s), cancelling 0 pending",
  })
  assert flatten is not None
  assert "FLATTEN" in flatten
  assert "closing 2 position" in flatten

  closed = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "position closed by owner flatten",
    "group_realized_pips": -12.5,
  })
  assert closed is not None
  assert "POSITION CLOSED" in closed
  assert "Total net: <b>-12.5 pips</b>" in closed
  assert "$" not in closed
