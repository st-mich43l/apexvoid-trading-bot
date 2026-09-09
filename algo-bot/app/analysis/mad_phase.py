"""MAD-0: Manipulation / Accumulation / Distribution phase + Asia range seal.

Shared Asia phase clock for technique structure analysis and entry-quality
soft confluence. ``mad_hard_gate`` / ``would_gate`` remain research stamps only
— they must not block trade-plan publish or activation
(``mad_hard_gate_enabled`` defaults false; live path does not call the gate).
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

# v2 (2026-09): directional manipulation/expansion, causal stale-day and
# double-sweep handling, ATR-normalized break/displacement evidence instead
# of Asia-midpoint distance, continuous confidence/affinity scoring. Must be
# persisted alongside every snapshot so replay/analysis never silently mixes
# v1 and v2 populations (v1 rows carry no `mad_version` key at all).
MAD_VERSION = 2

# Range quality (width/ATR): accumulation prefers a real but not huge box.
# Defaults below are the provisional fallback used when no config is
# supplied (e.g. direct unit-test calls to classify_mad_phase); production
# reads execution.mad.* via the _mad_*() accessors so these stay tunable
# without a code change (see §29 - no anecdotal hardcoding).
_RQ_ACCUM_MIN = 0.8
_RQ_ACCUM_MAX = 6.0
# Building Asia (unsealed): allow wider box — early session expansion is normal.
_RQ_BUILDING_ACCUM_MAX = 24.0
# Expansion: close beyond sealed Asia edge by this ATR multiple, or impulse.
_EXPAND_BREAK_ATR = 0.35
_EXPAND_IMPULSE_ATR = 1.25
# New (v2) expansion evidence defaults.
_EXPAND_ACCEPT_CLOSES = 2
_EXPAND_DISPLACEMENT_ATR = 1.25
# New (v2) manipulation-quality defaults.
_MANIP_MIN_PENETRATION_ATR = 0.05
_MANIP_MIN_RECLAIM_ATR = 0.05
# MAD -> confluence-v2 contribution cap lives in detectors.py
# (DetectorSettings.confluence_v2_mad_score_weight / _mad_score_weight) —
# that mechanism already self-normalizes against the v2 score range and
# measures ~7.5% of it at its current default, inside the §24 target
# (<=5-10%). Not duplicated here (§20 — one calculation per concept).


def _mad_cfg(cfg: Any | None) -> Any | None:
  """``execution.mad`` node, or ``None`` if absent (bare test stubs)."""
  return getattr(getattr(cfg, "execution", None), "mad", None)


def _expand_break_atr(cfg: Any | None) -> float:
  section = getattr(_mad_cfg(cfg), "expand", None)
  return float(getattr(section, "break_atr", _EXPAND_BREAK_ATR) or _EXPAND_BREAK_ATR)


def _expand_displacement_atr(cfg: Any | None) -> float:
  section = getattr(_mad_cfg(cfg), "expand", None)
  return float(
    getattr(section, "displacement_atr", _EXPAND_DISPLACEMENT_ATR)
    or _EXPAND_DISPLACEMENT_ATR
  )


def _expand_accept_closes(cfg: Any | None) -> int:
  section = getattr(_mad_cfg(cfg), "expand", None)
  return int(
    getattr(section, "accept_closes", _EXPAND_ACCEPT_CLOSES) or _EXPAND_ACCEPT_CLOSES
  )


def _manip_min_penetration_atr(cfg: Any | None) -> float:
  section = getattr(_mad_cfg(cfg), "manip", None)
  return float(
    getattr(section, "min_penetration_atr", _MANIP_MIN_PENETRATION_ATR)
    or _MANIP_MIN_PENETRATION_ATR
  )


def _manip_min_reclaim_atr(cfg: Any | None) -> float:
  section = getattr(_mad_cfg(cfg), "manip", None)
  return float(
    getattr(section, "min_reclaim_atr", _MANIP_MIN_RECLAIM_ATR)
    or _MANIP_MIN_RECLAIM_ATR
  )


def _accum_minimum_rq(cfg: Any | None) -> float:
  section = getattr(_mad_cfg(cfg), "accum", None)
  return float(getattr(section, "minimum_rq", _RQ_ACCUM_MIN) or _RQ_ACCUM_MIN)


def _accum_maximum_rq(cfg: Any | None) -> float:
  section = getattr(_mad_cfg(cfg), "accum", None)
  return float(getattr(section, "maximum_rq", _RQ_ACCUM_MAX) or _RQ_ACCUM_MAX)


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
  # v2 additions — directional manipulation (§3), expansion direction (§5),
  # phase-level evidence strength (§10). All None/0.0 on a v1-shaped payload
  # (mad_version absent) so old/new populations stay distinguishable.
  manipulation_direction: str | None = None
  expansion_direction: str | None = None
  confidence: float = 0.0
  mad_version: int = MAD_VERSION

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
      "manipulation_direction": self.manipulation_direction,
      "expansion_direction": self.expansion_direction,
      "confidence": round(float(self.confidence), 4),
      "mad_version": self.mad_version,
    }
    return payload

  @classmethod
  def from_dict(cls, data: Any) -> "MadPhaseSnapshot":
    """Reconstruct from a ``to_dict()``-shaped payload (Redis JSON, or the
    dict already carried on ``DetectionContext.mad`` — see detectors.py)."""
    data = data or {}
    asia = AsiaRangeSeal.from_dict(data.get("asia"))
    return cls(
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
      manipulation_direction=(
        None if data.get("manipulation_direction") is None
        else str(data["manipulation_direction"])
      ),
      expansion_direction=(
        None if data.get("expansion_direction") is None
        else str(data["expansion_direction"])
      ),
      confidence=float(data.get("confidence") or 0.0),
      # A payload with no mad_version key at all predates v2 - treat it as
      # v1, never as the current MAD_VERSION default, so replay/analysis can
      # tell legacy and new snapshots apart even from stale cached data.
      mad_version=int(data["mad_version"]) if "mad_version" in data else 1,
    )


def asia_range_key(symbol: str) -> str:
  """Shared Asia box — technique lane + HFS."""
  return f"mad:asia_range:{str(symbol).upper()}"


def mad_phase_key(symbol: str) -> str:
  """Shared phase snapshot — technique lane + HFS."""
  return f"mad:phase:{str(symbol).upper()}"


def mad_last_key(symbol: str) -> str:
  """Legacy HFS-only alias; prefer ``mad_phase_key``."""
  return f"scalp:last_mad:{str(symbol).upper()}"


# Soft entry-quality families (never hard-block trade plans).
# M1 scalping: no MAD ranking/gates. Technique: soft confluence only.
RANGE_EDGE_MAD_FAMILIES = frozenset({
  "range_scalp",
  "range_edge",
  "range_edge_mean_reversion",
})
REACTION_MAD_FAMILIES = frozenset({
  "reaction",
  "liquidity",
  "structural_reaction",
  "liquidity_sweep_reversal",
})
EXPANSION_PHASES = frozenset({PHASE_EXPAND, PHASE_MANIP})


def mad_soft_bonus(*, phase: str | None, family: str) -> float:
  """Soft confluence for entry quality / structure — never a hard gate.

  - ``accum`` favors Range Edge mean-reversion inside the Asia box
  - ``manip`` favors structural reaction / liquidity fade after Asia sweep-reclaim
  Impulse / expand do not receive soft favor (owner: no MAD ranking of
  continuation via soft score either).
  """
  p = str(phase or "").casefold()
  fam = str(family or "").casefold()
  if p == PHASE_ACCUM and fam in RANGE_EDGE_MAD_FAMILIES:
    return 0.12
  if p == PHASE_MANIP and fam in REACTION_MAD_FAMILIES:
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
  """Research preview of phase × strategy affinity (observe / mad_replay only)."""

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

  # v2: real displacement/acceptance evidence, not Asia-midpoint distance
  # (§4 — midpoint distance is kept only as descriptive telemetry, see
  # `measured["midpoint_distance_atr"]`, and never drives this score).
  expand = 0.0
  break_dist = snap.measured.get("break_distance_atr")
  if break_dist is not None and float(break_dist) > 0:
    expand = max(expand, _clamp01(float(break_dist) / _EXPAND_BREAK_ATR * 0.6))
  displacement = snap.measured.get("displacement_atr")
  if displacement is not None and float(displacement) > 0:
    expand = max(expand, _clamp01(float(displacement) / _EXPAND_DISPLACEMENT_ATR * 0.85))
  accepted = snap.measured.get("accepted_closes")
  if accepted is not None and int(accepted) > 0:
    expand = max(
      expand, min(1.0, int(accepted) / max(1, _EXPAND_ACCEPT_CLOSES) * 0.5),
    )
  if snap.phase == PHASE_EXPAND:
    expand = max(expand, 0.78)

  return MadFeatureScores(
    accum=_clamp01(accum),
    manip=_clamp01(manip),
    expand=_clamp01(expand),
  )


# Gate-strategy keys (the same vocabulary as mad_hard_gate/SHADOW_GATE_STRATEGIES,
# produced by mad_gate_strategy_for_setup) that each phase's own evidence
# actually supports. Kept separate from _CONTINUATION_GATE_STRATEGIES /
# _REVERSAL_GATE_STRATEGIES above (those describe hard-gate DIRECTION of
# risk - "needs manip/expand" / "avoid expand" - not which specific phase's
# affinity score should credit which strategy).
_ACCUM_AFFINITY_STRATEGIES = frozenset({"range_edge_mean_reversion", "range_sweep"})
_MANIP_AFFINITY_STRATEGIES = frozenset({"structural_reaction", "liquidity_sweep_reversal"})
_EXPAND_AFFINITY_STRATEGIES = _CONTINUATION_GATE_STRATEGIES


@dataclass(frozen=True)
class MadAffinityScore:
  """Bounded (0..1) directional, strategy-aware MAD quality for one candidate.

  ``final`` is a product of its components, so it is exactly 0.0 whenever the
  trade's direction disagrees with the phase's manipulation/expansion
  direction, or the strategy isn't one this phase's evidence supports — never
  a bypass for poor structure/location/trigger/room (§9). This is contextual
  scoring only; it must never be used to override an existing hard gate.
  """

  phase_score: float
  direction_score: float
  strategy_score: float
  confidence: float
  final: float

  def to_dict(self) -> dict[str, float]:
    return {
      "phase_score": round(float(self.phase_score), 4),
      "direction_score": round(float(self.direction_score), 4),
      "strategy_score": round(float(self.strategy_score), 4),
      "confidence": round(float(self.confidence), 4),
      "final": round(float(self.final), 4),
    }


def compute_mad_affinity(
  snapshot: MadPhaseSnapshot,
  *,
  direction: str,
  strategy: str | None,
) -> MadAffinityScore:
  """Directional, strategy-aware MAD affinity for one candidate trade.

  ``direction`` is the candidate's BUY/SELL. ``strategy`` is a gate-strategy
  key from :func:`mad_gate_strategy_for_setup` (or ``None`` if unresolved,
  which always scores 0 strategy affinity).
  """
  want = str(direction or "").upper()
  strat = str(strategy or "")
  features = compute_mad_features(snapshot)

  if snapshot.phase == PHASE_ACCUM:
    phase_score = features.accum
    # MAD does not re-judge premium/discount location for accumulation -
    # that is Structure -> Location's job upstream (§1). MAD's contribution
    # here is purely "is this a genuine, contained range," which is
    # direction-agnostic by construction.
    direction_score = 1.0 if want in {"BUY", "SELL"} else 0.0
    strategy_score = 1.0 if strat in _ACCUM_AFFINITY_STRATEGIES else 0.0
  elif snapshot.phase == PHASE_MANIP:
    phase_score = features.manip
    direction_score = (
      1.0
      if snapshot.manipulation_direction is not None
      and snapshot.manipulation_direction == want
      else 0.0
    )
    strategy_score = 1.0 if strat in _MANIP_AFFINITY_STRATEGIES else 0.0
  elif snapshot.phase == PHASE_EXPAND:
    phase_score = features.expand
    direction_score = (
      1.0
      if snapshot.expansion_direction is not None
      and snapshot.expansion_direction == want
      else 0.0
    )
    strategy_score = 1.0 if strat in _EXPAND_AFFINITY_STRATEGIES else 0.0
  else:
    phase_score = 0.0
    direction_score = 0.0
    strategy_score = 0.0

  confidence = _clamp01(snapshot.confidence)
  final = _clamp01(phase_score) * direction_score * strategy_score * confidence
  return MadAffinityScore(
    phase_score=_clamp01(phase_score),
    direction_score=_clamp01(direction_score),
    strategy_score=_clamp01(strategy_score),
    confidence=confidence,
    final=_clamp01(final),
  )


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


def mad_hard_gate(*, phase: str | None, strategy: str) -> MadGatePreview:
  """Counterfactual phase × strategy affinity for research / ``would_gate``.

  Reversal families prefer not to fade ``expand``. Continuation families prefer
  ``manip`` or ``expand``. ``unclear`` is always neutral. Live activation must
  not call this as a publish veto.
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
  """Return (sweep_side, reclaim) for the latest bar vs Asia box.

  ``sweep_side`` is ``"both"`` when the same bar sweeps both edges - no
  directional read is safe there (§11); callers must not pick a side by
  branch ordering. ``reclaim`` is always False for a double sweep.
  """
  tol = max(0.0, float(tolerance))
  swept_high = float(bar_high) > float(asia.high) + tol
  swept_low = float(bar_low) < float(asia.low) - tol
  if swept_high and swept_low:
    return "both", False
  if swept_high and float(bar_close) <= float(asia.high) + tol:
    return "high", True
  if swept_low and float(bar_close) >= float(asia.low) - tol:
    return "low", True
  if swept_high:
    return "high", False
  if swept_low:
    return "low", False
  return None, False


