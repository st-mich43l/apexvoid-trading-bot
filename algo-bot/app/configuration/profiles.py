"""Immutable canonical configuration profiles for the inactive root model."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ProfileAssignment:
  path: str
  value: object


@dataclass(frozen=True, slots=True)
class ConfigProfile:
  name: str
  assignments: tuple[ProfileAssignment, ...]

  def __post_init__(self) -> None:
    paths = tuple(item.path for item in self.assignments)
    if len(paths) != len(set(paths)):
      raise ValueError(f"profile {self.name!r} contains duplicate paths")
    if paths != tuple(sorted(paths)):
      raise ValueError(f"profile {self.name!r} assignments must be sorted")


def _profile(name: str, assignments: tuple[tuple[str, object], ...]) -> ConfigProfile:
  return ConfigProfile(
    name=name,
    assignments=tuple(
      ProfileAssignment(path=path, value=value)
      for path, value in sorted(assignments)
    ),
  )


CONSERVATIVE_PROFILE = _profile("conservative", ())

DEMO_EVAL_PROFILE = _profile("demo_eval", (
  ("actionability.counter_bias.allowed", True),
  ("actionability.gates.market_map_guard_enabled", True),
  ("actionability.gates.opposing_barrier_veto_enabled", False),
  ("actionability.overlapping_zones.veto_enabled", False),
  ("actionability.structural_guard.guard_mode", "observe"),
  ("actionability.zone_reconciliation.mode", "shadow"),
  ("contract.account.require_demo", True),
  ("delivery.scanner_cards.top_n", 0),
  ("execution.mapped_zone.hard_entry_drift_pips", 20.0),
  ("execution.mapped_zone.max_entry_drift_atr", 1.0),
  ("execution.mapped_zone.min_entry_drift_pips", 10.0),
  ("execution.policy.structural_reaction_lookback_bars", 3),
  ("execution.range.hard_entry_drift_pips", 20.0),
  ("execution.range.max_entry_drift_atr", 1.0),
  ("execution.range.min_entry_drift_pips", 10.0),
  ("execution.trend.hard_entry_drift_pips", 30.0),
  ("execution.trend.max_entry_drift_atr", 1.5),
  ("execution.trend.min_entry_drift_pips", 15.0),
  ("execution.zone_scaling.fill_enabled", True),
  ("lifecycle.candidate.execution_maximum_age_seconds", 420),
  ("lifecycle.candidate.storage_ttl_seconds", 604800),
  ("lifecycle.zone.cooldown_enabled", False),
  ("risk.exposure.allow_concurrent_strategies", True),
  ("risk.exposure.allow_hedged_xau", True),
  ("risk.exposure.non_hedged_opposite_policy", "broker_netting"),
  ("risk.exposure.require_flat_for_range", False),
  ("risk.position_limits.max_tracked_candidates", 0),
  ("risk.position_limits.maximum_per_symbol", 0),
  ("runtime.auto_trade.dry_run", False),
  ("runtime.auto_trade.enabled", True),
  ("runtime.auto_trade.strategy_match_enabled", True),
  ("strategies.breakout.breakout_enabled", True),
  ("strategies.mapped_zone.counter_bias_enabled", True),
  ("strategies.mapped_zone.enabled", True),
  ("strategies.matching.multiple_matches_enabled", True),
  ("strategies.matching.track_all_structural_matches", True),
  ("strategies.range_reversion.enabled", True),
  ("strategies.range_reversion.flip_enabled", True),
  ("strategies.range_reversion.two_sided_enabled", True),
  ("strategies.reaction.demand.enabled", True),
  ("strategies.reaction.enabled", True),
  ("strategies.reaction.key_level.enabled", True),
  ("strategies.reaction.liquidity_reversal.enabled", True),
  ("strategies.reaction.session_level.enabled", True),
  ("strategies.reaction.supply.enabled", True),
  ("strategies.reaction.trendline.enabled", True),
  ("strategies.selection.retest_enabled", True),
  ("strategies.trend.enabled", True),
))

PROFILES: Mapping[str, ConfigProfile] = MappingProxyType({
  CONSERVATIVE_PROFILE.name: CONSERVATIVE_PROFILE,
  DEMO_EVAL_PROFILE.name: DEMO_EVAL_PROFILE,
})


def profile_fingerprint(profile: ConfigProfile) -> str:
  payload: list[dict[str, Any]] = [
    {"path": assignment.path, "value": assignment.value}
    for assignment in profile.assignments
  ]
  encoded = json.dumps(
    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
  ).encode("utf-8")
  return sha256(encoded).hexdigest()


def get_profile(name: str) -> ConfigProfile:
  normalized = name.strip().lower()
  try:
    return PROFILES[normalized]
  except KeyError as exc:
    raise ValueError(
      "AUTO_TRADE_PROFILE must be conservative or demo_eval"
    ) from exc
