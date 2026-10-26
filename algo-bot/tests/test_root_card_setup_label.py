"""The root card names the zone it trades (SELL -> Supply, BUY -> Demand), never the shared "Supply Demand" identity."""

from __future__ import annotations

import pytest

from app.autotrade.setup_card import (
  card_setup_label,
  format_event_recovery_root_card,
  format_plan_published_root_card,
  forming_card_matches_strategy,
)
from app.autotrade.strategy_match import StrategyMatch

pytestmark = pytest.mark.no_database


def _match(strategy="Supply Demand", direction="SELL") -> StrategyMatch:
  return StrategyMatch(
    version=1, match_id="setup-label", symbol="XAU", source_tf="M5", event_ts="2026-07-31T13:03:00+00:00", issued_at=1_785_502_980,
    expires_at=1_785_503_400, strategy=strategy, strategy_mode="with_bias", bias_relationship="with_bias", direction=direction,
    key_level=4050.68, entry_low=4048.73, entry_high=4052.63, current_price=4051.6, confluence=2, reasons=("zone reaction",), atr=4.0,
    structure_swing=10.0, targets_pips=(20, 40, 60), tags=("zone",), structural_source="zone", structural_kind="zone", structural_timeframe="M5",
    reaction_type="rejection", htf_bias="up (H1)",
  )


@pytest.mark.parametrize("direction,shown", [("SELL", "Supply"), ("BUY", "Demand"), ("sell", "Supply"), ("buy", "Demand")])
def test_the_zone_strategy_is_shown_as_the_side_it_trades(direction, shown):
  assert card_setup_label("Supply Demand", direction) == shown


@pytest.mark.parametrize("strategy", ["Key Level", "Order Block", "FVG", "Confluence Zone", "Demand Zone Reaction", "Supply Zone Reaction"])
def test_every_other_strategy_keeps_its_name(strategy):
  assert card_setup_label(strategy, "BUY") == strategy and card_setup_label(strategy, "SELL") == strategy


def test_without_a_direction_the_identity_is_shown_not_a_guess():
  assert card_setup_label("Supply Demand", None) == "Supply Demand" and card_setup_label("Supply Demand", "") == "Supply Demand"
  assert card_setup_label(None, "BUY") == ""


@pytest.mark.parametrize("direction,icon,shown,other", [("SELL", "📉", "Supply", "Demand"), ("BUY", "📈", "Demand", "Supply")])
def test_the_published_root_card_reads_supply_for_sell_and_demand_for_buy(direction, icon, shown, other):
  card = format_plan_published_root_card(_match(direction=direction))
  body = next(line for line in card.splitlines() if f"<b>{direction} ·" in line)
  assert body.startswith(icon) and f"{direction} · {shown}</b>" in body
  assert "Supply Demand" not in card and f"· {other}</b>" not in card


def test_a_key_level_card_is_unchanged():
  assert "BUY · Key Level</b>" in format_plan_published_root_card(_match("Key Level", "BUY"))


@pytest.mark.parametrize("direction", ["SELL", "BUY"])
def test_a_card_matches_its_own_match_and_a_card_published_with_the_old_label_still_does(direction):
  match = _match(direction=direction)
  assert forming_card_matches_strategy(format_plan_published_root_card(match), match)
  old = "🤖 <b>ApexVoid Algo</b>\n🔎 <b>XAU M5 · SETUP FORMING</b>\n​\n" + ("📉" if direction == "SELL" else "📈") + f" <b>{direction} · Supply Demand</b> · ⭐⭐\n"
  assert forming_card_matches_strategy(old, match)


def test_a_card_of_another_setup_or_the_other_side_does_not_match():
  sell, buy = _match(direction="SELL"), _match(direction="BUY")
  assert not forming_card_matches_strategy(format_plan_published_root_card(sell), buy)
  assert not forming_card_matches_strategy(format_plan_published_root_card(_match("Key Level", "SELL")), sell)
  # "Supply" is a prefix of "Supply Zone Reaction": a different strategy's card must not read as this one
  other = "📉 <b>SELL · Supply Zone Reaction</b> · ⭐⭐\n"
  assert not forming_card_matches_strategy(other, sell)


@pytest.mark.parametrize("direction,shown", [("SELL", "Supply"), ("BUY", "Demand")])
def test_the_recovered_root_card_uses_the_same_label(direction, shown):
  card = format_event_recovery_root_card({"symbol": "XAU", "tf": "M5", "direction": direction, "strategy": "Supply Demand"})
  assert f"{direction} · {shown}</b>" in card and "Supply Demand" not in card
