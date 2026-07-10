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


def test_ema_and_atr_match_small_hand_fixture():
  df = _frame([1, 2, 3, 4, 5])

  ema = indicators.ema(df, 3)
  assert ema.iloc[:2].isna().all()
  assert ema.iloc[2] == pytest.approx(2.0)
  assert ema.iloc[3] == pytest.approx(3.0)
  assert ema.iloc[4] == pytest.approx(4.0)

  atr = indicators.atr(df, 3)
  assert atr.dropna().iloc[-1] == pytest.approx(2.0)


def test_bbands_exposes_normalized_columns():
  closes = [100 + i * 0.2 + i * i * 0.01 for i in range(80)]
  df = _frame(closes)

  bands = indicators.bbands(df, length=10, mult=2)

  assert list(bands.columns) == ["lower", "middle", "upper", "bandwidth"]
  assert bands["upper"].dropna().iloc[-1] > bands["middle"].dropna().iloc[-1]
  assert bands["middle"].dropna().iloc[-1] > bands["lower"].dropna().iloc[-1]
  assert bands["bandwidth"].dropna().iloc[-1] > 0
