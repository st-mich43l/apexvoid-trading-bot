"""Producer-owned range context freshness and withdrawal."""

from __future__ import annotations

import time

import pytest

from app.autotrade.range_types import AutoScalpBox, AutoScalpDecision, AutoScalpRail
from app.autotrade.range_context import (
  PRIVATE_SOURCE_MAX_AGE_SECONDS,
  SCANNER_SOURCE_MAX_AGE_SECONDS,
  RangeBarrier,
  RangeContext,
  continue_range_episode,
  evaluate_range_box_eligibility,
  is_range_context_current,
  persist_private_range_observation,
  persist_resolved_range,
  persist_scanner_range_observation,
  private_range_context,
  range_context_key,
  range_context_source_key,
  resolve_range_context,
)
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


def _barrier(level: float) -> RangeBarrier:
  return RangeBarrier(level, level - 0.2, level + 0.2, touches=3)


def _context(
  *,
  source: str,
  generated_at: int,
  ttl: int,
  lower: float = 4000.0,
  upper: float = 4010.0,
  state: str = "confirmed",
) -> RangeContext:
  low = _barrier(lower)
  high = _barrier(upper)
  width = upper - lower
  return RangeContext(
    version=1,
    range_id=f"{source}-{lower:.0f}-{upper:.0f}",
    symbol="XAU",
    state=state,
    source=source,
    execution_timeframe="M5" if source == "scanner" else "M1",
    context_timeframes=("M5",),
    lower=lower,
    upper=upper,
    equilibrium=(lower + upper) / 2,
    width_price=width,
    width_pips=width / 0.1,
    width_atr=width / 2.0,
    lower_barrier=low,
    upper_barrier=high,
    supports=(low,),
    resistances=(high,),
    quality=5.0,
    generated_at=generated_at,
    expires_at=generated_at + ttl,
  )


@pytest.mark.asyncio
async def test_scanner_observation_set_and_withdrawn():
  client = redis_state.get_client()
  symbol = "XAU"
  context = _context(source="scanner", generated_at=1_000, ttl=600)
  await persist_scanner_range_observation(
    client, symbol=symbol, context=context,
  )
  key = range_context_source_key(symbol, "scanner")
  assert await client.get(key) is not None
  await persist_scanner_range_observation(client, symbol=symbol, context=None)
  assert await client.get(key) is None


@pytest.mark.asyncio
async def test_private_observation_set_and_withdrawn():
  client = redis_state.get_client()
  symbol = "XAU"
  context = _context(source="private", generated_at=1_000, ttl=150)
  await persist_private_range_observation(
    client, symbol=symbol, context=context,
  )
  key = range_context_source_key(symbol, "private")
  assert await client.get(key) is not None
  await persist_private_range_observation(client, symbol=symbol, context=None)
  assert await client.get(key) is None


@pytest.mark.asyncio
async def test_resolver_does_not_rewrite_scanner_source():
  client = redis_state.get_client()
  symbol = "XAU"
  scanner = _context(source="scanner", generated_at=1_000, ttl=600)
  await persist_scanner_range_observation(
    client, symbol=symbol, context=scanner,
  )
  before = await client.get(range_context_source_key(symbol, "scanner"))
  private = _context(source="private", generated_at=1_050, ttl=150)
  resolved, comparison = resolve_range_context(scanner, private, now=1_060)
  await persist_resolved_range(
    client,
    symbol=symbol,
    resolved=resolved,
    comparison=comparison,
  )
  after = await client.get(range_context_source_key(symbol, "scanner"))
  assert after == before
  restored = RangeContext.from_json(after)
  assert restored is not None
  assert restored.generated_at == 1_000
  assert restored.expires_at == 1_600


def test_stale_scanner_excluded_from_resolution():
  now = 2_000
  scanner = _context(
    source="scanner",
    generated_at=now - SCANNER_SOURCE_MAX_AGE_SECONDS - 10,
    ttl=SCANNER_SOURCE_MAX_AGE_SECONDS + 100,
  )
  resolved, comparison = resolve_range_context(scanner, None, now=now)
  assert resolved is None
  assert comparison["resolution"] == "none"
  assert comparison["scanner"]["state"] == "stale"
  assert comparison["stale_source_excluded"] is True


def test_current_private_resolves_without_scanner():
  now = 2_000
  private = _context(
    source="private",
    generated_at=now - 30,
    ttl=PRIVATE_SOURCE_MAX_AGE_SECONDS,
  )
  resolved, comparison = resolve_range_context(None, private, now=now)
  assert resolved is not None
  assert resolved.source == "private"
  assert comparison["resolution"] == "private"
  assert comparison["private"]["state"] == "current"


