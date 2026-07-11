import pandas as pd
import pytest

from app import indicators


def _frame(closes: list[float]) -> pd.DataFrame:
  index = pd.date_range("2026-07-10", periods=len(closes), freq="5min", tz="UTC")
  close = pd.Series(closes, index=index)
  return pd.DataFrame({
    "open": close,
    "high": close + 1,
    "low": close - 1,
    "close": close,
    "volume": 100,
  }, index=index)


def test_atr_matches_small_hand_fixture():
  df = _frame([1, 2, 3, 4, 5])

  atr = indicators.atr(df, 3)

  assert atr.dropna().iloc[-1] == pytest.approx(2.0)


def test_indicators_exposes_only_atr():
  public = {
    name
    for name in dir(indicators)
    if not name.startswith("_") and callable(getattr(indicators, name))
  }

  assert public == {"atr"}
