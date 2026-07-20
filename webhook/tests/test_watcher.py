import os
import random
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault(
  "TELEGRAM_BOT_TOKEN",
  "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100123456789")

from app import broadcast, redis_state, watcher
from app.pips_format import pips_between, rr_entry
from app.symbols import pip_for


def _bar(ts: str, o: float, h: float, l: float, c: float) -> dict:
  return {"date": ts, "open": o, "high": h, "low": l, "close": c}


def _buy_signal(**over) -> dict:
  sig = {
    "id": 3,
    "daily_seq": 2,
    "channel_message_id": 77,
    "fill_state": "filled",
    "action": "BUY",
    "symbol": "XAU",
    "entry": 2000.0,
    "entry_end": 2002.0,
    "sl": 1990.0,
    "tps": [2010.0],
  }
  sig.update(over)
  return sig


def _sell_signal(**over) -> dict:
  sig = {
    "id": 3,
    "daily_seq": 2,
    "channel_message_id": 77,
    "fill_state": "filled",
    "action": "SELL",
    "symbol": "XAU",
    "entry": 2000.0,
    "entry_end": 2002.0,
    "sl": 2010.0,
    "tps": [1990.0],
  }
  sig.update(over)
  return sig


@pytest.fixture(autouse=True)
def _market_always_open(monkeypatch):
  monkeypatch.setattr(watcher, "_market_open", lambda: True)


def _feed(monkeypatch, sig, bars):
  monkeypatch.setattr(
    watcher, "get_open_signals", AsyncMock(return_value=[sig])
  )
  monkeypatch.setattr(watcher, "get_xau_bars", AsyncMock(return_value=bars))
  fanout = AsyncMock()
  monkeypatch.setattr(watcher, "fanout_update", fanout)
  return fanout


@pytest.mark.asyncio
async def test_skips_feed_without_filled_signals(monkeypatch):
  monkeypatch.setattr(
    watcher,
    "get_open_signals",
    AsyncMock(return_value=[{"id": 1, "fill_state": "pending"}]),
  )
  bars = AsyncMock()
  monkeypatch.setattr(watcher, "get_xau_bars", bars)

  await watcher._watcher_tick(object())

  bars.assert_not_awaited()


@pytest.mark.asyncio
async def test_cold_start_anchors_cursor_without_alerting(monkeypatch):
  bars = [
    _bar("2026-07-08T10:00:00.000Z", 2005, 2015, 2004, 2012),
    _bar("2026-07-08T10:01:00.000Z", 2012, 2016, 2011, 2015),
  ]
  fanout = _feed(monkeypatch, _buy_signal(), bars)

  await watcher._watcher_tick(object())

  fanout.assert_not_awaited()
  assert await redis_state.get_cursor("XAU") == "2026-07-08T10:01:00.000Z"


@pytest.mark.asyncio
async def test_tp_hit_notify_and_deduplicated(monkeypatch):
  await redis_state.set_cursor("XAU", "2026-07-08T09:59:00.000Z")
  # Wick: high pierces TP1 (2010) although close is back below it.
  bar = _bar("2026-07-08T10:00:00.000Z", 2005, 2010, 2004, 2003)
  fanout = _feed(monkeypatch, _buy_signal(), [bar])

  await watcher._watcher_tick(object())
  # A later bar still touching TP but not extending profit must not re-alert.
  later = _bar("2026-07-08T10:01:00.000Z", 2010, 2010, 2009, 2010)
  monkeypatch.setattr(watcher, "get_xau_bars", AsyncMock(return_value=[later]))
  await watcher._watcher_tick(object())

  fanout.assert_awaited_once()
  _, render = fanout.await_args.args
  assert render("vip") == (
    "🎯 <b>TP HIT</b> | #2\n"
    "📈 Fill: <b>2,010</b> (TP1)\n"
    "✅ Profit: <b>+80 pips</b> 💸\n\n"
  )
  assert render("public") == "🎯 TP1 +80 pips 💸"
  assert (await redis_state.get_progress(3))["tp"] == 1


