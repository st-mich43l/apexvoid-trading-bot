"""First-class structural reaction detectors and identity."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from app.analysis import detectors
from app.analysis.engine import AnalysisContext, TimeframeAnalysis
from app.analysis.technique_geometry import TECHNIQUE_SD, TechniqueInstance
from app.analysis.technique_detectors import supply_demand_technique_reaction
from app.analysis.structural_reaction_support import (
  STRUCTURAL_SETUPS,
  engulfing_on_bar,
  evaluate_structural_reaction,
  structural_thesis_id,
)
from app.analysis.structure import Level, Zone
from app.analysis.trendlines import Trendline
from app.analysis.types import Break, DealingRange, Grab, Pool, SessionLevel
from app.autotrade.execution_policy import strategy_family
from app.autotrade.multi_match import dedupe_matches, same_thesis
from app.autotrade.strategy_match import StrategyMatch


pytestmark = pytest.mark.no_database


def _df(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
  index = pd.date_range("2026-07-10", periods=len(rows), freq="5min", tz="UTC")
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=index,
  )


def _series(df: pd.DataFrame, value: float) -> pd.Series:
  return pd.Series([value] * len(df), index=df.index)


def _indicators(df: pd.DataFrame, *, atr: float = 3) -> detectors.IndicatorSet:
  return detectors.IndicatorSet(atr=_series(df, atr))


def _buy_rejection_df() -> pd.DataFrame:
  return _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (107, 109, 103, 104, 100),
    (104, 106, 102, 103, 100),
    (106, 110, 101, 109, 100),
  ])


def _sell_rejection_df() -> pd.DataFrame:
  return _df([
    (110, 112, 108, 110, 100),
    (109, 110, 101, 102, 100),
    (102, 107, 100, 106, 100),
    (106, 108, 104, 107, 100),
    (107, 112, 101, 103, 100),
  ])


def _ctx(
  df: pd.DataFrame,
  *,
  bias: str = "up",
  levels: list[Level] | None = None,
  zones: list[Zone] | None = None,
  breaks: list[Break] | None = None,
  grabs: list[Grab] | None = None,
  session_levels: list[SessionLevel] | None = None,
  trendlines: list[Trendline] | None = None,
  dealing_range: DealingRange | None = None,
) -> detectors.DetectionContext:
  tf = "M5"
  structure = detectors.StructureSet(
    swings=[],
    bias=bias,
    levels=levels or [],
    equal_levels=[],
    fvg_zones=[],
    order_blocks=[],
    breaks=breaks or [],
    zones=zones or [],
    liquidity_grabs=grabs or [],
    session_levels=session_levels or [],
    dealing_range=dealing_range,
    trendlines=trendlines or [],
  )
  return detectors.DetectionContext(
    symbol="XAU",
    tf=tf,
    frames={tf: df},
    indicators={tf: _indicators(df)},
    structures={tf: structure},
    htf_bias=bias,
    settings=detectors.DetectorSettings(confluence_floor=2),
  )


def _with_technique_instances(
  ctx: detectors.DetectionContext,
  instances: list[TechniqueInstance],
) -> detectors.DetectionContext:
  tf = ctx.tf.upper()
  df = ctx.frames[tf]
  analysis = AnalysisContext(
    frames=ctx.frames,
    per_tf={
      tf: TimeframeAnalysis(
        df=df,
        atr=ctx.indicators[tf].atr,
        swings=[],
        structure="trend",
        breaks=[],
        key_levels=[],
        legs=[],
        supply_demand_zones=[],
        order_blocks=[],
        flip_zones=[],
        fvg_zones=[],
        zones=ctx.structures[tf].zones,
        liquidity_pools=[],
        liquidity_grabs=[],
        momentum="neutral",
        technique_instances=instances,
      ),
    },
    htf_bias=ctx.htf_bias,
  )
  return replace(ctx, analysis=analysis)


def _sd_instance(zone: Zone) -> TechniqueInstance:
  return TechniqueInstance(
    technique=TECHNIQUE_SD,
    side="buy" if zone.side == "demand" else "sell",
    low=float(zone.low),
    high=float(zone.high),
    origin_ts=zone.created_ts,
    sources=tuple(zone.sources or [zone.source or "supply_demand"]),
    measured={"touches": zone.touches, "mitigated": zone.mitigated, "score": zone.score},
    origin_index=int(zone.origin_index),
  )


def test_default_detectors_exclude_zone_reaction():
  names = [item.__name__ for item in detectors.DEFAULT_DETECTORS]
  assert "zone_reaction" not in names
  for required in (
    "key_level_reaction",
    "confluence_zone_reaction",
    "supply_demand_technique_reaction",
    "order_block_technique_reaction",
    "fvg_technique_reaction",
    "ifvg_technique_reaction",
    "crt_technique_reaction",
    "flip_demand_zone_reaction",
    "flip_supply_zone_reaction",
  ):
    assert required in names
  assert "demand_zone_reaction" not in names
  assert "supply_zone_reaction" not in names


def test_live_registry_matches_detector_settings_defaults():
  """DEFAULT_DETECTORS is built from LIVE_DETECTOR_REGISTRY filtered by
  DetectorSettings defaults. momentum_ride is live; box_breakout/
  break_retest remain disabled (replay-only with reason).
  """
  names = {item.__name__ for item in detectors.DEFAULT_DETECTORS}

  assert names == {
    "key_level_reaction",
    "confluence_zone_reaction",
    "supply_demand_technique_reaction",
    "order_block_technique_reaction",
    "fvg_technique_reaction",
    "ifvg_technique_reaction",
    "crt_technique_reaction",
    "flip_demand_zone_reaction",
    "flip_supply_zone_reaction",
    "session_level_reaction",
    "trendline_reaction",
    "range_edge_scalp",
    "momentum_ride",
    "snap_back",
    "fade_scalp",
  }
  for disabled in (
    "box_breakout",
    "break_retest",
    "zone_reaction",
    "demand_zone_reaction",
    "supply_zone_reaction",
  ):
    assert disabled not in names, f"{disabled} must not be in the live registry"


def test_disabled_registry_entries_all_have_a_replay_only_reason():
  for registration in detectors.LIVE_DETECTOR_REGISTRY:
    if not registration.enabled(detectors.DetectorSettings()):
      assert registration.replay_only_reason, (
        f"{registration.name} is disabled by default but has no "
        "replay_only_reason - a disabled detector must never be silently "
        "unexplained"
      )


def test_build_default_detectors_honors_settings_not_just_defaults():
  all_on = detectors.DetectorSettings(
    box_breakout_enabled=True,
    break_retest_enabled=True,
    momentum_ride_enabled=True,
    snap_back_enabled=True,
    fade_scalp_enabled=True,
    zone_reaction_fallback_enabled=True,
  )
  names = {
    item.__name__ for item in detectors.build_default_detectors(all_on)
  }
  assert names == {
    "key_level_reaction",
    "confluence_zone_reaction",
    "supply_demand_technique_reaction",
    "order_block_technique_reaction",
    "fvg_technique_reaction",
    "ifvg_technique_reaction",
    "crt_technique_reaction",
    "demand_zone_reaction",
    "supply_zone_reaction",
    "flip_demand_zone_reaction",
    "flip_supply_zone_reaction",
    "session_level_reaction",
    "trendline_reaction",
    "range_edge_scalp",
    "box_breakout",
    "break_retest",
    "momentum_ride",
    "snap_back",
    "fade_scalp",
  }

  all_off = detectors.DetectorSettings(
    key_level_reaction_enabled=False,
    demand_reaction_enabled=False,
    supply_reaction_enabled=False,
    flip_zone_enabled=False,
    session_level_reaction_enabled=False,
    trendline_reaction_enabled=False,
    range_scalp_enabled=False,
    momentum_ride_enabled=False,
    snap_back_enabled=False,
    fade_scalp_enabled=False,
    technique_sd_enabled=False,
    technique_ob_enabled=False,
    technique_fvg_enabled=False,
    technique_ifvg_enabled=False,
    technique_crt_enabled=False,
    confluence_zone_enabled=False,
  )
  assert detectors.build_default_detectors(all_off) == ()


def test_live_detector_report_lists_every_registration_with_a_reason():
  report = detectors.live_detector_report(detectors.DetectorSettings())
  by_name = {row["name"]: row for row in report}
  assert len(report) == len(detectors.LIVE_DETECTOR_REGISTRY)
  assert by_name["key_level_reaction"]["enabled"] is True
  assert by_name["key_level_reaction"]["replay_only_reason"] is None
  assert by_name["box_breakout"]["enabled"] is False
  assert by_name["box_breakout"]["replay_only_reason"]


def test_supply_demand_technique_reaction_buy():
  df = _buy_rejection_df()
  zone = Zone(101, 106, "demand", source="supply_demand", score=10, touches=0)
  ctx = _with_technique_instances(
    _ctx(df, bias="down", zones=[zone]),
    [_sd_instance(zone)],
  )
  result = supply_demand_technique_reaction(ctx)
  assert result is not None
  assert result.setup == "Supply Demand"
  assert result.direction == "BUY"
  assert result.structural_source == "technique"
  assert result.structural_kind == TECHNIQUE_SD
  assert result.structural_id
  assert result.bias_relationship == "counter_bias"
  assert result.confirmation_type in {
    "wick_rejection", "strong_reclaim", "sweep_reclaim", "rejection_choch",
  }


def test_supply_demand_technique_reaction_uses_clipped_entry_when_provided():
  """2026-08-23 fix: instance_from_zone now clips SD/OB entries to a
  proximal band (structural_low/high keep the full raw zone). Confirms
  _publish_technique actually trades the clipped zone, not the raw one,
  and surfaces the proximal-entry reason."""
  df = _buy_rejection_df()
  zone = Zone(101, 106, "demand", source="supply_demand", score=10, touches=0)
  instance = _sd_instance(zone)
  clipped_instance = replace(
    instance,
    low=104.0,
    high=106.0,
    measured={
      **instance.measured,
      "structural_low": 101.0,
      "structural_high": 106.0,
      "entry_clipped": True,
      "entry_max_width_price": 2.0,
    },
  )
  ctx = _with_technique_instances(
    _ctx(df, bias="down", zones=[zone]), [clipped_instance],
  )
  result = supply_demand_technique_reaction(ctx)
  assert result is not None
  assert result.entry_zone.low == pytest.approx(104.0)
  assert result.entry_zone.high == pytest.approx(106.0)
  assert result.structural_low == pytest.approx(101.0)
  assert result.structural_high == pytest.approx(106.0)
  assert any("proximal supply_demand entry" in reason for reason in result.reasons)


def test_demand_zone_reaction_buy_legacy_fallback():
  df = _buy_rejection_df()
  zone = Zone(101, 106, "demand", source="supply_demand", score=10, touches=0)
  ctx = replace(
    _ctx(df, bias="down", zones=[zone]),
    settings=detectors.DetectorSettings(
      confluence_floor=2,
      zone_reaction_fallback_enabled=True,
    ),
  )
  result = detectors.demand_zone_reaction(ctx)
  assert result is not None
  assert result.setup == "Zone Reaction"
  assert result.direction == "BUY"


def test_supply_zone_reaction_sell():
  df = _sell_rejection_df()
  zone = Zone(107, 112, "supply", source="supply_demand", score=10, touches=0)
  ctx = replace(
    _ctx(df, bias="up", zones=[zone]),
    settings=detectors.DetectorSettings(
      confluence_floor=2,
      zone_reaction_fallback_enabled=True,
    ),
  )
  result = detectors.supply_zone_reaction(ctx)
  assert result is not None
  assert result.setup == "Zone Reaction"
  assert result.direction == "SELL"
  assert result.structural_kind == "supply"
  assert result.bias_relationship == "counter_bias"


def test_flip_demand_zone_reaction_buy():
  df = _buy_rejection_df()
  zone = Zone(101, 106, "demand", source="flip_zone", score=10, touches=0)
  result = detectors.flip_demand_zone_reaction(_ctx(df, bias="down", zones=[zone]))
  assert result is not None
  assert result.setup == "Flip Zone"
  assert result.direction == "BUY"
  assert result.structural_source == "flip_zone"
  assert result.structural_kind == "demand"
  assert strategy_family(result.setup) == "supply_demand"
  assert "Flip Zone" in STRUCTURAL_SETUPS


def test_flip_supply_zone_reaction_sell():
  df = _sell_rejection_df()
  zone = Zone(107, 112, "supply", source="flip_zone", score=10, touches=0)
  result = detectors.flip_supply_zone_reaction(_ctx(df, bias="up", zones=[zone]))
  assert result is not None
  assert result.setup == "Flip Zone"
  assert result.direction == "SELL"
  assert result.structural_source == "flip_zone"
  assert result.structural_kind == "supply"


def test_demand_zone_reaction_skips_flip_source():
  df = _buy_rejection_df()
  flip = Zone(101, 106, "demand", source="flip_zone", score=10, touches=0)
  fallback = detectors.DetectorSettings(
    confluence_floor=2,
    zone_reaction_fallback_enabled=True,
  )
  assert detectors.demand_zone_reaction(
    replace(_ctx(df, bias="down", zones=[flip]), settings=fallback),
  ) is None
  demand = Zone(101, 106, "demand", source="supply_demand", score=10, touches=0)
  assert detectors.demand_zone_reaction(
    replace(_ctx(df, bias="down", zones=[demand]), settings=fallback),
  ) is not None


def test_flip_zone_ignores_non_flip_source():
  df = _buy_rejection_df()
  demand = Zone(101, 106, "demand", source="supply_demand", score=10, touches=0)
  assert detectors.flip_demand_zone_reaction(
    _ctx(df, bias="down", zones=[demand]),
  ) is None


def test_key_level_support_buy_and_resistance_sell():
  buy_df = _buy_rejection_df()
  support = Level(105, "support", touches=3, strength=3)
  buy = detectors.key_level_reaction(
    _ctx(buy_df, bias="down", levels=[support]),
  )
  assert buy is not None
  assert buy.setup == "Key Level"
  assert buy.direction == "BUY"
  assert buy.structural_source == "key_level"
  assert buy.key_level_role == "support"

  sell_df = _sell_rejection_df()
  resistance = Level(107, "resistance", touches=3, strength=3)
  sell = detectors.key_level_reaction(
    _ctx(sell_df, bias="up", levels=[resistance]),
  )
  assert sell is not None
  assert sell.direction == "SELL"
  assert sell.key_level_role == "resistance"


def test_generic_key_level_remains_ambiguous_raw_observation():
  result = detectors.key_level_reaction(_ctx(
    _buy_rejection_df(),
    bias="down",
    levels=[Level(105, "reaction", touches=3, strength=3)],
  ))

  assert result is not None
  assert result.key_level_role == "ambiguous"


def test_key_level_require_explicit_role_skips_ambiguous():
  settings = detectors.DetectorSettings(
    confluence_floor=2,
    key_level_require_explicit_role=True,
  )
  result = detectors.key_level_reaction(replace(
    _ctx(
      _buy_rejection_df(),
      bias="down",
      levels=[Level(105, "reaction", touches=3, strength=3)],
    ),
    settings=settings,
  ))
  assert result is None


def test_key_level_counter_bias_buy_and_sell_still_publish():
  """Counter-bias stays live on both sides (no HTF veto on Key Level)."""
  support = Level(105, "support", touches=3, strength=3)
  buy = detectors.key_level_reaction(
    _ctx(_buy_rejection_df(), bias="down", levels=[support]),
  )
  assert buy is not None
  assert buy.direction == "BUY"
  assert buy.bias_relationship == "counter_bias"

  resistance = Level(107, "resistance", touches=3, strength=3)
  sell = detectors.key_level_reaction(
    _ctx(_sell_rejection_df(), bias="up", levels=[resistance]),
  )
  assert sell is not None
  assert sell.direction == "SELL"
  assert sell.bias_relationship == "counter_bias"


def test_key_level_min_sell_zone_score_filters_weak_supply():
  resistance = Level(107, "resistance", touches=3, strength=3)
  weak = Zone(105, 109, "supply", source="supply_demand", score=5.0, touches=1)
  strong = Zone(105, 109, "supply", source="supply_demand", score=12.0, touches=2)
  settings = detectors.DetectorSettings(
    confluence_floor=2,
    key_level_min_sell_zone_score=10.0,
  )
  # Counter-bias SELL (HTF up) with weak nearest supply → skip.
  assert detectors.key_level_reaction(replace(
    _ctx(_sell_rejection_df(), bias="up", levels=[resistance], zones=[weak]),
    settings=settings,
  )) is None
  # Same counter-bias path with strong supply → keep.
  kept = detectors.key_level_reaction(replace(
    _ctx(_sell_rejection_df(), bias="up", levels=[resistance], zones=[strong]),
    settings=settings,
  ))
  assert kept is not None
  assert kept.direction == "SELL"
  assert kept.bias_relationship == "counter_bias"


def test_accepted_resistance_break_is_not_key_level_reaction():
  df = _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (107, 109, 103, 104, 100),
    (104, 108, 102, 107, 100),
    (106, 110, 101, 109, 100),
  ])
  resistance = Level(105, "resistance", touches=3, strength=3)

  assert detectors.key_level_reaction(
    _ctx(df, bias="up", levels=[resistance]),
  ) is None


def test_session_level_pdl_buy_and_pdh_sell():
  buy_df = _buy_rejection_df()
  pdl = SessionLevel("PDL", 105.0, buy_df.index[-1], swept=False)
  buy = detectors.session_level_reaction(
    _ctx(buy_df, bias="range", session_levels=[pdl]),
  )
  assert buy is not None
  assert buy.setup == "Session Level"
  assert buy.direction == "BUY"
  assert buy.structural_kind == "PDL"

  sell_df = _sell_rejection_df()
  pdh = SessionLevel("PDH", 107.0, sell_df.index[-1], swept=False)
  sell = detectors.session_level_reaction(
    _ctx(sell_df, bias="range", session_levels=[pdh]),
  )
  assert sell is not None
  assert sell.direction == "SELL"
  assert sell.structural_kind == "PDH"


def test_trendline_unbroken_support_and_resistance():
  buy_df = _buy_rejection_df()
  support = Trendline(
    "support", (0, 2, 4), 0.0, 105.0, touches=3, broken=False, break_index=None,
    fit_error_atr=0.0, violations=0, bars_since_last_touch=0, span_bars=4,
    exhausted=False,
  )
  buy = detectors.trendline_reaction(
    _ctx(buy_df, bias="up", trendlines=[support]),
  )
  assert buy is not None
  assert buy.setup == "Trendline"
  assert buy.direction == "BUY"

  sell_df = _sell_rejection_df()
  resistance = Trendline(
    "resistance",
    (0, 2, 4),
    0.0,
    107.0,
    touches=3,
    broken=False,
    break_index=None,
    fit_error_atr=0.0,
    violations=0,
    bars_since_last_touch=0,
    span_bars=4,
    exhausted=False,
  )
  sell = detectors.trendline_reaction(
    _ctx(sell_df, bias="down", trendlines=[resistance]),
  )
  assert sell is not None
  assert sell.direction == "SELL"


def test_broken_trendline_is_not_trendline_reaction():
  df = _buy_rejection_df()
  broken = Trendline(
    "support",
    (0, 2),
    0.0,
    105.0,
    touches=3,
    broken=True,
    break_index=2,
    fit_error_atr=0.0,
    violations=0,
    bars_since_last_touch=0,
    span_bars=2,
    exhausted=False,
  )
  assert detectors.trendline_reaction(_ctx(df, trendlines=[broken])) is None


def test_no_confirmation_yields_no_match():
  # Price never revisits demand in the lookback window.
  df = _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (110, 112, 109, 111, 100),
    (111, 113, 110, 112, 100),
    (112, 114, 111, 113, 100),
  ])
  zone = Zone(101, 106, "demand", source="supply_demand", score=10)
  conf = evaluate_structural_reaction(
    df, direction="BUY", low=101, high=106, lookback_bars=3,
  )
  assert conf is None
  assert detectors.demand_zone_reaction(_ctx(df, zones=[zone])) is None


def test_touch_prior_bar_confirmation_within_lookback():
  # Touch on bar -2 (deep into demand), confirmation on last bar.
  df = _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (107, 109, 103, 104, 100),
    (104, 106, 100.5, 103, 100),  # touch demand
    (106, 110, 101, 109, 100),  # bullish rejection confirmation
  ])
  conf = evaluate_structural_reaction(
    df,
    direction="BUY",
    low=100,
    high=106,
    lookback_bars=3,
  )
  assert conf is not None
  assert conf.touch_index <= conf.confirmation_index
  assert conf.confirmation_type
  # Prior bar also touched; confirmation may land on the same closed bar.
  assert any(
    conf.touch_index == idx or idx < conf.confirmation_index
    for idx in range(max(0, len(df) - 3), len(df))
  )
  zone = Zone(100, 106, "demand", source="supply_demand", score=10)
  instance = _sd_instance(zone)
  result = supply_demand_technique_reaction(
    replace(
      _with_technique_instances(_ctx(df, zones=[zone]), [instance]),
      settings=detectors.DetectorSettings(
        confluence_floor=2,
        structural_reaction_lookback_bars=3,
        max_entry_atr=5.0,
      ),
    )
  )
  assert result is not None
  assert result.setup == "Supply Demand"


def test_confirmation_older_than_lookback_rejected():
  df = _df([
    (106, 110, 101, 109, 100),  # old rejection
    (109, 111, 108, 110, 100),
    (110, 112, 109, 111, 100),
    (111, 113, 110, 112, 100),
    (112, 114, 111, 113, 100),
  ])
  conf = evaluate_structural_reaction(
    df,
    direction="BUY",
    low=101,
    high=106,
    lookback_bars=2,
  )
  assert conf is None


def test_engulfing_on_bar_bullish_and_bearish():
  bullish_prior = pd.Series({"open": 103.0, "high": 104.0, "low": 102.0, "close": 102.5})
  bullish_engulf = pd.Series({"open": 102.0, "high": 106.5, "low": 101.8, "close": 106.0})
  assert engulfing_on_bar(bullish_engulf, bullish_prior, "BUY") is True
  assert engulfing_on_bar(bullish_engulf, bullish_prior, "SELL") is False

  bearish_prior = pd.Series({"open": 102.5, "high": 104.0, "low": 102.0, "close": 103.0})
  bearish_engulf = pd.Series({"open": 106.0, "high": 106.5, "low": 101.8, "close": 102.0})
  assert engulfing_on_bar(bearish_engulf, bearish_prior, "SELL") is True
  assert engulfing_on_bar(bearish_engulf, bearish_prior, "BUY") is False

  # Body does not fully cover the prior bar's body -> not an engulfing bar.
  partial_prior = pd.Series({"open": 103.0, "high": 104.0, "low": 102.0, "close": 100.0})
  small_bar = pd.Series({"open": 102.0, "high": 103.0, "low": 101.5, "close": 102.8})
  assert engulfing_on_bar(small_bar, partial_prior, "BUY") is False


def test_engulfing_confirms_a_slow_grind_reaction_with_no_rejection_wick():
  # Live gap: a multi-hour chop right at a demand zone (small-bodied
  # consolidation candles, no single dramatic rejection wick) never
  # satisfies wick_rejection_on_bar/strong_reclaim_on_bar, so
  # Demand Zone Reaction stayed silent for hours despite repeated genuine
  # touches. A bullish engulfing candle - a well-established reversal
  # confirmation on its own - is exactly the pattern that shape of reaction
  # actually produces.
  df = _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (107, 109, 103, 104, 100),
    (104, 106, 100.5, 103, 100),  # small-bodied touch at demand
    (102, 106.5, 101.8, 106, 100),  # bullish engulfing confirmation
  ])
  conf = evaluate_structural_reaction(
    df, direction="BUY", low=100, high=106, lookback_bars=3,
  )
  assert conf is not None
  assert conf.confirmation_type == "engulfing"


def test_engulfing_never_overrides_a_stronger_confirmation():
  # Additive only: when a bar independently qualifies as an engulfing bar
  # AND some stronger existing pattern, the existing pattern must still
  # win - engulfing is checked last in the confirmation chain. (This bar
  # happens to also sweep below the zone and reclaim, so strong_reclaim
  # wins; the point being proven is simply that it is never "engulfing".)
  df = _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (107, 109, 103, 104, 100),
    (104, 106, 100.5, 103, 100),  # small-bodied touch at demand
    (102, 106, 98, 105, 100),  # engulfing-shaped, but also a sweep+reclaim
  ])
  conf = evaluate_structural_reaction(
    df, direction="BUY", low=100, high=106, lookback_bars=3,
  )
  assert conf is not None
  assert engulfing_on_bar(df.iloc[-1], df.iloc[-2], "BUY")
  assert conf.confirmation_type != "engulfing"
  assert conf.confirmation_type == "strong_reclaim"


def test_strategy_family_and_stable_thesis_identity():
  assert strategy_family("Key Level") == "key_level"
  assert strategy_family("Zone Reaction") == "supply_demand"
  assert strategy_family("Flip Zone") == "supply_demand"
  assert strategy_family("Demand Zone Reaction") == "supply_demand"
  assert strategy_family("Supply Zone Reaction") == "supply_demand"
  assert strategy_family("Session Level") == "session_level"
  assert strategy_family("Trendline") == "trendline"
  assert strategy_family("Mapped Zone Reaction") == "mapped_zone_reaction"

  first = structural_thesis_id(
    symbol="XAU",
    strategy="Demand Zone Reaction",
    direction="BUY",
    structural_source="supply_demand",
    structural_id="abc",
    touch_bar_ts="t1",
    confirmation_bar_ts="c1",
  )
  moved_entry = structural_thesis_id(
    symbol="XAU",
    strategy="Demand Zone Reaction",
    direction="BUY",
    structural_source="supply_demand",
    structural_id="abc",
    touch_bar_ts="t1",
    confirmation_bar_ts="c1",
  )
  canonical = structural_thesis_id(
    symbol="XAU",
    strategy="Zone Reaction",
    direction="BUY",
    structural_source="supply_demand",
    structural_id="abc",
    touch_bar_ts="t1",
    confirmation_bar_ts="c1",
  )
  supply_legacy = structural_thesis_id(
    symbol="XAU",
    strategy="Supply Zone Reaction",
    direction="BUY",
    structural_source="supply_demand",
    structural_id="abc",
    touch_bar_ts="t1",
    confirmation_bar_ts="c1",
  )
  other = structural_thesis_id(
    symbol="XAU",
    strategy="Demand Zone Reaction",
    direction="BUY",
    structural_source="supply_demand",
    structural_id="xyz",
    touch_bar_ts="t1",
    confirmation_bar_ts="c1",
  )
  assert first == moved_entry
  assert first == canonical
  assert first == supply_legacy
  assert first != other


def test_legacy_zone_reaction_aliases_same_thesis_without_shared_sid():
  demand = replace(
    _match(match_id="demand-legacy"),
    strategy="Demand Zone Reaction",
    structural_zone_id=None,
    zone_id=None,
  )
  zone = replace(
    _match(match_id="zone-canonical"),
    strategy="Zone Reaction",
    structural_zone_id=None,
    zone_id=None,
  )
  assert same_thesis(demand, zone, atr=2.0)
  kept, _ = dedupe_matches([demand, zone], atr=2.0)
  assert len(kept) == 1


def test_legacy_zone_reaction_aliases_same_thesis_with_shared_sid():
  demand = replace(
    _match(match_id="demand-sid"),
    strategy="Demand Zone Reaction",
    structural_zone_id="sid-shared",
  )
  zone = replace(
    _match(match_id="zone-sid"),
    strategy="Zone Reaction",
    structural_zone_id="sid-shared",
  )
  assert same_thesis(demand, zone, atr=2.0)


def _match(**kwargs) -> StrategyMatch:
  base = dict(
    version=1,
    match_id="m1",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-07-10T00:00:00+00:00",
    issued_at=1,
    expires_at=1000,
    strategy="Demand Zone Reaction",
    strategy_mode="counter_bias",
    direction="BUY",
    key_level=105.0,
    entry_low=104.0,
    entry_high=106.0,
    current_price=105.5,
    confluence=3,
    reasons=("demand",),
    atr=2.0,
    structure_swing=104.0,
    targets_pips=(30, 60),
    family="supply_demand",
    structural_source="supply_demand",
    zone_id="sid-1",
    structural_zone_id="sid-1",
    touch_bar_ts="t1",
    confirmation_bar_ts="c1",
  )
  base.update(kwargs)
  return StrategyMatch(**base)


def test_cross_strategy_dedup_prefers_first_class():
  demand = _match(match_id="demand")
  pullback = _match(
    match_id="pullback",
    strategy="Trend Pullback",
    family="trend_pullback",
    structural_source="Trend Pullback",
    zone_id="float:104:106",
    structural_zone_id=None,
    confirmation_bar_ts="c1",
  )
  assert same_thesis(demand, pullback, atr=2.0)
  kept, events = dedupe_matches([pullback, demand], atr=2.0)
  assert len(kept) == 1
  assert kept[0].strategy == "Demand Zone Reaction"
  assert any(item["event"] == "merged_confluence" for item in events)


def test_independent_sources_remain_separate():
  demand = _match(match_id="d", structural_zone_id="demand-1", zone_id="demand-1")
  key = _match(
    match_id="k",
    strategy="Key Level",
    family="key_level",
    structural_source="key_level",
    structural_zone_id="key-1",
    zone_id="key-1",
    entry_low=110.0,
    entry_high=112.0,
    key_level=111.0,
  )
  assert not same_thesis(demand, key, atr=2.0)
  kept, _ = dedupe_matches([demand, key], atr=2.0)
  assert len(kept) == 2


def test_overlapping_key_levels_same_confirmation_are_one_thesis():
  first = _match(
    match_id="dac0ac35aaaa",
    strategy="Key Level",
    family="key_level",
    structural_source="key_level",
    structural_zone_id="47519286aaaa",
    zone_id="47519286aaaa",
    entry_low=215.898,
    entry_high=215.921,
    key_level=215.85,
    confirmation_bar_ts="c-gbpjpy",
    touch_bar_ts="t-gbpjpy",
  )
  second = _match(
    match_id="ca1c22e73aaa",
    strategy="Key Level",
    family="key_level",
    structural_source="key_level",
    structural_zone_id="90824b10aaaa",
    zone_id="90824b10aaaa",
    entry_low=215.898,
    entry_high=215.935,
    key_level=215.85,
    confirmation_bar_ts="c-gbpjpy",
    touch_bar_ts="t-gbpjpy",
  )
  assert same_thesis(first, second, atr=0.15)
  kept, _ = dedupe_matches([first, second], atr=0.15)
  assert len(kept) == 1


def test_structural_setups_constant():
  assert STRUCTURAL_SETUPS == {
    "Key Level",
    "Zone Reaction",
    "Flip Zone",
    "Demand Zone Reaction",
    "Supply Zone Reaction",
    "Session Level",
    "Trendline",
    "Supply Demand",
    "Order Block",
    "FVG",
    "iFVG",
    "CRT",
    "Confluence Zone",
  }


# --- Candle Confirmation V2 compatibility (§21/§28) -----------------------
# candle_evidence flows from ReactionConfirmation through to DetectionResult
# as shadow telemetry only; confirmation_type is unchanged either way.


def test_reaction_confirmation_carries_candle_evidence_for_the_confirmation_bar():
  buy_df = _buy_rejection_df()
  confirmation = evaluate_structural_reaction(
    buy_df, direction="BUY", low=99.0, high=105.0, lookback_bars=3, atr=1.0,
  )
  assert confirmation is not None
  assert confirmation.candle_evidence is not None
  assert 0.0 <= confirmation.candle_evidence.final_score <= 1.0


def test_detection_result_candle_telemetry_matches_confirmation_and_never_changes_it():
  buy_df = _buy_rejection_df()
  support = Level(105, "support", touches=3, strength=3)
  result = detectors.key_level_reaction(_ctx(buy_df, bias="down", levels=[support]))
  assert result is not None
  original_confirmation_type = result.confirmation_type

  # Same detection, computed twice - the candle_* telemetry must be
  # deterministic and must never move confirmation_type.
  again = detectors.key_level_reaction(_ctx(buy_df, bias="down", levels=[support]))
  assert again is not None
  assert again.confirmation_type == original_confirmation_type
  assert result.candle_version == 2
  assert result.candle_final_score is not None
  assert 0.0 <= result.candle_final_score <= 1.0
  assert result.candle_patterns is not None
  assert result.candle_primary_pattern in result.candle_patterns.split(",")
