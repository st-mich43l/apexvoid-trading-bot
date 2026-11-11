"""Forward outcome labelling of a published Go opportunity on stored closed bars. Pure: no I/O, no clock, no database.

An opportunity's geometry is immutable and public: the entry band, the stop (the technical invalidation) and the first target, published
when it was created. This module asks one question of the bars that FOLLOW its earliest permitted execution time: what would a single
limit order at the proximal edge of the band have done, under explicit and conservative assumptions? The answer is a hypothetical
(replay-derived) outcome. It is never mixed with realised trades (``auto_trade_results``), never feeds a decision, and uses nothing
from the bars before the opportunity became eligible.

Methodology (assumptions version ``v1``; every number below is a parameter of ``Assumptions`` and is stored with each label):

* Eligibility. The opportunity is known once its observed bar has closed: ``eligible_from = max(created_at + timeframe, produced_at) +
  latency``. Only M1 bars that OPEN at or after ``eligible_from`` are used. It stays fillable until ``min(expires_at, terminal_at)`` where
  ``terminal_at`` is the Go engine's own invalidation time (bars opening at or after it are not used: conservative).
* Entry. One limit order at the proximal edge of the band (BUY: ``entry_high``, SELL: ``entry_low``), filled at that price plus an
  explicit adverse ``slippage_pips``. Bars are the feed's BID series. A BUY pays the ask, so it fills when ``low + spread <= entry_high``;
  a SELL sells at the bid, so it fills when ``high >= entry_low``. A bar whose range overlaps the band without a BUY filling is
  ``unfilled_touched`` (the spread kept it out), never ``unfilled_not_touched``.
* Spread. Per M1 bar, the archived quote statistic ``spread_basis`` (default ``max``: the widest spread of that minute, which makes every
  leg harder to fill and every stop easier to hit). With no archived quotes for the minute, the fallback spread of the symbol is used and
  the label says so (``spread_source``).
* Exits. BUY: stop when ``low <= stop``, target when ``high >= target`` (the bid). SELL: stop when ``high + spread >= stop``, target when
  ``low + spread <= target`` (the ask). The stop fills at the stop price plus adverse slippage; the target at the target price.
* Ambiguity. A bar in which both the stop and the target are reachable is ``ambiguous_sl_tp``; a fill bar in which either exit is
  reachable is ``ambiguous_fill_bar``: the order inside a bar is unknowable from OHLC and no favourable order is assumed. Ambiguous
  labels carry ``r_low`` (the stop) and ``r_high`` (the target) and are excluded or bounded, never resolved.
* Horizon. A filled order not resolved within ``max_hold_seconds`` is ``open_timeout`` (marked to the last close); one still open when the
  stored bars end is ``open_data_end`` (provisional: it is labelled again when more bars exist).
* Data quality. A scheduled-closure-aware gap in the M1 bars between ``eligible_from`` and the end of the used window makes the label
  ``data_quality = 'gap'``; an opportunity with no bar in its first minutes is ``no_data``.

Known limits are listed in docs/research-data.md (limit-at-edge is not the live ladder, no queue position, OHLC order, bid-based bars).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Callable

from app.analysis_client.models import GO_EXPIRY_REASONS
from app.research.bar_store import TIMEFRAME_SECONDS, find_gaps

LABELLER_VERSION = "outcome-labeller-1"

FILL_FILLED = "filled"
FILL_UNFILLED_TOUCHED = "unfilled_touched"
FILL_UNFILLED_NOT_TOUCHED = "unfilled_not_touched"
FILL_NO_DATA = "no_data"

OUTCOME_TP = "tp"
OUTCOME_SL = "sl"
OUTCOME_AMBIGUOUS_SL_TP = "ambiguous_sl_tp"
OUTCOME_AMBIGUOUS_FILL_BAR = "ambiguous_fill_bar"
OUTCOME_OPEN_TIMEOUT = "open_timeout"
OUTCOME_OPEN_DATA_END = "open_data_end"
OUTCOME_NO_DATA = "no_data"

RESOLVED = (OUTCOME_TP, OUTCOME_SL)
AMBIGUOUS = (OUTCOME_AMBIGUOUS_SL_TP, OUTCOME_AMBIGUOUS_FILL_BAR)

THESIS_BUCKET_ATR_FRACTION = 0.5  # the Algo Bot adapter's bucket for strategies without a persistent structure id
THESIS_BUCKET_MIN_PIPS = 15


@dataclass(frozen=True)
class Assumptions:
  version: str = "v1"
  entry_model: str = "proximal_limit"
  latency_seconds: int = 0
  spread_basis: str = "max"  # max | mean | min of the minute's archived quotes
  fallback_spread_pips: dict[str, float] = field(default_factory=lambda: {"XAU": 3.0, "EURUSD": 1.0, "GBPUSD": 1.5, "USDJPY": 1.5, "GBPJPY": 3.0})
  default_fallback_spread_pips: float = 3.0
  slippage_pips: float = 0.0
  max_hold_seconds: int = 86400
  first_bar_tolerance_seconds: int = 180  # no bar this soon after eligibility means the archive does not cover the opportunity

  @property
  def label_set(self) -> str:
    return f"{self.version}:{self.entry_model}:{self.spread_basis}"

  def as_json(self) -> str:
    return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

  @property
  def fingerprint(self) -> str:
    return hashlib.sha1(self.as_json().encode()).hexdigest()[:12]


@dataclass(frozen=True)
class OpportunitySpec:
  """The immutable facts of a published opportunity that the label reads, plus provenance carried through unchanged."""

  opportunity_id: str
  strategy: str
  symbol: str
  direction: str
  timeframe: str
  entry_low: float
  entry_high: float
  stop: float
  target: float
  created_at: int
  produced_at: int | None
  expires_at: int
  terminal_at: int | None  # the Go engine's invalidation time; None when it expired or is still live
  terminal_reason: str | None
  pip: float
  thesis_key: str
  quality_overall: float | None
  quality_components: dict
  stars: int | None
  config_fingerprint: str | None
  config_version: int | None


@dataclass(frozen=True)
class Bar:
  open_ts: int
  open: float
  high: float
  low: float
  close: float


@dataclass
class Label:
  opportunity_id: str
  label_set: str
  eligible_from: int
  window_end: int
  end_reason: str  # expired | invalidated
  fill_state: str
  outcome: str
  fill_ts: int | None = None
  fill_price: float | None = None
  exit_ts: int | None = None
  target_r: float | None = None
  r_low: float | None = None
  r_high: float | None = None
  r_mark: float | None = None
  mfe_r: float | None = None
  mae_r: float | None = None
  r1_ts: int | None = None
  r2_ts: int | None = None
  spread_source: str | None = None
  spread_used: float | None = None
  data_quality: str = "ok"
  bars_used: int = 0
  coverage_end: int | None = None  # open of the last bar available to this label
  complete: bool = True  # False: the window is not yet fully covered by stored bars, so the label is not final
  touches: dict | None = None  # first-touch bar open times after the fill: stop, r1, r2, r3, r4, be (return to entry after r1), be_in_r1_bar
  ladder_outcome: str | None = None  # the live scalp/reaction exit book on the same bars: 50% at 1R, stop to break-even, 50% at 2R
  ladder_r_low: float | None = None
  ladder_r_high: float | None = None


SpreadFn = Callable[[int], "tuple[float, str] | None"]


def tf_seconds(timeframe: str) -> int:
  return TIMEFRAME_SECONDS[timeframe.upper()]


def eligible_from(spec: OpportunitySpec, a: Assumptions) -> int:
  closed = spec.created_at + tf_seconds(spec.timeframe)
  if spec.produced_at:
    closed = max(closed, int(spec.produced_at))
  return closed + int(a.latency_seconds)


def window_end(spec: OpportunitySpec) -> tuple[int, str]:
  if spec.terminal_at is not None and spec.terminal_at < spec.expires_at:
    return spec.terminal_at, "invalidated"
  return spec.expires_at, "expired"


def thesis_key(symbol: str, strategy: str, direction: str, zone_id: str | None, entry_low: float, entry_high: float, atr: float | None, pip: float) -> str:
  """The same structural identity the Algo Bot adapter gives a thesis: the reaction's zone id when Go published one, otherwise the strategy and
  an ATR-scaled bucket of the entry midpoint, so re-publications of one structure share a key."""
  if zone_id:
    structure = str(zone_id)
  else:
    bucket = max(float(atr or 0.0) * THESIS_BUCKET_ATR_FRACTION, pip * THESIS_BUCKET_MIN_PIPS)
    structure = f"{strategy}:{round(((entry_low + entry_high) / 2.0) / bucket)}"
  return f"{symbol}|{strategy}|{direction}|{structure}"


def spec_from_rows(creation: dict, terminal: dict | None, *, pip: float) -> tuple[OpportunitySpec | None, int | None]:
  """Build (spec, quality model version) from an ``analysis.opportunity.v1`` envelope and, when the opportunity ended, its terminal envelope; (None, None) if unusable."""
  try:
    payload = creation["payload"]
    entry = payload["entry"]
    technical = payload.get("technical_context") or {}
    confirmation = technical.get("confirmation") or {}
    confluence = technical.get("confluence") or {}
    quality = payload.get("quality") or {}
    components = dict(quality.get("components") or {})
    direction = str(payload["direction"]).upper()
    low, high = float(entry["low"]), float(entry["high"])
    stop = float(payload["invalidation"]["price"])
    target = float(payload["targets"][0]["price"]["price"])
    symbol = str(payload["symbol"]).upper()
    strategy = str(payload["strategy"])
    timeframe = str(payload.get("timeframe") or "").upper()
    created_at, expires_at = int(payload["created_at"]), int(payload["expires_at"])
  except (KeyError, IndexError, TypeError, ValueError):
    return None, None
  if direction not in ("BUY", "SELL") or timeframe not in TIMEFRAME_SECONDS or not (high >= low > 0) or not pip > 0:
    return None, None
  terminal_at = terminal_reason = None
  if terminal:
    reason = str((terminal.get("payload") or {}).get("reason_code") or "")
    terminal_reason = reason or None
    stamp = (terminal.get("payload") or {}).get("invalidated_at") or terminal.get("occurred_at")
    if stamp is not None and reason.upper() not in GO_EXPIRY_REASONS:
      terminal_at = int(stamp)
  version = components.get("quality_model_version")
  spec = OpportunitySpec(
    opportunity_id=str(payload["id"]), strategy=strategy, symbol=symbol, direction=direction, timeframe=timeframe,
    entry_low=low, entry_high=high, stop=stop, target=target, created_at=created_at,
    produced_at=int(creation["produced_at"]) if creation.get("produced_at") is not None else None,
    expires_at=expires_at, terminal_at=terminal_at, terminal_reason=terminal_reason, pip=pip,
    thesis_key=thesis_key(symbol, strategy, direction, confirmation.get("zone_id"), low, high, technical.get("atr"), pip),
    quality_overall=None if quality.get("overall") is None else float(quality["overall"]),
    quality_components=components,
    stars=None if confluence.get("selected_stars") is None else int(confluence["selected_stars"]),
    config_fingerprint=creation.get("config_fingerprint"),
    config_version=None if creation.get("config_version") is None else int(creation["config_version"]),
  )
  return spec, (None if version is None else int(version))


def _spread_at(bar: Bar, spread_fn: SpreadFn, a: Assumptions, spec: OpportunitySpec) -> tuple[float, str]:
  found = spread_fn(bar.open_ts - bar.open_ts % 60)
  if found is not None:
    return found
  pips = a.fallback_spread_pips.get(spec.symbol, a.default_fallback_spread_pips)
  return pips * spec.pip, "assumed"


def _timeline(spec: OpportunitySpec, bars: list[Bar], fill_ts: int, fill_price: float, risk: float, spread_fn: SpreadFn, a: Assumptions, horizon_end: int) -> tuple[dict, float | None]:
  """First-touch times (bar open) after the fill for the stop and +1R..+4R, the first return to the entry after +1R, and the mark at the end.

  The fill bar contributes only its stop (``stop`` equal to ``fill_ts`` means "reachable in the fill bar": ambiguous), never a favourable touch.
  """
  buy = spec.direction == "BUY"
  touches: dict = {"stop": None, "r1": None, "r2": None, "r3": None, "r4": None, "be": None, "be_in_r1_bar": False}
  last_mark = None
  for bar in bars:
    if bar.open_ts < fill_ts or bar.open_ts >= horizon_end:
      continue
    spread, _ = _spread_at(bar, spread_fn, a, spec)
    stop_hit = bar.low <= spec.stop if buy else bar.high + spread >= spec.stop
    if stop_hit and touches["stop"] is None:
      touches["stop"] = bar.open_ts
    if bar.open_ts == fill_ts:
      continue
    best = (bar.high - fill_price) if buy else (fill_price - (bar.low + spread))
    for n in (1, 2, 3, 4):
      key = f"r{n}"
      if touches[key] is None and best >= n * risk:
        touches[key] = bar.open_ts
    returned = bar.low <= fill_price if buy else bar.high + spread >= fill_price
    if touches["r1"] == bar.open_ts and returned:
      touches["be_in_r1_bar"] = True
    if touches["r1"] is not None and touches["be"] is None and bar.open_ts > touches["r1"] and returned:
      touches["be"] = bar.open_ts
    exit_price = bar.close if buy else bar.close + spread
    last_mark = ((exit_price - fill_price) if buy else (fill_price - exit_price)) / risk
  return touches, last_mark


def ladder_outcome(t: dict, fill_ts: int, last_mark_r: float | None, slip_r: float = 0.0) -> tuple[str, float, float]:
  """(outcome, r_low, r_high) of the live exit book (50% at 1R, stop to break-even after it, 50% at 2R) from the touch timeline.

  Same-bar ties are never resolved in anyone's favour: they return the conservative and the favourable bound.
  """
  loss = -(1.0 + slip_r)
  stop, r1, r2, be = t["stop"], t["r1"], t["r2"], t["be"]
  if stop is not None and stop == fill_ts:
    return "ambiguous_fill_bar", loss, 1.5
  if stop is not None and (r1 is None or stop < r1):
    return "sl", loss, loss
  if stop is not None and stop == r1:
    return "ambiguous_sl_r1", loss, 1.5
  if r1 is None:
    mark = last_mark_r if last_mark_r is not None else 0.0
    return "open", mark, mark
  # +1R was reached before any stop: half is booked at +1R (0.5R); the stop sits at the entry from the next bar on.
  if t.get("be_in_r1_bar"):
    return ("ambiguous_be_r1_bar", 0.5, 1.5) if r2 is not None and r2 >= r1 else ("be", 0.5, 0.5)
  if r2 is not None and (be is None or r2 < be):
    return "tp2", 1.5, 1.5
  if r2 is not None and be is not None and r2 == be:
    return "ambiguous_be_r2", 0.5, 1.5
  if be is not None:
    return "tp1_be", 0.5, 0.5
  tail = 0.5 + 0.5 * (last_mark_r if last_mark_r is not None else 1.0)
  return "tp1_open", tail, tail


def label_opportunity(spec: OpportunitySpec, bars: list[Bar], spread_fn: SpreadFn, a: Assumptions, *, coverage_end: int | None = None) -> Label:
  """Label one opportunity from M1 ``bars`` (ascending by open time, any bars from before ``eligible_from`` are ignored).

  ``coverage_end`` is the open time of the newest bar the store holds for the symbol (None: use the last bar given); a window that
  extends past it is not complete and the label is provisional.
  """
  start = eligible_from(spec, a)
  end, end_reason = window_end(spec)
  start_aligned = start + (-start) % 60  # first whole minute at or after eligibility
  usable = [b for b in bars if b.open_ts >= start_aligned]
  last_open = coverage_end if coverage_end is not None else (bars[-1].open_ts if bars else None)
  label = Label(spec.opportunity_id, a.label_set, start, end, end_reason, FILL_NO_DATA, OUTCOME_NO_DATA, coverage_end=last_open)
  if not usable or usable[0].open_ts > start_aligned + a.first_bar_tolerance_seconds:
    label.complete = last_open is not None and last_open + 60 >= end  # absent data is final only once the stored bars have passed the window
    return label

  buy = spec.direction == "BUY"
  slip = a.slippage_pips * spec.pip
  fill_price = (spec.entry_high + slip) if buy else (spec.entry_low - slip)
  risk = abs(fill_price - spec.stop)
  if risk <= 0 or (buy and spec.stop >= fill_price) or (not buy and spec.stop <= fill_price):
    label.fill_state, label.outcome = FILL_NO_DATA, OUTCOME_NO_DATA
    label.data_quality = "bad_geometry"
    return label
  target_r = abs(spec.target - fill_price) / risk
  label.target_r = target_r

  horizon_end = None
  touched = False
  used: list[Bar] = []
  filled_at: int | None = None
  mfe = mae = 0.0
  for bar in usable:
    if bar.open_ts >= end and filled_at is None:
      break
    if horizon_end is not None and bar.open_ts >= horizon_end:
      break
    spread, source = _spread_at(bar, spread_fn, a, spec)
    used.append(bar)
    if filled_at is None:
      if buy:
        overlaps = bar.low <= spec.entry_high and bar.high >= spec.entry_low
        fills = bar.low + spread <= spec.entry_high
      else:
        overlaps = bar.high >= spec.entry_low and bar.low <= spec.entry_high
        fills = bar.high >= spec.entry_low
      touched = touched or overlaps
      if not fills:
        continue
      filled_at = bar.open_ts
      horizon_end = filled_at + a.max_hold_seconds
      label.fill_state, label.fill_ts, label.fill_price = FILL_FILLED, filled_at, fill_price
      label.spread_source, label.spread_used = source, spread
    # From the fill bar on: where can the stop and the target be reached in this bar?
    if buy:
      stop_hit, target_hit = bar.low <= spec.stop, bar.high >= spec.target
      favourable, adverse = (bar.high - fill_price) / risk, (fill_price - bar.low) / risk
      r1, r2 = bar.high >= fill_price + risk, bar.high >= fill_price + 2 * risk
    else:
      stop_hit, target_hit = bar.high + spread >= spec.stop, bar.low + spread <= spec.target
      favourable, adverse = (fill_price - (bar.low + spread)) / risk, ((bar.high + spread) - fill_price) / risk
      r1, r2 = bar.low + spread <= fill_price - risk, bar.low + spread <= fill_price - 2 * risk
    # Inside the fill bar the order of events is unknown: its adverse extreme counts (worst case), its favourable one and any 1R / 2R touch do not.
    mae = max(mae, adverse)
    if bar.open_ts > filled_at:
      mfe = max(mfe, favourable)
      if r1 and label.r1_ts is None:
        label.r1_ts = bar.open_ts
      if r2 and label.r2_ts is None:
        label.r2_ts = bar.open_ts
    label.exit_ts = bar.open_ts
    if bar.open_ts == filled_at and (stop_hit or target_hit):
      label.outcome = OUTCOME_AMBIGUOUS_FILL_BAR
      label.r_low, label.r_high = -(1 + slip / risk), target_r
      break
    if stop_hit and target_hit:
      label.outcome = OUTCOME_AMBIGUOUS_SL_TP
      label.r_low, label.r_high = -(1 + slip / risk), target_r
      break
    if stop_hit:
      label.outcome = OUTCOME_SL
      label.r_low = label.r_high = -(1 + slip / risk)
      break
    if target_hit:
      label.outcome = OUTCOME_TP
      label.r_low = label.r_high = target_r
      break
  label.bars_used = len(used)
  label.mfe_r, label.mae_r = (mfe, mae) if filled_at is not None else (None, None)
  if filled_at is not None:
    label.touches, last_mark = _timeline(spec, usable, filled_at, fill_price, risk, spread_fn, a, horizon_end)
    label.ladder_outcome, label.ladder_r_low, label.ladder_r_high = ladder_outcome(label.touches, filled_at, last_mark, slip / risk)

  if filled_at is None:
    label.fill_state = FILL_UNFILLED_TOUCHED if touched else FILL_UNFILLED_NOT_TOUCHED
    label.outcome = f"{'expired' if end_reason == 'expired' else 'invalidated'}_{'touched_' if touched else ''}unfilled"
    label.complete = last_open is not None and last_open + 60 >= end
  elif label.outcome not in RESOLVED + AMBIGUOUS:
    last = used[-1]
    exit_price = last.close if buy else last.close + _spread_at(last, spread_fn, a, spec)[0]
    label.r_mark = ((exit_price - fill_price) if buy else (fill_price - exit_price)) / risk
    label.r_low = label.r_high = label.r_mark
    label.exit_ts = last.open_ts
    horizon_reached = last_open is not None and last_open + 60 >= horizon_end
    label.outcome = OUTCOME_OPEN_TIMEOUT if horizon_reached else OUTCOME_OPEN_DATA_END
    label.complete = horizon_reached
  else:
    label.complete = True

  # Continuity of the bars this label actually used: a closure-aware gap inside the window is flagged, never filled.
  if len(used) >= 2:
    gaps = [g for g in find_gaps(spec.symbol, "M1", [b.open_ts for b in used]) if g.unexplained > 0]
    if gaps:
      label.data_quality = "gap"
  return label
