"""Pure volatility ruler wrapper over pandas-ta."""

import pandas as pd
import pandas_ta as ta


def atr(df: pd.DataFrame, length: int) -> pd.Series:
  return ta.atr(df["high"], df["low"], df["close"], length=length)