@pytest.mark.asyncio
async def test_sell_whole_price_tp_hits_on_same_price_handle(monkeypatch):
  await redis_state.set_cursor("XAU", "2026-07-08T09:59:00.000Z")
  sig = _sell_signal(
    entry=4027.0,
    entry_end=4029.0,
    sl=4035.0,
    tps=[4017.0],
  )
  bar = _bar("2026-07-08T10:00:00.000Z", 4023, 4024, 4017.82, 4018.1)
  fanout = _feed(monkeypatch, sig, [bar])

  await watcher._watcher_tick(object())

  fanout.assert_awaited_once()
  _, render = fanout.await_args.args
  assert (
    "Fill: <b>4,017</b> (TP1) · ran to <b>4,017.82</b>"
    in render("vip")
  )
  assert (await redis_state.get_progress(3))["tp"] == 1


def test_sell_decimal_tp_keeps_exact_threshold():
  assert watcher._tp_hit(4017.82, 4017.0, is_buy=False)
  assert not watcher._tp_hit(4017.82, 4017.5, is_buy=False)


@pytest.mark.asyncio
async def test_runner_alerts_after_final_tp_new_high(monkeypatch):
  await redis_state.set_cursor("XAU", "2026-07-08T09:59:00.000Z")
  tp_bar = _bar("2026-07-08T10:00:00.000Z", 2005, 2010, 2004, 2010)
  fanout = _feed(monkeypatch, _buy_signal(), [tp_bar])

  await watcher._watcher_tick(object())

  runner = _bar("2026-07-08T10:01:00.000Z", 2010, 2015, 2009, 2014)
  monkeypatch.setattr(watcher, "get_xau_bars", AsyncMock(return_value=[runner]))
  await watcher._watcher_tick(object())

  assert fanout.await_count == 2
  _, render = fanout.await_args_list[1].args
  assert render("vip") == (
    "🚀 <b>TP RUNNER</b> | #2\n"
    "📈 Price: <b>2,015</b>\n"
    "✅ Profit: <b>+130 pips</b> 💸💸\n\n"
  )
  assert render("public") == "🚀 runner +130 pips 💸💸"
  markup_fn = fanout.await_args_list[1].kwargs["markup_fn"]
  assert markup_fn("public") is None
  assert markup_fn("vip").inline_keyboard[0][0].callback_data == "c0:3:1:130"
  assert (await redis_state.get_progress(3))["runner_pips"] == 130

  lower_high = _bar("2026-07-08T10:02:00.000Z", 2014, 2014, 2011, 2012)
  monkeypatch.setattr(
    watcher, "get_xau_bars", AsyncMock(return_value=[lower_high])
  )
  await watcher._watcher_tick(object())

  assert fanout.await_count == 2


@pytest.mark.asyncio
async def test_runner_alerts_after_final_tp_new_low_for_sell(monkeypatch):
  await redis_state.set_cursor("XAU", "2026-07-08T09:59:00.000Z")
  tp_bar = _bar("2026-07-08T10:00:00.000Z", 1995, 2003, 1990, 1991)
  fanout = _feed(monkeypatch, _sell_signal(), [tp_bar])

  await watcher._watcher_tick(object())

  runner = _bar("2026-07-08T10:01:00.000Z", 1991, 1992, 1985, 1986)
  monkeypatch.setattr(watcher, "get_xau_bars", AsyncMock(return_value=[runner]))
  await watcher._watcher_tick(object())

  assert fanout.await_count == 2
  _, render = fanout.await_args_list[1].args
  assert render("vip") == (
    "🚀 <b>TP RUNNER</b> | #2\n"
    "📈 Price: <b>1,985</b>\n"
    "✅ Profit: <b>+150 pips</b> 💸💸\n\n"
  )
  assert render("public") == "🚀 runner +150 pips 💸💸"


@pytest.mark.asyncio
async def test_ignores_bars_before_signal_fill(monkeypatch):
  # Idle overnight leaves the global cursor stale, so new_bars spans the whole
  # day. A signal filled at 10:00 must not react to the 09:00 bar that already
  # swept both TP1 (2010) and SL (1990) — otherwise it trips on open.
  await redis_state.set_cursor("XAU", "2026-07-08T08:59:00.000Z")
  fill_ts = datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc).timestamp()
  pre_fill = _bar("2026-07-08T09:00:00.000Z", 2005, 2015, 1985, 2003)
  post_fill = _bar("2026-07-08T10:01:00.000Z", 2003, 2004, 2002, 2003)
  fanout = _feed(monkeypatch, _buy_signal(filled_at=fill_ts), [pre_fill, post_fill])

  await watcher._watcher_tick(object())

  fanout.assert_not_awaited()
  assert await redis_state.get_cursor("XAU") == "2026-07-08T10:01:00.000Z"


