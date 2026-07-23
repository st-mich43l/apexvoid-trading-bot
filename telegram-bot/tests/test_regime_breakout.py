import pandas as pd

from app.analysis.engine import AnalysisSettings, Regime, regime
from app.analysis.types import DealingRange
from app.analysis.regime import accepted_box_break


def _df(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close"],
    index=pd.date_range("2026-07-10", periods=len(rows), freq="5min", tz="UTC"),
  ).assign(volume=100)


def test_regime_marks_contracting_window_as_coiling():
  contracting = _df([
    (105, 110, 100, 105),
    (105, 109, 101, 105),
    (105, 108, 102, 105),
    (105, 107, 103, 105),
    (105, 106.5, 103.5, 105),
    (105, 106, 104, 105),
  ])
  steady = _df([(105, 110, 100, 105)] * 6)
  range_ = DealingRange(110, 100, 105, 0.5, "eq")
  cfg = AnalysisSettings(chop_lookback=6, coil_contract=0.8)

  assert regime(contracting, 1.0, [], "range", range_, cfg).coiling is True
  assert regime(steady, 1.0, [], "range", range_, cfg).coiling is False


def test_displacement_close_accepts_box_break_immediately():
  df = _df([
    (105, 106, 104, 105),
    (105, 106, 104, 105),
    (109.2, 112, 109, 111.5),
  ])

  result = accepted_box_break(
    df,
    1.0,
    Regime("chop", 110, 100, 3.0, [], True),
    AnalysisSettings(),
  )

  assert result is not None
  assert result.direction == "up"
  assert result.accept_index == 2
  assert result.acceptance == "displacement"
  assert result.coiling is True


def test_two_weak_closes_accept_but_single_reentry_does_not():
  accepted = _df([
    (105, 106, 104, 105),
    (110.15, 110.35, 110.1, 110.2),
    (110.2, 110.45, 110.15, 110.3),
  ])
  rejected = _df([
    (105, 106, 104, 105),
    (110.15, 110.35, 110.1, 110.2),
    (110.2, 110.3, 109.8, 109.9),
  ])
  box = Regime("chop", 110, 100, 3.0, [], False)
  cfg = AnalysisSettings(breakout_accept_bars=2)

  result = accepted_box_break(accepted, 1.0, box, cfg)

  assert result is not None
  assert result.accept_index == 2
  assert result.acceptance == "2 closes"
  assert accepted_box_break(rejected, 1.0, box, cfg) is None
