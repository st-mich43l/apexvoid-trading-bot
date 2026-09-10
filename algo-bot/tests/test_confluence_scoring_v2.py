from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.no_database

from app.analysis.detectors import (
  CONFIRM_ENGULFING,
  CONFIRM_REJECTION_CHOCH,
  CONFIRM_STRONG_RECLAIM,
  CONFIRM_SWEEP_RECLAIM,
  CONFIRM_WICK_REJECTION,
  ConfluenceFactors,
  DetectorSettings,
  _FACTOR_SCORE_MAX,
  _MAD_SCORE_WEIGHT,
  _ZONE_QUALITY_WEIGHT,
  _ZONE_SCORE_MAX,
  _confluence_from_zone,
  _confluence_v2_score,
  _factors_for_confirmation,
  _raw_factor_score,
  _reaction_factors,
  _stars_from_v2_ratio,
  _v2_score_max,
)
from app.analysis.structure import Zone
from app.configuration.models.analysis import AnalysisConfluenceConfig
from app.persistence import store


@pytest.mark.parametrize(
  ("confirmation_type", "wick", "displacement", "structural"),
  (
    (CONFIRM_WICK_REJECTION, True, False, False),
    (CONFIRM_SWEEP_RECLAIM, False, False, True),
    (CONFIRM_REJECTION_CHOCH, False, False, False),
    (CONFIRM_STRONG_RECLAIM, False, False, True),
    (CONFIRM_ENGULFING, False, True, False),
  ),
)
def test_reaction_factor_mapping_uses_confirmation_evidence(
  confirmation_type, wick, displacement, structural,
):
  factors = _reaction_factors(
    SimpleNamespace(confirmation_type=confirmation_type),
    htf_aligned=True,
    touches=2,
    session_context=False,
  )
  assert factors.wick_rejection is wick
  assert factors.displacement_grade is displacement
  assert factors.structural_agreement is structural
  assert factors.session_context is False


def test_choch_layers_structural_agreement_and_choch_after_mapping():
  mapped = _reaction_factors(
    SimpleNamespace(confirmation_type=CONFIRM_REJECTION_CHOCH),
    htf_aligned=False,
    touches=0,
    session_context=True,
  )
  factors = _factors_for_confirmation(
    mapped, SimpleNamespace(confirmation_type=CONFIRM_REJECTION_CHOCH),
  )
  assert factors.structural_agreement is True
  assert factors.choch is True
  assert factors.session_context is True


def test_confirmation_type_changes_factor_score():
  wick = _reaction_factors(
    SimpleNamespace(confirmation_type=CONFIRM_WICK_REJECTION),
    htf_aligned=True, touches=2, session_context=True,
  )
  choch_confirmation = SimpleNamespace(confirmation_type=CONFIRM_REJECTION_CHOCH)
  choch = _factors_for_confirmation(
    _reaction_factors(
      choch_confirmation,
      htf_aligned=True, touches=2, session_context=True,
    ),
    choch_confirmation,
  )
  assert _raw_factor_score(wick) != _raw_factor_score(choch)


def test_v2_zone_quality_is_bounded_and_keeps_factors():
  factors = ConfluenceFactors(htf_aligned=True, touches=2)
  synthetic = Zone(100, 101, "demand", score=0.0)
  maximum = Zone(100, 101, "demand", score=_ZONE_SCORE_MAX)
  oversized = Zone(100, 101, "demand", score=_ZONE_SCORE_MAX * 2)

  assert _confluence_v2_score(synthetic, factors) == _raw_factor_score(factors)
  assert _confluence_v2_score(maximum, factors) == (
    _raw_factor_score(factors) + _ZONE_QUALITY_WEIGHT
  )
  assert _confluence_v2_score(oversized, factors) == _confluence_v2_score(
    maximum, factors,
  )


def test_v2_allows_three_stars_for_touched_zone_without_changing_v1_cap():
  factors = ConfluenceFactors(
    htf_aligned=True, touches=3, wick_rejection=True,
    displacement_grade=True, session_context=True, structural_agreement=True,
  )
  zone = Zone(100, 101, "demand", score=_ZONE_SCORE_MAX, touches=1)
  assert _confluence_from_zone(zone, factors) == 2
  assert _stars_from_v2_ratio(
    _confluence_v2_score(zone, factors) / _v2_score_max(),
  ) == 3


def test_mad_is_bounded_raw_score_contribution():
  zone = Zone(100, 101, "demand", score=0.0)
  factors = ConfluenceFactors()
  assert _confluence_v2_score(zone, factors, mad_bonus=1.0) == _MAD_SCORE_WEIGHT
  assert _v2_score_max() == _FACTOR_SCORE_MAX + _ZONE_QUALITY_WEIGHT + _MAD_SCORE_WEIGHT


def test_v2_confluence_config_rejects_invalid_cut_order_and_version():
  with pytest.raises(ValueError):
    AnalysisConfluenceConfig(v2_star_two_ratio=0.585, v2_star_three_ratio=0.585)
  with pytest.raises(ValueError):
    AnalysisConfluenceConfig(scoring_version="v3")


def test_v2_settings_are_configurable_without_affecting_default_v1():
  settings = DetectorSettings()
  assert settings.confluence_scoring_version == "v1"
  assert settings.confluence_v2_star_two_ratio < settings.confluence_v2_star_three_ratio


@pytest.mark.asyncio
async def test_fill_write_persists_all_shadow_confluence_fields(monkeypatch):
  calls = []

  class Connection:
    async def execute(self, query, *args):
      calls.append((query, args))

  @asynccontextmanager
  async def connect():
    yield Connection()

  monkeypatch.setattr(store, "_connect", connect)
  await store._record_auto_trade_fill({
    "type": "order_filled",
    "position_id": 42,
    "group_id": "group-42",
    "stream": "algo_auto",
    "symbol": "XAU",
    "setup": "Trend Pullback",
    "direction": "BUY",
    "price": 4000.0,
    "volume": 1000,
    "timestamp": 1,
    "confluence_v1": 2,
    "confluence_v2": 3,
    "confluence_v2_raw": 15.25,
    "confluence_scoring_version": "v1",
  })
  query, args = calls[-1]
  assert "confluence_v2_raw" in query
  # Positional, not trailing: candle_* telemetry fields append after these
  # in the same VALUES tuple, so confluence stays at its own fixed slot.
  assert args[12:16] == (2, 3, 15.25, "v1")
