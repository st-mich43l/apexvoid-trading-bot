"""Math-defined SMC technique instances (T1–T5) and confluence band inputs.

Each technique is an independent geometric predicate on OHLC + ATR. When two
or more distinct techniques overlap on price, ``build_confluence_bands`` (in
``confluence_zone.py``) produces a Confluence Zone band — a separate publishable
setup with explicit technique tags.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import pandas as pd

from app.analysis.structural_reaction_support import (
  band_touched,
  evaluate_structural_reaction,
)
from app.analysis.types import Zone
from app.analysis.zones import fvg, order_blocks, supply_demand

TECHNIQUE_SD = "supply_demand"
TECHNIQUE_OB = "order_block"
TECHNIQUE_FVG = "fvg"
TECHNIQUE_IFVG = "ifvg"
TECHNIQUE_CRT = "crt"

TECHNIQUE_TAGS = frozenset({
  TECHNIQUE_SD,
  TECHNIQUE_OB,
  TECHNIQUE_FVG,
  TECHNIQUE_IFVG,
  TECHNIQUE_CRT,
})

TECHNIQUE_SHORT = {
  TECHNIQUE_SD: "SD",
  TECHNIQUE_OB: "OB",
  TECHNIQUE_FVG: "FVG",
  TECHNIQUE_IFVG: "iFVG",
  TECHNIQUE_CRT: "CRT",
}

TECHNIQUE_SETUP_NAMES = {
  TECHNIQUE_SD: "Supply Demand",
  TECHNIQUE_OB: "Order Block",
  TECHNIQUE_FVG: "FVG",
  TECHNIQUE_IFVG: "iFVG",
  TECHNIQUE_CRT: "CRT",
}

CONFLUENCE_SETUP_NAME = "Confluence Zone"


@dataclass(frozen=True)
class TechniqueGeometrySettings:
  pip_size: float = 0.1
  epsilon_atr_frac: float = 0.05
  max_zone_atr: float = 3.0
  fvg_max_atr: float = 2.0
  fvg_min_pips: float = 1.0
  momentum_body_frac: float = 0.6
  crt_min_atr: float = 1.5
  crt_reclaim_bars: int = 6
  confluence_min_overlap: float = 0.5
  zone_merge_max_width: float = 6.0
  structural_reaction_lookback_bars: int = 3


@dataclass(frozen=True)
class TechniqueInstance:
  technique: str
  side: str  # "buy" | "sell"
  low: float
  high: float
  origin_ts: pd.Timestamp | None
  sources: tuple[str, ...]
  measured: dict[str, Any] = field(default_factory=dict)
  instance_id: str = ""
  origin_index: int = -1

  def __post_init__(self) -> None:
    if self.low > self.high:
      object.__setattr__(self, "low", self.high)
      object.__setattr__(self, "high", self.low)
    if not self.instance_id:
      raw = (
        f"{self.technique}|{self.side}|{self.low:.5f}|{self.high:.5f}|"
        f"{self.origin_index}|{','.join(self.sources)}"
      )
      object.__setattr__(
        self,
        "instance_id",
        hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24],
      )


def epsilon(*, pip_size: float, atr: float, settings: TechniqueGeometrySettings) -> float:
  return max(float(pip_size), float(settings.epsilon_atr_frac) * max(0.0, float(atr)))


def zone_side_to_trade_side(zone_side: str) -> str:
  return "buy" if str(zone_side).casefold() == "demand" else "sell"


def trade_side_to_zone_side(trade_side: str) -> str:
  return "demand" if str(trade_side).casefold() == "buy" else "supply"


def overlap_ratio(
  a_low: float,
  a_high: float,
  b_low: float,
  b_high: float,
) -> float:
  overlap = min(a_high, b_high) - max(a_low, b_low)
  if overlap <= 0:
    return 0.0
  smaller = min(a_high - a_low, b_high - b_low)
  if smaller <= 0:
    return 1.0 if a_low <= b_high and b_low <= a_high else 0.0
  return overlap / smaller


def _zone_technique(source: str) -> str | None:
  src = str(source or "").casefold()
  if src == "supply_demand":
    return TECHNIQUE_SD
  if src == "order_block":
    return TECHNIQUE_OB
  if src.endswith("_fvg"):
    return TECHNIQUE_FVG
  if src == "ifvg":
    return TECHNIQUE_IFVG
  if src == "crt":
    return TECHNIQUE_CRT
  return None


def instance_from_zone(zone: Zone, *, technique: str | None = None) -> TechniqueInstance | None:
  sources = tuple(zone.sources or ([zone.source] if zone.source else []))
  resolved = technique
  if resolved is None:
    for source in sources:
      resolved = _zone_technique(source)
      if resolved is not None:
        break
  if resolved is None:
    return None
  return TechniqueInstance(
    technique=resolved,
    side=zone_side_to_trade_side(zone.side),
    low=float(zone.low),
    high=float(zone.high),
    origin_ts=zone.created_ts,
    sources=sources,
    measured={
      "touches": int(zone.touches),
      "mitigated": bool(zone.mitigated),
      "score": float(getattr(zone, "score", 0.0)),
    },
    origin_index=int(zone.origin_index),
  )


def is_unmitigated(
  *,
  side: str,
  low: float,
  high: float,
  df: pd.DataFrame,
  origin_index: int,
  atr: float,
  settings: TechniqueGeometrySettings,
) -> bool:
  """No close through the far edge after origin."""
  if df.empty or origin_index < 0:
    return True
  e = epsilon(pip_size=settings.pip_size, atr=atr, settings=settings)
  start = max(0, origin_index + 1)
  for index in range(start, len(df)):
    close = float(df.iloc[index]["close"])
    if side == "buy" and close < float(low) - e:
      return False
    if side == "sell" and close > float(high) + e:
      return False
  return True


def proximal_retest(
  *,
  side: str,
  low: float,
  high: float,
  price: float,
  atr: float,
  settings: TechniqueGeometrySettings,
) -> bool:
  e = epsilon(pip_size=settings.pip_size, atr=atr, settings=settings)
  if side == "buy":
    proximal = float(high)
    return abs(price - proximal) <= e or (float(low) - e <= price <= float(high) + e)
  proximal = float(low)
  return abs(price - proximal) <= e or (float(low) - e <= price <= float(high) + e)


def width_within_atr(
  low: float,
  high: float,
  atr: float,
  max_atr: float,
) -> bool:
  if atr <= 0:
    return True
  return (float(high) - float(low)) <= max(0.0, float(max_atr)) * atr


def fvg_not_fully_filled(
  *,
  side: str,
  low: float,
  high: float,
  df: pd.DataFrame,
  origin_index: int,
) -> bool:
  """Gap not fully closed by subsequent price."""
  if df.empty or origin_index < 0:
    return True
  for index in range(origin_index + 1, len(df)):
    row = df.iloc[index]
    if side == "buy":
      if float(row["low"]) <= float(low):
        return False
    else:
      if float(row["high"]) >= float(high):
        return False
  return True


def has_structural_confirmation(
  df: pd.DataFrame,
  *,
  direction: str,
  low: float,
  high: float,
  lookback_bars: int,
) -> bool:
  return evaluate_structural_reaction(
    df,
    direction=direction,
    low=low,
    high=high,
    lookback_bars=lookback_bars,
    grabs=[],
    has_choch=False,
  ) is not None


def validate_technique_instance(
  instance: TechniqueInstance,
  df: pd.DataFrame,
  *,
  price: float,
  atr: float,
  settings: TechniqueGeometrySettings,
  require_reaction: bool = True,
) -> bool:
  direction = "BUY" if instance.side == "buy" else "SELL"
  if instance.measured.get("mitigated"):
    return False
  if not is_unmitigated(
    side=instance.side,
    low=instance.low,
    high=instance.high,
    df=df,
    origin_index=instance.origin_index,
    atr=atr,
    settings=settings,
  ):
    return False
  if not proximal_retest(
    side=instance.side,
    low=instance.low,
    high=instance.high,
    price=price,
    atr=atr,
    settings=settings,
  ):
    return False
  max_atr = settings.max_zone_atr
  if instance.technique == TECHNIQUE_FVG:
    width = instance.high - instance.low
    if width < settings.fvg_min_pips * settings.pip_size:
      return False
    max_atr = settings.fvg_max_atr
    if not fvg_not_fully_filled(
      side=instance.side,
      low=instance.low,
      high=instance.high,
      df=df,
      origin_index=instance.origin_index,
    ):
      return False
  if not width_within_atr(instance.low, instance.high, atr, max_atr):
    return False
  if instance.technique == TECHNIQUE_OB:
    body_frac = float(instance.measured.get("body_frac", 0.0))
    if body_frac < settings.momentum_body_frac:
      return False
    if not instance.measured.get("has_bos"):
      return False
  if require_reaction and not has_structural_confirmation(
    df,
    direction=direction,
    low=instance.low,
    high=instance.high,
    lookback_bars=settings.structural_reaction_lookback_bars,
  ):
    return False
  return True


def discover_ifvg_instances(
  fvg_zones: Sequence[Zone],
  df: pd.DataFrame,
  *,
  settings: TechniqueGeometrySettings,
) -> list[TechniqueInstance]:
  """T4 — first close through gap flips side."""
  instances: list[TechniqueInstance] = []
  for zone in fvg_zones:
    if zone.origin_index < 0:
      continue
    z_lo, z_hi = float(zone.low), float(zone.high)
    inverted_side: str | None = None
    invert_index = -1
    for index in range(zone.origin_index + 1, len(df)):
      close = float(df.iloc[index]["close"])
      if zone.side == "demand" and close < z_lo:
        inverted_side = "sell"
        invert_index = index
        break
      if zone.side == "supply" and close > z_hi:
        inverted_side = "buy"
        invert_index = index
        break
    if inverted_side is None:
      continue
    # Second invalidating close through from new side.
    trade_side = inverted_side
    for index in range(invert_index + 1, len(df)):
      close = float(df.iloc[index]["close"])
      if trade_side == "buy" and close < z_lo:
        inverted_side = None
        break
      if trade_side == "sell" and close > z_hi:
        inverted_side = None
        break
    if inverted_side is None:
      continue
    instances.append(TechniqueInstance(
      technique=TECHNIQUE_IFVG,
      side=trade_side,
      low=z_lo,
      high=z_hi,
      origin_ts=df.index[invert_index],
      sources=("ifvg",),
      measured={"inverted_from": zone.source, "invert_index": invert_index},
      origin_index=invert_index,
    ))
  return instances


def discover_crt_instances(
  h1_df: pd.DataFrame,
  exec_df: pd.DataFrame,
  *,
  h1_atr: float,
  exec_atr: float,
  settings: TechniqueGeometrySettings,
) -> list[TechniqueInstance]:
  """T5 — H1 impulse range with sweep + reclaim on execution TF."""
  if h1_df.empty or exec_df.empty or h1_atr <= 0:
    return []
  instances: list[TechniqueInstance] = []
  min_range = settings.crt_min_atr * h1_atr
  reclaim = max(1, int(settings.crt_reclaim_bars))
  row = h1_df.iloc[-1]
  range_high = float(row["high"])
  range_low = float(row["low"])
  range_width = range_high - range_low
  if range_width < min_range:
    return instances
  mid = (range_high + range_low) / 2.0

  for side, edge, direction in (
    ("buy", range_low, "BUY"),
    ("sell", range_high, "SELL"),
  ):
    swept = False
    sweep_index = -1
    for index in range(max(0, len(exec_df) - reclaim - 5), len(exec_df)):
      bar = exec_df.iloc[index]
      if side == "buy" and float(bar["low"]) < range_low:
        swept = True
        sweep_index = index
      if side == "sell" and float(bar["high"]) > range_high:
        swept = True
        sweep_index = index
    if not swept:
      continue
    reclaimed = False
    for index in range(sweep_index, min(len(exec_df), sweep_index + reclaim + 1)):
      close = float(exec_df.iloc[index]["close"])
      if side == "buy" and close >= range_low:
        reclaimed = True
        break
      if side == "sell" and close <= range_high:
        reclaimed = True
        break
    if not reclaimed:
      continue
    price = float(exec_df.iloc[-1]["close"])
    if side == "buy" and price > mid:
      continue
    if side == "sell" and price < mid:
      continue
    if not has_structural_confirmation(
      exec_df,
      direction=direction,
      low=range_low,
      high=range_high,
      lookback_bars=settings.structural_reaction_lookback_bars,
    ):
      continue
    instances.append(TechniqueInstance(
      technique=TECHNIQUE_CRT,
      side=side,
      low=range_low,
      high=range_high,
      origin_ts=h1_df.index[-1],
      sources=("crt",),
      measured={"h1_range_atr": range_width / h1_atr},
      origin_index=len(h1_df) - 1,
    ))
  return instances


def _ob_body_frac(df: pd.DataFrame, origin: int) -> float:
  if origin < 0 or origin >= len(df):
    return 0.0
  row = df.iloc[origin]
  body = abs(float(row["close"]) - float(row["open"]))
  full = max(float(row["high"]) - float(row["low"]), 1e-9)
  return body / full


def enrich_ob_instances(
  instances: Iterable[TechniqueInstance],
  df: pd.DataFrame,
) -> list[TechniqueInstance]:
  enriched: list[TechniqueInstance] = []
  for item in instances:
    if item.technique != TECHNIQUE_OB:
      enriched.append(item)
      continue
    body_frac = _ob_body_frac(df, item.origin_index)
    measured = {**item.measured, "body_frac": body_frac, "has_bos": True}
    enriched.append(TechniqueInstance(
      technique=item.technique,
      side=item.side,
      low=item.low,
      high=item.high,
      origin_ts=item.origin_ts,
      sources=item.sources,
      measured=measured,
      instance_id=item.instance_id,
      origin_index=item.origin_index,
    ))
  return enriched


def collect_technique_instances(
  *,
  sd_zones: Sequence[Zone],
  ob_zones: Sequence[Zone],
  fvg_zones: Sequence[Zone],
  df: pd.DataFrame,
  h1_df: pd.DataFrame | None = None,
  h1_atr: float = 0.0,
  exec_atr: float = 0.0,
  settings: TechniqueGeometrySettings | None = None,
) -> list[TechniqueInstance]:
  """Unmerged technique instances for detector routing (map merge stays separate)."""
  settings = settings or TechniqueGeometrySettings()
  instances: list[TechniqueInstance] = []
  for zone in sd_zones:
    if zone.mitigated:
      continue
    item = instance_from_zone(zone, technique=TECHNIQUE_SD)
    if item is not None:
      instances.append(item)
  for zone in ob_zones:
    if zone.mitigated or zone.break_kind is None:
      continue
    item = instance_from_zone(zone, technique=TECHNIQUE_OB)
    if item is not None:
      instances.append(item)
  for zone in fvg_zones:
    if zone.mitigated:
      continue
    item = instance_from_zone(zone, technique=TECHNIQUE_FVG)
    if item is not None:
      instances.append(item)
  instances = enrich_ob_instances(instances, df)
  instances.extend(discover_ifvg_instances(fvg_zones, df, settings=settings))
  if h1_df is not None and not h1_df.empty:
    instances.extend(discover_crt_instances(
      h1_df,
      df,
      h1_atr=h1_atr,
      exec_atr=exec_atr,
      settings=settings,
    ))
  return instances


def instances_for_technique(
  instances: Sequence[TechniqueInstance],
  technique: str,
) -> list[TechniqueInstance]:
  return [item for item in instances if item.technique == technique]


def technique_display_tags(tags: Iterable[str]) -> str:
  parts = [TECHNIQUE_SHORT.get(str(tag), str(tag).upper()) for tag in sorted(set(tags))]
  return "+".join(parts)
