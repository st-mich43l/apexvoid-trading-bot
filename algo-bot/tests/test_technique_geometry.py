"""Unit tests for technique_geometry predicates and confluence band builder."""

from __future__ import annotations

import pandas as pd
import pytest

from app.analysis.confluence_zone import build_confluence_bands
from app.analysis.technique_geometry import (
  TECHNIQUE_FVG,
  TECHNIQUE_IFVG,
  TECHNIQUE_OB,
  TECHNIQUE_SD,
  TechniqueGeometrySettings,
  TechniqueInstance,
  collect_technique_instances,
  discover_ifvg_instances,
  epsilon,
  not_invalidated,
  overlap_ratio,
  proximal_retest,
  validate_technique_instance,
  zone_is_spent,
)
from app.analysis.types import Zone


pytestmark = pytest.mark.no_database


def _df(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
  index = pd.date_range("2026-08-01", periods=len(rows), freq="5min", tz="UTC")
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close"],
    index=index,
  )


def test_epsilon_uses_pip_and_atr_floor():
  settings = TechniqueGeometrySettings(pip_size=0.1)
  assert epsilon(pip_size=0.1, atr=1.0, settings=settings) == 0.1
  assert epsilon(pip_size=0.1, atr=10.0, settings=settings) == pytest.approx(0.5)


def test_overlap_ratio_partial():
  assert overlap_ratio(100.0, 102.0, 101.0, 103.0) == pytest.approx(0.5)


def test_proximal_retest_buy_inside_band():
  settings = TechniqueGeometrySettings(pip_size=0.1)
  assert proximal_retest(
    side="buy", low=100.0, high=101.0, price=100.5, atr=2.0, settings=settings,
  )


def test_fvg_discovery_three_candle_gap():
  df = _df([
    (100, 101, 99, 100),
    (100, 101, 99.5, 100),
    (102, 103, 101.5, 102.5),
  ])
  zones = [
    Zone(101.0, 101.5, "demand", origin_index=2, source="bullish_fvg"),
  ]
  instances, _rejects = collect_technique_instances(
    sd_zones=[], ob_zones=[], fvg_zones=zones, df=df,
    price=102.0, atr=2.0, validation_enabled=False,
  )
  assert len(instances) == 1
  assert instances[0].technique == TECHNIQUE_FVG


def test_ifvg_inverts_bullish_fvg_on_close_through():
  df = _df([
    (100, 101, 99, 100),
    (100, 101, 99.5, 100),
    (102, 103, 101.5, 102.5),
    (102, 102.5, 100.5, 100.8),
  ])
  zones = [
    Zone(101.0, 101.5, "demand", origin_index=2, source="bullish_fvg"),
  ]
  ifvg = discover_ifvg_instances(zones, df, settings=TechniqueGeometrySettings())
  assert len(ifvg) == 1
  assert ifvg[0].technique == TECHNIQUE_IFVG
  assert ifvg[0].side == "sell"


def test_build_confluence_bands_requires_two_distinct_techniques():
  instances = [
    TechniqueInstance(
      TECHNIQUE_SD, "buy", 4100.0, 4101.0, None, ("supply_demand",),
      origin_index=1,
    ),
    TechniqueInstance(
      TECHNIQUE_OB, "buy", 4100.5, 4101.5, None, ("order_block",),
      measured={"has_bos": True, "body_frac": 0.8},
      origin_index=2,
    ),
    TechniqueInstance(
      TECHNIQUE_FVG, "buy", 4102.0, 4103.0, None, ("bullish_fvg",),
      origin_index=3,
    ),
  ]
  bands = build_confluence_bands(
    instances, symbol="XAU", atr=2.0, pip_size=0.1, min_overlap=0.5,
  )
  assert len(bands) == 1
  assert set(bands[0].technique_tags) == {TECHNIQUE_SD, TECHNIQUE_OB}
  assert bands[0].low == 4100.0
  assert bands[0].high == 4101.5


def test_single_technique_does_not_form_confluence_band():
  instances = [
    TechniqueInstance(
      TECHNIQUE_SD, "buy", 4100.0, 4101.0, None, ("supply_demand",),
      origin_index=1,
    ),
    TechniqueInstance(
      TECHNIQUE_SD, "buy", 4100.2, 4101.2, None, ("supply_demand",),
      origin_index=4,
    ),
  ]
  bands = build_confluence_bands(
    instances, symbol="XAU", atr=2.0, pip_size=0.1, min_overlap=0.5,
  )
  assert bands == []


