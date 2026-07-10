"""Pure indicator wrappers over pandas-ta plus local WAE composition."""

import pandas as pd
import pandas_ta as ta


def ema(df: pd.DataFrame, length: int) -> pd.Series:
  return ta.ema(df["close"], length=length)


def atr(df: pd.DataFrame, length: int) -> pd.Series:
  return ta.atr(df["high"], df["low"], df["close"], length=length)


def mfi(df: pd.DataFrame, length: int) -> pd.Series:
  return ta.mfi(
    df["high"],
    df["low"],
    df["close"],
    df["volume"],
    length=length,
  )


def bbands(df: pd.DataFrame, length: int, mult: float) -> pd.DataFrame:
  raw = ta.bbands(df["close"], length=length, std=mult)
  lower = raw[next(col for col in raw.columns if col.startswith("BBL_"))]
  middle = raw[next(col for col in raw.columns if col.startswith("BBM_"))]
  upper = raw[next(col for col in raw.columns if col.startswith("BBU_"))]
  return pd.DataFrame({
    "lower": lower,
    "middle": middle,
    "upper": upper,
    "bandwidth": upper - lower,
  }, index=df.index)


def wae(
  df: pd.DataFrame,
  fast: int,
  slow: int,
  sensitivity: float,
  bb_length: int,
  bb_mult: float,
) -> pd.DataFrame:
  """Compose Waddah Attar Explosion v2 from MACD delta, BB width, and ATR."""
  macd = ema(df, fast) - ema(df, slow)
  trend = (macd - macd.shift(1)) * sensitivity
  bands = bbands(df, bb_length, bb_mult)
  dead_zone = atr(df, bb_length)
  return pd.DataFrame({
    "trend_up": trend.where(trend > 0, 0),
    "trend_down": (-trend).where(trend < 0, 0),
    "explosion": bands["bandwidth"],
    "dead_zone": dead_zone,
  }, index=df.index)
