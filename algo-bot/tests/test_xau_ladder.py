from app.autotrade import xau_ladder


def test_entry_leg_prices_sell_uses_low_shallow_and_midpoint_deep():
  # Matches this session's own worked example: XAU SELL, zone 4341-4344.
  prices = xau_ladder.entry_leg_prices("SELL", 4341.0, 4344.0, 4346.0)
  assert prices.shallow == 4341.0
  assert prices.deep == 4342.5


def test_entry_leg_prices_buy_uses_high_shallow_and_midpoint_deep():
  prices = xau_ladder.entry_leg_prices("BUY", 4048.73, 4052.63, 4045.0)
  assert prices.shallow == 4052.63
  assert prices.deep == 4050.68           # midpoint, rounded to the instrument's 2 digits


def test_entry_leg_prices_degenerate_zone_uses_stop_midpoint():
  # ctrader-engine's own confirmed live example: BUY 4390, SL 4384 -> Deep 4387.
  prices = xau_ladder.entry_leg_prices("BUY", 4390.0, 4390.0, 4384.0)
  assert prices.shallow == 4390.0
  assert prices.deep == 4387.0


def test_risk_leg_price_rests_on_the_entry_side_of_stop():
  # SELL: stop above entry, risk leg 15 pips BELOW the stop (entry side).
  assert xau_ladder.risk_leg_price("SELL", 4346.0, 0.1) == 4344.5
  # BUY: stop below entry, risk leg 15 pips ABOVE the stop (entry side).
  assert xau_ladder.risk_leg_price("BUY", 4045.0, 0.1) == 4046.5


def test_risk_leg_volume_is_equity_tiered():
  assert xau_ladder.risk_leg_volume(500.0) == 0.02
  assert xau_ladder.risk_leg_volume(999.99) == 0.02
  assert xau_ladder.risk_leg_volume(1_000.0) == 0.05
  assert xau_ladder.risk_leg_volume(50_000.0) == 0.05


def test_build_ladder_produces_shallow_deep_and_risk_leg():
  legs = xau_ladder.build_ladder(
    "SELL", 4341.0, 4344.0, 4346.0,
    pip_size=0.1, total_entry_volume=1.0, equity=5_000.0,
  )
  assert len(legs) == 3
  shallow, deep, risk = legs
  assert (shallow.price, shallow.volume, shallow.is_risk_leg) == (4341.0, 0.8, False)
  assert (deep.price, deep.volume, deep.is_risk_leg) == (4342.5, 0.2, False)
  assert (risk.price, risk.volume, risk.is_risk_leg) == (4344.5, 0.05, True)


def test_build_ladder_can_omit_the_risk_leg():
  legs = xau_ladder.build_ladder(
    "SELL", 4341.0, 4344.0, 4346.0,
    pip_size=0.1, total_entry_volume=1.0, equity=5_000.0,
    include_risk_leg=False,
  )
  assert len(legs) == 2
  assert not any(leg.is_risk_leg for leg in legs)


def test_worst_case_group_risk_includes_every_leg_including_risk_leg():
  legs = xau_ladder.build_ladder(
    "SELL", 4341.0, 4344.0, 4346.0,
    pip_size=0.1, total_entry_volume=1.0, equity=5_000.0,
  )
  # Distances to stop (4346): shallow 5.0, deep 3.5, risk leg 1.5.
  expected = 0.8 * 5.0 + 0.2 * 3.5 + 0.05 * 1.5
  assert xau_ladder.worst_case_group_risk(legs, 4346.0) == expected