@pytest.mark.asyncio
async def test_alerts_on_bar_at_or_after_fill(monkeypatch):
  # Boundary: a post-fill bar still alerts even when a pre-fill bar is present,
  # proving the fill filter does not over-suppress live price action.
  await redis_state.set_cursor("XAU", "2026-07-08T08:59:00.000Z")
  fill_ts = datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc).timestamp()
  pre_fill = _bar("2026-07-08T09:00:00.000Z", 2005, 2006, 2004, 2005)
  post_fill = _bar("2026-07-08T10:00:00.000Z", 2005, 2010, 2004, 2003)
  fanout = _feed(monkeypatch, _buy_signal(filled_at=fill_ts), [pre_fill, post_fill])

  await watcher._watcher_tick(object())

  fanout.assert_awaited_once()
  assert (await redis_state.get_progress(3))["tp"] == 1


@pytest.mark.asyncio
async def test_sequential_tp_fires_in_order(monkeypatch):
  await redis_state.set_cursor("XAU", "2026-07-08T09:59:00.000Z")
  # One bar whose high crosses both TP1 (2010) and TP2 (2020).
  bar = _bar("2026-07-08T10:00:00.000Z", 2005, 2025, 2004, 2024)
  sig = _buy_signal(tps=[2010.0, 2020.0])
  fanout = _feed(monkeypatch, sig, [bar])

  await watcher._watcher_tick(object())

  assert fanout.await_count == 3
  keys = [
    call.args[1]("vip").split("(")[1][:3]
    for call in fanout.await_args_list[:2]
  ]
  assert keys == ["TP1", "TP2"]
  assert (await redis_state.get_progress(3))["tp"] == 2


@pytest.mark.asyncio
async def test_sl_hit_stops_further_alerts(monkeypatch):
  await redis_state.set_cursor("XAU", "2026-07-08T09:59:00.000Z")
  sl_bar = _bar("2026-07-08T10:00:00.000Z", 2000, 2001, 1989, 1992)
  fanout = _feed(monkeypatch, _buy_signal(), [sl_bar])

  await watcher._watcher_tick(object())
  # Price recovers to TP after the stop — must NOT alert (trade is done).
  recover = _bar("2026-07-08T10:01:00.000Z", 2000, 2011, 1999, 2010)
  monkeypatch.setattr(watcher, "get_xau_bars", AsyncMock(return_value=[recover]))
  await watcher._watcher_tick(object())

  fanout.assert_awaited_once()
  _, render = fanout.await_args.args
  assert render("vip").startswith("⚠️ <b>NEAR SL</b>")
  markup_fn = fanout.await_args.kwargs["markup_fn"]
  assert markup_fn("public") is None
  assert markup_fn("vip").inline_keyboard[0][0].callback_data == "c0:3:0:-120"
  assert (await redis_state.get_progress(3))["sl"] is True


@pytest.mark.asyncio
async def test_manual_tp_mark_suppresses_watcher(monkeypatch):
  await redis_state.set_cursor("XAU", "2026-07-08T09:59:00.000Z")
  await watcher.mark_tp_alert(3, 1)  # simulate manual /trade_tp booking
  bar = _bar("2026-07-08T10:00:00.000Z", 2005, 2010, 2004, 2010)
  fanout = _feed(monkeypatch, _buy_signal(), [bar])

  await watcher._watcher_tick(object())

  fanout.assert_not_awaited()


@pytest.mark.asyncio
async def test_stopped_out_signal_skips_tiingo_fetch(monkeypatch):
  # A filled signal that already hit SL is done — it must not keep polling
  # Tiingo (which would silently drain the free-tier request quota).
  await redis_state.set_sl_flag(3)
  bars = AsyncMock()
  monkeypatch.setattr(
    watcher, "get_open_signals", AsyncMock(return_value=[_buy_signal()])
  )
  monkeypatch.setattr(watcher, "get_xau_bars", bars)

  await watcher._watcher_tick(object())

  bars.assert_not_awaited()


@pytest.mark.asyncio
async def test_tp_alert_carries_owner_button_on_vip_only(monkeypatch):
  await redis_state.set_cursor("XAU", "2026-07-08T09:59:00.000Z")
  bar = _bar("2026-07-08T10:00:00.000Z", 2005, 2010, 2004, 2003)
  fanout = _feed(monkeypatch, _buy_signal(), [bar])

  await watcher._watcher_tick(object())

  markup_fn = fanout.await_args.kwargs["markup_fn"]
  assert markup_fn("public") is None
  kb = markup_fn("vip")
  assert kb.inline_keyboard[0][0].callback_data == "c0:3:1:80"


