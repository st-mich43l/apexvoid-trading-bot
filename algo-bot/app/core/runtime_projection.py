"""Process-bound narrow canonical-config projection (Phase 2I-A).

This is the composition-root-adjacent wrapper over
:func:`app.configuration.runtime_projection.project_from`. Because it reads the
active process ``runtime_config`` (owned by :mod:`app.core.config`), it lives in
``app.core`` rather than inside the configuration package -- the configuration
package must never import the composition root. Production modules replace their
former per-call flat legacy config facade defaults with
``project_runtime_config(<declared field names>)`` from here.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace

from app.configuration.runtime_projection import project_from


def project_runtime_config(field_names: Iterable[str]) -> SimpleNamespace:
  """Build a narrow snapshot of ``field_names`` off the active ``runtime_config``.

  The ``runtime_config`` import is deferred so importing this module never pulls
  the configuration composition root at import time (preserving the lazy
  decoupling several analysis modules rely on).
  """
  from app.core.config import runtime_config

  return project_from(runtime_config, field_names)
