"""The scalp activation policy namespace intentionally differs from display."""

from types import SimpleNamespace

import pytest

from app.scalping import activation, publish
from app.scalping.models import (
  ARCHETYPE_BREAKOUT_RETEST,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_RANGE_SWEEP,
)


pytestmark = pytest.mark.no_database


@pytest.mark.parametrize(
  ("archetype", "policy_key"),
  (
    (ARCHETYPE_RANGE_SWEEP, "Range Edge Scalp"),
    (ARCHETYPE_IMPULSE_PULLBACK, "Trend Pullback"),
    (ARCHETYPE_BREAKOUT_RETEST, "Break & Retest"),
  ),
)
def test_activation_policy_key_differs_from_published_display(
  archetype, policy_key,
):
  assert activation._strategy_name(SimpleNamespace(archetype=archetype)) == policy_key
  assert publish._strategy_name(archetype) != policy_key