def test_public_watcher_alert_hides_pips_when_disabled(monkeypatch):
  monkeypatch.setattr(watcher.settings, "public_show_pips", False)

  tp = watcher._render_level_alert("public", "TP", "TP1", 2, 2010, 90)
  sl = watcher._render_level_alert("public", "SL", "SL", 2, 1990, 110)
  runner = watcher._render_level_alert(
    "public", "RUNNER", "RUNNER", 2, 2020, 190
  )

  assert tp == "🎯 TP hit"
  assert sl == "🛡 SL hit"
  assert runner == "🚀 runner"
  assert not any(char.isdigit() for char in tp + sl + runner)


@pytest.mark.asyncio
async def test_incident_replay_books_sell_sl_at_level(monkeypatch):
  await redis_state.set_cursor("XAU", "2026-07-08T09:59:00.000Z")
  sig = _sell_signal(
    entry=3995.0,
    entry_end=3997.0,
    sl=4000.0,
    tps=[3990.0],
  )
  bar = _bar("2026-07-08T10:00:00.000Z", 3998, 4002.99, 3996, 4001)
  fanout = _feed(monkeypatch, sig, [bar])

  await watcher._watcher_tick(object())

  fanout.assert_awaited_once()
  _, render = fanout.await_args.args
  assert render("vip") == (
    "⚠️ <b>NEAR SL</b> | #2\n"
    "📉 Fill: <b>4,000</b> (SL) · ran to <b>4,002.99</b>\n"
    "❌ Loss: <b>-50 pips</b>\n\n"
  )
  assert render("public") == "🛡 SL (-50 pips)"
  markup_fn = fanout.await_args.kwargs["markup_fn"]
  assert markup_fn("vip").inline_keyboard[0][0].callback_data == "c0:3:0:-50"


@pytest.mark.asyncio
async def test_tp_replay_books_tier_at_level_then_runner_at_extreme(monkeypatch):
  await redis_state.set_cursor("XAU", "2026-07-08T09:59:00.000Z")
  sig = _sell_signal(
    entry=3995.0,
    entry_end=3997.0,
    sl=4000.0,
    tps=[3990.0],
  )
  bar = _bar("2026-07-08T10:00:00.000Z", 3997, 3998, 3985, 3987)
  fanout = _feed(monkeypatch, sig, [bar])

  await watcher._watcher_tick(object())

  assert fanout.await_count == 2
  _, render = fanout.await_args_list[0].args
  assert render("vip") == (
    "🎯 <b>TP HIT</b> | #2\n"
    "📈 Fill: <b>3,990</b> (TP1) · ran to <b>3,985</b>\n"
    "✅ Profit: <b>+50 pips</b> 💸\n\n"
  )
  markup_fn = fanout.await_args_list[0].kwargs["markup_fn"]
  assert markup_fn("vip").inline_keyboard[0][0].callback_data == "c0:3:1:50"
  _, runner_render = fanout.await_args_list[1].args
  assert "TP RUNNER" in runner_render("vip")
  assert "+100 pips" in runner_render("vip")


