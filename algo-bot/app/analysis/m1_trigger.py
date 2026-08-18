"""M1 candlestick trigger (Codex Prompt P3, Part 2).

Once a setup is confirmed on M5 at a `ConfluenceZone`, a qualifying M1 candle
can refine timing and anchor the stop wick. M1 is optional evidence here,
never a setup source (that role was removed from M1 entirely in P2) - this
module only answers whether the already-decided zone/direction just printed a
qualifying candle; it never proposes a zone or direction of its own.

Boundary rule: algo-bot is the only place candlestick patterns are
evaluated. The C# executor's `market_watch` entry stays mechanical; optional
candlestick evidence is evaluated here before publication.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.autotrade.execution_confirmation import parse_bar_timestamp


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
  measured: dict[str, Any] | None = None


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


def _price(cfg: Any, value: float) -> str:
  units = getattr(cfg, "units", None)
  digits = getattr(units, "price_digits", None)
  if digits is None:
    digits = getattr(
      getattr(getattr(cfg, "contract", None), "instrument", None),
      "price_digits",
      2,
    )
  return f"{float(value):.{max(0, int(digits))}f}"


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


def _intersects_zone(
  geo: _BarGeometry,
  *,
  zone_low: float,
  zone_high: float,
) -> bool:
  return geo.low <= zone_high and geo.high >= zone_low


def enabled_patterns(cfg: Any) -> set[str]:
  raw = str(cfg.analysis.triggers.m1.patterns or "")
  configured = {item.strip() for item in raw.split(",") if item.strip()}
  return configured & set(ALL_PATTERNS)


def _wick_rejection(
  bar: pd.Series, geo: _BarGeometry, *, direction: str,
  zone_low: float, zone_high: float, cfg: Any,
) -> M1TriggerResult | None:
  wick_fraction = float(cfg.analysis.triggers.m1.wick_fraction)
  if direction == "BUY":
    if geo.close <= zone_high or geo.lower_wick / geo.range_ < wick_fraction:
      return None
    return M1TriggerResult(
      "wick_rejection", direction, geo.low, bar.name,
      f"wicked to {_price(cfg, geo.low)} into zone, closed "
      f"{_price(cfg, geo.close)} back above",
    )
  if geo.close >= zone_low or geo.upper_wick / geo.range_ < wick_fraction:
    return None
  return M1TriggerResult(
    "wick_rejection", direction, geo.high, bar.name,
    f"wicked to {_price(cfg, geo.high)} into zone, closed "
    f"{_price(cfg, geo.close)} back below",
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
      f"closed {_price(cfg, geo.close)} beyond key level "
      f"{_price(cfg, key_level)}",
    )
  if geo.close >= key_level:
    return None
  return M1TriggerResult(
    "body_close", direction, geo.high, bar.name,
    f"closed {_price(cfg, geo.close)} beyond key level "
    f"{_price(cfg, key_level)}",
  )


def _strong_close(
  bar: pd.Series, geo: _BarGeometry, *, direction: str,
  zone_low: float, zone_high: float, cfg: Any,
) -> M1TriggerResult | None:
  """Require a directional strong close relative to the zone, not just the bar.

  A BUY close near the top of its own candle is insufficient when price remains
  below the zone midpoint / failed to reclaim. Mirrored for SELL.
  """
  strong_pct = float(cfg.analysis.triggers.m1.strong_close_pct)
  zone_mid = (float(zone_low) + float(zone_high)) / 2.0
  measured = {
    "bar_open": geo.open,
    "bar_high": geo.high,
    "bar_low": geo.low,
    "bar_close": geo.close,
    "zone_low": float(zone_low),
    "zone_high": float(zone_high),
    "zone_mid": zone_mid,
    "close_relative_to_zone": (geo.close - float(zone_low)) / max(
      float(zone_high) - float(zone_low), 1e-9,
    ),
  }
  if direction == "BUY":
    if (geo.high - geo.close) / geo.range_ > strong_pct:
      return None
    if geo.close <= geo.open:
      return None
    if geo.close < zone_mid:
      return None
    if geo.close < float(zone_low):
      return None
    closed_away = geo.close >= float(zone_high)
    measured["closed_away_from_zone"] = closed_away
    return M1TriggerResult(
      "strong_close",
      direction,
      geo.low,
      bar.name,
      (
        f"closed {_price(cfg, geo.close)} in top {strong_pct:.0%} of range "
        f"and toward/above zone mid {_price(cfg, zone_mid)}"
        + ("; reclaim above proximal" if closed_away else "")
      ),
      measured=measured,
    )
  if (geo.close - geo.low) / geo.range_ > strong_pct:
    return None
  if geo.close >= geo.open:
    return None
  if geo.close > zone_mid:
    return None
  if geo.close > float(zone_high):
    return None
  closed_away = geo.close <= float(zone_low)
  measured["closed_away_from_zone"] = closed_away
  return M1TriggerResult(
    "strong_close",
    direction,
    geo.high,
    bar.name,
    (
      f"closed {_price(cfg, geo.close)} in bottom {strong_pct:.0%} of range "
      f"and toward/below zone mid {_price(cfg, zone_mid)}"
      + ("; reclaim below proximal" if closed_away else "")
    ),
    measured=measured,
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
  if geo.body / geo.range_ > _HAMMER_BODY_FRACTION:
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
    key_level, cfg: _strong_close(
      bar, geo, direction=direction, zone_low=zone_low, zone_high=zone_high, cfg=cfg,
    ),
  "pin_bar": lambda bar, geo, prior, direction, zone_low, zone_high,
    key_level, cfg: _pin_bar(bar, geo, direction=direction, cfg=cfg),
  "engulfing": lambda bar, geo, prior, direction, zone_low, zone_high,
    key_level, cfg: _engulfing(bar, geo, prior, direction=direction, cfg=cfg),
  "hammer": lambda bar, geo, prior, direction, zone_low, zone_high,
    key_level, cfg: _hammer(
      bar, geo, direction=direction, zone_low=zone_low, zone_high=zone_high, cfg=cfg,
    ),
}


def _evaluate_bar(
  bar: pd.Series,
  *,
  prior: pd.Series | None,
  zone_low: float,
  zone_high: float,
  key_level: float,
  direction: str,
  cfg: Any,
) -> M1TriggerResult | None:
  geo = _geometry(bar)
  if geo is None or not _intersects_zone(
    geo,
    zone_low=zone_low,
    zone_high=zone_high,
  ):
    return None
  patterns = enabled_patterns(cfg)
  for pattern in ALL_PATTERNS:
    if pattern not in patterns:
      continue
    detector = _DETECTORS[pattern]
    result = detector(
      bar,
      geo,
      prior,
      direction,
      zone_low,
      zone_high,
      key_level,
      cfg,
    )
    if result is not None:
      return result
  return None


def _bar_is_closed(bar: pd.Series) -> bool:
  """Frames from RedisOHLCSource are closed-only; honour explicit flags too."""
  if "closed" not in bar.index:
    return True
  value = bar.get("closed")
  if value is None or pd.isna(value):
    return True
  return bool(value)


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
  if cfg is None:
    from app.core.config import runtime_config
    cfg = runtime_config
  bar = m1.iloc[-1]
  if not _bar_is_closed(bar):
    return None
  prior = m1.iloc[-2] if len(m1) >= 2 else None
  if prior is not None and not _bar_is_closed(prior):
    prior = None
  return _evaluate_bar(
    bar,
    prior=prior,
    zone_low=zone_low,
    zone_high=zone_high,
    key_level=key_level,
    direction=direction,
    cfg=cfg,
  )


def evaluate_m1_trigger_window(
  m1: pd.DataFrame,
  *,
  zone_low: float,
  zone_high: float,
  key_level: float,
  direction: str,
  earliest_bar_ts: int,
  after_bar_ts: int | None,
  cfg: Any = None,
) -> M1TriggerResult | None:
  """Return the earliest unprocessed closed trigger in the current episode."""
  if cfg is None:
    from app.core.config import runtime_config
    cfg = runtime_config
  if m1 is None or m1.empty:
    return None
  side = str(direction).upper()
  if side not in {"BUY", "SELL"}:
    return None
  earliest = int(earliest_bar_ts)
  after = None if after_bar_ts is None else int(after_bar_ts)
  timestamps = [parse_bar_timestamp(index) for index in m1.index]
  for position, bar_ts in enumerate(timestamps):
    if bar_ts is None or bar_ts < earliest:
      continue
    if after is not None and bar_ts <= after:
      continue
    bar = m1.iloc[position]
    if not _bar_is_closed(bar):
      continue
    prior = None
    if position > 0:
      prior_ts = timestamps[position - 1]
      prior_bar = m1.iloc[position - 1]
      if (
        prior_ts is not None
        and prior_ts >= earliest
        and _bar_is_closed(prior_bar)
      ):
        prior = prior_bar
    result = _evaluate_bar(
      bar,
      prior=prior,
      zone_low=zone_low,
      zone_high=zone_high,
      key_level=key_level,
      direction=side,
      cfg=cfg,
    )
    if result is not None:
      return M1TriggerResult(
        pattern=result.pattern,
        direction=result.direction,
        wick_extreme=result.wick_extreme,
        bar_ts=bar_ts,
        message=result.message,
      )
  return None


def latest_eligible_m1_bar_ts(
  m1: pd.DataFrame | None,
  *,
  earliest_bar_ts: int,
  after_bar_ts: int | None,
) -> int | None:
  """Newest closed bar timestamp eligible for the current episode."""
  if m1 is None or m1.empty:
    return None
  earliest = int(earliest_bar_ts)
  after = None if after_bar_ts is None else int(after_bar_ts)
  latest = None
  for position, index in enumerate(m1.index):
    bar_ts = parse_bar_timestamp(index)
    if bar_ts is None or bar_ts < earliest:
      continue
    if after is not None and bar_ts <= after:
      continue
    if not _bar_is_closed(m1.iloc[position]):
      continue
    latest = bar_ts
  return latest
