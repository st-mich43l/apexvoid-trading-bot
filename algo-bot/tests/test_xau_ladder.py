from app.autotrade import xau_ladder
import pytest


pytestmark = pytest.mark.no_database


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
