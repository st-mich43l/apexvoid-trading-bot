import pytest
from types import SimpleNamespace

from app.core.symbols import pip_for
from app.signals.fx_manual_algo import build_fx_manual_contract
from app.signals.parsing import (
  DEFAULT_SETUP_TYPE,
  DEFAULT_SL_PIPS,
  DEFAULT_TP_PIPS,
  _parse_manual,
)


pytestmark = pytest.mark.no_database

PIP = pip_for("XAU")


# 2026-09 (owner-reported): manual /algo TP levels are now a bot-calculated
# 0.5R/1R/2R/3R ladder, not the pip-default DEFAULT_TP_PIPS ladder below (and
# not whatever the owner types as tp - see MANUAL_ALGO_DEFAULT_TARGET_R_MULTIPLES).
_R_MULTIPLES = (0.5, 1.0, 2.0, 3.0)


def test_owner_short_form_example_auto_fills_sl_tp_and_setup():
  # Owner's own example: "xau buy 4078-75 / algo" -> sl always 60 pips,
  # that is 4072 (entry_high 4078 - 6.0).
  parsed = _parse_manual("xau buy 4078-75 / algo")

  assert parsed is not None
  assert parsed["action"] == "BUY"
  assert parsed["entry"] == pytest.approx(4075.0)
  assert parsed["entry_end"] == pytest.approx(4078.0)
  assert parsed["sl"] == pytest.approx(4072.0)
  risk = 4078.0 - 4072.0
  assert parsed["tps"] == [
    pytest.approx(4078.0 + r * risk) for r in _R_MULTIPLES
  ]
  assert parsed["setup_type"] == DEFAULT_SETUP_TYPE
  assert parsed["execution_mode"] == "algo"


def test_owner_manual_sl_example_keeps_explicit_stop():
  # Owner's own example: "xau buy 4078-75 / sl 4070 / algo" must follow the
  # owner's stop price exactly, not the 60-pip default - the R ladder is
  # measured from that exact stop.
  parsed = _parse_manual("xau buy 4078-75 / sl 4070 / algo")

  assert parsed is not None
  assert parsed["sl"] == pytest.approx(4070.0)
  risk = 4078.0 - 4070.0
  assert parsed["tps"] == [
    pytest.approx(4078.0 + r * risk) for r in _R_MULTIPLES
  ]
  assert parsed["setup_type"] == DEFAULT_SETUP_TYPE


def test_short_form_sell_defaults_sl_above_and_tp_below_entry():
  parsed = _parse_manual("xau sell 4105-4100 / algo")

  assert parsed is not None
  assert parsed["action"] == "SELL"
  # rr_entry for SELL is entry_low (4100).
  assert parsed["sl"] == pytest.approx(4100.0 + DEFAULT_SL_PIPS * PIP)
  risk = DEFAULT_SL_PIPS * PIP
  assert parsed["tps"] == [
    pytest.approx(4100.0 - r * risk) for r in _R_MULTIPLES
  ]


def test_short_form_explicit_setup_tag_overrides_default():
  parsed = _parse_manual("xau buy 4078-75 / trend-pullback / algo")

  assert parsed is not None
  assert parsed["setup_type"] == "trend-pullback"
  assert parsed["sl"] == pytest.approx(4072.0)


def test_short_form_explicit_tp_is_ignored_in_algo_mode():
  # algo mode always uses the bot-calculated R ladder now, even when the
  # owner still types explicit tp values - same result as no tp at all.
  parsed = _parse_manual("xau buy 4078-75 / tp 88/98 / algo")

  assert parsed is not None
  assert parsed["sl"] == pytest.approx(4072.0)
  assert parsed["setup_type"] == DEFAULT_SETUP_TYPE
  risk = 4078.0 - 4072.0
  assert parsed["tps"] == [
    pytest.approx(4078.0 + r * risk) for r in _R_MULTIPLES
  ]


def test_full_form_signal_with_both_sl_and_tp_defaults_key_level():
  # Setup is always key-level unless the command tags something else —
  # SL/TP being present does not leave it untagged. The R ladder applies
  # to every manual signal, algo or notify alike (owner: "non /algo must
  # work as the same") - explicit typed tp is ignored here exactly as it
  # is in algo mode, and execution_mode stays notify since there's no
  # /algo suffix.
  parsed = _parse_manual("gold sell 4100-4105 / sl 4110 / tp 95/90/80")

  assert parsed is not None
  assert parsed["setup_type"] == DEFAULT_SETUP_TYPE
  assert parsed["sl"] == pytest.approx(4110.0)
  risk = 4110.0 - 4100.0
  assert parsed["tps"] == [
    pytest.approx(4100.0 - r * risk) for r in _R_MULTIPLES
  ]
  assert parsed["execution_mode"] == "notify"