def test_is_range_context_current_rejects_expired_and_over_age():
  now = 10_000
  fresh = _context(source="scanner", generated_at=now - 60, ttl=600)
  assert is_range_context_current(
    fresh, now=now, max_age_seconds=SCANNER_SOURCE_MAX_AGE_SECONDS,
  )
  over_age = _context(
    source="scanner",
    generated_at=now - SCANNER_SOURCE_MAX_AGE_SECONDS - 1,
    ttl=SCANNER_SOURCE_MAX_AGE_SECONDS + 100,
  )
  assert not is_range_context_current(
    over_age, now=now, max_age_seconds=SCANNER_SOURCE_MAX_AGE_SECONDS,
  )


def test_range_box_eligibility_requires_live_private_and_resolved():
  now = int(time.time())
  private = _context(source="private", generated_at=now, ttl=150)
  resolved = _context(source="merged", generated_at=now, ttl=150)
  rail = AutoScalpRail(
    role="support",
    low=3999.8,
    high=4000.2,
    level=4000.0,
    touches=3,
    score=2.0,
    timeframes=("M1",),
    sources=("private",),
  )
  box = AutoScalpBox(
    box_id="box-1",
    lower=rail,
    upper=AutoScalpRail(
      role="resistance",
      low=4009.8,
      high=4010.2,
      level=4010.0,
      touches=3,
      score=2.0,
      timeframes=("M1",),
      sources=("private",),
    ),
    width_pips=100.0,
    inside_ratio=0.8,
  )
  decision = AutoScalpDecision(
    state="candidate",
    box=box,
    rail=rail,
    direction="BUY",
    confluence=3,
  )
  eligible = evaluate_range_box_eligibility(
    symbol="XAU",
    decision=decision,
    private_context=private,
    resolved=resolved,
    regime_state="chop",
    now=now,
    range_enabled=True,
  )
  assert eligible.eligible
  assert eligible.reason_code == "candidate_ready"

  trend = evaluate_range_box_eligibility(
    symbol="XAU",
    decision=decision,
    private_context=private,
    resolved=resolved,
    regime_state="trend",
    now=now,
    range_enabled=True,
  )
  assert not trend.eligible
  assert trend.reason_code == "range_regime_not_chop"

  no_box = evaluate_range_box_eligibility(
    symbol="XAU",
    decision=AutoScalpDecision(state="waiting_for_box"),
    private_context=None,
    resolved=resolved,
    regime_state="chop",
    now=now,
    range_enabled=True,
  )
  assert not no_box.eligible
  assert no_box.reason_code == "no_private_box"


@pytest.mark.asyncio
async def test_both_sources_absent_deletes_resolved():
  client = redis_state.get_client()
  symbol = "XAU"
  resolved = _context(source="merged", generated_at=1_000, ttl=600)
  await persist_resolved_range(
    client,
    symbol=symbol,
    resolved=resolved,
    comparison={"resolution": "merged", "state": "confirmed"},
  )
  assert await client.get(range_context_key(symbol)) is not None
  await persist_resolved_range(
    client,
    symbol=symbol,
    resolved=None,
    comparison={"resolution": "none", "state": "no_range"},
  )
  assert await client.get(range_context_key(symbol)) is None


def test_private_range_context_absent_without_box():
  decision = AutoScalpDecision(state="waiting_for_box")
  assert private_range_context(
    symbol="XAU",
    decision=decision,
    atr=2.0,
    pip_size=0.1,
    generated_at=1_000,
    ttl=150,
  ) is None


def test_private_range_uses_native_box_as_episode_identity():
  box = AutoScalpBox(
    box_id="native-private-episode",
    lower=AutoScalpRail(
      "support", 3999.8, 4000.2, 4000.0, 3, 3.0, ("M1",), ("M1",),
    ),
    upper=AutoScalpRail(
      "resistance", 4009.8, 4010.2, 4010.0, 3, 3.0, ("M1",), ("M1",),
    ),
    width_pips=100.0,
  )
  context = private_range_context(
    symbol="XAU",
    decision=AutoScalpDecision(state="candidate", box=box),
    atr=2.0,
    pip_size=0.1,
    generated_at=1_000,
    ttl=150,
  )

  assert context is not None
  assert context.range_id == "native-private-episode"


def test_range_episode_survives_small_drift_but_not_new_auction():
  previous = _context(
    source="scanner", generated_at=1_000, ttl=600,
    lower=4000.0, upper=4010.0,
  )
  small_drift = _context(
    source="scanner", generated_at=1_300, ttl=600,
    lower=4000.2, upper=4010.1,
  )
  new_auction = _context(
    source="scanner", generated_at=1_600, ttl=600,
    lower=4007.0, upper=4017.0,
  )

  continued = continue_range_episode(previous, small_drift)
  restarted = continue_range_episode(previous, new_auction)

  assert continued is not None
  assert continued.range_id == previous.range_id
  assert continued.episode_started_at == previous.generated_at
  assert restarted is not None
  assert restarted.range_id == new_auction.range_id
