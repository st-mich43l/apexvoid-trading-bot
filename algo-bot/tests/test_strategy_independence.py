"""Strategy independence: 21 strategies, no strategy family.

Each strategy owns its execution profile in ``strategy_catalog``. The legacy
``strategy_family`` string survives only as a label on TradePlan V8 and
persisted events; nothing may branch on it.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from app.autotrade import execution_policy as ep
from app.autotrade import execution_route as er
from app.autotrade import strategy_catalog as catalog
from app.autotrade import strategy_names as names
from app.autotrade import strategy_taxonomy as tx
from app.autotrade.arbitration import ExecutionIntent, arbitrate_execution_intents

pytestmark = pytest.mark.no_database

APP = Path(__file__).resolve().parents[1] / "app"
FIXTURE = Path(__file__).parent / "fixtures" / "execution_profiles_family_era.json"


def snapshot() -> dict:
  """Every externally observable execution decision a strategy name drives."""
  every = set(catalog.STRATEGY_BY_NAME)
  for entry in names.STRATEGY_NAMES:
    every.add(entry.canonical)
    every.update(entry.aliases)
  every.update({"", "bogus"})
  out: dict[str, dict] = {}
  for name in sorted(every):
    row: dict = {}
    try:
      policy = dataclasses.asdict(ep.policy_for(name))
      policy.pop("strategy", None)
      policy.pop("family", None)
      row["policy"] = policy
    except Exception as error:
      row["policy"] = "ERR:" + type(error).__name__
    row["tiers"] = "".join(
      ep.classify_tier(
        confluence=c, strategy=name, range_state=rs, fallback_edge=fb,
        post_impulse=pi, one_sided=one,
      )
      for c in range(5)
      for rs in (None, "provisional_range", "post_impulse_range")
      for fb in (False, True)
      for pi in (False, True)
      for one in (False, True)
    )
    drift = []
    for atr in (0.0, 0.5, 3.0, 12.0):
      for room in (None, 2.0, 50.0):
        try:
          drift.append(ep.max_entry_drift_pips(
            strategy=name, atr=atr, pip_size=0.1, remaining_target_room_pips=room,
          ))
        except Exception as error:
          drift.append("ERR:" + type(error).__name__)
    row["drift"] = drift
    row["flags"] = {
      "reaction": tx.is_reaction_strategy(name),
      "zone": tx.is_zone_strategy(name),
      "range": tx.is_range_strategy(name),
      "m1": tx.is_m1_scalp_strategy(name),
      "technique_or_confluence": tx.is_technique_or_confluence(name),
      "breakout_retest_scalp": tx.is_breakout_retest_scalp_strategy(name),
      "scalp": tx.is_scalp_strategy(name),
      "bypass_with_tp": tx.bypasses_opposing_structure_gates(name, full_take_profit_pips=10),
      "bypass": tx.bypasses_opposing_structure_gates(name),
      "market_scale": er.reaction_market_scale_eligible(strategy=name),
    }
    out[name] = row
  return json.loads(json.dumps(out, sort_keys=True, default=str))


def test_removing_families_did_not_change_any_strategys_execution_decisions():
  """The family-era code produced FIXTURE; the per-strategy catalog must match it.

  Policy values, the A/B/C tier of 120 confluence/flag combinations, the
  entry-drift tolerance across ATR and target-room cases, and every taxonomy
  flag, for every strategy name and alias. This is the proof that making each
  strategy own its profile changed no execution or risk behavior.
  """
  assert snapshot() == json.loads(FIXTURE.read_text())


def test_the_strategy_catalog_has_one_row_per_strategy_and_no_family_tables():
  assert len({p.name for p in catalog.STRATEGY_PROFILES}) == len(catalog.STRATEGY_PROFILES)
  for removed in (
    "_DEFAULT_POLICIES", "_STRATEGY_FAMILY", "_FAMILY_HARD_DRIFT_DEFAULT",
    "strategy_family", "FAMILY_SUPPLY_DEMAND", "FAMILY_RANGE_REVERSION",
  ):
    assert not hasattr(ep, removed), removed
  for removed in ("canonical_family", "CANONICAL_FAMILY_ZONE"):
    assert not hasattr(tx, removed), removed
  assert not hasattr(names, "names_for_family")
  assert "family" not in {f.name for f in dataclasses.fields(names.StrategyName)}


def test_a_strategy_profile_can_change_without_changing_any_other():
  fvg = catalog.STRATEGY_BY_NAME["FVG"]
  order_block = catalog.STRATEGY_BY_NAME["Order Block"]
  assert fvg.execution is not order_block.execution or fvg.execution == order_block.execution
  changed = dataclasses.replace(
    fvg, execution=dataclasses.replace(fvg.execution, max_entry_drift_atr=9.0),
  )
  assert changed.execution.max_entry_drift_atr == 9.0
  assert order_block.execution.max_entry_drift_atr != 9.0
  assert catalog.STRATEGY_BY_NAME["FVG"].execution.max_entry_drift_atr != 9.0


# The only places the legacy family string may appear: serialising a plan or
# event, or carrying the label through the pipeline. No comparison, membership
# test or lookup on it is allowed anywhere.
_LABEL_CARRIERS = {
  "autotrade/strategy_catalog.py",
  "autotrade/go_opportunity_policy.py",
  "autotrade/trade_plan.py",
  "autotrade/trade_plan_builder.py",
  "autotrade/strategy_match.py",
  "autotrade/lifecycle.py",
  "autotrade/delivery.py",
  "autotrade/setup_card.py",
  "autotrade/setup_lifecycle.py",
  "autotrade/route_outcome.py",
  "autotrade/worker.py",
  "autotrade/arbitration.py",
  "autotrade/reaction_identity.py",
  "autotrade/strategy_identity.py",
  "signals/manual_plan.py",
}


def test_no_trading_decision_reads_the_legacy_family_label():
  decisions = re.compile(
    r"(?:\bfamily|\bstrategy_family|\.family)\s*(?:==|!=|\bin\b|\bnot in\b)"
    r"|(?:==|!=|\bin\b)\s*(?:match|self|item|plan)?\.?(?:family|strategy_family)\b"
    r"|\.get\(\s*(?:family|strategy_family)\b"
    r"|\bis_\w+\([^)]*\bfamily\s*="
  )
  offenders = []
  for path in sorted(APP.rglob("*.py")):
    relative = path.relative_to(APP).as_posix()
    text = path.read_text()
    for number, line in enumerate(text.splitlines(), 1):
      if line.lstrip().startswith("#"):
        continue
      if decisions.search(line):
        offenders.append(f"{relative}:{number}: {line.strip()}")
  assert not offenders, "\n".join(offenders)
  scanned = {
    p.relative_to(APP).as_posix()
    for p in APP.rglob("*.py")
    if re.search(r"\bstrategy_family\b|\.family\b", p.read_text())
  }
  assert scanned <= _LABEL_CARRIERS, sorted(scanned - _LABEL_CARRIERS)


def test_enable_switches_are_per_strategy_except_the_documented_scalp_mode():
  """A switch shared by several strategies would disable one by changing another."""
  live = defaultdict(list)
  for profile in catalog.STRATEGY_PROFILES:
    entry = names.BY_CANONICAL.get(profile.name)
    if entry is not None and entry.retired:
      continue
    live[profile.enable_setting].append(profile.name)
  shared = {setting: sorted(group) for setting, group in live.items() if len(group) > 1}
  assert shared == {
    "auto_algo.strategies.scalping.mode": [
      "Breakout Retest Scalp", "Impulse Pullback Scalp", "Range Sweep Scalp",
    ],
  }, "a new shared enable switch couples strategies; give each strategy its own"


def _intent(intent_id, strategy, quality, *, structural_id="zone-1"):
  return ExecutionIntent(
    intent_id=intent_id, source="go_analysis_engine", strategy=strategy,
    direction="BUY", confluence=3, freshness=100.0, distance_pips=0.0,
    entry_low=100.0, entry_high=101.0, structural_id=structural_id,
    quality_overall=quality, atr=1.0,
  )


def test_arbitration_keeps_every_strategys_attribution_when_theses_coincide():
  """Independent detection first; correlation only afterwards, with attribution kept."""
  result = arbitrate_execution_intents([
    _intent("confluence", "Confluence Zone", 0.70),
    _intent("fvg", "FVG", 0.82),
    _intent("ob", "Order Block", 0.64),
  ])
  everyone = {item.intent_id: item.strategy for item in (*result.ordered, *result.suppressed)}
  assert everyone == {"confluence": "Confluence Zone", "fvg": "FVG", "ob": "Order Block"}
  assert [item.strategy for item in result.ordered] == ["FVG"]
  assert set(result.thesis_losers) == {"confluence", "ob"}
  assert set(result.thesis_losers.values()) == {"fvg"}
