"""S14C: deterministic same-input Go-vs-Python policy-input replay (XAU M5 supply/demand).

Both engines are fed the *same bytes*: one immutable real multi-timeframe closed-bar
capture (``contracts/analysis/replay/*.json``). The Go side is
``cmd/replay -capture`` (its envelopes go through the production decoder and the
live adapter ``build_strategy_match``); the Python side is the legacy detectors
(``build_context`` + the live technique detector ``supply_demand_technique_reaction``,
the "Supply Demand" source the scanner runs in ``DEFAULT_DETECTORS``) run on the
frames visible at each M5 close. Nothing is invented: a value neither engine
produced is ``None`` and counted as such; the report never fills a gap.

What is compared, per matched setup (the policy inputs the live pipeline consumes):

* reaction confirmation: bar time and kind;
* entry / structural zone geometry and the invalidation edge, in price and ATR;
* ATR;
* higher-timeframe bias (Python H1/M15 vs the Go H1/H4 the adapter maps);
* confluence and the tier it maps to;
* opposing-structure verdict from the worker's own Python barrier check (the
  retained duplicate computation) evaluated on identical frames for both sides.

The verdict is deliberately conservative: ``pass`` only when nothing is unresolved.
Differences are reported, categorised, and left for an owner disposition; this
module never approves anything.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from app.analysis_client.models import OpportunityEnvelope, OpportunityTopic, parse_analysis_event
from app.autotrade import go_opportunity_policy as pol
from app.autotrade.execution_policy import classify_tier, strategy_family

REPORT_VERSION = 1
TF_MINUTES = {"M5": 5, "M15": 15, "H1": 60, "H4": 240}
SCOPES = ("supply", "demand")
# Python confirmation kinds that are a wick/body rejection of the zone, the only
# thing Go's ``reaction_type`` ("rejection") asserts.
PYTHON_REJECTION_KINDS = frozenset({"wick_rejection"})
ATR_TOLERANCE_REL = 0.01


class ReplayError(ValueError):
  """The capture or an input file is malformed; never repaired."""


# ---- capture -------------------------------------------------------------------------

@dataclass(frozen=True)
class Capture:
  symbol: str
  provenance: dict[str, Any]
  frames: dict[str, pd.DataFrame]
  h4_derived: bool
  sha256: str
  path: str


def _frame(tf: str, rows: list[list[float]]) -> pd.DataFrame:
  minutes = TF_MINUTES[tf]
  previous = None
  for i, row in enumerate(rows):
    if len(row) != 6:
      raise ReplayError(f"{tf} row {i} has {len(row)} columns")
    t, o, h, l, c, _ = row
    if h < l or h < o or h < c or l > o or l > c or l <= 0:
      raise ReplayError(f"{tf} bar {i} at t={int(t)} is not OHLC-consistent")
    if int(t) % (minutes * 60) != 0:
      raise ReplayError(f"{tf} bar {i} at t={int(t)} is not aligned to its timeframe")
    if previous is not None and int(t) <= previous:
      raise ReplayError(f"{tf} bars must be strictly increasing (t={int(t)} after t={previous})")
    previous = int(t)
  df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"])
  df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("t").astype("int64"), unit="s", utc=True), name="time")
  return df


def derive_h4(h1: pd.DataFrame) -> pd.DataFrame:
  """UTC-aligned H4 from H1, complete four-bar buckets only (same rule as the Go side)."""
  rows: list[list[float]] = []
  bucket = 4 * 3600
  stamps = [int(ts.timestamp()) for ts in h1.index]
  i = 0
  while i < len(stamps):
    start = stamps[i] - stamps[i] % bucket
    j = i
    while j < len(stamps) and stamps[j] - stamps[j] % bucket == start:
      j += 1
    group = h1.iloc[i:j]
    if len(group) == 4 and stamps[i] == start:
      rows.append([start, float(group["open"].iloc[0]), float(group["high"].max()), float(group["low"].min()), float(group["close"].iloc[-1]), float(group["volume"].sum())])
    i = j
  return _frame("H4", rows)


def load_capture(path: str | Path, *, derive_h4_from_h1: bool = False) -> Capture:
  """Load and validate a capture. H4 is derived from H1 only when asked: the
  production feed delivers no H4 (config/trading-bot.yml), so the faithful
  default replays exactly the timeframes the live system has."""
  raw = Path(path).read_bytes()
  try:
    data = json.loads(raw)
  except json.JSONDecodeError as exc:
    raise ReplayError(f"{path}: {exc}") from None
  if data.get("version") != 1 or not data.get("symbol") or not data.get("timeframes"):
    raise ReplayError("capture needs version 1, a symbol and timeframes")
  if data.get("columns") != ["t", "open", "high", "low", "close", "volume"]:
    raise ReplayError("capture columns must be t,open,high,low,close,volume")
  frames = {}
  for tf, rows in data["timeframes"].items():
    if tf not in TF_MINUTES:
      raise ReplayError(f"unknown timeframe {tf!r}")
    frames[tf] = _frame(tf, rows)
  h4_derived = False
  if derive_h4_from_h1 and "H4" not in frames and "H1" in frames:
    frames["H4"] = derive_h4(frames["H1"])
    h4_derived = True
  if "M5" not in frames:
    raise ReplayError("capture has no M5 bars")
  return Capture(str(data["symbol"]).upper(), dict(data.get("provenance") or {}), frames, h4_derived, hashlib.sha256(raw).hexdigest(), str(path))


def frames_at(capture: Capture, close_at: int, *, lookbacks: dict[str, int] | None = None) -> dict[str, pd.DataFrame]:
  """The bars a live cycle would see at ``close_at``: only bars already closed."""
  out = {}
  for tf, df in capture.frames.items():
    closes = df.index + pd.Timedelta(minutes=TF_MINUTES[tf])
    visible = df[closes <= pd.Timestamp(close_at, unit="s", tz="UTC")]
    if lookbacks and tf in lookbacks:
      visible = visible.tail(lookbacks[tf])
    if not visible.empty:
      out[tf] = visible
  return out


# ---- Go side -------------------------------------------------------------------------

@dataclass(frozen=True)
class GoCase:
  event: OpportunityEnvelope
  match: Any | None
  rejection: str | None

  @property
  def id(self) -> str:
    return self.event.payload.id

  @property
  def scope(self) -> str:
    return self.event.payload.strategy

  @property
  def confirmation_at(self) -> int:
    return int(self.event.payload.technical_context.confirmation.confirmation_bar_time)


def load_go_cases(path: str | Path) -> tuple[list[GoCase], Counter]:
  """Decode each envelope with the production decoder and adapt it with the live
  adapter. Returns only confirmed reviewed-scope M5 cases plus a tally of what
  was left out (so nothing disappears silently)."""
  cases: list[GoCase] = []
  skipped: Counter = Counter()
  for number, line in enumerate(Path(path).read_text().splitlines(), 1):
    if not line.strip():
      continue
    try:
      event = parse_analysis_event(OpportunityTopic, line)
    except Exception as exc:  # noqa: BLE001 - the decoder's own error text is the evidence
      raise ReplayError(f"{path}:{number}: {exc}") from None
    profile = pol.REVIEWED_SCOPES.get(event.payload.strategy)
    tech = event.payload.technical_context
    if profile is None:
      skipped["scope_not_reviewed"] += 1
      continue
    if tech is None or tech.confirmation is None:
      skipped["resting_zone_not_confirmed"] += 1
      continue
    try:
      match = pol.build_strategy_match(event, profile=profile, epoch=0, now=event.payload.created_at + 1)
      cases.append(GoCase(event, match, None))
    except pol.AdapterRejection as exc:
      cases.append(GoCase(event, None, exc.code))
  cases.sort(key=lambda c: (c.confirmation_at, c.id))
  return cases, skipped


# ---- Python side ---------------------------------------------------------------------

@dataclass(frozen=True)
class PythonObservation:
  id: str
  scope: str
  direction: str
  detected_at_close: int
  confirmation_bar_ts: int | None
  touch_bar_ts: int | None
  confirmation_type: str | None
  confluence: int
  htf_bias: str
  atr: float | None
  entry_low: float
  entry_high: float
  structural_low: float | None
  structural_high: float | None
  structural_id: str | None
  bias_relationship: str | None
  reasons: tuple[str, ...]

  def as_dict(self) -> dict[str, Any]:
    d = asdict(self)
    d["reasons"] = list(self.reasons)
    return d

  @classmethod
  def from_dict(cls, d: dict[str, Any]) -> "PythonObservation":
    return cls(**{**d, "reasons": tuple(d.get("reasons") or ())})


def _epoch(value: Any) -> int | None:
  if value is None or value == "":
    return None
  try:
    return int(float(value))
  except (TypeError, ValueError):
    return int(pd.Timestamp(value).timestamp())


def python_observation_from_result(result: Any, ctx: Any, close_at: int) -> PythonObservation:
  scope = "supply" if result.direction == "SELL" else "demand"
  atr_series = ctx.indicators[ctx.tf].atr
  atr = float(atr_series.iloc[-1]) if atr_series is not None and len(atr_series) and math.isfinite(float(atr_series.iloc[-1])) else None
  zone = result.entry_zone
  confirmation_at = _epoch(result.confirmation_bar_ts)
  ident = hashlib.sha256(f"{result.direction}|{result.structural_id}|{confirmation_at}".encode()).hexdigest()[:16]
  return PythonObservation(
    id=f"py_{ident}", scope=scope, direction=result.direction, detected_at_close=int(close_at),
    confirmation_bar_ts=confirmation_at, touch_bar_ts=_epoch(result.touch_bar_ts),
    confirmation_type=result.confirmation_type or result.confirmation, confluence=int(result.confluence),
    htf_bias=str(ctx.htf_bias), atr=atr, entry_low=float(zone.low), entry_high=float(zone.high),
    structural_low=None if result.structural_low is None else float(result.structural_low),
    structural_high=None if result.structural_high is None else float(result.structural_high),
    structural_id=result.structural_id, bias_relationship=result.bias_relationship, reasons=tuple(result.reasons or ()),
  )


@dataclass(frozen=True)
class PythonReplay:
  observations: list[PythonObservation]
  evaluated: int
  warmup_skipped: int


def replay_python(
  capture: Capture, *, first_close_at: int | None = None, last_close_at: int | None = None,
  stride: int = 1, progress: Callable[[int, int], None] | None = None,
) -> PythonReplay:
  """Run the live "Supply Demand" technique detector on the frames visible at each M5 close.

  This is the detector the scanner actually runs for the reviewed scopes
  (``DEFAULT_DETECTORS``; the older ``supply_zone_reaction`` / ``demand_zone_reaction``
  fallbacks are disabled by ``zone_reaction_fallback_enabled`` and would report
  nothing). Heavy (a full context build per bar) and needs the production analysis
  stack, so imports are local. ``stride`` > 1 is for smoke runs only; the report
  records it.
  """
  from app.analysis.detectors import build_context
  from app.analysis.ohlc_source import window_for_timeframe
  from app.analysis.technique_detectors import supply_demand_technique_reaction
  from app.analysis.scanner import _detector_settings, _htf_tfs

  symbol = capture.symbol
  settings = _detector_settings(symbol)
  htf_order = _htf_tfs()
  wanted = ["M5", *[tf.upper() for tf in htf_order if tf.upper() in capture.frames]]
  lookbacks = {tf: window_for_timeframe(tf) for tf in wanted}
  m5 = capture.frames["M5"]
  closes = [int(ts.timestamp()) + 300 for ts in m5.index]
  indices = [i for i, c in enumerate(closes) if (first_close_at is None or c >= first_close_at) and (last_close_at is None or c <= last_close_at)]
  observations: dict[str, PythonObservation] = {}
  evaluated = warmup = 0
  for n, i in enumerate(indices[::max(1, stride)]):
    close_at = closes[i]
    frames = {tf: df for tf, df in frames_at(capture, close_at, lookbacks=lookbacks).items() if tf in wanted}
    ctx = build_context(symbol, "M5", frames, settings, htf_order, causal_structure=False)
    atr = ctx.indicators["M5"].atr
    if len(frames["M5"]) < lookbacks["M5"] or atr is None or atr.dropna().empty:
      # Fewer bars than a live cycle always has (the configured M5 window): a
      # live cycle never evaluates here, so neither does the replay.
      warmup += 1
      if progress:
        progress(n + 1, len(indices[::max(1, stride)]))
      continue
    evaluated += 1
    result = supply_demand_technique_reaction(ctx)
    if result is not None:
      obs = python_observation_from_result(result, ctx, close_at)
      observations.setdefault(obs.id, obs)      # first bar that showed the reaction wins
    if progress:
      progress(n + 1, len(indices[::max(1, stride)]))
  ordered = sorted(observations.values(), key=lambda o: (o.confirmation_bar_ts or 0, o.id))
  return PythonReplay(ordered, evaluated, warmup)


def write_python_observations(path: str | Path, observations: Iterable[PythonObservation]) -> None:
  Path(path).write_text("".join(json.dumps(o.as_dict(), sort_keys=True, separators=(",", ":")) + "\n" for o in observations))


def read_python_observations(path: str | Path) -> list[PythonObservation]:
  return [PythonObservation.from_dict(json.loads(line)) for line in Path(path).read_text().splitlines() if line.strip()]


# ---- ATR / geometry / policy model (what the live pipeline would do with each input) ----------

def _pip_and_digits(symbol: str) -> tuple[float, int]:
  from app.autotrade import units
  from app.core import instrument_geometry
  return float(units.pip_size(symbol)), int(instrument_geometry.price_digits(symbol))


def atr_reference(capture: Capture, reference_time: int, length: int = 14) -> dict[str, Any]:
  """Independent ATR at the reference M5 bar: the configured ``simple`` (rolling
  mean of true range, ``math_utils.atr_series``) and Python's Wilder/RMA
  (``indicators.atr``), both from the capture, using only bars closed by then."""
  from app.analysis.indicators import atr as wilder_atr
  from app.analysis.math_utils import atr_series

  m5 = capture.frames["M5"]
  visible = m5[m5.index <= pd.Timestamp(reference_time, unit="s", tz="UTC")]
  simple = float(atr_series(visible, length).iloc[-1]) if len(visible) else None
  wilder_series = wilder_atr(visible, length) if len(visible) > length else None
  wilder = None if wilder_series is None or wilder_series.dropna().empty else float(wilder_series.dropna().iloc[-1])
  return {"simple": simple, "wilder": wilder}


def go_model(case: GoCase, capture: Capture) -> dict[str, Any]:
  """ATR provenance, geometry conversion and the policy verdict for one Go case."""
  from app.core.config import runtime_config

  payload = case.event.payload
  tech = payload.technical_context
  symbol = payload.symbol.upper()
  pip, digits = _pip_and_digits(symbol)
  tick = 10 ** -digits
  direction = payload.direction
  proximal = payload.entry.low if direction == "SELL" else payload.entry.high
  stop = float(payload.invalidation.price)
  stop_pips = abs(proximal - stop) / pip
  ref = atr_reference(capture, int(tech.reference_time))
  reported = float(tech.atr)
  targets = []
  for target in payload.targets:
    price = float(target.price.price)
    distance = (proximal - price) if direction == "SELL" else (price - proximal)
    pips = round(distance / pip)
    back = proximal - pips * pip if direction == "SELL" else proximal + pips * pip
    targets.append({"price": price, "distance_pips": round(distance / pip, 4), "pips_rounded": int(pips), "price_after_rounding": round(back, 6), "rounding_error_price": round(back - price, 6)})
  def aligned(value: float) -> bool:
    return abs(value / tick - round(value / tick)) < 1e-6
  model: dict[str, Any] = {
    "atr": {
      "reported": reported, "simple_recomputed": ref["simple"], "wilder_recomputed": ref["wilder"],
      "reported_matches_simple": ref["simple"] is not None and abs(reported - ref["simple"]) <= 1e-9 * max(1.0, abs(reported)),
      "wilder_vs_reported_relative": None if not ref["wilder"] else round((ref["wilder"] - reported) / reported, 6),
    },
    "geometry": {
      "pip": pip, "tick": tick, "digits": digits, "entry_low": float(payload.entry.low), "entry_high": float(payload.entry.high),
      "proximal_edge": proximal, "stop_price": stop, "stop_pips": round(stop_pips, 4), "targets": targets,
      "rr_first_target": None if not targets or stop_pips <= 0 else round(targets[0]["distance_pips"] / stop_pips, 4),
      "prices_tick_aligned": all(aligned(v) for v in (float(payload.entry.low), float(payload.entry.high), stop, *[t["price"] for t in targets])),
      "max_target_rounding_error_price": max((abs(t["rounding_error_price"]) for t in targets), default=0.0),
    },
    "htf": {"timeframes_present": sorted(h.timeframe for h in tech.higher_timeframes), "timeframe_used": None if case.match is None else next((t.split("go_")[-1] for t in case.match.tags if t.startswith("htf_bias_source:")), None),
            "directions": {h.timeframe: h.direction for h in tech.higher_timeframes}},
  }
  model["policy"] = {
    "adapter_rejection": case.rejection,
    **({} if case.match is None else _policy_verdict(case.match.confluence, case.match.tier, int(case.confirmation_at) + 300, symbol, runtime_config)),
  }
  return model


def _policy_verdict(confluence: int, tier: str, close_at: int, symbol: str, runtime_config: Any) -> dict[str, Any]:
  """Tier, risk multiplier and the confluence-dependent eligibility gates the live worker applies."""
  from app.autotrade import killzone
  from app.autotrade.execution_policy import risk_multiplier_for_tier
  from app.core import instrument_geometry

  inst = instrument_geometry.instrument_runtime(symbol)
  quality = killzone.evaluate_instrument_session_quality(ts=close_at, cfg=inst)
  session_min = killzone.session_quality_minimum_confluence(inst, quality)
  global_min = max(1, int(runtime_config.actionability.gates.min_confluence))
  return {
    "confluence": confluence, "tier": tier, "risk_multiplier": risk_multiplier_for_tier(tier),
    "global_min_confluence": global_min, "session_label": quality.label, "session_min_confluence": session_min,
    "passes_global_min": confluence >= global_min, "passes_session_min": confluence >= session_min,
  }


def python_model(obs: PythonObservation, symbol: str) -> dict[str, Any]:
  from app.core.config import runtime_config
  tier = classify_tier(confluence=obs.confluence, strategy="Supply Demand")
  return _policy_verdict(obs.confluence, tier, int(obs.detected_at_close), symbol, runtime_config)


# ---- comparison ------------------------------------------------------------------------

RELAXED_WINDOW_SECONDS = 900


def _coverage(python: list[PythonObservation], go_cases: list[GoCase], window: int) -> dict[str, Any]:
  """Many-to-many view: the strict 1:1 match punishes an engine that re-confirms the
  same zone on consecutive bars. Here a case counts as covered when the other engine
  confirmed an overlapping zone of the same side within ``window`` seconds."""
  def near(scope: str, at: int, low: float, high: float, others: Iterable[tuple[str, int | None, float, float]]) -> bool:
    return any(o_scope == scope and o_at is not None and abs(o_at - at) <= window and _iou(low, high, o_low, o_high) > 0 for o_scope, o_at, o_low, o_high in others)
  py_rows = [(o.scope, o.confirmation_bar_ts, o.entry_low, o.entry_high) for o in python]
  go_rows = []
  for c in go_cases:
    v = _go_view(c)
    go_rows.append((c.scope, c.confirmation_at, v["entry_low"], v["entry_high"]))
  go_covered = sum(1 for row in go_rows if near(row[0], row[1], row[2], row[3], py_rows))
  py_covered = sum(1 for row in py_rows if near(row[0], row[1], row[2], row[3], go_rows))
  return {
    "window_seconds": window,
    "go_cases_with_python_confirmation_nearby": _rate(go_covered, len(go_rows)),
    "python_observations_with_go_confirmation_nearby": _rate(py_covered, len(py_rows)),
  }


def _iou(a_low: float, a_high: float, b_low: float, b_high: float) -> float:
  inter = max(0.0, min(a_high, b_high) - max(a_low, b_low))
  union = max(a_high, b_high) - min(a_low, b_low)
  return inter / union if union > 0 else 0.0


def _go_view(case: GoCase) -> dict[str, Any]:
  p = case.event.payload
  tech = p.technical_context
  higher = {h.timeframe: h.direction for h in tech.higher_timeframes}
  return {
    "id": case.id, "scope": case.scope, "direction": p.direction, "created_at": p.created_at,
    "confirmation_at": case.confirmation_at, "touch_at": tech.confirmation.touch_bar_time,
    "reaction_type": tech.confirmation.reaction_type, "atr": float(tech.atr),
    "entry_low": float(p.entry.low), "entry_high": float(p.entry.high), "invalidation": float(p.invalidation.price),
    "evidence": [e.code for e in p.evidence], "quality": float(p.quality.overall),
    "higher_timeframes": higher, "adapter_rejection": case.rejection,
  }


def _distal(direction: str, low: float, high: float) -> float:
  return high if direction == "SELL" else low


def _htf_relation(python_htf: str, go: dict[str, Any], match: Any | None) -> str:
  if match is None or not match.htf_bias:
    return "go_unavailable"
  if python_htf in ("", "unknown", "neutral", "none", "range"):
    return "python_unavailable"
  return "agree" if python_htf == match.htf_bias else "conflict"


def _opposing(capture: Capture, direction: str, entry_reference: float, atr: float | None, low: float, high: float, close_at: int) -> dict[str, Any]:
  """The worker's own Python barrier check on the frames visible at ``close_at``."""
  from app.autotrade import worker
  from app.core import instrument_geometry  # same module the worker reads its buffer from

  frames = frames_at(capture, close_at)
  zones = worker._htf_zones(frames, None, symbol=capture.symbol)
  levels = worker._htf_levels(frames, None, symbol=capture.symbol)
  reason = worker._opposing_barrier_reason(
    direction, float(entry_reference), atr, zones, levels,
    float(instrument_geometry.structural_barrier_buffer_atr(capture.symbol)), exclude_low=low, exclude_high=high,
  )
  return {"vetoed": reason is not None, "reason": reason}


