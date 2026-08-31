"""Live scalp outcome instrumentation — measurement only, no trading gates.

Tracks MFE/MAE, classifies exit path, and computes volume-weighted realized R
so the ledger and the price-excursion book can be reconciled.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
import math
from typing import Any

from app.scalping.models import LIVE_OUTCOME_VERSION, ScalpLiveOutcome, ScalpOpportunity
from app.scalping.telemetry import incr


log = logging.getLogger(__name__)

OUTCOME_TTL_SECONDS = 30 * 24 * 3600
EXCURSION_TTL_SECONDS = 7 * 24 * 3600
TRACE_TTL_SECONDS = 7 * 24 * 3600

EXIT_FULL_STOP = "full_stop"
EXIT_TP1_ONLY = "tp1_only"
EXIT_TP1_BE_FLAT = "tp1_be_flat"
EXIT_TP1_BE_TP2 = "tp1_be_tp2"
EXIT_MANUAL_CLOSE = "manual_close"
EXIT_UNKNOWN = "unknown"

# Default 50/50 (1R, 2R) ladder used when leg ratios are not supplied.
_DEFAULT_LADDER_RATIOS = (0.5, 0.5)
_DEFAULT_LADDER_R = (1.0, 2.0)


def resolve_risk_denominator(
  *,
  fill_price: float | None,
  invalidation_price: float,
  pip_size: float,
  expected_stop_pips: float,
) -> tuple[float, str, float | None]:
  """Return ``(risk_unit_pips, source, planned_vs_realized_ratio)``.

  Prefer fill-to-invalidation distance. Fall back to planned stop only when
  the weighted fill is unavailable.
  """
  planned = max(1e-9, float(expected_stop_pips))
  try:
    fill = None if fill_price is None else float(fill_price)
  except (TypeError, ValueError):
    fill = None
  pip = float(pip_size)
  if (
    fill is None
    or not math.isfinite(fill)
    or pip <= 0
    or not math.isfinite(pip)
  ):
    return planned, "planned", None
  realized = abs(fill - float(invalidation_price)) / pip
  if not math.isfinite(realized) or realized <= 0:
    return planned, "planned", None
  ratio = realized / planned if planned > 0 else None
  return realized, "realized", ratio


def outcome_key(symbol: str, opportunity_id: str) -> str:
  return f"scalp:outcome:{symbol.upper()}:{opportunity_id}"


def excursion_key(symbol: str, opportunity_id: str) -> str:
  return f"scalp:excursion:{symbol.upper()}:{opportunity_id}"


def exit_trace_key(group_id: str) -> str:
  return f"scalp:exit_trace:{_strip_v8(group_id)}"


def bind_key(match_id: str) -> str:
  return f"scalp:bind:{_strip_v8(match_id)}"


def opportunity_index_key(symbol: str, opportunity_id: str) -> str:
  return f"scalp:opportunity_index:{symbol.upper()}:{opportunity_id}"


def open_excursions_key(symbol: str) -> str:
  return f"scalp:open_excursions:{symbol.upper()}"


def _strip_v8(value: str | None) -> str:
  text = str(value or "").strip()
  if text.startswith("v8:"):
    return text[3:]
  return text


@dataclass
class ExitTrace:
  """Accumulated fill/close flags for one scalp group."""

  group_id: str
  filled: bool = False
  tp1: bool = False
  tp2: bool = False
  be_moved: bool = False
  stopped: bool = False
  manual: bool = False
  single_target: bool = False
  events: list[str] = field(default_factory=list)

  def to_json(self) -> str:
    return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

  @classmethod
  def from_json(cls, raw: str | bytes) -> ExitTrace:
    data = json.loads(raw)
    return cls(
      group_id=str(data.get("group_id") or ""),
      filled=bool(data.get("filled")),
      tp1=bool(data.get("tp1")),
      tp2=bool(data.get("tp2")),
      be_moved=bool(data.get("be_moved")),
      stopped=bool(data.get("stopped")),
      manual=bool(data.get("manual")),
      single_target=bool(data.get("single_target")),
      events=[str(item) for item in (data.get("events") or [])],
    )


@dataclass
class ExcursionState:
  """Running MFE/MAE for an open live scalp position."""

  opportunity_id: str
  episode_id: str
  symbol: str
  archetype: str
  direction: str
  session: str
  htf_bias: str
  regime: str
  entry_price: float
  invalidation_price: float
  stop_pips: float
  planned_target_pips: float
  planned_rr: float
  group_id: str
  match_id: str
  opened_at: int
  pip_size: float
  max_high: float
  min_low: float
  bars_held: int = 0
  legs_filled: int = 0
  ladder_ratios: tuple[float, ...] = _DEFAULT_LADDER_RATIOS
  ladder_r_multiples: tuple[float, ...] = _DEFAULT_LADDER_R
  expected_stop_pips: float = 0.0
  risk_denominator_source: str = "planned"
  planned_vs_realized_stop_ratio: float | None = None
  version: int = LIVE_OUTCOME_VERSION

  def to_json(self) -> str:
    payload = asdict(self)
    payload["ladder_ratios"] = list(self.ladder_ratios)
    payload["ladder_r_multiples"] = list(self.ladder_r_multiples)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)

  @classmethod
  def from_json(cls, raw: str | bytes) -> ExcursionState:
    data = json.loads(raw)
    ratios = tuple(float(x) for x in (data.get("ladder_ratios") or _DEFAULT_LADDER_RATIOS))
    multiples = tuple(
      float(x) for x in (data.get("ladder_r_multiples") or _DEFAULT_LADDER_R)
    )
    ratio = data.get("planned_vs_realized_stop_ratio")
    expected = data.get("expected_stop_pips")
    return cls(
      opportunity_id=str(data["opportunity_id"]),
      episode_id=str(data.get("episode_id") or ""),
      symbol=str(data["symbol"]).upper(),
      archetype=str(data.get("archetype") or ""),
      direction=str(data.get("direction") or "").upper(),
      session=str(data.get("session") or ""),
      htf_bias=str(data.get("htf_bias") or ""),
      regime=str(data.get("regime") or ""),
      entry_price=float(data["entry_price"]),
      invalidation_price=float(data["invalidation_price"]),
      stop_pips=float(data["stop_pips"]),
      planned_target_pips=float(data.get("planned_target_pips") or 0.0),
      planned_rr=float(data.get("planned_rr") or 0.0),
      group_id=str(data.get("group_id") or ""),
      match_id=str(data.get("match_id") or ""),
      opened_at=int(data.get("opened_at") or 0),
      pip_size=float(data.get("pip_size") or 0.1),
      max_high=float(data["max_high"]),
      min_low=float(data["min_low"]),
      bars_held=int(data.get("bars_held") or 0),
      legs_filled=int(data.get("legs_filled") or 0),
      ladder_ratios=ratios or _DEFAULT_LADDER_RATIOS,
      ladder_r_multiples=multiples or _DEFAULT_LADDER_R,
      expected_stop_pips=float(
        expected if expected is not None else data.get("stop_pips") or 0.0
      ),
      risk_denominator_source=str(data.get("risk_denominator_source") or "planned"),
      planned_vs_realized_stop_ratio=None if ratio is None else float(ratio),
      version=int(data.get("version") or 1),
    )


def volume_weighted_r(
  *,
  exit_path: str,
  stop_pips: float,
  leg_close_ratios: tuple[float, ...],
  leg_r_multiples: tuple[float, ...],
) -> float:
  """Realized R weighted by the volume actually closed at each leg.

  The scalp ladder books (1R, 2R) at 50/50 with the runner moved to breakeven
  after TP1 (publish.py), so a TP1-then-BE exit realizes +0.5R, not the
  +1R that price-excursion accounting reports.
  """
  del stop_pips  # R multiples are already stop-normalized.
  path = str(exit_path or EXIT_UNKNOWN)
  ratios = tuple(float(x) for x in leg_close_ratios)
  multiples = tuple(float(x) for x in leg_r_multiples)
  if not ratios and not multiples:
    ratios, multiples = _default_legs_for_path(path)
  if len(ratios) != len(multiples):
    raise ValueError(
      f"leg_close_ratios/leg_r_multiples length mismatch: "
      f"{len(ratios)} vs {len(multiples)}"
    )
  if not ratios:
    return 0.0
  return float(sum(r * m for r, m in zip(ratios, multiples)))


def _default_legs_for_path(
  exit_path: str,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
  if exit_path == EXIT_TP1_BE_TP2:
    return _DEFAULT_LADDER_RATIOS, _DEFAULT_LADDER_R
  if exit_path == EXIT_TP1_BE_FLAT:
    return _DEFAULT_LADDER_RATIOS, (1.0, 0.0)
  if exit_path == EXIT_FULL_STOP:
    return (1.0,), (-1.0,)
  if exit_path == EXIT_TP1_ONLY:
    # Half of a 50/50 book closed at 1R (no runner fill recorded).
    return (0.5,), (1.0,)
  return (), ()


def classify_exit_path(trace: ExitTrace | dict[str, Any]) -> str:
  """Derive exactly one exit path from the fill/close event sequence."""
  if isinstance(trace, dict):
    trace = ExitTrace(
      group_id=str(trace.get("group_id") or ""),
      filled=bool(trace.get("filled")),
      tp1=bool(trace.get("tp1")),
      tp2=bool(trace.get("tp2")),
      be_moved=bool(trace.get("be_moved")),
      stopped=bool(trace.get("stopped")),
      manual=bool(trace.get("manual")),
      single_target=bool(trace.get("single_target")),
      events=[str(x) for x in (trace.get("events") or [])],
    )
  if trace.manual and not trace.tp1 and not trace.stopped:
    return EXIT_MANUAL_CLOSE
  if trace.stopped and not trace.tp1:
    return EXIT_FULL_STOP
  if trace.tp1 and trace.be_moved and trace.tp2:
    return EXIT_TP1_BE_TP2
  if trace.tp1 and trace.be_moved and not trace.tp2:
    return EXIT_TP1_BE_FLAT
  if trace.tp1 and not trace.tp2 and not trace.be_moved:
    return EXIT_TP1_ONLY
  if trace.manual:
    return EXIT_MANUAL_CLOSE
  return EXIT_UNKNOWN


def legs_for_exit_path(
  exit_path: str,
  *,
  ladder_ratios: tuple[float, ...] = _DEFAULT_LADDER_RATIOS,
  ladder_r_multiples: tuple[float, ...] = _DEFAULT_LADDER_R,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
  """Map an exit path onto the published ladder ratios / R multiples."""
  ratios = tuple(float(x) for x in ladder_ratios) or _DEFAULT_LADDER_RATIOS
  multiples = tuple(float(x) for x in ladder_r_multiples) or _DEFAULT_LADDER_R
  if exit_path == EXIT_FULL_STOP:
    return (1.0,), (-1.0,)
  if exit_path == EXIT_TP1_BE_TP2:
    if len(ratios) >= 2 and len(multiples) >= 2:
      return ratios[:2], multiples[:2]
    return _DEFAULT_LADDER_RATIOS, _DEFAULT_LADDER_R
  if exit_path == EXIT_TP1_BE_FLAT:
    if len(ratios) >= 2 and len(multiples) >= 1:
      return ratios[:2], (multiples[0], 0.0)
    return _DEFAULT_LADDER_RATIOS, (1.0, 0.0)
  if exit_path == EXIT_TP1_ONLY:
    if len(ratios) == 1 and len(multiples) >= 1:
      return (ratios[0],), (multiples[0],)
    if len(ratios) >= 1 and len(multiples) >= 1:
      return (ratios[0],), (multiples[0],)
    return (0.5,), (1.0,)
  if exit_path == EXIT_MANUAL_CLOSE:
    return (1.0,), (0.0,)
  return (), ()


def excursion_mfe_mae(
  *,
  direction: str,
  entry_price: float,
  max_high: float,
  min_low: float,
  pip_size: float,
) -> tuple[float, float]:
  """Signed MFE/MAE in pips; favourable is positive for both directions."""
  pip = float(pip_size)
  if not math.isfinite(pip) or pip <= 0:
    return 0.0, 0.0
  entry = float(entry_price)
  high = float(max_high)
  low = float(min_low)
  if str(direction).upper() == "SELL":
    mfe = (entry - low) / pip
    mae = (high - entry) / pip
  else:
    mfe = (high - entry) / pip
    mae = (entry - low) / pip
  return float(mfe), float(mae)


def update_excursion_extremes(
  state: ExcursionState,
  *,
  bar_high: float,
  bar_low: float,
) -> ExcursionState:
  """Accrue M1 extremes; keeps running after TP1 / BE until full close."""
  high = float(bar_high)
  low = float(bar_low)
  return ExcursionState(
    opportunity_id=state.opportunity_id,
    episode_id=state.episode_id,
    symbol=state.symbol,
    archetype=state.archetype,
    direction=state.direction,
    session=state.session,
    htf_bias=state.htf_bias,
    regime=state.regime,
    entry_price=state.entry_price,
    invalidation_price=state.invalidation_price,
    stop_pips=state.stop_pips,
    planned_target_pips=state.planned_target_pips,
    planned_rr=state.planned_rr,
    group_id=state.group_id,
    match_id=state.match_id,
    opened_at=state.opened_at,
    pip_size=state.pip_size,
    max_high=max(state.max_high, high),
    min_low=min(state.min_low, low),
    bars_held=int(state.bars_held) + 1,
    legs_filled=state.legs_filled,
    ladder_ratios=state.ladder_ratios,
    ladder_r_multiples=state.ladder_r_multiples,
    expected_stop_pips=state.expected_stop_pips,
    risk_denominator_source=state.risk_denominator_source,
    planned_vs_realized_stop_ratio=state.planned_vs_realized_stop_ratio,
    version=state.version,
  )


def ladder_from_opportunity(
  opportunity: ScalpOpportunity,
  *,
  risk_unit_pips: float | None = None,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
  """Mirror publish._scalp_target_ladder close-ratio semantics."""
  try:
    from app.scalping.publish import _scalp_target_ladder

    _final, targets = _scalp_target_ladder(opportunity)
  except Exception:
    targets = ()
  stop = max(
    1e-9,
    float(risk_unit_pips)
    if risk_unit_pips is not None
    else float(opportunity.expected_stop_pips),
  )
  if not targets:
    return _DEFAULT_LADDER_RATIOS, _DEFAULT_LADDER_R
  if len(targets) == 1:
    return (1.0,), (float(targets[0]) / stop,)
  ratios = tuple(1.0 / len(targets) for _ in targets)
  multiples = tuple(float(t) / stop for t in targets)
  return ratios, multiples


def excursion_from_opportunity(
  opportunity: ScalpOpportunity,
  *,
  entry_price: float,
  group_id: str,
  match_id: str,
  opened_at: int,
  pip_size: float,
  session: str = "",
  htf_bias: str = "",
  regime: str = "",
) -> ExcursionState:
  expected_stop = float(opportunity.expected_stop_pips)
  risk_unit, source, ratio = resolve_risk_denominator(
    fill_price=entry_price,
    invalidation_price=float(opportunity.invalidation_price),
    pip_size=pip_size,
    expected_stop_pips=expected_stop,
  )
  ratios, multiples = ladder_from_opportunity(
    opportunity, risk_unit_pips=risk_unit,
  )
  entry = float(entry_price)
  target = float(opportunity.expected_target_pips)
  planned_rr = float(opportunity.expected_reward_risk) if opportunity.expected_reward_risk else (
    target / expected_stop if expected_stop > 0 else 0.0
  )
  return ExcursionState(
    opportunity_id=opportunity.opportunity_id,
    episode_id=str(opportunity.episode_id or ""),
    symbol=str(opportunity.symbol).upper(),
    archetype=str(opportunity.archetype),
    direction=str(opportunity.direction).upper(),
    session=str(session or ""),
    htf_bias=str(htf_bias or ""),
    regime=str(regime or ""),
    entry_price=entry,
    invalidation_price=float(opportunity.invalidation_price),
    stop_pips=float(risk_unit),
    planned_target_pips=target,
    planned_rr=planned_rr,
    group_id=_strip_v8(group_id),
    match_id=_strip_v8(match_id),
    opened_at=int(opened_at),
    pip_size=float(pip_size),
    max_high=entry,
    min_low=entry,
    bars_held=0,
    legs_filled=0,
    ladder_ratios=ratios,
    ladder_r_multiples=multiples,
    expected_stop_pips=expected_stop,
    risk_denominator_source=source,
    planned_vs_realized_stop_ratio=ratio,
    version=LIVE_OUTCOME_VERSION,
  )


def finalize_live_outcome(
  excursion: ExcursionState,
  *,
  exit_path: str,
  realized_pips: float,
  closed_at: int,
  legs_filled: int | None = None,
) -> ScalpLiveOutcome:
  ratios, multiples = legs_for_exit_path(
    exit_path,
    ladder_ratios=excursion.ladder_ratios,
    ladder_r_multiples=excursion.ladder_r_multiples,
  )
  realized_r = volume_weighted_r(
    exit_path=exit_path,
    stop_pips=float(excursion.stop_pips),
    leg_close_ratios=ratios,
    leg_r_multiples=multiples,
  )
  mfe, mae = excursion_mfe_mae(
    direction=excursion.direction,
    entry_price=excursion.entry_price,
    max_high=excursion.max_high,
    min_low=excursion.min_low,
    pip_size=excursion.pip_size,
  )
  filled = int(legs_filled) if legs_filled is not None else int(excursion.legs_filled)
  if filled <= 0:
    if exit_path == EXIT_FULL_STOP:
      filled = 1
    elif exit_path in {EXIT_TP1_ONLY, EXIT_TP1_BE_FLAT}:
      filled = 1
    elif exit_path == EXIT_TP1_BE_TP2:
      filled = 2
  expected_stop = float(excursion.expected_stop_pips or excursion.stop_pips)
  return ScalpLiveOutcome(
    opportunity_id=excursion.opportunity_id,
    episode_id=excursion.episode_id,
    symbol=excursion.symbol,
    archetype=excursion.archetype,
    direction=excursion.direction,
    session=excursion.session,
    htf_bias=excursion.htf_bias,
    regime=excursion.regime,
    entry_price=excursion.entry_price,
    invalidation_price=excursion.invalidation_price,
    stop_pips=float(excursion.stop_pips),
    planned_target_pips=excursion.planned_target_pips,
    planned_rr=excursion.planned_rr,
    exit_path=exit_path,
    realized_r=float(realized_r),
    realized_pips=float(realized_pips),
    legs_filled=filled,
    mfe_pips=float(mfe),
    mae_pips=float(mae),
    bars_held=int(excursion.bars_held),
    opened_at=int(excursion.opened_at),
    closed_at=int(closed_at),
    version=int(excursion.version or LIVE_OUTCOME_VERSION),
    expected_stop_pips=expected_stop,
    realized_risk_pips=(
      float(excursion.stop_pips)
      if excursion.risk_denominator_source == "realized"
      else None
    ),
    planned_vs_realized_stop_ratio=excursion.planned_vs_realized_stop_ratio,
    risk_denominator_source=str(excursion.risk_denominator_source or "planned"),
    measured={
      "ladder_ratios": list(excursion.ladder_ratios),
      "ladder_r_multiples": list(excursion.ladder_r_multiples),
      "group_id": excursion.group_id,
      "match_id": excursion.match_id,
    },
  )


def apply_trace_event(trace: ExitTrace, event: dict[str, Any]) -> ExitTrace:
  """Fold one auto-trade event into the exit-path trace."""
  event_type = str(event.get("type") or "").casefold()
  message = str(event.get("message") or "").casefold()
  reason = str(event.get("reason_code") or event.get("close_reason") or "").casefold()
  target = str(event.get("target") or event.get("tp_label") or "").casefold()
  events = list(trace.events)
  if event_type and (not events or events[-1] != event_type):
    events.append(event_type)

  filled = trace.filled
  tp1 = trace.tp1
  tp2 = trace.tp2
  be_moved = trace.be_moved
  stopped = trace.stopped
  manual = trace.manual
  single_target = trace.single_target

  if event_type in {"opened", "add", "manual_opened", "order_filled"}:
    filled = True
  if event_type in {"tp_booked", "take_profit"} or "tp completed" in message:
    if "tp2" in target or "tp2" in message or target.endswith("2"):
      tp2 = True
      tp1 = True
    else:
      tp1 = True
  if event_type in {"group_sl_moved_to_be", "sl_moved"} or "moved to be" in message or "break even" in message:
    be_moved = True
  if "be" in message and ("sl" in message or "stop" in message or "moved" in message):
    be_moved = True
  if event_type in {"group_stop_loss"} or "stop_loss" in reason or "group_stop" in reason:
    stopped = True
  if event_type in {"position_closed", "group_result", "manual_closed"}:
    if "stop" in reason or "group_stop" in reason or "stop loss" in message:
      stopped = True
    if "tp2" in message or "highest tp archived tp2" in message:
      tp2 = True
      tp1 = True
    if "take profit" in message or "tp" in reason:
      if not tp2 and not tp1:
        tp1 = True
    if event_type == "manual_closed" or "manual" in reason or "external" in reason:
      manual = True
  # Explicit ladder size hint from the plan/event when present.
  targets = event.get("targets_pips") or event.get("target_plan")
  if isinstance(targets, (list, tuple)) and len(targets) == 1:
    single_target = True
  if event.get("single_target") is True:
    single_target = True

  return ExitTrace(
    group_id=trace.group_id or _strip_v8(
      str(event.get("group_id") or event.get("setup_id") or "")
    ),
    filled=filled,
    tp1=tp1,
    tp2=tp2,
    be_moved=be_moved,
    stopped=stopped,
    manual=manual,
    single_target=single_target,
    events=events[-32:],
  )


async def save_bind(
  client: Any,
  *,
  match_id: str,
  opportunity_id: str,
  symbol: str,
  signal_id: str | None = None,
) -> None:
  payload = {
    "opportunity_id": opportunity_id,
    "symbol": str(symbol).upper(),
    "match_id": _strip_v8(match_id),
    "signal_id": signal_id or "",
  }
  await client.set(
    bind_key(match_id),
    json.dumps(payload, separators=(",", ":"), sort_keys=True),
    ex=OUTCOME_TTL_SECONDS,
  )
  if signal_id:
    await client.set(
      opportunity_index_key(symbol, opportunity_id),
      signal_id,
      ex=OUTCOME_TTL_SECONDS,
    )


async def load_bind(client: Any, match_or_group_id: str) -> dict[str, Any] | None:
  raw = await client.get(bind_key(match_or_group_id))
  if raw is None:
    return None
  try:
    data = json.loads(raw)
  except (TypeError, ValueError, json.JSONDecodeError):
    return None
  return data if isinstance(data, dict) else None


async def save_excursion(client: Any, state: ExcursionState) -> None:
  await client.set(
    excursion_key(state.symbol, state.opportunity_id),
    state.to_json(),
    ex=EXCURSION_TTL_SECONDS,
  )
  await client.sadd(open_excursions_key(state.symbol), state.opportunity_id)


async def load_excursion(
  client: Any, symbol: str, opportunity_id: str,
) -> ExcursionState | None:
  raw = await client.get(excursion_key(symbol, opportunity_id))
  if raw is None:
    return None
  return ExcursionState.from_json(raw)


async def clear_excursion(client: Any, symbol: str, opportunity_id: str) -> None:
  await client.delete(excursion_key(symbol, opportunity_id))
  await client.srem(open_excursions_key(symbol), opportunity_id)


async def save_exit_trace(client: Any, trace: ExitTrace) -> None:
  await client.set(exit_trace_key(trace.group_id), trace.to_json(), ex=TRACE_TTL_SECONDS)


async def load_exit_trace(client: Any, group_id: str) -> ExitTrace | None:
  raw = await client.get(exit_trace_key(group_id))
  if raw is None:
    return None
  return ExitTrace.from_json(raw)


async def save_live_outcome(client: Any, outcome: ScalpLiveOutcome) -> None:
  await client.set(
    outcome_key(outcome.symbol, outcome.opportunity_id),
    outcome.to_json(),
    ex=OUTCOME_TTL_SECONDS,
  )


async def resolve_stop_pips_from_signal(
  client: Any,
  *,
  symbol: str,
  opportunity_id: str | None = None,
  group_id: str | None = None,
) -> float | None:
  """Best-effort stop from the persisted scalp signal (L3 invariant value)."""
  opp_id = opportunity_id
  if not opp_id and group_id:
    bound = await load_bind(client, group_id)
    if bound:
      opp_id = str(bound.get("opportunity_id") or "") or None
      symbol = str(bound.get("symbol") or symbol)
  if not opp_id:
    return None
  signal_id = await client.get(opportunity_index_key(symbol, opp_id))
  if signal_id is None:
    # Fall back: try bind's signal_id
    bound = await load_bind(client, group_id or opp_id)
    if bound:
      signal_id = bound.get("signal_id")
  if not signal_id:
    return None
  text = signal_id.decode() if isinstance(signal_id, (bytes, bytearray)) else str(signal_id)
  raw = await client.get(f"scalp:signal:{symbol.upper()}:{text}")
  if raw is None:
    return None
  try:
    data = json.loads(raw)
  except (TypeError, ValueError, json.JSONDecodeError):
    return None
  opportunity = data.get("opportunity") if isinstance(data, dict) else None
  if not isinstance(opportunity, dict):
    return None
  stop = opportunity.get("expected_stop_pips")
  try:
    value = float(stop) if stop is not None else None
  except (TypeError, ValueError):
    return None
  if value is None or value <= 0:
    return None
  return value


async def update_open_excursions_for_bar(
  client: Any,
  *,
  symbol: str,
  bar_high: float,
  bar_low: float,
) -> int:
  """Update every open live-scalp excursion from the current M1 bar."""
  raw_ids = await client.smembers(open_excursions_key(symbol))
  if not raw_ids:
    return 0
  updated = 0
  for token in raw_ids:
    oid = token.decode() if isinstance(token, (bytes, bytearray)) else str(token)
    state = await load_excursion(client, symbol, oid)
    if state is None:
      await client.srem(open_excursions_key(symbol), oid)
      continue
    nxt = update_excursion_extremes(state, bar_high=bar_high, bar_low=bar_low)
    await save_excursion(client, nxt)
    updated += 1
  return updated


async def reconcile_ledger_r(
  client: Any,
  *,
  symbol: str,
  opportunity_id: str,
  realized_r: float,
  ledger_delta: float | None,
) -> None:
  if ledger_delta is None:
    return
  if abs(float(realized_r) - float(ledger_delta)) <= 0.01:
    return
  await incr(client, symbol, "r_ledger_mismatch")
  log.warning(
    "scalp r_ledger_mismatch opportunity_id=%s realized_r=%s ledger_delta=%s",
    opportunity_id,
    realized_r,
    ledger_delta,
  )
