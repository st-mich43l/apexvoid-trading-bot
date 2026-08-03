"""Allowed deployment-metadata observability reads.

Service version, git SHA, and the expected-broker deployment assertion are
deploy-time metadata rather than tunable configuration: they have no catalog
home and describe the running image, not trading behavior. Phase 2H isolates
these ambient reads here as a single reviewed, allowed-observability boundary
so ``config_health`` no longer performs them inline. The defaults and semantics
are preserved exactly.
"""

from __future__ import annotations

import os


def service_version() -> str:
  """Return the deployed service version label (``dev`` when unset)."""
  return os.getenv("SERVICE_VERSION", "dev")


def git_sha() -> str:
  """Return the deployed git SHA (``unknown`` when unset)."""
  return os.getenv("GIT_SHA", "unknown")


def expected_broker() -> str:
  """Return the deploy-time expected broker assertion (empty when unset)."""
  return os.getenv("AUTO_TRADE_EXPECTED_BROKER", "")
