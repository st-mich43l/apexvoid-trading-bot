"""Owner /1r personal-trade suffix: single entry, single 1R TP, DM-only."""

from __future__ import annotations

import pytest

from app.core.symbols import pip_for
from app.signals.parsing import DEFAULT_SETUP_TYPE, DEFAULT_SL_PIPS, _parse_manual


pytestmark = pytest.mark.no_database

PIP = pip_for("XAU")


def test_xau_zone_1r_collapses_entry_to_conservative_edge_and_books_one_r():
  # Owner's example shape: "xau buy 4285-82 / 1r" - entry_high (4285) is the
  # conservative BUY edge (rr_entry), same edge the default ladder already
  # measures from; /1r collapses the zone to exactly that single price.
  parsed = _parse_manual("xau buy 4285-82 / 1r")

  assert parsed is not None
  assert parsed["entry"] == pytest.approx(4285.0)
  assert parsed["entry_end"] == pytest.approx(4285.0)
  assert parsed["sl"] == pytest.approx(4285.0 - DEFAULT_SL_PIPS * PIP)
  risk = DEFAULT_SL_PIPS * PIP
  assert parsed["tps"] == [pytest.approx(4285.0 + risk)]
  assert parsed["personal_trade"] is True
  assert parsed["execution_mode"] == "algo"
  assert parsed["setup_type"] == DEFAULT_SETUP_TYPE


def test_xau_sell_1r_targets_below_entry():
  parsed = _parse_manual("xau sell 4105-4100 / 1r")

  assert parsed is not None
  # rr_entry for SELL is entry_low (4100).
  assert parsed["entry"] == pytest.approx(4100.0)
  assert parsed["entry_end"] == pytest.approx(4100.0)
  risk = DEFAULT_SL_PIPS * PIP
  assert parsed["tps"] == [pytest.approx(4100.0 - risk)]
  assert parsed["personal_trade"] is True


def test_1r_overrides_explicit_tp():
  # /1r is itself the explicit exit instruction - it wins even over a tp the
  # owner also typed in the same message.
  parsed = _parse_manual("xau buy 4078-75 / tp 88/98 / 1r")

  assert parsed is not None
  risk = 4078.0 - 4073.0  # default 50p sl from entry_high 4078
  assert parsed["tps"] == [pytest.approx(4078.0 + risk)]
  assert parsed["personal_trade"] is True


def test_1r_respects_explicit_sl():
  parsed = _parse_manual("xau buy 4078-75 / sl 4070 / 1r")

  assert parsed is not None
  assert parsed["sl"] == pytest.approx(4070.0)
  risk = 4078.0 - 4070.0
  assert parsed["tps"] == [pytest.approx(4078.0 + risk)]


def test_1r_alone_arms_execution_without_algo_suffix():
  parsed = _parse_manual("xau buy 4078 / 1r")

  assert parsed is not None
  assert parsed["execution_mode"] == "algo"
  assert parsed["personal_trade"] is True


def test_1r_composes_with_algo_idempotently():
  parsed = _parse_manual("xau buy 4078 / algo / 1r")

  assert parsed is not None
  assert parsed["execution_mode"] == "algo"
  assert parsed["personal_trade"] is True


def test_without_1r_personal_trade_is_false():
  parsed = _parse_manual("xau buy 4078-75 / algo")

  assert parsed is not None
  assert parsed["personal_trade"] is False


def test_1r_single_price_entry_is_unaffected_by_collapse():
  parsed = _parse_manual("xau sell 4100 / 1r")

  assert parsed is not None
  assert parsed["entry"] == pytest.approx(4100.0)
  assert parsed["entry_end"] == pytest.approx(4100.0)
  assert parsed["personal_trade"] is True


class TestFxManual1R:
  @pytest.fixture(autouse=True)
  def _production_config(self, monkeypatch):
    from tests.test_config_effective_instrument_context import (
      _load_production_example,
    )

    cfg = _load_production_example().config
    for target in (
      "app.core.config.runtime_config",
      "app.core.symbols.runtime_config",
      "app.signals.parsing.runtime_config",
      "app.signals.fx_manual_algo.runtime_config",
    ):
      monkeypatch.setattr(target, cfg, raising=False)

  def test_fx_1r_books_single_target_at_one_r(self):
    parsed = _parse_manual("eurusd buy 1.15007 / 1r")

    assert parsed is not None
    assert parsed["entry"] == pytest.approx(1.15007)
    assert parsed["entry_end"] == pytest.approx(1.15007)
    assert parsed["personal_trade"] is True
    assert parsed["execution_mode"] == "algo"
    risk = 1.15007 - 1.14867  # default fixed-R/R sl, per existing fixture
    assert parsed["tps"] == [pytest.approx(1.15007 + risk)]
    assert parsed["target_weights"] == [100]

  def test_fx_1r_sell_direction(self):
    parsed = _parse_manual("eurusd sell 1.15007 / 1r")

    assert parsed is not None
    assert parsed["tps"][0] < parsed["entry"]
    assert len(parsed["tps"]) == 1
