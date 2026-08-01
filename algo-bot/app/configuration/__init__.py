"""Inactive typed-configuration foundation.

The production application still imports :mod:`app.core.config`; nothing in
this package participates in startup during Phase 2A.
"""

from app.configuration.metadata import ConfigKind
from app.configuration.metadata import ConfigMetadata
from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import MismatchPolicy
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field


__all__ = (
  "ConfigKind",
  "ConfigMetadata",
  "ConfigOwner",
  "ConfigUnit",
  "MismatchPolicy",
  "ReloadPolicy",
  "RiskClassification",
  "config_field",
)