def _manipulation_direction(sweep_side: str | None, reclaim: bool) -> str | None:
  """Directional read of a resolved (single-side) sweep+reclaim (§3).

  A high-side sweep that reclaims is bearish liquidity manipulation - it
  favors a SELL reaction, never a BUY. A low-side sweep that reclaims is
  bullish - it favors BUY, never SELL. Anything else (no reclaim, no sweep,
  or a double sweep) carries no directional read.
  """
  if not reclaim or sweep_side not in {"high", "low"}:
    return None
  return "SELL" if sweep_side == "high" else "BUY"


def _manip_quality(
  bar_high: float,
  bar_low: float,
  bar_close: float,
  asia: AsiaRangeSeal,
  atr_v: float,
  sweep_side: str | None,
) -> dict[str, float | None]:
  """Normalized penetration/reclaim-depth/close-location for a resolved sweep (§12)."""
  if sweep_side not in {"high", "low"} or atr_v <= 0:
    return {
      "sweep_penetration_atr": None,
      "reclaim_depth_atr": None,
      "close_location": None,
    }
  high, low, close = float(bar_high), float(bar_low), float(bar_close)
  bar_range = high - low
  if sweep_side == "high":
    penetration = safe_div(high - float(asia.high), atr_v)
    reclaim_depth = safe_div(float(asia.high) - close, atr_v)
    # SELL reclaim quality: strong rejection closes near the bar LOW.
    close_location = safe_div(high - close, bar_range) if bar_range > 0 else None
  else:
    penetration = safe_div(float(asia.low) - low, atr_v)
    reclaim_depth = safe_div(close - float(asia.low), atr_v)
    # BUY reclaim quality: strong rejection closes near the bar HIGH.
    close_location = safe_div(close - low, bar_range) if bar_range > 0 else None
  return {
    "sweep_penetration_atr": penetration,
    "reclaim_depth_atr": reclaim_depth,
    "close_location": close_location,
  }


