"""MAD-0: Manipulation / Accumulation / Distribution phase + Asia range seal.

Shared Asia phase clock for technique (FX fixed_rr) and telemetry. Soft use:
accumulation → Range Edge Scalp confluence. When
``execution.technique.mad_hard_gate_enabled`` is on with enforce, FX technique
publish/activation applies ``mad_hard_gate`` live (HFS/scalping remains exempt).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from app.scalping.math_features import safe_div, zone_width_atr


PHASE_ACCUM = "accum"
PHASE_MANIP = "manip"
PHASE_EXPAND = "expand"
PHASE_UNCLEAR = "unclear"

PHASES = frozenset({PHASE_ACCUM, PHASE_MANIP, PHASE_EXPAND, PHASE_UNCLEAR})

# Range quality (width/ATR): accumulation prefers a real but not huge box.
_RQ_ACCUM_MIN = 0.8
_RQ_ACCUM_MAX = 6.0
# Building Asia (unsealed): allow wider box — early session expansion is normal.
_RQ_BUILDING_ACCUM_MAX = 24.0
# Expansion: close beyond sealed Asia edge by this ATR multiple, or impulse.
_EXPAND_BREAK_ATR = 0.35
_EXPAND_IMPULSE_ATR = 1.25


@dataclass(frozen=True)
class AsiaRangeSeal:
  """Sealed (or building) Asia session high/low for one trading day."""

  day_key: str
  high: float
  low: float
  sealed: bool
  sealed_at: int | None
  bar_count: int
  source: str = "m5"
  updated_at: int = 0

  @property
  def mid(self) -> float:
    return (float(self.high) + float(self.low)) / 2.0

  @property
  def width(self) -> float:
    return max(0.0, float(self.high) - float(self.low))

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)

  @classmethod
  def from_dict(cls, data: Any) -> AsiaRangeSeal | None:
    if not data:
      return None
    try:
      high = float(data["high"])
      low = float(data["low"])
      if high < low:
        return None
      return cls(
        day_key=str(data["day_key"]),
        high=high,
        low=low,
        sealed=bool(data.get("sealed", False)),
        sealed_at=_opt_int(data.get("sealed_at")),
        bar_count=int(data.get("bar_count") or 0),
        source=str(data.get("source") or "m5"),
        updated_at=int(data.get("updated_at") or 0),
      )
    except (KeyError, TypeError, ValueError):
      return None


@dataclass(frozen=True)
class MadPhaseSnapshot:
  phase: str
  asia: AsiaRangeSeal | None
  range_quality_atr: float | None
  price_vs_asia: str | None
  sweep_side: str | None
  reclaim: bool
  reason_code: str
  measured: dict[str, Any] = field(default_factory=dict)

  def to_dict(self) -> dict[str, Any]:
    payload = {
      "phase": self.phase,
      "range_quality_atr": self.range_quality_atr,
      "price_vs_asia": self.price_vs_asia,
      "sweep_side": self.sweep_side,
      "reclaim": self.reclaim,
      "reason_code": self.reason_code,
      "measured": dict(self.measured),
      "asia": None if self.asia is None else self.asia.to_dict(),
    }
    return payload


def asia_range_key(symbol: str) -> str:
  """Shared Asia box — technique lane + HFS."""
  return f"mad:asia_range:{str(symbol).upper()}"


def mad_phase_key(symbol: str) -> str:
  """Shared phase snapshot — technique lane + HFS."""
  return f"mad:phase:{str(symbol).upper()}"


def mad_last_key(symbol: str) -> str:
  """Legacy HFS-only alias; prefer ``mad_phase_key``."""
  return f"scalp:last_mad:{str(symbol).upper()}"


# Owner 2026-08-26: MAD must not drive HFS/scalping. Soft affinity is only
# accumulation → Range Edge Scalp (technique). No manip/expand bonuses, no
# Impulse/Momentum/Range Sweep/reaction MAD nudges.
RANGE_EDGE_MAD_FAMILIES = frozenset({
  "range_scalp",
  "range_edge",
  "range_edge_mean_reversion",
})
RANGE_SCALP_PHASES = frozenset({PHASE_ACCUM})
EXPANSION_PHASES = frozenset({PHASE_EXPAND, PHASE_MANIP})


def mad_soft_bonus(*, phase: str | None, family: str) -> float:
  """Soft confluence nudge — accumulation for Range Edge Scalp only."""
  p = str(phase or "").casefold()
  fam = str(family or "").casefold()
  if p != PHASE_ACCUM:
    return 0.0
  if fam in RANGE_EDGE_MAD_FAMILIES:
    return 0.12
  return 0.0


def _clamp01(value: float) -> float:
  return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class MadFeatureScores:
  """Continuous A/M/D scores (0–1) for shadow telemetry and replay."""

  accum: float
  manip: float
  expand: float

  def to_dict(self) -> dict[str, float]:
    return {
      "accum": round(float(self.accum), 4),
      "manip": round(float(self.manip), 4),
      "expand": round(float(self.expand), 4),
    }


@dataclass(frozen=True)
class MadGatePreview:
  """MAD hard-gate preview — live on FX technique when ``mad_hard_gate_enabled``."""

  would_block: bool
  reason_code: str

  def to_dict(self) -> dict[str, Any]:
    return {"would_block": self.would_block, "reason_code": self.reason_code}


# Math shadow + technique/HFS families evaluated for ``would_gate`` stamps.
SHADOW_GATE_STRATEGIES: tuple[str, ...] = (
  "structural_reaction",
  "liquidity_sweep_reversal",
  "range_edge_mean_reversion",
  "impulse_pullback_continuation",
  "range_sweep",
  "breakout_retest",
)

# Continuation (post-displacement): needs manip (Judas reclaim) or expand (accepted break).
_CONTINUATION_GATE_STRATEGIES = frozenset({
  "impulse_pullback_continuation",
  "impulse_pullback",
  "impulse",
  "breakout_retest",
})

# Mean-reversion / structural reaction: do not fade distribution (expand).
_REVERSAL_GATE_STRATEGIES = frozenset({
  "structural_reaction",
  "liquidity_sweep_reversal",
  "range_edge_mean_reversion",
  "range_sweep",
  "range_edge",
  "range_scalp",
  "hfs_range",
})

# Legacy aliases — same rules as above.
_IMPULSE_GATE_STRATEGIES = _CONTINUATION_GATE_STRATEGIES
_RANGE_GATE_STRATEGIES = _REVERSAL_GATE_STRATEGIES


def _rq_accum_score(rq: float | None, *, building: bool = False) -> float:
  if rq is None:
    return 0.0
  r = float(rq)
  if r < _RQ_ACCUM_MIN:
    return _clamp01(r / _RQ_ACCUM_MIN * 0.35)
  peak = 2.5
  width = _RQ_BUILDING_ACCUM_MAX if building else _RQ_ACCUM_MAX
  if r <= peak:
    return _clamp01(0.55 + (r - _RQ_ACCUM_MIN) / max(peak - _RQ_ACCUM_MIN, 1e-9) * 0.45)
  if r <= width:
    return _clamp01(1.0 - (r - peak) / max(width - peak, 1e-9) * 0.55)
  return 0.15


def compute_mad_features(snap: MadPhaseSnapshot) -> MadFeatureScores:
  """Continuous accumulation / manipulation / expansion scores from phase snapshot."""
  building = bool(
    snap.asia is not None
    and not snap.asia.sealed
    and snap.measured.get("session") == "asia"
  )
  rq = snap.range_quality_atr
  inside = snap.price_vs_asia == "inside"

  accum = _rq_accum_score(rq, building=building) if inside else 0.0
  if snap.phase == PHASE_ACCUM:
    accum = max(accum, 0.72)
  elif snap.phase == PHASE_MANIP and inside:
    accum = max(accum, 0.35)

  manip = 0.0
  if snap.reclaim:
    manip = 0.95
  elif snap.sweep_side:
    manip = 0.55
  if snap.phase == PHASE_MANIP:
    manip = max(manip, 0.8)

  expand = 0.0
  impulse = snap.measured.get("impulse_atr")
  if impulse is not None and float(impulse) > 0:
    expand = _clamp01(float(impulse) / _EXPAND_IMPULSE_ATR * 0.85)
  if snap.price_vs_asia in {"above", "below"} and snap.asia and snap.asia.sealed:
    expand = max(expand, 0.55)
  if snap.phase == PHASE_EXPAND:
    expand = max(expand, 0.78)

  return MadFeatureScores(
    accum=_clamp01(accum),
    manip=_clamp01(manip),
    expand=_clamp01(expand),
  )


def technique_mad_hard_gate_enabled(cfg: Any | None) -> bool:
  """True when FX technique lane should apply live ``mad_hard_gate``."""
  tech = getattr(getattr(cfg, "execution", None), "technique", None)
  if tech is None:
    return False
  return bool(getattr(tech, "mad_hard_gate_enabled", False))


def mad_gate_strategy_for_setup(
  setup: str,
  *,
  family: str | None = None,
  strategy_mode: str | None = None,
) -> str | None:
  """Map ZoneWatch / technique setup to ``mad_hard_gate`` strategy key.

  Uses ``strategy_taxonomy`` exact names — no substring guessing on registered
  strategies. Unregistered legacy labels fall back to ``family`` / ``mode``.
  """
  from app.autotrade.strategy_taxonomy import (
    canonical_family,
    is_m1_scalp_strategy,
    is_liquidity_strategy,
    is_range_strategy,
    is_reaction_strategy,
    is_technique_or_confluence,
    is_zone_strategy,
  )

  name = str(setup or "").strip()
  if not name:
    return None

  fam = str(family or "").casefold()
  mode = str(strategy_mode or "").casefold()

  if is_m1_scalp_strategy(name):
    lower = name.casefold()
    if "impulse" in lower or "momentum" in lower:
      return "impulse_pullback_continuation"
    if "breakout" in lower:
      return "breakout_retest"
    if "range sweep" in lower or "range_sweep" in lower:
      return "range_sweep"

  if (
    is_range_strategy(name)
    or fam in {"range", "range_scalp", "range_edge", "range_reversion"}
    or mode == "range_scalp"
  ):
    return "range_edge_mean_reversion"

  if is_liquidity_strategy(name) or fam in {"liquidity", "sweep", "fade"}:
    return "liquidity_sweep_reversal"

  if (
    is_reaction_strategy(name)
    or is_zone_strategy(name)
    or is_technique_or_confluence(name)
    or fam in {
      "reaction",
      "zone",
      "key_level",
      "supply_demand",
      "order_block",
      "fvg",
      "ifvg",
      "crt",
      "supply",
      "demand",
      "confluence",
    }
  ):
    return "structural_reaction"

  canon = canonical_family(name)
  if canon in {"reaction", "zone"}:
    return "structural_reaction"
  if canon == "liquidity":
    return "liquidity_sweep_reversal"
  if canon == "range":
    return "range_edge_mean_reversion"
  if canon == "scalp":
    return "impulse_pullback_continuation"

  return None


async def evaluate_technique_mad_gate(
  client: Any,
  *,
  symbol: str,
  strategy: str,
  cfg: Any | None,
  family: str | None = None,
  strategy_mode: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
  """Return (allowed, reason_code, measured) for FX technique MAD hard gate."""
  from app.autotrade.killzone import technique_enforce

  if not technique_mad_hard_gate_enabled(cfg):
    return True, "mad_gate_disabled", {}
  if not technique_enforce(cfg):
    return True, "technique_pack_off", {}
  gate_key = mad_gate_strategy_for_setup(
    strategy,
    family=family,
    strategy_mode=strategy_mode,
  )
  if gate_key is None:
    return True, "mad_gate_not_applicable", {}
  snap = await load_mad_phase(client, symbol)
  phase = snap.phase if snap else None
  preview = mad_hard_gate(phase=phase, strategy=gate_key)
  measured = {
    "mad_phase": phase,
    "mad_gate_strategy": gate_key,
    "mad_gate": preview.to_dict(),
  }
  if preview.would_block:
    return False, preview.reason_code, measured
  return True, preview.reason_code, measured


def mad_hard_gate(*, phase: str | None, strategy: str) -> MadGatePreview:
  """Hard gate by MAD phase × strategy family.

  Reversal families (range, liquidity sweep, structural reaction) block during
  ``expand`` — do not fade distribution. Continuation families (impulse,
  breakout retest) require ``manip`` or ``expand`` — need displacement first.
  ``unclear`` is always neutral (no block).
  """
  p = str(phase or "").casefold()
  strat = str(strategy or "").casefold()
  if not p or p == PHASE_UNCLEAR:
    return MadGatePreview(would_block=False, reason_code="mad_gate_neutral_unclear")
  if strat in _CONTINUATION_GATE_STRATEGIES:
    if p not in EXPANSION_PHASES:
      return MadGatePreview(
        would_block=True,
        reason_code="mad_gate_impulse_needs_manip_or_expand",
      )
  if strat in _REVERSAL_GATE_STRATEGIES:
    if p == PHASE_EXPAND:
      return MadGatePreview(
        would_block=True,
        reason_code="mad_gate_reversal_avoid_expand",
      )
  return MadGatePreview(would_block=False, reason_code="mad_gate_allowed")


def enrich_mad_payload_for_shadow(snap: MadPhaseSnapshot) -> dict[str, Any]:
  """Phase dict + continuous features + per-strategy ``would_gate`` previews."""
  payload = snap.to_dict()
  payload["features"] = compute_mad_features(snap).to_dict()
  payload["would_gate"] = {
    strategy: mad_hard_gate(phase=snap.phase, strategy=strategy).to_dict()
    for strategy in SHADOW_GATE_STRATEGIES
  }
  return payload


def _opt_int(value: Any) -> int | None:
  if value is None or value == "":
    return None
  try:
    return int(value)
  except (TypeError, ValueError):
    return None


def _session_hours(cfg: Any | None) -> tuple[int, int, int]:
  sessions = getattr(getattr(cfg, "market_data", None), "sessions", None)
  asia = int(getattr(sessions, "asia_start", 22) or 22)
  london = int(getattr(sessions, "london_start", 7) or 7)
  rollover = int(getattr(sessions, "daily_rollover_utc_hour", 21) or 21)
  return asia, london, rollover


def asia_day_key(ts: int, cfg: Any | None = None) -> str:
  """Trading-day id for the Asia box that contains / precedes ``ts``."""
  asia_start, london_start, _ = _session_hours(cfg)
  dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
  hour = dt.hour
  # Before London open → still on the Asia day that began previous calendar evening.
  if hour < london_start:
    start = (dt - timedelta(days=1)).replace(
      hour=asia_start, minute=0, second=0, microsecond=0,
    )
  elif hour >= asia_start:
    start = dt.replace(hour=asia_start, minute=0, second=0, microsecond=0)
  else:
    # London → pre-Asia: Asia day is the most recent sealed evening start.
    start = (dt - timedelta(days=1)).replace(
      hour=asia_start, minute=0, second=0, microsecond=0,
    )
  return start.strftime("%Y-%m-%d")


def asia_window_bounds(ts: int, cfg: Any | None = None) -> tuple[int, int]:
  """[start, end) unix bounds for the Asia session tied to ``ts``."""
  asia_start, london_start, _ = _session_hours(cfg)
  dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
  hour = dt.hour
  if hour < london_start:
    start_dt = (dt - timedelta(days=1)).replace(
      hour=asia_start, minute=0, second=0, microsecond=0,
    )
    end_dt = dt.replace(hour=london_start, minute=0, second=0, microsecond=0)
  elif hour >= asia_start:
    start_dt = dt.replace(hour=asia_start, minute=0, second=0, microsecond=0)
    end_dt = (dt + timedelta(days=1)).replace(
      hour=london_start, minute=0, second=0, microsecond=0,
    )
  else:
    start_dt = (dt - timedelta(days=1)).replace(
      hour=asia_start, minute=0, second=0, microsecond=0,
    )
    end_dt = dt.replace(hour=london_start, minute=0, second=0, microsecond=0)
  return int(start_dt.timestamp()), int(end_dt.timestamp())


def _bar_ts(index_value: Any) -> int:
  return int(pd.Timestamp(index_value).timestamp())


def filter_ohlc_window(
  df: pd.DataFrame,
  *,
  start_ts: int,
  end_ts: int,
) -> pd.DataFrame:
  if df is None or df.empty:
    return df
  ts = df.index.map(_bar_ts)
  mask = (ts >= int(start_ts)) & (ts < int(end_ts))
  return df.loc[mask]


def update_asia_range_seal(
  previous: AsiaRangeSeal | None,
  df: pd.DataFrame,
  *,
  now: int,
  session: str,
  cfg: Any | None = None,
  source: str = "m5",
) -> AsiaRangeSeal | None:
  """Build or extend Asia H/L; seal once session leaves Asia.

  ``df`` should be M5 (or M1) OHLC covering the Asia window.
  """
  day = asia_day_key(now, cfg)
  start_ts, end_ts = asia_window_bounds(now, cfg)
  window = filter_ohlc_window(df, start_ts=start_ts, end_ts=end_ts)
  if window is None or window.empty:
    if previous is not None and previous.day_key == day:
      # Seal if we left Asia even without new bars.
      if session != "asia" and not previous.sealed:
        return replace(previous, sealed=True, sealed_at=int(now), updated_at=int(now))
      return previous
    return previous

  high = float(window["high"].astype(float).max())
  low = float(window["low"].astype(float).min())
  if high < low:
    return previous
  count = int(len(window))

  if previous is None or previous.day_key != day:
    building = AsiaRangeSeal(
      day_key=day,
      high=high,
      low=low,
      sealed=False,
      sealed_at=None,
      bar_count=count,
      source=source,
      updated_at=int(now),
    )
  else:
    building = AsiaRangeSeal(
      day_key=day,
      high=max(float(previous.high), high),
      low=min(float(previous.low), low),
      sealed=bool(previous.sealed),
      sealed_at=previous.sealed_at,
      bar_count=max(int(previous.bar_count), count),
      source=source,
      updated_at=int(now),
    )

  if session != "asia" and not building.sealed:
    return replace(building, sealed=True, sealed_at=int(now))
  return building


def _price_vs_asia(price: float, asia: AsiaRangeSeal) -> str:
  if price > float(asia.high):
    return "above"
  if price < float(asia.low):
    return "below"
  return "inside"


def detect_asia_sweep_reclaim(
  bar_high: float,
  bar_low: float,
  bar_close: float,
  asia: AsiaRangeSeal,
  *,
  tolerance: float = 0.0,
) -> tuple[str | None, bool]:
  """Return (sweep_side, reclaim) for the latest bar vs Asia box."""
  tol = max(0.0, float(tolerance))
  swept_high = float(bar_high) > float(asia.high) + tol
  swept_low = float(bar_low) < float(asia.low) - tol
  if swept_high and float(bar_close) <= float(asia.high) + tol:
    return "high", True
  if swept_low and float(bar_close) >= float(asia.low) - tol:
    return "low", True
  if swept_high:
    return "high", False
  if swept_low:
    return "low", False
  return None, False


def classify_mad_phase(
  *,
  price: float,
  atr: float,
  session: str,
  asia: AsiaRangeSeal | None,
  m5_structure: str = "range",
  bar_high: float | None = None,
  bar_low: float | None = None,
  bar_close: float | None = None,
  impulse_atr_value: float | None = None,
  pip_size: float = 0.1,
) -> MadPhaseSnapshot:
  """Classify accum / manip / expand / unclear from Asia seal + tape."""
  atr_v = float(atr) if atr and atr > 0 else 0.0
  if asia is None or asia.width <= 0:
    return MadPhaseSnapshot(
      phase=PHASE_UNCLEAR,
      asia=asia,
      range_quality_atr=None,
      price_vs_asia=None,
      sweep_side=None,
      reclaim=False,
      reason_code="asia_range_missing",
    )

  rq = zone_width_atr(asia.high, asia.low, atr_v) if atr_v > 0 else None
  vs = _price_vs_asia(float(price), asia)
  sweep_side = None
  reclaim = False
  if None not in (bar_high, bar_low, bar_close):
    sweep_side, reclaim = detect_asia_sweep_reclaim(
      float(bar_high),
      float(bar_low),
      float(bar_close),
      asia,
      tolerance=max(float(pip_size), atr_v * 0.05) if atr_v > 0 else float(pip_size),
    )

  measured: dict[str, Any] = {
    "session": session,
    "asia_sealed": bool(asia.sealed),
    "asia_day_key": asia.day_key,
    "impulse_atr": impulse_atr_value,
  }

  # Manipulation: raid beyond Asia edge then reclaim (classic London open print).
  if sweep_side and reclaim:
    return MadPhaseSnapshot(
      phase=PHASE_MANIP,
      asia=asia,
      range_quality_atr=rq,
      price_vs_asia=vs,
      sweep_side=sweep_side,
      reclaim=True,
      reason_code="asia_sweep_reclaim",
      measured=measured,
    )

  # Expansion: accepted break of sealed Asia box or strong impulse away.
  break_dist = None
  if atr_v > 0 and vs == "above":
    break_dist = safe_div(float(price) - float(asia.high), atr_v)
  elif atr_v > 0 and vs == "below":
    break_dist = safe_div(float(asia.low) - float(price), atr_v)
  impulse = float(impulse_atr_value or 0.0)
  if asia.sealed and (
    (break_dist is not None and break_dist >= _EXPAND_BREAK_ATR and not reclaim)
    or impulse >= _EXPAND_IMPULSE_ATR
  ):
    return MadPhaseSnapshot(
      phase=PHASE_EXPAND,
      asia=asia,
      range_quality_atr=rq,
      price_vs_asia=vs,
      sweep_side=sweep_side,
      reclaim=False,
      reason_code="asia_break_or_impulse",
      measured=measured,
    )

  # Accumulation: inside (or building) Asia box with sane RQ + range structure.
  structure = str(m5_structure or "").casefold()
  rq_val = float(rq) if rq is not None else None
  rq_sealed_ok = rq_val is not None and _RQ_ACCUM_MIN <= rq_val <= _RQ_ACCUM_MAX
  building_asia = (
    session == "asia"
    and not asia.sealed
    and vs == "inside"
    and rq_val is not None
    and _RQ_ACCUM_MIN <= rq_val <= _RQ_BUILDING_ACCUM_MAX
  )
  inside_or_building = vs == "inside" or (session == "asia" and not asia.sealed)
  if (
    inside_or_building
    and structure in {"range", "unknown", ""}
    and (rq_sealed_ok or building_asia)
  ):
    return MadPhaseSnapshot(
      phase=PHASE_ACCUM,
      asia=asia,
      range_quality_atr=rq,
      price_vs_asia=vs,
      sweep_side=sweep_side,
      reclaim=False,
      reason_code="asia_building_accum" if building_asia and not rq_sealed_ok else "asia_box_accum",
      measured=measured,
    )

  return MadPhaseSnapshot(
    phase=PHASE_UNCLEAR,
    asia=asia,
    range_quality_atr=rq,
    price_vs_asia=vs,
    sweep_side=sweep_side,
    reclaim=reclaim,
    reason_code="no_mad_signature",
    measured=measured,
  )


async def load_asia_range_seal(client: Any, symbol: str) -> AsiaRangeSeal | None:
  raw = await client.get(asia_range_key(symbol))
  if raw is None:
    return None
  try:
    import json

    data = json.loads(raw)
  except (TypeError, ValueError, json.JSONDecodeError):
    return None
  return AsiaRangeSeal.from_dict(data)


async def save_asia_range_seal(
  client: Any,
  symbol: str,
  seal: AsiaRangeSeal,
  *,
  ttl_seconds: int = 3 * 24 * 3600,
) -> None:
  import json

  await client.set(
    asia_range_key(symbol),
    json.dumps(seal.to_dict(), separators=(",", ":"), sort_keys=True),
    ex=max(3600, int(ttl_seconds)),
  )


async def save_mad_phase(
  client: Any,
  symbol: str,
  phase: MadPhaseSnapshot,
  *,
  ttl_seconds: int = 3 * 24 * 3600,
) -> None:
  import json

  payload = json.dumps(
    enrich_mad_payload_for_shadow(phase),
    separators=(",", ":"),
    sort_keys=True,
  )
  ttl = max(3600, int(ttl_seconds))
  pipe = client.pipeline(transaction=False)
  pipe.set(mad_phase_key(symbol), payload, ex=ttl)
  # Keep HFS telemetry key in sync for existing dig scripts.
  pipe.set(mad_last_key(symbol), payload, ex=ttl)
  await pipe.execute()


async def load_mad_phase(client: Any, symbol: str) -> MadPhaseSnapshot | None:
  import json

  raw = await client.get(mad_phase_key(symbol))
  if raw is None:
    raw = await client.get(mad_last_key(symbol))
  if raw is None:
    return None
  try:
    data = json.loads(raw)
  except (TypeError, ValueError, json.JSONDecodeError):
    return None
  asia = AsiaRangeSeal.from_dict(data.get("asia"))
  return MadPhaseSnapshot(
    phase=str(data.get("phase") or PHASE_UNCLEAR),
    asia=asia,
    range_quality_atr=(
      None if data.get("range_quality_atr") is None
      else float(data["range_quality_atr"])
    ),
    price_vs_asia=(
      None if data.get("price_vs_asia") is None
      else str(data["price_vs_asia"])
    ),
    sweep_side=(
      None if data.get("sweep_side") is None else str(data["sweep_side"])
    ),
    reclaim=bool(data.get("reclaim", False)),
    reason_code=str(data.get("reason_code") or ""),
    measured=dict(data.get("measured") or {}),
  )


async def refresh_mad_for_symbol(
  client: Any,
  *,
  symbol: str,
  ohlc: pd.DataFrame,
  now: int,
  session: str,
  price: float,
  atr: float,
  m5_structure: str = "range",
  bar_high: float | None = None,
  bar_low: float | None = None,
  bar_close: float | None = None,
  cfg: Any | None = None,
  pip_size: float = 0.1,
  source: str = "m5",
) -> MadPhaseSnapshot:
  """Update shared Asia seal + phase for technique and HFS lanes."""
  prior = await load_asia_range_seal(client, symbol)
  seal, phase = evaluate_mad_for_cycle(
    previous=prior,
    ohlc=ohlc,
    now=now,
    session=session,
    price=price,
    atr=atr,
    m5_structure=m5_structure,
    bar_high=bar_high,
    bar_low=bar_low,
    bar_close=bar_close,
    cfg=cfg,
    pip_size=pip_size,
    source=source,
  )
  if seal is not None:
    await save_asia_range_seal(client, symbol, seal)
  await save_mad_phase(client, symbol, phase)
  return phase


def evaluate_mad_for_cycle(
  *,
  previous: AsiaRangeSeal | None,
  ohlc: pd.DataFrame,
  now: int,
  session: str,
  price: float,
  atr: float,
  m5_structure: str,
  bar_high: float | None,
  bar_low: float | None,
  bar_close: float | None,
  cfg: Any | None = None,
  pip_size: float = 0.1,
  source: str = "m5",
) -> tuple[AsiaRangeSeal | None, MadPhaseSnapshot]:
  """Update Asia seal from OHLC and classify phase for one M1 cycle."""
  seal = update_asia_range_seal(
    previous,
    ohlc,
    now=now,
    session=session,
    cfg=cfg,
    source=source,
  )
  impulse = None
  if seal is not None and atr and atr > 0:
    impulse = abs(float(price) - float(seal.mid)) / float(atr)
  phase = classify_mad_phase(
    price=price,
    atr=atr,
    session=session,
    asia=seal,
    m5_structure=m5_structure,
    bar_high=bar_high,
    bar_low=bar_low,
    bar_close=bar_close,
    impulse_atr_value=impulse,
    pip_size=pip_size,
  )
  return seal, phase
