# AMD context (Accumulation / Manipulation / Distribution)

AMD is **supporting technical evidence**, produced by the Go Analysis Engine and stamped on an opportunity when it is first observed. It is not a
strategy, not a gate and not a score: no strategy reads it, the selected (V1) confluence stars never include it, `Quality.Overall` is still the mean
of the strategy's own `score_*` dimensions, `quality_ranking` stays `false`, and Algo Bot only records it. A setup with no AMD context, or with a
conflicting one, trades exactly as before.

## Where it is computed

`closed bars -> context.Build -> mad.Classify (per timeframe) -> Snapshot -> worker.technicalContext -> opportunity.MADContext -> Kafka -> Algo Bot match/plan (recorded)`

* `internal/mad` owns the model (`mad.go` types/affinity, `sequence.go` range and event scan, `relation.go` per-setup relation).
* The range is the **Asia session of the session calendar** (`session.Config.Window`), so it follows daylight saving like the session domain. (The fixed
  UTC 22:00-07:00 box used before ended an hour early in winter and, because "sealed" came from the session *label*, left the box unsealed for that hour.)
* The context an opportunity carries is the snapshot of the timeframe that **observed** it (an M1 scalp, an M15 trendline, an H1 key level each read
  their own), not the primary timeframe's.
* An event is read only from bars that have closed; times are bar open times; the evidence closes no later than the observation bar.

## What is measured

| Stage | Event | Evidence (ATR-normalised, so XAU and FX share it) |
|---|---|---|
| Range | the Asia window's high/low; **sealed** by the first bar at or after the window close | bars present vs expected, `range_quality_atr` |
| Sweep | a closed bar trades beyond a boundary by more than `max(pip, 0.05 ATR)` | penetration (ATR), swept level, extreme |
| Reclaim | a later (or the same) bar closes back inside before `accept_closes` closes beyond | reclaim depth (ATR), close location |
| Expansion | displacement from the reclaim close >= `expand.displacement_atr`, or accepted closes beyond the far edge >= `expand.break_atr` | displacement, break distance, accepted closes, strength |

Order is enforced and tested: range sealed <= sweep <= reclaim <= expansion start <= expansion confirmation <= observation. A phase is never
manufactured: a lone sweep or a lone expansion is a **partial** sequence (`stages` lists exactly what happened), an expansion with no sweep is
"expansion without manipulation", an accepted break is a breakout and not a sweep (`note: sweep_accepted_beyond_level`).

Invalidation (kept on the sequence for audit, never reported as current): `two_sided_sweep`, `conflicting_sweep`, `sweep_extreme_lost`,
`expansion_failed_back_inside`. After `analysis.mad.sequence.validity_hours` (8) without a new event the sequence is `stale`; the next Asia session
starts a new range and the old sequence is no longer reported. No range is read when the history does not reach the window start, when under half
of the expected bars are present, or on a timeframe coarser than H1.

`mad.sequence.id` is `asia:<window open>:<side>:<sweep time>` (or `...:expansion:<dir>:<start>`): it keeps its identity as later events are added.
A context refresh never republishes an opportunity: the context is attached at the observation boundary and an already-published opportunity is not
mutated.

## Relation to a setup

`aligned` / `conflicting` / `partial` / `irrelevant` / `unavailable`, with reason codes and evidence IDs (`mad.relation`, `relation_reasons`, `evidence_ids`).

| Strategy | Reads AMD | aligned when |
|---|---|---|
| `crt`, `key_level`, `range_scalp` (fade) | yes | a reclaimed sweep of at least the configured minimum measures supports the setup direction (sweep low -> BUY) |
| `ifvg` | yes | as above **and** the reclaim precedes the setup's own formation (a sweep that came after it is not the one it was built on) |
| `trendline` | yes | an expansion in its direction (continuation) or a reclaimed failed break in its direction; the opposite is `conflicting` |
| `impulse_pullback`, `impulse_retrace` | no | `irrelevant: continuation_only_strategy` |
| `asia_range` | no | `irrelevant: strategy_owns_asia_range` (it is built on the same range; reading it again would double count) |
| zone strategies, flip, round number, break retest, shock retest, confluence zone | no | `irrelevant: strategy_not_amd_relevant` (a later change may add one with its own evidence) |

`unavailable` means no current evidence (no range, forming range, stale, incomplete history). An invalidated sequence is `conflicting` for a setup in
its direction and for a two-sided sweep, and `partial` (`opposing_sequence_invalidated`) for the other direction.

## Affinity

`mad.affinity` / `confluence.mad_bonus` feed only the unselected V2 confluence telemetry. Accumulation is **neutral by definition** (`Neutral`): a box
that has not been left says nothing about direction, so it carries no direction or family evidence and the affinity is 0 *by design*. (Before, the 0
was an accident: `DirectionScore` was 1 for any direction but `StrategyScore` was never assigned in that phase. No bonus is given: it would be
arbitrary.) Inactive (stale/invalidated/unavailable) context scores 0.

## Contract

`technical_context.mad` (opportunity-v1) gains optional `timeframe`, `active`, `observed_at`, `range{id,kind,timeframe,high,low,formed_from,sealed_at,bar_count,expected_bars}`,
`sequence{id,status,stages,direction,sweep_*,reclaim_*,expansion_*,invalidation_reason,note}`, `relation`, `relation_reasons`, `evidence_ids`. All are optional; `version`
is 3. Algo Bot copies `timeframe, active, relation, relation_reasons, range.id, sequence.id, sequence.status` onto the match and the plan's `analysis`
block (`mad_*`), which is stored in the immutable plan snapshot. Nothing in `execution_policy` or `execution_route` reads a `mad_*` field (a test fails if one does).

## What is not proven

AMD is **not** shown to improve outcomes. Evidence from 11 days of production bars (XAU, EURUSD, GBPUSD, USDJPY, GBPJPY: 4528 replayed
opportunities) and the 91 executed `algo_auto` trades that match a replayed opportunity: only 30 of those trades belong to an AMD-relevant
strategy (Key Level 23, iFVG 3, Range Scalp 4; no Trendline or CRT), and 21 of those 30 had no AMD evidence (`unavailable`). That is far too little for
mean R, MAE or a matched comparison to say anything; none is claimed. Ranking stays off until a matched-outcome study with outcome labels for
unexecuted opportunities (the research labeller of docs/research-data.md) shows incremental value beyond the liquidity and structure features the
strategies already score.
