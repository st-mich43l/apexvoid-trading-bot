"""Read-only comparison of neutral selection and hypothetical quality-based selection on the same labelled candidate population.

Pure functions over label rows (dicts with the ``opportunity_outcome_labels`` columns); ``tools/research_quality.py`` loads the rows. Nothing
here touches production state, and nothing here is a quality model: ``quality_overall`` is the Go engine's published score, read as published.

Design (each choice is there to stop a result from being an artefact):

* One observation per structural episode. Repeated publications of one thesis (same ``thesis_key``, each less than ``episode_gap_seconds``
  after the previous one) are one episode and only its FIRST publication is a candidate, so a single structure cannot count several times.
* Quality bands are walk-forward: a candidate's percentile is its Overall among the strictly EARLIER first-of-episode candidates of the SAME
  strategy (all published ones, filled or not). Scores of different strategies are never compared, nothing from the future is used, and a
  strategy needs ``min_history`` earlier candidates before it contributes.
* One population. Every policy below chooses among the same filled candidates with enough history, under the same hypothetical outcomes
  (``r_low``: ambiguous bars count as the stop, the conservative bound) and costs.
    all         take every filled candidate (the neutral control)
    top_half    percentile >= 0.5 (hypothetical quality-based selection)
    bottom_half percentile <  0.5 (the contrast)
    neutral_k   per strategy and UTC day, the EARLIEST k filled candidates, k = how many top_half chose that day (same trade budget, no score)
* Uncertainty is a day-cluster bootstrap (days are resampled whole, because candidates of one day share a market), not a per-trade one.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass

from app.research.outcome_model import FILL_FILLED

EPISODE_GAP_SECONDS = 86400
MIN_HISTORY = 30
BOOTSTRAP_REPS = 2000


@dataclass
class Candidate:
  row: dict
  publications: int = 1
  pct: float | None = None

  @property
  def strategy(self) -> str:
    return self.row["strategy"]

  @property
  def day(self) -> int:
    return int(self.row["created_at"]) // 86400

  @property
  def filled(self) -> bool:
    return self.row["fill_state"] == FILL_FILLED and self.row.get("r_low") is not None


def apply_r_model(rows: list[dict], r_model: str) -> list[dict]:
  """``target``: first touch of the published stop or target (the label's primary outcome). ``ladder``: the live exit book (50% at 1R, break-even,
  50% at 2R) on the same bars. Both are hypothetical; the rows are copies with ``r_low`` / ``r_high`` / ``outcome`` set from the chosen model."""
  if r_model == "target":
    return rows
  if r_model != "ladder":
    raise ValueError(f"unknown r model {r_model!r}")
  out = []
  for r in rows:
    row = dict(r)
    row["r_low"], row["r_high"], row["outcome"] = r.get("ladder_r_low"), r.get("ladder_r_high"), r.get("ladder_outcome") or r["outcome"]
    out.append(row)
  return out


def usable_rows(rows: list[dict], *, include_gaps: bool = False) -> list[dict]:
  """Complete, non-provisional labels with a published quality score and usable bars."""
  out = []
  for r in rows:
    if not r.get("complete") or r.get("outcome") == "no_data" or r.get("quality_overall") is None:
      continue
    if r.get("data_quality") != "ok" and not (include_gaps and r.get("data_quality") == "gap"):
      continue
    out.append(r)
  return out


def first_of_episode(rows: list[dict], episode_gap: int = EPISODE_GAP_SECONDS) -> list[Candidate]:
  """Collapse repeated publications of one thesis to the first one of each episode."""
  by_thesis: dict[str, list[dict]] = defaultdict(list)
  for r in rows:
    by_thesis[r["thesis_key"]].append(r)
  out: list[Candidate] = []
  for items in by_thesis.values():
    items.sort(key=lambda r: (int(r["created_at"]), r["opportunity_id"]))
    current: Candidate | None = None
    previous_ts = None
    for r in items:
      ts = int(r["created_at"])
      if current is None or previous_ts is None or ts - previous_ts > episode_gap:
        current = Candidate(r)
        out.append(current)
      else:
        current.publications += 1
      previous_ts = ts
  out.sort(key=lambda c: (int(c.row["created_at"]), c.row["opportunity_id"]))
  return out


def assign_walk_forward_percentiles(cands: list[Candidate], min_history: int = MIN_HISTORY) -> None:
  """Percentile of each candidate's Overall among strictly earlier candidates of its own strategy (ties count half); None before min_history."""
  history: dict[str, list[float]] = defaultdict(list)
  for c in cands:
    prior = history[c.strategy]
    score = float(c.row["quality_overall"])
    if len(prior) >= min_history:
      below = sum(1 for p in prior if p < score)
      equal = sum(1 for p in prior if p == score)
      c.pct = (below + 0.5 * equal) / len(prior)
    prior.append(score)


