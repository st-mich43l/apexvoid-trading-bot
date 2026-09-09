"""Effective instrument context: parity, rollout, policy, and fail-closed rules."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.configuration.effective_instrument import (
  EffectiveInstrumentError,
  build_effective_instrument,
)
from app.configuration.models.instruments import (
  FX_FIXED_2R_V1_POLICY,
  InstrumentConfig,
  InstrumentContractConfig,
  InstrumentLookbacksConfig,
  InstrumentManualConfig,
  InstrumentManualEntryMode,
  InstrumentManualRiskReference,
  InstrumentMarketDataConfig,
  InstrumentRollout,
  InstrumentTargetMode,
  InstrumentsConfig,
  XAU_CURRENT_V1_POLICY,
  XAU_FIXED_2R_V1_POLICY,
  effective_rollout,
  resolve_manual_profile,
)
from app.configuration.python_loader import load_python_canonical_settings
from app.configuration.python_sources import load_python_runtime_source_bundle
from app.configuration.source_policy import PythonConfigurationSourcePolicy


pytestmark = pytest.mark.no_database

_CONFIG_FILE = (
  Path(__file__).resolve().parents[2] / "config" / "trading-bot.yml"
)


def _load_production_example():
  policy = PythonConfigurationSourcePolicy(config_file=str(_CONFIG_FILE))
  return load_python_canonical_settings(
    load_python_runtime_source_bundle(policy=policy),
  )


def _parity_payload(config, effective) -> dict:
  """Structured comparison surface for XAU global leaves vs effective context."""
  return {
    "canonical_symbol": (
      config.contract.instrument.canonical_symbol,
      effective.identity.canonical_symbol,
    ),
    "broker_symbol": (
      config.instruments.root["XAU"].broker_symbol,
      effective.identity.broker_symbol,
    ),
    "pip_size": (
      config.contract.instrument.pip_size,
      effective.units.pip_size,
    ),
    "price_digits": (
      config.contract.instrument.price_digits,
      effective.units.price_digits,
    ),
    "contract_units_per_lot": (
      config.contract.instrument.contract_units_per_lot,
      effective.units.contract_units_per_lot,
    ),
    "timeframes": (
      tuple(config.instruments.root["XAU"].timeframes),
      effective.identity.timeframes,
    ),
    "lookbacks": (
      config.market_data.lookbacks.model_dump(mode="python"),
      effective.market_data.lookbacks.model_dump(mode="python"),
    ),
    "zones": (
      config.analysis.zones.symbol_contract.model_dump(mode="python"),
      effective.analysis.zones.model_dump(mode="python"),
    ),
    "execution": (
      config.execution.model_dump(mode="python"),
      effective.execution.model_dump(mode="python"),
    ),
    "risk": (
      config.risk.model_dump(mode="python"),
      effective.risk.model_dump(mode="python"),
    ),
    "lifecycle": (
      config.lifecycle.model_dump(mode="python"),
      effective.lifecycle.model_dump(mode="python"),
    ),
    "strategies": (
      config.strategies.model_dump(mode="python"),
      effective.strategies.model_dump(mode="python"),
    ),
    "actionability": (
      config.actionability.model_dump(mode="python"),
      effective.actionability.model_dump(mode="python"),
    ),
  }


def test_production_yaml_xau_effective_parity():
  loaded = _load_production_example()
  cfg = loaded.config
  effective = cfg.for_instrument("XAU")
  assert effective.identity.rollout is InstrumentRollout.LIVE
  assert effective.policy_name == XAU_FIXED_2R_V1_POLICY
  assert "XAUUSD" in effective.identity.aliases
  # Structure fixed_rr pack expands execution/stop/session away from root
  # ladder defaults — parity is identity + units + non-pack domains only.
  payload = _parity_payload(cfg, effective)
  for name in (
    "pip_size",
    "price_digits",
    "contract_units_per_lot",
    "timeframes",
    "lifecycle",
  ):
    left, right = payload[name]
    assert left == right, name
  assert cfg.enabled_instruments() == ("EURUSD", "GBPJPY", "GBPUSD", "USDJPY", "XAU")
  assert cfg.live_instruments() == ("EURUSD", "GBPJPY", "GBPUSD", "USDJPY", "XAU")
  assert cfg.instrument_for_broker_symbol("xauusd").identity.canonical_symbol == "XAU"
  assert int(effective.execution.reaction.stop_min_pips) == 50
  assert int(effective.execution.reaction.stop_max_pips) == 100
  assert effective.targeting.mode is InstrumentTargetMode.FIXED_RR


def test_production_yaml_fx_live_executable_units():
  loaded = _load_production_example()
  cfg = loaded.config
  eurusd = cfg.for_instrument("EURUSD")
  gbpusd = cfg.for_instrument("GBPUSD")
  gbpjpy = cfg.for_instrument("GBPJPY")
  usdjpy = cfg.for_instrument("USDJPY")
  xau = cfg.for_instrument("XAU")
  assert eurusd.identity.rollout is InstrumentRollout.LIVE
  assert gbpjpy.identity.rollout is InstrumentRollout.LIVE
  assert eurusd.units.pip_size == 0.0001
  assert eurusd.units.price_digits == 5
  assert eurusd.units.contract_units_per_lot == 100000.0
  assert eurusd.units.pip_value_per_lot == 10.0
  assert gbpjpy.units.pip_size == 0.01
  assert gbpjpy.units.price_digits == 3
  assert gbpjpy.units.contract_units_per_lot == 100000.0
  assert gbpjpy.units.pip_value_per_lot == 7.0
  assert xau.units.pip_value_per_lot == 10.0
  assert xau.units.volume_units_per_lot == 10_000
  assert xau.units.max_lots == 10.0
  assert xau.units.plan_max_volume() == 100_000
  assert eurusd.units.volume_units_per_lot == 10_000_000
  assert gbpjpy.units.volume_units_per_lot == 10_000_000
  assert eurusd.units.plan_max_volume() == 100_000_000
  assert gbpjpy.units.plan_max_volume() == 100_000_000
  assert eurusd.policy_name == FX_FIXED_2R_V1_POLICY
  # GBPJPY keeps the historical frontload policy name; close-ratio front-load
  # was retired — same uniform 1R/2R 50/50 + BE contract as other fixed_rr.
  assert gbpjpy.policy_name == "fx_fixed_2r_frontload_v1"
  assert xau.policy_name == XAU_FIXED_2R_V1_POLICY
  assert eurusd.targeting.mode is InstrumentTargetMode.FIXED_RR
  assert gbpjpy.targeting.mode is InstrumentTargetMode.FIXED_RR
  assert eurusd.targeting.reward_risk == 2.0
  assert gbpjpy.targeting.reward_risk == 2.0
  assert eurusd.targeting.target_r_multiples == (1.0, 2.0)
  assert gbpjpy.targeting.target_r_multiples == (1.0, 2.0)
  assert eurusd.targeting.close_ratios == (0.5, 0.5)
  assert gbpjpy.targeting.close_ratios == (0.5, 0.5)
  assert eurusd.targeting.breakeven_after_r == 1.0
  assert gbpjpy.targeting.breakeven_after_r == 1.0
  assert eurusd.targeting.trail_after_r is None
  assert gbpjpy.targeting.trail_after_r is None
  assert eurusd.targeting.trail_to_r is None
  assert gbpjpy.targeting.trail_to_r is None
  assert eurusd.targeting.entry_clips == 2
  assert gbpjpy.targeting.entry_clips == 2
  assert xau.targeting.mode is InstrumentTargetMode.FIXED_RR
  # XAU deliberately diverges from the FX policies' shared 1R/2R shape onto
  # the same 4-level R ladder as manual /algo (0.5R/1R/2R/3R).
  assert xau.targeting.reward_risk == 3.0
  assert xau.targeting.target_r_multiples == (0.5, 1.0, 2.0, 3.0)
  assert xau.targeting.close_ratios == (0.4, 0.2, 0.2, 0.2)
  assert xau.targeting.breakeven_after_r == 0.5
  assert xau.targeting.trail_after_r is None
  assert xau.targeting.trail_to_r is None
  assert xau.targeting.entry_clips == 2
  assert int(xau.execution.reaction.stop_min_pips) == 50
  assert int(xau.execution.reaction.stop_max_pips) == 100
  assert float(xau.execution.stops.sl_distance) == 10.0
  # Scalp RR floor must stay 1.10 — pack must not overwrite with technique 2.0.
  assert float(xau.strategies.scalping.policy.minimum_reward_risk) == 1.10
  assert xau.execution.technique.reaction_publish_windows == "0-11,13-16"
  assert xau.manual.enabled is True
  assert xau.manual.algo_enabled is True
  assert xau.manual.entry_mode is InstrumentManualEntryMode.ZONE_LADDER
  assert xau.manual.risk_reference is InstrumentManualRiskReference.SHALLOW
  assert xau.manual.risk_multiplier == 1.0
  assert xau.manual.target_close_ratios == ()
  assert xau.manual.tp1_close_fraction == 0.4
  assert eurusd.manual.enabled is True
  assert eurusd.manual.algo_enabled is True
  assert eurusd.manual.entry_mode is InstrumentManualEntryMode.SINGLE
  assert eurusd.manual.risk_reference is InstrumentManualRiskReference.SHALLOW
  assert eurusd.manual.risk_multiplier == 1.5
  assert eurusd.manual.target_close_ratios == (0.25, 0.25, 0.50)
  assert eurusd.manual.tp1_close_fraction is None
  assert gbpjpy.manual.target_close_ratios == (0.40, 0.25, 0.35)
  assert float(eurusd.execution.range.min_rr) == 2.0
  assert int(eurusd.execution.reaction.stop_min_pips) == 10
  assert int(eurusd.execution.reaction.stop_max_pips) == 18
  assert int(gbpjpy.execution.reaction.stop_min_pips) == 15
  assert int(gbpjpy.execution.reaction.stop_max_pips) == 30
  assert float(eurusd.execution.stops.sl_distance) == 0.0018
  assert float(gbpjpy.execution.stops.sl_distance) == 0.30
  assert eurusd.analysis.zones.minimum_width_price == 0.0006
  assert gbpjpy.analysis.zones.minimum_width_price == 0.12
  assert float(eurusd.execution.mapped_zone.zone_min_width_abs) == 0.0006
  assert float(gbpjpy.execution.mapped_zone.zone_min_width_abs) == 0.12
  assert float(eurusd.risk.exposure.opposing_minimum_separation_price) == 0.0015
  assert float(gbpjpy.risk.exposure.opposing_minimum_separation_price) == 0.25
  assert int(eurusd.execution.entry.max_spread_pips) == 1
  assert int(gbpjpy.execution.entry.max_spread_pips) == 3
  assert eurusd.execution.technique.reaction_publish_windows == "7-11,13-16"
  assert gbpusd.execution.technique.reaction_publish_windows == "7-11,13-16"
  assert gbpjpy.execution.technique.reaction_publish_windows == "7-11"
  assert usdjpy.execution.technique.reaction_publish_windows == "0-7,13-16"
  for instrument in (eurusd, gbpusd, gbpjpy, usdjpy):
    assert instrument.execution.technique.reaction_require_publish_window is False
    assert instrument.execution.technique.scalp_require_killzone is False
    assert instrument.execution.technique.selective_session_min_confluence == 3
  assert eurusd.execution.technique.require_sweep_body is False
  assert gbpjpy.execution.technique.require_sweep_body is False
  assert int(eurusd.execution.activation.reaction_trigger_maximum_age_bars) == 3
  assert int(gbpjpy.execution.activation.reaction_trigger_maximum_age_bars) == 3
  assert eurusd.analysis.runtime.levels.round_step == 0.001
  assert gbpjpy.analysis.runtime.levels.round_step == 0.1
  assert eurusd.is_live()
  assert eurusd.is_executable()
  assert gbpjpy.is_live()
  assert gbpjpy.is_executable()
  assert cfg.instrument_for_broker_symbol("EURUSD").identity.canonical_symbol == "EURUSD"
  assert cfg.instrument_for_broker_symbol("GBPJPY").identity.canonical_symbol == "GBPJPY"
  assert cfg.instruments.root["EURUSD"].reaction_session == "london_ny"
  assert cfg.instruments.root["GBPJPY"].reaction_session == "london"
  assert cfg.instruments.root["USDJPY"].reaction_session == "tokyo_ny"
  assert cfg.instruments.root["EURUSD"].overrides == {
    "execution.technique.selective_session_min_confluence": 3,
  }
  # XAU's ATR is dollar-denominated, so the FX-tuned barrier_buffer_atr
  # (0.5) buffers away 40-115+ pips of real opposing-structure room -- an
  # escape-hatch override, same shape as GBPJPY/USDJPY below.
  assert cfg.instruments.root["XAU"].overrides == {
    "actionability.target_room.barrier_buffer_atr": 0.15,
  }
  # The FX session quality floor is explicit instrument ownership. GBPJPY retains
  # its event-cluster quality guard, and USDJPY its directional defended level.
  assert cfg.instruments.root["GBPJPY"].overrides == {
    "actionability.gates.event_cluster_guard_enabled": True,
    "analysis.levels.minimum_key_touches": 3,
    "strategies.reaction.key_level.require_explicit_role": True,
    "strategies.reaction.key_level.min_grade": "A",
    "execution.technique.selective_session_min_confluence": 3,
  }
  assert set(cfg.instruments.root["USDJPY"].overrides) == {
    "risk.exposure.defended_levels",
    "risk.exposure.defended_level_buffer_price",
    "execution.technique.selective_session_min_confluence",
  }
  assert cfg.instruments.root["EURUSD"].price_scale is not None
  assert cfg.instruments.root["GBPJPY"].price_scale is not None
  assert cfg.instruments.root["USDJPY"].price_scale is not None
  assert "execution.reaction.stop_min_pips" not in cfg.instruments.root["EURUSD"].overrides
  assert "execution.range.min_rr" not in cfg.instruments.root["GBPJPY"].overrides
  assert "execution.range.min_rr" not in cfg.instruments.root["USDJPY"].overrides


def test_fx_execution_pack_expands_and_explicit_override_wins():
  loaded = _load_production_example()
  cfg = loaded.config
  gbp = cfg.instruments.root["GBPJPY"]
  patched = gbp.model_copy(
    update={
      "overrides": {
        **gbp.overrides,
        "execution.reaction.stop_max_pips": 40,
      },
    },
  )
  runtime = cfg.model_copy(
    update={"instruments": InstrumentsConfig(root={
      "XAU": cfg.instruments.root["XAU"],
      "EURUSD": cfg.instruments.root["EURUSD"],
      "GBPJPY": patched,
    })},
  )
  effective = runtime.for_instrument("GBPJPY")
  assert int(effective.execution.reaction.stop_min_pips) == 15
  assert int(effective.execution.reaction.stop_max_pips) == 40
  assert int(effective.execution.stops.trend.minimum_pips) == 15
  assert int(effective.execution.range.min_target_pips) == 30
  assert float(effective.execution.range.min_rr) == 2.0


def test_manual_profile_compatibility_defaults_are_narrow():
  loaded = _load_production_example()
  cfg = loaded.config
  xau = cfg.instruments.root["XAU"].model_copy(update={"manual": None})
  eurusd = cfg.instruments.root["EURUSD"].model_copy(update={"manual": None})
  xag = _make_second_instrument(manual=None)

  xau_manual = resolve_manual_profile("XAU", xau)
  eurusd_manual = resolve_manual_profile(
    "EURUSD",
    eurusd,
    legacy_fixed_rr_risk_multiplier=1.5,
  )
  xag_manual = resolve_manual_profile("XAG", xag)

  assert xau_manual.entry_mode is InstrumentManualEntryMode.ZONE_LADDER
  assert xau_manual.risk_reference is InstrumentManualRiskReference.SHALLOW
  assert xau_manual.risk_multiplier == 1.0
  assert xau_manual.target_close_ratios == ()
  assert xau_manual.tp1_close_fraction is None
  assert eurusd_manual.entry_mode is InstrumentManualEntryMode.SINGLE
  assert eurusd_manual.risk_multiplier == 1.5
  # Compatibility path copies autonomous targeting.close_ratios when manual
  # is omitted — now the uniform 50/50 ladder.
  assert eurusd_manual.target_close_ratios == (0.5, 0.5)
  assert eurusd_manual.tp1_close_fraction is None
  assert xag_manual.enabled is False
  assert xag_manual.algo_enabled is False


def test_manual_profile_rejects_unsafe_combinations():
  empty = InstrumentManualConfig()
  assert empty.enabled is False
  assert empty.algo_enabled is False
  with pytest.raises(ValidationError, match="requires manual.enabled"):
    InstrumentManualConfig(
      enabled=False,
      algo_enabled=True,
    )
  with pytest.raises(ValidationError, match="must be finite"):
    InstrumentManualConfig(risk_multiplier=float("inf"))
  with pytest.raises(ValidationError):
    InstrumentManualConfig(tp1_close_fraction=1.0)
  with pytest.raises(ValidationError, match="must sum to 1"):
    InstrumentManualConfig(target_close_ratios=(0.25, 0.25))
  with pytest.raises(ValidationError, match="mutually exclusive"):
    InstrumentManualConfig(
      target_close_ratios=(0.5, 0.5),
      tp1_close_fraction=0.4,
    )


def test_live_fx_policy_requires_session_envelope_activation():
  loaded = _load_production_example()
  payload = loaded.config.instruments.root["EURUSD"].model_dump(mode="python")
  with pytest.raises(ValidationError, match="require reaction_session"):
    InstrumentConfig.model_validate({**payload, "reaction_session": None})
  with pytest.raises(ValidationError, match="require stop_envelope"):
    InstrumentConfig.model_validate({**payload, "stop_envelope": None})
  with pytest.raises(ValidationError, match="require activation"):
    InstrumentConfig.model_validate({**payload, "activation": None})
  with pytest.raises(ValidationError, match="require price_scale"):
    InstrumentConfig.model_validate({**payload, "price_scale": None})


def test_for_instrument_case_normalization():
  loaded = _load_production_example()
  a = loaded.config.for_instrument("xau")
  b = loaded.config.for_instrument("XAU")
  assert a.identity.canonical_symbol == b.identity.canonical_symbol
  assert a.units.model_dump() == b.units.model_dump()
  assert a is b
  assert loaded.config.for_instrument("XAUUSD") is a


def test_for_instrument_cache_is_thread_safe(monkeypatch):
  from concurrent.futures import ThreadPoolExecutor
  from threading import Lock
  from time import sleep

  from app.configuration import effective_instrument as effective_module

  cfg = _load_production_example().config
  original_builder = effective_module.build_effective_instrument
  calls = 0
  calls_lock = Lock()

  def counting_builder(*args, **kwargs):
    nonlocal calls
    with calls_lock:
      calls += 1
    # Make overlapping uncached callers deterministic; the runtime cache must
    # serialize this first composition and let every other caller reuse it.
    sleep(0.01)
    return original_builder(*args, **kwargs)

  monkeypatch.setattr(
    effective_module,
    "build_effective_instrument",
    counting_builder,
  )
  with ThreadPoolExecutor(max_workers=8) as executor:
    results = tuple(executor.map(cfg.for_instrument, ("XAU",) * 24))

  assert calls == 1
  assert all(result is results[0] for result in results)


def test_direct_lookup_cache_does_not_hide_ambiguous_alias():
  from app.configuration.effective_instrument import EffectiveInstrumentError

  cfg = _load_production_example().config
  eurusd = cfg.instruments.root["EURUSD"]
  conflicting = eurusd.model_copy(update={"aliases": ("XAUUSD",)})
  instruments = cfg.instruments.model_copy(
    update={
      "root": {
        **cfg.instruments.root,
        "EURUSD": conflicting,
      },
    },
  )
  ambiguous = cfg.model_copy(update={"instruments": instruments})

  assert ambiguous.for_instrument("EURUSD").instrument_id == "EURUSD"
  with pytest.raises(EffectiveInstrumentError, match="ambiguous symbol"):
    ambiguous.for_instrument("XAUUSD")


def test_model_copy_does_not_inherit_effective_instrument_cache():
  cfg = _load_production_example().config
  original = cfg.for_instrument("XAU")
  xau = cfg.instruments.root["XAU"]
  assert xau.contract is not None
  updated_contract = xau.contract.model_copy(update={"price_digits": 4})
  updated_xau = xau.model_copy(update={"contract": updated_contract})
  updated_instruments = cfg.instruments.model_copy(
    update={"root": {**cfg.instruments.root, "XAU": updated_xau}},
  )

  copied = cfg.model_copy(update={"instruments": updated_instruments})
  copied_effective = copied.for_instrument("XAU")
  assert copied_effective is not original
  assert copied_effective.units.price_digits == 4
  assert cfg.for_instrument("XAU") is original
  assert original.units.price_digits == 2

  deep_copied = cfg.model_copy(deep=True)
  deep_effective = deep_copied.for_instrument("XAU")
  assert deep_effective is not original
  assert deep_effective.units.price_digits == original.units.price_digits


@pytest.mark.parametrize(
  ("payload", "expected"),
  [
    ({"enabled": True}, InstrumentRollout.LIVE),
    ({"enabled": False}, InstrumentRollout.DISABLED),
    ({"rollout": "disabled"}, InstrumentRollout.DISABLED),
    ({"rollout": "feed_only"}, InstrumentRollout.FEED_ONLY),
    ({"rollout": "analysis_only"}, InstrumentRollout.ANALYSIS_ONLY),
    ({"rollout": "paper"}, InstrumentRollout.PAPER),
    ({"rollout": "live"}, InstrumentRollout.LIVE),
    ({"enabled": True, "rollout": "live"}, InstrumentRollout.LIVE),
    ({"enabled": False, "rollout": "disabled"}, InstrumentRollout.DISABLED),
  ],
)
def test_rollout_compatibility_mapping(payload, expected):
  body = {
    "canonical_symbol": "XAU",
    "broker_symbol": "XAU",
    "contract": {
      "pip_size": 0.1,
      "price_digits": 2,
      "contract_units_per_lot": 100.0,
    },
    **payload,
  }
  if expected is InstrumentRollout.DISABLED and "contract" in body:
    # disabled may omit contract
    if payload.get("enabled") is False or payload.get("rollout") == "disabled":
      pass
  instrument = InstrumentConfig.model_validate(body)
  assert effective_rollout(instrument) is expected


@pytest.mark.parametrize(
  "payload",
  [
    {"enabled": True, "rollout": "disabled"},
    {"enabled": False, "rollout": "live"},
    {"enabled": False, "rollout": "paper"},
    {"enabled": False, "rollout": "feed_only"},
  ],
)
def test_conflicting_enabled_and_rollout_fail_closed(payload):
  with pytest.raises(ValidationError):
    InstrumentConfig.model_validate(
      {
        "canonical_symbol": "XAU",
        "broker_symbol": "XAU",
        "contract": {
          "pip_size": 0.1,
          "price_digits": 2,
          "contract_units_per_lot": 100.0,
        },
        **payload,
      }
    )


def test_unknown_policy_rejected():
  with pytest.raises(ValidationError, match="unknown instrument policy"):
    InstrumentConfig.model_validate(
      {
        "enabled": True,
        "policy": "not_a_real_policy",
        "canonical_symbol": "XAU",
        "broker_symbol": "XAU",
        "contract": {
          "pip_size": 0.1,
          "price_digits": 2,
          "contract_units_per_lot": 100.0,
        },
      }
    )


def test_override_wins_and_records_provenance():
  loaded = _load_production_example()
  cfg = loaded.config
  xau = cfg.instruments.root["XAU"]
  updated = xau.model_copy(
    update={
      "overrides": {"execution.entry.maximum_chase_distance_pips": 12.5},
    },
  )
  instruments = InstrumentsConfig(root={"XAU": updated})
  runtime = cfg.model_copy(update={"instruments": instruments})
  effective = build_effective_instrument(runtime, "XAU")
  assert effective.execution.entry.maximum_chase_distance_pips == 12.5
  assert cfg.execution.entry.maximum_chase_distance_pips != 12.5
  paths = {item.path for item in effective.provenance.entries}
  assert "execution.entry.maximum_chase_distance_pips" in paths


def _make_second_instrument(**kwargs) -> InstrumentConfig:
  from app.configuration.models.instruments import (
    InstrumentAnalysisConfig,
    InstrumentZoneWidthConfig,
  )

  defaults = {
    "enabled": True,
    "rollout": InstrumentRollout.FEED_ONLY,
    "canonical_symbol": "XAG",
    "broker_symbol": "XAGUSD",
    "policy": XAU_CURRENT_V1_POLICY,
    "contract": InstrumentContractConfig(
      pip_size=0.01,
      price_digits=3,
      contract_units_per_lot=5000.0,
    ),
    "market_data": InstrumentMarketDataConfig(
      lookbacks=InstrumentLookbacksConfig(
        h1_bars=100,
        m15_bars=100,
        m5_bars=100,
        m1_bars=100,
      ),
    ),
    "analysis": InstrumentAnalysisConfig(
      zones=InstrumentZoneWidthConfig(
        minimum_width_price=0.05,
        preferred_minimum_width_price=0.05,
        preferred_maximum_width_price=0.2,
        major_maximum_width_price=0.5,
      ),
    ),
  }
  defaults.update(kwargs)
  return InstrumentConfig(**defaults)


def test_multi_instrument_feed_only_second_symbol():
  loaded = _load_production_example()
  cfg = loaded.config
  xau = cfg.instruments.root["XAU"]
  silver = _make_second_instrument()
  instruments = InstrumentsConfig(root={"XAU": xau, "XAG": silver})
  runtime = cfg.model_copy(update={"instruments": instruments})
  xau_eff = runtime.for_instrument("XAU")
  xag_eff = runtime.for_instrument("XAG")
  assert xau_eff.units.pip_size == 0.1
  assert xag_eff.units.pip_size == 0.01
  assert xag_eff.units.price_digits == 3
  assert xag_eff.identity.rollout is InstrumentRollout.FEED_ONLY
  assert not xag_eff.is_live()
  assert not xag_eff.is_executable()
  assert runtime.live_instruments() == ("XAU",)
  assert runtime.enabled_instruments() == ("XAG", "XAU")


def test_duplicate_broker_symbol_rejected():
  with pytest.raises(ValidationError, match="duplicate broker_symbol"):
    InstrumentsConfig.model_validate(
      {
        "XAU": {
          "enabled": True,
          "canonical_symbol": "XAU",
          "broker_symbol": "SAME",
          "contract": {
            "pip_size": 0.1,
            "price_digits": 2,
            "contract_units_per_lot": 100.0,
          },
        },
        "XAG": {
          "enabled": True,
          "rollout": "feed_only",
          "canonical_symbol": "XAG",
          "broker_symbol": "SAME",
          "policy": XAU_CURRENT_V1_POLICY,
          "contract": {
            "pip_size": 0.01,
            "price_digits": 3,
            "contract_units_per_lot": 5000.0,
          },
          "market_data": {
            "lookbacks": {
              "h1_bars": 100,
              "m15_bars": 100,
              "m5_bars": 100,
              "m1_bars": 100,
            },
          },
        },
      }
    )


def test_missing_and_invalid_contract_rejected():
  with pytest.raises(ValidationError):
    InstrumentConfig.model_validate(
      {
        "enabled": True,
        "canonical_symbol": "XAU",
        "broker_symbol": "XAU",
      }
    )
  with pytest.raises(ValidationError):
    InstrumentContractConfig.model_validate(
      {"pip_size": 0, "price_digits": 2, "contract_units_per_lot": 100}
    )
  with pytest.raises(ValidationError):
    InstrumentContractConfig.model_validate(
      {"pip_size": 0.1, "price_digits": -1, "contract_units_per_lot": 100}
    )
  with pytest.raises(ValidationError):
    InstrumentContractConfig.model_validate(
      {"pip_size": 0.1, "price_digits": 2, "contract_units_per_lot": 0}
    )


def test_unknown_override_path_rejected():
  loaded = _load_production_example()
  cfg = loaded.config
  xau = cfg.instruments.root["XAU"].model_copy(
    update={"overrides": {"not.a.catalog.path": 1}},
  )
  runtime = cfg.model_copy(
    update={"instruments": InstrumentsConfig(root={"XAU": xau})},
  )
  with pytest.raises(EffectiveInstrumentError, match="unknown"):
    runtime.for_instrument("XAU")


def test_secret_and_protocol_constant_overrides_rejected():
  loaded = _load_production_example()
  cfg = loaded.config
  from app.configuration.catalog import iter_catalog_entries

  secret_path = next(entry.path for entry in iter_catalog_entries() if entry.secret)
  protocol_path = next(
    entry.path
    for entry in iter_catalog_entries()
    if entry.protocol_constant or entry.kind == "protocol_constant"
  )
  for path in (secret_path, protocol_path):
    xau = cfg.instruments.root["XAU"].model_copy(
      update={"overrides": {path: "x"}},
    )
    runtime = cfg.model_copy(
      update={"instruments": InstrumentsConfig(root={"XAU": xau})},
    )
    with pytest.raises(EffectiveInstrumentError):
      runtime.for_instrument("XAU")


def test_unknown_symbol_fails_closed():
  loaded = _load_production_example()
  with pytest.raises(EffectiveInstrumentError, match="unknown instrument"):
    loaded.config.for_instrument("BTCUSD")


def test_disabled_instrument_without_contract_allowed_in_registry():
  InstrumentsConfig.model_validate(
    {
      "XAU": {
        "enabled": True,
        "canonical_symbol": "XAU",
        "broker_symbol": "XAU",
        "contract": {
          "pip_size": 0.1,
          "price_digits": 2,
          "contract_units_per_lot": 100.0,
        },
      },
      "EUR": {
        "enabled": False,
        "canonical_symbol": "EUR",
        "broker_symbol": "EURUSD",
      },
    }
  )
