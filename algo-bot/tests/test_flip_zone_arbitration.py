from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from app.analysis import detectors
from app.analysis.key_level_role import (
  ROLE_AMBIGUOUS,
  ROLE_BROKEN_RESISTANCE,
  ROLE_BROKEN_SUPPORT,
  ROLE_RESISTANCE,
  ROLE_SUPPORT,
)
from app.analysis.structure import Level, Zone
from app.autotrade.strategy_match import StrategyMatch

pytestmark = pytest.mark.no_database


def _context(*, zone: Zone, level: Level, fallback: bool = False):
  index = pd.date_range(
    "2026-09-03T08:00:00Z", periods=5, freq="5min",
  )
  df = pd.DataFrame(
    [
      (100.0, 101.0, 99.0, 100.0),
      (100.0, 103.0, 99.0, 102.0),
      (102.0, 105.0, 101.0, 104.0),
      (104.0, 106.0, 103.0, 105.0),
      (105.0, 107.0, 104.0, 105.0),
    ],
    columns=["open", "high", "low", "close"],
    index=index,
  )
  structure = detectors.StructureSet(
    swings=[],
    bias="up",
    levels=[level],
    equal_levels=[],
    fvg_zones=[],
    order_blocks=[],
    zones=[zone],
  )
  settings = detectors.DetectorSettings(
    confluence_floor=1,
    zone_reaction_fallback_enabled=fallback,
  )
  return detectors.DetectionContext(
    symbol="XAU",
    tf="M5",
    frames={"M5": df},
    indicators={"M5": detectors.IndicatorSet(
      atr=pd.Series([2.0] * len(df), index=df.index),
    )},
    structures={"M5": structure},
    htf_bias="up",
    settings=settings,
  )


def _confirmation():
  return SimpleNamespace(
    confirmation_type="wick_rejection",
    touch_bar_ts="2026-09-03T08:15:00+00:00",
    confirmation_bar_ts="2026-09-03T08:20:00+00:00",
  )


def _patch_role(monkeypatch, role: str):
  monkeypatch.setattr(
    detectors,
    "classify_key_level_role",
    lambda **_kwargs: SimpleNamespace(role=role),
  )
  monkeypatch.setattr(
    detectors,
    "evaluate_structural_reaction",
    lambda *args, **kwargs: _confirmation(),
  )


def _flip_context(side: str):
  if side == "demand":
    return _context(
      zone=Zone(100.0, 102.5, "demand", source="flip_zone", score=10.0),
      level=Level(100.0, kind="reaction", touches=3, band=1.0),
    )
  return _context(
    zone=Zone(102.5, 105.0, "supply", source="flip_zone", score=10.0),
    level=Level(105.0, kind="reaction", touches=3, band=1.0),
  )


def test_demand_flip_requires_broken_resistance_and_carries_role(monkeypatch):
  _patch_role(monkeypatch, ROLE_BROKEN_RESISTANCE)

  result = detectors.flip_demand_zone_reaction(_flip_context("demand"))

  assert result is not None
  assert result.direction == "BUY"
  assert result.key_level_role == ROLE_BROKEN_RESISTANCE


def test_demand_flip_rejects_intact_support_and_counts(monkeypatch):
  events = []
  _patch_role(monkeypatch, ROLE_SUPPORT)
  ctx = replace(_flip_context("demand"), metric_sink=lambda *event: events.append(event))

  assert detectors.flip_demand_zone_reaction(ctx) is None
  assert events == [("flip_zone_role_contradiction", "XAU", {"tf": "M5"})]


def test_supply_flip_requires_broken_support_and_carries_role(monkeypatch):
  _patch_role(monkeypatch, ROLE_BROKEN_SUPPORT)

  result = detectors.flip_supply_zone_reaction(_flip_context("supply"))

  assert result is not None
  assert result.direction == "SELL"
  assert result.key_level_role == ROLE_BROKEN_SUPPORT


def test_supply_flip_rejects_intact_resistance_where_key_level_sell_owns_it(
  monkeypatch,
):
  _patch_role(monkeypatch, ROLE_RESISTANCE)
  ctx = _flip_context("supply")

  assert detectors.flip_supply_zone_reaction(ctx) is None
  key_level = detectors.key_level_reaction(ctx)
  assert key_level is not None
  assert key_level.direction == "SELL"
  # The role belongs to Key Level, not an unbroken supply flip.
  assert detectors._flip_role_agrees(ROLE_RESISTANCE, "SELL") is False


