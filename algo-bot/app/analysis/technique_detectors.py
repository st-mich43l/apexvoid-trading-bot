"""Named technique + confluence zone publishers (T1–T5 + Confluence Zone)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from app.analysis.confluence_zone import (
  build_confluence_bands,
  confluence_band_covers_instance,
)
from app.analysis.technique_geometry import (
  CONFLUENCE_SETUP_NAME,
  TECHNIQUE_CRT,
  TECHNIQUE_FVG,
  TECHNIQUE_IFVG,
  TECHNIQUE_OB,
  TECHNIQUE_SD,
  TECHNIQUE_SETUP_NAMES,
  TechniqueInstance,
  technique_display_tags,
  trade_side_to_zone_side,
)
from app.analysis.structural_reaction_support import evaluate_structural_reaction
from app.analysis.types import Zone

if TYPE_CHECKING:
  from app.analysis.detectors import (
    ConfluenceFactors,
    DetectionContext,
    DetectionResult,
  )


def _technique_instances(ctx: "DetectionContext") -> list[TechniqueInstance]:
  if ctx.analysis is None:
    return []
  tf_analysis = ctx.analysis.per_tf.get(ctx.tf.upper())
  if tf_analysis is None:
    return []
  return list(tf_analysis.technique_instances)


def _confluence_bands_for_ctx(ctx: "DetectionContext"):
  from app.analysis.confluence_zone import ConfluenceBand

  instances = _technique_instances(ctx)
  if not instances:
    return []
  df, ind, _st = _exec(ctx)
  atr = _atr(ind)
  pip_size = max(float(ctx.settings.pip_size), 1e-12)
  return build_confluence_bands(
    instances,
    symbol=ctx.symbol,
    atr=atr,
    pip_size=pip_size,
    source_tf=ctx.tf,
    min_overlap=ctx.settings.zone_merge_overlap,
    max_width=(
      float(ctx.settings.max_merged_zone_atr) * max(atr, pip_size)
    ),
  )


def _exec(ctx: "DetectionContext"):
  from app.analysis.detectors import _exec as detectors_exec
  return detectors_exec(ctx)


def _atr(ind) -> float:
  from app.analysis.detectors import _atr as detectors_atr
  return detectors_atr(ind)


def _current_price(ctx: "DetectionContext", df) -> float:
  from app.analysis.detectors import _current_price as detectors_price
  return detectors_price(ctx, df)


def _instance_to_zone(instance: TechniqueInstance) -> Zone:
  return Zone(
    instance.low,
    instance.high,
    trade_side_to_zone_side(instance.side),
    origin_index=instance.origin_index,
    created_ts=instance.origin_ts,
    source=instance.technique,
    sources=list(instance.sources),
    touches=int(instance.measured.get("touches", 0)),
    mitigated=bool(instance.measured.get("mitigated", False)),
    score=float(instance.measured.get("score", 0.0)),
  )


def _publish_technique(
  ctx: "DetectionContext",
  instance: TechniqueInstance,
  *,
  setup: str,
) -> "DetectionResult | None":
  from app.analysis.detectors import (
    STAR_TWO_SCORE,
    _bias_for_direction,
    _entry_valid_for_settings,
    _finish,
    _recent_choch_flag,
    _structural_finish,
    _zone_grabs_for,
    _zone_key,
    ConfluenceFactors,
    zone_structural_id,
  )
  from app.analysis.detectors import _number

  df, ind, st = _exec(ctx)
  if len(df) < 3:
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  if instance.measured.get("mitigated"):
    return None
  direction = "BUY" if instance.side == "buy" else "SELL"
  zone = _instance_to_zone(instance)
  structural_low = float(instance.measured.get("structural_low", zone.low))
  structural_high = float(instance.measured.get("structural_high", zone.high))
  if not _entry_valid_for_settings(zone, price, atr, direction, ctx.settings):
    return None
  lookback = max(1, int(ctx.settings.structural_reaction_lookback_bars))
  # CRT confirmation uses the full H1 candle; entry zone is the proximal clip.
  conf_low = structural_low
  conf_high = structural_high
  conf = evaluate_structural_reaction(
    df,
    direction=direction,
    low=conf_low,
    high=conf_high,
    lookback_bars=lookback,
    grabs=_zone_grabs_for(st, zone, direction),
    has_choch=_recent_choch_flag(st, direction, len(df), ctx.settings, lookback),
  )
  if conf is None:
    return None
  factors = ConfluenceFactors(
    htf_aligned=ctx.htf_bias == _bias_for_direction(direction),
    touches=int(zone.touches),
    wick_rejection=True,
    structural_agreement=True,
    displacement_grade=float(getattr(zone, "score", 0.0)) >= STAR_TWO_SCORE,
  )
  side_label = trade_side_to_zone_side(instance.side)
  reasons = [
    f"{side_label} {instance.technique} {_number(zone.low)}-{_number(zone.high)}",
    technique_display_tags([instance.technique]),
  ]
  if instance.measured.get("entry_clipped"):
    structural_label = "H1" if instance.technique == TECHNIQUE_CRT else "structural"
    reasons.append(
      f"proximal {instance.technique} entry ({structural_label} "
      f"{_number(structural_low)}-{_number(structural_high)})"
    )
  result = _structural_finish(
    ctx,
    setup=setup,
    direction=direction,
    level=_zone_key(zone, price, direction),
    zone=zone,
    price=price,
    atr=atr,
    reasons=reasons,
    structural_source="technique",
    structural_id=instance.instance_id,
    structural_low=structural_low,
    structural_high=structural_high,
    structural_kind=instance.technique,
    confirmation=conf,
    source_touches=int(zone.touches),
    source_score=float(getattr(zone, "score", 0.0)),
    factors=factors,
  )
  if result is None:
    return None
  return replace(result, confluence_tags=(instance.technique,))


def _technique_reaction(
  ctx: "DetectionContext",
  *,
  technique: str,
  enabled: bool,
) -> "DetectionResult | None":
  if not enabled:
    return None
  bands = _confluence_bands_for_ctx(ctx)
  for instance in _technique_instances(ctx):
    if instance.technique != technique:
      continue
    if any(confluence_band_covers_instance(band, instance) for band in bands):
      continue
    result = _publish_technique(
      ctx, instance, setup=TECHNIQUE_SETUP_NAMES[technique],
    )
    if result is not None:
      return result
  return None


def supply_demand_technique_reaction(ctx: "DetectionContext"):
  return _technique_reaction(
    ctx, technique=TECHNIQUE_SD, enabled=ctx.settings.technique_sd_enabled,
  )


def order_block_technique_reaction(ctx: "DetectionContext"):
  return _technique_reaction(
    ctx, technique=TECHNIQUE_OB, enabled=ctx.settings.technique_ob_enabled,
  )


def fvg_technique_reaction(ctx: "DetectionContext"):
  return _technique_reaction(
    ctx, technique=TECHNIQUE_FVG, enabled=ctx.settings.technique_fvg_enabled,
  )


def ifvg_technique_reaction(ctx: "DetectionContext"):
  return _technique_reaction(
    ctx, technique=TECHNIQUE_IFVG, enabled=ctx.settings.technique_ifvg_enabled,
  )


def crt_technique_reaction(ctx: "DetectionContext"):
  return _technique_reaction(
    ctx, technique=TECHNIQUE_CRT, enabled=ctx.settings.technique_crt_enabled,
  )


def confluence_zone_reaction(ctx: "DetectionContext") -> "DetectionResult | None":
  from app.analysis.detectors import (
    STAR_TWO_SCORE,
    _bias_for_direction,
    _entry_valid_for_settings,
    _recent_choch_flag,
    _structural_finish,
    _zone_grabs_for,
    _zone_key,
    ConfluenceFactors,
  )
  from app.analysis.detectors import _number

  if not ctx.settings.confluence_zone_enabled:
    return None
  df, ind, st = _exec(ctx)
  if len(df) < 3:
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  lookback = max(1, int(ctx.settings.structural_reaction_lookback_bars))
  best = None
  for band in _confluence_bands_for_ctx(ctx):
    direction = "BUY" if band.side == "buy" else "SELL"
    zone = Zone(
      band.low,
      band.high,
      trade_side_to_zone_side(band.side),
      source="confluence",
      sources=list(band.technique_tags),
      score=band.score,
    )
    if not _entry_valid_for_settings(zone, price, atr, direction, ctx.settings):
      continue
    conf = evaluate_structural_reaction(
      df,
      direction=direction,
      low=float(zone.low),
      high=float(zone.high),
      lookback_bars=lookback,
      grabs=_zone_grabs_for(st, zone, direction),
      has_choch=_recent_choch_flag(st, direction, len(df), ctx.settings, lookback),
    )
    if conf is None:
      continue
    tag_text = technique_display_tags(band.technique_tags)
    reasons = [
      f"confluence {tag_text} {_number(zone.low)}-{_number(zone.high)}",
      f"techniques {len(band.technique_tags)}",
    ]
    factors = ConfluenceFactors(
      htf_aligned=ctx.htf_bias == _bias_for_direction(direction),
      touches=0,
      wick_rejection=True,
      structural_agreement=True,
      displacement_grade=band.score >= STAR_TWO_SCORE,
    )
    candidate = _structural_finish(
      ctx,
      setup=CONFLUENCE_SETUP_NAME,
      direction=direction,
      level=_zone_key(zone, price, direction),
      zone=zone,
      price=price,
      atr=atr,
      reasons=reasons,
      structural_source="confluence",
      structural_id=band.zone_id,
      structural_low=float(zone.low),
      structural_high=float(zone.high),
      structural_kind=tag_text,
      confirmation=conf,
      source_score=band.score,
      factors=factors,
    )
    if candidate is None:
      continue
    candidate = replace(
      candidate,
      confluence_zone_id=band.zone_id,
      confluence_tags=band.technique_tags,
    )
    if best is None or candidate.confluence > best.confluence:
      best = candidate
  return best
