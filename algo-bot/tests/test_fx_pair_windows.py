"""Live FX pairs use their own session-quality context while 1:2 stays locked."""

from __future__ import annotations

import pytest

from app.autotrade.killzone import (
  evaluate_instrument_session_quality,
  evaluate_reaction_publish_window,
  reaction_require_publish_window,
  session_quality_minimum_confluence,
)
from tests.test_config_effective_instrument_context import _load_production_example


pytestmark = pytest.mark.no_database


def _instrument(symbol: str):
  return _load_production_example().config.for_instrument(symbol)


def _window(symbol: str, hour: int):
  """Classify pair windows with require=True (clock math only)."""
  inst = _instrument(symbol)
  return evaluate_reaction_publish_window(
    hour=hour,
    cfg=inst,
    require=True,
  )


def test_fx_pair_windows_match_their_liquidity_focuses():
  tokyo_eur = _window("EURUSD", 1)
  tokyo_gbp = _window("GBPJPY", 1)
  assert tokyo_eur.allowed is False
  assert tokyo_gbp.allowed is False

  # Mid-Tokyo (05 UTC = 14:00 JST) stays open for USDJPY. The old 0-3 cut
  # treated the Japanese afternoon as dead air and left it London/NY-shaped.
  mid_tokyo_eur = _window("EURUSD", 5)
  mid_tokyo_gbp = _window("GBPJPY", 5)
  mid_tokyo_usd = _window("USDJPY", 5)
  assert mid_tokyo_eur.allowed is False
  assert mid_tokyo_gbp.allowed is False
  assert mid_tokyo_usd.allowed is True

  ny_eur = _window("EURUSD", 13)
  ny_gbp = _window("GBPJPY", 13)
  assert ny_eur.allowed is True
  assert ny_gbp.allowed is False

  london_eur = _window("EURUSD", 8)
  london_gbp = _window("GBPJPY", 8)
  london_usd = _window("USDJPY", 8)
  assert london_eur.allowed is True
  assert london_gbp.allowed is True
  assert london_usd.allowed is False

  ny_late_eur = _window("EURUSD", 14)
  ny_late_gbp = _window("GBPJPY", 14)
  assert ny_late_eur.allowed is True
  assert ny_late_gbp.allowed is False

  xau = _window("XAU", 13)
  assert xau.allowed is True
  assert _window("XAU", 12).allowed is False


def test_fx_pairs_keep_locked_two_r_while_windows_differ():
  eurusd = _instrument("EURUSD")
  gbpjpy = _instrument("GBPJPY")
  assert eurusd.execution.technique.reaction_publish_windows != (
    gbpjpy.execution.technique.reaction_publish_windows
  )
  assert eurusd.targeting.reward_risk == gbpjpy.targeting.reward_risk == 2.0
  assert eurusd.targeting.entry_clips == gbpjpy.targeting.entry_clips == 2


def test_fx_sessions_are_quality_context_not_hard_gates():
  """FX focus hours are good; all other hours stay selectively tradeable."""
  for symbol in ("EURUSD", "GBPUSD", "GBPJPY", "USDJPY"):
    inst = _instrument(symbol)
    assert reaction_require_publish_window(inst) is False
    assert inst.execution.technique.scalp_require_killzone is False
    assert inst.execution.technique.selective_session_min_confluence == 3

  xau = _instrument("XAU")
  assert reaction_require_publish_window(xau) is False
  assert xau.execution.technique.scalp_require_killzone is False
  assert xau.execution.technique.selective_session_min_confluence == 0


@pytest.mark.parametrize(
  "symbol,hour,score,label,minimum",
  [
    ("EURUSD", 8, 2, "good", 0),
    ("EURUSD", 1, 1, "selective", 3),
    ("GBPJPY", 8, 2, "good", 0),
    ("GBPJPY", 13, 1, "selective", 3),
    ("USDJPY", 5, 2, "good", 0),
    ("USDJPY", 8, 1, "selective", 3),
    ("XAU", 12, 1, "selective", 0),
  ],
)
def test_pair_session_quality_only_raises_the_selective_confluence_floor(
  symbol: str,
  hour: int,
  score: int,
  label: str,
  minimum: int,
):
  quality = evaluate_instrument_session_quality(
    hour=hour,
    cfg=_instrument(symbol),
  )
  assert quality.score == score
  assert quality.label == label
  assert session_quality_minimum_confluence(_instrument(symbol), quality) == minimum


def test_publish_window_still_classifies_when_require_forced():
  dead = evaluate_reaction_publish_window(
    hour=14, cfg=_instrument("GBPJPY"), require=True,
  )
  live = evaluate_reaction_publish_window(
    hour=14, cfg=_instrument("EURUSD"), require=True,
  )
  assert dead.allowed is False
  assert dead.reason_code == "outside_reaction_publish_window"
  assert live.allowed is True
  soft = evaluate_reaction_publish_window(
    hour=14, cfg=_instrument("GBPJPY"), require=False,
  )
  assert soft.allowed is True
  assert soft.measured.get("would_block") is True