def compare(
  python: list[PythonObservation], go_cases: list[GoCase], capture: Capture, *,
  tolerance_seconds: int = 300, opposing: bool = True, models: bool = True, skipped: Counter | None = None,
  python_source: str = "unspecified", stride: int = 1, go_file_sha256: str = "", python_window: tuple[int, int] | None = None,
) -> dict[str, Any]:
  used: set[str] = set()
  pairs: list[dict[str, Any]] = []
  go_only: list[dict[str, Any]] = []
  by_scope: dict[str, list[PythonObservation]] = {s: [o for o in python if o.scope == s] for s in SCOPES}
  window = python_window
  for case in go_cases:
    go = _go_view(case)
    best = None
    for obs in by_scope.get(case.scope, []):
      if obs.id in used or obs.confirmation_bar_ts is None:
        continue
      delta = abs(obs.confirmation_bar_ts - case.confirmation_at)
      overlap = _iou(obs.entry_low, obs.entry_high, go["entry_low"], go["entry_high"])
      if delta <= tolerance_seconds and overlap > 0:
        key = (delta, -overlap)
        if best is None or key < best[0]:
          best = (key, obs, overlap)
    if best is None:
      in_window = window is None or (window[0] <= case.confirmation_at + 300 <= window[1])
      go_only.append({**go, "python_window_covers_it": in_window})
      continue
    _, obs, overlap = best
    used.add(obs.id)
    pairs.append(_pair(capture, obs, go, case.match, overlap, opposing, case, models))
  python_only = [
    o.as_dict() for o in python if o.id not in used
  ]
  matched_ids = {p["go_id"] for p in pairs}
  go_models = [
    {"id": c.id, "scope": c.scope, "confirmation_at": c.confirmation_at, "touch_at": c.event.payload.technical_context.confirmation.touch_bar_time,
     "matched": c.id in matched_ids, "model": go_model(c, capture)}
    for c in go_cases
  ] if models else []
  report = _report(pairs, python_only, go_only, python, go_cases, capture, tolerance_seconds, skipped or Counter(), python_source, stride, go_file_sha256, window)
  report["go_cases"] = go_models
  report["coverage"] = _coverage(python, go_cases, RELAXED_WINDOW_SECONDS)
  if models:
    report["findings"] = _findings(pairs, python_only, go_only, go_models, python)
  return report


