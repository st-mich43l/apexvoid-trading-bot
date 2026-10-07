"""Tests for auto-trade funnel diagnostics."""

from __future__ import annotations

import pytest

from app.autotrade.funnel_diagnostics import auto_trade_funnel_text
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


@pytest.mark.asyncio
async def test_funnel_text_renders_stages_exits_and_block_reasons():
  client = redis_state.get_client()
  await client.hset(
    "auto_trade:metrics:XAU",
    mapping={
      "strategy_match_checking": 100,
      "strategy_match_candidate_published": 25,
      "v8_plan_published": 20,
      "funnel_fill": 10,
      "strategy_match_waiting": 40,
      "strategy_match_blocked": 30,
      "strategy_match_blocked:confluence_below_minimum": 5,
      "strategy_match_blocked:strategy_disabled": 3,
      "strategy_match_arbitration_suppressed": 12,
      "strategy_match_expired": 7,
      "target_room_rejected": 2,
    },
  )

  text = await auto_trade_funnel_text("XAU")

  assert "Algo funnel — XAU" in text
  assert "checked: <b>100</b>" in text
  assert "candidate_published: <b>25</b> (25% of prior)" in text
  assert "plan_published: <b>20</b> (80% of prior)" in text
  assert "filled: <b>10</b> (50% of prior)" in text
  assert "arbitration_suppressed: <b>12</b>" in text
  assert "confluence_below_minimum: 5" in text


@pytest.mark.asyncio
async def test_funnel_text_is_zero_filled_without_metrics():
  text = await auto_trade_funnel_text("EURUSD")

  assert "Algo funnel — EURUSD" in text
  assert "checked: <b>0</b>" in text
