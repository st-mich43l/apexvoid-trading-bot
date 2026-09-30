import pytest
from pydantic import ValidationError

from app.configuration.models.analysis import AnalysisTechnicalAuthorityConfig


def test_technical_authority_defaults_to_live_go_with_consumer_on():
  assert AnalysisTechnicalAuthorityConfig().model_dump() == {
    "mode": "go", "consumer_enabled": True,
    "consumer_group": "apexvoid-algo-bot-analysis-opportunity-v1",
    "max_event_age_seconds": 900, "max_delivery_lag_seconds": 300,
    "arbitration_mode": "go",
    "thesis_correlation_mode": "go",
    "stop_envelope_mode": "go",
  }


@pytest.mark.parametrize(
  "values",
  [{"mode": "python"}, {"mode": "go_shadow"}, {"mode": "nope"}],
)
def test_technical_authority_rejects_non_go_modes(values):
  with pytest.raises(ValidationError):
    AnalysisTechnicalAuthorityConfig(**values)


def test_arbitration_mode_defaults_to_go():
  assert AnalysisTechnicalAuthorityConfig().arbitration_mode == "go"


@pytest.mark.parametrize("values", [{"arbitration_mode": "shadow"}, {"arbitration_mode": "nope"}])
def test_arbitration_mode_rejects_unknown_values(values):
  with pytest.raises(ValidationError):
    AnalysisTechnicalAuthorityConfig(**values)


def test_arbitration_mode_accepts_go():
  assert AnalysisTechnicalAuthorityConfig(arbitration_mode="go").arbitration_mode == "go"


def test_go_mode_is_the_default_live_configuration():
  assert AnalysisTechnicalAuthorityConfig(mode="go", consumer_enabled=True).mode == "go"
  assert AnalysisTechnicalAuthorityConfig().mode == "go"


@pytest.mark.parametrize("field", ["max_event_age_seconds", "max_delivery_lag_seconds"])
@pytest.mark.parametrize("value", [0, -1])
def test_live_freshness_limits_must_be_positive(field, value):
  """A zero/negative limit would silently disable backlog protection."""
  with pytest.raises(ValidationError):
    AnalysisTechnicalAuthorityConfig(**{field: value})


def test_live_freshness_limits_are_configurable_and_default_conservatively():
  config = AnalysisTechnicalAuthorityConfig(max_event_age_seconds=300, max_delivery_lag_seconds=60)
  assert (config.max_event_age_seconds, config.max_delivery_lag_seconds) == (300, 60)


def test_thesis_correlation_mode_defaults_to_go():
  assert AnalysisTechnicalAuthorityConfig().thesis_correlation_mode == "go"


@pytest.mark.parametrize("values", [{"thesis_correlation_mode": "shadow"}, {"thesis_correlation_mode": "nope"}])
def test_thesis_correlation_mode_rejects_unknown_values(values):
  with pytest.raises(ValidationError):
    AnalysisTechnicalAuthorityConfig(**values)


def test_thesis_correlation_mode_accepts_go():
  assert AnalysisTechnicalAuthorityConfig(thesis_correlation_mode="go").thesis_correlation_mode == "go"


def test_stop_envelope_mode_defaults_to_go():
  assert AnalysisTechnicalAuthorityConfig().stop_envelope_mode == "go"


@pytest.mark.parametrize("values", [{"stop_envelope_mode": "shadow"}, {"stop_envelope_mode": "nope"}])
def test_stop_envelope_mode_rejects_unknown_values(values):
  with pytest.raises(ValidationError):
    AnalysisTechnicalAuthorityConfig(**values)


def test_stop_envelope_mode_accepts_go():
  assert AnalysisTechnicalAuthorityConfig(stop_envelope_mode="go").stop_envelope_mode == "go"
