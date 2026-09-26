"""Operator CLI for the S14C Go-vs-Python policy replay.

Two steps, so the heavy legacy-detector pass is run once and reviewed:

  # 1. Python side: the live Supply Demand technique detector on the frames visible at each M5 close
  python -m app.scripts.policy_replay python-observations \\
      --capture ../contracts/analysis/replay/xau-production-capture-20260921.json \\
      --out /reports/python-observations.jsonl [--stride 1]

  # 2. Compare with the Go envelopes from `go run ./cmd/replay -capture ... -envelopes-out ...`
  python -m app.scripts.policy_replay report \\
      --capture ../contracts/analysis/replay/xau-production-capture-20260921.json \\
      --go-envelopes /reports/go-envelopes.jsonl \\
      --python-observations /reports/python-observations.jsonl \\
      --json /reports/policy-replay.json --md /reports/policy-replay.md

Nothing here touches Redis, PostgreSQL, Kafka, Telegram or a broker, and nothing
approves anything: the report lists what agrees, what does not, and what is open.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

from app.autotrade import policy_replay as pr


def _progress(done: int, total: int) -> None:
  if done == total or done % 100 == 0:
    print(f"python replay {done}/{total}", file=sys.stderr)


def cmd_python_observations(args: argparse.Namespace) -> int:
  capture = pr.load_capture(args.capture, derive_h4_from_h1=args.derive_h4)
  replay = pr.replay_python(capture, first_close_at=args.first_close, last_close_at=args.last_close, stride=args.stride, progress=_progress)
  observations = replay.observations
  pr.write_python_observations(args.out, observations)
  m5_closes = [int(ts.timestamp()) + 300 for ts in capture.frames["M5"].index]
  first = args.first_close if args.first_close is not None else m5_closes[0]
  last = args.last_close if args.last_close is not None else m5_closes[-1]
  meta = {
    "stride": args.stride, "window_close_at": [first, last], "capture_sha256": capture.sha256,
    "detectors": ["supply_demand_technique_reaction"], "observations": len(observations),
    "closes_evaluated": replay.evaluated, "closes_skipped_for_warmup": replay.warmup_skipped,
    "python": sys.version.split()[0], "pandas": pd.__version__, "pandas_ta": getattr(sys.modules.get("pandas_ta"), "version", None) or "unknown",
  }
  Path(str(args.out) + ".meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
  print(json.dumps({"observations": len(observations), "stride": args.stride, "out": args.out}))
  return 0


def cmd_report(args: argparse.Namespace) -> int:
  capture = pr.load_capture(args.capture, derive_h4_from_h1=args.derive_h4)
  cases, skipped = pr.load_go_cases(args.go_envelopes)
  observations = pr.read_python_observations(args.python_observations)
  meta_path = Path(args.python_meta) if args.python_meta else Path(str(args.python_observations) + ".meta.json")
  meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
  window = tuple(meta["window_close_at"]) if meta.get("window_close_at") else None
  report = pr.compare(
    observations, cases, capture, tolerance_seconds=args.tolerance_seconds, opposing=not args.no_opposing, skipped=skipped,
    python_source=str(args.python_observations), stride=int(meta.get("stride", 1)),
    go_file_sha256=hashlib.sha256(Path(args.go_envelopes).read_bytes()).hexdigest(), python_window=window,
  )
  Path(args.json).write_text(json.dumps(report, sort_keys=True, **({"separators": (",", ":")} if args.compact else {"indent": 2})) + "\n")
  Path(args.md).write_text(pr.render_markdown(report))
  print(json.dumps({"verdict": report["verdict"], "matching": report["matching"], "gates": report["gates"]}, indent=2))
  return 0


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  sub = parser.add_subparsers(dest="command", required=True)
  py = sub.add_parser("python-observations")
  py.add_argument("--capture", required=True)
  py.add_argument("--out", required=True)
  py.add_argument("--stride", type=int, default=1, help="1 = every M5 close (required for a real verdict)")
  py.add_argument("--derive-h4", action="store_true", help="derive H4 from H1 (the production feed has none; exploration only)")
  py.add_argument("--first-close", type=int, default=None)
  py.add_argument("--last-close", type=int, default=None)
  py.set_defaults(func=cmd_python_observations)
  rep = sub.add_parser("report")
  rep.add_argument("--capture", required=True)
  rep.add_argument("--go-envelopes", required=True)
  rep.add_argument("--python-observations", required=True)
  rep.add_argument("--python-meta", default=None, help="exporter meta JSON (default: <python-observations>.meta.json); records stride and the replayed window")
  rep.add_argument("--json", required=True)
  rep.add_argument("--md", required=True)
  rep.add_argument("--tolerance-seconds", type=int, default=300)
  rep.add_argument("--derive-h4", action="store_true", help="must match how the Go envelopes were replayed (go run ./cmd/replay -derive-h4)")
  rep.add_argument("--no-opposing", action="store_true")
  rep.add_argument("--compact", action="store_true", help="single-line JSON (for committed reports)")
  rep.set_defaults(func=cmd_report)
  return parser


def main(argv: list[str] | None = None) -> int:
  args = build_parser().parse_args(argv)
  try:
    return args.func(args)
  except pr.ReplayError as exc:
    print(json.dumps({"refused": str(exc)}), file=sys.stderr)
    return 2


if __name__ == "__main__":
  sys.exit(main())
