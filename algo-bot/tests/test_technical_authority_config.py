import pytest
from pydantic import ValidationError

from app.configuration.models.analysis import AnalysisTechnicalAuthorityConfig


def test_technical_authority_defaults_to_live_go_with_consumer_on():
  assert AnalysisTechnicalAuthorityConfig().model_dump() == {
    "consumer_enabled": True,
    "consumer_group": "apexvoid-algo-bot-analysis-opportunity-v1",
    "max_event_age_seconds": 900, "max_delivery_lag_seconds": 300,
  }


def test_technical_authority_rejects_removed_mode_field():
  with pytest.raises(ValidationError):
    AnalysisTechnicalAuthorityConfig(mode="go")


@pytest.mark.parametrize("field", ["max_event_age_seconds", "max_delivery_lag_seconds"])
@pytest.mark.parametrize("value", [0, -1])
def test_live_freshness_limits_must_be_positive(field, value):
  """A zero/negative limit would silently disable backlog protection."""
  with pytest.raises(ValidationError):
    AnalysisTechnicalAuthorityConfig(**{field: value})


def test_live_freshness_limits_are_configurable_and_default_conservatively():
  config = AnalysisTechnicalAuthorityConfig(max_event_age_seconds=300, max_delivery_lag_seconds=60)
  assert (config.max_event_age_seconds, config.max_delivery_lag_seconds) == (300, 60)