def _pair(capture: Capture, obs: PythonObservation, go: dict[str, Any], match: Any | None, overlap: float, opposing: bool, case: GoCase | None = None, models: bool = True) -> dict[str, Any]:
  atr_go, atr_py = go["atr"], obs.atr
  atr_ref = atr_py or atr_go
  dist_py = _distal(obs.direction, obs.entry_low, obs.entry_high)
  distal_go = go["invalidation"]
  fields: dict[str, Any] = {
    "confirmation_time": {"python": obs.confirmation_bar_ts, "go": go["confirmation_at"], "delta_seconds": None if obs.confirmation_bar_ts is None else obs.confirmation_bar_ts - go["confirmation_at"]},
    "confirmation_kind": {"python": obs.confirmation_type, "go": go["reaction_type"], "same_family": obs.confirmation_type in PYTHON_REJECTION_KINDS and go["reaction_type"] == "rejection"},
    "entry_zone": {"python": [obs.entry_low, obs.entry_high], "go": [go["entry_low"], go["entry_high"]], "iou": round(overlap, 4),
                   "low_delta": round(go["entry_low"] - obs.entry_low, 4), "high_delta": round(go["entry_high"] - obs.entry_high, 4),
                   "delta_atr": None if not atr_ref else round(max(abs(go["entry_low"] - obs.entry_low), abs(go["entry_high"] - obs.entry_high)) / atr_ref, 4)},
    # Informational: Python's executed stop is planned later (protective stop + buffer);
    # this compares Go's invalidation with the *unbuffered* Python zone edge.
    "invalidation": {"python_distal_edge": dist_py, "go": distal_go, "delta": round(distal_go - dist_py, 4), "delta_atr": None if not atr_ref else round(abs(distal_go - dist_py) / atr_ref, 4)},
    "atr": {"python": atr_py, "go": atr_go, "relative_delta": None if not atr_py else round((atr_go - atr_py) / atr_py, 6)},
    "htf_bias": {"python": obs.htf_bias, "go": None if match is None else match.htf_bias, "go_timeframes": go["higher_timeframes"], "relation": _htf_relation(obs.htf_bias, go, match)},
  }
  if match is not None:
    py_tier = classify_tier(confluence=obs.confluence, strategy="Supply Demand")
    fields["confluence"] = {"python": obs.confluence, "go_evidence_count": match.confluence, "python_tier": py_tier, "go_tier": match.tier,
                            "tier_agrees": py_tier == match.tier, "go_evidence": go["evidence"]}
  else:
    fields["confluence"] = {"python": obs.confluence, "go_evidence_count": len(go["evidence"]), "python_tier": classify_tier(confluence=obs.confluence, strategy="Supply Demand"), "go_tier": None, "tier_agrees": None, "go_evidence": go["evidence"]}
  if opposing:
    close_at = go["confirmation_at"] + 300
    py_ref = (obs.entry_low + obs.entry_high) / 2
    go_ref = (go["entry_low"] + go["entry_high"]) / 2
    py = _opposing(capture, obs.direction, py_ref, atr_py, obs.entry_low, obs.entry_high, close_at)
    go_v = _opposing(capture, obs.direction, go_ref, atr_go, go["entry_low"], go["entry_high"], close_at)
    fields["opposing_structure"] = {"python": py, "go": go_v, "agree": py["vetoed"] == go_v["vetoed"]}
  pair = {"python_id": obs.id, "go_id": go["id"], "scope": obs.scope, "adapter_rejection": go["adapter_rejection"], "fields": fields}
  if case is not None and models:
    fields["touch_time"] = {"python": obs.touch_bar_ts, "go": go["touch_at"], "delta_seconds": None if obs.touch_bar_ts is None else obs.touch_bar_ts - go["touch_at"]}
    pair["model"] = {"python": python_model(obs, capture.symbol), "go": go_model(case, capture)}
  return pair


