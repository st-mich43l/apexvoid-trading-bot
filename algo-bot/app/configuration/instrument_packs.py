"""Merge named instrument packs into per-symbol declarations."""

from __future__ import annotations

import copy
from typing import Any


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
  merged = copy.deepcopy(base)
  for key, value in override.items():
    if (
      key in merged
      and isinstance(merged[key], dict)
      and isinstance(value, dict)
    ):
      merged[key] = _deep_merge(merged[key], value)
    else:
      merged[key] = copy.deepcopy(value)
  return merged


def expand_instrument_declarations(
  instruments: dict[str, Any],
  packs: dict[str, Any] | None,
) -> dict[str, Any]:
  """Resolve ``pack: <name>`` references into full instrument blocks."""
  if not instruments:
    return {}
  pack_index = {
    str(name).strip(): body
    for name, body in (packs or {}).items()
    if isinstance(body, dict)
  }
  expanded: dict[str, Any] = {}
  for instrument_id, raw in instruments.items():
    if not isinstance(raw, dict):
      raise ValueError(
        f"instrument {instrument_id!r} must be a mapping"
      )
    body = copy.deepcopy(raw)
    pack_name = body.pop("pack", None)
    if pack_name is not None:
      token = str(pack_name).strip()
      if token not in pack_index:
        known = ", ".join(sorted(pack_index)) or "(none)"
        raise ValueError(
          f"instrument {instrument_id!r} references unknown pack {token!r}; "
          f"known packs: {known}"
        )
      body = _deep_merge(pack_index[token], body)
    expanded[str(instrument_id)] = body
  return expanded


def live_instrument_symbol_csv(instruments: dict[str, Any]) -> str:
  """Comma-separated canonical symbols for rollout=live instruments."""
  from app.configuration.models.instruments import (
    InstrumentConfig,
    effective_rollout,
    InstrumentRollout,
  )

  live: list[str] = []
  for instrument_id, raw in instruments.items():
    if not isinstance(raw, dict):
      continue
    try:
      instrument = InstrumentConfig.model_validate(raw)
    except Exception:
      continue
    if effective_rollout(instrument) is InstrumentRollout.LIVE:
      live.append(instrument.canonical_symbol.strip().upper())
  if "XAU" not in live:
    live.insert(0, "XAU")
  return ",".join(dict.fromkeys(live))
