#!/usr/bin/env python3
"""Stage C1 parity check: every categorized YAML leaf under config/ must
equal the value at its corresponding old path in config/trading-bot.yml
(or, for environments/demo_eval.yml, the live app.configuration.profiles
.DEMO_EVAL_PROFILE assignment list) — see
docs/configuration-v3-migration-audit.md.

This is Stage C1 self-verification only: it proves the mechanical split
preserved every value, nothing about a real loader (that's Stage C2+, and
no runtime reads config/apexvoid.yml or its includes yet). Run from the
repository root:

    python3 config/scripts/verify_stage_c1_parity.py

Requires PyYAML (already an algo-bot dependency: algo-bot/.venv/bin/python
has it) and, for the environment-overlay check, algo-bot's own app package
on PYTHONPATH:

    cd algo-bot && PYTHONPATH=. .venv/bin/python \
      ../config/scripts/verify_stage_c1_parity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "config"

errors: list[str] = []
checked = 0


def load(path: Path):
  with path.open() as f:
    return yaml.safe_load(f)


def flatten(value, prefix: str = "") -> dict[str, object]:
  out: dict[str, object] = {}
  if isinstance(value, dict):
    for k, v in value.items():
      key = f"{prefix}.{k}" if prefix else str(k)
      out.update(flatten(v, key))
  elif isinstance(value, list):
    out[prefix] = value
  else:
    out[prefix] = value
  return out


def compare(old_flat: dict, old_path: str, new_value, label: str) -> None:
  global checked
  checked += 1
  if old_path not in old_flat:
    errors.append(f"MISSING OLD PATH: {label}: old key {old_path!r} not in trading-bot.yml")
    return
  old_value = old_flat[old_path]
  if old_value != new_value:
    errors.append(f"MISMATCH: {label}: old[{old_path}]={old_value!r} != new={new_value!r}")


def compare_subtree(old_flat: dict, old_prefix: str, new_subtree, label: str) -> None:
  for leaf, value in flatten(new_subtree).items():
    old_path = f"{old_prefix}.{leaf}" if leaf else old_prefix
    compare(old_flat, old_path, value, f"{label}.{leaf}")


def verify_base_files() -> None:
  old = load(CONFIG / "trading-bot.yml")
  old_flat = flatten(old)

  analysis_new = load(CONFIG / "analysis.yml")["analysis"]
  core = {
    k: v for k, v in analysis_new.items()
    if k not in ("indicators", "calendar", "ctrader_feed", "scanner", "sessions", "spot", "watcher")
  }
  compare_subtree(old_flat, "analysis", core, "analysis.yml/analysis")
  for new_key, old_prefix in {
    "calendar": "market_data.calendar", "ctrader_feed": "market_data.ctrader_feed",
    "scanner": "market_data.scanner", "sessions": "market_data.sessions",
    "spot": "market_data.spot", "watcher": "market_data.watcher",
  }.items():
    compare_subtree(old_flat, old_prefix, analysis_new[new_key], f"analysis.yml/market_data.{new_key}")

  compare_subtree(old_flat, "execution", load(CONFIG / "execution.yml")["execution"], "execution.yml")

  telegram_new = load(CONFIG / "telegram.yml")["telegram"]
  flattened_keys = {
    "delete_root_on_terminal", "owner_dm_daily_wipe_enabled", "public_show_pips",
    "telegram_channel_id", "signal_public_channel_id",
  }
  for k, v in telegram_new.items():
    prefix = f"delivery.telegram.{k}" if k in flattened_keys else f"delivery.{k}"
    compare_subtree(old_flat, prefix, v, f"telegram.yml/{k}")

  instruments_file = load(CONFIG / "instruments.yml")
  compare_subtree(old_flat, "instrument_packs", instruments_file["instrument_packs"], "instruments.yml/instrument_packs")
  compare_subtree(old_flat, "instruments", instruments_file["instruments"], "instruments.yml/instruments")

  runtime_new = load(CONFIG / "runtime.yml")["runtime"]
  compare_subtree(old_flat, "contract.account", runtime_new["broker"], "runtime.yml/broker")
  compare(old_flat, "contract.mode", runtime_new["execution_contract"]["mode"], "runtime.yml/execution_contract.mode")

  streams = load(CONFIG / "transport.yml")["transport"]["redis_streams"]
  compare(old_flat, "contract.streams.candidates", streams["candidates"], "transport.yml/redis_streams.candidates")
  compare(old_flat, "contract.streams.events", streams["events"], "transport.yml/redis_streams.events")
  compare(old_flat, "contract.streams.trade_plans", streams["trade_plans"], "transport.yml/redis_streams.trade_plans")
  compare(old_flat, "contract.streams.candidate_maximum_length", streams["candidate_maximum_length"], "transport.yml/redis_streams.candidate_maximum_length")
  compare(old_flat, "contract.versions.candidate", streams["candidate_version"], "transport.yml/redis_streams.candidate_version")

  auto_algo_new = load(CONFIG / "auto-algo.yml")["auto_algo"]
  compare(old_flat, "runtime.auto_trade.enabled", auto_algo_new["enabled"], "auto-algo.yml/enabled")
  compare(old_flat, "runtime.auto_trade.dry_run", auto_algo_new["dry_run"], "auto-algo.yml/dry_run")
  compare(old_flat, "runtime.auto_trade.direct_publish_enabled", auto_algo_new["direct_publish_enabled"], "auto-algo.yml/direct_publish_enabled")
  compare(old_flat, "runtime.auto_trade.strategy_match_enabled", auto_algo_new["strategy_match_enabled"], "auto-algo.yml/strategy_match_enabled")
  compare(old_flat, "runtime.scanner.enabled", auto_algo_new["scanner"]["enabled"], "auto-algo.yml/scanner.enabled")
  for section in ("actionability", "lifecycle", "risk", "strategies"):
    compare_subtree(old_flat, section, auto_algo_new[section], f"auto-algo.yml/{section}")

  compare_subtree(old_flat, "manual_algo", load(CONFIG / "manual-algo.yml")["manual_algo"], "manual-algo.yml")


def verify_environment_overlays() -> None:
  sys.path.insert(0, str(REPO_ROOT / "algo-bot"))
  from app.configuration.profiles import CONSERVATIVE_PROFILE, DEMO_EVAL_PROFILE  # noqa: PLC0415

  rename = {
    "actionability": "auto_algo.actionability",
    "contract.account": "runtime.broker",
    "delivery.scanner_cards": "telegram.scanner_cards",
    "execution": "execution",
    "lifecycle": "auto_algo.lifecycle",
    "risk": "auto_algo.risk",
    "runtime.auto_trade.enabled": "auto_algo.enabled",
    "runtime.auto_trade.dry_run": "auto_algo.dry_run",
    "runtime.auto_trade.strategy_match_enabled": "auto_algo.strategy_match_enabled",
    "strategies": "auto_algo.strategies",
  }

  def remap(old_path: str) -> str | None:
    if old_path in rename:
      return rename[old_path]
    for old_prefix, new_prefix in sorted(rename.items(), key=lambda kv: -len(kv[0])):
      if old_path == old_prefix or old_path.startswith(old_prefix + "."):
        return new_prefix + old_path[len(old_prefix):]
    return None

  new_flat = flatten(load(CONFIG / "environments" / "demo_eval.yml"))
  for assignment in DEMO_EVAL_PROFILE.assignments:
    global checked
    checked += 1
    new_path = remap(assignment.path)
    if new_path is None:
      errors.append(f"NO REMAP RULE for demo_eval profile path {assignment.path!r}")
      continue
    if new_path not in new_flat:
      errors.append(f"MISSING in demo_eval.yml: {assignment.path!r} -> expected at {new_path!r}")
      continue
    if new_flat[new_path] != assignment.value:
      errors.append(f"MISMATCH: {assignment.path!r} -> {new_path}: profile={assignment.value!r} yaml={new_flat[new_path]!r}")

  if CONSERVATIVE_PROFILE.assignments:
    errors.append("CONSERVATIVE_PROFILE is no longer empty — update environments/production.yml to match")
  prod = load(CONFIG / "environments" / "production.yml")
  if prod not in (None, {}):
    errors.append(f"environments/production.yml should be empty (matches the empty conservative profile), got {prod!r}")


def main() -> int:
  verify_base_files()
  verify_environment_overlays()
  print(f"checked {checked} leaf values / profile assignments")
  if errors:
    print(f"\n{len(errors)} ERRORS:")
    for e in errors:
      print(" -", e)
    return 1
  print("ALL MATCH — every Stage C1 categorized-YAML leaf equals its old effective value.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