def _manip_confidence(measured: dict[str, Any], cfg: Any | None) -> float:
  """Evidence strength for a MANIP classification (§10) - meaningful
  penetration + a strong close back inside score higher than a tiny edge
  poke that technically satisfies the sweep+reclaim definition."""
  penetration = measured.get("sweep_penetration_atr")
  reclaim_depth = measured.get("reclaim_depth_atr")
  close_location = measured.get("close_location")
  if penetration is None or reclaim_depth is None:
    return 0.0
  min_pen = max(1e-9, _manip_min_penetration_atr(cfg))
  min_reclaim = max(1e-9, _manip_min_reclaim_atr(cfg))
  pen_score = _clamp01(float(penetration) / (min_pen * 4.0))
  reclaim_score = _clamp01(float(reclaim_depth) / (min_reclaim * 4.0))
  loc_score = _clamp01(float(close_location)) if close_location is not None else 0.5
  return _clamp01(0.45 * pen_score + 0.35 * reclaim_score + 0.20 * loc_score)


def _expand_confidence(
  measured: dict[str, Any],
  cfg: Any | None,
) -> float:
  """Evidence strength for an EXPAND classification (§10)."""
  break_dist = measured.get("break_distance_atr")
  displacement = measured.get("displacement_atr")
  accepted = measured.get("accepted_closes")
  break_score = (
    _clamp01(float(break_dist) / max(1e-9, _expand_break_atr(cfg) * 3.0))
    if break_dist is not None else 0.0
  )
  disp_score = (
    _clamp01(float(displacement) / max(1e-9, _expand_displacement_atr(cfg)))
    if displacement is not None else 0.0
  )
  accept_target = max(1, _expand_accept_closes(cfg))
  accept_score = (
    _clamp01(float(accepted) / (accept_target * 2.0)) if accepted is not None else 0.0
  )
  return _clamp01(0.4 * break_score + 0.4 * disp_score + 0.2 * accept_score)


