import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")

from app import broadcast, dedup, symbols, telegram, trade_ops
from app.pips_format import wing_icons


VIP_ID = -100123456789
PUBLIC_ID = -100987654321


@pytest.fixture
def dual_channels(monkeypatch):
  registry = [
    {"symbol": "XAU", "tier": "vip", "channel_id": VIP_ID},
    {"symbol": "XAU", "tier": "public", "channel_id": PUBLIC_ID},
  ]
  monkeypatch.setattr(symbols, "CHANNELS", registry)
  return registry


async def _signal(tmp_path, monkeypatch, visibility="both"):
  await dedup.init_db()
  record = await dedup.store_manual_signal(
    1,
    "BUY",
    2000.0,
    2002.0,
    1990.0,
    [2010.0, 2020.0],
    symbol="XAU",
    visibility=visibility,
  )
  return await dedup.get_manual_signal(record["id"])


def test_channels_and_targets(dual_channels):
  both = symbols.channels_for("XAU", "both")
  vip = symbols.channels_for("XAU", "vip")

  assert [row["channel_id"] for row in both] == [VIP_ID, PUBLIC_ID]
  assert [row["channel_id"] for row in vip] == [VIP_ID]
  assert symbols.targets_for({
    "symbol": "XAU",
    "visibility": "both",
  }) == [VIP_ID, PUBLIC_ID]


@pytest.mark.asyncio
@pytest.mark.parametrize(("visibility", "count"), [("both", 2), ("vip", 1)])
async def test_broadcast_entry_persists_delivery_targets(
  tmp_path,
  monkeypatch,
  dual_channels,
  visibility,
  count,
):
  signal = await _signal(tmp_path, monkeypatch, visibility)
  send = AsyncMock(side_effect=[
    SimpleNamespace(message_id=101),
    SimpleNamespace(message_id=102),
  ])
  monkeypatch.setattr(broadcast, "_send_message", send)

  await broadcast.broadcast_entry(signal)

  posts = await dedup.get_signal_posts(signal["id"])
  assert len(posts) == count
  assert [post["tier"] for post in posts] == (
    ["vip", "public"] if visibility == "both" else ["vip"]
  )
  refreshed = await dedup.get_manual_signal(signal["id"])
  assert refreshed["channel_message_id"] == 101


@pytest.mark.asyncio
async def test_vip_signal_never_fans_out_public(
  tmp_path,
  monkeypatch,
  dual_channels,
):
  signal = await _signal(tmp_path, monkeypatch, "vip")
  send = AsyncMock(return_value=SimpleNamespace(message_id=101))
  monkeypatch.setattr(broadcast, "_send_message", send)
  await broadcast.broadcast_entry(signal)
  send.reset_mock()

  await broadcast.fanout_update(signal, lambda tier: f"{tier} update")

  assert send.await_count == 1
  assert send.await_args.args[1] == VIP_ID
  assert PUBLIC_ID not in {
    call.args[1] for call in send.await_args_list
  }


def test_tier_rendering_hides_public_id():
  signal = {
    "daily_seq": 7,
    "symbol": "XAU",
    "action": "BUY",
    "entry": 2000.0,
    "entry_end": 2002.0,
    "sl": 1990.0,
    "tps": [2010.0],
  }
  result = {
    "action": "close",
    "ok": True,
    "row": {
      "daily_seq": 7,
      "closed": True,
      "net": 70,
    },
    "pips": 70,
  }

  assert "#7" in broadcast.render_entry(signal, "vip")
  assert "#7" not in broadcast.render_entry(signal, "public")
  assert "#7" in trade_ops.render_result(result, "XAU", "vip")
  assert "#7" not in trade_ops.render_result(result, "XAU", "public")


def test_public_close_pips_toggle_never_reveals_id(monkeypatch):
  result = {
    "action": "close",
    "ok": True,
    "row": {
      "daily_seq": 7,
      "closed": True,
      "net": 70,
    },
    "pips": 70,
  }

  monkeypatch.setattr(trade_ops.settings, "public_show_pips", True)
  assert trade_ops.render_result(result, "XAU", "public") == (
    "✅ closed — +70 pips win 💸"
  )
  assert trade_ops.render_result(result, "XAU", "vip") == (
    "✅ #7 closed — net +70 pips 💸"
  )

  monkeypatch.setattr(trade_ops.settings, "public_show_pips", False)
  public = trade_ops.render_result(result, "XAU", "public")
  assert public == "✅ closed — win"
  assert "#7" not in public
  assert "70" not in public
  assert trade_ops.render_result(result, "XAU", "vip") == (
    "✅ #7 closed — net +70 pips 💸"
  )


def test_partial_close_uses_clear_pips_without_at_sign():
  result = {
    "action": "close",
    "ok": True,
    "row": {
      "daily_seq": 7,
      "closed": False,
      "frac": 0.5,
      "remaining": 0.5,
    },
    "pips": 100,
  }

  text = trade_ops.render_result(result, "XAU", "public")

  assert text == "🎯 booked 50% · +100 pips 💸 · remaining 50%"
  assert "@" not in text


