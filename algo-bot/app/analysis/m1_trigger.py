"""M1 candlestick trigger (Codex Prompt P3, Part 2).

Once a setup is confirmed on M5 at a `ConfluenceZone`, the entry fires only
when a qualifying M1 candle prints at the zone. M1 is a trigger here, never a
setup source (that role was removed from M1 entirely in P2) - this module
only ever answers "did the already-decided zone/direction just get a
qualifying M1 candle", it never proposes a zone or a direction of its own.

Boundary rule: algo-bot is the only place candlestick patterns are
evaluated. The C# executor's `market_watch` entry stays mechanical
(quote-in-zone -> submit market) - the trigger decision already happened
here, before the plan was ever published (see worker.py::_publish_trade_plan_v7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


ALL_PATTERNS = (
  "wick_rejection",
  "body_close",
  "strong_close",
  "pin_bar",
  "engulfing",
  "hammer",
)


@dataclass(frozen=True)
class M1TriggerResult:
  pattern: str
  direction: str
  wick_extreme: float
  bar_ts: Any
  message: str


@dataclass(frozen=True)
class _BarGeometry:
  open: float
  high: float
  low: float
  close: float
  range_: float
  body: float
  upper_wick: float
  lower_wick: float


def _geometry(bar: pd.Series) -> _BarGeometry | None:
  o = float(bar["open"])
  h = float(bar["high"])
  l = float(bar["low"])
  c = float(bar["close"])
  range_ = h - l
  if range_ <= 0:
    return None
  body = abs(c - o)
  upper_wick = h - max(o, c)
  lower_wick = min(o, c) - l
  return _BarGeometry(o, h, l, c, range_, body, upper_wick, lower_wick)


def enabled_patterns(cfg: Any) -> set[str]:
  raw = str(getattr(cfg, "m1_trigger_patterns", ",".join(ALL_PATTERNS)) or "")
  configured = {item.strip() for item in raw.split(",") if item.strip()}
  return configured & set(ALL_PATTERNS)


def _wick_rejection(
  bar: pd.Series, geo: _BarGeometry, *, direction: str,
  zone_low: float, zone_high: float, cfg: Any,
) -> M1TriggerResult | None:
  wick_fraction = float(getattr(cfg, "m1_trigger_wick_fraction", 0.5))
  touches_zone = geo.low <= zone_high and geo.high >= zone_low
  if not touches_zone:
    return None
  if direction == "BUY":
    if geo.close <= zone_high or geo.lower_wick / geo.range_ < wick_fraction:
      return None
    return M1TriggerResult(
      "wick_rejection", direction, geo.low, bar.name,
      f"wicked to {geo.low:.2f} into zone, closed {geo.close:.2f} back above",
    )
  if geo.close >= zone_low or geo.upper_wick / geo.range_ < wick_fraction:
    return None
  return M1TriggerResult(
    "wick_rejection", direction, geo.high, bar.name,
    f"wicked to {geo.high:.2f} into zone, closed {geo.close:.2f} back below",
  )


def _body_close(
  bar: pd.Series, geo: _BarGeometry, *, direction: str,
  key_level: float, cfg: Any,
) -> M1TriggerResult | None:
  if direction == "BUY":
    if geo.close <= key_level:
      return None
    return M1TriggerResult(
      "body_close", direction, geo.low, bar.name,
      f"closed {geo.close:.2f} beyond key level {key_level:.2f}",
    )
  if geo.close >= key_level:
    return None
  return M1TriggerResult(
    "body_close", direction, geo.high, bar.name,
    f"closed {geo.close:.2f} beyond key level {key_level:.2f}",
  )


def _strong_close(
  bar: pd.Series, geo: _BarGeometry, *, direction: str, cfg: Any,
) -> M1TriggerResult | None:
  strong_pct = float(getattr(cfg, "m1_trigger_strong_close_pct", 0.2))
  if direction == "BUY":
    if (geo.high - geo.close) / geo.range_ > strong_pct:
      return None
    return M1TriggerResult(
      "strong_close", direction, geo.low, bar.name,
      f"closed in top {strong_pct:.0%} of range",
    )
  if (geo.close - geo.low) / geo.range_ > strong_pct:
    return None
  return M1TriggerResult(
    "strong_close", direction, geo.high, bar.name,
    f"closed in bottom {strong_pct:.0%} of range",
  )


_PIN_BAR_WICK_FRACTION = 0.66
_PIN_BAR_BODY_FRACTION = 0.2
_PIN_BAR_OPPOSITE_WICK_FRACTION = 0.15


def _pin_bar(
  bar: pd.Series, geo: _BarGeometry, *, direction: str, cfg: Any,
) -> M1TriggerResult | None:
  if geo.body / geo.range_ > _PIN_BAR_BODY_FRACTION:
    return None
  if direction == "BUY":
    if (
      geo.lower_wick / geo.range_ < _PIN_BAR_WICK_FRACTION
      or geo.upper_wick / geo.range_ > _PIN_BAR_OPPOSITE_WICK_FRACTION
    ):
      return None
    return M1TriggerResult(
      "pin_bar", direction, geo.low, bar.name,
      "long lower wick, small body - bullish pin bar",
    )
  if (
    geo.upper_wick / geo.range_ < _PIN_BAR_WICK_FRACTION
    or geo.lower_wick / geo.range_ > _PIN_BAR_OPPOSITE_WICK_FRACTION
  ):
    return None
  return M1TriggerResult(
    "pin_bar", direction, geo.high, bar.name,
    "long upper wick, small body - bearish pin bar",
  )


def _engulfing(
  bar: pd.Series, geo: _BarGeometry, prior: pd.Series | None, *,
  direction: str, cfg: Any,
) -> M1TriggerResult | None:
  if prior is None:
    return None
  prior_open = float(prior["open"])
  prior_close = float(prior["close"])
  body_low = min(geo.open, geo.close)
  body_high = max(geo.open, geo.close)
  prior_low = min(prior_open, prior_close)
  prior_high = max(prior_open, prior_close)
  if direction == "BUY":
    if geo.close <= geo.open:  # must itself be a bullish bar
      return None
    if not (body_low <= prior_low and body_high >= prior_high):
      return None
    return M1TriggerResult(
      "engulfing", direction, geo.low, bar.name,
      "bullish body engulfs prior bar's body",
    )
  if geo.close >= geo.open:
    return None
  if not (body_low <= prior_low and body_high >= prior_high):
    return None
  return M1TriggerResult(
    "engulfing", direction, geo.high, bar.name,
    "bearish body engulfs prior bar's body",
  )


_HAMMER_WICK_MULTIPLE = 2.0
_HAMMER_BODY_FRACTION = 0.3
_HAMMER_OPPOSITE_WICK_FRACTION = 0.25


def _hammer(
  bar: pd.Series, geo: _BarGeometry, *, direction: str,
  zone_low: float, zone_high: float, cfg: Any,
) -> M1TriggerResult | None:
  touches_zone = geo.low <= zone_high and geo.high >= zone_low
  if not touches_zone or geo.body / geo.range_ > _HAMMER_BODY_FRACTION:
    return None
  if direction == "BUY":
    if (
      geo.lower_wick < _HAMMER_WICK_MULTIPLE * max(geo.body, 1e-9)
      or geo.upper_wick / geo.range_ > _HAMMER_OPPOSITE_WICK_FRACTION
    ):
      return None
    return M1TriggerResult(
      "hammer", direction, geo.low, bar.name,
      "hammer: long lower wick, small body, at zone",
    )
  if (
    geo.upper_wick < _HAMMER_WICK_MULTIPLE * max(geo.body, 1e-9)
    or geo.lower_wick / geo.range_ > _HAMMER_OPPOSITE_WICK_FRACTION
  ):
    return None
  return M1TriggerResult(
    "hammer", direction, geo.high, bar.name,
    "shooting star: long upper wick, small body, at zone",
  )


_DETECTORS = {
  "wick_rejection": lambda bar, geo, prior, direction, zone_low, zone_high,
    key_level, cfg: _wick_rejection(
      bar, geo, direction=direction, zone_low=zone_low, zone_high=zone_high, cfg=cfg,
    ),
  "body_close": lambda bar, geo, prior, direction, zone_low, zone_high,
    key_level, cfg: _body_close(bar, geo, direction=direction, key_level=key_level, cfg=cfg),
  "strong_close": lambda bar, geo, prior, direction, zone_low, zone_high,
    key_level, cfg: _strong_close(bar, geo, direction=direction, cfg=cfg),
  "pin_bar": lambda bar, geo, prior, direction, zone_low, zone_high,
    key_level, cfg: _pin_bar(bar, geo, direction=direction, cfg=cfg),
  "engulfing": lambda bar, geo, prior, direction, zone_low, zone_high,
    key_level, cfg: _engulfing(bar, geo, prior, direction=direction, cfg=cfg),
  "hammer": lambda bar, geo, prior, direction, zone_low, zone_high,
    key_level, cfg: _hammer(
      bar, geo, direction=direction, zone_low=zone_low, zone_high=zone_high, cfg=cfg,
    ),
}


def evaluate_m1_trigger(
  m1: pd.DataFrame,
  *,
  zone_low: float,
  zone_high: float,
  key_level: float,
  direction: str,
  cfg: Any = None,
) -> M1TriggerResult | None:
  """Evaluate the latest CLOSED M1 bar against every enabled pattern.

  `m1` must contain only closed bars (the caller's normal closed-bar
  discipline - this function never special-cases an in-progress bar). Returns
  the first qualifying pattern's result (patterns are checked in
  `ALL_PATTERNS` order, so `wick_rejection` wins ties over eg. `strong_close`
  on the same bar), or None if no enabled pattern qualifies.
  """
  if m1 is None or m1.empty:
    return None
  direction = direction.upper()
  if direction not in {"BUY", "SELL"}:
    return None
  bar = m1.iloc[-1]
  geo = _geometry(bar)
  if geo is None:
    return None
  prior = m1.iloc[-2] if len(m1) >= 2 else None
  patterns = enabled_patterns(cfg)
  for pattern in ALL_PATTERNS:
    if pattern not in patterns:
      continue
    detector = _DETECTORS[pattern]
    result = detector(bar, geo, prior, direction, zone_low, zone_high, key_level, cfg)
    if result is not None:
      return result
  return None
