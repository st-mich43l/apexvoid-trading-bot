"""Narrow canonical-config projections for production consumers (Phase 2I-A).

This module replaces production ``runtime_config_facade()`` calls. Rather than
exposing the entire flat legacy surface through a persistent facade object,
callers declare only the specific legacy field names that their own logic (and
the immediate sub-helpers they hand the object to) actually consume. The
builder returns a plain, one-shot ``SimpleNamespace`` snapshot whose values are
read from the active canonical ``runtime_config`` through the reviewed
``DIRECT_LEGACY_PATHS`` map -- the *same* traversal ``CanonicalSettingsFacade``
performs -- so value/type parity with both authorities is guaranteed while each
production object stays narrow and auditable.

The snapshot is deliberately not a facade: it has no ``__getattr__`` fallback,
does not expose the full legacy surface, and holds no reference to the config
after construction.

This module lives in the configuration package and therefore stays free of any
``app.core.config`` import (the composition root sits *above* the configuration
package). The process-bound convenience wrapper that reads the active
``runtime_config`` lives in :mod:`app.core.runtime_projection`; production
modules import ``project_runtime_config`` from there.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace

from app.configuration.generated.legacy_access import DIRECT_LEGACY_PATHS


def project_from(config: object, field_names: Iterable[str]) -> SimpleNamespace:
  """Snapshot ``field_names`` off ``config`` via canonical ``DIRECT_LEGACY_PATHS``.

  ``config`` is any canonical settings root (the process ``runtime_config`` in
  production, or a canonical model in tests). Each legacy ``name`` is resolved
  by traversing its reviewed canonical path, matching ``CanonicalSettingsFacade``
  exactly. Unknown names raise ``KeyError`` so a stale field list fails loudly
  instead of silently degrading to a Python default.
  """
  values: dict[str, object] = {}
  for name in field_names:
    path = DIRECT_LEGACY_PATHS.get(name)
    if path is None:
      raise KeyError(f"unknown legacy config field for runtime projection: {name!r}")
    value: object = config
    for part in path:
      value = getattr(value, part)
    values[name] = value
  return SimpleNamespace(**values)
