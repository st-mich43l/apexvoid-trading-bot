"""Deterministic catalog traversal for the inactive canonical schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from types import UnionType
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from app.configuration.models.root import ApexVoidConfig


@dataclass(frozen=True)
class CatalogEntry:
  item_id: str
  path: str
  legacy_attr: str | None
  canonical_env: str | None
  deprecated_aliases: tuple[str, ...]
  type: str
  required: bool
  default: Any
  constraints: dict[str, Any]
  owner: str
  unit: str
  risk_classification: str
  kind: str
  configurable: bool
  protocol_constant: bool
  algorithm_constant: bool
  secret: bool
  shared_with_ctrader: bool
  mismatch_policy: str
  reload_policy: str
  runtime_reload_policy: str
  default_contexts: tuple[dict[str, Any], ...]
  allowed_values: tuple[Any, ...]
  validation_summary: str | None
  evidence_notes: tuple[str, ...]
  description: str
  catalog_version: int
  introduced_in: str
  deprecated: bool
  replacement_path: str | None
  terminal_deprecation_reason: str | None

  def as_dict(self) -> dict[str, Any]:
    return _json_value(asdict(self))


@dataclass(frozen=True)
class DerivedLegacyProperty:
  property_name: str
  source_path: str | None
  source_property: str | None
  transformation: str
  return_type: str

  def as_dict(self) -> dict[str, Any]:
    return asdict(self)


DERIVED_LEGACY_PROPERTIES = (
  DerivedLegacyProperty(
    property_name="signal_vip_channel_id",
    source_path="delivery.telegram.telegram_channel_id",
    source_property=None,
    transformation="identity",
    return_type="int",
  ),
  DerivedLegacyProperty(
    property_name="telegram_chat_id",
    source_path="delivery.telegram.telegram_channel_id",
    source_property=None,
    transformation="str(value)",
    return_type="str",
  ),
  DerivedLegacyProperty(
    property_name="xau_public_channel_id",
    source_path="delivery.telegram.signal_public_channel_id",
    source_property=None,
    transformation="identity",
    return_type="Optional[int]",
  ),
  DerivedLegacyProperty(
    property_name="xau_vip_channel_id",
    source_path=None,
    source_property="signal_vip_channel_id",
    transformation="identity",
    return_type="int",
  ),
)


def _json_value(value: Any) -> Any:
  if isinstance(value, Decimal):
    return str(value)
  if isinstance(value, tuple):
    return [_json_value(item) for item in value]
  if isinstance(value, list):
    return [_json_value(item) for item in value]
  if isinstance(value, dict):
    return {
      str(key): _json_value(item)
      for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }
  return value


def _nested_model(annotation: Any) -> type[BaseModel] | None:
  if isinstance(annotation, type) and issubclass(annotation, BaseModel):
    return annotation
  return None


def _declared_type(annotation: Any, item_id: str) -> str:
  origin = get_origin(annotation)
  args = get_args(annotation)
  if origin in (UnionType, getattr(__import__("typing"), "Union")):
    non_none = [item for item in args if item is not type(None)]
    if len(non_none) == 1:
      inner = _declared_type(non_none[0], item_id)
      if inner == "str":
        return "Optional[str]"
      if inner == "int":
        return "Optional[int]"
  if origin is list and args:
    inner = "string" if args[0] is str else _declared_type(args[0], item_id)
    return f"list[{inner}]"
  if annotation is bool:
    return "bool"
  if annotation is Decimal:
    return "decimal"
  if annotation is float:
    return "float"
  if annotation is int:
    if item_id == "ctrader.env.CTRADER_ACCOUNT_ID":
      return "long"
    return "int"
  if annotation is str:
    return (
      "str"
      if item_id.startswith(("python.settings.", "hardcoded."))
      else "string"
    )
  raise TypeError(f"unsupported catalog annotation {annotation!r} for {item_id}")


def _constraints(field: FieldInfo) -> dict[str, Any]:
  result: dict[str, Any] = {}
  for constraint in field.metadata:
    for name in (
      "ge", "gt", "le", "lt", "min_length", "max_length", "pattern",
    ):
      if hasattr(constraint, name):
        value = getattr(constraint, name)
        if value is not None:
          result[name] = _json_value(value)
  return dict(sorted(result.items()))


def _field_default(field: FieldInfo, *, secret: bool) -> Any:
  if secret:
    return "<redacted>"
  if field.is_required() or field.default is PydanticUndefined:
    return "<required>"
  return _json_value(field.default)


def _iter_fields(
  model: type[BaseModel],
  prefix: tuple[str, ...] = (),
):
  for name, field in model.model_fields.items():
    path = (*prefix, name)
    metadata = (field.json_schema_extra or {}).get("apexvoid_config")
    if metadata is not None:
      yield ".".join(path), field, metadata
    nested = _nested_model(field.annotation)
    if nested is not None:
      yield from _iter_fields(nested, path)


def iter_catalog_entries(
  model: type[BaseModel] = ApexVoidConfig,
) -> tuple[CatalogEntry, ...]:
  entries = []
  for path, field, metadata in _iter_fields(model):
    secret = bool(metadata["secret"])
    entries.append(CatalogEntry(
      item_id=metadata["item_id"],
      path=path,
      legacy_attr=metadata["legacy_attr"],
      canonical_env=metadata["canonical_env"],
      deprecated_aliases=tuple(metadata["deprecated_aliases"]),
      type=_declared_type(field.annotation, metadata["item_id"]),
      required=field.is_required(),
      default=_field_default(field, secret=secret),
      constraints=_constraints(field),
      owner=metadata["owner"],
      unit=metadata["unit"],
      risk_classification=metadata["risk_classification"],
      kind=metadata["kind"],
      configurable=metadata["configurable"],
      protocol_constant=metadata["protocol_constant"],
      algorithm_constant=metadata["algorithm_constant"],
      secret=secret,
      shared_with_ctrader=metadata["shared_with_ctrader"],
      mismatch_policy=metadata["mismatch_policy"],
      reload_policy=metadata["reload_policy"],
      runtime_reload_policy=metadata["runtime_reload_policy"],
      default_contexts=tuple(metadata["default_contexts"]),
      allowed_values=tuple(metadata["allowed_values"]),
      validation_summary=metadata["validation_summary"],
      evidence_notes=tuple(metadata["evidence_notes"]),
      description=metadata["description"],
      catalog_version=metadata["catalog_version"],
      introduced_in=metadata["introduced_in"],
      deprecated=metadata["deprecated"],
      replacement_path=metadata["replacement_path"],
      terminal_deprecation_reason=metadata["terminal_deprecation_reason"],
    ))
  return tuple(sorted(entries, key=lambda item: (item.path, item.item_id)))


def infer_ctrader_type(entry: CatalogEntry) -> str | None:
  """Infer the C# surface type from canonical declaration evidence."""
  if not entry.shared_with_ctrader and entry.owner != "ctrader":
    return None
  summary = entry.validation_summary or ""
  for marker, type_name in (
    ("EnvironmentResolver.IntList", "IReadOnlyList<int>"),
    ("EnvironmentResolver.StringList", "IReadOnlyList<string>"),
    ("EnvironmentResolver.Bool", "bool"),
    ("EnvironmentResolver.Decimal", "decimal"),
    ("EnvironmentResolver.Int", "int"),
    ("EnvironmentResolver.String", "string"),
  ):
    if marker in summary:
      return type_name
  return {
    "bool": "bool",
    "decimal": "decimal",
    "float": "decimal",
    "int": "int",
    "long": "long",
    "str": "string",
    "string": "string",
    "list[int]": "IReadOnlyList<int>",
    "list[string]": "IReadOnlyList<string>",
  }.get(entry.type)
