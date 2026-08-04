"""Deployment policy for the generated ``.env.example`` template.

The example file is intentionally minimal: it lists the deployment-critical
environment variables an operator sets, sourced from the canonical catalog plus
a small set of non-catalog bootstrap/compose variables. Secret values are never
emitted — they render as typed placeholders. The full contract lives in
``docs/configuration/environment-reference.generated.md``.

Value overrides in ``ENV_EXAMPLE_VALUE_OVERRIDES`` identify intentional
deployment-example choices (profile selection and Compose-parity pins). They
must not duplicate types, aliases, or descriptions — those come from the
catalog.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.configuration.environment_contract import environment_entry_for_path


ENVIRONMENT_REFERENCE_DOC = (
  "docs/configuration/environment-reference.generated.md"
)
REQUIRED_SECRET_PLACEHOLDER = "<required-secret>"
OPTIONAL_SECRET_PLACEHOLDER = "<optional-secret>"
REQUIRED_CHANNEL_PLACEHOLDER = "<required-channel-id>"
REQUIRED_IDENTIFIER_PLACEHOLDER = "<required-identifier>"


@dataclass(frozen=True, slots=True)
class DeploymentEnvVar:
  name: str
  placeholder: str
  comment: str


# Non-catalog deployment variables. The compose-managed Postgres password is
# owned by the database service, not the algo-bot configuration catalog.
# APEXVOID_CONFIG_AUTHORITY was removed in Phase 2I-B: leftover values are
# unmanaged unknown environment variables and do not alter runtime behavior.
EXTRA_DEPLOYMENT_ENV: tuple[DeploymentEnvVar, ...] = (
  DeploymentEnvVar(
    "POSTGRES_PASSWORD",
    REQUIRED_SECRET_PLACEHOLDER,
    "Postgres password (compose-managed database credential).",
  ),
)


# Catalog canonical paths that appear in the minimal example, in file order.
ENV_EXAMPLE_CATALOG_PATHS: tuple[str, ...] = (
  "bootstrap.telegram.bot_token",
  "delivery.telegram.telegram_channel_id",
  "bootstrap.postgres.url",
  "bootstrap.redis.url",
  "runtime.profile",
  "strategies.mapped_zone.enabled",
  "actionability.gates.market_map_guard_enabled",
  "bootstrap.logging.directory",
  "bootstrap.logging.retention_days",
  "bootstrap.logging.file_enabled",
  "bootstrap.ctrader.credentials.client_id",
  "bootstrap.ctrader.credentials.client_secret",
  "bootstrap.ctrader.credentials.access_token",
  "bootstrap.ctrader.credentials.refresh_token",
  "bootstrap.ctrader.credentials.account_id",
)


# Intentional deployment-example overrides. Keys are canonical paths.
# Keep this small: profile selection + root-Compose parity pins only.
ENV_EXAMPLE_VALUE_OVERRIDES: dict[str, str] = {
  "runtime.profile": "demo_eval",
  # Root Compose intentionally keeps mapped-zone + market-map guard off even
  # under demo_eval (direct demo fixture enables them). Preserve that intent.
  "strategies.mapped_zone.enabled": "false",
  "actionability.gates.market_map_guard_enabled": "false",
  "bootstrap.redis.url": "redis://redis:6379/0",
  # Operationally required for local startup even when the catalog marks the
  # field optional (dotenv may supply a compose-default DSN).
  "bootstrap.postgres.url": REQUIRED_SECRET_PLACEHOLDER,
}


def _format_value(entry, path: str) -> str:
  if path in ENV_EXAMPLE_VALUE_OVERRIDES:
    return ENV_EXAMPLE_VALUE_OVERRIDES[path]
  if entry.secret:
    if entry.required:
      return REQUIRED_SECRET_PLACEHOLDER
    return OPTIONAL_SECRET_PLACEHOLDER
  if entry.canonical_env == "SIGNAL_VIP_CHANNEL_ID":
    return REQUIRED_CHANNEL_PLACEHOLDER
  default = entry.default
  if default in (None, "<required>"):
    if entry.required:
      return REQUIRED_IDENTIFIER_PLACEHOLDER
    return ""
  if isinstance(default, bool):
    return "true" if default else "false"
  if isinstance(default, str) and default.startswith("<") and default.endswith(">"):
    if "secret" in default or entry.secret:
      return REQUIRED_SECRET_PLACEHOLDER if entry.required else OPTIONAL_SECRET_PLACEHOLDER
    return REQUIRED_IDENTIFIER_PLACEHOLDER if entry.required else ""
  return str(default)


def render_env_example() -> str:
  """Render the deterministic ``.env.example`` template text."""
  lines = [
    "# Generated minimal environment template. Do not edit manually.",
    "# Regenerate with: python -m app.configuration.generate --write",
    f"# Full contract: {ENVIRONMENT_REFERENCE_DOC}",
    "# Secrets use typed placeholders; never commit real secret values.",
    "",
    "# --- Deployment ---",
  ]
  for extra in EXTRA_DEPLOYMENT_ENV:
    lines.append(f"# {extra.comment}")
    lines.append(f"{extra.name}={extra.placeholder}")
  lines.append("")
  lines.append("# --- Configuration (catalog-backed) ---")
  for path in ENV_EXAMPLE_CATALOG_PATHS:
    entry = environment_entry_for_path(path)
    if entry is None or not entry.canonical_env:
      raise ValueError(f"env-example path has no canonical ENV binding: {path}")
    marker = " (secret)" if entry.secret else ""
    lines.append(f"# {entry.path} [{entry.type}]{marker}")
    lines.append(f"{entry.canonical_env}={_format_value(entry, path)}")
  lines.append("")
  lines.append(
    "# Optional knobs: see docs/configuration/environment-reference.generated.md"
  )
  return "\n".join(lines).rstrip() + "\n"
