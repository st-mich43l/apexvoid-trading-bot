"""Required regression test C for the zone/M1 simplification refactor:
an ambiguous key level must never generate both a BUY and a SELL
opportunity, and must not resolve direction via an arbitrary
confluence-margin tiebreak - see key_level_reaction() in
app/analysis/detectors.py and docs/p0-simple-zone-m1-baseline-map.md
section 3.

Level/price values below were chosen empirically against the fixture
price action (not asserted from theory) to land in each of the three
deterministic branches: level below price, level above price, and price
inside the level's own band (proximal_band_atr=0.5 * atr=3 = 1.5).
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.analysis import detectors
from app.analysis.structure import Level, Zone


def _df(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
  index = pd.date_range("2026-07-10", periods=len(rows), freq="5min", tz="UTC")
  return pd.DataFrame(
    rows, columns=["open", "high", "low", "close", "volume"], index=index,
  )


def _series(df: pd.DataFrame, value: float) -> pd.Series:
  return pd.Series([value] * len(df), index=df.index)


def _ctx(
  df: pd.DataFrame,
  *,
  bias: str = "up",
  levels: list[Level],
  zones: list[Zone] | None = None,
) -> detectors.DetectionContext:
  tf = "M5"
  structure = detectors.StructureSet(
    swings=[], bias=bias, levels=levels, equal_levels=[], fvg_zones=[],
    order_blocks=[], breaks=[], zones=list(zones or []), liquidity_grabs=[],
    session_levels=[], dealing_range=None, trendlines=[],
  )
  return detectors.DetectionContext(
    symbol="XAU", tf=tf, frames={tf: df},
    indicators={tf: detectors.IndicatorSet(atr=_series(df, 3))},
    structures={tf: structure}, htf_bias=bias,
    settings=detectors.DetectorSettings(confluence_floor=2),
  )


def _flat_df(price: float) -> pd.DataFrame:
  # No wicks, no directional movement anywhere in the lookback window - a
  # genuinely flat market cannot confirm a reaction in either direction.
  return _df([(price, price, price, price, 100) for _ in range(5)])


def _buy_rejection_df() -> pd.DataFrame:
  # Last close (current price) = 109.
  return _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (107, 109, 103, 104, 100),
    (104, 106, 102, 103, 100),
    (106, 110, 101, 109, 100),
  ])


def _sell_rejection_df() -> pd.DataFrame:
  # Last close (current price) = 103.
  return _df([
    (110, 112, 108, 110, 100),
    (109, 110, 101, 102, 100),
    (102, 107, 100, 106, 100),
    (106, 108, 104, 107, 100),
    (107, 112, 101, 103, 100),
  ])


def test_ambiguous_level_with_no_reaction_produces_no_opportunity():
  flat = _flat_df(105.0)
  level = Level(105.0, "reaction", touches=3, strength=3)

  result = detectors.key_level_reaction(_ctx(flat, bias="up", levels=[level]))

  assert result is None


def test_ambiguous_level_with_bullish_reclaim_produces_exactly_one_buy():
  # price=109, band=[107.5, 110.5] - 108.5 sits inside the level's own
  # band, so direction comes from whichever side confirms, not position.
  buy_df = _buy_rejection_df()
  level = Level(108.5, "reaction", touches=3, strength=3)

  result = detectors.key_level_reaction(_ctx(buy_df, bias="down", levels=[level]))

  assert result is not None
  assert result.direction == "BUY"
  assert result.key_level_role == "ambiguous"


def test_ambiguous_level_with_bearish_reclaim_produces_exactly_one_sell_in_a_separate_episode():
  # price=103, band=[101.5, 104.5] - 103.4 sits inside the level's own
  # band, same "inside the band" shape as the BUY case above, but this
  # fixture's actual price action only confirms SELL there.
  sell_df = _sell_rejection_df()
  level = Level(103.4, "reaction", touches=3, strength=3)

  result = detectors.key_level_reaction(_ctx(sell_df, bias="up", levels=[level]))

  assert result is not None
  assert result.direction == "SELL"
  assert result.key_level_role == "ambiguous"
  # A distinct underlying level/price action produces a distinct
  # structural_id - never the same episode as the BUY case above.
  buy_df = _buy_rejection_df()
  buy_level = Level(108.5, "reaction", touches=3, strength=3)
  buy_result = detectors.key_level_reaction(
    _ctx(buy_df, bias="down", levels=[buy_level]),
  )
  assert buy_result is not None
  assert buy_result.structural_id != result.structural_id


def test_level_deterministically_below_price_never_evaluates_sell():
  # Level clearly below current price (109) and outside its own band -
  # support hypothesis only, never a confluence-margin coin flip.
  buy_df = _buy_rejection_df()
  level = Level(105.0, "reaction", touches=3, strength=3)

  result = detectors.key_level_reaction(_ctx(buy_df, bias="down", levels=[level]))

  assert result is not None
  assert result.direction == "BUY"


def test_level_deterministically_above_price_never_evaluates_buy():
  # Level clearly above current price (103) and outside its own band -
  # resistance hypothesis only.
  sell_df = _sell_rejection_df()
  level = Level(107.0, "reaction", touches=3, strength=3)

  result = detectors.key_level_reaction(_ctx(sell_df, bias="up", levels=[level]))

  assert result is not None
  assert result.direction == "SELL"


def _naive_buy_but_actually_sell_df() -> pd.DataFrame:
  # level=100, band=[98.5, 101.5]. Current price (last close) = 101.8, just
  # above the band - price-position alone says "support, BUY". But bar 1
  # (a sweep of the band's low then a strong bearish reclaim back through
  # the top) only confirms SELL, never BUY - verified directly against
  # evaluate_structural_reaction. Price is kept close to the band (not far
  # above it) deliberately: _entry_valid_for_settings/_level_valid both
  # require a SELL's price to sit at-or-below the zone it is reacting off
  # of, so the fixture must land price just past the level, not deep into
  # a runaway breakout, for the contradicting SELL to ever be reachable.
  return _df([
    (100, 101, 99, 100, 100),
    (100, 103, 97, 98, 100),
    (99, 102.3, 98.5, 101.8, 100),
  ])


def test_naive_position_guess_alone_finds_nothing_without_zone_context():
  # Without any zone telling the detector otherwise, price-position is all
  # it has - "BUY" is assumed and, since this fixture's real reaction at
  # the band is bearish, that assumed BUY never confirms. No opportunity.
  df = _naive_buy_but_actually_sell_df()
  level = Level(100.0, "reaction", touches=3, strength=3)

  result = detectors.key_level_reaction(_ctx(df, bias="down", levels=[level]))

  assert result is None


def test_opposing_supply_zone_unlocks_the_sell_the_naive_guess_missed():
  # key_levels() (levels.py) only ever produces kind="reaction"/"round" -
  # never an explicit support/resistance label - so without zone context
  # classify_key_level_role falls through to "price above the level ->
  # assume support -> BUY" with no awareness that a real supply zone sits
  # right at this level. Owner's complaint, reproduced: this used to try
  # BUY (and fail, per the test above) instead of recognizing the SELL a
  # real opposing zone - and the actual price action - supported.
  df = _naive_buy_but_actually_sell_df()
  level = Level(100.0, "reaction", touches=3, strength=3)
  supply_zone = Zone(98.0, 102.0, "supply")

  result = detectors.key_level_reaction(
    _ctx(df, bias="down", levels=[level], zones=[supply_zone]),
  )

  assert result is not None
  assert result.direction == "SELL"
  # 2026-09 (Opposing Structure V2 repair, §15/16): the SAME zone that
  # unlocked SELL here is threaded onto the result as its own telemetry,
  # distinct from (but sourced from the same structural authority as) the
  # later room-level opposing_* fields evaluate_structural_target_room
  # computes at the actionability stage.
  assert result.key_level_opposing_zone_low == 98.0
  assert result.key_level_opposing_zone_high == 102.0
  assert result.key_level_opposing_zone_side == "supply"


def test_mitigated_opposing_zone_does_not_unlock_the_other_direction():
  # A zone that already broke (mitigated) is dead structure - it must not
  # contradict the naive guess the same way a live zone does.
  df = _naive_buy_but_actually_sell_df()
  level = Level(100.0, "reaction", touches=3, strength=3)
  dead_supply_zone = Zone(98.0, 102.0, "supply", mitigated=True)

  result = detectors.key_level_reaction(
    _ctx(df, bias="down", levels=[level], zones=[dead_supply_zone]),
  )

  assert result is None