@pytest.mark.parametrize("side", ["demand", "supply"])
def test_ambiguous_flip_is_rejected_even_without_explicit_role_gate(
  monkeypatch, side,
):
  events = []
  _patch_role(monkeypatch, ROLE_AMBIGUOUS)
  settings = replace(
    _flip_context(side).settings,
    key_level_require_explicit_role=False,
  )
  ctx = replace(
    _flip_context(side),
    settings=settings,
    metric_sink=lambda *event: events.append(event),
  )

  detector = (
    detectors.flip_demand_zone_reaction
    if side == "demand" else detectors.flip_supply_zone_reaction
  )
  assert detector(ctx) is None
  assert events[0][0] == "flip_zone_role_contradiction"


def test_unresolvable_flip_anchor_counts_and_returns_none(monkeypatch):
  events = []
  ctx = replace(
    _context(
      zone=Zone(103.0, 105.0, "demand", source="flip_zone"),
      level=Level(100.0, kind="reaction", band=1.0),
    ),
    metric_sink=lambda *event: events.append(event),
  )

  assert detectors.flip_demand_zone_reaction(ctx) is None
  assert events == [("flip_zone_level_unresolved", "XAU", {"tf": "M5"})]


@pytest.mark.parametrize(
  ("role", "demand_allowed", "supply_allowed"),
  [
    (ROLE_SUPPORT, False, False),
    (ROLE_RESISTANCE, False, False),
    (ROLE_AMBIGUOUS, False, False),
    (ROLE_BROKEN_RESISTANCE, True, False),
    (ROLE_BROKEN_SUPPORT, False, True),
  ],
)
def test_key_level_and_flip_are_mutually_exclusive_by_role(
  monkeypatch, role, demand_allowed, supply_allowed,
):
  _patch_role(monkeypatch, role)
  demand = detectors.flip_demand_zone_reaction(_flip_context("demand"))
  supply = detectors.flip_supply_zone_reaction(_flip_context("supply"))
  key_demand = detectors.key_level_reaction(_flip_context("demand"))
  key_supply = detectors.key_level_reaction(_flip_context("supply"))

  assert (demand is not None) is demand_allowed
  assert (supply is not None) is supply_allowed
  assert not (demand is not None and supply is not None)
  assert not (key_demand is not None and demand is not None)
  assert not (key_supply is not None and supply is not None)


def test_zone_reaction_does_not_consult_flip_role_gate(monkeypatch):
  def fail_if_called(**_kwargs):
    raise AssertionError("Zone Reaction must not consult flip role gate")

  monkeypatch.setattr(detectors, "classify_key_level_role", fail_if_called)
  monkeypatch.setattr(
    detectors,
    "evaluate_structural_reaction",
    lambda *args, **kwargs: _confirmation(),
  )
  ctx = _context(
    zone=Zone(100.0, 104.0, "demand", source="supply_demand", score=10.0),
    level=Level(100.0, kind="reaction", band=1.0),
    fallback=True,
  )

  result = detectors.demand_zone_reaction(ctx)

  assert result is not None
  assert result.key_level_role is None


def test_flip_and_key_level_share_the_same_role_band_helper():
  ctx = _flip_context("demand")
  level = ctx.structures["M5"].levels[0]
  atr = float(ctx.indicators["M5"].atr.iloc[-1])

  assert detectors._key_level_reaction_band(level, ctx, atr) == max(
    float(level.band),
    ctx.settings.proximal_band_atr * atr,
  )


def _match(
  strategy: str,
  *,
  low: float,
  high: float,
  match_id: str,
) -> StrategyMatch:
  return StrategyMatch(
    version=1,
    match_id=match_id,
    symbol="XAU",
    source_tf="M5",
    event_ts="bar-100",
    issued_at=100,
    expires_at=200,
    strategy=strategy,
    strategy_mode="with_trend",
    direction="BUY",
    key_level=100.0,
    entry_low=low,
    entry_high=high,
    current_price=102.0,
    confluence=3,
    reasons=(),
    atr=2.0,
    structure_swing=low,
    targets_pips=(30, 60),
    structural_source=("key_level" if strategy == "Key Level" else "flip_zone"),
    structural_zone_low=low,
    structural_zone_high=high,
  )


def test_same_bar_overlapping_flip_is_superseded_by_key_level():
  from app.analysis.scanner import _arbitrate_flip_zone_matches

  key = _match("Key Level", low=100.0, high=104.0, match_id="key")
  flip = _match("Flip Zone", low=101.0, high=103.0, match_id="flip")

  kept, events = _arbitrate_flip_zone_matches(
    [flip, key], overlap_threshold=0.5,
  )

  assert [item.match_id for item in kept] == ["key"]
  assert events == [{
    "match_id": "flip",
    "event": "flip_zone_superseded_by_key_level",
    "strategy": "Flip Zone",
  }]
