"""UTC session, prior-day, and prior-week liquidity levels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta, timezone

import pandas as pd

from app.analysis.types import SessionLevel


@dataclass(frozen=True)
class SessionWindow:
  name: str
  start_hour: int
  end_hour: int


def session_levels(df: pd.DataFrame, cfg) -> list[SessionLevel]:
  if df.empty:
    return []
  frame = _utc_sorted(df)
  levels = [
    *_session_extremes(frame, cfg),
    *_previous_day_levels(frame, _daily_rollover(cfg)),
  ]
  return sorted(levels, key=lambda item: (item.ts, item.name, item.price))


def previous_week_levels(df: pd.DataFrame) -> list[SessionLevel]:
  if df.empty:
    return []
  frame = _utc_sorted(df)
  current_week = _week_start(frame.index[-1])
  previous_week = current_week - pd.Timedelta(days=7)
  if frame.index[0] > previous_week:
    return []
  week = frame[(frame.index >= previous_week) & (frame.index < current_week)]
  if week.empty:
    return []
  return [
    _level_from_extreme(frame, week, "PWH", "high", week.index[-1]),
    _level_from_extreme(frame, week, "PWL", "low", week.index[-1]),
  ]


def _utc_sorted(df: pd.DataFrame) -> pd.DataFrame:
  frame = df.sort_index()
  if frame.index.tz is None:
    return frame.tz_localize("UTC")
  return frame.tz_convert("UTC")


def _session_extremes(df: pd.DataFrame, cfg) -> list[SessionLevel]:
  levels: list[SessionLevel] = []
  last_ts = df.index[-1]
  for window in _windows(cfg):
    groups: dict[date, list[int]] = {}
    for i, ts in enumerate(df.index):
      if not _in_window(ts.hour, window.start_hour, window.end_hour):
        continue
      groups.setdefault(_session_date(ts, window), []).append(i)
    closed = 0
    for session_date in sorted(groups, reverse=True):
      close_ts = _session_close_ts(session_date, window)
      if close_ts > last_ts:
        continue
      run = df.iloc[groups[session_date]]
      if run.empty:
        continue
      levels.append(_level_from_extreme(df, run, f"{window.name}_H", "high", close_ts))
      levels.append(_level_from_extreme(df, run, f"{window.name}_L", "low", close_ts))
      closed += 1
      if closed >= 2:
        break
  return levels


def _previous_day_levels(df: pd.DataFrame, rollover_hour: int) -> list[SessionLevel]:
  grouped: dict[date, list[int]] = {}
  for i, ts in enumerate(df.index):
    grouped.setdefault(_trading_day(ts, rollover_hour), []).append(i)
  days = sorted(grouped)
  if len(days) < 2:
    return []
  previous_day = days[-2]
  close_ts = _daily_close_ts(previous_day, rollover_hour)
  run = df.iloc[grouped[previous_day]]
  return [
    _level_from_extreme(df, run, "PDH", "high", close_ts),
    _level_from_extreme(df, run, "PDL", "low", close_ts),
  ]


def _level_from_extreme(
  df: pd.DataFrame,
  run: pd.DataFrame,
  name: str,
  column: str,
  closed_at: pd.Timestamp,
) -> SessionLevel:
  if column == "high":
    ts = run[column].astype(float).idxmax()
    price = float(run.loc[ts, column])
  else:
    ts = run[column].astype(float).idxmin()
    price = float(run.loc[ts, column])
  swept_ts = _swept_ts(df, name, price, closed_at)
  return SessionLevel(
    name=name,
    price=price,
    ts=ts,
    swept=swept_ts is not None,
    swept_ts=swept_ts,
  )


def _swept_ts(
  df: pd.DataFrame,
  name: str,
  price: float,
  closed_at: pd.Timestamp,
) -> pd.Timestamp | None:
  later = df[df.index > closed_at]
  if later.empty:
    return None
  if _is_high_level(name):
    swept = later[later["high"].astype(float) > price]
  else:
    swept = later[later["low"].astype(float) < price]
  if swept.empty:
    return None
  return swept.index[0]


def _windows(cfg) -> list[SessionWindow]:
  asia = int(getattr(cfg, "session_asia_start", 22))
  london = int(getattr(cfg, "session_london_start", 7))
  ny = int(getattr(cfg, "session_ny_start", 13))
  return [
    SessionWindow("ASIA", asia, london),
    SessionWindow("LONDON", london, ny),
    SessionWindow("NY", ny, asia),
  ]


def _daily_rollover(cfg) -> int:
  return int(getattr(cfg, "daily_rollover_utc_hour", 21))


def _in_window(hour: int, start: int, end: int) -> bool:
  if start < end:
    return start <= hour < end
  return hour >= start or hour < end


def _session_date(ts: pd.Timestamp, window: SessionWindow) -> date:
  current = ts.date()
  if window.start_hour > window.end_hour and ts.hour < window.end_hour:
    return current - timedelta(days=1)
  return current


def _session_close_ts(session_date: date, window: SessionWindow) -> pd.Timestamp:
  close_date = session_date
  if window.start_hour > window.end_hour:
    close_date = session_date + timedelta(days=1)
  return pd.Timestamp.combine(
    close_date,
    time(window.end_hour, tzinfo=timezone.utc),
  )


def _trading_day(ts: pd.Timestamp, rollover_hour: int) -> date:
  if ts.hour >= rollover_hour:
    return ts.date() + timedelta(days=1)
  return ts.date()


def _daily_close_ts(day: date, rollover_hour: int) -> pd.Timestamp:
  return pd.Timestamp.combine(day, time(rollover_hour, tzinfo=timezone.utc))


def _week_start(ts: pd.Timestamp) -> pd.Timestamp:
  start_date = ts.date() - timedelta(days=ts.weekday())
  return pd.Timestamp.combine(start_date, time(0, tzinfo=timezone.utc))


def _is_high_level(name: str) -> bool:
  return name.endswith("_H") or name in {"PDH", "PWH"}
