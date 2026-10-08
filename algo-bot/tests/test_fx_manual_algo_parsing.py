"""FX owner manual /algo parsing and contract defaults."""

from __future__ import annotations

import pytest

from app.signals.fx_manual_algo import build_fx_manual_contract
from app.signals.parsing import _parse_manual
from tests.support.canonical_fixtures import _load_production_example


pytestmark = pytest.mark.no_database


@pytest.fixture(autouse=True)
def _production_config(monkeypatch):
  cfg = _load_production_example().config
  for target in (
    "app.core.config.runtime_config",
    "app.core.symbols.runtime_config",
    "app.signals.parsing.runtime_config",
    "app.signals.fx_manual_algo.runtime_config",
  ):
    monkeypatch.setattr(target, cfg, raising=False)


def test_eurusd_sell_without_algo_suffix_is_notify_only():
  parsed = _parse_manual("eurusd sell 1.15007")

  assert parsed is not None
  assert parsed["action"] == "SELL"
  assert parsed["execution_mode"] == "notify"
  assert parsed["setup_type"] == "key-level"


def test_fx_entry_zone_range_is_not_used():
  parsed = _parse_manual("eurusd buy 1.15007-1.15100 / algo")

  assert parsed is not None
  assert parsed["entry"] == pytest.approx(1.15007)
  assert parsed["entry_end"] == pytest.approx(1.15007)
  assert parsed["manual_single_entry"] is True


def test_eurusd_explicit_sl_and_tp_override_defaults():
  parsed = _parse_manual(
    "eurusd buy 1.15007 / sl 1.14900 / tp 1.15100/1.15200/1.15300 / algo"
  )

  assert parsed is not None
  assert parsed["sl"] == pytest.approx(1.14900)
  assert parsed["tps"] == [
    pytest.approx(1.15100),
    pytest.approx(1.15200),
    pytest.approx(1.15300),
  ]
  assert parsed["setup_type"] == "key-level"


def test_usdjpy_buy_single_price_algo():
  parsed = _parse_manual("usdjpy buy 148.520 / algo")

  assert parsed is not None
  assert parsed["symbol"] == "USDJPY"
  assert parsed["action"] == "BUY"
  assert parsed["execution_mode"] == "algo"
  assert parsed["manual_single_entry"] is True
  assert parsed["setup_type"] == "key-level"


def test_gbpjpy_frontload_weights_from_policy():
  contract = build_fx_manual_contract("GBPJPY", "BUY", 216.168)

  assert contract["target_weights"] == [40, 25, 35]
