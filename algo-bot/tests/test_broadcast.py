import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")

from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf
from app.signals import broadcast, trade_ops
from app.persistence import store
from app.core import symbols
from app.bot import wiring
from app.signals.pips_format import wing_icons


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
  await store.init_db()
  record = await store.store_manual_signal(
    1,
    "BUY",
    2000.0,
    2002.0,
    1990.0,
    [2010.0, 2020.0],
    symbol="XAU",
    visibility=visibility,
  )
  return await store.get_manual_signal(record["id"])


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

  posts = await store.get_signal_posts(signal["id"])
  assert len(posts) == count
  assert [post["tier"] for post in posts] == (
    ["vip", "public"] if visibility == "both" else ["vip"]
  )
  refreshed = await store.get_manual_signal(signal["id"])
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


@pytest.mark.no_database
def test_render_entry_pins_tp_r_multiples_to_original_sl_not_trailed_stop():
  """Live 2026-09-04: editing the pinned card after a stop trail re-ran
  this with the now-current (near-BE) sl. R = TP-distance / risk, so as a
  trailed stop shrinks risk toward zero every TP's R blows up (a 343.8R
  line went out live on a real XAU SELL). Each TP's R must stay pinned to
  the ORIGINAL risk - same convention _achieved_rr already uses for the
  close line - while the SL/risk display itself still tracks the live
  trail.
  """
  # XAU SELL 4420-4425 (entry_reference from rr_entry - the wider edge),
  # original stop 4428 (risk 8 from that edge), now trailed to 4420.16.
  signal = {
    "daily_seq": 10,
    "symbol": "XAU",
    "action": "SELL",
    "entry": 4420.0,
    "entry_end": 4425.0,
    "sl": 4420.16,
    "original_sl": 4428.0,
    "tps": [4414.0, 4407.0, 4398.0, 4384.0, 4365.0],
  }

  card = broadcast.render_entry(signal, "vip")

  assert "SL:     <b>4,420.16</b>" in card
  # risk is displayed in pips (0.16 price / 0.1 pip_size = 1.6, rounds to 2).
  assert "risk <b>2 pips</b>" in card


@pytest.mark.no_database
def test_render_entry_shows_risk_in_pips_not_raw_price():
  # Live 2026-09-10: the card showed "risk 8" for an $8.00 XAU stop
  # distance - readers naturally read a bare number as pips, understating
  # the real risk by 1/pip_size (10x for XAU's 0.1 pip). Every other pips
  # figure this bot shows (loss/win, R-multiple denominators) already
  # divides by pip_for() - this line must match.
  signal = {
    "daily_seq": 5,
    "symbol": "XAU",
    "action": "BUY",
    "entry": 4398.0,
    "entry_end": 4403.0,
    "sl": 4395.0,
    "tps": [4408.0],
  }
  card = broadcast.render_entry(signal, "vip")
  # entry_reference (BUY) = entry_end = 4403; risk = 4403-4395 = 8.0 price
  # = 80 pips, not "risk 8".
  assert "risk <b>80 pips</b>" in card
  assert "risk <b>8</b>" not in card
  # The bug this guards: with the live (near-zero) risk instead, TP1 alone
  # would have rendered as roughly 37R - assert that never appears.
  assert "343.8R" not in card
  assert "37.5R" not in card


@pytest.mark.no_database
def test_render_entry_uses_real_fill_for_risk_once_known():
  """Live 2026-09-10 (signal #309): the SL line quoted "risk 60 pips" off
  the conservative pre-fill zone edge (4336), but the real fill landed at
  4335.45 - true risk only 54 pips - and the close line correctly reported
  -0.7R off the real fill, leaving "60 pips" impossible to reconcile
  against it. Once broker_fill_price is known, the risk line must use it,
  same fill-trust convention as trade_ops._achieved_rr's close-time R calc.
  TP R-multiples stay pinned to the zone-edge/original_sl convention -
  unaffected by this.
  """
  signal = {
    "daily_seq": 4,
    "symbol": "XAU",
    "action": "BUY",
    "entry": 4333.0,
    "entry_end": 4336.0,
    "sl": 4330.0,
    "tps": [4339.0, 4342.0, 4348.0, 4354.0],
  }
  assert "risk <b>60 pips</b>" in broadcast.render_entry(signal, "vip")

  signal["broker_fill_price"] = 4335.45
  card = broadcast.render_entry(signal, "vip")
  assert "risk <b>54 pips</b>" in card
  assert "risk <b>60 pips</b>" not in card


