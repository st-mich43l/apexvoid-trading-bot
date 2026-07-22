import pytest

from app.autotrade import delivery
from app.persistence import redis_state


def test_render_auto_trade_event_filters_noise_and_escapes_message():
  assert delivery.render_auto_trade_event({
    "type": "rejected",
    "message": "ordinary candidate rejection",
  }) is None
  text = delivery.render_auto_trade_event({
    "type": "opened",
    "message": "BUY <0.12> lots",
    "position_id": 91,
  })
  assert "ApexVoid Algo" in text
  assert "Position opened" in text
  assert "BUY &lt;0.12&gt; lots" in text
  assert "<code>91</code>" in text
  assert "auto trade" not in text.lower()


def test_render_box_open_and_full_tp_as_shareable_cards():
  opened = delivery.render_auto_trade_event({
    "type": "opened",
    "message": (
      "Sell 0.04 lots filled 4,066.78, SL 4,070.63 · 39p structure · "
      "full TP 50p · range 4,062.00-4,069.00 · risk-bound"
    ),
    "position_id": 39025496,
  })
  take_profit = delivery.render_auto_trade_event({
    "type": "take_profit",
    "message": "FULL TP +50 pips closed volume 400",
    "position_id": 39025496,
    "price": 4061.78,
  })

  assert "XAU SELL opened" in opened
  assert "Entry: <b>4,066.78</b>" in opened
  assert "SL: <b>4,070.63</b> · 39 pips" in opened
  assert "Full TP: <b>4,061.78</b> · +50 pips" in opened
  assert "Box: <b>4,062.00–4,069.00</b>" in opened
  assert opened.count("39025496") == 1
  assert "FULL TAKE PROFIT" in take_profit
  assert "Profit: <b>+50 pips</b>" in take_profit
  assert "Position closed in full" in take_profit
  assert "Auto trade" not in (opened + take_profit)


def test_opened_event_renders_strategy_attribution():
  opened = delivery.render_auto_trade_event({
    "type": "opened",
    "message": (
      "Sell 0.04 lots filled 4,066.78, SL 4,070.63 · 39p structure · "
      "full TP 50p · range 4,062.00-4,069.00 · risk-bound"
    ),
    "position_id": 39025496,
    "candidate_id": "a" * 64,
    "setup": "Range Box Scalp",
    "regime": "chop",
    "confluence": 3,
  })

  assert "Range Box Scalp" in opened
  assert "chop" in opened
  assert "★★★" in opened


def test_opened_event_without_attribution_degrades_gracefully():
  opened = delivery.render_auto_trade_event({
    "type": "opened",
    "message": "Sell 0.04 lots filled 4,066.78, SL 4,070.63 · 39p structure · legacy",
    "position_id": 1,
  })
  assert opened is not None
  assert "🧭" not in opened


def test_render_auto_trade_stop_and_warning_events():
  stop = delivery.render_auto_trade_event({
    "type": "stop_moved",
    "message": "🛡 Auto trade stop → 4,029.49 (BE+3) · position 39016393",
    "position_id": 39016393,
  })
  warning = delivery.render_auto_trade_event({
    "type": "warning",
    "message": "token grants live account 44669326 — re-authorize as demo only",
  })

  assert "ApexVoid Algo" in stop
  assert "Risk protected" in stop
  assert "SL moved to <b>4,029.49</b>" in stop
  assert "BE+3" in stop
  assert "Warning" in warning
  assert "live account 44669326" in warning
  assert "auto trade" not in (stop + warning).lower()


def test_render_scale_in_zone_and_group_events():
  scale_in = delivery.render_auto_trade_event({
    "type": "add",
    "message": "Tranche 2 · 0.08 lots · exposure-bound",
  })
  zone = delivery.render_auto_trade_event({
    "type": "zone_planned",
    "message": "two limits",
  })
  result = delivery.render_auto_trade_event({
    "type": "group_result",
    "message": "realised $42 · no-add $31",
  })

  assert "Scale-in filled" in scale_in
  assert "Entry plan ready" in zone
  assert "Trade result" in result
  assert "ApexVoid Algo" in scale_in + zone + result


@pytest.mark.asyncio
async def test_group_stats_split_adds_and_deduplicate():
  client = redis_state.get_client()
  event = {
    "type": "group_result",
    "group_id": "group-a",
    "had_adds": True,
    "group_realized_pnl": 42,
    "counterfactual_pnl": 31,
    "group_realized_pips": 84,
    "counterfactual_pips": 73,
  }

  await delivery._record_group_result(client, event)
  await delivery._record_group_result(client, event)
  await delivery._record_group_result(client, {
    "type": "group_result",
    "group_id": "group-b",
    "had_adds": False,
    "group_realized_pnl": 7,
  })

  stats = await client.hgetall("auto_trade:stats")
  assert stats["groups"] == "2"
  assert stats["with_adds"] == "1"
  assert stats["without_adds"] == "1"
  assert float(stats["realized_pnl"]) == 49
  assert float(stats["add_delta_pnl"]) == 11
  assert float(stats["realized_pips"]) == 84
  assert float(stats["counterfactual_pips"]) == 73
  assert stats["adds_improved"] == "1"


@pytest.mark.asyncio
async def test_pause_resume_and_status(monkeypatch):
  monkeypatch.setattr(delivery.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(delivery.settings, "auto_trade_dry_run", False)
  await delivery.set_auto_trade_paused(True)
  client = redis_state.get_client()
  await client.set(
    "auto_trade:last_gate",
    '{"state":"waiting_rejection","box":{"low":4016.5,"high":4024.5},"full_tp_pips":70}',
  )
  assert await client.get("auto_trade:paused") == "1"
  text = await delivery.auto_trade_status_text()
  assert "demo trading" in text
  assert "paused" in text
  assert "Trades today: <b>0</b> · <b>unlimited</b>" in text
  assert "Measured groups" in text
  assert "ApexVoid Algo" in text
  assert "independent M1 two-edge box scalp" in text
  assert "waiting_rejection" in text
  assert "box 4,016.50–4,024.50" in text
  assert "full TP 70p" in text
  assert "auto trader" not in text.lower()
  await delivery.set_auto_trade_paused(False)
  assert await client.get("auto_trade:paused") is None
