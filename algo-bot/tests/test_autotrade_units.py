import pytest

from app.autotrade import delivery, units
from app.core.symbols import canonical_symbol, pip_for


def test_auto_trade_delivery_uses_the_canonical_pip_definition():
  assert delivery.units is units


def test_xau_target_pips_round_trip_to_price():
  entry = 4_000.0
  target = entry + 3.0

  targets_pips = round((target - entry) / units.pip_size("XAU"))
  restored = entry + targets_pips * units.pip_size("XAU")

  assert targets_pips == 30
  assert restored == target


def test_xau_and_xauusd_resolve_to_the_same_canonical_symbol():
  assert canonical_symbol("XAU") == "XAU"
  assert canonical_symbol("XAUUSD") == "XAU"
  assert canonical_symbol("xauusd") == "XAU"


def test_xauusd_does_not_fall_through_to_a_generic_pip_size():
  # 23 Jul-era gap: CTRADER_SYMBOL is configured as "XAUUSD" on the C# side
  # while every internal payload uses "XAU" - a lookup keyed on the raw
  # broker string must not silently default to 1.0 (a 10x error versus
  # XAU's actual 0.1).
  assert units.pip_size("XAU") == 0.1
  assert units.pip_size("XAUUSD") == 0.1
  assert units.pip_size("XAU") == units.pip_size("XAUUSD")
  assert pip_for("XAUUSD") == pip_for("XAU")


def test_unconfigured_symbol_raises_instead_of_silently_defaulting():
  with pytest.raises(KeyError):
    units.pip_size("EURUSD")
