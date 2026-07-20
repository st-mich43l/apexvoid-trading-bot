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


@pytest.mark.asyncio
async def test_pause_resume_and_status(monkeypatch):
  monkeypatch.setattr(auto_trade_ops.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(auto_trade_ops.settings, "auto_trade_dry_run", False)
  monkeypatch.setattr(auto_trade_ops.settings, "auto_trade_max_daily_trades", 6)
  monkeypatch.setattr(auto_trade_ops.settings, "auto_trade_fast_scalp_enabled", True)
  await auto_trade_ops.set_auto_trade_paused(True)
  client = redis_state.get_client()
  await client.set("auto_trade:last_fast_gate", '{"state":"weak_body"}')
  assert await client.get("auto_trade:paused") == "1"
  text = await auto_trade_ops.auto_trade_status_text()
  assert "demo trading" in text
  assert "paused" in text
  assert "0/6" in text
  assert "Fast M1 gate" in text
  assert "weak_body" in text
  await auto_trade_ops.set_auto_trade_paused(False)
  assert await client.get("auto_trade:paused") is None
