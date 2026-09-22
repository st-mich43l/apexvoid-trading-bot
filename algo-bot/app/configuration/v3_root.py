"""Configuration V3 root-document resolution and un-consolidation.

Stage C3 (docs/configuration-v3-migration-audit.md): the categorized YAML
under `config/` (apexvoid.yml + its includes + one environment overlay)
becomes the real, live source `load_config_file` reads — not a second,
parallel, unwired system. This module owns exactly two things:

  1. `resolve_v3_document(root_path)` — implement rebuild-configuration-
     architecture.md's §14 include/merge/overlay spec for real, against
     the actual root file on disk (this is the production twin of
     config/scripts/resolve_reference.py's reference implementation;
     that script stays as the standalone reference/fixture generator,
     this module is what the running application actually calls).
  2. `unconsolidate(resolved)` — translate the resolved V3 document (its
     `runtime`/`transport`/`database`/`analysis`/`auto_algo`/
     `manual_algo`/`execution`/`telegram`/`journal`/`instruments`/
     `instrument_packs` categories) back into the OLD flat top-level
     shape (`actionability`/`analysis`/`contract`/`delivery`/`execution`/
     `instrument_packs`/`instruments`/`lifecycle`/`manual_algo`/
     `market_data`/`risk`/`runtime`/`strategies`/`bootstrap`) that
     `ApexVoidConfig` and every existing consumer (`runtime_config.
     actionability.foo`, `runtime_config.delivery.bar`, ...) already
     expect.

Un-consolidating rather than rewriting every consumer to the new category
names is a deliberate choice, not a shortcut: it makes the categorized
YAML the real source of truth for a live process while keeping the
blast radius to one function instead of every module that imports
`runtime_config`. See the audit's Stage C3 section for the reasoning.

`config_file.py::load_config_file` calls into this module when the file
it's given is a V3 root document (has an `includes:` key); a plain
flat-shape file (the historical `trading-bot.yml` layout) is untouched
and keeps working exactly as before — this module changes nothing about
that path.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml


class V3ConfigError(ValueError):
  """A V3 root/include/overlay resolution or un-consolidation failure."""


def is_v3_root_document(document: object) -> bool:
  return isinstance(document, dict) and "includes" in document


def _load_yaml(path: Path) -> Any:
  try:
    text = path.read_text(encoding="utf-8")
  except OSError as exc:
    raise V3ConfigError(f"unreadable: {path}: {exc}") from None
  try:
    return yaml.safe_load(text)
  except yaml.YAMLError as exc:
    raise V3ConfigError(f"malformed YAML: {path}: {exc}") from None


def _deep_merge(base: Any, overlay: Any) -> Any:
  """§14: mappings deep-merge; scalars and lists are replaced wholesale."""
  if not isinstance(base, dict) or not isinstance(overlay, dict):
    return copy.deepcopy(overlay)
  out = copy.deepcopy(base)
  for key, value in overlay.items():
    if key in out and isinstance(out[key], dict) and isinstance(value, dict):
      out[key] = _deep_merge(out[key], value)
    else:
      out[key] = copy.deepcopy(value)
  return out


def resolve_v3_document(root_path: Path) -> dict[str, Any]:
  """Resolve a V3 root document's includes + environment overlay.

  `root_path` is the file APEXVOID_CONFIG_FILE points at — an
  `apexvoid.yml`-shaped document with `version: 3` and an `includes:`
  list. Includes and the environment overlay are resolved relative to
  `root_path`'s own directory (normally `config/`), matching how the
  root file's own `includes:` entries are written (relative to
  `config/`, per docs/configuration.md).
  """
  root = _load_yaml(root_path)
  if not isinstance(root, dict):
    raise V3ConfigError(f"{root_path}: top-level V3 root document must be a mapping")
  if root.get("version") != 3:
    raise V3ConfigError(
      f"{root_path}: unsupported version {root.get('version')!r} — only 3 is supported (§18)"
    )
  includes = root.get("includes")
  if not isinstance(includes, list) or not includes:
    raise V3ConfigError(f"{root_path}: 'includes' must be a non-empty list")

  base_dir = root_path.parent
  merged: dict[str, Any] = {"version": 3}
  seen: set[str] = set()
  environment_overlay: dict[str, Any] | None = None
  environment_name: str | None = None

  for include in includes:
    if not isinstance(include, str) or not include:
      raise V3ConfigError(f"{root_path}: invalid include entry {include!r}")
    if include in seen:
      raise V3ConfigError(f"{root_path}: duplicate include {include!r}")
    seen.add(include)
    if include.startswith("/") or ".." in Path(include).parts:
      raise V3ConfigError(f"{root_path}: include {include!r} escapes the config root — not allowed")
    include_path = base_dir / include
    if not include_path.is_file():
      raise V3ConfigError(f"{root_path}: missing include {include!r}")

    doc = _load_yaml(include_path) or {}
    if not isinstance(doc, dict):
      raise V3ConfigError(f"{include_path}: top-level value must be a mapping")

    if include.startswith("environments/"):
      # The environment overlay is applied last, after every base
      # category file, never merged in include order (§10/§12).
      if environment_overlay is not None:
        raise V3ConfigError(f"{root_path}: more than one environments/*.yml include")
      environment_overlay = doc
      environment_name = Path(include).stem
      continue

    for top_key, value in doc.items():
      if top_key in merged and top_key != "version":
        raise V3ConfigError(
          f"{root_path}: duplicate base ownership of top-level key {top_key!r} "
          f"(already set before {include!r} was included)"
        )
      merged[top_key] = value

  if environment_overlay is not None:
    merged = _deep_merge(merged, environment_overlay)
  merged.setdefault("runtime", {})
  merged["runtime"]["environment"] = environment_name or "production"
  return merged


def _flatten(value: Any, prefix: tuple[str, ...] = ()) -> dict[str, Any]:
  """Turn a nested mapping into {dotted.path: leaf_value}. Non-dict
  values (including lists) are leaves — matches the same convention
  config/scripts/*_parity.py already use."""
  out: dict[str, Any] = {}
  if isinstance(value, dict):
    for key, sub in value.items():
      out.update(_flatten(sub, (*prefix, str(key))))
  else:
    out[".".join(prefix)] = value
  return out


def _unflatten(flat: dict[str, Any]) -> dict[str, Any]:
  root: dict[str, Any] = {}
  for path, value in flat.items():
    cursor = root
    parts = path.split(".")
    for part in parts[:-1]:
      cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value
  return root


# Category roots that rename 1:1 onto an old top-level catalog group with
# no internal restructuring (new category key -> old catalog root key).
_DIRECT_CATEGORY_RENAME = {
  "manual_algo": "manual_algo",
  "execution": "execution",
  "analysis": "analysis",
}

# auto_algo.* fans out to five different old root groups depending on the
# leaf's own first path segment — mirrors docs/configuration-v3-migration-
# audit.md's Stage C1 category table exactly, in reverse.
_AUTO_ALGO_SUBTREE_TO_OLD_ROOT = {
  "actionability": "actionability",
  "lifecycle": "lifecycle",
  "risk": "risk",
  "strategies": "strategies",
}


def _unconsolidate_auto_algo(auto_algo: dict[str, Any]) -> dict[str, dict[str, Any]]:
  out: dict[str, dict[str, Any]] = {}
  runtime_auto_trade: dict[str, Any] = {}
  for key, value in auto_algo.items():
    if key == "enabled":
      runtime_auto_trade["enabled"] = value
    elif key == "dry_run":
      runtime_auto_trade["dry_run"] = value
    elif key == "direct_publish_enabled":
      runtime_auto_trade["direct_publish_enabled"] = value
    elif key == "strategy_match_enabled":
      runtime_auto_trade["strategy_match_enabled"] = value
    elif key == "scanner":
      out.setdefault("runtime", {})["scanner"] = value
    elif key in _AUTO_ALGO_SUBTREE_TO_OLD_ROOT:
      out[_AUTO_ALGO_SUBTREE_TO_OLD_ROOT[key]] = value
    else:
      raise V3ConfigError(f"un-consolidation: unknown auto_algo.{key} — no old-shape mapping")
  if runtime_auto_trade:
    out.setdefault("runtime", {})["auto_trade"] = runtime_auto_trade
  return out


def _unconsolidate_telegram(telegram: dict[str, Any]) -> dict[str, Any]:
  # Inverse of Stage C1's flatten (telegram.yml's own header comment):
  # delivery.telegram.{delete_root_on_terminal, owner_dm_daily_wipe_enabled,
  # public_show_pips, telegram_channel_id, signal_public_channel_id} were
  # promoted to this file's top level; everything else keeps its old
  # sub-key (lifecycle/presentation/reports/scanner_cards).
  flattened_keys = {
    "delete_root_on_terminal", "owner_dm_daily_wipe_enabled",
    "public_show_pips", "telegram_channel_id", "signal_public_channel_id",
  }
  delivery: dict[str, Any] = {}
  telegram_sub: dict[str, Any] = {}
  for key, value in telegram.items():
    if key in flattened_keys:
      telegram_sub[key] = value
    else:
      delivery[key] = value
  if telegram_sub:
    delivery["telegram"] = telegram_sub
  return delivery


def _unconsolidate_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
  """§11 in reverse: nested category-rooted override -> flat dotted-path
  string keyed dict, the shape InstrumentDeclaration.overrides actually
  requires (dict[str, Any], a mapping of dotted path to value — see
  app/configuration/models/instruments.py). The six leaves that also
  renamed their category root while un-dotting (auto_algo.actionability.*
  etc.) are mapped back to their real old dotted path here, the same
  reverse-mapping this module applies everywhere else — see
  config/scripts/verify_stage_c2_parity.py's DIVERGENCES table for the
  authoritative list of which leaves these are.
  """
  flat_new = _flatten(overrides)
  out: dict[str, Any] = {}
  for path, value in flat_new.items():
    if path.startswith("auto_algo."):
      rest = path[len("auto_algo."):]
      first, _, remainder = rest.partition(".")
      if first == "actionability":
        out[f"actionability.{remainder}"] = value
      elif first == "lifecycle":
        out[f"lifecycle.{remainder}"] = value
      elif first == "risk":
        out[f"risk.{remainder}"] = value
      elif first == "strategies":
        out[f"strategies.{remainder}"] = value
      else:
        raise V3ConfigError(f"un-consolidation: unknown overrides.auto_algo.{first}.*")
    else:
      out[path] = value
  return out


def _unconsolidate_instruments_block(instruments_or_packs: dict[str, Any]) -> dict[str, Any]:
  out: dict[str, Any] = {}
  for name, body in instruments_or_packs.items():
    body = dict(body) if isinstance(body, dict) else body
    if isinstance(body, dict) and "overrides" in body and isinstance(body["overrides"], dict):
      body = {**body, "overrides": _unconsolidate_overrides(body["overrides"])}
    out[name] = body
  return out


def _unconsolidate_bootstrap(resolved: dict[str, Any]) -> dict[str, Any]:
  """runtime.logging.* / transport.redis.url / database.postgres.{database,
  username} -> bootstrap.{logging,redis,postgres}.* — the non-secret
  bootstrap catalog leaves (see app/configuration/models/bootstrap.py).
  Secret leaves (postgres.password/url, telegram.bot_token, every
  ctrader.credentials.* field) have no YAML source at all, by design —
  §13's secrets policy — and are simply not produced here; they keep
  reaching ApexVoidConfig exactly as before, via the dotenv/process-env
  layers in python_sources.py, untouched by this module.
  """
  out: dict[str, Any] = {}
  runtime = resolved.get("runtime", {})
  logging_cfg = runtime.get("logging")
  if isinstance(logging_cfg, dict):
    bootstrap_logging: dict[str, Any] = {}
    if "directory" in logging_cfg:
      bootstrap_logging["directory"] = logging_cfg["directory"]
    if "retention_days" in logging_cfg:
      bootstrap_logging["retention_days"] = logging_cfg["retention_days"]
    if "file_enabled" in logging_cfg:
      bootstrap_logging["file_enabled"] = logging_cfg["file_enabled"]
    if "level" in logging_cfg:
      bootstrap_logging["level"] = logging_cfg["level"]
    file_names = logging_cfg.get("file_name")
    if isinstance(file_names, dict) and "ctrader-engine" in file_names:
      bootstrap_logging["ctrader_file_name"] = file_names["ctrader-engine"]
    if bootstrap_logging:
      out.setdefault("bootstrap", {})["logging"] = bootstrap_logging

  transport = resolved.get("transport", {})
  redis_cfg = transport.get("redis")
  if isinstance(redis_cfg, dict) and "url" in redis_cfg:
    out.setdefault("bootstrap", {})["redis"] = {"url": redis_cfg["url"]}

  database = resolved.get("database", {})
  postgres_cfg = database.get("postgres")
  if isinstance(postgres_cfg, dict):
    bootstrap_postgres: dict[str, Any] = {}
    if "database" in postgres_cfg:
      bootstrap_postgres["db"] = postgres_cfg["database"]
    if "username" in postgres_cfg:
      bootstrap_postgres["user"] = postgres_cfg["username"]
    if bootstrap_postgres:
      out.setdefault("bootstrap", {})["postgres"] = bootstrap_postgres

  return out


def unconsolidate(resolved: dict[str, Any]) -> dict[str, Any]:
  """Translate a resolved V3 document into the old flat top-level shape
  `load_config_file`'s existing catalog-flattening logic already expects.
  Raises V3ConfigError on any category leaf this module doesn't know how
  to place — a silent drop here would silently disable a real setting,
  exactly what §41 forbids.
  """
  out: dict[str, Any] = {"version": 1}

  for new_key, old_key in _DIRECT_CATEGORY_RENAME.items():
    if new_key in resolved:
      out[old_key] = copy.deepcopy(resolved[new_key])

  if "auto_algo" in resolved:
    for old_key, value in _unconsolidate_auto_algo(resolved["auto_algo"]).items():
      if old_key in out and isinstance(out[old_key], dict) and isinstance(value, dict):
        out[old_key] = _deep_merge(out[old_key], value)
      else:
        out[old_key] = value

  if "telegram" in resolved:
    out["delivery"] = _unconsolidate_telegram(resolved["telegram"])

  if "instruments" in resolved:
    out["instruments"] = _unconsolidate_instruments_block(resolved["instruments"])
  if "instrument_packs" in resolved:
    out["instrument_packs"] = _unconsolidate_instruments_block(resolved["instrument_packs"])

  # runtime.broker / runtime.execution_contract -> contract.account / contract.mode
  runtime = resolved.get("runtime", {})
  contract: dict[str, Any] = {}
  broker = runtime.get("broker")
  if isinstance(broker, dict):
    contract["account"] = broker
  execution_contract = runtime.get("execution_contract")
  if isinstance(execution_contract, dict) and "mode" in execution_contract:
    contract["mode"] = execution_contract["mode"]

  # runtime.environment -> runtime.profile, in the OLD profile naming —
  # real consumers (app/autotrade/config_health.py, lifecycle.py,
  # delivery.py) read runtime_config.runtime.profile for diagnostics and
  # do exact string comparisons against "demo_eval"/"conservative". This
  # is the one place old naming and new naming genuinely differ (Stage
  # C1's environments/production.yml corresponds to the old profile name
  # "conservative", not "production" — see docs/configuration-v3-
  # migration-audit.md §9) and must be bridged explicitly, not silently
  # left to whatever the schema default happens to be.
  environment = runtime.get("environment")
  profile_by_environment = {"production": "conservative", "demo_eval": "demo_eval"}
  if environment in profile_by_environment:
    out.setdefault("runtime", {})["profile"] = profile_by_environment[environment]
  elif environment is not None:
    raise V3ConfigError(
      f"un-consolidation: unknown environment {environment!r} — no old profile-name mapping"
    )

  # transport.redis_streams -> contract.streams / contract.versions
  transport = resolved.get("transport", {})
  streams = transport.get("redis_streams")
  if isinstance(streams, dict):
    contract_streams = {
      k: v for k, v in streams.items()
      if k in ("candidates", "events", "trade_plans", "candidate_maximum_length")
    }
    if contract_streams:
      contract["streams"] = contract_streams
    if "candidate_version" in streams:
      contract["versions"] = {"candidate": streams["candidate_version"]}
  if contract:
    out["contract"] = contract

  # analysis.yml's market_data.* leaves (calendar/scanner/sessions/spot/
  # watcher) live inside the resolved "analysis" category (Stage C1's own
  # choice — see that file's header comment) but belong under the old
  # top-level "market_data" group, not "analysis".
  if "analysis" in out:
    analysis = out["analysis"]
    market_data: dict[str, Any] = {}
    for key in ("calendar", "scanner", "sessions", "spot", "watcher"):
      if key in analysis:
        market_data[key] = analysis.pop(key)
    # analysis.indicators / analysis.zones.merge_overlap / analysis.
    # measurements.* are Stage C2 additions with no old catalog leaf at
    # all (either brand new — indicators.atr.algorithm didn't exist as a
    # concept — or newly surfaced from a Python-only default, §8/§9);
    # the old ApexVoidConfig schema has no home for them, so they are
    # dropped here rather than raising: they don't change old-shape
    # behavior because the old shape never read them in the first place.
    analysis.pop("indicators", None)
    if "zones" in analysis and isinstance(analysis["zones"], dict):
      analysis["zones"].pop("merge_overlap", None)
      if not analysis["zones"]:
        analysis.pop("zones")
    analysis.pop("measurements", None)
    if market_data:
      out["market_data"] = market_data

  bootstrap_contrib = _unconsolidate_bootstrap(resolved)
  if bootstrap_contrib:
    out.update(bootstrap_contrib)

  if "journal" in resolved:
    # No old top-level "journal" group exists in ApexVoidConfig at all —
    # this is a genuinely new category (Stage C1's own note: nothing in
    # the old system reads a journal.* setting, the whole file records
    # already-always-on observed behavior). Nothing to un-consolidate
    # onto; dropped deliberately, not silently — same reasoning as the
    # analysis Stage C2 additions above.
    pass

  return _restore_csv_string_types(out)


def _restore_csv_string_types(old_shape: dict[str, Any]) -> dict[str, Any]:
  """Stage C2 (§10) converted several old CSV-string catalog leaves to
  native YAML lists. The Pydantic catalog still types those specific
  leaves `str` (app/configuration/models/*.py — changing that too is real
  code-level V3 adoption for consumers that still `.split(",")` them,
  tracked as later Stage C3 work, not silently bundled in here). Un-
  consolidation must hand the old validation pipeline a string or it
  fails closed with a type error — rejoin any list-valued leaf the
  catalog still expects as a string, using the catalog's own type label
  as the source of truth (not a hardcoded list of the eight known cases),
  so a future CSV->list conversion doesn't quietly break startup again
  without a matching model update.
  """
  from app.configuration.catalog import iter_catalog_entries  # deferred: avoids a module-load-order cycle with config_file.py

  str_paths = {entry.path for entry in iter_catalog_entries() if entry.type == "str"}

  def format_item(item: Any) -> str:
    # A whole-number float (160.0) formats as "160", matching how the old
    # CSV strings were actually written (e.g. instruments.USDJPY.overrides'
    # defended_levels: '160') rather than introducing a cosmetic ".0".
    if isinstance(item, float) and item.is_integer():
      return str(int(item))
    return str(item)

  def rejoin(value: Any, path: str) -> Any:
    if path in str_paths and isinstance(value, list):
      return ",".join(format_item(item) for item in value)
    return value

  flat = _flatten({k: v for k, v in old_shape.items() if k not in ("instruments", "instrument_packs")})
  for path, value in list(flat.items()):
    flat[path] = rejoin(value, path)
  rebuilt = _unflatten(flat)

  # instruments.*/instrument_packs.*.overrides are dotted-string-keyed
  # dicts applied against the same catalog paths at override time — a
  # list value there hits the identical str-typed-leaf problem (found
  # live: instruments.USDJPY.overrides["risk.exposure.defended_levels"]
  # after Stage C2's §10 conversion of that leaf to a float list).
  for section in ("instruments", "instrument_packs"):
    block = old_shape.get(section)
    if not isinstance(block, dict):
      continue
    for name, body in block.items():
      if not isinstance(body, dict):
        continue
      overrides = body.get("overrides")
      if isinstance(overrides, dict):
        for override_path, value in list(overrides.items()):
          overrides[override_path] = rejoin(value, override_path)

  rebuilt["instruments"] = old_shape.get("instruments", {})
  rebuilt["instrument_packs"] = old_shape.get("instrument_packs", {})
  rebuilt["version"] = old_shape.get("version", 1)
  return rebuilt