@pytest.mark.no_database
def test_render_entry_falls_back_to_current_sl_when_never_trailed():
  """A signal that hasn't trailed yet has no original_sl - must fall back
  to the live sl so a first-post card (sl == the real original) is
  unaffected by this fix.
  """
  signal = {
    "daily_seq": 10,
    "symbol": "XAU",
    "action": "SELL",
    "entry": 4420.0,
    "entry_end": 4425.0,
    "sl": 4428.0,
    "tps": [4414.0],
  }

  card = broadcast.render_entry(signal, "vip")
  entry_reference = broadcast.rr_entry(signal)
  risk = abs(entry_reference - 4428.0)

  assert broadcast._rr(4414.0, entry_reference, risk) in card


@pytest.mark.no_database
def test_manual_entry_card_shows_canonical_setup_and_confluence():
  signal = {
    "daily_seq": 7,
    "symbol": "XAU",
    "action": "BUY",
    "entry": 2000.0,
    "entry_end": 2002.0,
    "sl": 1990.0,
    "tps": [2010.0],
    "setup_type": "key-level",
    "confluence": 2,
  }

  vip = broadcast.render_entry(signal, "vip")
  public = broadcast.render_entry(signal, "public")

  assert "🏷 Setup:  <b>Key Level</b>  ⭐⭐" in vip
  assert "🏷 Setup:  <b>Key Level</b>  ⭐⭐" in public
  assert "#7" not in public


@pytest.mark.no_database
def test_manual_entry_card_preserves_unknown_setup_label_safely():
  signal = {
    "daily_seq": 7,
    "symbol": "XAU",
    "action": "BUY",
    "entry": 2000.0,
    "entry_end": 2002.0,
    "sl": 1990.0,
    "tps": [2010.0],
    "setup_type": "Custom <setup>",
  }

  card = broadcast.render_entry(signal, "vip")

  assert "🏷 Setup:  <b>Custom &lt;setup&gt;</b>" in card


@pytest.mark.no_database
def test_fx_manual_algo_entry_card_uses_entry_price_not_zone(monkeypatch):
  from tests.test_config_effective_instrument_context import _load_production_example

  cfg = _load_production_example().config
  for target in (
    "app.core.config.runtime_config",
    "app.core.symbols.runtime_config",
    "app.signals.fx_manual_algo.runtime_config",
  ):
    monkeypatch.setattr(target, cfg, raising=False)
  signal = {
    "daily_seq": 3,
    "symbol": "EURUSD",
    "action": "BUY",
    "entry": 1.15007,
    "entry_end": 1.15007,
    "sl": 1.14867,
    "tps": [1.15147, 1.15217, 1.15287],
  }
  card = broadcast.render_entry(signal, "vip")

  assert "Entry Price:" in card
  assert "Entry Zone:" not in card
  assert "1.15007 - " not in card


@pytest.mark.no_database
def test_xau_entry_card_still_uses_entry_zone():
  signal = {
    "daily_seq": 7,
    "symbol": "XAU",
    "action": "BUY",
    "entry": 2000.0,
    "entry_end": 2002.0,
    "sl": 1990.0,
    "tps": [2010.0],
  }
  card = broadcast.render_entry(signal, "vip")

  assert "Entry Zone:" in card
  assert "Entry Price:" not in card


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

  install_runtime_overrides(monkeypatch, legacy_overrides={"public_show_pips": True})
  assert trade_ops.render_result(result, "XAU", "public") == (
    "✅ closed — +70 pips win 💸"
  )
  assert trade_ops.render_result(result, "XAU", "vip") == (
    "✅ #7 closed — achieved +70 pips 💸"
  )

  install_runtime_overrides(monkeypatch, legacy_overrides={"public_show_pips": False})
  public = trade_ops.render_result(result, "XAU", "public")
  assert public == "✅ closed — win"
  assert "#7" not in public
  assert "70" not in public
  assert trade_ops.render_result(result, "XAU", "vip") == (
    "✅ #7 closed — achieved +70 pips 💸"
  )


