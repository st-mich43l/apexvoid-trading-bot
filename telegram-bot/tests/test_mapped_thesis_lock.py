"""One active initial group per Mapped Zone thesis (incident 22:46 / 22:49)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.autotrade.reaction_identity import (
  advance_thesis_rearm_on_bar,
  evaluate_thesis_rearm_for_publish,
  mapped_group_id,
  mapped_reaction_id,
  mapped_thesis_id,
  structural_zone_id,
  thesis_claim_key,
  thesis_claim_payload,
)
from app.autotrade.strategy_match import StrategyMatch
from app.autotrade.worker import _publish_strategy_match


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
  return SimpleNamespace(**values)


class FakeRedis:
  def __init__(self):
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
    entry_low=4060.0,
    entry_high=4073.0,
    current_price=4072.55,
    confluence=3,
    reasons=("mapped",),
    atr=2.4,
    structure_swing=4060.39,
    targets_pips=(30, 60),
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


@pytest.mark.asyncio
async def test_incident_second_reaction_suppressed_by_thesis_lock(monkeypatch):
  """22:46 then 22:49 same zone/thesis, different reaction_id → one publish."""
  from app.core import config as config_mod

  monkeypatch.setattr(config_mod.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(
    config_mod.settings, "auto_trade_mapped_zone_enabled", True,
  )
  monkeypatch.setattr(config_mod.settings, "auto_trade_map_thesis_lock_enabled", True)
  monkeypatch.setattr(config_mod.settings, "auto_trade_min_confluence", 1)
  monkeypatch.setattr(config_mod.settings, "auto_trade_candidate_ttl", 600)
  monkeypatch.setattr(
    config_mod.settings, "auto_trade_opposing_barrier_veto_enabled", False,
  )
  monkeypatch.setattr(config_mod.settings, "auto_trade_overlap_veto_enabled", False)
  monkeypatch.setattr(config_mod.settings, "auto_trade_zone_cooldown_enabled", False)
  monkeypatch.setattr(config_mod.settings, "auto_trade_htf_veto_enabled", False)

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

  zone = structural_zone_id(
    "XAU", "BUY", 4060.39, 4072.38, atr=2.4, pip_size=0.1, tags=["demand", "ob"],
  )
  thesis = mapped_thesis_id(
    symbol="XAU",
    strategy="Mapped Zone Reaction",
    direction="BUY",
    structural_zone_id=zone,
  )
  r1 = mapped_reaction_id(
    symbol="XAU",
    strategy="Mapped Zone Reaction",
    direction="BUY",
    structural_zone_id=zone,
    touch_bar_ts="2026-07-24T15:45:00+00:00",
    confirmation_bar_ts="2026-07-24T15:46:00+00:00",
    reaction_type="rejection",
  )
  r2 = mapped_reaction_id(
    symbol="XAU",
    strategy="Mapped Zone Reaction",
    direction="BUY",
    structural_zone_id=zone,
    touch_bar_ts="2026-07-24T15:48:00+00:00",
    confirmation_bar_ts="2026-07-24T15:49:00+00:00",
    reaction_type="rejection",
  )
  assert r1 != r2
  assert thesis

  spot = SimpleNamespace(price=4072.55, ts=1_784_908_000, fresh=True)
  first = await _publish_strategy_match(
    client,
    "XAU",
    spot,
    _match(
      reaction_id=r1,
      thesis_id=thesis,
      touch="2026-07-24T15:45:00+00:00",
      confirm="2026-07-24T15:46:00+00:00",
      zone_id=zone,
    ),
  )
  assert first is not None
  assert sum(
    1 for stream, _ in client.stream
    if stream == config_mod.settings.auto_trade_stream
  ) == 1

  second = await _publish_strategy_match(
    client,
    "XAU",
    spot,
    _match(
      reaction_id=r2,
      thesis_id=thesis,
      touch="2026-07-24T15:48:00+00:00",
      confirm="2026-07-24T15:49:00+00:00",
      zone_id=zone,
    ),
  )
  assert second is None
  assert sum(
    1 for stream, _ in client.stream
    if stream == config_mod.settings.auto_trade_stream
  ) == 1
  metrics = client.metrics.get("auto_trade:metrics:XAU", {})
  assert metrics.get("mapped_thesis_claimed", 0) == 1
  assert metrics.get("duplicate_thesis_suppressed", 0) >= 1
  assert thesis_claim_key(thesis) in client.kv


def test_rearm_requires_outside_bars_then_reentry():
  claim = json.loads(thesis_claim_payload(
    thesis_id="t1",
    strategy="Mapped Zone Reaction",
    strategy_family="mapped_zone",
    symbol="XAU",
    direction="BUY",
    structural_zone_id="z1",
    structural_zone_low=4060.0,
    structural_zone_high=4072.0,
    active_reaction_id="r1",
    candidate_id="c1",
    group_id="g1",
    state="terminal_waiting_exit",
    claimed_at=1,
    touch_bar_ts="t0",
    confirmation_bar_ts="c0",
  ))
  # Inside zone — still waiting exit.
  updated, _ = advance_thesis_rearm_on_bar(
    claim,
    bar_ts="b1",
    bar_low=4065.0,
    bar_high=4068.0,
    close=4066.0,
    atr=2.0,
    rearm_atr=0.50,
    rearm_bars=3,
    now_ts=10,
  )
  assert updated["state"] == "terminal_waiting_exit"

  # Leave by 0.30 ATR (< 0.50) — not enough.
  updated, _ = advance_thesis_rearm_on_bar(
    updated,
    bar_ts="b2",
    bar_low=4072.4,
    bar_high=4072.6,
    close=4072.5,
    atr=2.0,
    rearm_atr=0.50,
    rearm_bars=3,
    now_ts=20,
  )
  assert updated["state"] == "terminal_waiting_exit"

  # Leave by >= 0.50 ATR for three unique bars.
  for i, ts in enumerate(("b3", "b4", "b5"), start=1):
    updated, metric = advance_thesis_rearm_on_bar(
      updated,
      bar_ts=ts,
      bar_low=4074.0,
      bar_high=4075.0,
      close=4074.5,
      atr=2.0,
      rearm_atr=0.50,
      rearm_bars=3,
      now_ts=30 + i,
    )
  assert updated["state"] == "outside_zone"
  assert updated["outside_bar_count"] == 3

  # Duplicate bar does not double-count.
  updated, _ = advance_thesis_rearm_on_bar(
    updated,
    bar_ts="b5",
    bar_low=4074.0,
    bar_high=4075.0,
    close=4074.5,
    atr=2.0,
    rearm_atr=0.50,
    rearm_bars=3,
    now_ts=40,
  )
  assert updated["outside_bar_count"] == 3

  # Re-enter → rearm_ready.
  updated, metric = advance_thesis_rearm_on_bar(
    updated,
    bar_ts="b6",
    bar_low=4068.0,
    bar_high=4071.0,
    close=4070.0,
    atr=2.0,
    rearm_atr=0.50,
    rearm_bars=3,
    now_ts=50,
  )
  assert updated["state"] == "rearm_ready"
  assert updated["rearm_ready"] is True
  assert metric == "mapped_thesis_rearm_ready"

  decision = evaluate_thesis_rearm_for_publish(
    updated,
    new_touch_ts="t2",
    new_confirmation_ts="c2",
    price=4070.0,
    atr=2.0,
    rearm_atr=0.50,
    rearm_bars=3,
  )
  assert decision.allowed is True


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
