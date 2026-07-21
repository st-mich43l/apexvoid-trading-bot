import pytest

from app import auto_trade_ops, redis_state


def test_render_auto_trade_event_filters_noise_and_escapes_message():
  assert auto_trade_ops.render_auto_trade_event({
    "type": "rejected",
    "message": "ordinary candidate rejection",
  }) is None
  text = auto_trade_ops.render_auto_trade_event({
    "type": "opened",
    "message": "BUY <0.12> lots",
    "position_id": 91,
  })
  assert "Auto trade opened" in text
  assert "BUY &lt;0.12&gt; lots" in text
  assert "<code>91</code>" in text


def test_render_auto_trade_stop_and_warning_events():
  stop = auto_trade_ops.render_auto_trade_event({
    "type": "stop_moved",
    "message": "🛡 Auto trade stop → 4,029.49 (BE+3) · position 39016393",
    "position_id": 39016393,
  })
  warning = auto_trade_ops.render_auto_trade_event({
    "type": "warning",
    "message": "token grants live account 44669326 — re-authorize as demo only",
  })

  assert "Auto trade stop moved" in stop
  assert "BE+3" in stop
  assert "Auto Trader warning" in warning
  assert "live account 44669326" in warning


def test_render_scale_in_zone_and_group_events():
  scale_in = auto_trade_ops.render_auto_trade_event({
    "type": "add",
    "message": "Tranche 2 · 0.08 lots · exposure-bound",
  })
  zone = auto_trade_ops.render_auto_trade_event({
    "type": "zone_planned",
    "message": "two limits",
  })
  result = auto_trade_ops.render_auto_trade_event({
    "type": "group_result",
    "message": "realised $42 · no-add $31",
  })

  assert "scale-in" in scale_in
  assert "zone fill" in zone
  assert "group result" in result


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

  await auto_trade_ops._record_group_result(client, event)
  await auto_trade_ops._record_group_result(client, event)
  await auto_trade_ops._record_group_result(client, {
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
  monkeypatch.setattr(auto_trade_ops.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(auto_trade_ops.settings, "auto_trade_dry_run", False)
  monkeypatch.setattr(auto_trade_ops.settings, "auto_trade_max_daily_trades", 6)
  await auto_trade_ops.set_auto_trade_paused(True)
  client = redis_state.get_client()
  await client.set(
    "auto_trade:last_gate",
    '{"state":"waiting_rejection","rail":{"role":"support","low":4016.5,"high":4017.5}}',
  )
  assert await client.get("auto_trade:paused") == "1"
  text = await auto_trade_ops.auto_trade_status_text()
  assert "demo trading" in text
  assert "paused" in text
  assert "0/6" in text
  assert "Measured groups" in text
  assert "independent M1 range scalp · raw M5/M15 rails" in text
  assert "waiting_rejection" in text
  assert "support" in text
  assert "4,016.50–4,017.50" in text
  await auto_trade_ops.set_auto_trade_paused(False)
  assert await client.get("auto_trade:paused") is None
