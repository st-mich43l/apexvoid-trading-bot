"""Mapped Zone Reaction must execute exactly once per structural reaction."""

from __future__ import annotations

from types import SimpleNamespace

from tests.configuration.canonical_fixtures import (
  install_runtime_overrides,
  leaf,
  map_strategy_cfg,
)

import pytest

from app.analysis.market_map import MapEntry, MarketMap
from app.autotrade.multi_match import same_thesis
from app.autotrade.reaction_identity import (
  mapped_group_id,
  mapped_reaction_id,
  mapped_thesis_id,
  structural_zone_id,
  zones_materially_equivalent,
)
from app.autotrade.strategy_match import StrategyMatch
from app.autotrade.worker import _publish_strategy_match, _strategy_group_id


def _zone(lo: float, hi: float) -> MapEntry:
  return MapEntry(
    "buy",
    lo,
    hi,
    int(lo),
    int(hi),
    "major",
    ["demand", "ob", "fresh"],
    12.0,
  )


def _map(entry: MapEntry, price: float = 4058.0) -> MarketMap:
  return MarketMap(
    entries=[entry],
    price=price,
    eq=4058.0,
    box_low=4040.0,
    box_high=4070.0,
    bias="up",
    bias_tf="M30",
    source_timeframe="M5",
  )


def _cfg(**overrides):
  values = {
    "auto_trade_mapped_zone_enabled": True,
    "auto_trade_max_entry_distance_pips": 50,
    "auto_trade_strategy_match_max_age_seconds": 420,
    "auto_trade_tp_pips": "30,60,90",
    "auto_trade_map_zone_min_width_atr": 0.15,
    "auto_trade_map_zone_min_width_abs": 1.0,
    "auto_trade_map_counter_bias_enabled": False,
    "auto_trade_map_track_distance_atr": 8.0,
    "auto_trade_map_execute_distance_atr": 8.0,
    "auto_trade_map_reaction_lookback_bars": 5,
    "atr_length": 14,
    "proximal_band_atr": 0.5,
    "pip_size": 0.1,
  }
  values.update(overrides)
  return map_strategy_cfg(**values)


def _rejection_m1(*, close: float = 4058.5):
  import pandas as pd
  # Same touch/confirmation timestamps every evaluation — the incident memory.
  index = pd.DatetimeIndex([
    "2026-07-24 21:40:00+00:00",
    "2026-07-24 21:41:00+00:00",
  ])
  # Bar 0 touches demand; bar 1 is a BUY rejection (close near highs).
  rows = [
    (4056.0, 4060.5, 4054.2, 4055.5),
    (4055.5, 4061.5, 4054.8, 4060.8),
  ]
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close"],
    index=index,
  ).assign(volume=100.0)


def test_incident_zone_jitter_shares_structural_zone_id():
  atr = 2.4
  pip = 0.1
  bands = [
    (4054.26, 4062.31),
    (4054.10, 4062.20),
    (4054.08, 4062.16),
    (4054.08, 4062.15),
    (4054.06, 4062.17),
    (4054.07, 4062.17),
    (4054.08, 4062.15),
  ]
  ids = [
    structural_zone_id(
      "XAUUSD", "BUY", lo, hi, atr=atr, pip_size=pip, tags=["demand", "ob"],
    )
    for lo, hi in bands
  ]
  assert len(set(ids)) == 1
  assert zones_materially_equivalent(
    bands[0][0], bands[0][1], bands[-1][0], bands[-1][1], atr=atr,
  )


def test_same_reaction_same_reaction_id_across_event_ts():
  zone_id = structural_zone_id(
    "XAUUSD", "BUY", 4054.26, 4062.31, atr=2.4, pip_size=0.1, tags=["demand"],
  )
  first = mapped_reaction_id(
    symbol="XAUUSD",
    strategy="Mapped Zone Reaction",
    direction="BUY",
    structural_zone_id=zone_id,
    touch_bar_ts="2026-07-24T21:40:00+00:00",
    confirmation_bar_ts="2026-07-24T21:41:00+00:00",
    reaction_type="reclaim",
  )
  second = mapped_reaction_id(
    symbol="XAUUSD",
    strategy="Mapped Zone Reaction",
    direction="BUY",
    structural_zone_id=zone_id,
    touch_bar_ts="2026-07-24T21:40:00+00:00",
    confirmation_bar_ts="2026-07-24T21:41:00+00:00",
    reaction_type="reclaim",
  )
  assert first == second