def test_optimize_imbalance_entry_clips_sell_fvg_to_five_price():
  from app.analysis.technique_geometry import optimize_imbalance_entry_zone

  zone = Zone(
    4420.9, 4430.5, "supply", origin_index=2, source="bearish_fvg",
  )
  clipped, changed = optimize_imbalance_entry_zone(
    zone, direction="SELL", max_width_price=5.0,
  )
  assert changed is True
  assert clipped.low == pytest.approx(4420.9)
  assert clipped.high == pytest.approx(4425.9)
  assert clipped.high - clipped.low == pytest.approx(5.0)


def test_optimize_imbalance_entry_clips_buy_fvg_to_five_price():
  from app.analysis.technique_geometry import optimize_imbalance_entry_zone

  zone = Zone(
    4410.0, 4420.0, "demand", origin_index=2, source="bullish_fvg",
  )
  clipped, changed = optimize_imbalance_entry_zone(
    zone, direction="BUY", max_width_price=5.0,
  )
  assert changed is True
  assert clipped.low == pytest.approx(4415.0)
  assert clipped.high == pytest.approx(4420.0)


def test_optimize_imbalance_leaves_non_fvg_zone_alone():
  from app.analysis.technique_geometry import optimize_imbalance_entry_zone

  zone = Zone(
    4420.9, 4430.5, "supply", origin_index=2, source="supply_demand",
  )
  same, changed = optimize_imbalance_entry_zone(
    zone, direction="SELL", max_width_price=5.0,
  )
  assert changed is False
  assert same.low == zone.low
  assert same.high == zone.high


def test_optimize_imbalance_confluence_tags_trigger_clip():
  from app.analysis.technique_geometry import optimize_imbalance_entry_zone

  zone = Zone(
    4420.9, 4430.5, "supply", origin_index=2, source="confluence",
    sources=["fvg_ifvg"],
  )
  clipped, changed = optimize_imbalance_entry_zone(
    zone, direction="SELL", max_width_price=5.0, tags=("fvg_ifvg",),
  )
  assert changed is True
  assert clipped.high - clipped.low == pytest.approx(5.0)


def test_optimize_imbalance_skips_already_narrow_band():
  from app.analysis.technique_geometry import optimize_imbalance_entry_zone

  zone = Zone(
    4420.0, 4423.0, "supply", origin_index=2, source="bearish_fvg",
  )
  same, changed = optimize_imbalance_entry_zone(
    zone, direction="SELL", max_width_price=5.0,
  )
  assert changed is False
  assert same is zone


def test_optimize_crt_entry_clips_buy_to_proximal_low():
  from app.analysis.technique_geometry import optimize_crt_entry_zone

  zone = Zone(4400.0, 4424.0, "demand", origin_index=2, source="crt")
  clipped, changed = optimize_crt_entry_zone(
    zone, direction="BUY", max_width_price=5.0,
  )
  assert changed is True
  assert clipped.low == pytest.approx(4400.0)
  assert clipped.high == pytest.approx(4405.0)


def test_optimize_crt_entry_clips_sell_to_proximal_high():
  from app.analysis.technique_geometry import optimize_crt_entry_zone

  zone = Zone(4400.0, 4424.0, "supply", origin_index=2, source="crt")
  clipped, changed = optimize_crt_entry_zone(
    zone, direction="SELL", max_width_price=5.0,
  )
  assert changed is True
  assert clipped.low == pytest.approx(4419.0)
  assert clipped.high == pytest.approx(4424.0)


def test_optimize_technique_entry_clips_supply_to_proximal_low():
  """2026-08-23 dig: Supply Demand/Order Block had no entry clip at all -
  the raw multi-candle zone fed straight into the stop-clearance calc and
  routinely blew fast pairs' envelopes (0 Order Block fills, ever)."""
  from app.analysis.technique_geometry import optimize_technique_entry_zone

  zone = Zone(4400.0, 4424.0, "supply", origin_index=2, source="supply_demand")
  clipped, changed = optimize_technique_entry_zone(zone, max_width_price=5.0)
  assert changed is True
  # Supply: price falls into it from below, so the near/proximal edge is
  # the bottom - keep low, clip the top down.
  assert clipped.low == pytest.approx(4400.0)
  assert clipped.high == pytest.approx(4405.0)


