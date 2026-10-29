"""The root card names the structure it trades (fvg v6): "Structure: FVG M15 · low–high · formed HH:MM UTC", plus the order zone when it differs.

Go publishes an optional ``structure`` block for the zone family (supply, demand, order_block, fvg) on every timeframe. The card reads it for display only:
strategy identity, stats keys, policy and results do not, and an event without the block renders exactly as before.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

from pydantic import ValidationError

from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.autotrade import go_opportunity_policy as pol
from app.autotrade.setup_card import forming_card_matches_strategy, format_plan_published_root_card
from app.autotrade.strategy_match import StrategyMatch
from tests.test_go_opportunity_policy import golden


FORMED_AT = int(datetime(2026, 10, 14, 14, 15, tzinfo=timezone.utc).timestamp())


def _match(strategy="FVG", direction="BUY", structure=("M15", 4152.30, 4154.40, FORMED_AT), **overrides) -> StrategyMatch:
  base = StrategyMatch(
    version=1, match_id="structure-card", symbol="XAU", source_tf="M5", event_ts="2026-10-14T14:35:00+00:00", issued_at=1_791_988_500,
    expires_at=1_791_990_000, strategy=strategy, strategy_mode="with_bias", bias_relationship="with_bias", direction=direction,
    key_level=4153.35, entry_low=4152.30, entry_high=4154.40, current_price=4160.0, confluence=2, reasons=("zone reaction",), atr=4.0,
    structure_swing=10.0, targets_pips=(20, 40, 60), tags=("zone",), structural_source="zone", structural_kind="zone", structural_timeframe="M5",
    reaction_type="rejection", htf_bias="up (H1)",
  )
  if structure is not None:
    timeframe, low, high, formed_at = structure
    base = dataclasses.replace(base, structure_timeframe=timeframe, structure_low=low, structure_high=high, structure_formed_at=formed_at)
  return dataclasses.replace(base, **overrides)


def _structure_lines(card: str) -> list[str]:
  return [line for line in card.splitlines() if line.startswith(("Structure:", "Order zone:"))]


def test_an_fvg_card_names_the_gap_and_its_timeframe(timeframe):
  card = format_plan_published_root_card(_match(structure=(timeframe, 4152.30, 4154.40, FORMED_AT)))
  assert _structure_lines(card) == [f"Structure: FVG {timeframe} · 4,152.30–4,154.40 · formed 14:15 UTC"]


def test_the_structure_line_sits_directly_under_the_setup_name():
  lines = format_plan_published_root_card(_match()).splitlines()
  setup = next(i for i, line in enumerate(lines) if "BUY · FVG</b>" in line)
  assert lines[setup + 1].startswith("Structure: FVG M15")


  "strategy,direction,name",
  [("Supply Demand", "SELL", "Supply"), ("Supply Demand", "BUY", "Demand"), ("Order Block", "BUY", "Order Block"), ("Order Block", "SELL", "Order Block")],
)
def test_the_other_zone_strategies_use_their_own_name(strategy, direction, name):
  card = format_plan_published_root_card(_match(strategy, direction, structure=("H1", 4180.0, 4186.5, FORMED_AT), entry_low=4180.0, entry_high=4186.5))
  assert _structure_lines(card) == [f"Structure: {name} H1 · 4,180.00–4,186.50 · formed 14:15 UTC"]
  assert "Supply Demand" not in card


def test_the_price_uses_the_instruments_digits():
  card = format_plan_published_root_card(_match(
    symbol="EURUSD", entry_low=1.16470, entry_high=1.16520, current_price=1.1700, atr=0.0008, key_level=1.1650,
    structure_low=1.16432, structure_high=1.16520, structure_timeframe="M15",
  ))
  assert "Structure: FVG M15 · 1.16432–1.16520 · formed 14:15 UTC" in card


def test_the_order_zone_line_appears_only_when_the_order_zone_differs_from_the_structure():
  same = format_plan_published_root_card(_match())
  assert not any(line.startswith("Order zone:") for line in same.splitlines())

  clipped = format_plan_published_root_card(_match(entry_low=4153.60, entry_high=4154.40))
  assert _structure_lines(clipped) == [
    "Structure: FVG M15 · 4,152.30–4,154.40 · formed 14:15 UTC",
    "Order zone: 4,153.60–4,154.40",
  ]


def test_the_order_zone_is_the_zone_the_plan_trades():
  # the entry span the published plan holds wins over the match's own band
  card = format_plan_published_root_card(_match(), entry_span=(4150.00, 4156.00))
  assert "Order zone: 4,150.00–4,156.00" in card
  card = format_plan_published_root_card(_match(entry_low=4153.60, entry_high=4154.40), entry_span=(4152.30, 4154.40))
  assert not any(line.startswith("Order zone:") for line in card.splitlines())


def test_an_event_without_the_block_renders_exactly_as_before():
  without = format_plan_published_root_card(_match(structure=None))
  assert _structure_lines(without) == []
  with_block = format_plan_published_root_card(_match())
  assert [line for line in with_block.splitlines() if not line.startswith("Structure:")] == without.splitlines()


def test_a_partial_or_non_finite_block_renders_nothing(bad):
  assert _structure_lines(format_plan_published_root_card(_match(**bad))) == []


def test_without_a_formation_time_the_line_has_no_time():
  card = format_plan_published_root_card(_match(structure_formed_at=None))
  assert _structure_lines(card) == ["Structure: FVG M15 · 4,152.30–4,154.40"]


def test_the_display_block_changes_no_identity_and_the_card_still_matches_its_setup():
  with_block, without = _match(), _match(structure=None)
  assert with_block.match_id == without.match_id and with_block.strategy == without.strategy
  assert forming_card_matches_strategy(format_plan_published_root_card(with_block), with_block)
  # a card published before this change (no structure line) still belongs to the same setup
  assert forming_card_matches_strategy(format_plan_published_root_card(without), with_block)


def test_the_match_round_trips_with_and_without_the_block():
  _, event = _event({"timeframe": "M15", "low": 4331.0, "high": 4333.0, "formed_at": FORMED_AT})
  match = pol.build_strategy_match(event, profile=pol.REVIEWED_SCOPES["fvg"], now=event.payload.created_at + 1)
  assert match is not None and StrategyMatch.from_json(match.to_json()) == match
  # a match cached before this change has none of the four keys and must still read back
  old = json.loads(match.to_json())
  for key in ("structure_timeframe", "structure_low", "structure_high", "structure_formed_at"):
    old.pop(key)
  parsed = StrategyMatch.from_json(json.dumps(old))
  assert parsed is not None and parsed.structure_timeframe is None and parsed.structure_low is None


def _event(structure=None, **overrides):
  raw = golden()
  payload = raw["payload"]
  payload.update({
    "strategy": "fvg", "direction": "BUY", "timeframe": "M5", "structure_timeframe": "M15",
    "entry": {"low": 4330.0, "high": 4333.5}, "invalidation": {"price": 4327.0}, "targets": [{"price": {"price": 4341.0}}],
    "evidence": [{"code": "m5_fvg_zone_confirmed"}, {"code": "htf_zone_m15"}],
  })
  if structure is not None:
    payload["structure"] = structure
  payload.update(overrides)
  return raw, parse_analysis_event(OpportunityTopic, json.dumps(raw))


def test_the_envelope_parses_with_and_without_the_block():
  _, old = _event()
  assert old.payload.structure is None
  _, new = _event({"timeframe": "M15", "low": 4331.0, "high": 4333.0, "formed_at": FORMED_AT})
  assert (new.payload.structure.timeframe, new.payload.structure.low, new.payload.structure.high, new.payload.structure.formed_at) == ("M15", 4331.0, 4333.0, FORMED_AT)


def test_a_malformed_block_is_rejected(block):
    _event(block)


def test_the_policy_copies_the_block_to_the_match_and_the_card_shows_it():
  _, event = _event({"timeframe": "M15", "low": 4331.0, "high": 4333.0, "formed_at": FORMED_AT})
  match = pol.build_strategy_match(event, profile=pol.REVIEWED_SCOPES["fvg"], now=event.payload.created_at + 1)
  assert match is not None
  assert (match.structure_timeframe, match.structure_low, match.structure_high, match.structure_formed_at) == ("M15", 4331.0, 4333.0, FORMED_AT)
  card = format_plan_published_root_card(match)
  assert "Structure: FVG M15 · 4,331.00–4,333.00 · formed 14:15 UTC" in card
  assert "Order zone: 4,330.00–4,333.50" in card


def test_an_event_without_the_block_builds_a_match_without_one():
  _, event = _event()
  match = pol.build_strategy_match(event, profile=pol.REVIEWED_SCOPES["fvg"], now=event.payload.created_at + 1)
  assert match is not None and match.structure_timeframe is None and match.structure_low is None
  assert _structure_lines(format_plan_published_root_card(match)) == []