@pytest.mark.asyncio
async def test_buy_mirror_books_sl_and_tp_at_levels(monkeypatch):
  fanout = AsyncMock()
  monkeypatch.setattr(watcher, "fanout_update", fanout)
  sig = _buy_signal(
    entry=3995.0,
    entry_end=3997.0,
    sl=3992.0,
    tps=[4002.0],
  )

  sl_bar = _bar("2026-07-08T10:00:00.000Z", 3994, 3995, 3989.01, 3990)
  await watcher._evaluate(
    sig,
    sl_bar,
    {"tp": 0, "sl": False, "runner_pips": 0},
    atr=5.0,
  )

  _, sl_render = fanout.await_args.args
  assert (
    "Fill: <b>3,992</b> (SL) · ran to <b>3,989.01</b>"
    in sl_render("vip")
  )
  assert "Loss: <b>-50 pips</b>" in sl_render("vip")

  fanout.reset_mock()
  tp_bar = _bar("2026-07-08T10:01:00.000Z", 3998, 4007, 3996, 4005)
  await watcher._evaluate(
    sig,
    tp_bar,
    {"tp": 0, "sl": False, "runner_pips": 0},
    atr=5.0,
  )

  assert fanout.await_count == 2
  _, tp_render = fanout.await_args_list[0].args
  assert (
    "Fill: <b>4,002</b> (TP1) · ran to <b>4,007</b>"
    in tp_render("vip")
  )
  assert "Profit: <b>+50 pips</b>" in tp_render("vip")
  _, runner_render = fanout.await_args_list[1].args
  assert "+100 pips" in runner_render("vip")


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("sig", "bar", "fill", "pips"),
  [
    (
      _sell_signal(entry=3995, entry_end=3997, sl=4000),
      _bar("2026-07-08T10:00:00.000Z", 4004, 4005, 4003, 4004),
      "4,004",
      90,
    ),
    (
      _buy_signal(entry=3995, entry_end=3997, sl=3992),
      _bar("2026-07-08T10:00:00.000Z", 3988, 3989, 3987, 3988),
      "3,988",
      90,
    ),
  ],
  ids=["sell", "buy"],
)
async def test_sl_gap_books_at_open(monkeypatch, sig, bar, fill, pips):
  fanout = AsyncMock()
  monkeypatch.setattr(watcher, "fanout_update", fanout)
  progress = {"tp": 0, "sl": False, "runner_pips": 0}

  await watcher._evaluate(sig, bar, progress, atr=1.0)

  _, render = fanout.await_args.args
  assert f"Fill: <b>{fill}</b> (SL)" in render("vip")
  assert f"Loss: <b>-{pips} pips</b>" in render("vip")
  markup_fn = fanout.await_args.kwargs["markup_fn"]
  assert markup_fn("vip").inline_keyboard[0][0].callback_data == (
    f"c0:3:0:-{pips}"
  )


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("sig", "bar", "fill", "pips"),
  [
    (
      _sell_signal(entry=3995, entry_end=3997, sl=4005, tps=[3990]),
      _bar("2026-07-08T10:00:00.000Z", 3988, 3989, 3987, 3988),
      "3,988",
      70,
    ),
    (
      _buy_signal(entry=3995, entry_end=3997, sl=3990, tps=[4002]),
      _bar("2026-07-08T10:00:00.000Z", 4004, 4005, 4003, 4004),
      "4,004",
      70,
    ),
  ],
  ids=["sell", "buy"],
)
async def test_tp_gap_books_at_open(monkeypatch, sig, bar, fill, pips):
  fanout = AsyncMock()
  monkeypatch.setattr(watcher, "fanout_update", fanout)
  progress = {"tp": 0, "sl": False, "runner_pips": 0}

  await watcher._evaluate(sig, bar, progress, atr=1.0)

  _, render = fanout.await_args_list[0].args
  assert f"Fill: <b>{fill}</b> (TP1)" in render("vip")
  assert f"Profit: <b>+{pips} pips</b>" in render("vip")
  markup_fn = fanout.await_args_list[0].kwargs["markup_fn"]
  assert markup_fn("vip").inline_keyboard[0][0].callback_data == (
    f"c0:3:1:{pips}"
  )


def test_small_overshoot_is_omitted_from_alert():
  text = watcher._render_level_alert(
    "vip", "SL", "SL", 2, 4000.0, 50, 4000.2, atr=3.0
  )

  assert "Fill: <b>4,000</b> (SL)" in text
  assert "ran to" not in text


def test_card_and_watcher_share_conservative_entry_property():
  rng = random.Random(20260720)
  for action in ("BUY", "SELL"):
    for _ in range(25):
      entry = round(rng.uniform(1900, 4200), 1)
      entry_end = round(entry + rng.uniform(1, 5), 1)
      reference = entry_end if action == "BUY" else entry
      risk = round(rng.uniform(3, 15), 1)
      sl = reference - risk if action == "BUY" else reference + risk
      direction = 1 if action == "BUY" else -1
      tps = [reference + direction * risk * multiple for multiple in (1, 2, 3)]
      sig = {
        "daily_seq": 1,
        "symbol": "XAU",
        "action": action,
        "entry": entry,
        "entry_end": entry_end,
        "sl": sl,
        "tps": tps,
      }

      assert rr_entry(sig) == reference
      assert pips_between(sig, sl) == round(risk / pip_for("XAU"))
      card = broadcast.render_entry(sig, "vip")
      assert f"risk <b>{broadcast._price(risk, 'XAU')}</b>" in card
      for tp in tps:
        expected = round(abs(tp - reference) / pip_for("XAU"))
        assert pips_between(sig, tp) == expected
