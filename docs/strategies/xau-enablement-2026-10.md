# XAU enablement review: `ifvg` (v3) and `session_level` (v4)

**Status: enabled on 13 Oct 2026 at the owner's explicit instruction, against the recommendation to keep `session_level` contained. The evidence below is unchanged.**
`config/instruments.yml` is **unchanged** by this document's pull request: the edit that
would take `ifvg` and `session_level` off XAU's `observe_only_strategies` list is
prepared as an applyable patch ([`xau-enablement-2026-10.patch`](xau-enablement-2026-10.patch),
verified with `git apply --check`) and is the one step that needs the owner. (While
preparing this branch the harness refused a direct edit of the live containment file as
a production change; the patch is the deliberate, reviewable alternative.)

## Decision requested

| | Technical certification | XAU hypothetical evidence | Recommendation |
|---|---|---|---|
| `ifvg` (v3, #767) | **passes** | about zero and wide: 24 filled setups over three windows, mean -0.04 R; no interval excludes zero | the owner's risk-appetite call; if enabled, treat as unproven and review after the first trades |
| `session_level` (v4, #768) | **passes** | leans **negative**: 14 filled setups over the three windows (5 + 2 + 7), mean -0.64 R; the out-of-sample window -0.61 R [-1.00, 0.17] (n=7) | **keep contained** on current evidence |

The two names can be removed independently: the patch deletes one line each. A removal
changes nothing but those two names; sizing, risk percent, exposure limits and group
risk are not touched by it.

Rollback is the reverse: put the names back. Containment is read at match creation and
again at plan admission, so it stops **new** plans only; it never cancels, modifies or
closes a plan or position that already exists
(`test_rollback_recontains_new_matches_without_touching_a_published_plan`).

## The change (not applied)

```diff
--- a/config/instruments.yml          # instruments.XAU.overrides.execution.go_opportunity
         go_opportunity:
           observe_only_strategies:
-            - ifvg
             - liquidity_sweep
-            - session_level
             - impulse_pullback
           observe_only_structure_timeframes:
             - M15                      # unchanged
```

`liquidity_sweep`, `impulse_pullback` and the M15 structure containment are preserved.
Apply with `git apply docs/strategies/xau-enablement-2026-10.patch` (it also replaces the
two stale evidence comments with a pointer to this page and the rollback note).

## Certification checklist

| Requirement | Evidence |
|---|---|
| Configuration loads; both strategies enabled in Go (`analysis.strategies.<id>.enabled`) and in the Python catalog | `test/engine` `TestStrategyConfigsFromConfigEnablesTheCompleteCatalogForShadow` (ifvg v3, session_level v4, all enabled); `TestAll21StrategiesHaveConcreteFactoriesAndValidProductionConfig`; `REVIEWED_SCOPES` carries both; the Python catalog switches `auto_algo.strategies.technique.ifvg.enabled` and `auto_algo.strategies.reaction.session_level.enabled` are `true` in `config/auto-algo.yml` |
| BUY and SELL positive fixtures | `internal/strategy/ifvg`, `internal/strategy/sessionlevel` (bearish twins are exact mirrors), and through the real engine (`test/ifvgreplay`, `test/slreplay` synthetic tests) |
| False positives, causal ordering, stable identities, prefix invariance, data gaps | negative scenarios in both packages (27 for iFVG), randomized prefix invariance over 8,661 and 6,000+ evaluations, no future dependence bar by bar on 6 and 7 real captures, feed holes ([ifvg](ifvg.md), [session_level](session_level.md)) |
| Real XAU and FX replay | [ifvg](ifvg.md#replay-evidence), [session_level](session_level.md#replay-evidence), plus the out-of-sample window below |
| Stop and target correctness, never tightened, envelope refusal | invariant tests on every setup real data produced; `risk_exceeds_execution_envelope` |
| Frozen Python oracle goldens immutable | untouched; `session_level_reaction` is *measured only* in `test/legacyparity` |
| Adapter eligibility, TradePlan admission, cross-service | `algo-bot/tests/test_xau_enablement_chain.py` (11 tests) and the existing `test_go_full_chain.py`, on real PostgreSQL and Redis (below) |
| Confirmed opportunities reach the Algo Bot | with the proposed list simulated by a runtime override, both strategies reach a valid TradePlan V8 once, repeated cycles do not duplicate it |
| Resting / incomplete reactions cannot execute | a candidate without a confirmed reaction, or without the strategy's evidence prefix, produces no match and no plan even with containment lifted |
| Arbitration and exposure intact | no code in this change; the unchanged suites pass (below) |
| `XAU` / `XAUUSD` alias | `containment_reason("XAUUSD", ...)` equals `"XAU"` under the proposed list |
| FX unchanged | every FX list stays `{impulse_pullback}`, M15 structure containment unchanged |
| Other 19 strategies unaffected | the strategy-config test and the full Go/Python suites |

## Old versus new on XAU (hypothetical, causal fills; never realised performance)

Windows. **Development**: 14-21 Sep (where the thresholds' magnitudes were checked).
**Incident**: 28 Sep-6 Oct, the losing trades, so *not* independent evidence.
**Out of sample**: 6 Oct 06:00 to 12 Oct 07:30, read from production after the designs were
fixed (`replay-xau-production-capture-20261012.json`, M5 2000 bars from 1 Oct 01:55).
v2 / v3 are the previous contracts (frozen in the replay test packages).

### `session_level`

| Window | previous (v3): setups, filled, mean R | new (v4): setups, filled, mean R | v4 positive / stopped |
|---|---|---|---|
| Development 14-21 Sep | 48, 48, +0.14 | 6, 5, -0.53 | 1 / 4 |
| Incident 1-6 Oct (the 1-12 Oct capture; the 28 Sep-6 Oct capture gives 38, 38, +0.21 and 3, 1, -1.00) | 20, 20, +0.01 | 4, 2, -1.00 | 0 / 2 |
| Out of sample 6-12 Oct | 27, 26, -0.18 [-0.62, 0.30] | 7, 7, -0.61 [-1.00, 0.17] | 1 / 6 |

Median stop distance: v3 64-73 pips, v4 49-56 pips; median target room 2.0-2.7 ATR for v3, 1.6-2.0
ATR for v4; MFE / MAE (R): v3 1.2-1.6 / 0.9-1.0, v4 0.9-1.0 / 1.0-1.2. Fill rate 100 % for v3, 5 of 6, 1 of 3 and 9 of 11 for v4.
v4 publishes at the confirming candle's close and never later; a confirmation is bound
to a touch or sweep at most 3 or 6 candles before it. The independent audit finds a defect (a level not current, an incomplete period, older than
a day) in 25 of 48, 14 of 38 and 26 of 47 of the v3 setups on the three captures. Details and the five losing live trades: [session_level](session_level.md).

### `ifvg`

| Window | previous (v2) setups, filled, mean R | new (v3) setups, filled, mean R | v3 positive / stopped |
|---|---|---|---|
| Development 14-21 Sep | 110, 110, -0.15 | 7, 6, -0.52 | 1 / 5 |
| Incident 1-6 Oct (the 1-12 Oct capture) | 75, 75, -0.39 [-0.68, -0.06] | 11, 10, +0.39 [-0.69, 1.59] | 4 / 6 |
| Out of sample 6-12 Oct | 88, 87, +0.24 [-0.17, 0.72] | 8, 8, -0.23 [-1.00, 0.91] | 2 / 6 |

(Reproduce: `IFVG_REPORT_DIR=/tmp/ifvg go test ./test/ifvgreplay -run OutOfSample -v`.) Median
stop distance v2 33-41 pips, v3 44-50; target room 2.4 ATR (v2) and 2.5-2.7 ATR (v3).

### What these numbers do and do not say

- **Proven**: both contracts are causal, deterministic, independent of loaded history, honest
  about the stop (beyond the structure, never tightened, refused over the instrument cap) and
  publish only a confirmed reaction; each setup they published on real data satisfies its own
  contract under an independent audit.
- **Not proven, in either direction**: profitability. Every interval includes zero; the v3/v4
  samples are 1 to 10 filled setups per window. The simulator is not the executor: the live
  XAU session-level trades lost 0 of 5 while the simulation of the old contract on the same
  window averages +0.21 R, and the old iFVG lost live in both windows reviewed while the
  simulation shows -0.39 R and +0.24 R. Simulated R must not be read as live expectancy.
- `session_level` v4's XAU simulation is negative in all three windows (14 fills). With this
  few setups a poor expectancy cannot be told from bad luck, which is why the recommendation
  is to keep it contained until more data exists, not to tune it on seven setups.
- `ifvg` v3's XAU result is indistinguishable from zero. Enabling it is a decision to run an
  unproven strategy under the existing risk controls, which this change does not alter.

## Safety and rollback verification

- Containment is one predicate (`go_containment.containment_reason`) run at match creation
  (`go_opportunity_policy`) and again at plan admission (`worker._publish_trade_plan_v8`).
  Removing a name makes new confirmed matches eligible; restoring it makes new matches (and
  matches stored earlier) ineligible. It never calls a cancel path.
- Rollback **does not**: stop or restart the Go engine (Go keeps producing, storing and
  arbitrating the opportunities either way), disable any other strategy, cancel a published or
  submitted plan, close a position, change a TradePlan already in the stream, or touch Kafka,
  Redis keys other than the match and route-outcome records of the new opportunities, or the
  position-management rules. `test_rollback_recontains_new_matches_without_touching_a_published_plan`
  asserts the stream, the plan state and the absence of a cancel intent before and after.
- No change to position size, risk percent, exposure limits or group risk is in this change; the
  XAU opposite-exposure policy and the arbitration are untouched code paths.
- Nothing is deployed or restarted by this change. Enabling requires merging the patch and the
  normal deployment, both the owner's call.

## Test results

Run on this branch while it was stacked on #767 and #768 (both merged to `master` unchanged
afterwards), 12 Oct 2026:

- **Go** (`analysis-engine`): `go vet ./...` clean; `go test -count=1 ./...`: **63 packages ok, 0
  failures** (includes `test/replaycapture`, `test/legacyparity`, `test/ifvgreplay`,
  `test/slreplay`).
- **Python** (`algo-bot`, real PostgreSQL 16 and Redis): `pytest tests`: **1,795 passed, 1 skipped,
  0 failed** (1,784 before this change plus the 11 new enablement tests).
- **.NET** (`ctrader-engine`): `dotnet test CTraderFeed.Tests.csproj`: **423 passed, 0 failed**.
- Not run: anything against the live broker or the production services (nothing was deployed,
  restarted or traded; production was read only to take the 12 Oct capture).

## Final certification verdict

- **Technical certification of the two contracts: PASS** (iFVG v3: #767, Session Level v4: #768,
  both merged to `master`; the evidence is in their pages).
- **Safety of the enablement mechanism and of its rollback: PASS** (tests above).
- **Evidence that enabling XAU execution improves outcomes: NONE.** For `session_level` the
  hypothetical evidence leans negative; for `ifvg` it is neutral and wide.
- **Action**: not applied. The owner decides, per strategy, whether to apply the patch.