def test_vip_close_reports_losing_when_net_negative():
  result = {
    "action": "close",
    "ok": True,
    "row": {
      "daily_seq": 5,
      "closed": True,
      "net": -47,
    },
    "pips": -47,
  }

  assert trade_ops.render_result(result, "XAU", "vip") == (
    "🛑 #5 closed — losing -47 pips"
  )
  assert trade_ops.render_result(result, "XAU", "public") == (
    "🛑 closed — -47 pips loss"
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

  assert text == "🎯 +100 pips 💸"
  assert "@" not in text


def test_partial_close_with_tp_number_labels_which_target_was_hit():
  result = {
    "action": "close",
    "ok": True,
    "row": {
      "daily_seq": 7,
      "closed": False,
      "frac": 0.25,
      "remaining": 0.75,
    },
    "pips": 37,
    "tp_number": 1,
  }

  assert trade_ops.render_result(result, "XAU", "vip") == (
    "🎯 #7 TP1 +37 pips 💸"
  )
  assert trade_ops.render_result(result, "XAU", "public") == (
    "🎯 TP1 +37 pips 💸"
  )


def test_achieved_rr_matches_reports_realized_r_convention():
  # Entry 2000-2002 (midpoint 2001), original_sl 1990 - risk = 110 pips.
  # +220 pips achieved is exactly 2R.
  sig = {
    "action": "BUY",
    "entry": 2000.0,
    "entry_end": 2002.0,
    "sl": 1994.0,
    "original_sl": 1990.0,
    "symbol": "XAU",
  }

  assert trade_ops._achieved_rr(sig, 220) == "+2.0R"
  assert trade_ops._achieved_rr(sig, -55) == "-0.5R"


def test_achieved_rr_uses_original_sl_not_a_trailed_stop():
  # sl has since trailed to break-even (2001) - risk must stay pinned to
  # original_sl, or a trailed stop would inflate R toward infinity.
  sig = {
    "action": "BUY",
    "entry": 2000.0,
    "entry_end": 2002.0,
    "sl": 2001.0,
    "original_sl": 1990.0,
    "symbol": "XAU",
  }

  assert trade_ops._achieved_rr(sig, 220) == "+2.0R"


def test_achieved_rr_falls_back_to_sl_when_original_sl_missing():
  sig = {
    "action": "BUY",
    "entry": 2000.0, "entry_end": 2002.0, "sl": 1990.0, "symbol": "XAU",
  }

  assert trade_ops._achieved_rr(sig, 220) == "+2.0R"


def test_achieved_rr_none_when_risk_is_zero():
  sig = {
    "action": "SELL",
    "entry": 2000.0, "entry_end": 2002.0, "sl": 2001.0, "symbol": "XAU",
  }

  assert trade_ops._achieved_rr(sig, 0) is None


def test_achieved_rr_uses_the_deepest_legs_own_entry_not_the_peak_pips_leg():
  # 2026-09 (owner-reported): "for pips archived, it must calculate from
  # the deepest entry as well" - risk must be measured from the DEEPEST
  # leg's own entry, not whichever leg happened to report the most pips.
  # SELL zone 4100-4105: shallow=4100 (near edge, most likely to fill),
  # deep=4105 (far edge, best sell price) per AutoTradeEngine.cs's
  # ManualEntryLegPrices. Shallow books a big win (150p, the peak);
  # deep gets stopped at BE (0p) after price reverses following the
  # shallow win - a realistic "BE trails to deeper entry" outcome.
  # Zone midpoint (4102.5) and the old peak-pips-tied lookup (4100, the
  # SHALLOW leg) would both give the wrong, smaller risk. Correct: risk
  # from the deep leg's own 4105 entry: |4105-4110| = 5.0 price = 50 pips
  # -> 150/50 = 3.0R.
  sig = {
    "action": "SELL",
    "entry": 4100.0,
    "entry_end": 4105.0,
    "sl": 4106.0,
    "original_sl": 4110.0,
    "symbol": "XAU",
    "legs": [
      {"frac": 0.8, "pips": 150, "entry_price": 4100.0},
      {"frac": 0.2, "pips": 0, "entry_price": 4105.0},
    ],
  }

  assert trade_ops._achieved_rr(sig, 150) == "+3.0R"


def test_achieved_rr_falls_back_to_zone_when_no_leg_carries_entry_price():
  # An older signal's legs predate entry_price - must not crash, and must
  # fall back to the zone-midpoint convention rather than silently using
  # an absent value.
  sig = {
    "action": "BUY",
    "entry": 2000.0,
    "entry_end": 2002.0,
    "sl": 1994.0,
    "original_sl": 1990.0,
    "symbol": "XAU",
    "legs": [{"frac": 1.0, "pips": 220, "ts": 1}],
  }

  assert trade_ops._achieved_rr(sig, 220) == "+2.0R"


def test_achieved_rr_prefers_broker_fill_over_a_single_legs_bad_entry():
  # Live 2026-09-10 (signal #293): a single-leg SELL closed -57 pips but
  # showed -5.8R, not the correct ~-0.7R. AutoTradeEngine.cs's restart-gap
  # orphan reconciliation (InvestigateOrphanedGroupPlanAsync) sets a leg's
  # entry_price from broker deal-history reconstruction, which can disagree
  # with the normal live-confirmed broker_fill_price. A single-leg trade's
  # own entry can never legitimately differ from broker_fill_price, so a
  # mismatch there is the signature of that less-reliable path - trust the
  # live fill. |4390.16-4398| = 7.84 price = 78.4 pips -> -57/78.4 = -0.7R.
  sig = {
    "action": "SELL",
    "entry": 4390.0,
    "entry_end": 4395.0,
    "sl": 4398.0,
    "original_sl": 4398.0,
    "symbol": "XAU",
    "broker_fill_price": 4390.16,
    "legs": [{"frac": 1.0, "pips": -57, "entry_price": 4397.02}],
  }

  assert trade_ops._achieved_rr(sig, -57) == "-0.7R"


def test_achieved_rr_keeps_deepest_leg_for_genuine_multi_leg_trades():
  # The broker_fill_price override above must not swallow the legitimate
  # multi-leg deepest-entry convention: broker_fill_price is deliberately
  # the group's SHALLOWEST (worst-case) leg there, not the deep leg this
  # calc needs - see test_achieved_rr_uses_the_deepest_legs_own_entry_not_
  # the_peak_pips_leg above. Only a single-leg mismatch is now overridden.
  sig = {
    "action": "SELL",
    "entry": 4100.0,
    "entry_end": 4105.0,
    "sl": 4106.0,
    "original_sl": 4110.0,
    "symbol": "XAU",
    "broker_fill_price": 4100.0,
    "legs": [
      {"frac": 0.8, "pips": 150, "entry_price": 4100.0},
      {"frac": 0.2, "pips": 0, "entry_price": 4105.0},
    ],
  }

  assert trade_ops._achieved_rr(sig, 150) == "+3.0R"


def test_final_close_with_tp_number_labels_which_target_closed_it(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"public_show_pips": True})
  result = {
    "action": "close",
    "ok": True,
    "row": {
      "daily_seq": 7,
      "closed": True,
      "net": 570,
    },
    "pips": 570,
    "tp_number": 4,
  }

  assert trade_ops.render_result(result, "XAU", "vip") == (
    "✅ #7 TP4 closed — achieved +570 pips 💸💸💸"
  )
  assert trade_ops.render_result(result, "XAU", "public") == (
    "✅ TP4 closed — +570 pips win 💸💸💸"
  )


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

  install_runtime_overrides(monkeypatch, legacy_overrides={"public_show_pips": False})
  assert trade_ops.render_result(result, "XAU", "public") == "🎯 TP2 hit"