def test_optimize_technique_entry_clips_demand_to_proximal_high():
  from app.analysis.technique_geometry import optimize_technique_entry_zone

  zone = Zone(4400.0, 4424.0, "demand", origin_index=2, source="order_block")
  clipped, changed = optimize_technique_entry_zone(zone, max_width_price=5.0)
  assert changed is True
  # Demand: price falls into it from above, so the near/proximal edge is
  # the top - keep high, clip the bottom up.
  assert clipped.low == pytest.approx(4419.0)
  assert clipped.high == pytest.approx(4424.0)


def test_optimize_technique_entry_leaves_narrow_zone_untouched():
  from app.analysis.technique_geometry import optimize_technique_entry_zone

  zone = Zone(4400.0, 4403.0, "supply", origin_index=2, source="supply_demand")
  same, changed = optimize_technique_entry_zone(zone, max_width_price=5.0)
  assert changed is False
  assert same is zone


def test_instance_from_zone_clips_supply_demand_and_preserves_structural_bounds():
  from app.analysis.technique_geometry import instance_from_zone

  zone = Zone(4400.0, 4424.0, "supply", origin_index=2, source="supply_demand")
  item = instance_from_zone(
    zone, technique=TECHNIQUE_SD, entry_max_width_price=5.0,
  )
  assert item is not None
  assert item.side == "sell"
  assert item.low == pytest.approx(4400.0)
  assert item.high == pytest.approx(4405.0)
  assert item.measured["structural_low"] == pytest.approx(4400.0)
  assert item.measured["structural_high"] == pytest.approx(4424.0)
  assert item.measured["entry_clipped"] is True


def test_instance_from_zone_clips_order_block():
  from app.analysis.technique_geometry import instance_from_zone

  zone = Zone(4400.0, 4424.0, "demand", origin_index=2, source="order_block")
  item = instance_from_zone(
    zone, technique=TECHNIQUE_OB, entry_max_width_price=5.0,
  )
  assert item is not None
  assert item.side == "buy"
  assert item.low == pytest.approx(4419.0)
  assert item.high == pytest.approx(4424.0)
  assert item.measured["structural_low"] == pytest.approx(4400.0)
  assert item.measured["structural_high"] == pytest.approx(4424.0)


def test_instance_from_zone_without_entry_max_width_leaves_zone_unclipped():
  """Backward compatible: omitting entry_max_width_price is a pure no-op,
  same shape as every call site before this fix."""
  from app.analysis.technique_geometry import instance_from_zone

  zone = Zone(4400.0, 4424.0, "supply", origin_index=2, source="supply_demand")
  item = instance_from_zone(zone, technique=TECHNIQUE_SD)
  assert item is not None
  assert item.low == pytest.approx(4400.0)
  assert item.high == pytest.approx(4424.0)
  assert "entry_clipped" not in item.measured


def test_collect_technique_instances_clips_sd_ob_fvg_entries():
  df = _df([(100, 101, 99, 100)] * 3)
  sd_zones = [Zone(4400.0, 4424.0, "supply", origin_index=1, source="supply_demand")]
  ob_zones = [
    Zone(
      4400.0, 4424.0, "demand", origin_index=1, source="order_block",
      break_kind="BOS",
    ),
  ]
  fvg_zones = [
    Zone(4400.0, 4424.0, "demand", origin_index=1, source="bullish_fvg"),
  ]
  instances, _rejects = collect_technique_instances(
    sd_zones=sd_zones, ob_zones=ob_zones, fvg_zones=fvg_zones, df=df,
    price=4402.0, atr=10.0,
    settings=TechniqueGeometrySettings(fvg_entry_max_width_price=5.0),
    validation_enabled=False,
  )
  by_technique = {item.technique: item for item in instances}
  assert by_technique[TECHNIQUE_SD].high - by_technique[TECHNIQUE_SD].low == pytest.approx(5.0)
  assert by_technique[TECHNIQUE_OB].high - by_technique[TECHNIQUE_OB].low == pytest.approx(5.0)
  assert by_technique[TECHNIQUE_FVG].high - by_technique[TECHNIQUE_FVG].low == pytest.approx(5.0)
  for technique in (TECHNIQUE_SD, TECHNIQUE_OB, TECHNIQUE_FVG):
    assert by_technique[technique].measured["structural_low"] == pytest.approx(4400.0)
    assert by_technique[technique].measured["structural_high"] == pytest.approx(4424.0)


