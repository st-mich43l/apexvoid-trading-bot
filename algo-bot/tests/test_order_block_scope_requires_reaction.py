"""order_block publishes only confirmed reactions (docs/strategies/order_block.md).

The resting-block observation path was deleted from the Go strategy because Algo Bot never traded it: a resting zone opportunity (no confirmation) is
rejected as ``reaction_confirmation_unavailable``. This pins that the adapter still requires a reaction for ``order_block``, so deleting the path cannot
change what executes.
"""

import pytest

from app.autotrade import go_opportunity_policy as pol

pytestmark = pytest.mark.no_database


def test_the_order_block_scope_requires_a_confirmed_reaction():
  assert pol.REVIEWED_SCOPES["order_block"].requires_reaction is True
