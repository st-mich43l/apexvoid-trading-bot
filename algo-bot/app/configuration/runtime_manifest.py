"""Deterministic, secret-safe ResolvedRuntimeManifest compiler.

The manifest is generated from the canonical resolver output (full ApexVoidConfig
resolution + effective instrument contexts). It does not introduce a second
precedence engine, YAML parser, or ENV parser.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.configuration.catalog import CatalogEntry
from app.configuration.catalog import iter_catalog_entries
from app.configuration.ctrader_option_classification import (
  AUTO_TRADE_OPTIONS_CLASSIFICATION,
  FEED_OPTIONS_CLASSIFICATION,
  _manifest_key,
  assert_complete_classification,
  classification_counts,
  migration_entries,
)


def _manifest_json_key(property_name: str) -> str:
  if property_name == "CTraderSymbol":
    return "ctrader_symbol"
  return _manifest_key(property_name)
from app.configuration.effective_instrument import build_effective_instrument
from app.configuration.fingerprints import configuration_contract_fingerprint
from app.configuration.models.base import FrozenConfigModel
from app.configuration.models.root import ApexVoidConfig
from app.configuration.python_loader import CanonicalConfigurationError
from app.configuration.python_loader import load_python_canonical_settings
from app.configuration.python_sources import load_python_runtime_source_bundle
from app.configuration.resolver import resolve_configuration
from app.configuration.source_policy import PythonConfigurationSourcePolicy
from app.configuration.source_types import ResolutionTrace


MANIFEST_VERSION = 2
MANIFEST_VERSION_V1 = 1

# Placeholders used only so ApexVoidConfig can validate when secrets are absent
# from the compiler process. They must never appear in serialized output.
_COMPILER_SECRET_ENV = {
  "CTRADER_CLIENT_ID": "manifest-compiler-client-id",
  "CTRADER_CLIENT_SECRET": "DO_NOT_LEAK_CTRADER_SECRET",
  "CTRADER_ACCESS_TOKEN": "DO_NOT_LEAK_CTRADER_ACCESS_TOKEN",
  "CTRADER_REFRESH_TOKEN": "DO_NOT_LEAK_CTRADER_REFRESH_TOKEN",
  "CTRADER_ACCOUNT_ID": "1",
  "TELEGRAM_BOT_TOKEN": "DO_NOT_LEAK_TELEGRAM_TOKEN",
  "POSTGRES_PASSWORD": "DO_NOT_LEAK_DATABASE_PASSWORD",
  "DATABASE_URL": "postgresql://apexvoid:DO_NOT_LEAK_DATABASE_PASSWORD@localhost:5432/signals",
}


class RuntimeManifestError(ValueError):
  """Fail-closed runtime manifest error."""


class ResolvedRuntimeManifest(FrozenConfigModel):
  """Versioned cross-service runtime manifest (file-based; not Redis health).

  Version 2 introduces ``instrument_runtimes``. Top-level ``feed`` and
  ``auto_trade`` remain as explicit XAU compatibility projections
  (deprecated for new multi-symbol consumers).
  """

  model_config = ConfigDict(frozen=True, extra="forbid")

  manifest_version: int
  contract_fingerprint: str
  effective_configuration_fingerprint: str
  profile: str
  global_: dict[str, Any] = Field(alias="global")
  instruments: dict[str, Any]
  instrument_runtimes: dict[str, Any] = Field(default_factory=dict)
  feed: dict[str, Any]
  auto_trade: dict[str, Any]
  live_instruments: list[str]

  def model_dump_manifest(self) -> dict[str, Any]:
    return self.model_dump(by_alias=True, mode="python")


def canonical_decimal(value: Any) -> str:
  """Invariant decimal string suitable for cross-language parity."""
  if isinstance(value, bool):
    raise TypeError("boolean is not a decimal")
  try:
    decimal = Decimal(str(value))
  except (InvalidOperation, ValueError) as exc:
    raise RuntimeManifestError(f"invalid decimal value: {value!r}") from exc
  normalized = decimal.normalize()
  text = format(normalized, "f")
  if "." in text:
    text = text.rstrip("0").rstrip(".")
  if text in {"", "-"}:
    text = "0"
  return text


def _json_ready(value: Any, *, path: str) -> Any:
  if value is None:
    return None
  if isinstance(value, bool):
    return value
  if isinstance(value, int) and not isinstance(value, bool):
    return int(value)
  if isinstance(value, float):
    return canonical_decimal(value)
  if isinstance(value, Decimal):
    return canonical_decimal(value)
  if isinstance(value, str):
    return value
  if isinstance(value, (list, tuple)):
    return [_json_ready(item, path=f"{path}[]") for item in value]
  if isinstance(value, Mapping):
    return {
      str(key): _json_ready(item, path=f"{path}.{key}")
      for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }
  if hasattr(value, "model_dump"):
    return _json_ready(value.model_dump(mode="python"), path=path)
  if hasattr(value, "value"):  # enum
    return _json_ready(value.value, path=path)
  raise RuntimeManifestError(
    f"unsupported manifest value type at {path}: {type(value).__name__}"
  )


def fingerprint_payload(payload: Mapping[str, Any]) -> str:
  raw = serialize_manifest_bytes(payload)
  return hashlib.sha256(raw).hexdigest()


def serialize_manifest_bytes(payload: Mapping[str, Any]) -> bytes:
  return (
    json.dumps(
      payload,
      ensure_ascii=False,
      indent=2,
      sort_keys=True,
      separators=(",", ": "),
    )
    + "\n"
  ).encode("utf-8")


def _catalog_by_path() -> dict[str, CatalogEntry]:
  return {entry.path: entry for entry in iter_catalog_entries()}


def _walk_attr(root: Any, dotted: str) -> Any:
  cursor = root
  for part in dotted.split("."):
    if cursor is None:
      return None
    if isinstance(cursor, Mapping):
      cursor = cursor.get(part)
    else:
      cursor = getattr(cursor, part, None)
  return cursor


def _resolve_full_config(
  *,
  config_file: str | None,
  process_environment: Mapping[str, str] | None = None,
) -> tuple[ApexVoidConfig, ResolutionTrace, str]:
  """Resolve full ApexVoidConfig including ctrader-owned trading leaves."""
  policy = PythonConfigurationSourcePolicy(config_file=config_file)
  bundle = load_python_runtime_source_bundle(policy=policy)
  env = dict(process_environment or bundle.process_environment)
  for key, value in _COMPILER_SECRET_ENV.items():
    env.setdefault(key, value)
  resolved = resolve_configuration(
    init_values=bundle.init_values,
    process_environment=env,
    dotenv_values=bundle.dotenv_values,
    file_secret_values=bundle.file_secret_values,
    config_file_values=bundle.config_file_values,
    instruments=bundle.instruments,
    model=ApexVoidConfig,
  )
  if resolved.conflicts:
    conflict = resolved.conflicts[0]
    raise RuntimeManifestError(
      f"configuration conflict at {conflict.path}: {conflict.message}"
    )
  if resolved.missing_required_paths:
    missing = [
      path
      for path in resolved.missing_required_paths
      if not _catalog_by_path().get(path, None) or not _catalog_by_path()[path].secret
    ]
    # Secrets may still be missing if placeholders failed; surface clearly.
    if resolved.missing_required_paths and not all(
      (_catalog_by_path().get(path) and _catalog_by_path()[path].secret)
      or path in resolved.flat_values
      for path in resolved.missing_required_paths
    ):
      # Use placeholder injection into nested input for secret leaves.
      pass
  nested = dict(resolved.nested_input)
  nested["instruments"] = dict(resolved.instruments)
  # Inject secret placeholders into nested structure when absent.
  catalog = _catalog_by_path()
  for path in list(resolved.missing_required_paths):
    entry = catalog.get(path)
    if entry is None:
      continue
    if entry.secret or (
      entry.canonical_env in _COMPILER_SECRET_ENV
    ):
      value = _COMPILER_SECRET_ENV.get(entry.canonical_env or "", "PLACEHOLDER")
      if entry.canonical_env == "CTRADER_ACCOUNT_ID":
        value = 1
      cursor = nested
      parts = path.split(".")
      for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
      cursor[parts[-1]] = value
  try:
    config = ApexVoidConfig.model_validate(nested)
  except ValidationError as exc:
    raise RuntimeManifestError(
      f"full configuration validation failed: {exc.error_count()} error(s)"
    ) from None
  return config, resolved.trace, resolved.profile


def _option_value_from_config(config: ApexVoidConfig, catalog_path: str | None) -> Any:
  if catalog_path is None:
    return None
  return _walk_attr(config, catalog_path)


def _parse_int_list(value: Any) -> list[int]:
  if value is None:
    return []
  if isinstance(value, str):
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return [int(part) for part in parts]
  if isinstance(value, (list, tuple)):
    return [int(item) for item in value]
  raise RuntimeManifestError(f"expected int list, got {type(value).__name__}")


def _parse_string_list(value: Any) -> list[str]:
  if value is None:
    return []
  if isinstance(value, str):
    return [part.strip() for part in value.split(",") if part.strip()]
  if isinstance(value, (list, tuple)):
    return [str(item) for item in value]
  raise RuntimeManifestError(f"expected string list, got {type(value).__name__}")


def _redis_symbol_from_ctrader(symbol: str) -> str:
  normalized = symbol.replace("/", "").upper()
  if normalized.endswith("USD") and len(normalized) > 3:
    return normalized[:-3]
  return normalized


def _symbols_list(config: ApexVoidConfig) -> list[str]:
  raw = config.contract.instrument.symbols
  items = _parse_string_list(raw)
  return sorted({item.upper() for item in items})


_INT_LIST_AUTO_TRADE_PROPS = frozenset({
  "TargetsPips",
  "TargetWeights",
  "RangeTargetsPips",
})

_INT_AUTO_TRADE_PROPS = frozenset({
  "BreakEvenBufferTicks",
  "CandidateMaxAgeSeconds",
  "SpotMaxAgeSeconds",
  "MaxSpreadPips",
  "MaxEntryDistancePips",
  "MinConfluence",
  "PollMilliseconds",
  "MaxTranches",
  "AddMaxAgeBars",
  "AddCooldownBars",
  "AddMinStopPips",
  "ZoneFillTtlBars",
  "TrendStopMinPips",
  "TrendStopMaxPips",
  "BrokerAbsenceConfirmations",
  "BrokerAbsenceRecheckSeconds",
  "BrokerRecoveryTimeoutSeconds",
  "FlipExitBufferPips",
  "FlipConfirmTimeoutSeconds",
  "ZoneCooldownMinutes",
  "CandidateContractVersion",
  "CandidateStorageTtlSeconds",
  "ConfigManifestVersion",
  "RangeBoxScaleOutThresholdPips",
  "RangeBoxScaleOutTriggerPips",
  "PositionMissingConfirmations",
  "PositionMissingRecheckSeconds",
})


def build_feed_projection(config: ApexVoidConfig) -> dict[str, Any]:
  feed: dict[str, Any] = {}
  for prop, (classification, _env, catalog_path) in sorted(
    FEED_OPTIONS_CLASSIFICATION.items()
  ):
    if classification != "manifest":
      continue
    key = _manifest_json_key(prop)
    if prop == "CTraderSymbol":
      value = config.market_data.ctrader_feed.symbol
    elif prop == "ExpectedBroker":
      value = config.contract.account.expected_broker
    elif prop == "Timeframes":
      value = _parse_string_list(
        _option_value_from_config(config, catalog_path)
      )
    else:
      value = _option_value_from_config(config, catalog_path)
    if value is None:
      raise RuntimeManifestError(
        f"feed option {prop} missing at catalog path {catalog_path!r}"
      )
    feed[key] = _json_ready(value, path=f"feed.{key}")
  feed["redis_symbol"] = _redis_symbol_from_ctrader(str(feed["ctrader_symbol"]))
  return feed


def build_auto_trade_projection(config: ApexVoidConfig) -> dict[str, Any]:
  auto: dict[str, Any] = {}
  for prop, (classification, _env, catalog_path) in sorted(
    AUTO_TRADE_OPTIONS_CLASSIFICATION.items()
  ):
    if classification != "manifest":
      continue
    key = _manifest_json_key(prop)
    if prop == "ConfigManifestVersion":
      value = 2
    elif prop == "Symbols":
      value = _symbols_list(config)
    elif prop in _INT_LIST_AUTO_TRADE_PROPS:
      value = _parse_int_list(_option_value_from_config(config, catalog_path))
    elif prop in _INT_AUTO_TRADE_PROPS:
      raw = _option_value_from_config(config, catalog_path)
      if raw is None:
        raise RuntimeManifestError(
          f"auto_trade option {prop} missing at catalog path {catalog_path!r}"
        )
      value = int(Decimal(str(raw)))
    else:
      value = _option_value_from_config(config, catalog_path)
      if value is None and catalog_path is not None:
        raise RuntimeManifestError(
          f"auto_trade option {prop} missing at catalog path {catalog_path!r}"
        )
    auto[key] = _json_ready(value, path=f"auto_trade.{key}")
  return auto


def build_instrument_slice(config: ApexVoidConfig, instrument_id: str) -> dict[str, Any]:
  effective = build_effective_instrument(config, instrument_id)
  provenance = effective.provenance.as_secret_safe_dict()
  return {
    "identity": _json_ready(effective.identity, path=f"instruments.{instrument_id}.identity"),
    "units": _json_ready(effective.units, path=f"instruments.{instrument_id}.units"),
    "targeting": _json_ready(
      effective.targeting,
      path=f"instruments.{instrument_id}.targeting",
    ),
    "market_data": {
      "lookbacks": _json_ready(
        effective.market_data.lookbacks,
        path=f"instruments.{instrument_id}.market_data.lookbacks",
      ),
    },
    "analysis": {
      "zones": _json_ready(
        effective.analysis.zones,
        path=f"instruments.{instrument_id}.analysis.zones",
      ),
    },
    "strategies": _json_ready(
      effective.strategies,
      path=f"instruments.{instrument_id}.strategies",
    ),
    "actionability": _json_ready(
      effective.actionability,
      path=f"instruments.{instrument_id}.actionability",
    ),
    "execution": _json_ready(
      effective.execution,
      path=f"instruments.{instrument_id}.execution",
    ),
    "risk": _json_ready(
      effective.risk,
      path=f"instruments.{instrument_id}.risk",
    ),
    "lifecycle": _json_ready(
      effective.lifecycle,
      path=f"instruments.{instrument_id}.lifecycle",
    ),
    "policy_name": effective.policy_name,
    "provenance": provenance,
  }


def build_global_section(config: ApexVoidConfig) -> dict[str, Any]:
  return {
    "contracts": _json_ready(
      {
        "mode": config.contract.mode,
        "versions": config.contract.versions,
        "streams": config.contract.streams,
        "instrument": {
          "canonical_symbol": config.contract.instrument.canonical_symbol,
          "symbols": config.contract.instrument.symbols,
          "pip_size": config.contract.instrument.pip_size,
          "price_digits": config.contract.instrument.price_digits,
          "contract_units_per_lot": (
            config.contract.instrument.contract_units_per_lot
          ),
        },
        "account": {
          "expected_broker": config.contract.account.expected_broker,
          "require_demo": config.contract.account.require_demo,
        },
      },
      path="global.contracts",
    ),
    "streams": _json_ready(config.contract.streams, path="global.streams"),
    "account_policy": _json_ready(
      {
        "require_demo": config.contract.account.require_demo,
        "expected_broker": config.contract.account.expected_broker,
      },
      path="global.account_policy",
    ),
    "execution_runtime": _json_ready(
      {
        "auto_trade_enabled": config.runtime.auto_trade.enabled,
        "dry_run": config.runtime.auto_trade.dry_run,
        "profile": config.runtime.profile,
      },
      path="global.execution_runtime",
    ),
  }


def assert_no_secrets_in_payload(payload: Mapping[str, Any]) -> None:
  catalog = _catalog_by_path()
  secret_paths = {entry.path for entry in catalog.values() if entry.secret}
  sentinels = {
    "DO_NOT_LEAK_CTRADER_SECRET",
    "DO_NOT_LEAK_CTRADER_ACCESS_TOKEN",
    "DO_NOT_LEAK_CTRADER_REFRESH_TOKEN",
    "DO_NOT_LEAK_TELEGRAM_TOKEN",
    "DO_NOT_LEAK_DATABASE_PASSWORD",
    "CTRADER_CLIENT_SECRET",
    "CTRADER_ACCESS_TOKEN",
    "CTRADER_REFRESH_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "POSTGRES_PASSWORD",
  }

  def walk(node: Any, path: str) -> None:
    if isinstance(node, Mapping):
      for key, value in node.items():
        child = f"{path}.{key}" if path else str(key)
        if child in secret_paths or key in {
          "client_secret",
          "access_token",
          "refresh_token",
          "bot_token",
          "password",
        }:
          raise RuntimeManifestError(
            f"secret path selected for serialization: {child}"
          )
        walk(value, child)
      return
    if isinstance(node, list):
      for item in node:
        walk(item, path)
      return
    if isinstance(node, str):
      upper = node.upper()
      for sentinel in sentinels:
        if sentinel in upper or sentinel in node:
          raise RuntimeManifestError(
            f"secret sentinel leaked into manifest at {path}"
          )

  walk(payload, "")


def build_instrument_runtime(
  config: ApexVoidConfig,
  instrument_id: str,
  *,
  feed_projection: Mapping[str, Any],
  auto_trade_projection: Mapping[str, Any],
) -> dict[str, Any]:
  """Build one instrument runtime projection for manifest V2."""
  slice_ = build_instrument_slice(config, instrument_id)
  rollout = slice_["identity"]["rollout"]
  # XAU retains the shared top-level feed/auto_trade projections for parity.
  # Future instruments receive instrument-identity feed keys without inventing
  # traded execution values in this PR.
  if instrument_id.upper() == "XAU":
    feed = dict(feed_projection)
    auto_trade = dict(auto_trade_projection)
  else:
    instrument = config.instruments.root[instrument_id]
    feed = {
      **{
        key: value
        for key, value in feed_projection.items()
        if key
        not in {
          "ctrader_symbol",
          "redis_symbol",
          "timeframes",
        }
      },
      "ctrader_symbol": instrument.broker_symbol,
      "redis_symbol": instrument.canonical_symbol,
      "timeframes": list(instrument.timeframes),
    }
    auto_trade = {
      **auto_trade_projection,
      "canonical_symbol": instrument.canonical_symbol,
      "symbols": [instrument.canonical_symbol],
      "pip_size": slice_["units"]["pip_size"],
      "contract_size": slice_["units"]["contract_units_per_lot"],
    }
  return {
    "rollout": rollout,
    "identity": slice_["identity"],
    "units": slice_["units"],
    "targeting": slice_["targeting"],
    "feed": _json_ready(feed, path=f"instrument_runtimes.{instrument_id}.feed"),
    "analysis": slice_["analysis"],
    "auto_trade": _json_ready(
      auto_trade,
      path=f"instrument_runtimes.{instrument_id}.auto_trade",
    ),
    "strategies": slice_["strategies"],
    "actionability": slice_["actionability"],
    "execution": slice_["execution"],
    "risk": slice_["risk"],
    "lifecycle": slice_["lifecycle"],
    "policy_name": slice_["policy_name"],
    "provenance": slice_["provenance"],
  }


def upgrade_v1_payload_to_v2(payload: Mapping[str, Any]) -> dict[str, Any]:
  """Map a V1 manifest to a V2-equivalent XAU-only shape (no multi-symbol)."""
  if int(payload.get("manifest_version", 0)) == MANIFEST_VERSION:
    return dict(payload)
  if int(payload.get("manifest_version", 0)) != MANIFEST_VERSION_V1:
    raise RuntimeManifestError(
      f"unsupported manifest_version {payload.get('manifest_version')!r}; "
      f"expected {MANIFEST_VERSION_V1} or {MANIFEST_VERSION}"
    )
  instruments = payload.get("instruments") or {}
  if "XAU" not in instruments:
    raise RuntimeManifestError("V1 manifest requires instruments.XAU")
  xau = instruments["XAU"]
  upgraded = dict(payload)
  upgraded["manifest_version"] = MANIFEST_VERSION
  upgraded["instrument_runtimes"] = {
    "XAU": {
      "rollout": xau["identity"]["rollout"],
      "identity": xau["identity"],
      "units": xau["units"],
      "targeting": xau.get(
        "targeting",
        {"mode": "ladder_pips", "reward_risk": None},
      ),
      "feed": payload["feed"],
      "analysis": xau.get("analysis", {}),
      "auto_trade": payload["auto_trade"],
      "strategies": xau.get("strategies", {}),
      "actionability": xau.get("actionability", {}),
      "execution": xau.get("execution", {}),
      "risk": xau.get("risk", {}),
      "lifecycle": xau.get("lifecycle", {}),
      "policy_name": xau.get("policy_name", "xau_current_v1"),
      "provenance": xau.get("provenance", {"entries": []}),
    }
  }
  # Do not re-fingerprint — V1 mount consumers compare against resolved V2
  # via the compiler, not via silent V1 reinterpretation as multi-symbol.
  return upgraded


def build_resolved_runtime_manifest(
  *,
  config_file: str | None = None,
  process_environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
  """Compile the fingerprinted runtime manifest payload."""
  assert_complete_classification()
  config, _trace, profile = _resolve_full_config(
    config_file=config_file,
    process_environment=process_environment,
  )
  instruments = {
    instrument_id: build_instrument_slice(config, instrument_id)
    for instrument_id in sorted(config.instruments.root)
  }
  # Executable set: live only
  live_ids = [
    instrument_id
    for instrument_id, slice_ in instruments.items()
    if slice_["identity"]["rollout"] == "live"
  ]
  if "XAU" not in instruments:
    raise RuntimeManifestError("manifest requires instruments.XAU")
  if instruments["XAU"]["identity"]["rollout"] != "live":
    raise RuntimeManifestError(
      "production manifest requires instruments.XAU rollout=live"
    )
  feed = build_feed_projection(config)
  auto_trade = build_auto_trade_projection(config)
  instrument_runtimes = {
    instrument_id: build_instrument_runtime(
      config,
      instrument_id,
      feed_projection=feed,
      auto_trade_projection=auto_trade,
    )
    for instrument_id in sorted(instruments)
  }
  payload = {
    "manifest_version": MANIFEST_VERSION,
    "contract_fingerprint": configuration_contract_fingerprint(
      iter_catalog_entries()
    ),
    "profile": profile,
    "global": build_global_section(config),
    "instruments": instruments,
    "instrument_runtimes": instrument_runtimes,
    # Deprecated XAU compatibility projections — prefer instrument_runtimes.XAU.
    "feed": feed,
    "auto_trade": auto_trade,
    "live_instruments": live_ids,
  }
  # Fingerprint excludes nothing else; no generated_at.
  effective = fingerprint_payload(payload)
  payload["effective_configuration_fingerprint"] = effective
  assert_no_secrets_in_payload(payload)
  # Re-validate typed model (unknown keys forbidden).
  ResolvedRuntimeManifest.model_validate(payload)
  return payload


def write_manifest_atomic(payload: Mapping[str, Any], output: Path) -> str:
  output.parent.mkdir(parents=True, exist_ok=True)
  raw = serialize_manifest_bytes(payload)
  fingerprint = hashlib.sha256(raw).hexdigest()
  fd, tmp_name = tempfile.mkstemp(
    prefix=".resolved-runtime.",
    suffix=".json.tmp",
    dir=str(output.parent),
  )
  try:
    with os.fdopen(fd, "wb") as handle:
      handle.write(raw)
      handle.flush()
      os.fsync(handle.fileno())
    os.chmod(tmp_name, 0o644)
    os.replace(tmp_name, output)
  finally:
    if os.path.exists(tmp_name):
      os.unlink(tmp_name)
  return fingerprint


def load_manifest_file(path: Path) -> dict[str, Any]:
  try:
    payload = json.loads(path.read_text(encoding="utf-8"))
  except FileNotFoundError as exc:
    raise RuntimeManifestError(f"manifest file missing: {path}") from exc
  except json.JSONDecodeError as exc:
    raise RuntimeManifestError(f"manifest JSON malformed: {path}") from None
  if not isinstance(payload, dict):
    raise RuntimeManifestError("manifest root must be an object")
  version = payload.get("manifest_version")
  if version == MANIFEST_VERSION_V1:
    payload = upgrade_v1_payload_to_v2(payload)
  elif version != MANIFEST_VERSION:
    raise RuntimeManifestError(
      f"unsupported manifest_version {version!r}; expected "
      f"{MANIFEST_VERSION_V1} or {MANIFEST_VERSION}"
    )
  assert_no_secrets_in_payload(payload)
  ResolvedRuntimeManifest.model_validate(payload)
  return payload


def verify_manifest_matches_resolution(
  *,
  config_file: str | None,
  manifest_path: Path,
  process_environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
  expected = build_resolved_runtime_manifest(
    config_file=config_file,
    process_environment=process_environment,
  )
  actual = load_manifest_file(manifest_path)
  if actual.get("effective_configuration_fingerprint") != expected[
    "effective_configuration_fingerprint"
  ]:
    raise RuntimeManifestError(
      "mounted manifest fingerprint does not match freshly resolved configuration"
    )
  if actual.get("live_instruments") != expected.get("live_instruments"):
    raise RuntimeManifestError("live instrument list mismatch versus mounted manifest")
  return actual


def env_migration_document() -> dict[str, Any]:
  assert_complete_classification()
  counts = classification_counts()
  if counts.get("unclassified", 0):
    raise RuntimeManifestError("unclassified cTrader options remain")
  return {
    "manifest_version": MANIFEST_VERSION,
    "counts": {
      **counts,
      "unclassified": 0,
      "total": sum(counts.values()),
    },
    "entries": migration_entries(),
  }