def _accum_confidence(
  rq: float | None,
  *,
  building: bool,
  cfg: Any | None,
) -> float:
  """Evidence strength for an ACCUM classification (§10) - contained,
  volatility-normalized width scores higher near the middle of the
  accepted range-quality band than near either edge of it."""
  if rq is None:
    return 0.0
  minimum = _accum_minimum_rq(cfg)
  maximum = _RQ_BUILDING_ACCUM_MAX if building else _accum_maximum_rq(cfg)
  if not (minimum <= float(rq) <= maximum):
    return 0.0
  mid = (minimum + maximum) / 2.0
  span = max(1e-9, (maximum - minimum) / 2.0)
  distance_from_mid = abs(float(rq) - mid) / span
  return _clamp01(1.0 - 0.5 * distance_from_mid)


def _displacement_atr(
  ohlc: pd.DataFrame | None,
  atr_v: float,
  *,
  lookback: int = 5,
) -> float | None:
  """Causal |close_t - close_t-k| / ATR - only bars already in ``ohlc``."""
  if ohlc is None or ohlc.empty or atr_v <= 0:
    return None
  closes = ohlc["close"].astype(float)
  k = min(int(lookback), len(closes) - 1)
  if k <= 0:
    return None
  return safe_div(abs(float(closes.iloc[-1]) - float(closes.iloc[-1 - k])), atr_v)


