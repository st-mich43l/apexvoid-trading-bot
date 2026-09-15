# Own scalp mechanism (research-first)

ApexVoid’s scalping identity is **M1 micro + M5 context**, scored from an
ATR-normalized feature vector. This document is the mechanism design.
**Hard gates are deferred** until live/shadow performance tables justify them.

Live allow/block remains the existing heuristic path
(`strategies.py` → activation → publish). Math evaluators stamp
**counterfactuals only**.

## Lane contract

| Lane | Owns | Does not own |
| --- | --- | --- |
| **Scalp** | M1 microstructure archetypes on M5 `ScalpContextSnapshot` | ZoneWatch technique publish |
| **Analysis technique** | Supply Demand / OB / FVG / iFVG / CRT / Range Edge / Fade | M1 scalp archetypes |

MAD soft-favor (`accum`) applies **only** to technique **Range Edge Scalp**.
MAD must not rank or gate the scalp lane ([MAD.md](MAD.md)).

Product language is **scalp** (`family=scalp`, `strategy_mode=scalp_m1`).
Legacy `HFS *` name aliases (`strategy_names.py`) / Redis `scalp:*` keys remain for compatibility.

## Forced dual timeframe

```text
M5 ScalpContextSnapshot  (immutable, Redis-pinned via unified_context)
        +
M1 microstructure        (swings, sweep/reclaim, impulse, breakout, ignition)
        ↓
discover_all → research stamps (features + math counterfactual)
        ↓
activation / ranking / publish   ← unchanged authority
        ↓
performance join (archetype × session × math_agree × outcome)
```

Session/killzone clocks do **not** empty `permitted_archetypes`
(structure/technique decide; see #425). Weak volume is an analysis reject,
not a sterilized permit set.

## State vector \(X_t\)

Built by [`math_features.py`](../../algo-bot/app/scalping/math_features.py):

\[
X_t = [L, V, M, S, R, Q, C] (+ E, \text{session}, VR)
\]

| Symbol | Meaning |
| --- | --- |
| \(L\) | Location (discount/premium via range position) |
| \(V\) | Volatility / zone width in ATR |
| \(M\) | Momentum / impulse ATR |
| \(S\) | Structure / trigger quality |
| \(R\) | Room net of cost |
| \(Q\) | Trigger / reclaim quality |
| \(C\) | Execution cost ATR |
| \(E\) | Exhaustion |

Every discovered opportunity should carry `measured.scalp_features` (observe-only).

## Research function (not a live blocker)

\[
f(X_t, \text{archetype}, \text{structure}) \rightarrow \{\text{score}, \text{reasons}, \text{math\_would\_allow}\}
\]

- **Score**: `unified_scalp_score` when `math_score_inputs` exist; else legacy pip heuristic in ranking (unchanged).
- **math_would_allow**: counterfactual from [`math_strategies.py`](../../algo-bot/app/scalping/math_strategies.py) stamped as `measured.math_counterfactual`.
- Live publish **ignores** `math_would_allow` until a later gate PR.

## Archetype ↔ math model map

| Live archetype | Display | Math model | Notes |
| --- | --- | --- | --- |
| `range_sweep` | Range Sweep Scalp | `liquidity_sweep_reversal` | Primary research pair |
| `impulse_pullback` | Impulse Pullback Scalp | `impulse_pullback_continuation` | Needs origin/extreme in measured |
| `breakout_retest` | Breakout Retest Scalp | `breakout_retest_continuation` | Observe-only; live uses compression box — [OWN_BREAKOUT_TECHNIQUE.md](OWN_BREAKOUT_TECHNIQUE.md) |
| *(technique)* Range Edge | Range Edge Scalp | `range_edge_mean_reversion` | Technique lane only — scalp may stamp research alias, never dual-publish |

## Performance tracking

Observe keys (existing + enriched):

- `scalp:last_math_shadow:{SYMBOL}` — cycle sidecar + `research.agree` rows
- Per-opportunity JSON in lifecycle / last opportunity — includes `scalp_features`, `math_counterfactual`

Join helper: `python -m app.scalping.performance` aggregates rows into

`archetype × session × math_agree → counts, optional expectancy when outcome present`.

## Gate roadmap (later)

Do **not** enable ControlledLivePolicy or hard-block from math until:

1. Enough stamped outcomes (target: dense archetype × session cells)
2. Development + validation expectancy positive on [REPLAY_LAB.md](REPLAY_LAB.md)
3. **Holdout untouched** during tuning (60/20/20)
4. Explicit owner PR that flips math from counterfactual → allow/block

Sequence remains: `research → model → replay → shadow → paper → live gates`.

## Related

- [SCALP_UNIFY_M1_M5.md](SCALP_UNIFY_M1_M5.md) — naming / M1+M5 unify
- [PHASE1_AUDIT.md](PHASE1_AUDIT.md) — pipeline inventory
- [CONTROLLED_LIVE.md](CONTROLLED_LIVE.md) — gated promotion (still disabled)
- [TECHNIQUE_SCALP_REDEFINE.md](TECHNIQUE_SCALP_REDEFINE.md) — MAD off M1 scalping