def test_discover_ifvg_instances_clips_entry_and_keeps_structural_bounds():
  df = _df([
    (100, 101, 99, 100),
    (100, 101, 99.5, 100),
    (102, 103, 101.5, 102.5),
    (102, 102.5, 90.0, 90.8),
  ])
  zones = [
    Zone(101.0, 130.0, "demand", origin_index=2, source="bullish_fvg"),
  ]
  ifvg = discover_ifvg_instances(
    zones, df, settings=TechniqueGeometrySettings(), entry_max_width_price=5.0,
  )
  assert len(ifvg) == 1
  item = ifvg[0]
  assert item.side == "sell"
  assert item.high - item.low == pytest.approx(5.0)
  assert item.measured["structural_low"] == pytest.approx(101.0)
  assert item.measured["structural_high"] == pytest.approx(130.0)


def test_discover_crt_clips_entry_and_keeps_h1_structural_bounds(monkeypatch):
  """Live 2026-08: full-H1 CRT entry failed width/stop envelopes."""
  import pandas as pd

  from app.analysis import technique_geometry as geom

  monkeypatch.setattr(geom, "has_structural_confirmation", lambda *a, **k: True)

  idx = pd.date_range("2026-08-21 10:00", periods=4, freq="h", tz="UTC")
  # Forming bar (last) is ignored; closed bar at index 2 is the CRT impulse.
  h1 = pd.DataFrame({
    "open": [4400.0, 4405.0, 4410.0, 4420.0],
    "high": [4408.0, 4412.0, 4434.0, 4425.0],
    "low": [4398.0, 4402.0, 4410.0, 4418.0],
    "close": [4406.0, 4410.0, 4430.0, 4422.0],
  }, index=idx)
  # M5: sweep below 4410 then reclaim; price still in discount half of 4410-4434.
  m5_idx = pd.date_range("2026-08-21 12:00", periods=8, freq="5min", tz="UTC")
  m5 = pd.DataFrame({
    "open": [4415.0, 4414.0, 4409.0, 4411.0, 4413.0, 4414.0, 4415.0, 4416.0],
    "high": [4417.0, 4416.0, 4412.0, 4414.0, 4416.0, 4417.0, 4418.0, 4419.0],
    "low": [4413.0, 4412.0, 4408.0, 4410.0, 4412.0, 4413.0, 4414.0, 4415.0],
    "close": [4414.0, 4413.0, 4411.0, 4413.0, 4415.0, 4416.0, 4417.0, 4418.0],
  }, index=m5_idx)
  settings = geom.TechniqueGeometrySettings(
    crt_min_atr=1.0,
    crt_entry_max_width_price=5.0,
    crt_h1_lookback_bars=3,
  )
  found = geom.discover_crt_instances(
    h1, m5, h1_atr=10.0, exec_atr=2.0, settings=settings,
  )
  buys = [item for item in found if item.side == "buy"]
  assert buys, "expected buy CRT on closed H1 impulse"
  item = buys[0]
  assert item.low == pytest.approx(4410.0)
  assert item.high == pytest.approx(4415.0)
  assert item.measured["structural_low"] == pytest.approx(4410.0)
  assert item.measured["structural_high"] == pytest.approx(4434.0)
  assert item.measured["entry_clipped"] is True
  assert item.origin_index == 2  # closed impulse, not forming bar


def _valid_buy_instance(**overrides) -> TechniqueInstance:
  measured = {"mitigated": False, "body_frac": 0.8, "has_bos": True}
  measured.update(overrides.get("measured") or {})
  return TechniqueInstance(
    technique=overrides.get("technique", TECHNIQUE_SD),
    side=overrides.get("side", "buy"),
    low=overrides.get("low", 100.0),
    high=overrides.get("high", 101.0),
    origin_ts=None,
    sources=("supply_demand",),
    measured=measured,
    origin_index=overrides.get("origin_index", 0),
  )


def test_validate_rejects_mitigated_flag():
  settings = TechniqueGeometrySettings(pip_size=0.1)
  df = _df([(100, 101, 99, 100.5)])
  instance = _valid_buy_instance(measured={"mitigated": True})
  reasons: list[str] = []
  assert not validate_technique_instance(
    instance, df, price=100.5, atr=2.0, settings=settings, reasons=reasons,
  )
  assert reasons == ["mitigated"]


def test_validate_rejects_not_invalidated():
  settings = TechniqueGeometrySettings(pip_size=0.1)
  df = _df([
    (100, 101, 99, 100),
    (100, 101, 98, 97),
  ])
  instance = _valid_buy_instance(low=100.0, high=101.0, origin_index=0)
  reasons: list[str] = []
  assert not validate_technique_instance(
    instance, df, price=100.5, atr=2.0, settings=settings, reasons=reasons,
  )
  assert reasons == ["not_invalidated"]


