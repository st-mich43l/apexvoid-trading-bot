"""Immutable M5 scalp context snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from typing import Any

import pandas as pd

from app.analysis.dealing_range import dealing_range
from app.analysis.types import Swing
from app.scalping.models import (
  ARCHETYPE_BREAKOUT_RETEST,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_MOMENTUM_CHASE,
  ARCHETYPE_RANGE_SWEEP,
  CONTEXT_VERSION,
  ScalpContextSnapshot,
  deterministic_id,
)
from app.runtime.price_identity import rounded_price


LIVE_SYMBOL = "XAU"


def _instrument_allows_hfs(instrument_cfg: Any) -> bool:
  """HFS is gold ladder-pip only — never FX ``fixed_rr`` books.

  Live 2026-08-20: treating every live instrument as HFS-eligible ran M1
  scalp cycles on EURUSD/GBPJPY/GBPUSD/USDJPY together with XAU, pegged the
  algo-bot container at ~100% CPU, and starved Telegram ``/trade`` responses
  for minutes. FX stays reaction + manual /algo.
  """
  targeting = getattr(instrument_cfg, "targeting", None)
  mode = getattr(targeting, "mode", None)
  if mode is None:
    return True
  value = getattr(mode, "value", mode)
  return str(value).casefold() != "fixed_rr"


def _hfs_symbols(cfg: Any | None = None) -> set[str]:
  if cfg is None:
    from app.core.config import runtime_config

    cfg = runtime_config
  live_instruments = getattr(cfg, "live_instruments", None)
  if callable(live_instruments):
    allowed: set[str] = set()
    for_instrument = getattr(cfg, "for_instrument", None)
    for item in live_instruments():
      symbol = str(item).upper()
      if callable(for_instrument):
        try:
          effective = for_instrument(symbol)
        except Exception:
          continue
        if not _instrument_allows_hfs(effective):
          continue
      allowed.add(symbol)
    return allowed or {LIVE_SYMBOL}
  identity = getattr(cfg, "identity", None)
  if identity is not None:
    rollout = getattr(identity.rollout, "value", identity.rollout)
    if str(rollout).lower() != "live":
      return set()
    if not _instrument_allows_hfs(cfg):
      return set()
    return {
      str(identity.instrument_id).upper(),
      str(identity.canonical_symbol).upper(),
      str(identity.broker_symbol).upper(),
      *(str(alias).upper() for alias in identity.aliases),
    }
  return {LIVE_SYMBOL}


def is_hfs_symbol(symbol: str, cfg: Any | None = None) -> bool:
  return str(symbol).upper() in _hfs_symbols(cfg)


def classify_session(ts: int, cfg: Any | None = None) -> str:
  """Return asia|london|new_york|london_ny_overlap|rollover."""
  hour = datetime.fromtimestamp(int(ts), tz=timezone.utc).hour
  sessions = getattr(getattr(cfg, "market_data", None), "sessions", None)
  asia = int(getattr(sessions, "asia_start", 22) or 22)
  london = int(getattr(sessions, "london_start", 7) or 7)
  ny = int(getattr(sessions, "ny_start", 13) or 13)
  rollover = int(getattr(sessions, "daily_rollover_utc_hour", 21) or 21)

  if hour in {(rollover - 1) % 24, rollover, (rollover + 1) % 24}:
    return "rollover"
  # London–NY overlap: NY open through +3h
  if ny <= hour < min(24, ny + 3):
    return "london_ny_overlap"
  if london <= hour < ny:
    return "london"
  if ny <= hour < asia or (asia > ny and hour >= ny):
    # after NY start until asia open
    if hour >= ny and (asia > hour or asia < london):
      if hour < asia or asia < london:
        if not (ny <= hour < ny + 3):
          return "new_york"
  # Asia window wraps midnight
  if hour >= asia or hour < london:
    return "asia"
  return "london"


_HFS_ARCHETYPES: tuple[str, ...] = (
  ARCHETYPE_RANGE_SWEEP,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_BREAKOUT_RETEST,
  ARCHETYPE_MOMENTUM_CHASE,
)

# Asia prints usable range/breakout edges; Impulse/Momentum stay London/NY-only
# after the 2026-08 Asia Impulse bleed and momentum quality dig.
_ASIA_EXCLUDED_ARCHETYPES = frozenset({
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_MOMENTUM_CHASE,
})


def permitted_archetypes_for_session(
  session: str,
  *,
  ts: int | None = None,
  hour: int | None = None,
  cfg: Any | None = None,
) -> tuple[str, ...]:
  """Permit HFS archetypes for Asia + killzones when technique pack enforces.

  Owner 2026-08-10: dead-hour Impulse churn ≈ −498 pips → killzone gate.
  Owner 2026-08-26: Asia still prints usable XAU range/breakout; Impulse and
  Momentum Chase are London/NY (killzone) only even when archetype flags are
  on. Rollover stays empty regardless of enforce.
  """
  if session == "rollover":
    return ()
  from app.autotrade.killzone import classify_killzone, technique_enforce

  tech = getattr(getattr(cfg, "execution", None), "technique", None)
  require_kz = True if tech is None else bool(
    getattr(tech, "hfs_require_killzone", True),
  )
  enabled = _enabled_hfs_archetypes(cfg)
  if session == "asia":
    return tuple(
      item for item in _HFS_ARCHETYPES
      if item in enabled and item not in _ASIA_EXCLUDED_ARCHETYPES
    )
  if technique_enforce(cfg) and require_kz:
    if ts is None and hour is None:
      # Legacy callers without a clock: named London/overlap/Asia are
      # optimistic; other labels without hour fail closed.
      if session not in {"london", "london_ny_overlap", "asia"}:
        return ()
    elif not classify_killzone(ts=ts, hour=hour, cfg=cfg).allowed:
      return ()
  return tuple(item for item in _HFS_ARCHETYPES if item in enabled)


def _enabled_hfs_archetypes(cfg: Any | None) -> frozenset[str]:
  arch = getattr(
    getattr(getattr(cfg, "strategies", None), "high_frequency_scalp", None),
    "archetypes",
    None,
  )
  allowed: list[str] = []
  if bool(getattr(arch, "range_sweep_enabled", True)):
    allowed.append(ARCHETYPE_RANGE_SWEEP)
  if bool(getattr(arch, "impulse_pullback_enabled", True)):
    allowed.append(ARCHETYPE_IMPULSE_PULLBACK)
  if bool(getattr(arch, "breakout_retest_enabled", True)):
    allowed.append(ARCHETYPE_BREAKOUT_RETEST)
  if bool(getattr(arch, "momentum_chase_enabled", False)):
    allowed.append(ARCHETYPE_MOMENTUM_CHASE)
  return frozenset(allowed)


def _bar_ts(df: pd.DataFrame) -> int | None:
  if df is None or df.empty:
    return None
  idx = df.index[-1]
  if hasattr(idx, "timestamp"):
    return int(idx.timestamp())
  try:
    return int(pd.Timestamp(idx).timestamp())
  except (TypeError, ValueError):
    return None


def _swings_from_ohlc(df: pd.DataFrame, *, lookback: int = 2) -> list[Swing]:
  """Lightweight swing marks for dealing-range construction."""
  if df is None or len(df) < lookback * 2 + 1:
    return []
  highs = df["high"].astype(float)
  lows = df["low"].astype(float)
  out: list[Swing] = []
  for i in range(lookback, len(df) - lookback):
    window_h = highs.iloc[i - lookback: i + lookback + 1]
    window_l = lows.iloc[i - lookback: i + lookback + 1]
    ts = df.index[i]
    if float(highs.iloc[i]) >= float(window_h.max()):
      out.append(Swing(index=i, kind="high", price=float(highs.iloc[i]), ts=pd.Timestamp(ts)))
    if float(lows.iloc[i]) <= float(window_l.min()):
      out.append(Swing(index=i, kind="low", price=float(lows.iloc[i]), ts=pd.Timestamp(ts)))
  return out


def compute_context_id(
  symbol: str,
  m5_bar_ts: int,
  dealing_range_low: float | None,
  dealing_range_high: float | None,
  active_range_low: float | None,
  active_range_high: float | None,
  structure: str,
  pip_size: float = 0.1,
) -> str:
  def identity_price(value: float | None) -> float | None:
    return None if value is None else rounded_price(value, pip_size)

  return deterministic_id(
    symbol.upper(),
    m5_bar_ts,
    identity_price(dealing_range_low),
    identity_price(dealing_range_high),
    identity_price(active_range_low),
    identity_price(active_range_high),
    structure,
  )


def is_context_fresh(
  snapshot: ScalpContextSnapshot,
  now: int,
  max_age_seconds: int,
  cfg: Any | None = None,
) -> bool:
  if snapshot.symbol.upper() not in _hfs_symbols(cfg):
    return False
  age = int(now) - int(snapshot.m5_bar_ts)
  return age <= max(0, int(max_age_seconds))


def build_scalp_context_snapshot(
  *,
  symbol: str,
  m5: pd.DataFrame,
  m15: pd.DataFrame | None,
  h1: pd.DataFrame | None,
  price: float,
  pip_size: float,
  atr: float,
  now: int,
  cfg: Any | None = None,
  htf_bias: str = "unknown",
  m5_structure: str = "range",
  regime: str = "range",
) -> ScalpContextSnapshot | None:
  """Build an immutable M5 context. Fail closed for non-live / malformed."""
  if not is_hfs_symbol(symbol, cfg):
    return None
  if m5 is None or m5.empty or not math.isfinite(price) or price <= 0:
    return None
  m5_ts = _bar_ts(m5)
  if m5_ts is None:
    return None

  active_lookback = 24
  window = m5.tail(active_lookback)
  active_low = float(window["low"].min())
  active_high = float(window["high"].max())
  if active_high <= active_low:
    return None
  active_eq = (active_low + active_high) / 2.0

  source_df = m15 if m15 is not None and not m15.empty else m5
  swings = _swings_from_ohlc(source_df)
  dr = dealing_range(swings, price)
  dealing_low = None if dr is None else float(dr.low)
  dealing_high = None if dr is None else float(dr.high)
  dealing_pos = None if dr is None else float(dr.position)

  support_high = active_low
  support_low = active_low - max(atr, pip_size) * 0.25
  resist_low = active_high
  resist_high = active_high + max(atr, pip_size) * 0.25

  buy_room = (active_high - price) / pip_size if pip_size > 0 else None
  sell_room = (price - active_low) / pip_size if pip_size > 0 else None

  context_ts = int(now) if now else m5_ts
  session = classify_session(context_ts, cfg)
  permitted = permitted_archetypes_for_session(
    session, ts=context_ts, cfg=cfg,
  )

  context_id = compute_context_id(
    symbol,
    m5_ts,
    dealing_low,
    dealing_high,
    active_low,
    active_high,
    m5_structure,
    pip_size,
  )
  return ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id=context_id,
    symbol=str(symbol).upper(),
    created_at=int(now),
    h1_bar_ts=_bar_ts(h1) if h1 is not None else None,
    m15_bar_ts=_bar_ts(m15) if m15 is not None else None,
    m5_bar_ts=m5_ts,
    htf_bias=str(htf_bias or "unknown"),
    m5_structure=str(m5_structure or "unknown"),
    regime=str(regime or "unknown"),
    dealing_range_low=dealing_low,
    dealing_range_high=dealing_high,
    dealing_range_position=dealing_pos,
    active_range_low=active_low,
    active_range_high=active_high,
    active_range_eq=active_eq,
    nearest_support_low=support_low,
    nearest_support_high=support_high,
    nearest_resistance_low=resist_low,
    nearest_resistance_high=resist_high,
    buy_corridor_room_pips=buy_room,
    sell_corridor_room_pips=sell_room,
    session=session,
    permitted_archetypes=permitted,
    atr=float(atr or 0.0),
    measured={
      "active_lookback": active_lookback,
      "price": float(price),
      "dealing_zone": None if dr is None else dr.zone,
    },
  )


def current_context_key(symbol: str) -> str:
  return f"scalp:context:{symbol.upper()}:current"


def historic_context_key(symbol: str, context_id: str) -> str:
  return f"scalp:context:{symbol.upper()}:{context_id}"


async def save_context(
  client: Any,
  snapshot: ScalpContextSnapshot,
  *,
  current_ttl: int,
  historic_ttl: int,
) -> None:
  payload = snapshot.to_json()
  pipe = client.pipeline(transaction=True)
  pipe.set(
    current_context_key(snapshot.symbol),
    payload,
    ex=max(60, int(current_ttl)),
  )
  pipe.set(
    historic_context_key(snapshot.symbol, snapshot.context_id),
    payload,
    ex=max(60, int(historic_ttl)),
  )
  await pipe.execute()


async def load_current_context(
  client: Any,
  symbol: str,
  cfg: Any | None = None,
) -> ScalpContextSnapshot | None:
  if not is_hfs_symbol(symbol, cfg):
    return None
  raw = await client.get(current_context_key(symbol))
  if raw is None:
    return None
  return ScalpContextSnapshot.from_json(raw)
