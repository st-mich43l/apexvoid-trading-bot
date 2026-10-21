# Q4: the 22-27 September logic against the Go Analysis Engine

Scope: recover the technical rules the Python scanner applied on 22-27 September 2026 and compare them with the Go engine on identical candles. Python is an isolated offline oracle (read-only worktrees, a venv); nothing here connects it to production. Results: [results JSON](q4-september-logic-results.json). Method and tools are those of [Q3b](q3-september-baseline-recovery.md) (`test/keylevelparity`, `test/aequality` with `AEQ_PARAMS`, `tools/q3b_key_level_ledger.py`) plus `tools/q4_contract_ab.py`.

## 0. Verdict

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | What logic ran on 22-27 Sep? | The Python scanner at `a75b6559`: the profitable-week code (`a1c77584`) plus **five owner-requested analysis changes of 21 Sep**, with settings unchanged (only pack names and feed timeframes differ in the config). The Go engine ran only in shadow until 28 Sep. | git history, config diff |
| 2 | Is there data to replay 22-27 Sep? | **No at M5.** Every capture's M5 ends on 21 Sep 14:30 UTC or starts on 28 Sep; H1 covers the days, M15 covers part of 25-27 Sep for the FX pairs and XAU. Key Level and the zone reactions are M5 decisions. | capture inventory |
| 3 | Is there a record of the reported performance? | **None I can reach.** `auto_trade_results` starts 29 Sep 20:32 UTC, no report in the repository or the production-reports folder covers 22-27 Sep. I could not verify the claim; please send the source. | database, repository |
| 4 | Does Go contain the 22-27 Sep logic? | **Yes, all of it, bit for bit.** The five rules are in the Go engine; the technique family was already proven against the frozen Python; the one place Go deliberately kept the older structure, Key Level (`detector_contract: profit_week`), now also has its `current` contract proven against the 22-27 Sep Python: 8 captures, 7,395 decisions, 0 mismatches. | `TestKeyLevelCurrentContractMatchesThePythonOf22To27Sep` |
| 5 | Is anything in Go technically worse than September? | **No confirmed regression.** The one place Go departs from the 22-27 Sep rules with a consequence, the Supply/Demand touch cut (zones touched more than twice), is supported by matched-random evidence. | section 4 |
| 6 | Should Key Level switch to the 22-27 Sep structure? | **Not on this evidence.** The two contracts agree on 87% of decisions; the 13% that differ are not distinguishable from random in either direction. Left as is. | section 3 |
| 7 | What changed in Go? | Nothing in the engine. Added: the proof for the `current` contract, the 22-27 Sep Python golden, `tools/q4_contract_ab.py`, tests. | |

## 1. The logic of 22-27 September

Analysis commits between `a1c77584` (19 Sep) and the 28 Sep cut-over, all of 21 Sep:

| Rule (owner-reported 2026-09-21) | Commit | Technical content |
|---|---|---|
| Reversal order blocks | `54bbcdc6` | an order block's qualifying break may be a CHoCH as well as a BOS (every reversal OB had been discarded); dead zones beyond 6 ATR leave the watchlist |
| Wick touches count | `15b0c29d` | a key level's touches become `max(fractal-swing touches, wick-touch episodes)`; an episode is a run of bars whose range overlaps the level band without opening on one side and closing beyond the other |
| Zones stay tradeable while they hold | `6fab6b02` | a close beyond the far edge breaks a zone only by more than 0.5 ATR; a close-through is forgiven as a sweep when price returns within 6 bars; more than 2 break episodes is noise; up to 30 retests |
| Unmerged technique zones with their own origin | `04db63bc` | supply/demand/OB/FVG instances are judged by their own origin, touches and break, not by the earliest merged member |
| With-bias entries skip the premium/discount gate | `c65bafdf` | continuation entries are exempt from the half-range bands |

Everything else is plumbing: `S13D` removed dead definitions, the cards and notifications changed, and from 23 Sep the Go engine was built (S4-S9 Kafka publication 23 Sep, `go_shadow` 26 Sep). The config differs from the profitable week only by instrument-pack names and feed timeframes. Execution rules (`execution_policy`, ladders, envelopes) were those of the profitable week: XAU cap 60, 80/20 market/scale entry.

Missing evidence, stated so it is not mistaken for a result: no M5 market data for 22-27 Sep, no trades, plans, rejections or logs of those days (the Algo Bot keeps 14 days of file logs), no account P&L.

## 2. The Go equivalent of each rule

