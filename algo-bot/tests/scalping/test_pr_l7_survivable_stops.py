from types import SimpleNamespace

import pandas as pd
import pytest

from app.analysis.types import Leg, Level, Zone
from app.scalping.models import CONTEXT_VERSION, ScalpContextSnapshot
from app.scalping.strategies import _stop_buffer, discover_impulse_pullback
from app.scalping.unified_context import _m1_atr
from app.scalping.microstructure import detect_impulse_pullback


pytestmark = pytest.mark.no_database


def _cfg():
  scalp = SimpleNamespace(
    mode="shadow",
    archetypes=SimpleNamespace(
      impulse_pullback_enabled=True,
      pullback_extreme_confirm_bars=2,
      impulse_displacement_atr_multiple=4.0,
      impulse_body_dominance=0.5,
      pullback_corrective_ratio=0.7,
    ),
    location=SimpleNamespace(
      pullback_buy_maximum_position=0.75,
      pullback_sell_minimum_position=0.25,
      level_proximity_atr_multiple=1.0,
    ),
    target=SimpleNamespace(minimum_net_target_pips=15.0),
    stop=SimpleNamespace(
      minimum_pips=12.0,
      maximum_pips=30.0,
      buffer_m1_atr_multiple=1.2,
      buffer_minimum_spread_multiple=1.5,
      zone_maximum_atr_multiple=1.5,
    ),
    policy=SimpleNamespace(maximum_spread_pips=5.0),
  )
  return SimpleNamespace(strategies=SimpleNamespace(scalping=scalp))


def _context(*, zones=(), levels=(), closes=()):
  return ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="l7-context",
    symbol="XAU",
    created_at=1_780_000_000,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_000_000,
    htf_bias="up",
    m5_structure="bullish",
    regime="trend",
    dealing_range_low=90.0,
    dealing_range_high=120.0,
    dealing_range_position=0.2,
    active_range_low=None,
    active_range_high=None,
    active_range_eq=None,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=1000.0,
    sell_corridor_room_pips=1000.0,
    session="london",
    permitted_archetypes=("impulse_pullback",),
    atr=5.0,
    m1_atr=1.0,
    key_levels=tuple(levels),
    zones=tuple(zones),
    measured={"m5_closes": list(closes)},
  )


def _event():
  return {
    "pattern": "impulse_pullback",
    "direction": "BUY",
    "bar_ts": 1_780_000_000,
    "origin": 90.0,
    "extreme": 110.0,
    "pullback_extreme": 99.8,
    "retracement": 0.5,
    "preferred": True,
    "close": 105.0,
    "impulse_bars": 5,
    "pullback_bars": 4,
    "impulse_len": 20.0,
    "body_dominance": 0.8,
    "mean_impulse_body": 2.0,
    "mean_pullback_body": 0.5,
  }


def test_m1_atr_uses_true_range_gap_component():
  rows = [
    {"high": 100.0, "low": 99.0, "close": 99.5}
    for _ in range(15)
  ]
  rows[-1] = {"high": 110.0, "low": 109.0, "close": 109.5}
  frame = pd.DataFrame(rows)
  assert _m1_atr(frame, pip_size=0.1) > 1.0


def test_scalp_structure_uses_canonical_m5_structure_without_trendlines(monkeypatch):
  import app.analysis.engine as engine

  level = Level(price=100.0, kind="reaction", touches=3, band=0.2, strength=4.0)
  zone = Zone(bottom=99.0, top=100.5, side="demand", source="sd")
  calls = []

  monkeypatch.setattr(engine, "atr_series", lambda *args: calls.append("atr") or pd.Series([1.0]))
  monkeypatch.setattr(engine, "find_swings", lambda *args: calls.append("swings") or [])
  monkeypatch.setattr(engine, "key_levels", lambda *args: calls.append("levels") or [level])
  monkeypatch.setattr(engine, "displacement", lambda *args: calls.append("legs") or [Leg(0, 1, "BUY", 2.0)])
  monkeypatch.setattr(engine, "supply_demand", lambda *args: calls.append("zones") or [zone])
  monkeypatch.setattr(engine, "mark_mitigation", lambda *args: calls.append("mitigation") or [zone])
  monkeypatch.setattr(
    engine,
    "find_trendlines",
    lambda *args: pytest.fail("scalp_structure must not invoke trendlines"),
  )

  structure = engine.scalp_structure(pd.DataFrame({"close": [100.0]}))

  assert structure.key_levels == (level,)
  assert structure.zones == (zone,)
  assert calls == ["atr", "swings", "levels", "legs", "zones", "mitigation"]


