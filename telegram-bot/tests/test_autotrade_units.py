from app.autotrade import delivery, gate, trend, units


def test_auto_trade_modules_share_one_pip_definition():
  assert gate.units is units
  assert trend.units is units
  assert delivery.units is units


def test_xau_target_pips_round_trip_to_price():
  entry = 4_000.0
  target = entry + 3.0

  targets_pips = round((target - entry) / units.pip_size("XAU"))
  restored = entry + targets_pips * units.pip_size("XAU")

  assert targets_pips == 30
  assert restored == target
