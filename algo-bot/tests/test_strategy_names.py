"""Canonical strategy-name contract and production label coverage."""

from __future__ import annotations

import asyncio

import pytest

from app.analysis.detectors import LIVE_DETECTOR_REGISTRY
from app.autotrade import reaction_funnel
from app.autotrade.reaction_funnel import normalize_setup_type
from app.configuration.config_file import ConfigFileError, load_config_file
from app.autotrade.strategy_names import (
  CANONICAL_FAMILY_UNKNOWN,
  STRATEGY_NAMES,
  resolve_strategy,
  strategy_for_detector,
)


pytestmark = pytest.mark.no_database


PRODUCTION_SETUP_TYPES = (
  "Breakout Retest Scalp", "CRT", "Confluence Zone", "FVG", "Fade Scalp",
  "Flip Zone", "HFS Impulse Pullback", "HFS Momentum Chase",
  "HFS Range Sweep", "Impulse Pullback Scalp", "Key Level",
  "Momentum Chase Scalp", "Range Sweep Scalp", "Session Level",
  "Supply Demand", "Trend Pullback", "Trendline", "Zone Reaction",
  "breakout-retest", "confluence", "confulence", "demand", "flip-zone",
  "golden-fibo", "iFVG", "key-level", "momentum", "ob", "supply",
)


def test_detector_registry_is_fully_named_and_trend_pullback_is_retired():
  for registration in LIVE_DETECTOR_REGISTRY:
    entry = strategy_for_detector(registration.name)
    assert entry is not None, registration.name
    assert entry.family != CANONICAL_FAMILY_UNKNOWN
  assert all(item.name != "trend_pullback" for item in LIVE_DETECTOR_REGISTRY)


def test_strategy_registry_has_unique_canonicals_detectors_and_aliases():
  assert len({entry.canonical for entry in STRATEGY_NAMES}) == len(STRATEGY_NAMES)
  detector_ids = [entry.detector_id for entry in STRATEGY_NAMES if entry.detector_id]
  assert len(detector_ids) == len(set(detector_ids))
  aliases = [alias for entry in STRATEGY_NAMES for alias in entry.aliases]
  assert len(aliases) == len(set(aliases))
  canonical_keys = {entry.canonical.casefold() for entry in STRATEGY_NAMES}
  assert not canonical_keys.intersection(aliases)


@pytest.mark.parametrize("raw", PRODUCTION_SETUP_TYPES)
def test_production_setup_type_is_resolvable(raw):
  assert resolve_strategy(raw) is not None, raw
  assert normalize_setup_type(raw) is not None


@pytest.mark.parametrize(
  ("raw", "expected"),
  [
    ("supply", "Supply Demand"),
    ("demand", "Supply Demand"),
    ("confluence", "Confluence Zone"),
    ("confulence", "Confluence Zone"),
    ("key level · add_momentum", "Key Level · add_momentum"),
    ("HFS Range Sweep", "Range Sweep Scalp"),
    ("HFS Impulse Pullback", "Impulse Pullback Scalp"),
  ],
)
def test_manual_aliases_and_scale_in_tags(raw, expected):
  assert normalize_setup_type(raw) == expected


@pytest.mark.asyncio
async def test_unmapped_setup_is_preserved_counted_and_logged_once(monkeypatch, caplog):
  raw = "Future Strategy PR-S"
  reaction_funnel._unresolved_setup_names.discard(raw)
  seen = []

  async def fake_increment(client, name, *, symbol="XAU", dimensions=None):
    seen.append((name, dimensions))

  class RedisState:
    @staticmethod
    def get_client():
      return object()

  monkeypatch.setattr(reaction_funnel, "increment_metric", fake_increment)
  monkeypatch.setattr("app.persistence.redis_state.get_client", RedisState.get_client)

  with caplog.at_level("WARNING"):
    assert normalize_setup_type(raw) == raw
    assert normalize_setup_type(raw) == raw
    await asyncio.sleep(0)

  assert seen == [("setup_name_unresolved", {"raw": raw})]
  assert [record for record in caplog.records if raw in record.message]


def test_removed_trend_pullback_config_key_fails_closed(tmp_path):
  path = tmp_path / "stale.yml"
  path.write_text(
    "version: 1\nstrategies:\n  trend:\n    pullback_enabled: true\n",
    encoding="utf-8",
  )
  with pytest.raises(ConfigFileError, match="strategies.trend.pullback_enabled"):
    load_config_file(path, missing_ok=False)
