"""Candle Confirmation V2 — evidence family C: SEQUENCE (§10-§15).

Multi-bar transitions: morning_star / evening_star, compression_break,
sweep_indecision_displacement. 2-5 closed bars, causal only — no lookahead,
no future bars, no equity-style gap requirements (XAU/FX are near-
continuous). Indecision (doji/spinning_top/inside_bar/compression) is
contextual only — never a directional confirmation by itself (§14); it
only ever feeds the *middle* of a sequence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analysis.candle_geometry import CandleGeometry, atr_quality, clamp01, fraction_quality
from app.analysis.candle_rejection import reclaim_depth_atr, sweep_penetration_atr
from app.scalping.math_features import safe_div

# Provisional defaults (§29/§48) — no equivalent pre-existing constant in
# this codebase (sequence detection is genuinely new), so every threshold
# here is provisional pending replay evidence.
_DOJI_BODY_FRACTION = 0.10
_SPINNING_TOP_BODY_FRACTION = 0.35
_SPINNING_TOP_WICK_BALANCE = 0.20

_FIRST_BODY_MINIMUM_ATR = 0.30
_MIDDLE_BODY_MAXIMUM_FRACTION = 0.30
_RECOVERY_MINIMUM_RATIO = 0.50
_THIRD_BODY_MINIMUM_ATR = 0.25

_COMPRESSION_MINIMUM_BARS = 2
_COMPRESSION_MAXIMUM_BARS = 4
_COMPRESSION_MAXIMUM_TOTAL_RANGE_ATR = 0.80
_COMPRESSION_MAXIMUM_AVERAGE_BODY_FRACTION = 0.35
_COMPRESSION_BREAKOUT_BODY_MINIMUM_ATR = 0.30


def _sequences_cfg(cfg: Any | None) -> Any | None:
  analysis = getattr(cfg, "analysis", None)
  section = getattr(analysis, "candle_confirmation", None)
  return getattr(section, "sequences", None)


def _doji_body_fraction(cfg: Any | None) -> float:
  section = getattr(_sequences_cfg(cfg), "indecision", None)
  return float(getattr(section, "doji_body_fraction", _DOJI_BODY_FRACTION) or _DOJI_BODY_FRACTION)


def _morning_evening_cfg(cfg: Any | None) -> Any | None:
  return getattr(_sequences_cfg(cfg), "morning_evening_star", None)


def _first_body_minimum_atr(cfg: Any | None) -> float:
  section = _morning_evening_cfg(cfg)
  return float(
    getattr(section, "first_body_minimum_atr", _FIRST_BODY_MINIMUM_ATR)
    or _FIRST_BODY_MINIMUM_ATR
  )


def _middle_body_maximum_fraction(cfg: Any | None) -> float:
  section = _morning_evening_cfg(cfg)
  return float(
    getattr(section, "middle_body_maximum_fraction", _MIDDLE_BODY_MAXIMUM_FRACTION)
    or _MIDDLE_BODY_MAXIMUM_FRACTION
  )


def _recovery_minimum_ratio(cfg: Any | None) -> float:
  section = _morning_evening_cfg(cfg)
  return float(
    getattr(section, "recovery_minimum_ratio", _RECOVERY_MINIMUM_RATIO)
    or _RECOVERY_MINIMUM_RATIO
  )


def _third_body_minimum_atr(cfg: Any | None) -> float:
  section = _morning_evening_cfg(cfg)
  return float(
    getattr(section, "third_body_minimum_atr", _THIRD_BODY_MINIMUM_ATR)
    or _THIRD_BODY_MINIMUM_ATR
  )


def _compression_cfg(cfg: Any | None) -> Any | None:
  return getattr(_sequences_cfg(cfg), "compression_break", None)


def _compression_minimum_bars(cfg: Any | None) -> int:
  section = _compression_cfg(cfg)
  return int(getattr(section, "minimum_bars", _COMPRESSION_MINIMUM_BARS) or _COMPRESSION_MINIMUM_BARS)


def _compression_maximum_bars(cfg: Any | None) -> int:
  section = _compression_cfg(cfg)
  return int(getattr(section, "maximum_bars", _COMPRESSION_MAXIMUM_BARS) or _COMPRESSION_MAXIMUM_BARS)


def _compression_maximum_total_range_atr(cfg: Any | None) -> float:
  section = _compression_cfg(cfg)
  return float(
    getattr(section, "maximum_total_range_atr", _COMPRESSION_MAXIMUM_TOTAL_RANGE_ATR)
    or _COMPRESSION_MAXIMUM_TOTAL_RANGE_ATR
  )


def _compression_maximum_average_body_fraction(cfg: Any | None) -> float:
  section = _compression_cfg(cfg)
  return float(
    getattr(
      section, "maximum_average_body_fraction",
      _COMPRESSION_MAXIMUM_AVERAGE_BODY_FRACTION,
    ) or _COMPRESSION_MAXIMUM_AVERAGE_BODY_FRACTION
  )


def _compression_breakout_body_minimum_atr(cfg: Any | None) -> float:
  section = _compression_cfg(cfg)
  return float(
    getattr(
      section, "breakout_body_minimum_atr", _COMPRESSION_BREAKOUT_BODY_MINIMUM_ATR,
    ) or _COMPRESSION_BREAKOUT_BODY_MINIMUM_ATR
  )


@dataclass(frozen=True)
class IndecisionEvidence:
  doji: bool
  spinning_top: bool
  inside_bar: bool
  body_fraction: float
  compression_score: float

  def to_dict(self) -> dict[str, Any]:
    return {
      "doji": self.doji,
      "spinning_top": self.spinning_top,
      "inside_bar": self.inside_bar,
      "body_fraction": round(float(self.body_fraction), 4),
      "compression_score": round(float(self.compression_score), 4),
    }


def evaluate_indecision(
  geo: CandleGeometry,
  prior: CandleGeometry | None = None,
  *,
  cfg: Any | None = None,
) -> IndecisionEvidence:
  """§14/§33 — contextual only, never directional by itself."""
  doji_threshold = _doji_body_fraction(cfg)
  doji = geo.body_fraction <= doji_threshold
  wick_balance = abs(geo.upper_wick_fraction - geo.lower_wick_fraction)
  spinning_top = (
    not doji
    and geo.body_fraction <= _SPINNING_TOP_BODY_FRACTION
    and wick_balance <= _SPINNING_TOP_WICK_BALANCE
  )
  inside_bar = (
    prior is not None
    and geo.high <= prior.high
    and geo.low >= prior.low
  )
  # Single-bar compression proxy — a tight bar relative to ATR. The
  # multi-bar compression_break detector below computes its own
  # window-level quality; this is a per-bar contextual reading only.
  compression_score = clamp01(1.0 - atr_quality(geo.range_atr, minimum=0.4))
  return IndecisionEvidence(
    doji=doji,
    spinning_top=spinning_top,
    inside_bar=inside_bar,
    body_fraction=geo.body_fraction,
    compression_score=compression_score,
  )


@dataclass(frozen=True)
class SequenceEvidence:
  score: float
  patterns: tuple[str, ...]
  bars: int

  @property
  def sequence_name(self) -> str | None:
    return self.patterns[0] if self.patterns else None

  def to_dict(self) -> dict[str, Any]:
    return {
      "score": round(float(self.score), 4),
      "patterns": list(self.patterns),
      "bars": self.bars,
      "sequence_name": self.sequence_name,
    }


def detect_morning_evening_star(
  bars: list[CandleGeometry],
  *,
  direction: str,
  cfg: Any | None = None,
) -> tuple[str, ...] | None:
  """§11 — exactly 3 closed bars, oldest-to-newest: pressure -> indecision
  -> recovery displacement. Math-defined (ATR/body/recovery-ratio), no
  equity-style gap requirement."""
  if len(bars) != 3:
    return None
  bar1, bar2, bar3 = bars
  bar1_move = abs(bar1.open - bar1.close)
  if bar1_move <= 0:
    return None
  if bar2.body_fraction > _middle_body_maximum_fraction(cfg):
    return None
  if (bar1.body_atr or 0.0) < _first_body_minimum_atr(cfg):
    return None
  if (bar3.body_atr or 0.0) < _third_body_minimum_atr(cfg):
    return None

  if direction == "BUY":
    if not (bar1.is_bearish and bar3.is_bullish):
      return None
    recovery = (bar3.close - bar1.close) / bar1_move
    if recovery < _recovery_minimum_ratio(cfg):
      return None
    return ("morning_star",)

  if not (bar1.is_bullish and bar3.is_bearish):
    return None
  recovery = (bar1.close - bar3.close) / bar1_move
  if recovery < _recovery_minimum_ratio(cfg):
    return None
  return ("evening_star",)


def detect_compression_break(
  bars: list[CandleGeometry],
  *,
  direction: str,
  atr: float,
  cfg: Any | None = None,
) -> tuple[str, ...] | None:
  """§12 — N compression bars (2-4, configurable) followed immediately by
  one directional breakout bar, oldest-to-newest."""
  if len(bars) < 3 or atr <= 0:
    return None
  *compression_bars, breakout = bars
  n_compression = len(compression_bars)
  if not (_compression_minimum_bars(cfg) <= n_compression <= _compression_maximum_bars(cfg)):
    return None

  highs = [b.high for b in compression_bars]
  lows = [b.low for b in compression_bars]
  total_range = max(highs) - min(lows)
  total_range_atr = safe_div(total_range, atr) or 0.0
  if total_range_atr > _compression_maximum_total_range_atr(cfg):
    return None
  avg_body_fraction = sum(b.body_fraction for b in compression_bars) / n_compression
  if avg_body_fraction > _compression_maximum_average_body_fraction(cfg):
    return None

  directional = breakout.is_bullish if direction == "BUY" else breakout.is_bearish
  if not directional or (breakout.body_atr or 0.0) < _compression_breakout_body_minimum_atr(cfg):
    return None
  if direction == "BUY" and breakout.close <= max(highs):
    return None
  if direction == "SELL" and breakout.close >= min(lows):
    return None
  return ("compression_break",)


def detect_sweep_indecision_displacement(
  bars: list[CandleGeometry],
  *,
  direction: str,
  level: float,
  atr: float,
  cfg: Any | None = None,
) -> tuple[str, ...] | None:
  """§13 — exactly 3 closed bars: liquidity sweep -> small-body indecision
  -> directional displacement that reclaims the swept level. Particularly
  relevant for XAU."""
  if len(bars) != 3 or atr <= 0:
    return None
  sweep_bar, indecision_bar, displacement_bar = bars
  swept = sweep_penetration_atr(
    direction=direction, bar_low=sweep_bar.low, bar_high=sweep_bar.high,
    level=level, atr=atr,
  )
  if swept is None:
    return None
  if indecision_bar.body_fraction > _middle_body_maximum_fraction(cfg):
    return None
  directional = (
    displacement_bar.is_bullish if direction == "BUY" else displacement_bar.is_bearish
  )
  if not directional or (displacement_bar.body_atr or 0.0) < _third_body_minimum_atr(cfg):
    return None
  reclaimed = reclaim_depth_atr(
    direction=direction, close=displacement_bar.close, level=level, atr=atr,
  )
  if reclaimed is None:
    return None
  return ("sweep_indecision_displacement",)


def _sequence_score(
  bars: list[CandleGeometry],
  *,
  direction: str,
  atr: float,
  level: float | None,
  cfg: Any | None,
) -> float:
  """§15 — one shared weighted formula across every sequence pattern:
  first bar's pressure, middle bar(s)' compression/indecision, last bar's
  reversal displacement, close-based recovery, and structural reclaim."""
  first, last = bars[0], bars[-1]
  middle = bars[1:-1] or [bars[len(bars) // 2]]

  initial_pressure_quality = atr_quality(first.body_atr, minimum=_first_body_minimum_atr(cfg))
  avg_middle_body = sum(b.body_fraction for b in middle) / len(middle)
  compression_or_indecision_quality = clamp01(
    1.0 - avg_middle_body / max(1e-9, _middle_body_maximum_fraction(cfg)),
  )
  reversal_displacement_quality = atr_quality(last.body_atr, minimum=_third_body_minimum_atr(cfg))

  first_move = abs(first.open - first.close)
  if first_move > 0:
    recovery = (
      (last.close - first.close) / first_move if direction == "BUY"
      else (first.close - last.close) / first_move
    )
    recovery_quality = fraction_quality(
      max(0.0, recovery), minimum=_recovery_minimum_ratio(cfg), strong=1.0,
    )
  else:
    recovery_quality = 0.0

  structural_reclaim_quality = 0.0
  if level is not None and atr > 0:
    reclaimed = reclaim_depth_atr(direction=direction, close=last.close, level=level, atr=atr)
    structural_reclaim_quality = atr_quality(reclaimed, minimum=0.05)

  return clamp01(
    0.25 * initial_pressure_quality
    + 0.20 * compression_or_indecision_quality
    + 0.30 * reversal_displacement_quality
    + 0.15 * recovery_quality
    + 0.10 * structural_reclaim_quality
  )


def evaluate_sequence(
  bars: list[CandleGeometry],
  *,
  direction: str,
  atr: float,
  level: float | None = None,
  cfg: Any | None = None,
) -> SequenceEvidence | None:
  """§10 — try every supported multi-bar sequence against ``bars``
  (2-5 closed bars, oldest-to-newest; caller is responsible for never
  including an open/in-progress bar — §23). Returns the union of every
  matching pattern's labels (deduplicated evidence, same discipline as
  rejection/displacement) with one shared score."""
  direction = str(direction).upper()
  if direction not in {"BUY", "SELL"} or len(bars) < 2:
    return None

  patterns: list[str] = []

  if len(bars) == 3:
    star = detect_morning_evening_star(bars, direction=direction, cfg=cfg)
    if star:
      patterns.extend(star)
    if level is not None:
      swept = detect_sweep_indecision_displacement(
        bars, direction=direction, level=level, atr=atr, cfg=cfg,
      )
      if swept:
        patterns.extend(swept)

  compression = detect_compression_break(bars, direction=direction, atr=atr, cfg=cfg)
  if compression:
    patterns.extend(compression)

  if not patterns:
    return None

  # Deduplicate while preserving first-seen order (§3/§18).
  seen: set[str] = set()
  deduped = tuple(p for p in patterns if not (p in seen or seen.add(p)))

  score = _sequence_score(bars, direction=direction, atr=atr, level=level, cfg=cfg)
  return SequenceEvidence(score=score, patterns=deduped, bars=len(bars))
