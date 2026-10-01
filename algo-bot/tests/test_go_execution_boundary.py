"""The Go opportunity path must not load Python technical detectors."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.autotrade.scalp_ladder import scalp_target_ladder
from app.autotrade.session_context import classify_session


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
AUTOTRADE_ROOT = APP_ROOT / "autotrade"


def _imports(path: Path) -> set[str]:
  tree = ast.parse(path.read_text(), filename=str(path))
  found: set[str] = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      found.update(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
      found.add(node.module)
  return found


def test_execution_package_has_no_legacy_analysis_imports():
  forbidden = {
    "app.analysis",
    "app.analysis.detectors",
    "app.analysis.scanner",
    "app.analysis.market_map",
    "app.scalping.context",
    "app.scalping.publish",
  }
  for path in AUTOTRADE_ROOT.glob("*.py"):
    imports = _imports(path)
    assert not any(
      module in forbidden or module.startswith("app.analysis.")
      or module in {"app.scalping.context", "app.scalping.publish"}
      for module in imports
    ), f"{path.name} imports retired technical analysis: {imports & forbidden}"


def test_session_labels_are_execution_owned_and_configurable():
  cfg = SimpleNamespace(market_data=SimpleNamespace(sessions=SimpleNamespace(
    asia_start=22,
    london_start=7,
    ny_start=13,
    daily_rollover_utc_hour=21,
  )))
  def ts(hour: int) -> int:
    return int(datetime(2026, 1, 1, hour, tzinfo=timezone.utc).timestamp())

  assert classify_session(ts(2), cfg) == "asia"
  assert classify_session(ts(8), cfg) == "london"
  assert classify_session(ts(14), cfg) == "london_ny_overlap"
  assert classify_session(ts(17), cfg) == "new_york"
  assert classify_session(ts(21), cfg) == "rollover"


def test_scalp_ladder_stays_one_or_two_targets():
  one_r = SimpleNamespace(
    expected_target_pips=20,
    expected_stop_pips=20,
    expected_reward_risk=1.0,
  )
  two_r = SimpleNamespace(
    expected_target_pips=45,
    expected_stop_pips=20,
    expected_reward_risk=2.25,
  )
  assert scalp_target_ladder(one_r) == (20, (20,))
  assert scalp_target_ladder(two_r) == (40, (20, 40))
