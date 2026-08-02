"""Deterministic fingerprints derived from typed configuration metadata."""

from hashlib import sha256
import json

from app.configuration.catalog import iter_catalog_entries


def catalog_fingerprint() -> str:
  payload = (
    json.dumps(
      [entry.as_dict() for entry in iter_catalog_entries()],
      indent=2,
      sort_keys=True,
      ensure_ascii=False,
      separators=(",", ": "),
    )
    + "\n"
  ).encode("utf-8")
  return sha256(payload).hexdigest()