| Rule | Go | Proof | Status |
|---|---|---|---|
| Reversal order blocks | `Zones` / `OrderBlocks` of the legacy frame (CHoCH and BOS); Key Level's `ContractZones` keep BOS-only under `profit_week` | `test/techniqueparity` against the 2 Oct frozen oracle (same analysis) | present |
| Wick-touch episodes | `Levels` of the frame (all non-Key-Level strategies); Key Level reads `SwingLevels` under `profit_week`, `Levels` under `current` | `current` contract proven vs the 22-27 Sep Python in this change | present, Key Level opt-in |
| Zones hold while they hold (sweep-aware validity) | technique publishers, same tolerances | techniqueparity | present; **Supply/Demand then cut at 2 touches (14 Oct)** |
| Own-origin technique zones | `TechniqueZones` | techniqueparity | present |
| With-bias PD exemption | none needed: Go has no premium/discount gate on structural strategies (only Fade Scalp's own structural-direction rule) | `docs/strategies/fade_scalp.md` | n/a |

Deliberate departures since 22-27 Sep, each documented elsewhere: formation-bar reactions removed (a structure cannot confirm itself; Q2, 828 removed opportunities, -0.021 R against random, a validity correction), session context measured instead of constant (Q2/Q3), Supply/Demand touch cut, Fade v3, and the purged strategies.

## 3. Key Level: the same candles, two structure contracts

Production runs `profit_week` (levels from fractal swings only, BOS-only order blocks). The Python of 22-27 Sep read the `current` structure. Replaying the Go engine under each (frozen session factor, so only the structure differs), 5,809 opportunity bars:

| Class | Count | Reading |
|---|---:|---|
| identical under both contracts | 5,053 | 87% |
| only under `current` (wick-touch levels admit more) | 496 | |
| only under `profit_week` | 143 | |
| same bar and direction, another level wins | 117 | |

Hypothetical outcomes against matched random entries (40 draws), September = development, October = out of sample, symbol-day cluster intervals:

| Class | Window | n | Mean R minus random [95%] |
|---|---|---:|---|
| only `current`, XAU | Sep | 20 | -0.262 [-0.704, +0.168] |
| only `current`, XAU | Oct | 81 | +0.074 [-0.103, +0.220] |
| only `current`, FX | Oct | 299 | +0.043 [-0.127, +0.196] |
| only `profit_week`, XAU | Sep / Oct | 6 / 15 | +0.969 [+0.131, +1.982] / +0.016 [-0.641, +0.998] |
| only `profit_week`, FX | Oct | 95 | +0.068 [-0.128, +0.285] |

Neither side is distinguishable from random or from the other, and the two XAU September cells have opposite signs on 6 and 20 observations. The wick-touch rule is a defensible definition of level significance (a level that price rejected twice has been tested whether or not a fractal formed) but nothing in the data shows it selects better entries, and nothing shows it selects worse. No change.

Decision-level comparison of the 22-27 Sep Python with the Go engine as it runs now (`tools/q3b_key_level_ledger.py`, 3,502 Go opportunities, 10,023 evaluated bars): detection 0, confirmation 0, geometry 0 divergences (the reference Go equals the Python); quality 1,179 (stars, the measured session factor), acceptance 1,028 (the confluence floor), 6 of 5,186 Python decision bars with no Go counterpart (a standing-thesis matching limit, not a missed setup), 66 current opportunities with no reference counterpart (sibling selection under measured stars).

| Window, group | Python-equivalent filled | Mean R minus random [95%] | Go-published minus all Python |
|---|---:|---|---|
| Sep 14-21, XAU | 323 | +0.027 [-0.079, +0.141] | -0.001 [-0.130, +0.086] |
| Sep 28 - Oct 14, XAU | 1,014 | -0.015 [-0.109, +0.081] | -0.023 [-0.069, +0.016] |
| Sep 28 - Oct 14, FX | 1,664 | +0.028 [-0.047, +0.112] | -0.025 [-0.098, +0.054] |

The floor again costs nothing measurable. Valid setups missed: none attributable to the engine; incorrect setups accepted: not separable without ground truth for individual setups (Q1/Q2 ground-truth work still applies).

## 4. Zone freshness: Supply and Demand touches

The 21 Sep rule let a touched zone trade up to 30 retests. The Go engine published exactly that until 14 Oct, then cut Supply and Demand at two touches because zones touched three or more times were the worst slice (raw R). With the matched random control and the cut switched off (`maximum_zone_touches: 0`), same candles:

| Set | n | Mean R minus random [95%] |
|---|---:|---|
| zones touched 3+ times (published only without the cut) | 812 | **-0.178 [-0.291, -0.050]** |
| zones touched at most twice | 2,778 | -0.016 [-0.075, +0.043] |
| difference | | **-0.161 [-0.304, -0.005]** |
| by cell: Supply FX / XAU Oct / XAU Sep | 419 / 42 / 7 | -0.133 / -0.293 [-0.681, -0.027] / -0.381 |
| by cell: Demand FX / XAU Oct / XAU Sep | 316 / 22 / 6 | -0.205 [-0.403, +0.009] / -0.209 / -0.675 |

All six cells are negative; September alone has 13 observations. The cut is technically supported: a zone that has been retested repeatedly is being consumed, and the matched control shows the entries into it are worse than random while fresher ones are not. It is specific to Supply and Demand: order blocks (touched 3+ minus at most 2: +0.097, n=117), FVG (+0.038, n=37) and Confluence Zone do not show it, so it is **not** generalised. No change.

## 5. Behaviour of Key Level decisions (hypotheses, none shipped)

Outcome by what confirmed the reaction, XAU, difference to random of the group against the rest, chronological split at 7 Oct:

| Confirmation | Development | Out of sample | Verdict by the pre-registered rule |
|---|---|---|---|
| strong reclaim (n=521) | +0.228 [+0.078, +0.370] | +0.043 [-0.075, +0.124] | not supported (shrinks out of sample) |
| sweep and reclaim (n=385) | -0.196 [-0.326, -0.039] | -0.084 [-0.386, +0.144] | not supported |
| engulfing (n=149) | -0.250 [-0.475, +0.061] | -0.194 [-0.417, -0.033] | not supported |
| wick rejection (n=171) | +0.083 | +0.111 | not supported |
| round-number level (n=674) | -0.079 | -0.138 [-0.240, -0.031] | not supported |

Reading: a liquidity sweep with a reclaim, the confirmation most associated with a "genuine liquidity grab", does not outperform a plain strong reclaim on XAU; if anything the opposite, in both windows, but no split passes the Bonferroni rule, the strong-reclaim advantage decays out of sample, and the same fields on FX are flat (differences between -0.10 and +0.05). They are leads for a larger sample, not rules. Per the brief, no scoring weight or generic bonus was added and no validity condition was changed on this evidence.

## 6. Confirmed regressions and corrections

None. Q4 changes no Go behaviour: the evidence supports neither switching the Key Level contract nor removing the Supply/Demand cut, and the remaining differences from September (formation-bar confirmation, measured session factor, strategy set) are validity corrections already measured as neutral. Additions: `TestKeyLevelCurrentContractMatchesThePythonOf22To27Sep` and its golden `testdata/key-level-oracle-sep22-27.json` (generator `CAPTURE_SET=sep22_27`), `tools/q4_contract_ab.py` with tests, and a ledger fix (a standing thesis may have been first published under another capture; unexplained Python decisions fell from 411 to 6).

## 7. Limitations and what would settle them

* No 22-27 Sep market data at M5: the only evidence for those days is by construction the code, not a replay of them. The September candles used are 14-21 Sep (XAU).
* The reported performance is unverified. If you can share the trade list (or a table export) for 22-27 Sep, the setups can be matched to what the Python scanner would have published on the 14-21 Sep neighbours only; for the days themselves no M5 data exists.
* Hypothetical outcomes only, single-leg fills, constant spreads, no sizing or position limits; the multi-leg XAU entry band and the admission rate downstream of the detector are not replayed (Q3b section 4).
* Sample sizes for the Key Level behaviour splits (hundreds of setups, one regime change between windows) cannot resolve differences below about 0.2 R.
* **Recommendation:** store the closed M1/M5 candles of every production day (a nightly dump of Redis `bars:*`). The next baseline question will otherwise end where this one did.

## 8. Reproduce

```
# Python of 22-27 Sep (read-only worktree of a75b6559; any venv with pandas)
git worktree add --detach /root/oracle-sep27 a75b6559
ORACLE_ROOT=/root/oracle-sep27 ENGINE_DIR=$PWD/analysis-engine APEXVOID_CONFIG_FILE=/root/oracle-sep27/config/trading-bot.yml \
  CAPTURE_SET=sep22_27 WORKERS=8 python analysis-engine/test/keylevelparity/generate_oracle_golden.py
cd analysis-engine && go test ./test/keylevelparity -run Of22To27Sep
# structure-contract and touch-cut A/B on identical candles
AEQ_PARAMS='{"key_level":{"detector_contract":"current","frozen_session_context":true},"supply":{"maximum_zone_touches":0},"demand":{"maximum_zone_touches":0}}' \
  AEQ_DUMP=/tmp/nocut go test ./test/aequality -run TestReplayAllStrategies     # and TestMatchedRandomBaseline with AEQ_RANDOM_OUT
python algo-bot/tools/q4_contract_ab.py --strategy supply --a /tmp/cut --b /tmp/nocut --out ab.json
```
