"""Owner end-of-day DM wipe: journal, sweep, and both middlewares."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot import owner_dm_journal
from app.persistence import redis_state
from tests.configuration.canonical_fixtures import install_runtime_overrides


pytestmark = [pytest.mark.asyncio, pytest.mark.no_database]

OWNER_ID = 555000111


def _enable(monkeypatch, **overrides):
  merged = {"telegram_owner_id": OWNER_ID, "owner_dm_daily_wipe_enabled": True}
  merged.update(overrides)
  return install_runtime_overrides(monkeypatch, legacy_overrides=merged)


async def test_record_message_id_noop_when_disabled(monkeypatch):
  install_runtime_overrides(
    monkeypatch,
    legacy_overrides={
      "telegram_owner_id": OWNER_ID, "owner_dm_daily_wipe_enabled": False,
    },
  )

  await owner_dm_journal.record_message_id(OWNER_ID, 1)

  client = redis_state.get_client()
  assert await client.smembers(owner_dm_journal._journal_key(
    owner_dm_journal._current_trade_date(),
  )) == set()


async def test_record_message_id_noop_for_non_owner_chat(monkeypatch):
  _enable(monkeypatch)

  await owner_dm_journal.record_message_id(999999999, 1)

  client = redis_state.get_client()
  key = owner_dm_journal._journal_key(owner_dm_journal._current_trade_date())
  assert await client.smembers(key) == set()


async def test_record_message_id_journals_owner_chat(monkeypatch):
  _enable(monkeypatch)

  await owner_dm_journal.record_message_id(OWNER_ID, 101)
  await owner_dm_journal.record_message_id(str(OWNER_ID), 102)

  client = redis_state.get_client()
  key = owner_dm_journal._journal_key(owner_dm_journal._current_trade_date())
  assert await client.smembers(key) == {"101", "102"}
  assert await client.ttl(key) > 0


async def test_sweep_owner_dm_deletes_every_journaled_id_and_clears_journal(
  monkeypatch,
):
  _enable(monkeypatch)
  trade_date = owner_dm_journal._current_trade_date()
  await owner_dm_journal.record_message_id(OWNER_ID, 201)
  await owner_dm_journal.record_message_id(OWNER_ID, 202)
  deleted = AsyncMock()
  import app.bot.client as client_module
  monkeypatch.setattr(client_module, "delete_message", deleted)

  count = await owner_dm_journal.sweep_owner_dm(trade_date)

  assert count == 2
  assert {c.args for c in deleted.await_args_list} == {
    (OWNER_ID, 201), (OWNER_ID, 202),
  }
  client = redis_state.get_client()
  key = owner_dm_journal._journal_key(trade_date)
  assert await client.exists(key) == 0


async def test_sweep_owner_dm_is_best_effort_on_individual_failures(monkeypatch):
  _enable(monkeypatch)
  trade_date = owner_dm_journal._current_trade_date()
  await owner_dm_journal.record_message_id(OWNER_ID, 301)
  await owner_dm_journal.record_message_id(OWNER_ID, 302)

  async def boom(chat_id, message_id):
    if message_id == 301:
      raise RuntimeError("message too old")

  import app.bot.client as client_module
  monkeypatch.setattr(client_module, "delete_message", boom)

  count = await owner_dm_journal.sweep_owner_dm(trade_date)

  assert count == 1


async def test_sweep_owner_dm_empty_journal_deletes_nothing(monkeypatch):
  _enable(monkeypatch)
  import app.bot.client as client_module
  deleted = AsyncMock()
  monkeypatch.setattr(client_module, "delete_message", deleted)

  count = await owner_dm_journal.sweep_owner_dm("2020-01-01")

  assert count == 0
  deleted.assert_not_awaited()


async def test_session_middleware_journals_message_sends_to_owner(monkeypatch):
  _enable(monkeypatch)
  method = SimpleNamespace(chat_id=OWNER_ID)
  # make_request already returns the unwrapped Telegram result (a Message
  # for an ordinary send) - AiohttpSession.make_request itself ends with
  # ``return cast(TelegramType, response.result)``, so there is no
  # ``.result`` left to unwrap here.
  response = SimpleNamespace(message_id=42)

  async def make_request(bot, method):
    return response

  result = await owner_dm_journal.owner_dm_session_middleware(
    make_request, bot=None, method=method,
  )

  assert result is response
  client = redis_state.get_client()
  key = owner_dm_journal._journal_key(owner_dm_journal._current_trade_date())
  assert await client.smembers(key) == {"42"}


async def test_session_middleware_journals_every_item_of_a_media_group(monkeypatch):
  # SendMediaGroup's unwrapped result is list[Message] - every item must be
  # journaled so the whole album gets swept, not silently dropped.
  _enable(monkeypatch)
  method = SimpleNamespace(chat_id=OWNER_ID)
  response = [
    SimpleNamespace(message_id=42), SimpleNamespace(message_id=43),
  ]

  async def make_request(bot, method):
    return response

  result = await owner_dm_journal.owner_dm_session_middleware(
    make_request, bot=None, method=method,
  )

  assert result is response
  client = redis_state.get_client()
  key = owner_dm_journal._journal_key(owner_dm_journal._current_trade_date())
  assert await client.smembers(key) == {"42", "43"}


async def test_session_middleware_ignores_methods_without_chat_id_or_message_id(
  monkeypatch,
):
  _enable(monkeypatch)
  # DeleteMessage-shaped: has chat_id, but its own boolean result carries no
  # message_id - must not journal anything, and must not crash on a bare
  # bool having no attributes at all.
  method = SimpleNamespace(chat_id=OWNER_ID)
  response = True

  async def make_request(bot, method):
    return response

  await owner_dm_journal.owner_dm_session_middleware(
    make_request, bot=None, method=method,
  )

  client = redis_state.get_client()
  key = owner_dm_journal._journal_key(owner_dm_journal._current_trade_date())
  assert await client.smembers(key) == set()


async def test_session_middleware_ignores_other_chats(monkeypatch):
  _enable(monkeypatch)
  method = SimpleNamespace(chat_id=-100123456789)  # a VIP channel post
  response = SimpleNamespace(message_id=42)

  async def make_request(bot, method):
    return response

  await owner_dm_journal.owner_dm_session_middleware(
    make_request, bot=None, method=method,
  )

  client = redis_state.get_client()
  key = owner_dm_journal._journal_key(owner_dm_journal._current_trade_date())
  assert await client.smembers(key) == set()


async def test_session_middleware_always_returns_the_response(monkeypatch):
  # Even a failure in journaling must never break the actual Telegram send.
  _enable(monkeypatch)
  method = SimpleNamespace(chat_id=OWNER_ID)
  response = SimpleNamespace(message_id="not-an-int")

  async def make_request(bot, method):
    return response

  result = await owner_dm_journal.owner_dm_session_middleware(
    make_request, bot=None, method=method,
  )

  assert result is response


async def test_loop_returns_immediately_when_disabled(monkeypatch):
  install_runtime_overrides(
    monkeypatch,
    legacy_overrides={
      "telegram_owner_id": OWNER_ID, "owner_dm_daily_wipe_enabled": False,
    },
  )
  sleeps: list[float] = []

  async def fake_sleep(seconds):
    sleeps.append(seconds)

  monkeypatch.setattr(owner_dm_journal.asyncio, "sleep", fake_sleep)

  await owner_dm_journal.owner_dm_daily_wipe_loop()

  assert sleeps == []


async def test_loop_targets_next_local_midnight_and_sweeps_the_ended_day(
  monkeypatch,
):
  _enable(monkeypatch)
  sweep = AsyncMock(return_value=3)
  monkeypatch.setattr(owner_dm_journal, "sweep_owner_dm", sweep)
  sleeps: list[float] = []

  async def fake_sleep(seconds):
    sleeps.append(seconds)
    if len(sleeps) >= 2:
      raise asyncio.CancelledError()

  monkeypatch.setattr(owner_dm_journal.asyncio, "sleep", fake_sleep)

  with pytest.raises(asyncio.CancelledError):
    await owner_dm_journal.owner_dm_daily_wipe_loop()

  assert len(sleeps) == 2
  assert 0 < sleeps[0] <= 24 * 3600
  sweep.assert_awaited_once()
  swept_date = sweep.await_args.args[0]
  assert swept_date == owner_dm_journal._current_trade_date()


async def test_loop_survives_sweep_exception_and_backs_off(monkeypatch):
  _enable(monkeypatch)

  async def boom(trade_date):
    raise RuntimeError("redis unavailable")

  monkeypatch.setattr(owner_dm_journal, "sweep_owner_dm", boom)
  sleeps: list[float] = []

  async def fake_sleep(seconds):
    sleeps.append(seconds)
    if len(sleeps) >= 2:
      raise asyncio.CancelledError()

  monkeypatch.setattr(owner_dm_journal.asyncio, "sleep", fake_sleep)

  with pytest.raises(asyncio.CancelledError):
    await owner_dm_journal.owner_dm_daily_wipe_loop()

  assert sleeps[1] == 3600