def test_validate_rejects_proximal_retest():
  settings = TechniqueGeometrySettings(pip_size=0.1)
  df = _df([(100, 101, 99, 100)])
  instance = _valid_buy_instance(low=100.0, high=101.0, origin_index=0)
  reasons: list[str] = []
  assert not validate_technique_instance(
    instance, df, price=110.0, atr=2.0, settings=settings, reasons=reasons,
  )
  assert reasons == ["proximal_retest"]


def test_validate_rejects_fvg_min_width():
  settings = TechniqueGeometrySettings(pip_size=0.1, fvg_min_pips=2.0)
  df = _df([(100, 101, 99, 100.05)])
  instance = _valid_buy_instance(
    technique=TECHNIQUE_FVG, low=100.0, high=100.05, origin_index=0,
  )
  reasons: list[str] = []
  assert not validate_technique_instance(
    instance, df, price=100.05, atr=2.0, settings=settings, reasons=reasons,
  )
  assert reasons == ["fvg_min_width"]


def test_validate_rejects_fvg_not_fully_filled():
  settings = TechniqueGeometrySettings(pip_size=0.1)
  df = _df([
    (100, 101, 99, 100),
    (100, 101, 99.5, 100),
  ])
  instance = _valid_buy_instance(
    technique=TECHNIQUE_FVG, low=100.0, high=101.0, origin_index=0,
  )
  reasons: list[str] = []
  assert not validate_technique_instance(
    instance, df, price=100.5, atr=2.0, settings=settings, reasons=reasons,
  )
  assert reasons == ["fvg_not_fully_filled"]


def test_validate_rejects_width_within_atr():
  settings = TechniqueGeometrySettings(pip_size=0.1, max_zone_atr=1.0)
  df = _df([(100, 105, 99, 102)])
  instance = _valid_buy_instance(low=100.0, high=105.0, origin_index=0)
  reasons: list[str] = []
  assert not validate_technique_instance(
    instance, df, price=102.0, atr=1.0, settings=settings, reasons=reasons,
  )
  assert reasons == ["width_within_atr"]


def test_validate_rejects_ob_momentum_body():
  settings = TechniqueGeometrySettings(pip_size=0.1, momentum_body_frac=0.6)
  df = _df([(100, 101, 99.9, 100.01)])
  instance = _valid_buy_instance(
    technique=TECHNIQUE_OB,
    measured={"mitigated": False, "body_frac": 0.2, "has_bos": True},
    origin_index=0,
  )
  reasons: list[str] = []
  assert not validate_technique_instance(
    instance, df, price=100.01, atr=2.0, settings=settings, reasons=reasons,
  )
  assert reasons == ["ob_momentum_body"]


def test_validate_rejects_ob_missing_bos():
  settings = TechniqueGeometrySettings(pip_size=0.1)
  df = _df([(100, 101, 99, 100.5)])
  instance = _valid_buy_instance(
    technique=TECHNIQUE_OB,
    measured={"mitigated": False, "body_frac": 0.8, "has_bos": False},
    origin_index=0,
  )
  reasons: list[str] = []
  assert not validate_technique_instance(
    instance, df, price=100.5, atr=2.0, settings=settings, reasons=reasons,
  )
  assert reasons == ["ob_missing_bos"]


def test_collect_technique_instances_records_reject_counters():
  df = _df([(100, 101, 99, 100)])
  zones = [
    Zone(100.0, 101.0, "demand", origin_index=0, source="supply_demand"),
  ]
  instances, rejects = collect_technique_instances(
    sd_zones=zones, ob_zones=[], fvg_zones=[], df=df,
    price=110.0, atr=2.0,
    settings=TechniqueGeometrySettings(pip_size=0.1),
    validation_enabled=True,
  )
  assert instances == []
  assert rejects == {"proximal_retest": 1}


def test_collect_technique_instances_validation_disabled_skips_filter():
  df = _df([(100, 101, 99, 100)])
  zones = [
    Zone(100.0, 101.0, "demand", origin_index=0, source="supply_demand"),
  ]
  with_validation, rejects_on = collect_technique_instances(
    sd_zones=zones, ob_zones=[], fvg_zones=[], df=df,
    price=110.0, atr=2.0,
    settings=TechniqueGeometrySettings(pip_size=0.1),
    validation_enabled=True,
  )
  without_validation, rejects_off = collect_technique_instances(
    sd_zones=zones, ob_zones=[], fvg_zones=[], df=df,
    price=110.0, atr=2.0,
    settings=TechniqueGeometrySettings(pip_size=0.1),
    validation_enabled=False,
  )
  assert with_validation == []
  assert rejects_on == {"proximal_retest": 1}
  assert len(without_validation) == 1
  assert rejects_off == {}


