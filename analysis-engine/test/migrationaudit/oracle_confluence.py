"""Exhaustive grid of the historical Python confluence scorer, for confluence_parity_test.go.

    python oracle_confluence.py --oracle <worktree> --out /tmp/confluence-oracle.json

Calls the frozen ``_confluence_from_zone``, ``_confluence_v2_score`` and ``_stars_from_v2_ratio`` unmodified.
"""
import argparse
import itertools
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--oracle", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
for key, value in {"TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi", "TELEGRAM_CHAT_ID": "-100123456789",
                   "DATABASE_URL": "postgresql://a:b@localhost:5/s", "POSTGRES_PASSWORD": "x"}.items():
  os.environ.setdefault(key, value)
os.environ.setdefault("APEXVOID_CONFIG_FILE", os.path.join(args.oracle, "config", "trading-bot.yml"))
sys.path.insert(0, os.path.join(args.oracle, "algo-bot"))
import app.analysis.detectors as D
from app.analysis.scanner import _detector_settings
from app.analysis.types import Zone

settings = _detector_settings("XAU")
cases = []
bools = (False, True)
for htf, wick, disp, sess, struct, fib, choch in itertools.product(bools, repeat=7):
  for touches in range(0, 6):
    factors = D.ConfluenceFactors(htf_aligned=htf, touches=touches, wick_rejection=wick, displacement_grade=disp,
                                  session_context=sess, structural_agreement=struct, fib_touch=fib, choch=choch)
    for zone_score in (0.0, 3.5, 8.0, 12.0, 12.5, 17.99, 18.0, 24.5, 40.0):
      for zone_touches in (0, 1, 3):
        for mad in (0.0, 0.5, 1.5):
          zone = Zone(bottom=1.0, top=2.0, side="demand", touches=zone_touches, score=zone_score)
          v1 = D._confluence_from_zone(zone, factors, settings)
          raw = D._confluence_v2_score(zone, factors, settings, mad)
          v2 = D._stars_from_v2_ratio(raw / D._v2_score_max(settings), settings)
          cases.append({"htf": htf, "touches": touches, "wick": wick, "disp": disp, "session": sess, "structural": struct,
                        "fib": fib, "choch": choch, "zone_score": zone_score, "zone_touches": zone_touches, "mad": mad,
                        "v1": v1, "v2_raw": raw, "v2": v2})
json.dump({"oracle": args.oracle, "constants": {"factor_max": D._FACTOR_SCORE_MAX, "zone_max": D._ZONE_SCORE_MAX, "v2_max": D._v2_score_max(settings)},
           "cases": cases}, open(args.out, "w"), separators=(",", ":"))
print(len(cases))
