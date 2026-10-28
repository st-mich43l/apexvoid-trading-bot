"""The closed-trade line: "🏆 closed • Achieved 105 pips • +2.7R 💸" (one chest for every win), "❌ closed • losing 21 pips • -1.0R" for a loss."""

from __future__ import annotations

import pytest

from app.autotrade import delivery

pytestmark = pytest.mark.no_database

WIN = "PLAN CLOSED · highest TP archived TP2 · @ 4106.00"


def _win(pips, stop=30):
  return delivery._format_position_closed_compact_line({"target_pips": pips, "stop_pips": stop}, WIN)


@pytest.mark.parametrize("pips,shown", [(15, "+0.5R"), (30, "+1.0R"), (60, "+2.0R"), (81, "+2.7R"), (90, "+3.0R"), (105, "+3.5R"), (300, "+10.0R")])
def test_every_win_has_one_chest_whatever_its_r_and_no_plus_on_the_pips(pips, shown):
  assert _win(pips) == f"🏆 closed • Achieved {pips} pips • {shown} 💸"


def test_a_win_never_has_more_than_one_chest():
  assert all(_win(p).count("💸") == 1 for p in (5, 30, 60, 120, 400))


def test_a_loss_is_a_cross_with_no_chest():
  line = delivery._format_position_closed_compact_line(
    {"group_realized_pips": -21, "stop_pips": 21}, "PLAN CLOSED · no TP archived · losing -21 pips · @ 4090.50")
  assert line == "❌ closed • losing 21 pips • -1.0R" and "💸" not in line


def test_breakeven_and_an_unconfirmed_close_keep_their_own_lines():
  flat = delivery._format_position_closed_compact_line({"group_realized_pips": 0, "stop_pips": 30}, "PLAN CLOSED · @ 4090.50")
  assert flat.startswith("➖ closed • breakeven") and "💸" not in flat
  assert delivery._format_position_closed_compact_line({}, "").startswith("🏁 closed • unconfirmed result")


def test_a_win_with_no_risk_on_the_event_has_no_r_but_still_its_chest():
  assert delivery._format_position_closed_compact_line({"target_pips": 90}, WIN) == "🏆 closed • Achieved 90 pips 💸"
