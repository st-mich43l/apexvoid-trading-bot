"""Pure indicator wrappers over pandas-ta."""

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
