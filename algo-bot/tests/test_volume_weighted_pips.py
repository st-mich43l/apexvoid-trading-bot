"""Volume-weighted partial/final pip PnL — broker-confirmed fills only."""

from app.autotrade.volume_pips import format_lots, leg_pips


def test_leg_pips_buy_and_sell_from_entry_exit():
  pip_size = 0.1
  assert leg_pips("SELL", 2650.0, 2645.16, pip_size) == 48.4
  assert leg_pips("BUY", 2650.0, 2654.84, pip_size) == 48.4


def test_format_lots_keeps_broker_precision():
  assert format_lots(0.09) == "0.09"
  assert format_lots(0.1) == "0.1"
  assert format_lots(1.0) == "1"
