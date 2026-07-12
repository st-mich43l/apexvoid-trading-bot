"""Shared pure price-action dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class Swing:
  index: int | pd.Timestamp
  kind: str
  price: float
  label: str = ""
  ts: pd.Timestamp | None = None


@dataclass(frozen=True)
class Leg:
  start: int
  end: int
  direction: str
  size: float


@dataclass(frozen=True)
class Break:
  kind: str
  direction: str
  level: float
  index: int
  ts: pd.Timestamp | None = None


@dataclass(frozen=True)
class Level:
  price: float
  kind: str = "reaction"
  touches: int = 1
  band: float = 0.0
  strength: float = 1.0


@dataclass(frozen=True)
class Zone:
  bottom: float
  top: float
  side: str
  origin_index: int = -1
  created_ts: pd.Timestamp | None = None
  touches: int = 0
  mitigated: bool = False
  source: str = ""
  sources: list[str] = field(default_factory=list)
  score: float = 0.0
  score_reasons: list[str] = field(default_factory=list)
  break_kind: str | None = None
  break_index: int | None = None

  def __post_init__(self) -> None:
    if self.bottom > self.top:
      bottom = self.top
      top = self.bottom
      object.__setattr__(self, "bottom", bottom)
      object.__setattr__(self, "top", top)
    if not self.sources and self.source:
      object.__setattr__(self, "sources", [self.source])

  @property
  def low(self) -> float:
    return self.bottom

  @property
  def high(self) -> float:
    return self.top

  @property
  def kind(self) -> str:
    return self.source or self.side


@dataclass(frozen=True)
class Pool:
  side: str
  level: float
  band: float
  touches: int = 1


@dataclass(frozen=True)
class Grab:
  pool: Pool
  index: int
  direction: str
  ts: pd.Timestamp | None = None
