"""Secret-safe legacy Settings versus canonical shadow parity reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from app.configuration.catalog import DERIVED_LEGACY_PROPERTIES
from app.configuration.catalog import iter_catalog_entries
from app.configuration.source_types import ShadowLoadResult


class ParityStatus(StrEnum):
  EQUAL = "equal"
  VALUE_MISMATCH = "value_mismatch"
  TYPE_MISMATCH = "type_mismatch"
  MISSING_LEGACY = "missing_legacy"
  MISSING_CANONICAL = "missing_canonical"
  VALIDATION_MISMATCH = "validation_mismatch"


@dataclass(frozen=True, slots=True)
class ParityRow:
  legacy_attribute: str
  canonical_path: str
  legacy_type: str
  canonical_type: str
  legacy_value: object
  canonical_value: object
  legacy_source: str
  canonical_source: str
  status: ParityStatus

  def as_dict(self) -> dict[str, Any]:
    values = asdict(self)
    values["status"] = self.status.value
    return values


@dataclass(frozen=True, slots=True)
class ParityReport:
  rows: tuple[ParityRow, ...]

  @property
  def equal_count(self) -> int:
    return sum(row.status is ParityStatus.EQUAL for row in self.rows)

  @property
  def total_count(self) -> int:
    return len(self.rows)

  @property
  def success(self) -> bool:
    return self.equal_count == self.total_count

  def as_dict(self) -> dict[str, Any]:
    return {
      "equal_count": self.equal_count,
      "total_count": self.total_count,
      "success": self.success,
      "rows": [row.as_dict() for row in self.rows],
    }


def _canonical_value(config: object, path: str) -> object:
  value = config
  for part in path.split("."):
    value = getattr(value, part)
  return value


def compare_legacy_settings(
  legacy_settings: object,
  shadow: ShadowLoadResult,
) -> ParityReport:
  entries = tuple(
    sorted(
      (entry for entry in iter_catalog_entries() if entry.legacy_attr),
      key=lambda entry: entry.legacy_attr or "",
    )
  )
  trace = shadow.trace.by_path()
  rows = []
  for entry in entries:
    attribute = entry.legacy_attr or ""
    legacy_missing = not hasattr(legacy_settings, attribute)
    canonical_missing = shadow.config is None
    legacy = None if legacy_missing else getattr(legacy_settings, attribute)
    canonical = (
      None if canonical_missing
      else _canonical_value(shadow.config, entry.path)
    )
    if legacy_missing:
      status = ParityStatus.MISSING_LEGACY
    elif canonical_missing:
      status = ParityStatus.MISSING_CANONICAL
    elif type(legacy) is not type(canonical):
      status = ParityStatus.TYPE_MISMATCH
    elif legacy != canonical:
      status = ParityStatus.VALUE_MISMATCH
    else:
      status = ParityStatus.EQUAL
    redacted_legacy = "<redacted>" if entry.secret else legacy
    redacted_canonical = "<redacted>" if entry.secret else canonical
    rows.append(ParityRow(
      legacy_attribute=attribute,
      canonical_path=entry.path,
      legacy_type=("<missing>" if legacy_missing else type(legacy).__name__),
      canonical_type=(
        "<missing>" if canonical_missing else type(canonical).__name__
      ),
      legacy_value=redacted_legacy,
      canonical_value=redacted_canonical,
      legacy_source=(
        "explicit" if attribute in legacy_settings.model_fields_set
        else "schema_or_profile"
      ),
      canonical_source=(
        "<missing>" if entry.path not in trace
        else trace[entry.path].source_kind.value
      ),
      status=status,
    ))
  return ParityReport(tuple(rows))


def compare_derived_properties(
  legacy_settings: object,
  shadow: ShadowLoadResult,
) -> dict[str, bool]:
  if shadow.config is None:
    return {item.property_name: False for item in DERIVED_LEGACY_PROPERTIES}
  config = shadow.config
  values = {
    "signal_vip_channel_id": config.delivery.telegram.telegram_channel_id,
    "telegram_chat_id": str(config.delivery.telegram.telegram_channel_id),
    "xau_public_channel_id": config.delivery.telegram.signal_public_channel_id,
    "xau_vip_channel_id": config.delivery.telegram.telegram_channel_id,
  }
  return {
    item.property_name: (
      getattr(legacy_settings, item.property_name) == values[item.property_name]
    )
    for item in DERIVED_LEGACY_PROPERTIES
  }