def _metrics(trades: list[Candidate]) -> dict:
  if not trades:
    return {"n": 0}
  ordered = sorted(trades, key=lambda c: (int(c.row["created_at"]), c.row["opportunity_id"]))
  low = [float(c.row["r_low"]) for c in ordered]
  high = [float(c.row["r_high"] if c.row.get("r_high") is not None else c.row["r_low"]) for c in ordered]
  wins, losses = [x for x in low if x > 0], [x for x in low if x < 0]
  peak = cum = drawdown = 0.0
  for x in low:
    cum += x
    peak = max(peak, cum)
    drawdown = max(drawdown, peak - cum)
  return {
    "n": len(low), "mean_r": sum(low) / len(low), "mean_r_high": sum(high) / len(high), "win": len(wins) / len(low),
    "pf": (sum(wins) / -sum(losses)) if losses else math.inf, "max_dd": drawdown,
    "ambiguous": sum(1 for c in ordered if str(c.row["outcome"]).startswith("ambiguous")) / len(ordered),
  }


def select_policies(cands: list[Candidate]) -> dict[str, list[Candidate]]:
  """The four policies over the same filled, banded candidates."""
  trades = [c for c in cands if c.filled and c.pct is not None]
  top = [c for c in trades if c.pct >= 0.5]
  bottom = [c for c in trades if c.pct < 0.5]
  budget: dict[tuple[str, int], int] = defaultdict(int)
  for c in top:
    budget[(c.strategy, c.day)] += 1
  neutral: list[Candidate] = []
  taken: dict[tuple[str, int], int] = defaultdict(int)
  for c in trades:  # already in time order
    key = (c.strategy, c.day)
    if taken[key] < budget.get(key, 0):
      taken[key] += 1
      neutral.append(c)
  return {"all": trades, "top_half": top, "bottom_half": bottom, "neutral_k": neutral}


def _separation(top: list[Candidate], bottom: list[Candidate]) -> float | None:
  """Stratified top-minus-bottom mean R, strata = strategy, weights = top-half trades."""
  by_s: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"t": [], "b": []})
  for c in top:
    by_s[c.strategy]["t"].append(float(c.row["r_low"]))
  for c in bottom:
    by_s[c.strategy]["b"].append(float(c.row["r_low"]))
  num = den = 0.0
  for d in by_s.values():
    if d["t"] and d["b"]:
      w = len(d["t"])
      num += w * (sum(d["t"]) / len(d["t"]) - sum(d["b"]) / len(d["b"]))
      den += w
  return num / den if den else None


def _bootstrap_by_day(top: list[Candidate], bottom: list[Candidate], seed: int = 17, reps: int = BOOTSTRAP_REPS):
  days = sorted({c.day for c in top + bottom})
  if len(days) < 5:
    return None
  by_day_top: dict[int, list[Candidate]] = defaultdict(list)
  by_day_bottom: dict[int, list[Candidate]] = defaultdict(list)
  for c in top:
    by_day_top[c.day].append(c)
  for c in bottom:
    by_day_bottom[c.day].append(c)
  rng = random.Random(seed)
  stats = []
  for _ in range(reps):
    sample = [rng.choice(days) for _ in days]
    t = [c for d in sample for c in by_day_top.get(d, [])]
    b = [c for d in sample for c in by_day_bottom.get(d, [])]
    value = _separation(t, b)
    if value is not None:
      stats.append(value)
  if len(stats) < reps // 2:
    return None
  stats.sort()
  return stats[int(0.025 * len(stats))], stats[int(0.975 * len(stats))]


