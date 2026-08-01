"""Cross-strategy actionability model shells."""

from pydantic import Field

from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class StructuralAnchorConfig(FrozenConfigModel):
  required: bool = config_field(
    False,
    item_id="python.settings.scanner_gate_require_structural_anchor",
    legacy_attr="scanner_gate_require_structural_anchor",
    env="SCANNER_GATE_REQUIRE_STRUCTURAL_ANCHOR",
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description="Require a structural anchor before actionability.",
  )
  maximum_source_touches: int = config_field(
    0,
    item_id="python.settings.scanner_gate_max_source_touches",
    legacy_attr="scanner_gate_max_source_touches",
    env="SCANNER_GATE_MAX_SOURCE_TOUCHES",
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.EXECUTION_SAFETY,
    description="Maximum source-zone touches accepted by the gate.",
  )


class ActionabilityConfig(FrozenConfigModel):
  structural_anchor: StructuralAnchorConfig = Field(
    default_factory=StructuralAnchorConfig,
  )
