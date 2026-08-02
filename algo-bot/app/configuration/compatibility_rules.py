"""Source-aware legacy compatibility rules deferred from Phase 2B."""

from __future__ import annotations

from dataclasses import replace

from app.configuration.source_types import ResolvedConfiguration
from app.configuration.source_types import ResolvedFieldSource
from app.configuration.source_types import ResolutionConflict
from app.configuration.source_types import ResolutionTrace
from app.configuration.source_types import SourceKind


def _derived_source(
  current: ResolvedFieldSource,
  *,
  profile: str,
  rule: str,
) -> ResolvedFieldSource:
  previous = (
    f"{current.source_kind.value}:{current.source_name}",
    *current.overridden_lower_precedence_sources,
  )
  return replace(
    current,
    source_kind=SourceKind.DERIVED_COMPATIBILITY_RULE,
    source_name=rule,
    supplied_alias=None,
    explicit=False,
    overridden_lower_precedence_sources=previous,
    profile_name=profile,
    compatibility_rule=rule,
  )


def apply_compatibility_rules(
  resolved: ResolvedConfiguration,
) -> ResolvedConfiguration:
  """Apply only behavior whose result depends on source provenance."""
  values = dict(resolved.flat_values)
  traces = dict(resolved.trace.by_path())
  conflicts = list(resolved.conflicts)

  demo_requirement = "contract.account.require_demo"
  if (
    resolved.profile == "demo_eval"
    and traces[demo_requirement].explicit
    and values.get(demo_requirement) is False
  ):
    conflicts.append(ResolutionConflict(
      code="demo_account_requirement",
      path=demo_requirement,
      source_kind=traces[demo_requirement].source_kind,
      source_name=traces[demo_requirement].source_name,
      canonical_env=traces[demo_requirement].canonical_env,
      message=(
        "AUTO_TRADE_PROFILE=demo_eval requires "
        "AUTO_TRADE_REQUIRE_DEMO_ACCOUNT=true"
      ),
    ))

  structural_guard = "actionability.structural_guard.guard_mode"
  if (
    resolved.profile == "conservative"
    and values.get(demo_requirement) is False
    and not traces[structural_guard].explicit
  ):
    rule = "conservative_live_structural_guard"
    values[structural_guard] = "strict"
    traces[structural_guard] = _derived_source(
      traces[structural_guard], profile=resolved.profile, rule=rule,
    )

  mapped_zone = "strategies.mapped_zone.enabled"
  market_map_guard = "actionability.gates.market_map_guard_enabled"
  if not traces[market_map_guard].explicit:
    rule = "market_map_guard_inherits_mapped_zone"
    values[market_map_guard] = values[mapped_zone]
    traces[market_map_guard] = _derived_source(
      traces[market_map_guard], profile=resolved.profile, rule=rule,
    )

  reconciliation_enabled = "actionability.zone_reconciliation.enabled"
  reconciliation_mode = "actionability.zone_reconciliation.mode"
  if values.get(reconciliation_enabled) is False:
    rule = "disabled_zone_reconciliation_forces_off"
    values[reconciliation_mode] = "off"
    traces[reconciliation_mode] = _derived_source(
      traces[reconciliation_mode], profile=resolved.profile, rule=rule,
    )

  return replace(
    resolved,
    nested_input=_nested(values),
    flat_values=values,
    trace=ResolutionTrace(tuple(traces[path] for path in sorted(traces))),
    conflicts=tuple(conflicts),
  )


def _nested(flat_values: dict[str, object]) -> dict[str, object]:
  root: dict[str, object] = {}
  for path, value in sorted(flat_values.items()):
    cursor = root
    parts = path.split(".")
    for part in parts[:-1]:
      child = cursor.setdefault(part, {})
      if not isinstance(child, dict):
        raise ValueError(f"canonical path collision at {path}")
      cursor = child
    cursor[parts[-1]] = value
  return root