def test_dollar_wing_thresholds():
  assert wing_icons(100) == "💸"
  assert wing_icons(101) == "💸💸"
  assert wing_icons(299) == "💸💸"
  assert wing_icons(300) == "💸💸💸"


def test_uncclose_rendering_restores_running_status_without_public_id():
  result = {
    "action": "uncclose",
    "ok": True,
    "row": {
      "id": 1,
      "daily_seq": 7,
    },
    "remaining": 0.5,
  }

  assert trade_ops.render_result(result, "XAU", "vip") == (
    "♻️ #7 restored — trade still running · remaining 50%"
  )
  public = trade_ops.render_result(result, "XAU", "public")
  assert public == "♻️ restored — trade still running · remaining 50%"
  assert "#7" not in public


@pytest.mark.asyncio
async def test_metadata_acks_stay_in_owner_dm(monkeypatch):
  fanout = AsyncMock()
  get_signal = AsyncMock()
  monkeypatch.setattr(trade_ops, "fanout_update", fanout)
  monkeypatch.setattr(trade_ops, "get_manual_signal", get_signal)

  text = await trade_ops.post_result({
    "action": "note",
    "ok": True,
    "sid": 1,
    "seq": 7,
  }, "XAU")

  assert text == "📝 #7 note saved"
  tagged = await trade_ops.post_result({
    "action": "tag",
    "ok": True,
    "sid": 1,
    "seq": 7,
    "setup": "ob-retest",
    "stars": 3,
  }, "XAU")

  assert tagged == "🏷 #7 tagged ob-retest ⭐⭐⭐"
  fanout.assert_not_awaited()
  get_signal.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_tp_is_notify_only_and_tier_aware(monkeypatch):
  signal = {
    "id": 1,
    "daily_seq": 7,
    "status": "open",
    "symbol": "XAU",
    "tps": [2010.0, 2020.0],
  }
  monkeypatch.setattr(
    trade_ops,
    "get_manual_signal",
    AsyncMock(return_value=signal),
  )

  result = await trade_ops.do_tp({
    "sid": 1,
    "symbol": "XAU",
    "tp_number": 2,
    "pips": 56,
  })

  assert result["ok"]
  assert trade_ops.render_result(result, "XAU", "vip") == (
    "🎯 #7 TP2 +56 pips 💸"
  )
  assert trade_ops.render_result(result, "XAU", "public") == (
    "🎯 TP2 +56 pips 💸"
  )

  monkeypatch.setattr(trade_ops.settings, "public_show_pips", False)
  assert trade_ops.render_result(result, "XAU", "public") == "🎯 TP2 hit"


@pytest.mark.asyncio
async def test_public_channel_command_is_ignored(monkeypatch, dual_channels):
  execute = AsyncMock()
  monkeypatch.setattr(telegram, "do_close", execute)
  msg = SimpleNamespace(
    text="close #3 +80",
    message_id=900,
    chat=SimpleNamespace(id=PUBLIC_ID),
    reply_to_message=SimpleNamespace(message_id=700),
  )

  await telegram.handle_channel_close(msg)

  execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_vip_reply_resolves_through_signal_posts(
  tmp_path,
  monkeypatch,
  dual_channels,
):
  signal = await _signal(tmp_path, monkeypatch)
  await dedup.insert_signal_post(signal["id"], VIP_ID, 555, "vip")

  assert await telegram._resolve_sid(None, 555, "XAU") == signal["id"]


@pytest.mark.asyncio
async def test_existing_single_post_is_backfilled(
  tmp_path,
  monkeypatch,
  dual_channels,
  sql,
):
  signal = await _signal(tmp_path, monkeypatch)
  await sql.exec(
    "UPDATE manual_signals SET channel_message_id = 777 WHERE id = $1",
    signal["id"],
  )

  await dedup.init_db()

  assert await dedup.get_signal_posts(signal["id"]) == [{
    "signal_id": signal["id"],
    "channel_id": VIP_ID,
    "message_id": 777,
    "tier": "vip",
  }]


@pytest.mark.asyncio
async def test_reopen_inherits_vip_visibility(
  tmp_path,
  monkeypatch,
  dual_channels,
):
  source = await _signal(tmp_path, monkeypatch, "vip")
  await dedup.close_leg(source["id"], 50)  # re-entry only applies once closed

  result = await trade_ops.do_reopen({
    "sid": source["id"],
    "symbol": "XAU",
    "entry_override": None,
  })

  reopened = await dedup.get_manual_signal(result["record"]["id"])
  assert reopened["visibility"] == "vip"


def test_entry_vip_flag_is_standalone_and_defaults_both():
  base = "gold sell 4100-4105 / sl 4110 / tp 95/90/80"

  assert telegram._parse_manual(base)["visibility"] == "both"
  assert telegram._parse_manual(base + " / vip")["visibility"] == "vip"
  parsed = telegram._parse_manual(
    base + " / vip / setup ob-retest ***"
  )
  assert parsed["visibility"] == "vip"
  assert parsed["setup_type"] == "ob-retest"
  scalp = telegram._parse_manual(base + " / scalp / vip")
  assert scalp["visibility"] == "vip"
  assert scalp["setup_type"] == "scalp"
