"""Volume-weighted partial/final pip PnL — broker-confirmed fills only."""

from app.autotrade.volume_pips import (
  broker_volume_to_lots,
  format_lots,
  format_signed_pips,
  leg_pips,
  remaining_after_close,
  trade_net_pips,
  volume_percent,
)


def test_mandatory_two_leg_volume_weighted_net():
  # Initial volume: 0.09 lot. Broker units use LotSize 10_000.
  lot_size = 10_000
  initial_lots = 0.09
  initial = initial_lots * lot_size  # 900

  leg1_volume = 0.03 * lot_size  # 300
  leg1_pips = 48.4
  leg2_volume = 0.06 * lot_size  # 600
  leg2_pips = 0.9

  booked = volume_percent(leg1_volume, initial)
  remaining_volume = remaining_after_close(initial, [leg1_volume])
  remaining_pct = volume_percent(remaining_volume, initial)
  net = trade_net_pips(
    [(leg1_pips, leg1_volume), (leg2_pips, leg2_volume)],
    initial,
  )

  assert booked == 33.3
  assert remaining_pct == 66.7
  assert format_lots(broker_volume_to_lots(remaining_volume, lot_size)) == "0.06"
  assert net == 16.7
  assert format_signed_pips(net) == "+16.7"


def test_leg_pips_buy_and_sell_from_entry_exit():
  pip_size = 0.1
  assert leg_pips("SELL", 2650.0, 2645.16, pip_size) == 48.4
  assert leg_pips("BUY", 2650.0, 2654.84, pip_size) == 48.4


def test_does_not_sum_raw_leg_pips_with_unequal_volume():
  # Naive sum would be 49.3; volume-weighted must be 16.7.
  assert trade_net_pips([(48.4, 300), (0.9, 600)], 900) == 16.7
  assert trade_net_pips([(48.4, 300), (0.9, 600)], 900) != 49.3


def test_format_lots_keeps_broker_precision():
  assert format_lots(0.09) == "0.09"
  assert format_lots(0.1) == "0.1"
  assert format_lots(1.0) == "1"
