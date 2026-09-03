"""Math-defined SMC technique instances (T1–T5) and confluence band inputs.

Each technique is an independent geometric predicate on OHLC + ATR. When two
or more distinct techniques overlap on price, ``build_confluence_bands`` (in
``confluence_zone.py``) produces a Confluence Zone band — a separate publishable
setup with explicit technique tags.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

import pandas as pd

from app.analysis.structural_reaction_support import (
  band_touched,
  evaluate_structural_reaction,
)
from app.analysis.types import Zone
from app.analysis.zones import fvg, order_blocks, supply_demand
from app.autotrade.strategy_names import (
  CONFLUENCE_ZONE,
  CRT,
  FVG,
  IFVG,
  ORDER_BLOCK,
  SUPPLY_DEMAND,
)

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
  TECHNIQUE_SD: SUPPLY_DEMAND,
  TECHNIQUE_OB: ORDER_BLOCK,
  TECHNIQUE_FVG: FVG,
  TECHNIQUE_IFVG: IFVG,
  TECHNIQUE_CRT: CRT,
}

CONFLUENCE_SETUP_NAME = CONFLUENCE_ZONE


# Owner entry-band for FVG / iFVG / imbalance: keep the full gap for
# structure/mitigation, but every strategy's *entry* zone is the proximal
# slice capped at this price width (XAU: 5.0 == 50 pips at pip_size 0.1).
FVG_IMBALANCE_ENTRY_MAX_WIDTH_PRICE = 5.0

# CRT shares the same proximal price width contract as FVG by default
# (instrument packs scale via price_scale.fvg_entry_max_width_price). Full
# H1 candle range stays structural; tradeable entry is the reclaim edge.
CRT_ENTRY_MAX_WIDTH_PRICE = FVG_IMBALANCE_ENTRY_MAX_WIDTH_PRICE
CRT_H1_LOOKBACK_BARS = 3

_FVG_IMBALANCE_TOKENS = frozenset({
  "fvg",
  "ifvg",
  "imbalance",
  "bullish_fvg",
  "bearish_fvg",
  "fvg_ifvg",
})


@dataclass(frozen=True)
class TechniqueGeometrySettings:
  pip_size: float = 0.1
  epsilon_atr_frac: float = 0.05
  max_zone_atr: float = 3.0
  fvg_max_atr: float = 2.0
  fvg_min_pips: float = 1.0
  fvg_entry_max_width_price: float = FVG_IMBALANCE_ENTRY_MAX_WIDTH_PRICE
  momentum_body_frac: float = 0.6
  crt_min_atr: float = 1.5
  crt_reclaim_bars: int = 6
  crt_entry_max_width_price: float = CRT_ENTRY_MAX_WIDTH_PRICE
  crt_h1_lookback_bars: int = CRT_H1_LOOKBACK_BARS
  confluence_min_overlap: float = 0.5
  zone_merge_max_width: float = 6.0
  structural_reaction_lookback_bars: int = 3


def _token_is_fvg_imbalance(token: str) -> bool:
  text = str(token or "").strip().lower().replace("-", "_").replace("+", "_")
  if not text:
    return False
  if text in _FVG_IMBALANCE_TOKENS:
    return True
  parts = {part for part in text.split("_") if part}
  if parts & {"fvg", "ifvg", "imbalance"}:
    return True
  return text.endswith("_fvg")


def is_fvg_imbalance_zone(
  zone: Any,
  *,
  tags: Iterable[str] = (),
  structural_kind: str | None = None,
) -> bool:
  """True when the zone / tags are FVG, iFVG, or imbalance provenance."""
  tokens: list[str] = []
  source = getattr(zone, "source", None)
  if source:
    tokens.append(str(source))
  for item in getattr(zone, "sources", None) or ():
    tokens.append(str(item))
  for item in tags or ():
    tokens.append(str(item))
  if structural_kind:
    tokens.append(str(structural_kind))
  return any(_token_is_fvg_imbalance(token) for token in tokens)


def optimize_imbalance_entry_zone(
  zone: Zone,
  *,
  direction: str,
  max_width_price: float = FVG_IMBALANCE_ENTRY_MAX_WIDTH_PRICE,
  tags: Iterable[str] = (),
  structural_kind: str | None = None,
) -> tuple[Zone, bool]:
  """Clip FVG/imbalance entry to a proximal ``max_width_price`` band.

  Structural low/high for fill/mitigation stay with the caller; this only
  shrinks the tradeable entry zone so every strategy (FVG, iFVG, Confluence,
  zone reaction on an FVG member) shares the same 5-price proximal contract.
  """
  if not is_fvg_imbalance_zone(
    zone, tags=tags, structural_kind=structural_kind,
  ):
    return zone, False
  try:
    low = float(zone.low)
    high = float(zone.high)
    max_width = float(max_width_price)
  except (TypeError, ValueError):
    return zone, False
  if not math.isfinite(low) or not math.isfinite(high) or high <= low:
    return zone, False
  if not math.isfinite(max_width) or max_width <= 0:
    return zone, False
  width = high - low
  if width <= max_width + 1e-12:
    return zone, False
  direction_u = str(direction or "").upper()
  side = str(getattr(zone, "side", "") or "").lower()
  sell_side = direction_u == "SELL" or side == "supply"
  if sell_side:
    return replace(zone, bottom=low, top=low + max_width), True
  return replace(zone, bottom=high - max_width, top=high), True


def optimize_crt_entry_zone(
  zone: Zone,
  *,
  direction: str,
  max_width_price: float = CRT_ENTRY_MAX_WIDTH_PRICE,
) -> tuple[Zone, bool]:
  """Clip CRT tradeable entry to the swept reclaim extreme.

  Full H1 candle stays structural (stop / mitigation). Unlike FVG/SD/OB
  (``optimize_imbalance_entry_zone`` / ``optimize_technique_entry_zone``),
  which keep the *proximal* edge (supply → low, demand → high), CRT keeps
  the *swept* edge: BUY → low (sweep below range), SELL → high (sweep above).
  That is the far edge for supply and the far edge for demand — intentional.
  ``proximal_retest()`` on a clipped CRT sell therefore checks ``low`` (=
  ``high - width``), not the supply proximal bottom.
  """
  try:
    low = float(zone.low)
    high = float(zone.high)
    max_width = float(max_width_price)
  except (TypeError, ValueError):
    return zone, False
  if not math.isfinite(low) or not math.isfinite(high) or high <= low:
    return zone, False
  if not math.isfinite(max_width) or max_width <= 0:
    return zone, False
  if high - low <= max_width + 1e-12:
    return zone, False
  direction_u = str(direction or "").upper()
  side = str(getattr(zone, "side", "") or "").lower()
  sell_side = direction_u == "SELL" or side == "supply"
  if sell_side:
    return replace(zone, bottom=high - max_width, top=high), True
  return replace(zone, bottom=low, top=low + max_width), True


def optimize_technique_entry_zone(
  zone: Zone,
  *,
  max_width_price: float = FVG_IMBALANCE_ENTRY_MAX_WIDTH_PRICE,
) -> tuple[Zone, bool]:
  """Clip a Supply/Demand or Order Block zone to its proximal tradeable band.

  2026-08-23 dig: unlike FVG (``optimize_imbalance_entry_zone``, via
  ``_proximal_if_wide``) and CRT (``optimize_crt_entry_zone``), Supply
  Demand and Order Block technique instances were built straight from the
  raw multi-candle zone / single-candle OB body with no entry clip at all —
  ``instance_from_zone`` fed that full width directly into
  ``_publish_technique``'s stop-clearance calc as both structural AND
  entry bounds. On a fast pair (e.g. GBPJPY, ~180 pips/day) that routinely
  blew the stop envelope and killed the candidate between
  activation_allowed and plan_published every time (0 Order Block fills
  ever recorded). Same proximal-edge contract as FVG/CRT: SELL/supply
  keeps the near (lower) edge — price falling into a supply zone from
  below touches the bottom first; BUY/demand keeps the near (upper) edge.
  Full zone stays structural (mitigation / confirmation) via the caller
  stashing the pre-clip bounds.
  """
  try:
    low = float(zone.low)
    high = float(zone.high)
    max_width = float(max_width_price)
  except (TypeError, ValueError):
    return zone, False
  if not math.isfinite(low) or not math.isfinite(high) or high <= low:
    return zone, False
  if not math.isfinite(max_width) or max_width <= 0:
    return zone, False
  if high - low <= max_width + 1e-12:
    return zone, False
  side = str(getattr(zone, "side", "") or "").lower()
  if side == "supply":
    return replace(zone, bottom=low, top=low + max_width), True
  return replace(zone, bottom=high - max_width, top=high), True


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


# Technique instances built straight from a raw detector Zone (Supply
# Demand, Order Block, FVG) share the same missing-clip bug: the full
# multi-candle / gap width was used as both structural bounds and the
# tradeable entry, unlike CRT/iFVG's own dedicated builders. See
# optimize_technique_entry_zone's docstring for the incident.
_ENTRY_CLIPPABLE_TECHNIQUES = frozenset({TECHNIQUE_SD, TECHNIQUE_OB, TECHNIQUE_FVG})


def instance_from_zone(
  zone: Zone,
  *,
  technique: str | None = None,
  entry_max_width_price: float | None = None,
) -> TechniqueInstance | None:
  sources = tuple(zone.sources or ([zone.source] if zone.source else []))
  resolved = technique
  if resolved is None:
    for source in sources:
      resolved = _zone_technique(source)
      if resolved is not None:
        break
  if resolved is None:
    return None
  measured: dict[str, Any] = {
    "touches": int(zone.touches),
    "mitigated": bool(zone.mitigated),
    "score": float(getattr(zone, "score", 0.0)),
  }
  entry_zone = zone
  if resolved in _ENTRY_CLIPPABLE_TECHNIQUES and entry_max_width_price:
    clipped_zone, clipped = optimize_technique_entry_zone(
      zone, max_width_price=entry_max_width_price,
    )
    if clipped:
      measured["structural_low"] = float(zone.low)
      measured["structural_high"] = float(zone.high)
      measured["entry_clipped"] = True
      measured["entry_max_width_price"] = float(entry_max_width_price)
      entry_zone = clipped_zone
  return TechniqueInstance(
    technique=resolved,
    side=zone_side_to_trade_side(zone.side),
    low=float(entry_zone.low),
    high=float(entry_zone.high),
    origin_ts=zone.created_ts,
    sources=sources,
    measured=measured,
    origin_index=int(zone.origin_index),
  )


def not_invalidated(
  *,
  side: str,
  low: float,
  high: float,
  df: pd.DataFrame,
  origin_index: int,
  atr: float,
  settings: TechniqueGeometrySettings,
) -> bool:
  """No close through the far edge after origin (invalidation, not mitigation)."""
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


# Deprecated alias — ``mitigated`` in ``measured`` is first-touch consumption.
is_unmitigated = not_invalidated  # noqa: F841 — one-release alias


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
  reasons: list[str] | None = None,
) -> bool:
  direction = "BUY" if instance.side == "buy" else "SELL"
  if instance.measured.get("mitigated"):
    if reasons is not None:
      reasons.append("mitigated")
    return False
  if not not_invalidated(
    side=instance.side,
    low=instance.low,
    high=instance.high,
    df=df,
    origin_index=instance.origin_index,
    atr=atr,
    settings=settings,
  ):
    if reasons is not None:
      reasons.append("not_invalidated")
    return False
  if not proximal_retest(
    side=instance.side,
    low=instance.low,
    high=instance.high,
    price=price,
    atr=atr,
    settings=settings,
  ):
    if reasons is not None:
      reasons.append("proximal_retest")
    return False
  max_atr = settings.max_zone_atr
  if instance.technique == TECHNIQUE_FVG:
    width = instance.high - instance.low
    if width < settings.fvg_min_pips * settings.pip_size:
      if reasons is not None:
        reasons.append("fvg_min_width")
      return False
    max_atr = settings.fvg_max_atr
    if not fvg_not_fully_filled(
      side=instance.side,
      low=instance.low,
      high=instance.high,
      df=df,
      origin_index=instance.origin_index,
    ):
      if reasons is not None:
        reasons.append("fvg_not_fully_filled")
      return False
  if not width_within_atr(instance.low, instance.high, atr, max_atr):
    if reasons is not None:
      reasons.append("width_within_atr")
    return False
  if instance.technique == TECHNIQUE_OB:
    body_frac = float(instance.measured.get("body_frac", 0.0))
    if body_frac < settings.momentum_body_frac:
      if reasons is not None:
        reasons.append("ob_momentum_body")
      return False
    if not instance.measured.get("has_bos"):
      if reasons is not None:
        reasons.append("ob_missing_bos")
      return False
  if require_reaction and not has_structural_confirmation(
    df,
    direction=direction,
    low=instance.low,
    high=instance.high,
    lookback_bars=settings.structural_reaction_lookback_bars,
  ):
    if reasons is not None:
      reasons.append("structural_confirmation")
    return False
  return True


def discover_ifvg_instances(
  fvg_zones: Sequence[Zone],
  df: pd.DataFrame,
  *,
  settings: TechniqueGeometrySettings,
  entry_max_width_price: float | None = None,
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
    entry_lo, entry_hi = z_lo, z_hi
    measured: dict[str, Any] = {
      "inverted_from": zone.source, "invert_index": invert_index,
    }
    # Same missing-clip bug as Supply Demand / Order Block / FVG (see
    # optimize_technique_entry_zone) - the full original gap width was
    # used as the tradeable entry with no proximal clip. An inverted
    # "sell" flip behaves like a supply zone (keep the near/low edge);
    # an inverted "buy" flip behaves like demand (keep the near/high edge).
    if (
      entry_max_width_price
      and math.isfinite(entry_max_width_price)
      and entry_max_width_price > 0
      and (z_hi - z_lo) > entry_max_width_price + 1e-12
    ):
      if trade_side == "sell":
        entry_lo, entry_hi = z_lo, z_lo + entry_max_width_price
      else:
        entry_lo, entry_hi = z_hi - entry_max_width_price, z_hi
      measured["structural_low"] = z_lo
      measured["structural_high"] = z_hi
      measured["entry_clipped"] = True
      measured["entry_max_width_price"] = float(entry_max_width_price)
    instances.append(TechniqueInstance(
      technique=TECHNIQUE_IFVG,
      side=trade_side,
      low=entry_lo,
      high=entry_hi,
      origin_ts=df.index[invert_index],
      sources=("ifvg",),
      measured=measured,
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
  """T5 — H1 impulse range with sweep + reclaim on execution TF.

  Live dig 2026-08: publishing the full H1 candle as the entry zone made CRT
  fail width / stop-envelope gates (``zone_too_wide``,
  ``stop_exceeds_envelope_furthest_leg``) with zero plan publishes. Keep the
  H1 range as structural bounds; clip tradeable entry to the proximal
  reclaim band. Prefer closed H1 candles (skip the forming bar).
  """
  del exec_atr  # reserved for future ATR-scaled reclaim tolerances
  if h1_df.empty or exec_df.empty or h1_atr <= 0:
    return []
  instances: list[TechniqueInstance] = []
  min_range = settings.crt_min_atr * h1_atr
  reclaim = max(1, int(settings.crt_reclaim_bars))
  entry_max = float(settings.crt_entry_max_width_price)
  lookback = max(1, int(settings.crt_h1_lookback_bars))
  # Prefer closed H1: forming bar widens continuously and is not a CRT
  # "candle range" until it settles.
  closed = h1_df.iloc[:-1] if len(h1_df) >= 2 else h1_df
  if closed.empty:
    return []
  start = max(0, len(closed) - lookback)
  found_sides: set[str] = set()
  # Newest closed candle first so a fresh CRT wins over an older sibling.
  for row_pos in range(len(closed) - 1, start - 1, -1):
    row = closed.iloc[row_pos]
    range_high = float(row["high"])
    range_low = float(row["low"])
    range_width = range_high - range_low
    if range_width < min_range:
      continue
    mid = (range_high + range_low) / 2.0
    origin_index = row_pos  # closed is h1_df without the forming bar
    origin_ts = closed.index[row_pos]

    for side, direction in (("buy", "BUY"), ("sell", "SELL")):
      if side in found_sides:
        continue
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
      structural = Zone(
        range_low,
        range_high,
        trade_side_to_zone_side(side),
        source="crt",
        sources=["crt"],
        origin_index=origin_index,
      )
      entry_zone, clipped = optimize_crt_entry_zone(
        structural,
        direction=direction,
        max_width_price=entry_max,
      )
      instances.append(TechniqueInstance(
        technique=TECHNIQUE_CRT,
        side=side,
        low=float(entry_zone.low),
        high=float(entry_zone.high),
        origin_ts=origin_ts,
        sources=("crt",),
        measured={
          "h1_range_atr": range_width / h1_atr,
          "structural_low": range_low,
          "structural_high": range_high,
          "entry_clipped": clipped,
          "entry_max_width_price": entry_max,
        },
        origin_index=origin_index,
      ))
      found_sides.add(side)
    if len(found_sides) >= 2:
      break
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
  price: float,
  atr: float,
  h1_df: pd.DataFrame | None = None,
  h1_atr: float = 0.0,
  exec_atr: float = 0.0,
  settings: TechniqueGeometrySettings | None = None,
  validation_enabled: bool = True,
) -> tuple[list[TechniqueInstance], dict[str, int]]:
  """Unmerged technique instances for detector routing (map merge stays separate)."""
  settings = settings or TechniqueGeometrySettings()
  entry_max_width_price = float(settings.fvg_entry_max_width_price)
  pending: list[TechniqueInstance] = []
  for zone in sd_zones:
    if zone.mitigated:
      continue
    item = instance_from_zone(
      zone, technique=TECHNIQUE_SD, entry_max_width_price=entry_max_width_price,
    )
    if item is not None:
      pending.append(item)
  for zone in ob_zones:
    if zone.mitigated or zone.break_kind is None:
      continue
    item = instance_from_zone(
      zone, technique=TECHNIQUE_OB, entry_max_width_price=entry_max_width_price,
    )
    if item is not None:
      pending.append(item)
  for zone in fvg_zones:
    if zone.mitigated:
      continue
    item = instance_from_zone(
      zone, technique=TECHNIQUE_FVG, entry_max_width_price=entry_max_width_price,
    )
    if item is not None:
      pending.append(item)
  pending = enrich_ob_instances(pending, df)
  pending.extend(discover_ifvg_instances(
    fvg_zones, df, settings=settings, entry_max_width_price=entry_max_width_price,
  ))
  if h1_df is not None and not h1_df.empty:
    pending.extend(discover_crt_instances(
      h1_df,
      df,
      h1_atr=h1_atr,
      exec_atr=exec_atr,
      settings=settings,
    ))
  if not validation_enabled:
    return pending, {}
  instances: list[TechniqueInstance] = []
  rejects: dict[str, int] = {}
  for item in pending:
    fail_reasons: list[str] = []
    if validate_technique_instance(
      item,
      df,
      price=price,
      atr=atr,
      settings=settings,
      require_reaction=False,
      reasons=fail_reasons,
    ):
      instances.append(item)
    else:
      for reason in fail_reasons:
        rejects[reason] = rejects.get(reason, 0) + 1
  return instances, rejects


def instances_for_technique(
  instances: Sequence[TechniqueInstance],
  technique: str,
) -> list[TechniqueInstance]:
  return [item for item in instances if item.technique == technique]


def technique_display_tags(tags: Iterable[str]) -> str:
  parts = [TECHNIQUE_SHORT.get(str(tag), str(tag).upper()) for tag in sorted(set(tags))]
  return "+".join(parts)
