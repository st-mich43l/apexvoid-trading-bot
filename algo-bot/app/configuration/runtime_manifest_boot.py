"""Early-boot verification of the mounted ResolvedRuntimeManifest.

Classified as EARLY_BOOT_ALLOWED for ambient ENV reads of bootstrap paths only.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.configuration.runtime_manifest import (
  RuntimeManifestError,
  verify_manifest_matches_resolution,
)


log = logging.getLogger("runtime_manifest_boot")

MANIFEST_FILE_ENV = "APEXVOID_RUNTIME_MANIFEST_FILE"
CONFIG_FILE_ENV = "APEXVOID_CONFIG_FILE"


def verify_mounted_runtime_manifest_or_raise() -> None:
  manifest_path = os.environ.get(MANIFEST_FILE_ENV)
  if not manifest_path:
    return
  try:
    mounted = verify_manifest_matches_resolution(
      config_file=os.environ.get(CONFIG_FILE_ENV),
      manifest_path=Path(manifest_path),
    )
  except RuntimeManifestError as exc:
    raise SystemExit(f"runtime_manifest_verification_failed: {exc}") from None
  log.info(
    "runtime_manifest_verified fingerprint=%s live=%s",
    mounted["effective_configuration_fingerprint"][:12],
    ",".join(mounted["live_instruments"]),
  )
