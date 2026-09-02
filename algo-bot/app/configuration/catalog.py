"""Deterministic catalog traversal for Catalog V2."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from types import UnionType
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from app.configuration.metadata import display_config_id
from app.configuration.models.root import ApexVoidConfig


@dataclass(frozen=True)
class CatalogEntry:
  path: str
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

  @property
  def display_id(self) -> str:
    return display_config_id(self.path)

  def as_dict(self) -> dict[str, Any]:
    payload = _json_value(asdict(self))
    payload["display_id"] = self.display_id
    return payload


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


def _declared_type(
  annotation: Any,
  *,
  path: str,
  owner: str,
  canonical_env: str | None,
  validation_summary: str | None,
) -> str:
  """Infer catalog type from annotation, owner, and C#/ENV metadata."""
  origin = get_origin(annotation)
  args = get_args(annotation)
  if origin in (UnionType, getattr(__import__("typing"), "Union")):
    non_none = [item for item in args if item is not type(None)]
    if len(non_none) == 1:
      inner = _declared_type(
        non_none[0],
        path=path,
        owner=owner,
        canonical_env=canonical_env,
        validation_summary=validation_summary,
      )
      if inner == "str":
        return "Optional[str]"
      if inner == "int":
        return "Optional[int]"
      if inner == "float":
        return "Optional[float]"
  if origin is list and args:
    inner = (
      "string"
      if args[0] is str
      else _declared_type(
        args[0],
        path=path,
        owner=owner,
        canonical_env=canonical_env,
        validation_summary=validation_summary,
      )
    )
    return f"list[{inner}]"
  if annotation is bool:
    return "bool"
  if annotation is Decimal:
    return "decimal"
  if annotation is float:
    return "float"
  if annotation is int:
    if canonical_env == "CTRADER_ACCOUNT_ID":
      return "long"
    return "int"
  if annotation is str:
    summary = validation_summary or ""
    if owner == "ctrader":
      return "string"
    if summary.startswith("direct environment read"):
      return "string"
    if owner == "shared" and (
      summary.startswith("FeedOptions.Env")
      or summary.startswith("EnvironmentResolver.String + AutoTradeOptions")
    ):
      return "string"
    return "str"
  raise TypeError(f"unsupported catalog annotation {annotation!r} for {path}")

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
    owner = metadata["owner"]
    canonical_env = metadata["canonical_env"]
    aliases = metadata.get("deprecated_aliases")
    if aliases is None:
      aliases = metadata.get("deprecated_env_aliases", ())
    entries.append(CatalogEntry(
      path=path,
      canonical_env=canonical_env,
      deprecated_aliases=tuple(aliases),
      type=_declared_type(
        field.annotation,
        path=path,
        owner=owner,
        canonical_env=canonical_env,
        validation_summary=metadata.get("validation_summary"),
      ),
      required=field.is_required(),
      default=_field_default(field, secret=secret),
      constraints=_constraints(field),
      owner=owner,
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
  return tuple(sorted(entries, key=lambda item: item.path))


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