def test_stop_buffer_has_spread_floor():
  context = SimpleNamespace(m1_atr=0.1)
  cfg = _cfg()
  assert _stop_buffer(context, cfg, 0.1) >= 0.75
  assert _stop_buffer(SimpleNamespace(m1_atr=3.0), cfg, 0.1) == pytest.approx(3.6)


def test_trigger_bar_extreme_is_unconfirmed():
  rows = []
  for i in range(10):
    rows.append({
      "open": 100.0 if i == 0 else 106.0,
      "high": 100.5 if i == 0 else 110.0 if i == 5 else 108.0,
      "low": 100.0 if i == 0 else 106.0,
      "close": 100.5 if i == 0 else 106.0,
    })
  rows[-1] = {"open": 105.0, "high": 108.0, "low": 104.0, "close": 106.0}
  result = detect_impulse_pullback(
    pd.DataFrame(rows), direction="BUY", pullback_extreme_confirm_bars=2,
  )
  assert result is not None
  assert result["reason"] == "pullback_extreme_unconfirmed"


def test_impulse_pullback_anchors_to_demand_zone(monkeypatch):
  monkeypatch.setattr(
    "app.scalping.strategies._detect_impulse",
    lambda *_args, **_kwargs: _event(),
  )
  zone = {
    "bottom": 99.0,
    "top": 100.0,
    "side": "demand",
    "touches": 4,
    "mitigated": False,
    "score": 8.0,
  }
  found = discover_impulse_pullback(
    _context(zones=(zone,), closes=(101.0, 102.0)),
    None,
    pd.DataFrame(),
    _cfg(),
    pip_size=0.1,
    now=1_780_000_000,
  )
  assert len(found) == 1
  opportunity = found[0]
  assert opportunity.key_level == 99.0
  assert opportunity.key_level != opportunity.trigger_price
  assert (opportunity.zone_low, opportunity.zone_high) == (99.0, 100.0)
  assert opportunity.key_level_role == "support"
  assert opportunity.measured["level_kind"] == "zone"


def test_impulse_pullback_without_reference_is_rejected(monkeypatch):
  monkeypatch.setattr(
    "app.scalping.strategies._detect_impulse",
    lambda *_args, **_kwargs: _event(),
  )
  reasons = []
  found = discover_impulse_pullback(
    _context(closes=(101.0, 102.0)),
    None,
    pd.DataFrame(),
    _cfg(),
    pip_size=0.1,
    now=1_780_000_000,
    idle_reasons=reasons,
  )
  assert found == []
  assert "impulse_pullback:impulse_no_level_reference" in reasons


def test_weak_impulse_and_impulsive_pullback_are_rejected(monkeypatch):
  weak = _event()
  weak["impulse_len"] = 3.0
  reasons = []
  monkeypatch.setattr(
    "app.scalping.strategies._detect_impulse",
    lambda *_args, **_kwargs: weak,
  )
  assert discover_impulse_pullback(
    _context(closes=(101.0, 102.0)), None, pd.DataFrame(), _cfg(),
    pip_size=0.1, now=1_780_000_000, idle_reasons=reasons,
  ) == []
  assert "impulse_pullback:impulse_no_displacement" in reasons

  corrective = _event()
  corrective["mean_pullback_body"] = corrective["mean_impulse_body"]
  reasons = []
  monkeypatch.setattr(
    "app.scalping.strategies._detect_impulse",
    lambda *_args, **_kwargs: corrective,
  )
  assert discover_impulse_pullback(
    _context(closes=(101.0, 102.0)), None, pd.DataFrame(), _cfg(),
    pip_size=0.1, now=1_780_000_000, idle_reasons=reasons,
  ) == []
  assert "impulse_pullback:pullback_not_corrective" in reasons


def test_broken_demand_and_wide_zone_are_rejected(monkeypatch):
  monkeypatch.setattr(
    "app.scalping.strategies._detect_impulse",
    lambda *_args, **_kwargs: _event(),
  )
  broken = {
    "bottom": 99.0, "top": 100.0, "side": "demand",
    "touches": 4, "mitigated": False, "score": 8.0,
  }
  reasons = []
  assert discover_impulse_pullback(
    _context(zones=(broken,), closes=(98.0, 97.0)), None, pd.DataFrame(),
    _cfg(), pip_size=0.1, now=1_780_000_000, idle_reasons=reasons,
  ) == []
  assert "impulse_pullback:impulse_level_role_mismatch" in reasons

  wide = {**broken, "top": 102.0}
  reasons = []
  assert discover_impulse_pullback(
    _context(zones=(wide,), closes=(103.0, 104.0)), None, pd.DataFrame(),
    _cfg(), pip_size=0.1, now=1_780_000_000, idle_reasons=reasons,
  ) == []
  assert "impulse_pullback:impulse_zone_too_wide" in reasons
