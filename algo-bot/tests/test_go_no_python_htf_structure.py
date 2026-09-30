"""Go-origin matches no longer trigger the Python HTF zone/level recompute."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.analysis_client.provenance import GO_ORIGIN_TAG
from app.autotrade import worker

pytestmark = pytest.mark.no_database


def _match(*tags: str) -> SimpleNamespace:
  return SimpleNamespace(tags=tuple(tags))


def test_go_only_matches_do_not_need_the_python_recompute():
  matches = [_match(GO_ORIGIN_TAG, "catalog:fvg"), _match(GO_ORIGIN_TAG)]
  assert worker._needs_python_htf_structure(matches) is False


def test_a_non_go_match_still_needs_it():
  assert worker._needs_python_htf_structure([_match("key_level")]) is True


def test_one_non_go_match_among_go_matches_still_needs_it():
  matches = [_match(GO_ORIGIN_TAG), _match("key_level")]
  assert worker._needs_python_htf_structure(matches) is True


def test_no_matches_and_missing_tags_are_handled():
  assert worker._needs_python_htf_structure([]) is False
  assert worker._needs_python_htf_structure([SimpleNamespace()]) is True


def test_counter_bias_adapter_ignores_a_go_match_so_it_never_reads_the_python_zones():
  # _adapt_counter_bias_target only acts on the exact "counter_bias" tag. Go
  # emits "bias:counter_bias", so a Go match is allowed through untouched
  # even when zones and levels are empty.
  match = SimpleNamespace(
    target_price=4190.64,
    tags=(GO_ORIGIN_TAG, "catalog:fvg", "bias:counter_bias"),
    direction="BUY",
  )
  adapted, outcome = worker._adapt_counter_bias_target(
    match, 4185.19, [], [], 0.1,
  )
  assert adapted is match
  assert outcome.reason_code == "not_counter_bias"
  assert outcome.hard_block is False