def _mapped_zone_reaction_match(
  lo: float,
  hi: float,
  *,
  hour: int,
) -> StrategyMatch:
  # M1 mapped-zone reaction no longer promotes a zone to a StrategyMatch
  # itself (retired as a setup source, P2) - so this replay test constructs
  # what evaluate_market_map_strategy used to build directly, reusing the
  # same identity functions, purely to exercise _publish_strategy_match's
  # reaction/thesis dedup discipline for strategy_mode="mapped_zone_reaction"
  # matches originating elsewhere.
  zone_structural_id = structural_zone_id(
    "XAUUSD", "BUY", lo, hi, atr=2.4, pip_size=0.1, tags=["demand", "ob", "fresh"],
  )
  reaction_id = mapped_reaction_id(
    symbol="XAUUSD",
    strategy="Mapped Zone Reaction",
    direction="BUY",
    structural_zone_id=zone_structural_id,
    touch_bar_ts="2026-07-24T21:40:00+00:00",
    confirmation_bar_ts="2026-07-24T21:41:00+00:00",
    reaction_type="reclaim",
  )
  return StrategyMatch(
    version=1,
    match_id=reaction_id,
    symbol="XAUUSD",
    source_tf="M1",
    event_ts=str(1_780_000_000 + hour),
    issued_at=1_780_000_000 + hour,
    expires_at=1_780_000_000 + hour + 420,
    strategy="Mapped Zone Reaction",
    strategy_mode="mapped_zone_reaction",
    direction="BUY",
    key_level=hi,
    entry_low=4056.0,
    entry_high=4060.5,
    current_price=4058.5,
    confluence=3,
    reasons=("H1 bias up", f"mapped BUY zone {lo:.2f}-{hi:.2f}"),
    atr=2.4,
    structure_swing=lo,
    targets_pips=(30, 60, 90),
    family="mapped_zone",
    structural_source="market_map_zone",
    zone_id=zone_structural_id,
    reaction_id=reaction_id,
    thesis_id=mapped_thesis_id(
      symbol="XAUUSD",
      strategy="Mapped Zone Reaction",
      direction="BUY",
      structural_zone_id=zone_structural_id,
    ),
    structural_zone_id=zone_structural_id,
    structural_zone_low=lo,
    structural_zone_high=hi,
    touch_bar_ts="2026-07-24T21:40:00+00:00",
    confirmation_bar_ts="2026-07-24T21:41:00+00:00",
    reaction_type="reclaim",
  )


