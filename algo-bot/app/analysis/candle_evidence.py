"""Candle Confirmation V2 — final evidence score (§16-§18).

``evaluate_all_candle_evidence`` is the single public entry point: builds
geometry once, runs every family evaluator, deduplicates labels across
families into ``all_patterns``/``primary_pattern`` (presentation only,
§18 — never affects score), active-weight-normalizes ``base_score`` so a
valid engulfing setup is never punished for lacking a Morning Star (§16),
and adds a small bounded ``synergy_bonus`` only for genuinely independent
cross-family evidence (§17 — never for same-geometry duplicate labels).

This is a NEW, additive layer. It never gates, never enters
``ConfluenceFactors``, and never overrides the existing M1/M5 pattern
decisions it sits alongside (§2/§19/§21/§42 Phase 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analysis.candle_displacement import DisplacementEvidence, evaluate_displacement
from app.analysis.candle_geometry import CandleGeometry, candle_geometry_from_bar
from app.analysis.candle_rejection import RejectionEvidence, evaluate_rejection
from app.analysis.candle_sequences import (
  IndecisionEvidence,
  SequenceEvidence,
  evaluate_indecision,
  evaluate_sequence,
)

CANDLE_CONFIRMATION_VERSION = 2

# §16 family weights.
_REJECTION_WEIGHT = 0.30
_DISPLACEMENT_WEIGHT = 0.45
_SEQUENCE_WEIGHT = 0.25

# §17 synergy — provisional (§29/§48), pending replay evidence.
_SYNERGY_REJECTION_PLUS_DISPLACEMENT = 0.08
_SYNERGY_SWEEP_PLUS_RECLAIM = 0.10
_SYNERGY_SEQUENCE_PLUS_DISPLACEMENT = 0.06
_SYNERGY_MAXIMUM_BONUS = 0.12

# §18 — presentation priority only, never affects the numeric score.
PATTERN_PRIORITY: tuple[str, ...] = (
  "sweep_reclaim",
  "sweep_indecision_displacement",
  "strong_reclaim",
  "engulfing",
  "morning_star",
  "evening_star",
  "strong_close",
  "hammer",
  "shooting_star",
  "pin_bar",
  "wick_rejection",
  "compression_break",
  "displacement_candle",
  "body_close",
  "inverted_hammer",
)

_SEQUENCE_WINDOWS = (5, 4, 3)


def _synergy_cfg(cfg: Any | None) -> Any | None:
  analysis = getattr(cfg, "analysis", None)
  section = getattr(analysis, "candle_confirmation", None)
  return getattr(section, "synergy", None)


def _synergy_rejection_plus_displacement(cfg: Any | None) -> float:
  section = _synergy_cfg(cfg)
  return float(
    getattr(section, "rejection_plus_displacement", _SYNERGY_REJECTION_PLUS_DISPLACEMENT)
    or _SYNERGY_REJECTION_PLUS_DISPLACEMENT
  )


def _synergy_sweep_plus_reclaim(cfg: Any | None) -> float:
  section = _synergy_cfg(cfg)
  return float(
    getattr(section, "sweep_plus_reclaim", _SYNERGY_SWEEP_PLUS_RECLAIM)
    or _SYNERGY_SWEEP_PLUS_RECLAIM
  )


def _synergy_sequence_plus_displacement(cfg: Any | None) -> float:
  section = _synergy_cfg(cfg)
  return float(
    getattr(section, "sequence_plus_displacement", _SYNERGY_SEQUENCE_PLUS_DISPLACEMENT)
    or _SYNERGY_SEQUENCE_PLUS_DISPLACEMENT
  )


def _synergy_maximum_bonus(cfg: Any | None) -> float:
  section = _synergy_cfg(cfg)
  return float(
    getattr(section, "maximum_bonus", _SYNERGY_MAXIMUM_BONUS) or _SYNERGY_MAXIMUM_BONUS
  )


@dataclass(frozen=True)
class CandleEvidence:
  version: int
  direction: str

  rejection: RejectionEvidence | None
  displacement: DisplacementEvidence | None
  sequence: SequenceEvidence | None
  indecision: IndecisionEvidence | None

  base_score: float
  synergy_bonus: float
  final_score: float

  primary_pattern: str | None
  all_patterns: tuple[str, ...] = field(default_factory=tuple)
  # The evaluated (last, currently-closing) bar's raw geometry — carried
  # for telemetry (§28's candle_body_fraction/candle_upper_wick_fraction/
  # etc.) so those readings never need re-deriving from a specific family
  # that may or may not have fired.
  geometry: CandleGeometry | None = None

  def to_dict(self) -> dict[str, Any]:
    return {
      "version": self.version,
      "direction": self.direction,
      "rejection": self.rejection.to_dict() if self.rejection else None,
      "displacement": self.displacement.to_dict() if self.displacement else None,
      "sequence": self.sequence.to_dict() if self.sequence else None,
      "indecision": self.indecision.to_dict() if self.indecision else None,
      "base_score": round(float(self.base_score), 4),
      "synergy_bonus": round(float(self.synergy_bonus), 4),
      "final_score": round(float(self.final_score), 4),
      "primary_pattern": self.primary_pattern,
      "all_patterns": list(self.all_patterns),
    }


def _dedup_patterns(*groups: tuple[str, ...]) -> tuple[str, ...]:
  seen: set[str] = set()
  ordered: list[str] = []
  for group in groups:
    for pattern in group:
      if pattern not in seen:
        seen.add(pattern)
        ordered.append(pattern)
  return tuple(ordered)


def _primary_pattern(all_patterns: tuple[str, ...]) -> str | None:
  if not all_patterns:
    return None
  present = set(all_patterns)
  for candidate in PATTERN_PRIORITY:
    if candidate in present:
      return candidate
  return all_patterns[0]


def _merge_sequence_evidence(candidates: list[SequenceEvidence]) -> SequenceEvidence | None:
  if not candidates:
    return None
  patterns = _dedup_patterns(*(candidate.patterns for candidate in candidates))
  avg_score = sum(candidate.score for candidate in candidates) / len(candidates)
  max_bars = max(candidate.bars for candidate in candidates)
  return SequenceEvidence(score=avg_score, patterns=patterns, bars=max_bars)


def evaluate_all_candle_evidence(
  bars: list[Any],
  *,
  direction: str,
  atr: float,
  level: float,
  zone_low: float | None = None,
  zone_high: float | None = None,
  cfg: Any | None = None,
) -> CandleEvidence | None:
  """Single entry point (§3). ``bars`` are raw closed OHLC bars (pandas
  Series or dict-like, ``open``/``high``/``low``/``close`` keys),
  oldest-to-newest, with the LAST bar being the one under evaluation.
  Up to the last 5 bars are used for sequence detection (§10); only the
  last (and, for engulfing/sequence, second-to-last) matter for
  rejection/displacement. Returns ``None`` when no family produces any
  evidence at all — a candle with genuinely nothing interesting about it
  is not artificially scored.
  """
  direction = str(direction).upper()
  if direction not in {"BUY", "SELL"} or not bars:
    return None

  geometries = [candle_geometry_from_bar(bar, atr=atr) for bar in bars]
  current = geometries[-1]
  prior = geometries[-2] if len(geometries) >= 2 else None

  rejection = evaluate_rejection(
    current, direction=direction, atr=atr, level=level,
    zone_low=zone_low, zone_high=zone_high, cfg=cfg,
  )
  displacement = evaluate_displacement(
    current, prior, direction=direction, level=level, atr=atr, cfg=cfg,
  )
  indecision = evaluate_indecision(current, prior, cfg=cfg)

  sequence_candidates: list[SequenceEvidence] = []
  for window in _SEQUENCE_WINDOWS:
    if len(geometries) >= window:
      candidate = evaluate_sequence(
        geometries[-window:], direction=direction, atr=atr, level=level, cfg=cfg,
      )
      if candidate is not None:
        sequence_candidates.append(candidate)
  sequence = _merge_sequence_evidence(sequence_candidates)

  if rejection is None and displacement is None and sequence is None:
    return None

  weighted_sum = 0.0
  active_weight = 0.0
  if rejection is not None:
    weighted_sum += rejection.score * _REJECTION_WEIGHT
    active_weight += _REJECTION_WEIGHT
  if displacement is not None:
    weighted_sum += displacement.score * _DISPLACEMENT_WEIGHT
    active_weight += _DISPLACEMENT_WEIGHT
  if sequence is not None:
    weighted_sum += sequence.score * _SEQUENCE_WEIGHT
    active_weight += _SEQUENCE_WEIGHT
  base_score = weighted_sum / active_weight if active_weight > 0 else 0.0

  synergy = 0.0
  if rejection is not None and displacement is not None:
    synergy += _synergy_rejection_plus_displacement(cfg)
  if rejection is not None and rejection.sweep and rejection.reclaim:
    synergy += _synergy_sweep_plus_reclaim(cfg)
  if sequence is not None and displacement is not None:
    synergy += _synergy_sequence_plus_displacement(cfg)
  synergy = min(synergy, _synergy_maximum_bonus(cfg))

  final_score = min(1.0, base_score + synergy)

  all_patterns = _dedup_patterns(
    rejection.patterns if rejection else (),
    displacement.patterns if displacement else (),
    sequence.patterns if sequence else (),
  )
  primary = _primary_pattern(all_patterns)

  return CandleEvidence(
    version=CANDLE_CONFIRMATION_VERSION,
    direction=direction,
    rejection=rejection,
    displacement=displacement,
    sequence=sequence,
    indecision=indecision,
    base_score=base_score,
    synergy_bonus=synergy,
    final_score=final_score,
    primary_pattern=primary,
    all_patterns=all_patterns,
    geometry=current,
  )
