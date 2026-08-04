"""Deterministic fingerprints derived from typed configuration metadata."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from app.configuration.catalog import CatalogEntry, iter_catalog_entries

# Contract fields that affect runtime behavior / cross-service handshakes.
_CONTRACT_KEYS = (
  "path",
  "type",
  "required",
  "default",
  "constraints",
  "canonical_env",
  "deprecated_aliases",
  "owner",
  "unit",
  "kind",
  "secret",
  "shared_with_ctrader",
  "mismatch_policy",
  "reload_policy",
  "runtime_reload_policy",
  "allowed_values",
  "deprecated",
  "replacement_path",
  "terminal_deprecation_reason",
  "configurable",
  "protocol_constant",
  "algorithm_constant",
)


def _json_bytes(value: Any) -> bytes:
  return (
    json.dumps(
      value,
      indent=2,
      sort_keys=True,
      ensure_ascii=False,
      separators=(",", ": "),
    )
    + "\n"
  ).encode("utf-8")


def _contract_slice(entry: CatalogEntry) -> dict[str, Any]:
  payload = entry.as_dict()
  return {key: payload.get(key) for key in _CONTRACT_KEYS}


def configuration_contract_fingerprint(
  entries: tuple[CatalogEntry, ...] | None = None,
) -> str:
  payload = _json_bytes([
    _contract_slice(entry)
    for entry in (entries if entries is not None else iter_catalog_entries())
  ])
  return sha256(payload).hexdigest()


def configuration_document_fingerprint(
  entries: tuple[CatalogEntry, ...] | None = None,
) -> str:
  payload = _json_bytes([
    entry.as_dict()
    for entry in (entries if entries is not None else iter_catalog_entries())
  ])
  return sha256(payload).hexdigest()


def catalog_fingerprint() -> str:
  """Backward-compatible alias for the contract fingerprint.

  Startup and config-health consumers should prefer
  ``configuration_contract_fingerprint``. Document generation may use
  ``configuration_document_fingerprint``.
  """
  return configuration_contract_fingerprint()