def _dist(values: list[float]) -> dict[str, Any]:
  values = sorted(v for v in values if v is not None)
  if not values:
    return {"n": 0}
  mid = values[len(values) // 2]
  return {"n": len(values), "min": round(values[0], 6), "median": round(mid, 6), "max": round(values[-1], 6)}


def _findings(pairs, python_only, go_only, go_models, python) -> dict[str, Any]:
  """The five S14C questions, answered from the numbers above (never asserted)."""
  models = [g["model"] for g in go_models]
  deltas = [p["fields"]["confirmation_time"]["delta_seconds"] for p in pairs if p["fields"]["confirmation_time"]["delta_seconds"] is not None]
  lags = [(g["confirmation_at"] - _touch(g)) / 300.0 for g in go_models if _touch(g) is not None]
  py_models = [p["model"]["python"] for p in pairs if "model" in p]
  go_paired = [p["model"]["go"]["policy"] for p in pairs if "model" in p and "confluence" in p["model"]["go"]["policy"]]
  go_all = [m["policy"] for m in models if "confluence" in m["policy"]]
  return {
    "confirmation": {
      "missed_by_go": len(python_only), "extra_in_go": len(go_only),
      "matched_time_delta_seconds": {str(k): v for k, v in sorted(Counter(deltas).items())},
      "go_touch_to_confirmation_bars": {str(k): v for k, v in sorted(Counter(round(v) for v in lags).items())},
      "python_kinds_matched": dict(Counter(p["fields"]["confirmation_kind"]["python"] for p in pairs)),
      "python_kinds_unmatched": dict(Counter(o["confirmation_type"] for o in python_only)),
    },
    "confluence": {
      "go_confluence_values": {str(k): v for k, v in sorted(Counter(g["confluence"] for g in go_all).items())},
      "go_tiers": dict(Counter(g["tier"] for g in go_all)),
      "matched_python_confluence_values": {str(k): v for k, v in sorted(Counter(m["confluence"] for m in py_models).items())},
      "matched_python_tiers": dict(Counter(m["tier"] for m in py_models)),
      "risk_multiplier_go": dict(Counter(str(g["risk_multiplier"]) for g in go_all)),
      "risk_multiplier_python_matched": dict(Counter(str(m["risk_multiplier"]) for m in py_models)),
      "go_passes_session_min": _rate(sum(1 for g in go_all if g["passes_session_min"]), len(go_all)),
      "python_matched_passes_session_min": _rate(sum(1 for m in py_models if m["passes_session_min"]), len(py_models)),
      "go_passes_where_python_would_not_matched": sum(1 for p in pairs if "model" in p and "confluence" in p["model"]["go"]["policy"]
                                                       and p["model"]["go"]["policy"]["passes_session_min"] and not p["model"]["python"]["passes_session_min"]),
    },
    "htf": {
      "timeframe_used": dict(Counter(m["htf"]["timeframe_used"] or "none" for m in models)),
      "timeframes_present": dict(Counter(",".join(m["htf"]["timeframes_present"]) or "none" for m in models)),
      "go_h1_h4_disagreements": sum(1 for m in models if len(set(m["htf"]["directions"].values())) > 1),
    },
    "atr": {
      "go_reported_equals_configured_simple": _rate(sum(1 for m in models if m["atr"]["reported_matches_simple"]), len(models)),
      "wilder_vs_reported_relative": _dist([m["atr"]["wilder_vs_reported_relative"] for m in models]),
    },
    "geometry": {
      "prices_tick_aligned": _rate(sum(1 for m in models if m["geometry"]["prices_tick_aligned"]), len(models)),
      "stop_pips": _dist([m["geometry"]["stop_pips"] for m in models]),
      "rr_first_target": _dist([m["geometry"]["rr_first_target"] for m in models]),
      "target_rounding_error_price": _dist([m["geometry"]["max_target_rounding_error_price"] for m in models]),
    },
  }


def _touch(go_model_row: dict[str, Any]) -> int | None:
  return go_model_row.get("touch_at")


def _rate(n: int, d: int) -> dict[str, Any]:
  return {"agree": n, "total": d, "rate": None if d == 0 else round(n / d, 4)}


def _report(pairs, python_only, go_only, python, go_cases, capture, tolerance, skipped, python_source, stride, go_sha, window) -> dict[str, Any]:
  def col(name):
    return [p["fields"][name] for p in pairs if name in p["fields"]]
  conf_time = col("confirmation_time")
  kinds = col("confirmation_kind")
  zones = col("entry_zone")
  inval = col("invalidation")
  atrs = [a for a in col("atr") if a["relative_delta"] is not None]
  htf = col("htf_bias")
  conf = [c for c in col("confluence") if c["tier_agrees"] is not None]
  opp = col("opposing_structure")
  agreement = {
    "confirmation_time_exact": _rate(sum(1 for c in conf_time if c["delta_seconds"] == 0), len(conf_time)),
    "confirmation_kind_same_family": _rate(sum(1 for k in kinds if k["same_family"]), len(kinds)),
    "entry_zone_iou_ge_0_5": _rate(sum(1 for z in zones if z["iou"] >= 0.5), len(zones)),
    "invalidation_vs_unbuffered_python_edge_within_0_5_atr": _rate(sum(1 for i in inval if i["delta_atr"] is not None and i["delta_atr"] <= 0.5), len(inval)),
    "atr_within_1pct": _rate(sum(1 for a in atrs if abs(a["relative_delta"]) <= ATR_TOLERANCE_REL), len(atrs)),
    "htf_bias_agree": _rate(sum(1 for h in htf if h["relation"] == "agree"), sum(1 for h in htf if h["relation"] in ("agree", "conflict"))),
    "tier_agrees": _rate(sum(1 for c in conf if c["tier_agrees"]), len(conf)),
    "opposing_structure_agree": _rate(sum(1 for o in opp if o["agree"]), len(opp)),
  }
  rejections = Counter(c.rejection for c in go_cases if c.rejection)
  htf_states = Counter(h["relation"] for h in htf)
  go_total = len(go_cases)
  gates = {
    "python_replay_is_full_stride": stride == 1,
    "every_go_case_adaptable": not rejections,
    "no_python_only": not python_only,
    "no_go_only": not go_only,
    "all_matched_fields_in_tolerance": all((v["total"] == 0 or v["agree"] == v["total"]) for v in agreement.values()),
    "no_htf_unavailability_or_conflict": not (htf_states.get("go_unavailable") or htf_states.get("python_unavailable") or htf_states.get("conflict")),
  }
  return {
    "version": REPORT_VERSION, "kind": "go_vs_python_policy_replay",
    "inputs": {
      "capture": {"path": capture.path, "sha256": capture.sha256, "provenance": capture.provenance,
                  "bars": {tf: len(df) for tf, df in sorted(capture.frames.items())}, "h4_derived_from_h1_utc_aligned": capture.h4_derived},
      "go": {"file_sha256": go_sha, "cases": go_total, "left_out": dict(skipped)},
      "python": {"source": python_source, "observations": len(python), "stride": stride, "window_close_at": list(window) if window else None},
    },
    "config": {"tolerance_seconds": tolerance, "scopes": list(SCOPES), "atr_relative_tolerance": ATR_TOLERANCE_REL},
    "go_adaptation": {"cases": go_total, "adaptable": go_total - sum(rejections.values()), "rejections": dict(rejections)},
    "matching": {"matched": len(pairs), "python_only": len(python_only), "go_only": len(go_only)},
    "agreement": agreement,
    "htf_relations": dict(htf_states),
    "confirmation_kinds": dict(Counter(k["python"] for k in kinds)),
    "confluence_mapping": {
      "python_values": dict(Counter(c["python"] for c in conf)), "go_evidence_counts": dict(Counter(c["go_evidence_count"] for c in conf)),
      "python_tiers": dict(Counter(c["python_tier"] for c in conf)), "go_tiers": dict(Counter(c["go_tier"] for c in conf)),
    },
    "gates": gates,
    "verdict": "pass" if all(gates.values()) else "unresolved_differences",
    "pairs": pairs, "python_only": python_only, "go_only": go_only,
    "limits": [
      "One real capture of one instrument over the captured window; not a performance or live-equivalence claim.",
      ("H4 was derived from H1 (UTC-aligned, complete buckets) although the production feed delivers none; the broker's own H4 boundaries are not verified." if capture.h4_derived
       else "No H4: the production feed delivers no H4 (config/trading-bot.yml), so Go's higher-timeframe context is H1 only and the adapter's H4 fallback is unreachable live."),
      "Python replay covers only the \"Supply Demand\" technique detector on frames visible at each M5 close (one best result per bar); scanner-level actionability gates and the market-map layer are not replayed.",
      "Opposing-structure verdicts use the worker's Python barrier function on identical frames for both sides; Go supplies no opposing-structure fact.",
      "The verdict is conservative: 'pass' needs every gate; anything else needs an owner disposition. This report approves nothing.",
    ],
  }


def _coverage_lines(report: dict[str, Any]) -> list[str]:
  cov = report.get("coverage")
  if not cov:
    return []
  g, p = cov["go_cases_with_python_confirmation_nearby"], cov["python_observations_with_go_confirmation_nearby"]
  return [
    f"- Many-to-many coverage within ±{cov['window_seconds']} s (an engine re-confirming the same zone on consecutive bars is not penalised): "
    f"Go cases with an overlapping Python confirmation nearby {g['agree']}/{g['total']}; Python observations with an overlapping Go confirmation nearby {p['agree']}/{p['total']}.",
  ]


def render_markdown(report: dict[str, Any]) -> str:
  i = report["inputs"]
  m = report["matching"]
  lines = [
    "# Go vs Python policy replay: XAU M5 supply / demand",
    "",
    f"**Verdict: `{report['verdict']}`** (conservative; this report approves nothing).",
    "",
    "## Inputs",
    f"- Capture `{Path(i['capture']['path']).name}` sha256 `{i['capture']['sha256'][:16]}…`, bars {i['capture']['bars']}, H4 derived from H1: {i['capture']['h4_derived_from_h1_utc_aligned']}.",
    f"- Go: {i['go']['cases']} confirmed reviewed-scope cases (left out: {i['go']['left_out'] or 'none'}).",
    f"- Python: {i['python']['observations']} observations from `{i['python']['source']}`, stride {i['python']['stride']}.",
    f"- Match tolerance: {report['config']['tolerance_seconds']} s on the confirmation bar plus positive zone overlap.",
    "",
    "## Result",
    f"- Matched **{m['matched']}**, Python only **{m['python_only']}**, Go only **{m['go_only']}**.",
    f"- Go adaptation: {report['go_adaptation']['adaptable']}/{report['go_adaptation']['cases']} adaptable; rejections {report['go_adaptation']['rejections'] or 'none'}.",
    *_coverage_lines(report),
    "",
    "### Field agreement on matched setups",
    "| policy input | agree | total | rate |",
    "| --- | ---: | ---: | ---: |",
  ]
  for name, v in report["agreement"].items():
    rate = "n/a" if v["rate"] is None else f"{v['rate']:.1%}"
    lines.append(f"| {name} | {v['agree']} | {v['total']} | {rate} |")
  cm = report["confluence_mapping"]
  lines += [
    "",
    f"- Higher-timeframe relations: {report['htf_relations'] or 'n/a'}.",
    f"- Python confirmation kinds: {report['confirmation_kinds'] or 'n/a'}.",
    f"- Confluence: Python values {cm['python_values']} vs Go evidence counts {cm['go_evidence_counts']}; tiers Python {cm['python_tiers']} vs Go {cm['go_tiers']}.",
    "",
    "### Gates",
  ]
  lines += [f"- {'PASS' if ok else 'OPEN'}: `{name}`" for name, ok in report["gates"].items()]
  f = report.get("findings")
  if f:
    c, k, h, a, g = f["confirmation"], f["confluence"], f["htf"], f["atr"], f["geometry"]
    lines += [
      "", "## Findings on the five S14C questions", "",
      "### 1. Confirmation",
      f"- Missed by Go (Python-only): **{c['missed_by_go']}**; extra in Go (Go-only): **{c['extra_in_go']}**.",
      f"- Matched confirmation-time delta (seconds → count): {c['matched_time_delta_seconds'] or 'n/a'}.",
      f"- Go touch→confirmation gap (bars → count): {c['go_touch_to_confirmation_bars'] or 'n/a'}.",
      f"- Python kinds, matched {c['python_kinds_matched'] or 'n/a'}; unmatched {c['python_kinds_unmatched'] or 'n/a'}.",
      "", "### 2. Confluence, tier, risk multiplier, eligibility",
      f"- Go confluence (evidence count) {k['go_confluence_values']} → tiers {k['go_tiers']}; matched Python confluence {k['matched_python_confluence_values']} → tiers {k['matched_python_tiers']}.",
      f"- Risk multiplier: Go {k['risk_multiplier_go']} vs matched Python {k['risk_multiplier_python_matched']} (reaction sizing ignores tier).",
      f"- Session-floor eligibility: Go {k['go_passes_session_min']['agree']}/{k['go_passes_session_min']['total']} pass; matched Python {k['python_matched_passes_session_min']['agree']}/{k['python_matched_passes_session_min']['total']} pass; matched setups Go passes but Python would not: **{k['go_passes_where_python_would_not_matched']}**.",
      "", "### 3. Higher-timeframe bias",
      f"- Timeframe used by the adapter: {h['timeframe_used']}; present in Go events: {h['timeframes_present']}; Go H1-vs-H4 disagreements preserved: {h['go_h1_h4_disagreements']}.",
      "", "### 4. ATR and geometry",
      f"- Go-reported ATR equals the configured `simple` formula recomputed from the capture: {a['go_reported_equals_configured_simple']['agree']}/{a['go_reported_equals_configured_simple']['total']}.",
      f"- Python's Wilder ATR relative to Go's reported: {a['wilder_vs_reported_relative']}.",
      f"- Prices on the instrument tick: {g['prices_tick_aligned']['agree']}/{g['prices_tick_aligned']['total']}; stop pips {g['stop_pips']}; R:R to first target {g['rr_first_target']}; whole-pip target rounding error (price) {g['target_rounding_error_price']}.",
      "", "### 5. Opposing structure",
      "- Go carries no opposing-structure fact. The worker's final barrier / target-room checks still recompute Python zones and levels from M15 OHLC (`worker._htf_zones`, `_htf_levels`, `_opposing_barrier_reason`, `structural_target_room`); they are retained. Per-pair verdicts above use that check on identical frames.",
    ]
  if report["go_only"]:
    outside = sum(1 for g in report["go_only"] if not g["python_window_covers_it"])
    lines += ["", f"### Go-only ({m['go_only']}; {outside} fall outside the Python replay window)"]
    lines += [f"- `{g['id'][:18]}` {g['scope']} at {g['confirmation_at']} entry [{g['entry_low']}, {g['entry_high']}] htf {g['higher_timeframes'] or '-'}" for g in report["go_only"][:25]]
    if len(report["go_only"]) > 25:
      lines.append(f"- … {len(report['go_only']) - 25} more in the JSON report")
  if report["python_only"]:
    lines += ["", f"### Python-only ({m['python_only']})"]
    lines += [f"- `{p['id']}` {p['scope']} at {p['confirmation_bar_ts']} entry [{p['entry_low']}, {p['entry_high']}] kind {p['confirmation_type']}" for p in report["python_only"][:25]]
  lines += ["", "## Limits"] + [f"- {item}" for item in report["limits"]]
  return "\n".join(lines) + "\n"