def _accepted_closes_beyond_edge(
  ohlc: pd.DataFrame | None,
  asia: AsiaRangeSeal | None,
  vs: str | None,
) -> int:
  """Count of the most recent consecutive closes beyond the Asia edge on
  the ``vs`` side - causal, only bars already in ``ohlc``."""
  if ohlc is None or ohlc.empty or asia is None or vs not in {"above", "below"}:
    return 0
  closes = ohlc["close"].astype(float)
  edge = float(asia.high) if vs == "above" else float(asia.low)
  count = 0
  for close in reversed(closes.tolist()):
    beyond = close > edge if vs == "above" else close < edge
    if not beyond:
      break
    count += 1
  return count


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
  midpoint_distance_atr: float | None = None,
  ohlc: pd.DataFrame | None = None,
  atr_long: float | None = None,
  pip_size: float = 0.1,
  now: int | None = None,
  cfg: Any | None = None,
) -> MadPhaseSnapshot:
  """Classify accum / manip / expand / unclear from Asia seal + tape.

  ``now``/``cfg`` are optional so direct unit tests of a single phase branch
  can omit them; production callers (``evaluate_mad_for_cycle``) always
  supply both so the stale-day check (§7) is active. ``ohlc`` is the causal
  OHLC frame (bars already closed by ``now``) backing displacement/
  acceptance evidence (§4); omitting it degrades gracefully (no accepted-
  closes/displacement credit), it never raises.
  """
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

  # §7 — fail closed on a stale Asia seal rather than classify against
  # yesterday's box. update_asia_range_seal can silently hand back a
  # previous-day seal when the current window has no bars yet; day_key
  # disagreeing with today's key is the only reliable signal of that.
  if now is not None and asia.day_key != asia_day_key(now, cfg):
    return MadPhaseSnapshot(
      phase=PHASE_UNCLEAR,
      asia=asia,
      range_quality_atr=None,
      price_vs_asia=None,
      sweep_side=None,
      reclaim=False,
      reason_code="asia_range_stale_or_missing",
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

  break_dist = None
  if atr_v > 0 and vs == "above":
    break_dist = safe_div(float(price) - float(asia.high), atr_v)
  elif atr_v > 0 and vs == "below":
    break_dist = safe_div(float(asia.low) - float(price), atr_v)
  displacement = _displacement_atr(ohlc, atr_v)
  accepted_closes = _accepted_closes_beyond_edge(ohlc, asia, vs)

  measured: dict[str, Any] = {
    "session": session,
    "asia_sealed": bool(asia.sealed),
    "asia_day_key": asia.day_key,
    # Descriptive only (§4) — never an EXPAND trigger. This is distance from
    # the Asia MIDPOINT, not a displacement/impulse measure.
    "midpoint_distance_atr": midpoint_distance_atr,
    "break_distance_atr": break_dist,
    "displacement_atr": displacement,
    "accepted_closes": accepted_closes,
    "atr_short_long_ratio": (
      safe_div(atr_v, float(atr_long)) if atr_long and float(atr_long) > 0 else None
    ),
  }

  # §11 — a bar sweeping both edges gets no arbitrary direction.
  if sweep_side == "both":
    return MadPhaseSnapshot(
      phase=PHASE_UNCLEAR,
      asia=asia,
      range_quality_atr=rq,
      price_vs_asia=vs,
      sweep_side=sweep_side,
      reclaim=False,
      reason_code="asia_double_sweep",
      measured=measured,
    )

  # Manipulation: raid beyond Asia edge then reclaim (classic London open
  # print). Phase classification stays threshold-free on penetration depth
  # (a tiny poke still IS a sweep+reclaim) - depth only drives confidence
  # (§10/§26 "tiny sweep -> low confidence", not "tiny sweep -> not manip").
  if sweep_side and reclaim:
    measured.update(
      _manip_quality(
        float(bar_high), float(bar_low), float(bar_close), asia, atr_v, sweep_side,
      )
    )
    return MadPhaseSnapshot(
      phase=PHASE_MANIP,
      asia=asia,
      range_quality_atr=rq,
      price_vs_asia=vs,
      sweep_side=sweep_side,
      reclaim=True,
      reason_code="asia_sweep_reclaim",
      measured=measured,
      manipulation_direction=_manipulation_direction(sweep_side, reclaim),
      confidence=_manip_confidence(measured, cfg),
    )

  # Expansion: accepted break of sealed Asia box, or strong causal
  # displacement — never Asia-midpoint distance (§4).
  accepted_ok = (
    break_dist is not None
    and break_dist >= _expand_break_atr(cfg)
    and accepted_closes >= _expand_accept_closes(cfg)
    and not reclaim
  )
  displaced_ok = displacement is not None and displacement >= _expand_displacement_atr(cfg)
  if asia.sealed and vs in {"above", "below"} and (accepted_ok or displaced_ok):
    return MadPhaseSnapshot(
      phase=PHASE_EXPAND,
      asia=asia,
      range_quality_atr=rq,
      price_vs_asia=vs,
      sweep_side=sweep_side,
      reclaim=False,
      reason_code="asia_break_accepted" if accepted_ok else "asia_strong_displacement",
      measured=measured,
      expansion_direction=("BUY" if vs == "above" else "SELL"),
      confidence=_expand_confidence(measured, cfg),
    )

  # Accumulation: inside (or building) Asia box with sane RQ + range structure.
  structure = str(m5_structure or "").casefold()
  rq_val = float(rq) if rq is not None else None
  minimum_rq = _accum_minimum_rq(cfg)
  maximum_rq = _accum_maximum_rq(cfg)
  rq_sealed_ok = rq_val is not None and minimum_rq <= rq_val <= maximum_rq
  building_asia = (
    session == "asia"
    and not asia.sealed
    and vs == "inside"
    and rq_val is not None
    and minimum_rq <= rq_val <= _RQ_BUILDING_ACCUM_MAX
  )
  inside_or_building = vs == "inside" or (session == "asia" and not asia.sealed)
  if (
    inside_or_building
    and structure in {"range", "unknown", ""}
    and (rq_sealed_ok or building_asia)
  ):
    is_building = building_asia and not rq_sealed_ok
    return MadPhaseSnapshot(
      phase=PHASE_ACCUM,
      asia=asia,
      range_quality_atr=rq,
      price_vs_asia=vs,
      sweep_side=sweep_side,
      reclaim=False,
      reason_code="asia_building_accum" if is_building else "asia_box_accum",
      measured=measured,
      confidence=_accum_confidence(rq_val, building=is_building, cfg=cfg),
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


# §6 — accumulation-quality telemetry stub: a small rolling history of past
# sealed Asia range widths per symbol, so replay/analysis can compare
# today's range against recent sessions instead of a fixed ATR band alone.
# Observational only — nothing in classify_mad_phase scores or gates on this
# yet; wiring it into MadPhaseSnapshot.measured is a follow-up once there is
# enough history to validate the comparison against.
ASIA_RANGE_HISTORY_MAX = 20
_ASIA_RANGE_HISTORY_TTL = 30 * 24 * 3600


def asia_range_history_key(symbol: str) -> str:
  return f"mad:asia_range_history:{str(symbol).upper()}"


async def record_asia_range_history_sample(
  client: Any,
  symbol: str,
  seal: AsiaRangeSeal | None,
) -> None:
  """Append a newly-sealed Asia range width once per (symbol, day)."""
  if seal is None or not seal.sealed:
    return
  key = asia_range_history_key(symbol)
  marker_key = f"{key}:last_day"
  last_day = await client.get(marker_key)
  if last_day is not None:
    last_day = last_day.decode() if isinstance(last_day, bytes) else str(last_day)
    if last_day == seal.day_key:
      return
  pipe = client.pipeline(transaction=False)
  pipe.lpush(key, f"{seal.day_key}:{seal.width}")
  pipe.ltrim(key, 0, ASIA_RANGE_HISTORY_MAX - 1)
  pipe.expire(key, _ASIA_RANGE_HISTORY_TTL)
  pipe.set(marker_key, seal.day_key, ex=_ASIA_RANGE_HISTORY_TTL)
  await pipe.execute()


async def load_asia_range_history(client: Any, symbol: str) -> list[float]:
  """Recent sealed Asia range widths, most-recent-first."""
  raw = await client.lrange(asia_range_history_key(symbol), 0, ASIA_RANGE_HISTORY_MAX - 1)
  widths: list[float] = []
  for item in raw or ():
    text = item.decode() if isinstance(item, (bytes, bytearray)) else str(item)
    try:
      widths.append(float(text.rsplit(":", 1)[-1]))
    except (ValueError, IndexError):
      continue
  return widths


def range_percentile(current_width: float, history: list[float]) -> float | None:
  """Percentile (0..1) of ``current_width`` within ``history`` (telemetry)."""
  if not history:
    return None
  below_or_equal = sum(1 for width in history if width <= current_width)
  return below_or_equal / len(history)


def range_vs_recent_median(current_width: float, history: list[float]) -> float | None:
  """``current_width`` / median(``history``) (telemetry, None if no history)."""
  if not history:
    return None
  ordered = sorted(history)
  n = len(ordered)
  mid = n // 2
  median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
  if median <= 0:
    return None
  return float(current_width) / median


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
  return MadPhaseSnapshot.from_dict(data)


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
  atr_long: float | None = None,
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
    atr_long=atr_long,
  )
  if seal is not None:
    await save_asia_range_seal(client, symbol, seal)
  await save_mad_phase(client, symbol, phase)
  await record_asia_range_history_sample(client, symbol, seal)
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
  atr_long: float | None = None,
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
  midpoint_distance = None
  if seal is not None and atr and atr > 0:
    midpoint_distance = abs(float(price) - float(seal.mid)) / float(atr)
  phase = classify_mad_phase(
    price=price,
    atr=atr,
    session=session,
    asia=seal,
    m5_structure=m5_structure,
    bar_high=bar_high,
    bar_low=bar_low,
    bar_close=bar_close,
    midpoint_distance_atr=midpoint_distance,
    ohlc=ohlc,
    atr_long=atr_long,
    pip_size=pip_size,
    now=now,
    cfg=cfg,
  )
  return seal, phase
