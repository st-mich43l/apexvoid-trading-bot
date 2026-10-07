"""One active initial group per thesis: the group id follows the thesis cycle."""

from __future__ import annotations

from app.autotrade.reaction_identity import mapped_group_id


def test_group_id_uses_thesis_cycle():
  a = mapped_group_id(
    symbol="XAU",
    strategy_family="mapped_zone",
    direction="BUY",
    thesis_id="thesis-1",
    thesis_cycle=1,
  )
  b = mapped_group_id(
    symbol="XAU",
    strategy_family="mapped_zone",
    direction="BUY",
    thesis_id="thesis-1",
    thesis_cycle=2,
  )
  assert a != b
