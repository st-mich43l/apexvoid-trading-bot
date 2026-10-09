"""Contract-shaped comparisons for published TradePlans.

The planner's prices change whenever its policy does (card rounding, the entry band, the stop
envelope, the target ladder). A test that pins those numbers fails on every new implementation
without catching a real defect, so tests compare what is a contract - shape, identity, ratios,
ordering, and relationships to the card or to Go's technical facts - and leave the planner's
price arithmetic to the focused tests that own it (test_card_prices, test_xau_zone_band).
"""

from __future__ import annotations

from typing import Any

# Fields that carry a planner-chosen price.
PRICE_KEYS = frozenset({
  "price", "zone_low", "zone_high", "order_price", "low", "high", "invalidation_price",
})


def assert_same_plan_contract(fresh: Any, committed: Any, *, price_tolerance: float, path: str = "") -> None:
  """``fresh`` has ``committed``'s exact shape and non-price content; its prices may differ by
  at most ``price_tolerance`` (price units), because they are the planner's policy, not the
  contract."""
  if isinstance(committed, dict):
    assert isinstance(fresh, dict), f"{path}: expected an object"
    assert set(fresh) == set(committed), f"{path}: keys differ: {sorted(set(fresh) ^ set(committed))}"
    for key in committed:
      assert_same_plan_contract(fresh[key], committed[key], price_tolerance=price_tolerance, path=f"{path}.{key}")
    return
  if isinstance(committed, list):
    assert isinstance(fresh, list) and len(fresh) == len(committed), f"{path}: list length differs"
    for index, (a, b) in enumerate(zip(fresh, committed)):
      assert_same_plan_contract(a, b, price_tolerance=price_tolerance, path=f"{path}[{index}]")
    return
  key = path.rsplit(".", 1)[-1].split("[")[0]
  if key in PRICE_KEYS and committed is not None:
    assert fresh is not None, f"{path}: price missing"
    assert abs(float(fresh) - float(committed)) <= price_tolerance, (
      f"{path}: {fresh} is more than {price_tolerance} from the fixture's {committed}"
    )
    return
  assert fresh == committed, f"{path}: {fresh!r} != {committed!r}"
