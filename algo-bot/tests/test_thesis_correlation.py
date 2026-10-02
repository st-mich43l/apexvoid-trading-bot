"""Go structural-identity thesis correlation is the only live contract."""

from __future__ import annotations

from app.autotrade.multi_match import _same_go_thesis, dedupe_matches
from app.autotrade.strategy_match import StrategyMatch

import pytest

pytestmark = pytest.mark.no_database


def _match(
  match_id: str,
  *,
  direction: str = "BUY",
  strategy: str = "Key Level",
  family: str = "key_level",
  confluence: int = 4,
  tier: str = "A",
  zone_id: str | None = None,
  go_thesis_id: str | None = None,
  entry_low: float = 100.0,
  entry_high: float = 100.5,
) -> StrategyMatch:
  return StrategyMatch(
    version=1, match_id=match_id, symbol="XAU", source_tf="M5", event_ts="1000",
    issued_at=1000, expires_at=2000, strategy=strategy, strategy_mode="go_m5_reaction",
    direction=direction, key_level=100.25, entry_low=entry_low, entry_high=entry_high,
    current_price=100.25, confluence=confluence, reasons=(), atr=1.0, structure_swing=99.0,
    targets_pips=(20,), tags=(), tier=tier, family=family, zone_id=zone_id,
    structural_zone_id=zone_id, go_thesis_id=go_thesis_id,
  )


def test_correlates_on_go_thesis_id_alone():
  left = _match("go_a", go_thesis_id="go_a", entry_low=100.0, entry_high=100.5)
  right = _match("go_b", go_thesis_id="go_a", entry_low=999.0, entry_high=999.5)  # wildly different geometry
  assert _same_go_thesis(left, right) is True


def test_treats_different_go_thesis_ids_as_different_theses():
  left = _match("go_a", go_thesis_id="go_a")
  right = _match("go_b", go_thesis_id="go_b")
  assert _same_go_thesis(left, right) is False


def test_missing_go_thesis_id_never_invokes_geometric_fallback():
  left = _match("go_a", zone_id="zone-1", go_thesis_id=None)
  right = _match("go_b", zone_id="zone-1", go_thesis_id=None)
  assert _same_go_thesis(left, right) is False


# ---- dedupe_matches end-to-end ---------------------------------------------

def test_dedupe_matches_merges_via_go_thesis_id():
  a = _match("go_a", strategy="Key Level", family="key_level", confluence=4, go_thesis_id="go_a", entry_low=100.0, entry_high=100.5)
  b = _match("go_b", strategy="FVG", family="fvg", confluence=3, go_thesis_id="go_a", entry_low=500.0, entry_high=500.5)

  kept, events = dedupe_matches([a, b], atr=1.0)

  assert len(kept) == 1
  assert {e["event"] for e in events} >= {"merged_confluence"} or {e["event"] for e in events} >= {"tracked"}


def test_dedupe_matches_keeps_both_when_go_thesis_ids_differ():
  a = _match("go_a", go_thesis_id="go_a")
  b = _match("go_b", go_thesis_id="go_b")

  kept, _events = dedupe_matches([a, b], atr=1.0)

  assert {m.match_id for m in kept} == {"go_a", "go_b"}


def test_dedupe_matches_uses_go_identity_only():
  # The live default follows Go's identity even when both matches carry an
  # identical go_thesis_id.
  a = _match("go_a", zone_id="zone-9", go_thesis_id="shared", entry_low=100.0, entry_high=100.5)
  b = _match("go_b", zone_id="zone-other", go_thesis_id="shared", entry_low=999.0, entry_high=999.5)

  kept, _events = dedupe_matches([a, b], atr=1.0)

  assert {m.match_id for m in kept} == {"go_a"}
