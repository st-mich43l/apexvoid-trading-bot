"""MAD-0 Asia range seal + phase classifier."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.analysis.mad_phase import (
  PHASE_ACCUM,
  PHASE_EXPAND,
  PHASE_MANIP,
  PHASE_UNCLEAR,
  AsiaRangeSeal,
  asia_day_key,
  asia_window_bounds,
  classify_mad_phase,
  detect_asia_sweep_reclaim,
  evaluate_mad_for_cycle,
  update_asia_range_seal,
)


pytestmark = pytest.mark.no_database


@pytest_asyncio.fixture
async def client():
  url = os.getenv("REAL_REDIS_URL")
  if not url:
    pytest.skip("REAL_REDIS_URL is required for MAD Redis tests")
  redis = Redis.from_url(url, decode_responses=True)
  await redis.ping()
  await redis.flushdb()
  try:
    yield redis
  finally:
    await redis.flushdb()
    await redis.aclose()


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
  return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp())


def _m5(rows: list[tuple[int, float, float, float, float]]) -> pd.DataFrame:
  index = [pd.Timestamp(ts, unit="s", tz="UTC") for ts, *_ in rows]
  return pd.DataFrame(
    {
      "open": [r[1] for r in rows],
      "high": [r[2] for r in rows],
      "low": [r[3] for r in rows],
      "close": [r[4] for r in rows],
    },
    index=index,
  )


def test_asia_day_key_wraps_midnight():
  # 03:00 UTC → Asia day started prior calendar evening (22:00).
  assert asia_day_key(_ts(2026, 8, 26, 3)) == "2026-08-25"
  assert asia_day_key(_ts(2026, 8, 25, 22, 30)) == "2026-08-25"
  assert asia_day_key(_ts(2026, 8, 26, 10)) == "2026-08-25"


def test_asia_window_bounds_overnight():
  start, end = asia_window_bounds(_ts(2026, 8, 26, 3))
  assert start == _ts(2026, 8, 25, 22)
  assert end == _ts(2026, 8, 26, 7)


def test_update_asia_range_builds_then_seals_after_asia():
  rows = [
    (_ts(2026, 8, 25, 22, 0), 3400.0, 3402.0, 3399.0, 3401.0),
    (_ts(2026, 8, 25, 23, 0), 3401.0, 3405.0, 3400.0, 3404.0),
    (_ts(2026, 8, 26, 2, 0), 3404.0, 3406.0, 3403.0, 3405.0),
  ]
  df = _m5(rows)
  building = update_asia_range_seal(
    None, df, now=_ts(2026, 8, 26, 2), session="asia",
  )
  assert building is not None
  assert building.sealed is False
  assert building.high == 3406.0
  assert building.low == 3399.0

  sealed = update_asia_range_seal(
    building, df, now=_ts(2026, 8, 26, 8), session="london",
  )
  assert sealed is not None
  assert sealed.sealed is True
  assert sealed.sealed_at == _ts(2026, 8, 26, 8)


def test_sweep_reclaim_high():
  asia = AsiaRangeSeal(
    day_key="2026-08-25", high=3405.0, low=3399.0,
    sealed=True, sealed_at=1, bar_count=3,
  )
  side, reclaim = detect_asia_sweep_reclaim(3406.5, 3403.0, 3404.5, asia)
  assert side == "high"
  assert reclaim is True


def test_classify_accum_inside_asia_box():
  asia = AsiaRangeSeal(
    day_key="2026-08-25", high=3410.0, low=3400.0,
    sealed=False, sealed_at=None, bar_count=10,
  )
  # width 10 / atr 5 = 2.0 RQ → accum band
  snap = classify_mad_phase(
    price=3404.0,
    atr=5.0,
    session="asia",
    asia=asia,
    m5_structure="range",
  )
  assert snap.phase == PHASE_ACCUM
  assert snap.reason_code == "asia_box_accum"


def test_classify_manip_on_sweep_reclaim():
  asia = AsiaRangeSeal(
    day_key="2026-08-25", high=3410.0, low=3400.0,
    sealed=True, sealed_at=1, bar_count=10,
  )
  snap = classify_mad_phase(
    price=3409.0,
    atr=5.0,
    session="london",
    asia=asia,
    m5_structure="range",
    bar_high=3412.0,
    bar_low=3407.0,
    bar_close=3409.0,
  )
  assert snap.phase == PHASE_MANIP
  assert snap.sweep_side == "high"
  assert snap.reclaim is True


def test_classify_expand_on_accepted_break():
  asia = AsiaRangeSeal(
    day_key="2026-08-25", high=3410.0, low=3400.0,
    sealed=True, sealed_at=1, bar_count=10,
  )
  snap = classify_mad_phase(
    price=3413.0,  # 0.6 ATR beyond high
    atr=5.0,
    session="london",
    asia=asia,
    m5_structure="bullish",
    bar_high=3413.5,
    bar_low=3411.0,
    bar_close=3413.0,
  )
  assert snap.phase == PHASE_EXPAND


def test_evaluate_mad_for_cycle_unclear_without_bars():
  seal, snap = evaluate_mad_for_cycle(
    previous=None,
    ohlc=pd.DataFrame(),
    now=_ts(2026, 8, 26, 3),
    session="asia",
    price=3400.0,
    atr=5.0,
    m5_structure="range",
    bar_high=None,
    bar_low=None,
    bar_close=None,
  )
  assert seal is None
  assert snap.phase == PHASE_UNCLEAR


def test_mad_soft_bonus_entry_quality_range_and_reaction():
  from app.analysis.mad_phase import mad_soft_bonus, PHASE_ACCUM, PHASE_EXPAND, PHASE_MANIP

  assert mad_soft_bonus(phase=PHASE_ACCUM, family="range_scalp") >= 0.1
  assert mad_soft_bonus(phase=PHASE_ACCUM, family="range_edge") >= 0.1
  assert mad_soft_bonus(phase=PHASE_MANIP, family="reaction") >= 0.1
  assert mad_soft_bonus(phase=PHASE_MANIP, family="liquidity") >= 0.1
  # No soft favor for impulse / expand / mismatched phase×family.
  assert mad_soft_bonus(phase=PHASE_EXPAND, family="impulse_pullback") == 0.0
  assert mad_soft_bonus(phase=PHASE_ACCUM, family="impulse_pullback") == 0.0
  assert mad_soft_bonus(phase=PHASE_MANIP, family="range_scalp") == 0.0
  assert mad_soft_bonus(phase=PHASE_ACCUM, family="range_sweep") == 0.0
  assert mad_soft_bonus(phase=PHASE_ACCUM, family="reaction") == 0.0
  assert mad_soft_bonus(phase=PHASE_EXPAND, family="reaction") == 0.0


def test_classify_building_asia_wide_rq_still_accum():
  """Prod 2026-08-26: RQ ~17.8 inside unsealed Asia box should label accum."""
  asia = AsiaRangeSeal(
    day_key="2026-08-25", high=4673.73, low=4630.06,
    sealed=False, sealed_at=None, bar_count=77,
  )
  snap = classify_mad_phase(
    price=4642.0,
    atr=2.45,
    session="asia",
    asia=asia,
    m5_structure="range",
  )
  assert snap.phase == PHASE_ACCUM
  assert snap.reason_code == "asia_building_accum"


def test_compute_mad_features_and_hard_gate():
  from app.analysis.mad_phase import (
    MadPhaseSnapshot,
    compute_mad_features,
    enrich_mad_payload_for_shadow,
    mad_hard_gate,
  )

  asia = AsiaRangeSeal(
    day_key="2026-08-25", high=3410.0, low=3400.0,
    sealed=True, sealed_at=1, bar_count=10,
  )
  accum_snap = MadPhaseSnapshot(
    phase=PHASE_ACCUM,
    asia=asia,
    range_quality_atr=2.0,
    price_vs_asia="inside",
    sweep_side=None,
    reclaim=False,
    reason_code="asia_box_accum",
    measured={"session": "asia"},
  )
  feats = compute_mad_features(accum_snap)
  assert feats.accum >= 0.7
  assert feats.manip < 0.5

  impulse_gate = mad_hard_gate(phase=PHASE_ACCUM, strategy="impulse_pullback_continuation")
  assert impulse_gate.would_block is True
  assert impulse_gate.reason_code == "mad_gate_impulse_needs_manip_or_expand"

  range_gate = mad_hard_gate(phase=PHASE_ACCUM, strategy="range_sweep")
  assert range_gate.would_block is False

  payload = enrich_mad_payload_for_shadow(accum_snap)
  assert "features" in payload
  assert "would_gate" in payload
  assert "impulse_pullback_continuation" in payload["would_gate"]


def test_mad_gate_strategy_for_setup_maps_technique_names():
  from app.analysis.mad_phase import mad_gate_strategy_for_setup

  assert mad_gate_strategy_for_setup("Range Edge Scalp") == (
    "range_edge_mean_reversion"
  )
  assert mad_gate_strategy_for_setup(
    "One-Sided Range Reaction",
    family="range",
  ) == "range_edge_mean_reversion"
  assert mad_gate_strategy_for_setup("Liquidity Sweep") == (
    "liquidity_sweep_reversal"
  )
  assert mad_gate_strategy_for_setup("Key Level") == (
    "structural_reaction"
  )
  assert mad_gate_strategy_for_setup("Order Block") == "structural_reaction"
  assert mad_gate_strategy_for_setup("FVG") == "structural_reaction"
  assert mad_gate_strategy_for_setup("Supply Zone") == "structural_reaction"
  assert mad_gate_strategy_for_setup(
    "Zone Reaction",
    family="zone",
  ) == "structural_reaction"


def test_mad_hard_gate_reversal_and_continuation_rules():
  from app.analysis.mad_phase import (
    PHASE_ACCUM,
    PHASE_EXPAND,
    PHASE_MANIP,
    mad_hard_gate,
  )

  expand_block = mad_hard_gate(
    phase=PHASE_EXPAND,
    strategy="structural_reaction",
  )
  assert expand_block.would_block is True
  assert expand_block.reason_code == "mad_gate_reversal_avoid_expand"

  manip_ok = mad_hard_gate(phase=PHASE_MANIP, strategy="structural_reaction")
  assert manip_ok.would_block is False

  accum_ok = mad_hard_gate(phase=PHASE_ACCUM, strategy="structural_reaction")
  assert accum_ok.would_block is False

  impulse_accum = mad_hard_gate(
    phase=PHASE_ACCUM,
    strategy="impulse_pullback_continuation",
  )
  assert impulse_accum.would_block is True

  breakout_manip = mad_hard_gate(phase=PHASE_MANIP, strategy="breakout_retest")
  assert breakout_manip.would_block is False

  breakout_accum = mad_hard_gate(phase=PHASE_ACCUM, strategy="breakout_retest")
  assert breakout_accum.would_block is True


@pytest.mark.asyncio
@pytest.mark.real_redis
async def test_save_mad_phase_persists_enriched_payload(client):
  from app.analysis.mad_phase import (
    MadPhaseSnapshot,
    PHASE_ACCUM,
    load_mad_phase,
    mad_phase_key,
    save_mad_phase,
  )

  snap = MadPhaseSnapshot(
    phase=PHASE_ACCUM,
    asia=None,
    range_quality_atr=2.0,
    price_vs_asia="inside",
    sweep_side=None,
    reclaim=False,
    reason_code="asia_box_accum",
    measured={},
  )
  await save_mad_phase(client, "GBPUSD", snap)
  raw = await client.get(mad_phase_key("GBPUSD"))
  import json

  payload = json.loads(raw)
  assert "features" in payload
  assert "would_gate" in payload
  loaded = await load_mad_phase(client, "GBPUSD")
  assert loaded is not None
  assert loaded.phase == PHASE_ACCUM
