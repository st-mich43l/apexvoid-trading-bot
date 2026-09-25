"""Unit tests for scalp vs reaction funnel helpers."""

from __future__ import annotations

import pytest

from app.autotrade.reaction_funnel import (
  BUCKET_REACTION,
  BUCKET_SCALP,
  funnel_bucket,
)
from app.autotrade.stats_ingestion import _complete_outcome


pytestmark = pytest.mark.no_database


def test_funnel_bucket_splits_scalp_and_reaction():
  assert funnel_bucket("Key Level") == BUCKET_REACTION
  assert funnel_bucket("Trendline") == BUCKET_REACTION
  assert funnel_bucket("Impulse Pullback Scalp", family="scalp") == BUCKET_SCALP
  assert funnel_bucket("Impulse Pullback Scalp", family="scalp") == BUCKET_SCALP
  assert funnel_bucket("Range Edge Scalp") == BUCKET_SCALP


def test_normalize_setup_type_aliases():
  from app.autotrade.reaction_funnel import (
    archetype_from_strategy,
    normalize_setup_type,
  )

  assert normalize_setup_type("momentum") == "Momentum Chase Scalp"
  assert normalize_setup_type("key-level") == "Key Level"
  assert normalize_setup_type("impulse pullback · add_momentum") == (
    "Impulse Pullback Scalp · add_momentum"
  )
  assert archetype_from_strategy("Impulse Pullback Scalp") == "impulse_pullback"
  assert archetype_from_strategy("Impulse Pullback Scalp") == "impulse_pullback"
  assert archetype_from_strategy("momentum") is None


@pytest.mark.parametrize(
  ("event", "expected"),
  [
    ({"type": "order_filled"}, "fill"),
    ({"type": "opened"}, "fill"),
    ({"type": "group_result", "reason_code": "group_stop_loss"}, "sl"),
    ({"type": "group_result", "reason_code": "take_profit"}, "tp"),
    ({"type": "group_result", "group_realized_pips": -12.5}, "sl"),
    ({"type": "group_result", "group_realized_pips": 40.0}, "tp"),
    ({
      "type": "position_closed",
      "reason_code": "manual_or_external_close",
      "group_realized_pips": 18.0,
      "message": "position closed at broker: manual or external order · winning 18.0 pips",
    }, "tp"),
    ({
      "type": "position_closed",
      "symbol": "XAU",
      "direction": "BUY",
      "price": 4630.96,
      "stop_loss": 4631.04,
      "target_pips": 31,
      "group_realized_pips": -60,
      "previous_state": "fully_open",
      "reason_code": "manual_or_external_close",
      "message": "PLAN CLOSED · highest TP archived TP1 · @ 4630.96",
    }, "sl"),
    ({"type": "setup_status"}, None),
  ],
)
def test_complete_outcome_classification(event, expected):
  assert _complete_outcome(event) == expected
