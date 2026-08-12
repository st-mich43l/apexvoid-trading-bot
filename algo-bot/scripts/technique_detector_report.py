#!/usr/bin/env python3
"""Print technique vs confluence detector enablement for pre-deploy replay checks."""

from __future__ import annotations

import json

from app.analysis.detectors import DetectorSettings, live_detector_report


def main() -> None:
  settings = DetectorSettings()
  rows = live_detector_report(settings)
  technique_rows = [
    row for row in rows
    if "technique" in str(row["name"])
    or str(row["name"]) in {"confluence_zone_reaction", "demand_zone_reaction", "supply_zone_reaction"}
  ]
  print(json.dumps({
    "technique_detectors": technique_rows,
    "zone_reaction_fallback": settings.zone_reaction_fallback_enabled,
  }, indent=2))


if __name__ == "__main__":
  main()
