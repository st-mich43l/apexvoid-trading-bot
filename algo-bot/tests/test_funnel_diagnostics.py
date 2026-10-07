"""Tests for auto-trade funnel diagnostics."""

from __future__ import annotations

import pytest

from app.autotrade.funnel_diagnostics import auto_trade_funnel_text
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


@pytest.mark.asyncio
async def test_funnel_text_renders_stages_and_block_reasons():
  client = redis_state.get_client()
  key = "auto_trade:metrics:XAU"
  await client.hset(
    key,
    mapping={
      "key_level_reaction_detected": 100,
      "zone_reaction_detected": 40,
      "scanner_setup_actionable": 80,
      "scanner_actionability_gated:opposing_entry_overlap": 12,
      "scanner_actionability_gated:execution_cost_insufficient_room": 8,
      "candidate_published": 25,
      "strategy_match_blocked:confluence_below_minimum": 5,
      "strategy_match_blocked:strategy_disabled": 3,
      "funnel_zone_discovered": 20,
      "static_eligibility_blocked": 4,
      "activation_allowed": 10,
      "activation_blocked:reaction_trigger_missing": 7,
      "activation_blocked:quote_outside_zone": 2,
      "v8_plan_published": 6,
      "target_room_rejected": 2,
    },
  )

  text = await auto_trade_funnel_text("XAU")
  assert "Algo funnel — XAU" in text
  assert "detected: <b>140</b>" in text
  assert "actionable: <b>80</b>" in text
  assert "match_published: <b>25</b>" in text
  assert "activation_allowed: <b>10</b>" in text
  assert "opposing_entry_overlap: 12" in text
  assert "reaction_trigger_missing: 7" in text
