"""S13 classified inventory: coverage, reachability and separate scans."""

from __future__ import annotations

from pathlib import Path

import s13_legacy_classification as cls

ROOT = Path(__file__).resolve().parents[1]

# Unreachable from every process entrypoint, reviewed as offline research or a
# compatibility re-export. A NEW unreachable module must be reviewed and added
# here deliberately, not slip in unnoticed.
REVIEWED_UNREACHABLE = {
  "app.scalping.lab_event_builder",
  "app.scalping.mad_phase",
  "app.scalping.mad_replay",
  "app.scalping.performance",
  "app.scalping.replay_lab",
}


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
  for rel, body in files.items():
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
  return tmp_path


def test_every_real_legacy_module_has_a_reviewed_role():
  report = cls.classify_inventory(ROOT)
  assert report["unclassified_modules"] == [], (
    "new app.analysis/app.scalping module needs a reviewed role in "
    "s13_legacy_classification.MODULE_ROLES"
  )
  # A deleted module may leave a stale role entry behind; that is harmless and
  # expected while S13 deletions land in separate PRs, so it is not asserted.


def test_unreachable_legacy_modules_are_exactly_the_reviewed_research_set():
  report = cls.classify_inventory(ROOT)
  # <= (not ==): a reviewed research/compat module may be deleted; a NEW
  # unreachable module must still be reviewed and added above deliberately.
  assert set(report["unreachable_modules"]) <= REVIEWED_UNREACHABLE
  for name in set(report["unreachable_modules"]):
    roles = report["modules"][name]["roles"]
    assert roles[0] in {cls.RESEARCH, cls.COMPAT}, f"{name} unreachable but not research/compat"


def test_outcome_accounting_and_policy_modules_are_not_marked_for_replacement():
  report = cls.classify_inventory(ROOT)
  for name in ("app.scalping.outcomes", "app.scalping.risk", "app.scalping.lifecycle", "app.scalping.models"):
    assert report["modules"][name]["retirement"] == "retain", name
  assert report["modules"]["app.analysis.detectors"]["retirement"] == "replace_with_go"


def test_analysis_client_never_imports_legacy_and_is_not_counted_as_blocker():
  report = cls.classify_inventory(ROOT)
  assert not any(edge["importer"].startswith("app/analysis_client/") for edge in report["external_edges"])
  assert not any(item.startswith("app/analysis_client/") for item in report["production_importers_blocking_deletion"])


def test_real_tree_has_no_dynamic_import_that_can_reach_legacy_modules():
  report = cls.classify_inventory(ROOT)
  assert [item for item in report["dynamic_imports"] if item.get("legacy_possible") == "yes"] == []


def test_real_startup_tasks_identify_the_one_legacy_background_loop():
  report = cls.classify_inventory(ROOT)
  legacy = [task["task"] for task in report["startup_tasks"] if task["legacy"] == "yes"]
  assert legacy == ["bar_event_dispatcher_loop"]
  consumer = next(task for task in report["startup_tasks"] if task["task"] == "analysis_opportunity_consumer_loop")
  assert consumer["conditional"] == "yes", "Go consumer must stay opt-in"


def test_reachability_follows_imports_and_parent_packages(tmp_path):
  root = _tree(tmp_path, {
    "app/__init__.py": "",
    "app/main.py": "from app.autotrade import worker\n",
    "app/autotrade/__init__.py": "",
    "app/autotrade/worker.py": "from app.analysis.zones import supply\n",
    "app/analysis/__init__.py": "",
    "app/analysis/zones.py": "from app.analysis import types\n",
    "app/analysis/types.py": "",
    "app/analysis/orphan.py": "from app.analysis import types\n",
  })
  modules, edges = cls.module_graph(root)
  live = cls.reachable(modules, edges, ["app.main"])
  assert {"app.analysis.zones", "app.analysis.types", "app.analysis"} <= live
  assert "app.analysis.orphan" not in live


def test_scans_report_dynamic_lazy_and_contract_imports(tmp_path):
  root = _tree(tmp_path, {
    "app/__init__.py": "",
    "app/main.py": (
      "import importlib\n"
      "from app.autotrade.delivery import loop\n"
      "async def main():\n"
      "  _spawn_supervised('delivery', loop)\n"
      "  if flag:\n"
      "    _spawn_supervised('cond', loop)\n"
      "importlib.import_module(name)\n"
      "__import__('typing')\n"
      "PATH = 'app.analysis.detectors'\n"
    ),
    "app/autotrade/__init__.py": "",
    "app/autotrade/delivery.py": (
      "def loop():\n"
      "  from app.scalping.lifecycle import active_key\n"
      "  return active_key\n"
    ),
    "app/scalping/__init__.py": "",
    "app/scalping/lifecycle.py": "def active_key(): ...\n",
    "app/analysis/__init__.py": "",
    "app/analysis/detectors.py": "",
    "app/analysis/mad_phase.py": "",
  })
  dyn = cls.dynamic_import_findings(root)
  kinds = {(item["kind"], item.get("target", item.get("value"))) for item in dyn}
  assert ("dynamic_import_call", "<non-constant>") in kinds
  assert ("dynamic_import_call", "typing") in kinds
  assert ("legacy_module_string", "app.analysis.detectors") in kinds
  by_target = {item["target"]: item["legacy_possible"] for item in dyn if item["kind"] == "dynamic_import_call"}
  assert by_target == {"<non-constant>": "yes", "typing": "no"}

  modules, _ = cls.module_graph(root)
  tasks = {task["task"]: task for task in cls.startup_task_findings(root, modules)}
  assert tasks["delivery"]["conditional"] == "no"
  assert tasks["cond"]["conditional"] == "yes"

  lazy = cls.callback_findings(root)
  assert [item["function"] for item in lazy] == ["loop"]
  assert lazy[0]["imports"].startswith("app.scalping.lifecycle")

  base = cls.classify_inventory(root, entrypoints=["app.main"])
  assert base["contracts"]["redis_contract_imports"] == {"app.scalping.lifecycle.active_key": ["app/autotrade/delivery.py"]}


def test_edge_classification_uses_both_importer_and_imported_role(tmp_path):
  root = _tree(tmp_path, {
    "app/__init__.py": "",
    "app/main.py": "",
    "app/autotrade/__init__.py": "",
    "app/autotrade/stats_ingestion.py": "from app.scalping.outcomes import save_live_outcome\n",
    "app/autotrade/trend.py": "from app.analysis.zones import supply_demand\n",
    "app/scalping/__init__.py": "",
    "app/scalping/outcomes.py": "",
    "app/analysis/__init__.py": "",
    "app/analysis/zones.py": "",
  })
  report = cls.classify_inventory(root, entrypoints=["app.main"])
  edges = {(edge["importer"], edge["imported"]): edge for edge in report["external_edges"]}
  accounting = edges[("app/autotrade/stats_ingestion.py", "app.scalping.outcomes.save_live_outcome")]
  assert accounting["importer_role"] == "outcome_accounting" and accounting["retirement"] == "retain"
  detection = edges[("app/autotrade/trend.py", "app.analysis.zones.supply_demand")]
  assert detection["importer_role"] == "execution_policy" and detection["retirement"] == "replace_with_go"
  assert report["production_importers_blocking_deletion"] == ["app/autotrade/trend.py"]