def test_full_form_algo_defaults_setup_to_key_level():
  parsed = _parse_manual(
    "gold sell 4100-4105 / sl 4110 / tp 95/90/80 / algo"
  )

  assert parsed is not None
  assert parsed["execution_mode"] == "algo"
  assert parsed["setup_type"] == DEFAULT_SETUP_TYPE


def test_explicit_setup_overrides_default():
  parsed = _parse_manual(
    "gold sell 4100-4105 / sl 4110 / tp 95/90/80 / supply / algo"
  )

  assert parsed is not None
  assert parsed["setup_type"] == "supply"
  parsed = _parse_manual("xauusd buy 4078-75 / algo")

  assert parsed is not None
  assert parsed["action"] == "BUY"
  assert parsed["sl"] == pytest.approx(4072.0)


def test_gbpjpy_frontload_weights_from_manual_profile(monkeypatch):
  from app.core import symbols
  from app.signals import fx_manual_algo
  from tests.test_config_effective_instrument_context import _load_production_example

  config = _load_production_example().config
  monkeypatch.setattr(
    fx_manual_algo,
    "runtime_config",
    config,
  )
  monkeypatch.setattr(symbols, "runtime_config", config)
  contract = build_fx_manual_contract("GBPJPY", "BUY", 216.168)

  assert contract["target_weights"] == [40, 25, 35]


def test_xau_single_price_short_form():
  parsed = _parse_manual("xau sell 4100 / algo")

  assert parsed is not None
  assert parsed["action"] == "SELL"
  assert parsed["entry"] == pytest.approx(4100.0)
  assert parsed["entry_end"] == pytest.approx(4100.0)
  assert parsed["execution_mode"] == "algo"


def test_short_form_without_algo_suffix_still_auto_fills():
  parsed = _parse_manual("xau buy 4078-75")

  assert parsed is not None
  assert parsed["execution_mode"] == "notify"
  assert parsed["sl"] == pytest.approx(4072.0)
  assert parsed["setup_type"] == DEFAULT_SETUP_TYPE


def test_configured_non_xau_zone_ladder_accepts_explicit_contract(monkeypatch):
  from app.signals import parsing

  effective = SimpleNamespace(
    manual=SimpleNamespace(
      entry_mode=SimpleNamespace(value="zone_ladder"),
      target_r_multiples=(),
    ),
  )
  config = SimpleNamespace(
    live_instruments=lambda: ("XAU", "XAG"),
    for_instrument=lambda _symbol: effective,
  )
  monkeypatch.setattr(parsing, "runtime_config", config)
  monkeypatch.setattr(
    parsing,
    "pip_for",
    lambda symbol: 0.01 if symbol == "XAG" else PIP,
  )

  parsed = parsing._parse_manual(
    "xag buy 31.80-32.10 / sl 31.50 / tp 32.80/33.50 / algo"
  )

  assert parsed is not None
  assert parsed["symbol"] == "XAG"
  assert parsed["entry"] == pytest.approx(31.80)
  assert parsed["entry_end"] == pytest.approx(32.10)
  assert parsed["sl"] == pytest.approx(31.50)
  # algo mode ignores the owner-typed tp too - bot-calculated R ladder from
  # entry (32.10) and this stop (31.50), risk 0.60.
  risk = 32.10 - 31.50
  assert parsed["tps"] == [
    pytest.approx(32.10 + r * risk) for r in _R_MULTIPLES
  ]


def test_configured_non_xau_zone_ladder_requires_explicit_sl_and_tp(monkeypatch):
  from app.signals import parsing

  effective = SimpleNamespace(
    manual=SimpleNamespace(entry_mode=SimpleNamespace(value="zone_ladder")),
  )
  config = SimpleNamespace(
    live_instruments=lambda: ("XAG",),
    for_instrument=lambda _symbol: effective,
  )
  monkeypatch.setattr(parsing, "runtime_config", config)

  assert parsing._parse_manual("xag buy 31.80-32.10 / algo") is None