# --- zone holds vs zone broken (owner 2026-09-21, XAU 4341-4353 demand) ------
#
# One M5 close 3.6 points under the zone (a liquidity sweep just before the
# week's biggest rally) used to kill it for good, though it then held on
# three more taps. A break is acceptance beyond the far edge, not a close
# through it.


def _closes(values: list[float]) -> pd.DataFrame:
  return _df([(v, v + 0.3, v - 0.3, v) for v in values])


def _holds(closes: list[float], **overrides) -> bool:
  settings = TechniqueGeometrySettings(pip_size=0.1, **overrides)
  return not_invalidated(
    side="buy", low=100.0, high=110.0, df=_closes(closes),
    origin_index=0, atr=4.0, settings=settings,
  )


def test_sweep_below_the_zone_and_reclaim_keeps_it_alive():
  # 96 is 4 below the low (> 0.5 ATR = 2.0 tolerance): a real close-through,
  # but the very next closes are back above 100 -> a sweep, not a break.
  assert _holds([105, 96, 101, 104, 106])


def test_unreclaimed_close_through_kills_the_zone():
  assert not _holds([105, 96, 95, 94, 95, 94, 95, 96, 95])


def test_shallow_close_inside_the_tolerance_is_not_a_break_at_all():
  # 99.0 is only 1.0 under the low, inside the 0.5 ATR (2.0) tolerance.
  assert _holds([105, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0, 99.0])


def test_a_break_still_in_progress_is_not_tradeable():
  # Closed through and has not reclaimed yet - do not buy into a zone
  # price is currently accepted below.
  assert not _holds([105, 104, 96])


def test_zone_swept_more_than_the_episode_budget_is_noise():
  sweeps = [105, 96, 101, 96, 101, 96, 101, 104]  # three forgiven sweeps
  assert not _holds(sweeps, max_break_episodes=2)
  assert _holds(sweeps, max_break_episodes=3)


def test_legacy_settings_restore_any_close_through_is_dead():
  legacy = dict(
    invalidation_tolerance_atr=0.0, sweep_reclaim_bars=0, max_break_episodes=0,
  )
  assert not _holds([105, 96, 101, 104], **legacy)


def _spent(*, mitigated=True, touches=8, closes=(105, 106, 107), **overrides) -> bool:
  settings = TechniqueGeometrySettings(pip_size=0.1, **overrides)
  return zone_is_spent(
    mitigated=mitigated, touches=touches, side="buy", low=100.0, high=110.0,
    df=_closes(list(closes)), origin_index=0, atr=4.0, settings=settings,
  )


def test_untouched_zone_is_never_spent():
  assert not _spent(mitigated=False, touches=0)


def test_touched_zone_that_still_holds_stays_tradeable():
  # 8 taps (XAU 4341-4353 had 8 by this morning) and never accepted below.
  assert not _spent(touches=8)


def test_touch_cap_is_only_a_sanity_bound():
  assert _spent(touches=31)
  assert not _spent(touches=31, retest_max_touches=60)


def test_retest_cap_zero_restores_first_touch_consumption():
  assert _spent(touches=1, retest_max_touches=0)


def test_violated_zone_flagged_mitigated_is_still_spent():
  # breaker_blocks stamps a violated OB mitigated=True, touches=1.
  assert _spent(touches=1, closes=(105, 96, 95, 94, 95, 94, 95, 96))


def test_touched_holding_demand_zone_becomes_an_instance_again():
  df = _closes([108, 104, 101, 106, 104, 105])
  zone = Zone(
    100.0, 110.0, "demand", origin_index=0, created_ts=df.index[0],
    source="supply_demand", touches=8, mitigated=True, score=14.5,
  )
  kwargs = dict(
    sd_zones=[zone], ob_zones=[], fvg_zones=[], df=df, price=105.0, atr=4.0,
  )
  new = TechniqueGeometrySettings(pip_size=0.1, max_zone_atr=4.0)
  old = TechniqueGeometrySettings(
    pip_size=0.1, max_zone_atr=4.0, retest_max_touches=0,
  )
  kept, _ = collect_technique_instances(settings=new, **kwargs)
  dropped, _ = collect_technique_instances(settings=old, **kwargs)
  assert len(kept) == 1
  assert dropped == []
