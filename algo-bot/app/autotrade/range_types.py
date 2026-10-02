"""Data contracts retained by range lifecycle and research tooling."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AutoScalpRail:
  role: str
  low: float
  high: float
  level: float
  touches: int
  score: float
  timeframes: tuple[str, ...]
  sources: tuple[str, ...]


@dataclass(frozen=True)
class AutoScalpBox:
  box_id: str
  lower: AutoScalpRail
  upper: AutoScalpRail
  width_pips: float
  inside_ratio: float = 0.0
  efficiency: float = 0.0
  formation_start_ts: int = 0
  formation_end_ts: int = 0


@dataclass(frozen=True)
class AutoScalpDecision:
  state: str
  direction: str | None = None
  trigger: str | None = None
  rail: AutoScalpRail | None = None
  target: AutoScalpRail | None = None
  target_room_pips: float | None = None
  full_tp_pips: int | None = None
  box: AutoScalpBox | None = None
  confluence: int = 0
  reasons: tuple[str, ...] = ()
  rail_count: int = 0
  sweep_low: float | None = None
  sweep_high: float | None = None
