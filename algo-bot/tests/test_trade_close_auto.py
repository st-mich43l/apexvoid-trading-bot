"""Owner /trade_close_auto: close ONE fully-autonomous (algo_auto) broker
position immediately, by its own position_id - before this, the only
owner control for an algo_auto position was /auto_close_all (flattens
every open position, manual and autonomous alike).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.configuration.canonical_fixtures import install_runtime_overrides

from app.bot.handlers import dm


def _owner_msg(text: str):
  return SimpleNamespace(
    chat=SimpleNamespace(type="private"),
    from_user=SimpleNamespace(id=42),
    text=text,
    answer=AsyncMock(),
  )


@pytest.mark.asyncio
async def test_no_args_lists_open_positions(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  monkeypatch.setattr(
    dm,
    "list_open_algo_auto_positions",
    AsyncMock(return_value=[
      {
        "position_id": 101, "symbol": "XAU", "direction": "BUY",
        "entry_price": 4350.0, "remaining_volume": 500,
      },
      {
        "position_id": 102, "symbol": "GBPJPY", "direction": "SELL",
        "entry_price": 215.0, "remaining_volume": 200,
      },
    ]),
  )
  close = AsyncMock()
  monkeypatch.setattr(dm, "request_close_auto_position", close)

  msg = _owner_msg("/trade_close_auto")
  await dm.handle_trade_close_auto(msg)

  close.assert_not_awaited()
  text = msg.answer.await_args.args[0]
  assert "#101" in text and "XAU" in text and "BUY" in text
  assert "#102" in text and "GBPJPY" in text and "SELL" in text


@pytest.mark.asyncio
async def test_no_args_no_open_positions(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  monkeypatch.setattr(
    dm, "list_open_algo_auto_positions", AsyncMock(return_value=[]),
  )

  msg = _owner_msg("/trade_close_auto")
  await dm.handle_trade_close_auto(msg)

  text = msg.answer.await_args.args[0]
  assert "No open algo_auto" in text


@pytest.mark.asyncio
async def test_valid_position_id_closes_immediately(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  monkeypatch.setattr(
    dm,
    "list_open_algo_auto_positions",
    AsyncMock(return_value=[
      {
        "position_id": 101, "symbol": "XAU", "direction": "BUY",
        "entry_price": 4350.0, "remaining_volume": 500,
      },
    ]),
  )
  close = AsyncMock()
  monkeypatch.setattr(dm, "request_close_auto_position", close)

  msg = _owner_msg("/trade_close_auto 101")
  await dm.handle_trade_close_auto(msg)

  close.assert_awaited_once_with(101)
  text = msg.answer.await_args.args[0]
  assert "101" in text
  assert "Close requested" in text


@pytest.mark.asyncio
async def test_unknown_position_id_is_rejected_without_closing(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  monkeypatch.setattr(
    dm, "list_open_algo_auto_positions", AsyncMock(return_value=[]),
  )
  close = AsyncMock()
  monkeypatch.setattr(dm, "request_close_auto_position", close)

  msg = _owner_msg("/trade_close_auto 999")
  await dm.handle_trade_close_auto(msg)

  close.assert_not_awaited()
  text = msg.answer.await_args.args[0]
  assert "999" in text
  assert "No open algo_auto position" in text


@pytest.mark.asyncio
async def test_non_owner_is_ignored(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  close = AsyncMock()
  monkeypatch.setattr(dm, "request_close_auto_position", close)

  msg = _owner_msg("/trade_close_auto 101")
  msg.from_user = SimpleNamespace(id=999)
  await dm.handle_trade_close_auto(msg)

  close.assert_not_awaited()
  msg.answer.assert_not_awaited()