@pytest.mark.asyncio
async def test_incident_replay_publishes_one_candidate(monkeypatch):
  """Exact 21:43–21:49 replay: one match identity, one publish, six suppressed."""
  cfg = install_runtime_overrides(monkeypatch, legacy_overrides={
    "auto_trade_enabled": True,
    "auto_trade_mapped_zone_enabled": True,
    "auto_trade_min_confluence": 1,
    "auto_trade_candidate_ttl": 600,
    "auto_trade_opposing_barrier_veto_enabled": False,
    "auto_trade_overlap_veto_enabled": False,
    "auto_trade_zone_cooldown_enabled": False,
    "auto_trade_htf_veto_enabled": False,
    "auto_trade_map_thesis_lock_enabled": True,
  })

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
      return 1 if key in self.kv else 0

    async def eval(self, *args, **kwargs):
      raise RuntimeError("lua unavailable in FakeRedis")

    async def xadd(self, stream, fields, maxlen=None, approximate=True):
      self.stream.append((stream, fields))
      return "1-0"

    async def hincrby(self, key, field, amount):
      bucket = self.metrics.setdefault(key, {})
      bucket[field] = bucket.get(field, 0) + amount
      return bucket[field]

    def pipeline(self):
      return self

    async def execute(self):
      return []

    def rpush(self, *args, **kwargs):
      return self

    def ltrim(self, *args, **kwargs):
      return self

    def expire(self, *args, **kwargs):
      return self

  client = FakeRedis()
  async def _noop_async(*args, **kwargs):
    return None

  async def _incr(c, name, symbol="XAU"):
    await c.hincrby(f"auto_trade:metrics:{symbol.upper()}", name, 1)

  monkeypatch.setattr("app.autotrade.worker.increment_metric", _incr)
  monkeypatch.setattr("app.autotrade.worker.emit_lifecycle", _noop_async)
  monkeypatch.setattr("app.autotrade.worker.event_in_window", _noop_async)
  monkeypatch.setattr("app.autotrade.worker._consume_strategy_match", _noop_async)
  monkeypatch.setattr("app.autotrade.worker._record_guard_evaluation", _noop_async)
  monkeypatch.setattr("app.autotrade.worker._zone_cooldown_reason", _noop_async)
  monkeypatch.setattr("app.autotrade.worker._record_gate_reject", _noop_async)

  bands = [
    (4054.26, 4062.31),
    (4054.10, 4062.20),
    (4054.08, 4062.16),
    (4054.08, 4062.15),
    (4054.06, 4062.17),
    (4054.07, 4062.17),
    (4054.08, 4062.15),
  ]
  event_hours = list(range(43, 50))
  matches = []
  published = []
  for hour, (lo, hi) in zip(event_hours, bands):
    match = _mapped_zone_reaction_match(lo, hi, hour=hour)
    matches.append(match)
    spot = SimpleNamespace(price=4058.5, ts=1_780_000_000 + hour, fresh=True)
    result = await _publish_strategy_match(
      client,
      "XAUUSD",
      spot,
      match,
      match_source="market_map_strategy",
      market_map=_map(_zone(lo, hi), price=4058.5),
      frames={"M1": _rejection_m1()},
    )
    published.append(result)

  assert len({m.reaction_id for m in matches}) == 1
  assert len({m.match_id for m in matches}) == 1
  assert len({_strategy_group_id(m) for m in matches}) == 1
  assert sum(1 for item in published if item is not None) == 1
  assert sum(1 for item in published if item is None) == 6
  metrics = client.metrics.get("auto_trade:metrics:XAUUSD", {})
  suppressed = (
    metrics.get("duplicate_reaction_suppressed", 0)
    + metrics.get("duplicate_thesis_suppressed", 0)
  )
  assert suppressed == 6
  assert metrics.get("mapped_reaction_claimed", 0) == 1
  assert metrics.get("mapped_thesis_claimed", 0) == 1
  assert sum(
    1 for stream, _ in client.stream
    if stream == leaf(cfg, "auto_trade_stream")
  ) == 1


def test_same_thesis_uses_reaction_id_not_event_ts():
  base = dict(
    version=1,
    match_id="abc",
    symbol="XAUUSD",
    source_tf="M1",
    event_ts="1",
    issued_at=1,
    expires_at=100,
    strategy="Mapped Zone Reaction",
    strategy_mode="mapped_zone_reaction",
    direction="BUY",
    key_level=4062.0,
    entry_low=4054.0,
    entry_high=4062.0,
    current_price=4058.0,
    confluence=3,
    reasons=("x",),
    atr=2.4,
    structure_swing=4054.0,
    targets_pips=(30, 60),
    family="mapped_zone",
    reaction_id="same-reaction",
    thesis_id="thesis",
    structural_zone_id="zone",
    touch_bar_ts="t1",
    confirmation_bar_ts="c1",
    reaction_type="reclaim",
  )
  left = StrategyMatch(**base)
  right = StrategyMatch(**{**base, "event_ts": "2", "entry_low": 4054.08, "entry_high": 4062.16})
  # Bypass _valid_match by constructing via dataclass directly — already done.
  assert same_thesis(left, right, atr=2.4) is True
  other = StrategyMatch(**{**base, "reaction_id": "other-reaction", "match_id": "other-reaction"})
  # Same thesis_id still collapses for in-memory dedupe before publish.
  assert same_thesis(left, other, atr=2.4) is True
  different_thesis = StrategyMatch(**{
    **base,
    "reaction_id": "other-reaction",
    "match_id": "other-reaction",
    "thesis_id": "other-thesis",
  })
  assert same_thesis(left, different_thesis, atr=2.4) is False


def test_group_id_stable_for_reaction():
  rid = "reaction-abc"
  a = mapped_group_id(
    symbol="XAUUSD",
    strategy_family="mapped_zone",
    direction="BUY",
    thesis_id="",
    reaction_id=rid,
  )
  b = mapped_group_id(
    symbol="XAUUSD",
    strategy_family="mapped_zone",
    direction="BUY",
    thesis_id="",
    reaction_id=rid,
  )
  assert a == b
