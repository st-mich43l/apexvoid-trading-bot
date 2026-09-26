import pytest
from pydantic import ValidationError

from app.configuration.models.analysis import AnalysisTechnicalAuthorityConfig


def test_technical_authority_defaults_to_python_with_consumer_off():
  assert AnalysisTechnicalAuthorityConfig().model_dump() == {
    "mode": "python", "consumer_enabled": False,
    "consumer_group": "apexvoid-algo-bot-analysis-opportunity-v1",
  }


@pytest.mark.parametrize(
  "values",
  [{"mode": "go_shadow"}, {"mode": "go"}, {"mode": "nope"}],
)
def test_technical_authority_rejects_modes_without_a_consumer_or_unknown_modes(values):
  with pytest.raises(ValidationError):
    AnalysisTechnicalAuthorityConfig(**values)


def test_shadow_mode_requires_explicit_consumer_enablement():
  config = AnalysisTechnicalAuthorityConfig(mode="go_shadow", consumer_enabled=True)
  assert config.mode == "go_shadow"


def test_go_mode_is_accepted_only_with_the_consumer_and_never_by_default():
  """`go` no longer means "unavailable": the per-scope authority fence (and its
  operator-recorded acceptance) is the gate. Enabling the mode moves no scope."""
  assert AnalysisTechnicalAuthorityConfig(mode="go", consumer_enabled=True).mode == "go"
  assert AnalysisTechnicalAuthorityConfig().mode == "python"
