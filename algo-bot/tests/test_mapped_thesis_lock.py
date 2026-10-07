"""One active initial group per Mapped Zone thesis (incident 22:46 / 22:49)."""

from __future__ import annotations

from types import SimpleNamespace

from tests.support.canonical_fixtures import (
  install_runtime_overrides,
  leaf,
  map_strategy_cfg,
)

import pytest

from app.autotrade.reaction_identity import (
  mapped_group_id,
  structural_zone_id,
  thesis_claim_key,
)
from app.autotrade.strategy_match import StrategyMatch


def _cfg(**overrides):
  values = {
    "auto_trade_mapped_zone_enabled": True,
    "auto_trade_map_thesis_lock_enabled": True,
    "auto_trade_map_reaction_rearm_bars": 3,
    "auto_trade_map_reaction_rearm_atr": 0.50,
    "auto_trade_max_entry_distance_pips": 50,
    "auto_trade_strategy_match_max_age_seconds": 420,
    "auto_trade_tp_pips": "30,60,90",
    "atr_length": 14,
    "proximal_band_atr": 0.5,
    "pip_size": 0.1,
  }
  values.update(overrides)
  return map_strategy_cfg(**values)


class FakeRedis:
  def __init__(self):
    self._apexvoid_allow_non_atomic_test_fallback = True
    self.kv = {}
    self.stream = []
    self.metrics = {}

  async def get(self, key):
    return self.kv.get(key)

  async def set(self, key, value, ex=None, nx=False):
    if nx and key in self.kv:
      return False
    self.kv[key] = value
    return True

  async def delete(self, *keys):
    for key in keys:
      self.kv.pop(key, None)
    return 1

  async def exists(self, key):
    return int(key in self.kv)

  async def eval(self, *args, **kwargs):
    raise RuntimeError("lua unavailable in FakeRedis")

  async def xadd(self, stream, fields, maxlen=None, approximate=True):
    self.stream.append((stream, fields))
    return "1-0"

  async def hincrby(self, key, field, amount):
    bucket = self.metrics.setdefault(key, {})
    bucket[field] = bucket.get(field, 0) + amount
    return bucket[field]

  async def scan_iter(self, match=None, count=50):
    if False:
      yield None
    return
    yield  # pragma: no cover


def _match(
  *,
  reaction_id: str,
  thesis_id: str,
  touch: str,
  confirm: str,
  zone_id: str = "zone-z1",
) -> StrategyMatch:
  return StrategyMatch(
    version=1,
    match_id=reaction_id,
    symbol="XAU",
    source_tf="M1",
    event_ts=confirm,
    issued_at=1_784_908_000,
    expires_at=1_784_908_420,
    strategy="Mapped Zone Reaction",
    strategy_mode="mapped_zone_reaction",
    direction="BUY",
    key_level=4072.38,
    entry_low=4070.0,
    entry_high=4073.0,
    current_price=4072.55,
    confluence=3,
    reasons=("mapped",),
    atr=2.4,
    structure_swing=4068.0,
    targets_pips=(30, 60, 90),
    family="mapped_zone",
    structural_source="market_map_zone",
    zone_id=zone_id,
    reaction_id=reaction_id,
    thesis_id=thesis_id,
    structural_zone_id=zone_id,
    structural_zone_low=4060.39,
    structural_zone_high=4072.38,
    touch_bar_ts=touch,
    confirmation_bar_ts=confirm,
    reaction_type="rejection",
  )


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
