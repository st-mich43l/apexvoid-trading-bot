"""S13 classified dependency inventory for ``app.analysis`` / ``app.scalping``.

Builds on ``s13_legacy_dependency_audit`` (static import edges) and adds what
S13A explicitly said it could not prove:

* every legacy module has a reviewed *role* (technical detection, execution
  policy input, presentation, manual trading, research, compatibility, ...);
* every importer -> legacy edge is classified by the role of what it imports
  and by the role of the importing production module;
* modules that are not reachable from any real process entrypoint are listed;
* dynamic imports, string module paths, startup tasks and Redis/Kafka
  contract names are scanned separately, because a static import graph cannot
  see them.

Run from ``algo-bot``::

    python s13_legacy_classification.py            # JSON
    python s13_legacy_classification.py --markdown # review table

Static analysis only. It never imports project modules, and it is evidence for
a deletion review, not a substitute for one: reachability is *static*, and a
module reported reachable may still be dead behind a runtime flag.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

from s13_legacy_dependency_audit import (
  LEGACY_PACKAGES,
  _imported_modules,
  _legacy_package,
  inventory_legacy_dependencies,
)

# Roles. "retain_*" roles are not technical detection and must survive S13;
# "replace_*" roles are what the Go Analysis Engine supersedes.
TECHNICAL = "technical_detection"
TECHNICAL_ORCHESTRATION = "technical_orchestration"
SHARED_MATH = "technical_shared_math"
MARKET_DATA = "market_data_access"
SHARED_TYPES = "shared_types"
POLICY_INPUT = "execution_policy_input"
PRESENTATION = "presentation"
ACCOUNTING = "outcome_accounting"
RESEARCH = "research"
COMPAT = "compatibility"
MARKER = "package_marker"

ROLE_RETIREMENT = {
  TECHNICAL: "replace_with_go",
  TECHNICAL_ORCHESTRATION: "replace_with_go",
  SHARED_MATH: "replace_with_go_facts",
  MARKET_DATA: "retain_until_no_python_ohlc_consumer",
  SHARED_TYPES: "retain_until_policy_consumes_go_facts",
  POLICY_INPUT: "retain",
  PRESENTATION: "retain",
  ACCOUNTING: "retain",
  RESEARCH: "retain_offline",
  COMPAT: "delete_with_target",
  MARKER: "retain",
}

# module -> (role(s), reviewed reason). A tuple of roles means the module
# genuinely mixes concerns; the first role is the dominant one.
MODULE_ROLES: dict[str, tuple[tuple[str, ...], str]] = {
  "app.analysis": ((MARKER,), "package marker"),
  "app.analysis.actionability": ((TECHNICAL,), "range bounds / actionability gates over Python context"),
  "app.analysis.bar_event_dispatcher": ((TECHNICAL_ORCHESTRATION,), "startup task; drives scanner and scalping per closed bar"),
  "app.analysis.candle_displacement": ((TECHNICAL,), "candle geometry detector"),
  "app.analysis.candle_evidence": ((TECHNICAL,), "candle evidence detector"),
  "app.analysis.candle_geometry": ((TECHNICAL,), "candle geometry primitives"),
  "app.analysis.candle_rejection": ((TECHNICAL,), "rejection detector"),
  "app.analysis.candle_sequences": ((TECHNICAL,), "sequence detector"),
  "app.analysis.confluence_zone": ((TECHNICAL, POLICY_INPUT), "zone merge (technical) plus claim/release identity used by execution ownership"),
  "app.analysis.dealing_range": ((TECHNICAL,), "dealing range / premium-discount"),
  "app.analysis.detectors": ((TECHNICAL,), "LIVE_DETECTOR_REGISTRY: the legacy strategy detectors"),
  "app.analysis.engine": ((TECHNICAL_ORCHESTRATION,), "analyze()/scalp_structure(): per-timeframe technical pipeline"),
  "app.analysis.entry_location": ((TECHNICAL, POLICY_INPUT), "entry location decision; config also parses PD archetypes from it"),
  "app.analysis.execution_eligibility": ((POLICY_INPUT,), "ExecutionEligibility dataclass consumed by StrategyMatch"),
  "app.analysis.fibonacci": ((TECHNICAL,), "fib ladders"),
  "app.analysis.indicators": ((SHARED_MATH,), "Wilder ATR (second ATR formula)"),
  "app.analysis.key_level_role": ((TECHNICAL,), "support/resistance role"),
  "app.analysis.levels": ((TECHNICAL,), "key level clustering"),
  "app.analysis.liquidity": ((TECHNICAL,), "liquidity pools/sweeps"),
  "app.analysis.m1_trigger": ((TECHNICAL, POLICY_INPUT), "M1 confirmation trigger consumed by entry activation"),
  "app.analysis.mad_phase": ((RESEARCH,), "MAD phase lanes (shared with replay research)"),
  "app.analysis.market_map": ((TECHNICAL,), "Market Map builder (retired as a decision input)"),
  "app.analysis.market_map_delivery": ((PRESENTATION,), "Telegram market-map delivery"),
  "app.analysis.math_utils": ((SHARED_MATH,), "atr_series/atr_scalar: duplicate ATR"),
  "app.analysis.momentum": ((TECHNICAL,), "momentum detector"),
  "app.analysis.ohlc_source": ((MARKET_DATA,), "Redis OHLC reader"),
  "app.analysis.regime": ((TECHNICAL,), "regime classifier"),
  "app.analysis.scalp_ranges": ((TECHNICAL,), "scalp range detector"),
  "app.analysis.scanner": ((TECHNICAL_ORCHESTRATION,), "scanner: detector cycle, setup cards, StrategyMatch publish"),
  "app.analysis.session_liquidity": ((TECHNICAL,), "session highs/lows"),
  "app.analysis.structural_reaction_support": ((TECHNICAL, POLICY_INPUT), "structural taxonomy and bias relationship"),
  "app.analysis.structure": ((TECHNICAL,), "market structure / breaks"),
  "app.analysis.swings": ((TECHNICAL,), "swing detection"),
  "app.analysis.technique_detectors": ((TECHNICAL,), "technique detectors"),
  "app.analysis.technique_geometry": ((TECHNICAL,), "technique geometry / invalidation"),
  "app.analysis.trendline_v2": ((TECHNICAL,), "trendline v2"),
  "app.analysis.trendlines": ((TECHNICAL,), "trendlines v1"),
  "app.analysis.types": ((SHARED_TYPES,), "Zone/Level dataclasses shared with policy"),
  "app.analysis.zones": ((TECHNICAL,), "supply/demand/displacement zones"),
  "app.scalping": ((MARKER,), "package marker"),
  "app.scalping.activation": ((POLICY_INPUT,), "scalp activation gates"),
  "app.scalping.context": ((POLICY_INPUT,), "classify_session/session context"),
  "app.scalping.lab_event_builder": ((RESEARCH,), "replay lab event builder"),
  "app.scalping.lifecycle": ((ACCOUNTING,), "scalp lifecycle ledger"),
  "app.scalping.mad_phase": ((COMPAT,), "re-export of app.analysis.mad_phase"),
  "app.scalping.mad_replay": ((RESEARCH,), "MAD replay"),
  "app.scalping.math_features": ((TECHNICAL,), "scalp math features"),
  "app.scalping.math_strategies": ((TECHNICAL,), "scalp math strategies"),
  "app.scalping.microstructure": ((TECHNICAL,), "M1 microstructure detector"),
  "app.scalping.models": ((ACCOUNTING,), "ScalpOpportunity / lifecycle records / STRATEGY_DISPLAY"),
  "app.scalping.outcomes": ((ACCOUNTING,), "live outcome ledger and R reconciliation"),
  "app.scalping.performance": ((RESEARCH,), "research performance aggregation"),
  "app.scalping.publish": ((TECHNICAL_ORCHESTRATION,), "scalp match/ladder publication"),
  "app.scalping.ranking": ((TECHNICAL,), "scalp ranking"),
  "app.scalping.replay": ((RESEARCH,), "scalp replay"),
  "app.scalping.replay_lab": ((RESEARCH,), "replay lab"),
  "app.scalping.research_stamp": ((RESEARCH,), "research stamp"),
  "app.scalping.risk": ((ACCOUNTING,), "scalp daily risk state"),
  "app.scalping.rollout": ((POLICY_INPUT,), "scalp rollout gates"),
  "app.scalping.runtime": ((TECHNICAL_ORCHESTRATION,), "handle_closed_bar: rebuilds technical context, runs discover_all"),
  "app.scalping.strategies": ((TECHNICAL,), "scalp strategies"),
  "app.scalping.telemetry": ((ACCOUNTING,), "scalp counters"),
  "app.scalping.unified_context": ((TECHNICAL,), "scalp unified technical context"),
}

# Importing production module -> its own concern. First matching prefix wins.
IMPORTER_ROLES: tuple[tuple[str, str], ...] = (
  ("app/analysis_client/", "go_consumer"),
  ("app/analysis/market_map_delivery.py", PRESENTATION),
  ("app/analysis/", "legacy_technical"),
  ("app/scalping/", "legacy_technical"),
  ("app/autotrade/setup_card.py", PRESENTATION),
  ("app/autotrade/delivery.py", PRESENTATION),
  ("app/autotrade/setups_report.py", PRESENTATION),
  ("app/autotrade/reaction_funnel.py", PRESENTATION),
  ("app/autotrade/stats_ingestion.py", "outcome_accounting"),
  ("app/autotrade/setup_expiry_sweeper.py", "outcome_accounting"),
  ("app/autotrade/", "execution_policy"),
  ("app/bot/", PRESENTATION),
  ("app/signals/", "manual_trading"),
  ("app/configuration/", "configuration"),
  ("app/main.py", "startup"),
)

# Redis key / stream / channel names exported by a legacy module.
_CONTRACT_NAME = re.compile(r"(_key|_KEY|_stream|_STREAM|_channel|_CHANNEL|STREAM|CHANNEL)$")
# Process entrypoints (Dockerfile CMD is ``python -m app.main``; the rest are
# operator CLIs under app/scripts).
DEFAULT_ENTRYPOINTS = (
  "app.main",
  "app.scripts.auto_strategy_baseline",
  "app.scripts.backfill_auto_trade_stats",
  "app.scripts.drop_manual_algo_charts",
  "app.scripts.repair_manual_algo_results",
  "app.scripts.analysis_authority",
)


def _module_name(root: Path, path: Path) -> str:
  parts = path.relative_to(root).with_suffix("").parts
  return ".".join(parts[:-1]) if parts[-1] == "__init__" else ".".join(parts)


def _parse(path: Path) -> ast.AST:
  return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def role_of(module: str) -> tuple[tuple[str, ...], str] | None:
  """Reviewed role for a legacy module or a symbol path inside it."""
  probe = module
  while probe:
    if probe in MODULE_ROLES:
      return MODULE_ROLES[probe]
    probe = probe.rpartition(".")[0]
  return None


def importer_role(rel_path: str) -> str:
  for prefix, role in IMPORTER_ROLES:
    if rel_path.startswith(prefix) or rel_path == prefix:
      return role
  return "other"


def module_graph(root: Path) -> tuple[dict[str, Path], dict[str, set[str]]]:
  app = root / "app"
  modules = {_module_name(root, path): path for path in sorted(app.rglob("*.py"))}
  edges: dict[str, set[str]] = {}
  for name, path in modules.items():
    package_parts = tuple(name.split(".")) if path.name == "__init__.py" else tuple(name.split(".")[:-1])
    found: set[str] = set()
    for node in ast.walk(_parse(path)):
      for imported in _imported_modules(node, package_parts):
        if imported in modules:
          found.add(imported)
    edges[name] = found
  return modules, edges


def reachable(modules: dict[str, Path], edges: dict[str, set[str]], entrypoints) -> set[str]:
  seen: set[str] = set()
  stack = [entry for entry in entrypoints if entry in modules]
  while stack:
    name = stack.pop()
    if name in seen:
      continue
    seen.add(name)
    parts = name.split(".")
    # Importing a submodule executes every parent package.
    stack.extend(".".join(parts[:i]) for i in range(1, len(parts)) if ".".join(parts[:i]) in modules)
    stack.extend(edges.get(name, ()))
  return seen


def dynamic_import_findings(root: Path) -> list[dict[str, str]]:
  """Constructs a static import graph cannot see."""
  findings: list[dict[str, str]] = []
  for path in sorted((root / "app").rglob("*.py")):
    rel = path.relative_to(root).as_posix()
    for node in ast.walk(_parse(path)):
      if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if name in {"import_module", "__import__"}:
          arg = node.args[0] if node.args else None
          target = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None
          # A non-constant target could name a legacy module; only a constant
          # non-legacy target (e.g. __import__("typing")) is provably benign.
          legacy_possible = target is None or _legacy_package(target) is not None
          findings.append({
            "file": rel, "line": str(node.lineno), "kind": "dynamic_import_call",
            "target": target or "<non-constant>",
            "legacy_possible": "yes" if legacy_possible else "no",
          })
      elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value
        if any(value == pkg or value.startswith(pkg + ".") for pkg in LEGACY_PACKAGES) and re.fullmatch(r"[\w.]+", value):
          findings.append({"file": rel, "line": str(node.lineno), "kind": "legacy_module_string", "value": value})
  return findings


def startup_task_findings(root: Path, modules: dict[str, Path]) -> list[dict[str, str]]:
  """Background tasks started by app.main and the module each one lives in."""
  main = root / "app" / "main.py"
  tree = _parse(main)
  origin: dict[str, str] = {}
  for node in ast.walk(tree):
    if isinstance(node, ast.ImportFrom) and node.module:
      for alias in node.names:
        origin[alias.asname or alias.name] = node.module
  tasks: list[dict[str, str]] = []
  for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_spawn_supervised" and len(node.args) >= 2:
      label = node.args[0].value if isinstance(node.args[0], ast.Constant) else "?"
      target = node.args[1].id if isinstance(node.args[1], ast.Name) else "?"
      module = origin.get(target, "app.main")
      tasks.append({
        "task": str(label),
        "function": target,
        "module": module,
        "legacy": "yes" if _legacy_package(module) else "no",
        "conditional": "yes" if _is_conditional(tree, node) else "no",
      })
  return sorted(tasks, key=lambda item: item["task"])


def _is_conditional(tree: ast.AST, call: ast.Call) -> bool:
  for node in ast.walk(tree):
    if isinstance(node, ast.If) and any(child is call for child in ast.walk(node)):
      return True
  return False


def callback_findings(root: Path) -> list[dict[str, str]]:
  """Local (function-scope) imports of legacy modules: callback/lazy wiring."""
  findings: list[dict[str, str]] = []
  for path in sorted((root / "app").rglob("*.py")):
    rel = path.relative_to(root).as_posix()
    if importer_role(rel) == "legacy_technical":
      continue
    package_parts = ("app", *path.parent.relative_to(root / "app").parts)
    for func in ast.walk(_parse(path)):
      if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
      for node in ast.walk(func):
        for imported in _imported_modules(node, package_parts):
          if _legacy_package(imported):
            findings.append({"file": rel, "function": func.name, "line": str(node.lineno), "imports": imported})
            break
  return findings


def contract_findings(root: Path, importers: dict[str, list[str]]) -> dict[str, list[str]]:
  """Redis-key/stream names exported by legacy modules and Kafka topic use."""
  redis: dict[str, list[str]] = {}
  for rel, edges in importers.items():
    if importer_role(rel) == "legacy_technical":
      continue
    for edge in edges:
      leaf = edge.rsplit(".", 1)[-1]
      if _CONTRACT_NAME.search(leaf) and edge not in LEGACY_PACKAGES:
        redis.setdefault(edge, []).append(rel)
  kafka: list[str] = []
  for path in sorted((root / "app").rglob("*.py")):
    rel = path.relative_to(root).as_posix()
    if rel.startswith("app/analysis_client/"):
      continue
    if "analysis.opportunity" in path.read_text(encoding="utf-8"):
      kafka.append(rel)
  return {"redis_contract_imports": {k: sorted(v) for k, v in sorted(redis.items())}, "kafka_topic_users_outside_client": kafka}  # type: ignore[return-value]


def classify_inventory(root: Path, entrypoints=DEFAULT_ENTRYPOINTS) -> dict[str, object]:
  base = inventory_legacy_dependencies(root)
  modules, edges = module_graph(root)
  live = reachable(modules, edges, entrypoints)
  legacy_modules = sorted(name for name in modules if _legacy_package(name))

  unclassified = [name for name in legacy_modules if role_of(name) is None]
  module_report = {}
  for name in legacy_modules:
    role = role_of(name)
    roles, reason = role if role else (("unclassified",), "no reviewed role")
    module_report[name] = {
      "roles": list(roles),
      "retirement": ROLE_RETIREMENT.get(roles[0], "unclassified"),
      "reason": reason,
      "reachable_from_entrypoints": name in live,
      "lines": sum(1 for _ in modules[name].open(encoding="utf-8")),
    }

  edge_report: list[dict[str, object]] = []
  for rel, imported_list in sorted(base["importers"].items()):  # type: ignore[union-attr]
    who = importer_role(rel)
    for imported in imported_list:
      role = role_of(imported)
      if role is None:
        continue
      edge_report.append({
        "importer": rel,
        "importer_role": who,
        "imported": imported,
        "imported_roles": list(role[0]),
        "retirement": ROLE_RETIREMENT.get(role[0][0], "unclassified"),
      })

  external = [edge for edge in edge_report if edge["importer_role"] not in {"legacy_technical", "go_consumer"}]
  blockers = sorted({str(edge["importer"]) for edge in external if edge["retirement"].startswith("replace")})  # type: ignore[union-attr]
  unreachable = [name for name, info in module_report.items() if not info["reachable_from_entrypoints"]]
  return {
    "scope": "static analysis of algo-bot/app; reachability is static, not runtime proof",
    "entrypoints": list(entrypoints),
    "legacy_module_count": len(legacy_modules),
    "legacy_lines": sum(int(info["lines"]) for info in module_report.values()),  # type: ignore[arg-type]
    "unclassified_modules": unclassified,
    "unreachable_modules": unreachable,
    "modules": module_report,
    "external_edges": external,
    "production_importers_blocking_deletion": blockers,
    "dynamic_imports": dynamic_import_findings(root),
    "lazy_function_scope_imports": callback_findings(root),
    "startup_tasks": startup_task_findings(root, modules),
    "contracts": contract_findings(root, base["importers"]),  # type: ignore[arg-type]
  }


def render_markdown(report: dict[str, object]) -> str:
  modules: dict[str, dict] = report["modules"]  # type: ignore[assignment]
  lines = [
    "# S13 classified legacy dependency inventory",
    "",
    f"- Legacy modules: {report['legacy_module_count']} ({report['legacy_lines']} lines)",
    f"- Unclassified modules: {len(report['unclassified_modules'])}",  # type: ignore[arg-type]
    f"- Unreachable from process entrypoints: {', '.join(report['unreachable_modules']) or 'none'}",  # type: ignore[arg-type]
    f"- Production importers that block deletion: {len(report['production_importers_blocking_deletion'])}",  # type: ignore[arg-type]
    "",
    "| Module | Role | Retirement | Reachable | Lines |",
    "|---|---|---|---|---|",
  ]
  for name, info in modules.items():
    lines.append(f"| `{name}` | {', '.join(info['roles'])} | {info['retirement']} | {'yes' if info['reachable_from_entrypoints'] else 'NO'} | {info['lines']} |")
  lines += ["", "## Startup tasks", "", "| Task | Module | Legacy | Conditional |", "|---|---|---|---|"]
  for task in report["startup_tasks"]:  # type: ignore[union-attr]
    lines.append(f"| `{task['task']}` | `{task['module']}` | {task['legacy']} | {task['conditional']} |")
  return "\n".join(lines) + "\n"


if __name__ == "__main__":
  result = classify_inventory(Path(__file__).resolve().parent)
  if "--markdown" in sys.argv:
    sys.stdout.write(render_markdown(result))
  else:
    print(json.dumps(result, indent=2, sort_keys=True))
