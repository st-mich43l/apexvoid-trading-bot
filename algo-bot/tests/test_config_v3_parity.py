"""Configuration V3 Stage C3 parity: config/apexvoid.yml (the categorized
V3 root + includes + production overlay) must resolve to the exact same
ApexVoidConfig as config/trading-bot.yml (the historical flat file) —
the whole reason app/configuration/v3_root.py's un-consolidation exists.

See docs/configuration-v3-migration-audit.md's Stage C3 section. This is
a permanent regression guard, not a one-off script: if either file drifts
without a matching update to the other (or to v3_root.py's un-
consolidation map), this test is what catches it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.configuration.python_loader import load_python_canonical_settings
from app.configuration.python_sources import load_python_runtime_source_bundle

pytestmark = pytest.mark.no_database

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TRADING_BOT_YML = _REPO_ROOT / "config" / "trading-bot.yml"
_APEXVOID_YML = _REPO_ROOT / "config" / "apexvoid.yml"


def _build(config_file: Path, monkeypatch: pytest.MonkeyPatch):
  monkeypatch.setenv("APEXVOID_CONFIG_FILE", str(config_file))
  bundle = load_python_runtime_source_bundle()
  return load_python_canonical_settings(bundle)


def _flatten(value, prefix: str = "") -> dict[str, object]:
  out: dict[str, object] = {}
  if isinstance(value, dict):
    for key, sub in value.items():
      out.update(_flatten(sub, f"{prefix}.{key}" if prefix else str(key)))
  else:
    out[prefix] = value
  return out


def test_apexvoid_yml_resolves_identically_to_trading_bot_yml(monkeypatch):
  """The entire non-secret resolved configuration (890 leaves at time of
  writing) must be byte-for-byte identical whether built from the old
  monolithic file or the new categorized V3 chain. `bootstrap` (secrets +
  ENV-sourced infra values) is excluded — those never come from either
  YAML file and are unaffected by this comparison by construction.
  """
  old = _build(_TRADING_BOT_YML, monkeypatch)
  new = _build(_APEXVOID_YML, monkeypatch)

  old_dump = old.config.model_dump(mode="python", exclude={"bootstrap"})
  new_dump = new.config.model_dump(mode="python", exclude={"bootstrap"})

  old_flat = _flatten(old_dump)
  new_flat = _flatten(new_dump)

  diffs = [
    f"{path}: old={old_flat[path]!r} new={new_flat[path]!r}"
    for path in sorted(set(old_flat) & set(new_flat))
    if old_flat[path] != new_flat[path]
  ]
  only_old = sorted(set(old_flat) - set(new_flat))
  only_new = sorted(set(new_flat) - set(old_flat))

  assert not diffs, "differing leaf values:\n" + "\n".join(diffs)
  assert not only_old, f"leaves only in trading-bot.yml result: {only_old}"
  assert not only_new, f"leaves only in apexvoid.yml result: {only_new}"
  assert old_dump == new_dump


def test_apexvoid_yml_instruments_match(monkeypatch):
  old = _build(_TRADING_BOT_YML, monkeypatch)
  new = _build(_APEXVOID_YML, monkeypatch)
  assert old.config.instruments == new.config.instruments


def test_v3_root_document_detected_by_includes_key():
  from app.configuration.v3_root import is_v3_root_document

  assert is_v3_root_document({"version": 3, "includes": ["runtime.yml"]})
  assert not is_v3_root_document({"version": 1, "actionability": {}})
  assert not is_v3_root_document(None)
  assert not is_v3_root_document([])


def test_v3_root_rejects_unsupported_version(tmp_path):
  from app.configuration.v3_root import V3ConfigError, resolve_v3_document

  root = tmp_path / "apexvoid.yml"
  root.write_text("version: 2\nincludes: []\n")
  with pytest.raises(V3ConfigError, match="unsupported version"):
    resolve_v3_document(root)


def test_v3_root_rejects_missing_include(tmp_path):
  from app.configuration.v3_root import V3ConfigError, resolve_v3_document

  root = tmp_path / "apexvoid.yml"
  root.write_text("version: 3\nincludes:\n  - does-not-exist.yml\n")
  with pytest.raises(V3ConfigError, match="missing include"):
    resolve_v3_document(root)


def test_v3_root_rejects_duplicate_include(tmp_path):
  from app.configuration.v3_root import V3ConfigError, resolve_v3_document

  (tmp_path / "a.yml").write_text("foo:\n  bar: 1\n")
  root = tmp_path / "apexvoid.yml"
  root.write_text("version: 3\nincludes:\n  - a.yml\n  - a.yml\n")
  with pytest.raises(V3ConfigError, match="duplicate include"):
    resolve_v3_document(root)


def test_v3_root_rejects_path_escaping_include(tmp_path):
  from app.configuration.v3_root import V3ConfigError, resolve_v3_document

  root = tmp_path / "apexvoid.yml"
  root.write_text("version: 3\nincludes:\n  - ../outside.yml\n")
  with pytest.raises(V3ConfigError, match="escapes"):
    resolve_v3_document(root)


def test_v3_root_rejects_duplicate_base_ownership(tmp_path):
  from app.configuration.v3_root import V3ConfigError, resolve_v3_document

  (tmp_path / "a.yml").write_text("foo:\n  bar: 1\n")
  (tmp_path / "b.yml").write_text("foo:\n  baz: 2\n")
  root = tmp_path / "apexvoid.yml"
  root.write_text("version: 3\nincludes:\n  - a.yml\n  - b.yml\n")
  with pytest.raises(V3ConfigError, match="duplicate base ownership"):
    resolve_v3_document(root)


def test_v3_root_environment_overlay_replaces_scalar_and_extends_map(tmp_path):
  from app.configuration.v3_root import resolve_v3_document

  (tmp_path / "a.yml").write_text("foo:\n  bar: 1\n  baz: 2\n")
  (tmp_path / "environments").mkdir()
  (tmp_path / "environments" / "test.yml").write_text("foo:\n  bar: 99\n")
  root = tmp_path / "apexvoid.yml"
  root.write_text("version: 3\nincludes:\n  - a.yml\n  - environments/test.yml\n")

  resolved = resolve_v3_document(root)
  assert resolved["foo"]["bar"] == 99  # overlay replaced the scalar
  assert resolved["foo"]["baz"] == 2  # untouched sibling survives the merge
  assert resolved["runtime"]["environment"] == "test"


def test_unconsolidate_restores_csv_string_typed_leaves():
  from app.configuration.v3_root import unconsolidate

  resolved = {
    "execution": {"targeting": {"default_ladder_pips": [30, 60, 90]}},
    "analysis": {},
    "auto_algo": {},
    "manual_algo": {},
    "telegram": {},
    "instruments": {},
    "instrument_packs": {},
    "runtime": {},
  }
  old_shape = unconsolidate(resolved)
  assert old_shape["execution"]["targeting"]["default_ladder_pips"] == "30,60,90"


def test_unconsolidate_rejects_unknown_auto_algo_leaf():
  from app.configuration.v3_root import V3ConfigError, unconsolidate

  with pytest.raises(V3ConfigError, match="unknown auto_algo"):
    unconsolidate({"auto_algo": {"not_a_real_section": {}}})
