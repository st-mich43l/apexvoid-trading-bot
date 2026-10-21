"""What the Go quality numbers (stars, ``quality.overall``) do downstream of Kafka: nothing that selects or sizes a trade.

The only quality-dependent acceptance is the Go confluence floor, which runs in the Analysis Engine before publication. After it, Algo Bot
records the stars as telemetry: a candidate below the policy minimum is still allowed (a preference note), the volume multiplier ignores the tier,
and with ``quality_ranking: false`` the arbitration order uses no quality score (pinned by test_arbitration_neutral_ranking). Pinned here so a later change that makes a score
operational is a decision, not an accident (docs/strategies/q3-quality-decision-map.md).
"""

import pytest

from app.autotrade import execution_policy as ep
from tests.test_zone_scale_execution import _cfg, _policy_match

pytestmark = pytest.mark.no_database


def _evaluate(confluence):
  return ep.evaluate_execution_policy(
    _policy_match(confluence=confluence), spot_price=4035.3, executable_quote=4035.3, regime="range", pip_size=0.1, cfg=_cfg(),
  )


def test_a_confluence_below_the_policy_minimum_is_a_preference_note_not_a_rejection():
  low, high = _evaluate(1), _evaluate(3)
  assert low.allowed and high.allowed
  assert low.measured.get("preference_reason_code") == "policy_confluence_below_minimum"
  assert "preference_reason_code" not in high.measured or high.measured["preference_reason_code"] != "policy_confluence_below_minimum"


def test_the_volume_multiplier_does_not_depend_on_the_tier():
  assert {ep.risk_multiplier_for_tier(tier) for tier in (ep.TIER_A, ep.TIER_B, ep.TIER_C)} == {1.0}


def test_the_committed_configuration_ranks_by_no_quality_score():
  from app.core.config import runtime_config

  assert runtime_config.auto_algo.actionability.scanner_gates.quality_ranking is False