def report(rows: list[dict], *, episode_gap: int = EPISODE_GAP_SECONDS, min_history: int = MIN_HISTORY, include_gaps: bool = False, r_model: str = "target") -> dict:
  """The whole comparison as a plain dict (JSON-serialisable apart from ``inf``)."""
  usable = usable_rows(apply_r_model(rows, r_model), include_gaps=include_gaps)
  cands = first_of_episode(usable, episode_gap)
  assign_walk_forward_percentiles(cands, min_history)
  banded = [c for c in cands if c.pct is not None]
  policies = select_policies(cands)
  out: dict = {
    "r_model": r_model, "labels_total": len(rows), "labels_usable": len(usable), "episodes": len(cands), "episodes_banded": len(banded),
    "repeat_publications_collapsed": len(usable) - len(cands),
    "fill_rate": {
      "top_half": _fill_rate([c for c in banded if c.pct >= 0.5]), "bottom_half": _fill_rate([c for c in banded if c.pct < 0.5]),
    },
    "pooled": {name: _metrics(items) for name, items in policies.items()},
    "per_strategy": {},
  }
  for strategy in sorted({c.strategy for c in banded}):
    sub = {name: [c for c in items if c.strategy == strategy] for name, items in policies.items()}
    block = {name: _metrics(items) for name, items in sub.items()}
    sep = _separation(sub["top_half"], sub["bottom_half"])
    block["separation"] = sep
    block["separation_ci"] = _bootstrap_by_day(sub["top_half"], sub["bottom_half"])
    out["per_strategy"][strategy] = block
  out["pooled"]["separation"] = _separation(policies["top_half"], policies["bottom_half"])
  out["pooled"]["separation_ci"] = _bootstrap_by_day(policies["top_half"], policies["bottom_half"])
  return out


def _fill_rate(cands: list[Candidate]) -> float | None:
  return None if not cands else sum(1 for c in cands if c.row["fill_state"] == FILL_FILLED) / len(cands)


def render(result: dict) -> str:
  lines = [
    f"r model: {result['r_model']}\nlabels {result['labels_total']}  usable {result['labels_usable']}  episodes {result['episodes']}  banded {result['episodes_banded']}  "
    f"repeat publications collapsed {result['repeat_publications_collapsed']}",
    "hypothetical outcomes (replay, r_low = ambiguous bars count as the stop); not realised trades",
    f"fill rate top_half {_fmt(result['fill_rate']['top_half'], pct=True)}  bottom_half {_fmt(result['fill_rate']['bottom_half'], pct=True)}",
    "",
    f"{'scope':<18}{'policy':<13}{'n':>5}{'meanR':>8}{'meanR_hi':>9}{'win':>6}{'PF':>6}{'maxDD':>7}{'ambig':>7}",
  ]

  def row(scope: str, block: dict) -> None:
    for name in ("all", "top_half", "bottom_half", "neutral_k"):
      m = block[name]
      if m["n"] == 0:
        lines.append(f"{scope:<18}{name:<13}{0:>5}")
        continue
      lines.append(
        f"{scope:<18}{name:<13}{m['n']:>5}{m['mean_r']:>+8.3f}{m['mean_r_high']:>+9.3f}{m['win']:>6.2f}{_fmt(m['pf']):>6}{m['max_dd']:>7.1f}{m['ambiguous']:>7.2f}"
      )
    sep, ci = block.get("separation"), block.get("separation_ci")
    lines.append(f"{scope:<18}{'top-bottom':<13}{'':>5}{_fmt(sep, sign=True):>8}  CI {('[%+.2f, %+.2f]' % ci) if ci else 'N/A (too few days)'}")

  row("POOLED", result["pooled"])
  for strategy, block in result["per_strategy"].items():
    row(strategy, block)
  return "\n".join(lines)


def _fmt(value, *, pct: bool = False, sign: bool = False) -> str:
  if value is None:
    return "N/A"
  if value == math.inf:
    return "inf"
  if pct:
    return f"{100 * value:.0f}%"
  return f"{value:+.3f}" if sign else f"{value:.2f}"
