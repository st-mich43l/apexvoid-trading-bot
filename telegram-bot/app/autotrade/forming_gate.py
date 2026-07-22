"""Typed Market Map + forming-setup gate for ApexVoid Algo.

The scanner owns PA analysis and emits a short-lived ``FormingRangeSetup``.
This module owns the execution-side confirmation.  It intentionally consumes
the typed Redis contract rather than rendered Telegram text or scanner
internals.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math

import pandas as pd

from app.autotrade import units
from app.autotrade.gate import (
  ATR_LENGTH,
  MAX_ENTRY_DISTANCE_PIPS,
  AutoScalpBox,
  AutoScalpDecision,
  AutoScalpRail,
  _atr,
  _box_is_broken,
  _clean_frame,
  _full_tp_pips,
  _m1_rail_trigger,
  _rail_distance,
  _target_room,
)


FORMING_GATE_VERSION = 1
FORMING_GATE_KEY_PREFIX = "auto_trade:forming_gate"
M5_CONFIRMATIONS = {
  "sweep_a",
  "sweep_reclaim",
  "rejection_choch",
  "rejection_edge",
}
_EPS = 1e-9


@dataclass(frozen=True)
class FormingRail:
  direction: str
  low: float
  high: float
  level: float
  score: float
  tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class FormingRangeSetup:
  version: int
  setup_id: str
  range_id: str
  symbol: str
  source_tf: str
  event_ts: str
  issued_at: int
  expires_at: int
  setup: str
  mode: str
  direction: str
  m5_confirmation: str
  key_level: float
  entry_low: float
  entry_high: float
  confluence: int
  reasons: tuple[str, ...]
  lower: FormingRail
  upper: FormingRail
  map_bias: str
  map_bias_tf: str | None = None

  def to_json(self) -> str:
    return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

  @classmethod
  def from_json(cls, raw: object) -> FormingRangeSetup | None:
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
      payload = json.loads(text)
      lower = FormingRail(
        direction=str(payload["lower"]["direction"]).upper(),
        low=float(payload["lower"]["low"]),
        high=float(payload["lower"]["high"]),
        level=float(payload["lower"]["level"]),
        score=float(payload["lower"]["score"]),
        tags=tuple(str(item) for item in payload["lower"].get("tags", [])),
      )
      upper = FormingRail(
        direction=str(payload["upper"]["direction"]).upper(),
        low=float(payload["upper"]["low"]),
        high=float(payload["upper"]["high"]),
        level=float(payload["upper"]["level"]),
        score=float(payload["upper"]["score"]),
        tags=tuple(str(item) for item in payload["upper"].get("tags", [])),
      )
      result = cls(
        version=int(payload["version"]),
        setup_id=str(payload["setup_id"]),
        range_id=str(payload["range_id"]),
        symbol=str(payload["symbol"]).upper(),
        source_tf=str(payload["source_tf"]).upper(),
        event_ts=str(payload["event_ts"]),
        issued_at=int(payload["issued_at"]),
        expires_at=int(payload["expires_at"]),
        setup=str(payload["setup"]),
        mode=str(payload["mode"]),
        direction=str(payload["direction"]).upper(),
        m5_confirmation=str(payload["m5_confirmation"]),
        key_level=float(payload["key_level"]),
        entry_low=float(payload["entry_low"]),
        entry_high=float(payload["entry_high"]),
        confluence=int(payload["confluence"]),
        reasons=tuple(str(item) for item in payload.get("reasons", [])),
        lower=lower,
        upper=upper,
        map_bias=str(payload.get("map_bias", "range")),
        map_bias_tf=(
          None
          if payload.get("map_bias_tf") is None
          else str(payload["map_bias_tf"]).upper()
        ),
      )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
      return None
    return result if _valid_setup(result) else None


def forming_gate_key(symbol: str) -> str:
  return f"{FORMING_GATE_KEY_PREFIX}:{symbol.upper()}"


def forming_setup_id(
  symbol: str,
  source_tf: str,
  event_ts: str,
  direction: str,
  lower: float,
  upper: float,
) -> str:
  raw = (
    f"v{FORMING_GATE_VERSION}|{symbol.upper()}|{source_tf.upper()}|"
    f"{event_ts}|{direction.upper()}|{lower:.5f}|{upper:.5f}"
  )
  return hashlib.sha256(raw.encode("ascii")).hexdigest()


def forming_range_id(symbol: str, lower: float, upper: float) -> str:
  """Stable identity shared by opposite-side cards for the same map box."""
  bucket = 10 * units.pip_size(symbol)
  lower_bucket = round(float(lower) / bucket)
  upper_bucket = round(float(upper) / bucket)
  return f"{symbol.lower()}-map-{lower_bucket}-{upper_bucket}"


def evaluate_forming_range_gate(
  frames: dict[str, pd.DataFrame],
  setup: FormingRangeSetup,
  *,
  symbol: str,
  spot_price: float | None = None,
  now: int | None = None,
  m1_confirmation_bars: int = 5,
  m5_structure_bars: int = 3,
) -> AutoScalpDecision:
  """Confirm one scanner forming setup against its Market Map rails on M1.

  Market Map supplies the range structure and the forming detector supplies
  one allowed direction.  A recent M1 rejection still owns entry timing; the
  map/forming signal never bypasses the executor's downstream safety gates.
  """
  now = (
    int(datetime.now(timezone.utc).timestamp())
    if now is None else int(now)
  )
  if setup.symbol != symbol.upper():
    return AutoScalpDecision(
      "forming_symbol_mismatch",
      reasons=(f"forming setup belongs to {setup.symbol}",),
    )
  if now > setup.expires_at:
    return AutoScalpDecision(
      "forming_stale",
      reasons=(f"forming setup {setup.setup_id[:12]} expired",),
    )
  if setup.setup != "Range Edge Scalp" or setup.mode != "range_scalp":
    return AutoScalpDecision(
      "forming_unsupported",
      reasons=(f"unsupported forming setup {setup.setup}/{setup.mode}",),
    )
  if "M1" not in frames or "M5" not in frames:
    return AutoScalpDecision(
      "forming_missing_frames",
      reasons=("forming gate requires M1 and M5",),
    )
  m1 = _clean_frame(frames["M1"])
  m5 = _clean_frame(frames["M5"])
  if len(m1) < ATR_LENGTH or m5.empty:
    return AutoScalpDecision(
      "forming_insufficient_history",
      reasons=(f"forming history M1={len(m1)} M5={len(m5)}",),
    )
  atr = _atr(m1)
  m5_atr = _atr(m5)
  if atr <= _EPS or m5_atr <= _EPS:
    return AutoScalpDecision(
      "forming_invalid_atr",
      reasons=(f"invalid ATR M1={atr:.4f} M5={m5_atr:.4f}",),
    )

  box = _mapped_box(setup, symbol)
  pip_size = units.pip_size(symbol)
  if _box_is_broken(m1, m5, box, m5_atr, pip_size):
    return AutoScalpDecision(
      "box_broken",
      box=box,
      rail_count=2,
      reasons=(f"Market Map range {box.box_id} accepted outside",),
    )

  rail = box.lower if setup.direction == "BUY" else box.upper
  target = box.upper if setup.direction == "BUY" else box.lower
  m5_structure = _m5_structure_confirmation(
    m5,
    rail,
    setup.direction,
    max(1, m5_structure_bars),
  )
  if m5_structure is None:
    return AutoScalpDecision(
      "forming_waiting_m5",
      direction=setup.direction,
      rail=rail,
      target=target,
      box=box,
      confluence=setup.confluence,
      rail_count=2,
      reasons=(
        f"scanner M5 confirmation {setup.m5_confirmation}",
        f"waiting for M5 structure hold at {rail.low:.2f}-{rail.high:.2f}",
      ),
    )
  trigger = _recent_m1_trigger(
    m1,
    rail,
    atr,
    setup.direction,
    max(1, m1_confirmation_bars),
  )
  if trigger is None:
    return AutoScalpDecision(
      "forming_waiting_m1",
      direction=setup.direction,
      rail=rail,
      target=target,
      box=box,
      confluence=setup.confluence,
      rail_count=2,
      reasons=(
        f"Market Map + {setup.setup} aligned on {setup.source_tf}",
        f"M5 structure {m5_structure}",
        f"waiting for M1 rejection at {rail.low:.2f}-{rail.high:.2f}",
      ),
    )

  live_price = float(m1["close"].iloc[-1]) if spot_price is None else float(spot_price)
  if not math.isfinite(live_price) or live_price <= 0:
    return AutoScalpDecision(
      "invalid_spot",
      rail=rail,
      target=target,
      box=box,
      rail_count=2,
      reasons=(f"invalid spot price: {spot_price!r}",),
    )
  distance_pips = _rail_distance(rail, live_price) / pip_size
  if distance_pips > MAX_ENTRY_DISTANCE_PIPS + _EPS:
    return AutoScalpDecision(
      "entry_moved",
      direction=setup.direction,
      trigger=trigger,
      rail=rail,
      target=target,
      box=box,
      confluence=setup.confluence,
      rail_count=2,
      reasons=(
        f"entry moved {distance_pips:.1f} pips beyond "
        f"{MAX_ENTRY_DISTANCE_PIPS} pip limit from Market Map rail",
      ),
    )
  room = _target_room(live_price, setup.direction, target)
  room_pips = 0.0 if room is None else room / pip_size
  full_tp_pips = _full_tp_pips(room_pips)
  if full_tp_pips is None:
    return AutoScalpDecision(
      "target_blocked",
      direction=setup.direction,
      trigger=trigger,
      rail=rail,
      target=target,
      target_room_pips=room_pips,
      box=box,
      confluence=setup.confluence,
      rail_count=2,
      reasons=(f"only {room_pips:.0f} pips room to opposite Market Map rail",),
    )

  reasons = (
    f"Market Map {setup.source_tf} range aligned",
    f"M5 {setup.m5_confirmation} + {m5_structure}",
    f"M1 {trigger.replace('_', ' ')}",
    f"{rail.role} rail {rail.low:.2f}-{rail.high:.2f}",
    f"opposite edge {room_pips:.0f} pips away",
    f"full TP {full_tp_pips} pips",
  )
  return AutoScalpDecision(
    "candidate",
    direction=setup.direction,
    trigger=trigger,
    rail=rail,
    target=target,
    target_room_pips=room_pips,
    full_tp_pips=full_tp_pips,
    box=box,
    confluence=setup.confluence,
    reasons=reasons,
    rail_count=2,
  )


def _valid_setup(setup: FormingRangeSetup) -> bool:
  numeric = (
    setup.key_level,
    setup.entry_low,
    setup.entry_high,
    setup.lower.low,
    setup.lower.high,
    setup.lower.level,
    setup.lower.score,
    setup.upper.low,
    setup.upper.high,
    setup.upper.level,
    setup.upper.score,
  )
  active = setup.lower if setup.direction == "BUY" else setup.upper
  entry_overlaps_rail = min(setup.entry_high, active.high) >= max(
    setup.entry_low,
    active.low,
  )
  return (
    setup.version == FORMING_GATE_VERSION
    and bool(setup.setup_id)
    and bool(setup.range_id)
    and bool(setup.symbol)
    and setup.direction in {"BUY", "SELL"}
    and setup.m5_confirmation in M5_CONFIRMATIONS
    and setup.confluence >= 1
    and setup.lower.direction == "BUY"
    and setup.upper.direction == "SELL"
    and all(math.isfinite(value) for value in numeric)
    and setup.entry_low <= setup.entry_high
    and setup.lower.low <= setup.lower.level <= setup.lower.high
    and setup.upper.low <= setup.upper.level <= setup.upper.high
    and setup.lower.level < setup.upper.level
    and active.low <= setup.key_level <= active.high
    and entry_overlaps_rail
    and setup.range_id == forming_range_id(
      setup.symbol,
      setup.lower.level,
      setup.upper.level,
    )
    and setup.setup_id == forming_setup_id(
      setup.symbol,
      setup.source_tf,
      setup.event_ts,
      setup.direction,
      setup.lower.level,
      setup.upper.level,
    )
    and setup.expires_at >= setup.issued_at
  )


def _mapped_box(setup: FormingRangeSetup, symbol: str) -> AutoScalpBox:
  lower = AutoScalpRail(
    "support",
    setup.lower.low,
    setup.lower.high,
    setup.lower.level,
    0,
    setup.lower.score,
    (setup.source_tf,),
    tuple(setup.lower.tags) or ("Market Map BUY rail",),
  )
  upper = AutoScalpRail(
    "resistance",
    setup.upper.low,
    setup.upper.high,
    setup.upper.level,
    0,
    setup.upper.score,
    (setup.source_tf,),
    tuple(setup.upper.tags) or ("Market Map SELL rail",),
  )
  width_pips = (upper.level - lower.level) / units.pip_size(symbol)
  return AutoScalpBox(
    setup.range_id,
    lower,
    upper,
    width_pips,
  )


def _recent_m1_trigger(
  m1: pd.DataFrame,
  rail: AutoScalpRail,
  atr: float,
  direction: str,
  bars: int,
) -> str | None:
  start = max(0, len(m1) - bars)
  for index in range(len(m1) - 1, start - 1, -1):
    trigger = _m1_rail_trigger(m1.iloc[:index + 1], rail, atr)
    if trigger is not None and trigger[0] == direction:
      return trigger[1]
  return None


def _m5_structure_confirmation(
  m5: pd.DataFrame,
  rail: AutoScalpRail,
  direction: str,
  bars: int,
) -> str | None:
  """Require a recent M5 touch and a close that still holds the mapped rail."""
  recent = m5.tail(max(1, bars))
  touched = any(
    float(row.low) <= rail.high and float(row.high) >= rail.low
    for row in recent.itertuples(index=False)
  )
  if not touched:
    return None
  latest_close = float(recent["close"].iloc[-1])
  held = (
    latest_close >= rail.low
    if direction == "BUY"
    else latest_close <= rail.high
  )
  if not held:
    return None
  for row in reversed(list(recent.itertuples(index=False))):
    if direction == "BUY" and (
      float(row.low) < rail.low and float(row.close) > rail.level
    ):
      return "sweep reclaim"
    if direction == "SELL" and (
      float(row.high) > rail.high and float(row.close) < rail.level
    ):
      return "sweep reclaim"
  if any(
    _directional_rejection(row, direction)
    for row in recent.itertuples(index=False)
  ):
    return "rejection hold"
  return "rail hold"


def _directional_rejection(row, direction: str) -> bool:
  open_ = float(row.open)
  high = float(row.high)
  low = float(row.low)
  close = float(row.close)
  span = high - low
  if span <= _EPS:
    return False
  body = abs(close - open_)
  upper = high - max(open_, close)
  lower = min(open_, close) - low
  if direction == "SELL":
    return upper >= body and close < open_ and close <= low + span / 3
  return lower >= body and close > open_ and close >= high - span / 3
