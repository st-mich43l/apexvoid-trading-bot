"""S14B freshness gate: pure, exhaustive, deterministic."""

from __future__ import annotations

import pytest

from app.analysis_client.freshness import (
  EXPIRED,
  FRESH,
  LAG_EXCEEDED,
  PRE_ACTIVATION,
  TOO_OLD,
  FreshnessLimits,
  evaluate_freshness,
)

LIMITS = FreshnessLimits(max_event_age_seconds=900, max_delivery_lag_seconds=300)
BOUNDARY = 10_000


def verdict(**overrides):
  fields = dict(
    observed_at=BOUNDARY + 100, expires_at=BOUNDARY + 5_000, produced_at=BOUNDARY + 101, published_at=None,
    consumed_at=BOUNDARY + 160, boundary=BOUNDARY, limits=LIMITS,
  )
  fields.update(overrides)
  return evaluate_freshness(**fields)


def test_a_new_confirmed_observation_after_activation_is_fresh():
  v = verdict()
  assert v.ok and v.code == FRESH
  assert (v.event_age_seconds, v.delivery_lag_seconds) == (60, 59)


def test_observation_exactly_at_the_boundary_counts_one_second_earlier_does_not():
  assert verdict(observed_at=BOUNDARY, produced_at=BOUNDARY).ok
  early = verdict(observed_at=BOUNDARY - 1, produced_at=BOUNDARY - 1)
  assert not early.ok and early.code == PRE_ACTIVATION


def test_history_replayed_after_activation_never_trades_however_fresh_the_delivery():
  # Old observation, brand-new Kafka publish (a re-published/replayed record).
  v = verdict(observed_at=BOUNDARY - 3_600, produced_at=BOUNDARY - 3_600, published_at=BOUNDARY + 155)
  assert not v.ok and v.code == PRE_ACTIVATION


def test_technical_expiry_is_inclusive_of_the_expiry_second():
  assert verdict(consumed_at=BOUNDARY + 4_999, expires_at=BOUNDARY + 5_000, limits=FreshnessLimits(10_000, 10_000)).ok
  v = verdict(consumed_at=BOUNDARY + 5_000, expires_at=BOUNDARY + 5_000, limits=FreshnessLimits(10_000, 10_000))
  assert not v.ok and v.code == EXPIRED


def test_event_age_limit_boundary():
  at_limit = verdict(consumed_at=BOUNDARY + 100 + 900, published_at=BOUNDARY + 100 + 900)
  assert at_limit.ok
  over = verdict(consumed_at=BOUNDARY + 100 + 901, published_at=BOUNDARY + 100 + 901)
  assert not over.ok and over.code == TOO_OLD


def test_delivery_lag_is_independent_of_setup_age():
  # Setup is young (60s) but Kafka held the record for 6 minutes: a backlog.
  v = verdict(published_at=BOUNDARY + 100, consumed_at=BOUNDARY + 100 + 301)
  assert not v.ok and v.code == LAG_EXCEEDED
  assert verdict(published_at=BOUNDARY + 100, consumed_at=BOUNDARY + 100 + 300).ok


def test_reasons_are_reported_most_fundamental_first():
  everything_wrong = verdict(
    observed_at=BOUNDARY - 10, produced_at=BOUNDARY - 10, expires_at=BOUNDARY, consumed_at=BOUNDARY + 99_999,
  )
  assert everything_wrong.code == PRE_ACTIVATION
  expired_and_old = verdict(expires_at=BOUNDARY + 200, consumed_at=BOUNDARY + 99_999)
  assert expired_and_old.code == EXPIRED
  old_and_lagging = verdict(consumed_at=BOUNDARY + 99_999, expires_at=BOUNDARY + 999_999)
  assert old_and_lagging.code == TOO_OLD


def test_kafka_publish_time_wins_over_the_envelope_but_falls_back_to_it():
  assert verdict(published_at=BOUNDARY + 150).published_at == BOUNDARY + 150
  assert verdict(published_at=None, produced_at=BOUNDARY + 111).published_at == BOUNDARY + 111
  assert verdict(published_at=0, produced_at=BOUNDARY + 111).published_at == BOUNDARY + 111


def test_details_record_technical_publication_and_consumption_times():
  d = verdict(published_at=BOUNDARY + 150).details()
  assert d == {
    "observed_at": BOUNDARY + 100, "expires_at": BOUNDARY + 5_000, "produced_at": BOUNDARY + 101,
    "published_at": BOUNDARY + 150, "consumed_at": BOUNDARY + 160, "activation_boundary": BOUNDARY,
    "event_age_seconds": 60, "delivery_lag_seconds": 10, "max_event_age_seconds": 900, "max_delivery_lag_seconds": 300,
  }


@pytest.mark.parametrize("consumed_at", range(BOUNDARY + 100, BOUNDARY + 100 + 1_200, 97))
def test_verdict_is_a_pure_function_of_its_inputs(consumed_at):
  a = verdict(consumed_at=consumed_at, published_at=consumed_at - 5)
  b = verdict(consumed_at=consumed_at, published_at=consumed_at - 5)
  assert a == b