@pytest.mark.asyncio
async def test_public_channel_command_is_ignored(monkeypatch, dual_channels):
  execute = AsyncMock()
  monkeypatch.setattr(wiring, "do_close", execute)
  msg = SimpleNamespace(
    text="close #3 +80",
    message_id=900,
    chat=SimpleNamespace(id=PUBLIC_ID),
    reply_to_message=SimpleNamespace(message_id=700),
  )

  await wiring.handle_channel_close(msg)

  execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_vip_reply_resolves_through_signal_posts(
  tmp_path,
  monkeypatch,
  dual_channels,
):
  signal = await _signal(tmp_path, monkeypatch)
  await store.insert_signal_post(signal["id"], VIP_ID, 555, "vip")

  assert await wiring._resolve_sid(None, 555, "XAU") == signal["id"]


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

  await store.init_db()

  assert await store.get_signal_posts(signal["id"]) == [{
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
  await store.close_leg(source["id"], 50)  # re-entry only applies once closed

  result = await trade_ops.do_reopen({
    "sid": source["id"],
    "symbol": "XAU",
    "entry_override": None,
  })

  reopened = await store.get_manual_signal(result["record"]["id"])
  assert reopened["visibility"] == "vip"


def test_entry_vip_flag_is_standalone_and_defaults_both():
  base = "gold sell 4100-4105 / sl 4110 / tp 95/90/80"

  assert wiring._parse_manual(base)["visibility"] == "both"
  assert wiring._parse_manual(base + " / vip")["visibility"] == "vip"
  parsed = wiring._parse_manual(
    base + " / vip / setup ob-retest ***"
  )
  assert parsed["visibility"] == "vip"
  assert parsed["setup_type"] == "ob-retest"
  scalp = wiring._parse_manual(base + " / scalp / vip")
  assert scalp["visibility"] == "vip"
  assert scalp["setup_type"] == "scalp"
