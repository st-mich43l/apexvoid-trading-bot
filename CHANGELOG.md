<!-- Every PR with behavior, config, deployment, or operator-facing changes
must add a concise entry under Unreleased. -->

# Changelog

All notable changes to ApexVoid Trading Bot are documented in this file.

The project deploys from `master` without tagged releases. Add new entries to
`Unreleased` in the same pull request as the code change, then move them into a
dated section after deployment.

## Unreleased

### Fixed
- Scanner observations without a canonical executable match no longer reserve
  the four-hour Telegram band dedup and suppress the forming card when that
  same structure later becomes executable.
- `/trade_stats` now ingests autonomous and manual Algo results through an
  independent durable cursor and backfills retained executor events at
  startup, instead of depending on the owner Telegram delivery cursor.
- Broker-confirmed `/trade_cancel` results now reply to every persisted VIP
  and public signal post; manual Algo request/override acknowledgements no
  longer show redundant waiting/awaiting text.
- TradePlan V7 TP and close events now carry and display the achieved pip count,
  and stats preserve the highest archived TP rather than understating a trade
  after its residual exits at BE or SL.
- Serialized cTrader access-token reads with account-list/account-auth requests,
  preventing startup health checks from queuing a stale token while proactive
  refresh rotates it and then failing auto-trade with
  `CH_ACCESS_TOKEN_INVALID`; transient auto-trade session faults now retry
  inside the still-healthy feed session instead of leaving execution stopped.

### Added
- Authority-neutral grouped `runtime_config` reads for all 140 eligible
  analysis, strategy, actionability, and lifecycle production call-sites.
  Generated Phase 2F inventory and AST guards enforce zero remaining or
  dynamic legacy reads, while process-isolated parity covers exact values,
  types, truthiness, and representative decision snapshots under both legacy
  and canonical authority. Trading behavior, ENV/Compose, execution, risk,
  contract, C#, and the default `legacy` authority remain unchanged.
- Authority-neutral grouped `runtime_config` reads for bootstrap, logging,
  Telegram/delivery, calendar, weekly reports, watcher, and OHLC consumers.
  Legacy authority now exposes an immutable legacy-backed canonical view while
  canonical authority exposes the validated Python model; generated AST and
  reverse-map guards prove all 127 eligible reads migrated with value/type
  parity, while the default remains `legacy` and rollback remains independent
  of canonical loading.
- Selectable canonical Python configuration authority behind the restart-only
  `APEXVOID_CONFIG_AUTHORITY` loader control. The default remains `legacy`;
  explicit `canonical` validates a frozen 387-leaf Python/shared projection,
  exposes the existing 316-field/four-property facade, excludes 50
  cTrader-only fields, fails closed, emits secret-safe startup diagnostics,
  and supports deterministic restart rollback.
- Non-authoritative Phase 2D1 configuration activation rehearsal: an
  AST-backed legacy `Settings` usage inventory, generated immutable Python
  access map, read-only 316-field/four-property canonical facade, local-only
  authority and rollback models, explicit readiness blockers, secret-safe
  diagnostics, and reproducible Python/.NET verification commands. Legacy
  `Settings` remains authoritative and no production consumer is migrated.
- Non-authoritative Phase 2C configuration shadow loading: immutable
  `conservative`/`demo_eval` profiles, deterministic source precedence,
  per-layer alias conflict detection, field provenance, legacy compatibility
  rules, four-fixture 316/316 parity checks, redacted diagnostics, and an
  offline shadow CLI. Legacy `Settings` remains the only active runtime loader.
- Source-generated System.Text.Json metadata and a production-path startup
  contract self-test for TradePlan V7, plus durable C# executor
  acknowledgements and per-stream rejection records.
- Durable Scanner -> Worker handoff on Redis Stream
  `auto_trade:strategy_match_ready` (schema v1, `algo-worker` consumer group)
  with duplicate suppression, pending-entry recovery, canonical
  `StrategyMatch` reload, startup reconciliation, and no-op acknowledgement
  for terminal or already-published setups.
- Typed scanner `ExecutionEligibility` contract. Invalid geometry, opposing
  target room, ambiguous cross-side structure, key-role mismatch, broken
  support/resistance routing, disabled counter-bias, tier C, and provisional
  R/R failures remain auditable analysis-only observations but cannot create
  an executable match, setup lifecycle, or queued card.
- Truthful one-card lifecycle snapshots: `QUEUED`, `PREFLIGHT`,
  `WAITING RETEST`, and `PLAN PUBLISHED`, including ready-event, scanner, and
  worker timestamps plus quote/zone evidence in route telemetry.
- Restart-safe reaction execution state at
  `auto_trade:execution_confirmation:{setup_id}`, including deterministic
  retest episode ids, side-aware zone evidence, consumed M1 timestamps and a
  bounded two-bar retest-trigger validity window.
- TradePlan V7 contract (`contracts/autotrade/trade-plan-v7.json`, Python
  `app/autotrade/trade_plan.py`, C# `TradePlanV7.cs`): a single absolute stop
  and a concrete entry instruction (market_watch/single_limit/limit_ladder)
  declared entirely by Python, replacing the V6 `Planned*`/`stop_adjustment*`
  field family that let both services compute a stop and compare. See
  `docs/adr-trade-plan-v7-boundary.md`.
- `AUTO_TRADE_CONTRACT_MODE` (`legacy_v6` default / `shadow_v7` /
  `v7_primary` / `v7_only`) as a new fatal field in the existing Python<->C#
  config-health handshake, alongside `trade_plan_version` and
  `trade_plan_stream`.
- `execution:trade_plans` Redis stream and `execution:plan:{plan_id}` /
  `execution:plan_state:{plan_id}` keys, isolated from `auto_trade:*`.
- Python setup lifecycle state machine (`app/autotrade/setup_lifecycle.py`,
  `analysis:setup:{setup_id}`) and a `TradePlan` builder from an
  already-confirmed `StrategyMatch` (`app/autotrade/trade_plan_builder.py`).
- C# `TradePlanExecutionEngine` (mechanical entry/volume/target/break-even
  decision logic only, not yet wired to broker order submission) plus a
  dependency-boundary test suite proving it never references
  `StructureStopPlanner`, `ResolveExecutionRoute`, or the other legacy
  dual-planning symbols.
- No behavior change from any of the above by default (`AUTO_TRADE_CONTRACT_MODE`
  defaults to `legacy_v6` everywhere); V7 is not yet published, consumed, or
  used to place any order.
- `AUTO_TRADE_POSITION_MISSING_CONFIRMATIONS` (default `2`) and
  `AUTO_TRADE_POSITION_MISSING_RECHECK_SECONDS` (default `3`): a tracked
  position missing from one broker reconcile snapshot is only "suspected"
  missing and stays fully tracked until independently confirmed absent
  across this many time-separated snapshots.
- Confluence-merge zone (`app/analysis/confluence_zone.py`): co-located/
  nearby key level, demand/supply, order block, FVG and breaker structures
  (overlapping, or gap `<= zone_merge_gap`, default `1.0`) collapse into one
  `ConfluenceZone` capped at `zone_merge_max_width` (default `6.0` price
  units) carrying the union of their tags and a stable id (bucketed
  mid/width + sorted tags, mirroring `structural_zone_id`'s scheme). Same
  directional role only - a demand and a supply at the same price never
  merge. `resolve_confluence_zone_id`/`claim_confluence_zone` key a new,
  independent SETNX claim on the merged zone + direction so a second
  strategy resolving onto an already-claimed zone is rejected
  (`zone_already_claimed`) rather than producing a second plan; wired into
  `worker.py::_publish_trade_plan_v7` alongside the existing thesis claim.
- `m1_trigger` (`app/analysis/m1_trigger.py`) supplies optional timing evidence
  for a setup already confirmed on M5: wick rejection, body close, strong
  close, pin bar, engulfing, or hammer/shooting star can re-anchor the stop to
  the trigger wick. Scanner-confirmed reactions do not require a qualifying
  M1 candle while their executable quote is inside the confirmed zone;
  non-reaction families retain their M1 timing policy. No candlestick logic
  crosses into `ctrader-engine`.

### Changed
- `/algo_status` adds compact profile, open group count, observed regime, and
  last route (strategy · status · reason) while staying well under Telegram's
  text limit (soft budget ~1200 chars; handler still hard-clips at 4000).
- V7 publication now retains a seven-day TTL-bound dedup tombstone and a full
  symbol/cycle owner record. The C# executor keeps its restart payload under a
  separate recovery key, preserving the Python plan payload's short TTL.
- Setup-card status is now a typed, atomic, monotonic Redis state. Delayed
  scanner/worker events cannot regress `PLAN PUBLISHED` to queued, preflight,
  or waiting-retest.
- Scanner-confirmed reactions publish in the durable worker attempt only when
  BUY ask or SELL bid is inside the raw entry zone plus contract tolerance.
  Nearby price outside the zone remains `WAITING_RETEST`; same-cycle zone
  re-entry does not require M1, while non-reaction strategies keep their
  family-specific M1 requirement. `AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS`
  remains only a mechanical executor anti-chase ceiling.
- TradePlan V7 persistence is atomic: deterministic plan key, published state,
  and stream event are committed by one Redis Lua operation, so concurrent
  M1/ready events and crash retries cannot publish two initial plans.
- Confluence merge width is now `6` price units. The #140 actionability,
  key-level ambiguity, and range-context disagreement policies observe by
  default behind explicit flags, so same-side merged structures continue to
  setup confirmation; genuinely overlapping BUY/SELL structures still gate.
- `map_strategy.py` selects mapped zones from `market_map.actionable_entries`
  (the uncapped structural pool) instead of `market_map.entries` (the
  Telegram-display-capped list), so the per-side display cap can no longer
  silently determine which structural zone is reachable for execution.
- Auto-trade Telegram cards no longer say "Algo bot READY" when Python
  merely publishes a V6 candidate; that headline is now "Algo bot PLAN
  PUBLISHED" - READY is reserved for the executor actually arming a plan.
- Scale-in adds (momentum and pullback) share the same volume-split cap:
  each tranche is limited to `AUTO_TRADE_ADD_SIZE_RATIO` (default `0.5`) of the
  initial tranche size, in addition to exposure/risk/add-cap ceilings. Python
  mirror: `app.autotrade.scale_in_sizing`.
- HTF analysis context moved from M30 to H1: the stack is now a single
  H1->M15->M5 source. `CTRADER_TIMEFRAMES` default is `M1,M5,M15,H1`
  (`ctrader-engine` subscribes and backfills H1 directly from cTrader,
  `TimeframeCodec` gained an `H1` mapping); `scanner_htf` default is
  `H1,M15`. M5 remains the setup formation/confirmation timeframe,
  unchanged. `detectors.build_context` now fails HTF bias closed to
  `"unknown"` (not `"up"`/`"down"`, and distinct from the legitimate
  `"range"` state) until H1 has at least 50 closed bars, instead of
  silently falling back to M15/M5-only bias while H1 warms up after a
  fresh subscribe or a redeploy gap.
- Setup delivery is now one forming card per setup (anchored to
  `setup_lifecycle.py`'s `setup_id`, stored at `auto_trade:forming_message:
  {setup_id}` alongside its chat id) with a reply-threaded lifecycle:
  `plan_armed`/`order_filled`/`take_profit`/`stop_moved`/`position_closed`
  reply directly to the card instead of scattering standalone messages
  (`delivery_thread_lifecycle`, default on). Re-detection of the same setup
  edits the existing card (`app/autotrade/setup_card.py`) rather than
  posting a new one. Rejected, invalidated and expired setups have their
  card deleted and produce no message at all - no more "EXECUTOR REJECTED"/
  "SETUP INVALIDATED" spam - and are never re-carded afterwards
  (`delivery_delete_on_terminal`, default on; falls back to editing the card
  to a neutral terminal state if Telegram's deletion window has passed).
  New `ARMED_WAITING_TRIGGER` setups expire via their own `expires_at` if
  price never enters the executable entry zone; a build rejection after
  zone/M1 confirmation and a scanner-detected invalidation for a
  still-waiting setup both route through the same delete-the-card path
  instead of a "rejected" card. A position with no `setup_lifecycle` record
  (eg. an older V6 position) is unaffected - lifecycle replies fall back to
  the pre-P4 position-id reply chain exactly as before.

### Removed
- Remove `AUTO_TRADE_EXECUTE_MAX_DISTANCE_PIPS` and `distance_proximity` as
  positive execution authorization. Distance remains telemetry/ranking only
  and cannot publish outside a strategy entry zone.
- The private M1 range gate (`app/autotrade/gate.py`) and the M1
  mapped-zone reaction (`map_strategy.py::_select_reaction_detailed`'s M1
  touch/rejection detector) no longer originate trade candidates - M1 is
  retained only as input for a future entry trigger, not as an autonomous
  setup source. The M1-reaction path's live-quote zone-widening
  (`entry_low = min(entry.lo - tolerance, price)`) is removed with it.
  Market Map's structural pool (`actionable_entries`) is unchanged and
  still reachable via `_select_reaction_detailed`, which now only ever
  reports the nearest tracked/executable zone (`waiting_for_touch`) rather
  than promoting one to a `StrategyMatch`. `evaluate_auto_scalp_gate` and
  `evaluate_range_box_eligibility` are still called for regime
  classification and status telemetry respectively, but their output can no
  longer construct an `ExecutionIntent`.

### Fixed
- cTrader now verifies the configured market-data account broker before
  symbol resolution, historical backfill or live subscription. A token that
  also grants an IC Markets account can no longer silently feed IC bars while
  the executor is configured for FP Markets; feed and execution both use the
  configured FP Markets account.
- Restore end-to-end V7 runtime consistency: Python no longer discards a
  successful `_publish_trade_plan_v7` result, published setups reconcile
  instead of re-entering preflight/invalidation, and terminal lifecycle
  failures map explicitly without permitting `PLAN_PUBLISHED -> INVALIDATED`.
- Scanner execution eligibility now honors the complete policy result,
  including `allowed`, exact reason, hard-block status, and measured evidence;
  denied setups remain analysis-only even when their provisional R/R passes.
- C# TradePlan consumption no longer depends on reflection-disabled JSON
  overloads. Malformed plans are durably rejected per message, transient
  failures retain the stream cursor for retry, and later valid plans continue
  through the same consumer session.
- Identical Telegram setup-card edits are local no-ops, and Telegram's
  `message is not modified` response is treated as success without traceback,
  fallback card creation, or retry storms.
- Manual `/algo` envelopes now carry an explicit
  `bypass_analysis_gates=true` contract and bypass scanner arbitration,
  confirmation, R/R, barrier, cooldown, and overlap analysis while retaining
  executor broker-mechanical safety.
- Scanner-confirmed structural reactions no longer lose valid entries to a
  forced arm-only worker cycle. In-zone M5 confirmations now run final V7
  preflight and publication in the same call; reactions outside the execution
  zone wait for a side-aware quote retest. Optional M1 evaluation scans every
  eligible unprocessed bar and applies one common zone-intersection gate to all
  six candle patterns, preventing stale triggers from anchoring a later entry.
- Separate scanner observations from actionable setups before lifecycle
  creation. Opposing cross-side reactions now resolve deterministically,
  ambiguous generic key levels remain analysis-only, and the nearest
  opposing `MarketMap.actionable_entries` barrier caps target room before
  reward/risk evaluation. Invalid or zero-room geometry blocks in observe,
  balanced, strict, conservative and demo-eval modes; the worker repeats the
  same pure geometry check before final TradePlan V7 publication. Raw
  observations and measured rejection reasons remain available in scanner
  status, detect logs and metrics without creating a forming card.
- Preserve detector quality when same-side structural detections merge:
  merged quality is now the strongest member's `confluence`, while
  `confluence_tags` remains the separate structural-diversity dimension.
- Keep preflight strategy-route diagnostics (`waiting`, `blocked`, and
  `executor_rejected`) in Redis/history/metrics but make them Telegram-silent,
  removing standalone `Algo bot BLOCKED` cards for invalid stop geometry,
  regime mismatch, entry drift/hard-cap, and equivalent execution vetoes.
  Analysis cards without an executable match now use a neutral
  `ANALYSIS ONLY` label instead of presenting another blocked alert.
- Suppress the scanner's remaining legacy standalone `SETUP INVALIDATED`
  Telegram fallback. Broken structures now only retire scanner watch state;
  lifecycle-backed setups still transition terminal and have their forming
  card deleted, while detections without a setup record remain log-only.
- Wire `merge_confluence_zones` into scanner detection and forming-card
  identity: co-located same-side key-level / demand / supply / OB / FVG /
  breaker detections now share the merged zone id, union tags, one setup,
  one forming card, and the exact same execution claim instead of producing
  duplicate detector cards before the existing one-order-per-zone guard.
- Move the shared execution-policy reward/risk check into setup eligibility:
  setups below their family's `min_reward_risk` are recorded as
  `rr_pre_gate` but never confirm or form a card. The plan-build R/R check
  remains authoritative after quote-in-zone eligibility and optional M1 timing;
  a late failure expires and silently deletes the forming card instead of
  posting BLOCKED spam.
- Broker SL/TP exits discovered by reconcile without a confirmed OrderType no
  longer show `reason unconfirmed`. Close reason is inferred when the exit
  sits on the protective stop — both a clean full SL before any TP and a BE
  stop-out after booked targets. After booked TPs, Total /
  `group_realized_pips` reports the highest target reached (e.g. TP2 = 60)
  instead of a volume-weighted blend diluted by the BE residual (~22.8).
- Manual / algo / algo_manual journal history and `/trade_stats` now record
  the highest TP/pips hit (`legs_achieved_pips`), not a volume-fraction or
  lot-weighted net that dilutes booked targets with a later BE residual.
- Break-even buffer direction: `StopTrailPlanner.ProtectedBreakevenStop` had
  BUY/SELL swapped (adverse-side cushion instead of profit-side protection),
  moving a SELL entry 4100.74 BE+6 stop to 4100.80 instead of the correct
  4100.68. Corrected to BUY entry+buffer / SELL entry-buffer; never-worsens-
  an-existing-stop behavior unchanged.
- Independent autonomous strategy groups on a non-hedged (netting) broker
  account now fail closed before broker submission
  (`independent_strategy_requires_hedged_account`) instead of silently
  collapsing into the existing net position and blending two strategies'
  SL/TP - previously only opposite-direction groups were rejected, so two
  same-direction independent strategies (the incident: Trendline Reaction
  and Key Level Reaction, both SELL) could both be admitted.
- `ReconcileAsync` no longer deletes a tracked position's state and
  publishes `position_closed` after a single broker snapshot omits it;
  absence must now be independently confirmed across
  `AUTO_TRADE_POSITION_MISSING_CONFIRMATIONS` time-separated snapshots
  first (`position_missing_snapshot_suspected` /
  `_confirmed` / `_recovered`).
- Added a fatal-event guard (`broker_position_identity_group_conflict`) if
  the broker ever returns an already-tracked PositionId for a distinct
  independent group, so the existing group's state cannot be silently
  overwritten and no further autonomous groups are admitted until resolved.
- Block opposite-direction autonomous initial groups before stop/route planning:
  worker preflight rejects with `opposite_initial_group_active` when a tracked
  initial position exists on the other side; the C# executor runs the same guard
  before route resolution so rejections no longer surface as misleading
  `final_protective_stop_contract_mismatch` when e.g. SELL is open and BUY is
  attempted.
- Suppress redundant `SETUP INVALIDATED` owner alerts after autonomous entry:
  only Telegram forming cards are tracked (not every claimed detection),
  overlapping setups share one level-band watch, open same-direction positions
  silence further invalidation pings, and `opened` clears the watch state.
- Decouple `SETUP FORMING` card volume from the execution digest: advisory
  cards are capped by `SCANNER_CARD_TOP_N` (default `2`), always suppress
  overlapping opposing directions, and deduplicate structural reactions by a
  stable level bucket instead of their jittering execution `structural_id`.
  Candidate tracking and publication remain unchanged.
- Align the C# scale-in eligibility gate with the adverse-side
  protected-breakeven stop produced after TP1. Momentum and pullback adds now
  accept the configured BUY `entry - buffer` / SELL `entry + buffer`
  threshold while still rejecting stops even one tick less protected.
- Make protective-stop contracts route-aware: market candidates validate the
  entry-independent raw/base stop, source, adjustment, clamp, and zone identity
  while trading the stop recomputed at the live entry; resting
  `single_limit`/`zone_split` contracts retain strict entry, distance, and pip
  equality. This prevents normal publish-to-fill drift from causing
  `final_protective_stop_contract_mismatch`.
- Make entry contracts route-aware: market candidates observe publish-to-fill
  drift without rejecting it while the existing 10-pip entry-zone cap remains
  the chase guard; resting entries remain strict. Correct the losing-side stop
  metric to `final_stop_not_on_losing_side`.
- Correct ApexVoid Algo break-even protection to **BE+6 broker ticks**
  (`AUTO_TRADE_BE_BUFFER_TICKS=6` × tick `0.01` = **0.06** price), not
  6 trading pips × `pip_size=0.1` (= 0.60). Buffer stays on the adverse side
  of fill: BUY `4087.66` → `4087.60`; SELL `4087.66` → `4087.72`. Config
  health compares `break_even_buffer_ticks` and `symbol_tick_size`; Telegram
  Risk Protected cards report `BE+6 ticks` and `Buffer: 0.06`. Deprecated
  `AUTO_TRADE_BE_BUFFER_PIPS` is read as a tick count only.
- Align autonomous publication with the executor entry-distance hard cap
  (`AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS`): adaptive strategy drift stays an
  observation tolerance, Algo bot READY / candidate XADD require the executor
  envelope, and outside-cap setups emit Algo bot WAIT while remaining retained.
- Rename Telegram route cards from AUTO READY/WAIT/BLOCKED/CHECKING to
  Algo bot READY/WAIT/BLOCKED/CHECKING.
- Rename owner Telegram menu commands from `/auto_*` to `/algo_*` (legacy
  `/auto_*` aliases remain), and make `/algo_status` report failures instead of
  failing silently when the status card errors or exceeds Telegram's length limit.
- Resolve concrete execution routes (`market` / `single_limit` / `zone_split`)
  before protective-stop planning for Key Level, Trendline, and other
  reaction families so strict stop contracts no longer publish unresolved
  `either` and mismatch at the executor.
- Suppress target evaluation on pre-fill quotes; enrich stop-moved Telegram
  cards with TP trigger context; deliver one canonical terminal Telegram
  result per trade group.
- Add a recovery-only `broker_reconciling` state: recovery claims never write
  normal `processing`, an expired recovery lease stays recovery-required, and
  a crashed recovery worker can never decay into a normal retry that places a
  duplicate order. `broker_outcome_unknown` releases to `retryable_error`
  only from the recovery-active state after the absence quorum.
- Heartbeat the recovery lease across the whole broker reconciliation
  (group-plan load, snapshots, delays, final fenced mutation); on ownership
  loss the stale recovery worker stops without counting confirmations,
  releasing the record or deleting the group plan.
- Persist broker-absence confirmation progress durably in Redis with a
  fenced, Redis-time-separated counter: an immediate process restart cannot
  accelerate the quorum, stale recovery owners cannot increment it, and
  progress survives recovery crashes. Validate quorum timing fatally at
  startup (confirmations ≥ 2, positive recheck interval, timeout covering the
  configured quorum).
- Validate structured candidate execution records identically in Python, C#
  and Redis Lua: explicit supported `version`, explicit known `state`, exact
  identity — no defaults, no fallback to `published`, no legacy downgrade for
  malformed JSON; publication reconciliation refuses restoration over them.
- Persist the actually resolved execution route and the exact submitted
  client order IDs in the group plan before the first broker side effect
  (including `either` routes resolving to market/limit/zone), expose the
  broker-echoed `ClientOrderId` on positions and pending orders, prioritise
  exact client-order identity in recovery matching, treat mismatched exact
  identities as conflicts, and adopt partial zone outcomes instead of
  resubmitting or confirming absence.
- Require consecutive broker absence confirmations (and deterministic client
  order identity) before releasing a recovery-required candidate for retry, so
  delayed broker visibility cannot create a duplicate order; expire
  `broker_submitting` into recovery rather than normal reclaim.
- Fail closed on unknown planned execution routes and incomplete planned entry
  / leg-entry contracts before any broker mutation.
- Require exact `candidate_id` and `stream_event_id` on structured execution
  records in Redis Lua, Python reconciliation, and C# parsers (legacy plain
  markers stay conservatively compatible).
- Cancel lease-ownership tokens before best-effort heartbeat metrics, and
  separate `broker_response_unknown` from `executor_lease_lost_after_broker`.
- Make retryable candidates reclaimable with a typed claim disposition so a
  failed attempt can be retried without advancing the stream cursor, and keep
  `broker_outcome_unknown` out of the normal retry path until deterministic
  broker reconciliation adopts or disproves the order.
- Fence every lease-owned mutation on exact candidate, stream-event and lease
  token identity (a missing token never authorises completion), keep terminal
  records immutable, and heartbeat the lease through long broker calls so a
  successor cannot claim mid-placement.
- Resolve the execution route and planned entry before validating the final
  stop; publish the same planned entry from Python; preserve the approved
  absolute stop after fill slippage; require an exact opposing-zone identity
  for pushed stops; and keep retryable / broker-unknown lifecycle states
  nonterminal so a successful retry is not refused as a backward transition.
- Decode structured candidate records with `cjson` during publication
  reconciliation, recognise every executor state, and refuse unknown legacy
  markers instead of treating them as retryable.
- Enforce a shared final protective-stop contract (`stop_plan_version=2`) so
  Python-approved final stop equals C# validated stop equals broker stop;
  fold opposing-zone push into the planner and reject
  `final_protective_stop_contract_mismatch` before any broker mutation.
- Replace scalar candidate markers with structured execution records and
  two-phase all-or-nothing publication reconciliation that preserves executor
  progress (`processing`/`ordered`/…) without partial ownership restores.
- Fence executor claims with token-owned renewable leases so stale workers
  cannot renew, transition, complete, release, or submit for a successor;
  mark post-broker uncertainty as `broker_outcome_unknown`.
- Separate lifecycle history from lifecycle transitions: telemetry and
  unknown events no longer default to `managing` or write
  `lifecycle_state:service`.
- Preserve ranked-intent priority under concurrent delivery with a token-owned
  cycle arbitration lock, typed publication outcomes, and terminal-only
  fallback; make the Redis stream authoritative and reconcile orphan
  candidate/reaction/thesis/cycle claims without another `XADD`.
- Evaluate Python reward/risk against the same Decimal protective-stop plan as
  the C# executor, carry candidate stop-plan v1 in contract v6, and reject
  cross-service differences beyond one symbol tick.
- Bound Redis group plans with lifecycle TTLs and remove them after rejection,
  dry-run, rollback, cancellation, range flip, full TP, or terminal closure;
  clear stale terminal reasons when a route later recovers.
- Finish the autonomous execution-integrity pipeline: every scanner/private
  intent now passes side-effect-free typed preflight before arbitration, one
  initial owns each closed M1 cycle atomically, exact preflight/arbitration/
  publication evidence is retained, and production Redis scripting fails
  closed without orphan candidate/reaction/thesis claims.
- Carry tier risk into every C# autonomous initial sizing path, separate order
  type from entry distribution, compute fill-relative targets from broker
  fills, enforce absolute/hybrid structural targets, and preserve exact manual
  `/algo` TP prices through reconciliation.
- Stop same-match replay from inflating confluence, make private range IDs
  formation-episode aware, render real range-side ownership state, and
  attribute status to the exact candidate/strategy that published.
- Make the autonomous execution pipeline single-winner and crash-safe: a
  cross-engine arbiter now selects at most one same-direction initial intent
  per M1 cycle, C# rejects opposite or already-active initial groups, candidate
  claim plus stream append is atomic, and the processing lease is 120 seconds.
- Keep Private Range producer-owned by its native M1 auction detector, resolve
  and persist range context only after the final private observation, carry a
  stable formation-episode ID across small rail drift, and prevent rail rearm
  while a candidate, pending order, or position still owns that side.
- Make `observe` overlap outcomes genuinely advisory, classify missing/invalid
  price data as `data_gap`/`warming_up` instead of chop, enforce chop as a hard
  Range Box/Range Edge eligibility contract, and enforce every declared
  strategy execution-policy field before publication.
- Prefer fresh structural matches over stale high-confluence duplicates,
  tighten narrow-zone and cross-source range compatibility, derive risk from
  the final tier, compute drift from remaining target room, and use the shared
  strategy-aware drift contract for mapped entries.
- Persist exact candidate-to-strategy attribution, symbol-specific status,
  unique route-funnel metrics, material-only route history, private
  range/trend route outcomes, and immediate scanner range withdrawal on a
  missing M5 execution frame.
- Require live producer-owned scanner/private range contexts for Range Box and
  Range Edge; withdraw absent sources immediately, stop resolver TTL refresh of
  source keys, and suppress stale scanner/worker status snapshots.
- Make Forming Cards report `AUTO CHECKING`, `AUTO WAIT`, `AUTO BLOCKED`, and
  `AUTO READY` from persisted per-match route outcomes; `AUTO READY` is now
  emitted only after the candidate stream `XADD` succeeds.
- Canonicalize StrategyMatch and Mapped Zone options, fail conflicting legacy
  aliases, route every active multi-match independently, and allow C# to
  atomically transition a publisher's `published` claim to `processing`.
- Exclude oversized context-only supply/demand zones from execution barriers
  while preserving them in the analysis pipeline.

### Added

- Add opt-in `SCANNER_GATE_*` M5/M15 structure filters for round-number-only
  anchors, exhausted levels, and low-confluence counter-bias reactions inside
  ranges. All filters default off; enabled rejects are removed consistently
  from cards and execution and recorded as `structure_gated`.

- Added real-Redis production-Lua concurrency/orphan-recovery tests, shared
  Python/C# stop-plan parity fixtures, and pending-fill restart/terminal-plan
  cleanup regressions.

- Added `AUTO_TRADE_MARKET_MAP_GUARD_ENABLED` so Market Map execution and its
  overlap/barrier guard are independently controlled. If omitted, the guard
  follows `AUTO_TRADE_MAPPED_ZONE_ENABLED`.

- Owner DM `/auto_close_all confirm` flattens all open ApexVoid Algo broker
  positions (and cancels pending labeled limits), pauses new entries, and
  books **Total net** from the real broker close fill (not a stop estimate).

- Range Box Scalp trades with Full TP **> 70** pips now scale out 50% at
  +30 pips from the broker fill, then ride the original Full TP for the
  remainder (`AUTO_TRADE_RANGE_BOX_SCALE_OUT_*`). Break-even stop move after
  scale-out stays off by default.

- Promote Key Level, Demand/Supply Zone, Session Level, and Trendline reactions
  to first-class executable scanner strategies (stable structural IDs, shared
  closed-bar confirmation lookback, bias as context not a hard gate). Generic
  `Zone Reaction` is removed from `DEFAULT_DETECTORS`. Mapped Zone remains
  disabled (`AUTO_TRADE_MAPPED_ZONE_ENABLED=false`).

### Fixed

- Owner `/trade_close` on a tracked algo position now drops Redis/engine
  state immediately after the broker fill so reconcile cannot re-book the
  same exit with a stop-loss estimate (duplicate POSITION CLOSED / wrong
  Total net).

- Algo `POSITION CLOSED` cards now include **Total net pips** (volume-weighted).
  Broker reconciliation closes also book the remaining volume into the weighted
  net and emit `group_result`. Partial TP cards show **Leg** pips plus **Net so
  far**. Manual/algo VIP booking measures signed pips from the real
  `broker_fill_price` when present, not only the advertised zone edge.
- Mapped Zone Reaction now allows at most one active initial group per
  structural thesis. A newer M1 touch/confirmation that produces a different
  `reaction_id` for the same `thesis_id` is suppressed via Redis
  `auto_trade:thesis_claim:{thesis_id}` (SET NX / Lua) before publish, with C#
  `active_thesis_group` defence-in-depth. Rearm requires the prior group to be
  terminal, price to leave the *raw* structural zone by
  `AUTO_TRADE_MAP_REACTION_REARM_ATR`, remain outside for
  `AUTO_TRADE_MAP_REACTION_REARM_BARS` closed M1 bars, then re-enter and form a
  newer confirmation. Keep `AUTO_TRADE_MAPPED_ZONE_ENABLED=false` until the
  thesis-lock images are deployed; `AUTO_TRADE_MAP_THESIS_LOCK_ENABLED=true`.
- Telegram ApexVoid Algo cards now show only the essential trade lifecycle
  (`order_filled` / opened, TP booked, risk protected, closed, group result,
  executor rejects). Pre-fill noise (`candidate_published`, `order_submitted`,
  `order_accepted`, `managing`, config/broker fatals) stays in Redis lifecycle
  streams and metrics but is suppressed at the Telegram render boundary. Partial
  TP cards no longer show remaining lot or volume lines.
- Mapped Zone Reaction now executes each structural reaction sequence at most
  once. Match/group identity derives from a stable `reaction_id` (symbol,
  strategy, direction, structural zone, touch/confirmation bar timestamps,
  reaction type) instead of the worker `event_ts` or spot-expanded entry
  bands. Redis `auto_trade:reaction_claim:{reaction_id}` SET NX claims the
  reaction across restarts; `same_thesis` and group IDs follow the reaction,
  and the C# executor rejects `duplicate_reaction_active` without Telegram
  spam. Zone coordinate jitter such as `4054.26–4062.31` vs `4054.08–4062.16`
  resolves to one `structural_zone_id`. Keep
  `AUTO_TRADE_MAPPED_ZONE_ENABLED=false` until verified on demo.

### Added

- Added typed, source-aware structural guard decisions with
  `observe|balanced|strict` policy modes, per-outcome Redis counters,
  `/auto_status` guard diagnostics, and non-destructive zone-reconciliation
  shadow metrics.
- Added a dedicated owner `/algo` execution path that preserves the supplied
  direction, entry zone, absolute SL, TP prices, setup and candidate/group
  ownership. Manual orders may coexist with autonomous or opposite-direction
  exposure on a broker-confirmed hedged demo account.
- Added executor-truth Telegram states for manual requests (`LIMIT ORDER
  PLACED`, `POSITION OPENED`, `DRY-RUN ONLY`, and machine-readable rejection)
  plus fatal Python/C# config-manifest checks for execution mode and Redis
  stream split-brain.
- Added the explicit `demo_eval` auto-trade profile, independent
  same-direction strategy groups, two-sided range analysis, multi-match
  tracking, unified scanner/private `RangeContext`, complete candidate
  lifecycle history, and Python/C# startup contract health manifests.
- Expanded `/auto_status` and execution Telegram cards with account capability,
  config health, resolved range/barriers, both rail states, active matches,
  strategy groups, counters, and real executor lifecycle badges.

### Changed

- Python and C# now resolve one versioned auto-trade configuration contract
  (manifest v2, candidate v5) from canonical environment names. Target/symbol
  sets are canonical in manifests while range target selection remains
  largest-fitting-first at runtime; execution max age and Redis storage TTL
  are separate settings.
- Executor readiness is published at `auto_trade:executor_readiness`.
  Non-hedged demo capability and storage-TTL drift are warning-only, with an
  explicit `AUTO_TRADE_NON_HEDGED_OPPOSITE_POLICY` for opposite exposure.
- Demo evaluation treats HTF bias as scoring/reporting metadata. Valid local
  BUY and SELL structures, including counter-bias mapped zones and trend
  pullbacks, remain executable and tracked; the cross-engine arbiter chooses
  one direction and at most one initial publication per M1 cycle.
- Initial strategy candidates always own independent groups. Only a trend
  candidate carrying an explicit compatible `parent_group_id` can enter the
  scale-in route.
- Demo evaluation does not require flat bot-owned XAU exposure for a
  same-direction initial group. Opposite autonomous initial exposure is
  rejected before broker submission, while duplicate candidate, reaction,
  thesis, group, and pending-order ownership remain independently enforced.

### Fixed

- Fixed the post-23-Jul demo-evaluation frequency collapse: a strategy's own
  key level/supply/demand source is no longer treated as an opposing barrier;
  ambiguous overlaps and temporary drift retain their exact StrategyMatch;
  counter-bias targets adapt around structure; and only confirmed stop-loss
  closures may enforce a zone cooldown.
- Fixed semantically identical Python/C# target ladders (descending versus
  ascending), numeric JSON forms, symbol ordering, FP Markets aliases, and
  demo account aliases being treated as fatal configuration mismatches that
  disabled all autonomous execution.
- Fixed owner `/algo` orders being filtered by autonomous confluence, regime,
  opposing-zone, exposure and scale-in gates, and fixed premature Telegram
  acknowledgement before the cTrader executor confirmed broker action.
- Fixed raw broker-name matching that rejected `FP Markets` when
  `AUTO_TRADE_EXPECTED_BROKER=fpmarkets`; broker identities now ignore spacing,
  punctuation and case.
- Zone-fill no longer hard-rejects when price is already inside the entry zone
  (production: Breakout Continuation SELL ~4025.59 inside 4024.37–4027.45).
  Geometry-aware routing classifies `price_inside_zone` / `invalid_limit_side`
  and falls back to `ProcessSingleInitialAsync` with reason
  `zone-fill geometry invalid; single-entry fallback` when
  `AUTO_TRADE_ZONE_FILL_FALLBACK_ENABLED=true` (default).
- Support/resistance detection is symmetric with dynamic clustering, controlled
  fallback barriers (`source=fallback_local_extreme`), and explicit range
  states (`no_range` / `provisional_range` / `confirmed_range` /
  `post_impulse_range` / `broken_range`). The previous 2-resistance/0-support
  scanner shape now either produces a confirmed fallback support or a
  machine-readable `missing_side_reason`.
- Market Map execute distance gains a small configurable tolerance
  (`AUTO_TRADE_MAP_EXECUTE_TOLERANCE_PIPS` / `_ATR`) so zones slightly outside
  the raw ATR window still execute after unchanged M1 touch + rejection.
- Entry drift is strategy-aware (range / trend / map ATR multipliers) instead of
  a single universal pip gate.

### Added

- Multi-strategy match storage at `auto_trade:strategy_matches:{symbol}` while
  preserving the legacy primary key `auto_trade:strategy_match:{symbol}`.
  Same-thesis setups merge as confluence; distinct theses stay tracked.
- Execution quality tiers A/B/C with risk multipliers
  (`AUTO_TRADE_TIER_A/B_RISK_MULTIPLIER`, post-impulse and one-sided multipliers).
  Tier B raises trade frequency at reduced risk; Tier C stays analysis-only.
- Adaptive range target ladder default `20,30,40,50,70` with 3-pip buffer and
  optional min RR (`AUTO_TRADE_RANGE_MIN_RR`).

### Fixed

- Regime classification no longer treats every narrow staircase step as chop.
  Height and containment tests stay as the primary chop signals; an additive
  directional override (LH/LL or HH/HL pairs over
  `AUTO_TRADE_REGIME_DIRECTION_LOOKBACK`, default `120`, with net displacement
  ≥ `AUTO_TRADE_REGIME_MIN_DISPLACEMENT_ATR`, default `4.0`) reclassifies as
  trend when both conditions hold, and records the override in the reason
  list. Ships dark behind `AUTO_TRADE_REGIME_DIRECTION_ENABLED=false`. Every
  scan still writes `auto_trade:regime_compare:{symbol}` with
  `{legacy}:{new}` so the counterfactual can be measured for 48h before
  enabling. The same override feeds the private-strategy regime gate when
  the flag is on so Trend becomes eligible on a directed tape.
- Market Map reaction distance no longer discards zones beyond `1.5×ATR`.
  Tracking (`AUTO_TRADE_MAP_TRACK_DISTANCE_ATR`, default `8.0`) and execution
  (`AUTO_TRADE_MAP_EXECUTE_DISTANCE_ATR`, default `1.5`) are separate: distant
  zones report as `waiting_for_touch` with both distances on `/auto_status`,
  while only the execute window may place an immediate market entry after
  unchanged M1 touch + rejection. Zones beyond track distance report
  `no_zone_in_range`.

### Added

- Added a second scale-in trigger, pullback add, alongside the existing
  momentum add — both now share one set of averaging-down/exposure
  invariants (favorable entry, profitable group, initial reached
  breakeven, every stop known, tranche cooldown, `MaxTranches`) but never
  both fire on the same candidate: fresh in-direction displacement is
  always evaluated as momentum regardless of anything else, otherwise a
  counter-direction/stale-displacement candidate is evaluated as pullback
  if `AUTO_TRADE_ADD_PULLBACK_ENABLED=true` (default `false`). Pullback
  requires no counter-direction BOS since the group opened, a retrace
  ratio from the extreme price back toward the initial entry within
  `AUTO_TRADE_ADD_PULLBACK_MIN_RETRACE`/`_MAX_RETRACE` (default
  `0.20`-`0.70`), the add entry inside a mapped zone on the correct side,
  and an M1 rejection candle; its stop sits beyond the retrace extreme
  (never clamped — a stop that would exceed the trend envelope rejects the
  add instead), and a combined-group-worst-case check
  (`AUTO_TRADE_ADD_MAX_GROUP_RISK_PCT`, default `3.0`) is enforced on top
  of the existing budget-based sizing check, since a pullback add's own
  stop isn't guaranteed to sit in profit the way the initial tranche's
  does.   Every scale-in tranche (momentum and pullback) is capped at
  `AUTO_TRADE_ADD_SIZE_RATIO` (default `0.5`) of the initial tranche's own
  size on top of exposure/risk/add-cap ceilings. Every rejection now
  names both the mode evaluated and the specific condition
  (`auto_trade:add_reject:{symbol}:{mode}:{condition}`), and each tranche
  is tagged `add_momentum`/`add_pullback` in its order message and
  persisted setup so the two are measurable separately from each other and
  from initial entries. Along the way, fixed the reason momentum adds have
  never fired in production (`/auto_status` showed `adds 0` regardless of
  regime): the only function that ever publishes a `regime="trend"`
  candidate never attached the displacement/BOS/opposing-level context
  `ScaleInTriggerPlanner` needs to accept a momentum add — box-scalp
  candidates carried it, trend candidates never did. Ships dark: both the
  new pullback flag and its prerequisite trend-candidate wiring land with
  `AUTO_TRADE_ADD_PULLBACK_ENABLED=false`, so momentum add behavior is
  otherwise unaffected until deliberately enabled.
- Added an inspectable Market Map strategy working set with one-hour Redis
  snapshots, `/auto_status` entry/filter/distance telemetry, rendered-map
  divergence warnings, and a default-enabled quality-gated `counter_bias` reaction
  path whose profit ladder is capped at box EQ.
- Added opt-in broker-confirmed range flip execution for defined box scalps,
  with opposing-edge targets, a `flip_pending` claim, timeout alerts, and the
  existing flat-exposure guard preserved. The feature defaults off.
- Added durable `algo_auto` and `algo_manual` execution ledgers plus per-stream
  fill count, win rate, mean R, total pips, and mean stop distance in trade
  stats and weekly reports. `/algo` remains visible in manual stats while
  `all_unique` removes the duplicate from combined figures.

- Added real broker execution for `/ algo` manual signals (PR 3 of 3):
  `ctrader-engine` now consumes `manual_trade:intents` (via a new
  Python-side bridge onto the existing `auto_trade:candidates` pipeline) and
  places a single pending LIMIT order at the owner's exact entry zone,
  absolute stop loss, and take-profit ladder — never a re-derived structure
  stop or fixed pip ladder like the autonomous box-scalp/trend/
  strategy-match engines use for themselves. Owner-override commands
  (`/trade_close`/`/trade_sl`/`/trade_cancel`) now route to the real
  position/pending order once a signal is algo-armed or filled, instead of
  only ever mutating Postgres/Telegram. Broker fill/TP/SL/close events drive
  the same `trade_ops.py → post_result → broadcast.fanout_update` lifecycle
  path a manually-confirmed signal already uses, so VIP/public channel posts
  update exactly like a manual command would. Ships dark:
  `MANUAL_ALGO_ENABLED` stays `false` by default.

- Added manual-signal broker execution infrastructure (PR 2 of 3; no broker
  executes real orders yet — this PR is plumbing only). `manual_signals`
  gained `execution_mode`/`execution_status`/`execution_intent_id`/
  `execution_revision`/`broker_position_id`/`broker_fill_price`/
  `execution_error` columns; a new versioned `ManualTradeIntent` contract
  (`app.signals.manual_intent`) carries the owner's exact entered SL/TP
  (not a re-derived structure stop) and publishes to the new
  `manual_trade:intents` Redis stream; and manual DM signals now accept an
  opt-in `/ algo` suffix (composes with the existing `/ vip` and `/ scalp`
  suffixes) that arms this contract when `MANUAL_ALGO_ENABLED=true`
  (default `false`). Nothing in this codebase consumes
  `manual_trade:intents` yet — a future `ctrader-engine` change is required
  before an `/ algo` signal can actually place a broker order.

- Added typed scanner-to-Algo strategy routing: the strongest completed M5
  detector match is transported with stable identity, expiry, entry/stop/TP
  context, attribution, and `/auto_status` visibility.

- Added per-position Telegram reply threads for ApexVoid Algo trade events,
  including standalone fallback when the original message is unavailable.
- Added proactive cTrader access-token refresh ahead of expiry, defensive
  `expiresIn` unit resolution, a host-mounted file mirror for rotated token
  recovery after Redis-volume loss, and rate-limited Telegram lifecycle alerts.
- Added an independent two-edge range-box scalp contract for ApexVoid Algo:
  BUY lower-edge and SELL upper-edge M1 rejections, full-position +50/+70-pip
  exits, repeated-touch 60-bar auction boxes, midpoint edge re-arming, stable
  box IDs, and confirmed-breakout retirement.
- Added shareable ApexVoid Algo Telegram cards for entries, full take profit,
  stop protection, warnings, and status without the old Auto Trader branding.
- Added momentum scale-in as independent, structure-stopped tranche positions
  under balance-based group loss, exposure, add-risk, and ladder invariants;
  averaging down is explicitly refused by design.
- Added planned two-limit zone fill (disabled by default), tranche/group tags,
  restart-safe multi-position reconciliation, binding-term telemetry,
  with-adds vs no-adds stats, and `AUTO_TRADE_ADD_REQUIRE_RISK_FREE`.
- Added weighted largest-remainder target splitting, broker-valid adaptive
  target plans for `0.02-0.04` lots, persisted TP ordinals, a monotonic stop
  ladder, and explicit target-weight and break-even-buffer controls.
- Added fingerprint-based cTrader refresh-token seeding with automatic cache
  reset, the `--reset-token-cache` operator command, live-account grant
  warnings, actionable account-grant remediation, and the optional
  `AUTO_TRADE_REQUIRE_DEMO_ONLY_TOKEN` hardening switch.
- Added demo-only cTrader market execution for qualified scalp
  candidates, with Fusion/Hedged/Trading-scope hard locks, one-position and
  freshness/spread/news/daily-cap gates, restart reconciliation, and durable
  Redis candidate/event contracts.
- Added operator-defined balance-band volume planning (`0.02-0.30` lots), a
  server-side `$6.5` stop, and broker-valid partial closes at
  `30/60/90/120/200` pips.
- Added owner auto-trade event DMs plus `/auto_status`, `/auto_pause`, and
  `/auto_resume` on both Telegram bots.
- Added a private auto-scalp worker that consumes only raw cTrader M1/M5/M15
  OHLC and live spot data, publishes Redis execution candidates, and has no
  scanner, forming-signal, Market Map, or Telegram dependency.

- Added lenient trailing setup-tag parsing, setup metadata in manual-signal
  confirmations, owner-only `/trade_untagged` backfill listings, and absolute
  `id:<db_id>` targeting for `/trade_tag`.
- Introduced this changelog and the repository rule requiring future changes to
  update it.
- Added deterministic significant-swing trendlines, diagonal reaction anchors,
  trendline confluence scoring, and trendline break-and-retest detection.
- Added the Box Breakout setup for accepted consolidation escapes, including
  displacement/two-close acceptance, edge retests, measured moves, and coil
  scoring.
- Added trendline, coil-contraction, breakout-buffer, acceptance-bar, and
  breakout-age configuration knobs.
- Added the two-sided Market Map assembler and monospace renderer, with scored
  zone tiers, bare levels, trendlines, breakout-retest pivots, human rounding,
  display merging, and per-side caps.
- Added owner-only `/trade_map`, guarded session-open Market Map DMs, scanner
  alert map references, gate-report map counts, and Market Map configuration
  knobs.
- Added the Market Map fallback ladder for spent zones, swept session levels,
  and round numbers so both trade sides retain actionable references.
- Added validated near-price SCALP range-edge rails to Market Map renders and
  scanner alerts, with a configurable display radius.
- Added deterministic Range Edge Scalp detection for both sides of local
  ranges, using repeated touch episodes, wick rejection, breakout invalidation,
  edge confirmation, EQ/opposing-edge targets, and shared Market Map rails.
- Added Range Edge Scalp configuration and scanner telemetry for barrier counts,
  active range quality, and live edge-touch state.
- Added a three-state market regime classifier (chop/trend/breakout) for
  ApexVoid Algo telemetry and private strategy context.
- Added trend-pullback and breakout-continuation entry modes reusing the
  existing price-action toolkit (swings, structure, displacement/zones,
  session liquidity), plus level-anchored target selection with
  spacing/de-dup rules and a fixed-ladder fallback.
- Added box-breakout as a tradeable setup instead of purely informational
  bookkeeping: an accepted, still-fresh box break now opens a position
  against the opposite box edge.
- Added 24h chop/trend/breakout share instrumentation on `/auto_status` and
  a one-shot owner DM when the chop share looks mistuned.
- Added the `AUTO_TRADE_TREND_ENABLED` kill switch (default off) and the C#
  `AUTO_TRADE_TREND_STOP_MIN_PIPS`/`AUTO_TRADE_TREND_STOP_MAX_PIPS`
  structure-stop band for trend-family candidates.

### Changed

- Raised the autonomous range-scalp minimum stop from 15 to 30 pips and added
  a 0.15 ATR swept-wick clearance floor. Manual stops retain owner precedence:
  opposing-zone protection may widen and notify, but never tighten them.
- Broker execution events now persist their `algo_auto`/`algo_manual` stream
  at fill time; watcher runner telemetry replies to the engine TP thread for
  algo-armed manual signals.

- The notify-only price watcher now reads closed M1 bars from ctrader-feed's
  Redis window as its primary source instead of polling Tiingo. Tiingo
  remains as a fallback for a single tick when the ctrader-feed bar is
  missing or older than `WATCHER_CTRADER_STALE_SECONDS` (default 180s); the
  watcher now runs even without `TIINGO_API_KEY` set, it just has no
  fallback for a gap in that case.
- Scanner detector output now owns strategy selection. The Algo worker no
  longer re-confirms a matched setup with a second M1/M5 or Market Map gate,
  and private strategies select the higher-confluence match instead of using a
  regime label as a global veto.

### Fixed

- Fixed the Market Map strategy deadlock where a collapsed zone could become
  the reported nearest target even though the same loop would never execute
  it. Degenerate geometry is now rejected by ATR/absolute minimum width,
  counted and warned; unreachable zones produce an honest distance-limit
  reason instead of waiting indefinitely for a touch.
- Fixed three manual `/algo` execution gaps found from live cards: (1) a
  broker-confirmed TP hit rendered as a bare "booked X% · +N pips" with no
  indication of which configured target fired, unlike the watcher-driven
  `TPn hit` label a regular manual signal gets — `manual_execution.py` now
  threads the already-resolved target ordinal through to `render_result`,
  which prefixes both partial and final-close cards with `TPn`. (2) the
  owner got a duplicate "🤖 ApexVoid Algo" DM for every take_profit/
  stop_moved/position_closed event on a manual-algo position, on top of
  the VIP/public channel card the signal already gets — `take_profit`/
  `stop_moved`/`position_closed` reuse the same event types the autonomous
  engines use and weren't filtered by `setup`, unlike the `opened` event
  which already used a distinct type for this reason; `_deliver_auto_trade_
  event` now skips any event with `setup == "Manual Algo"` outright. (3) on
  larger manual-algo positions (table/risk sizing > 0.13 lots) the first
  partial booking scaled up proportionally with account size instead of
  staying a consistent size; `VolumePlanner.FixFirstLegVolume` now pins the
  first leg to ~0.05 lots and redistributes the remainder evenly across the
  rest when total volume exceeds that threshold.
- Fixed `reconcile_opposing` over-trimming the zone map (regression from
  PR #89, live 22-23 Jul 2026 incident: zero `SETUP FORMING` cards for 6+
  hours). The original implementation treated any nonzero overlap between
  opposing supply/demand zones as a conflict and re-compared already-trimmed
  zones on every pass, so on dense M5 FVG output the cascade could empty the
  map. Opposing overlap now requires the same overlap-*ratio* bar as
  same-side merging (`ZONE_RECONCILE_OVERLAP = 0.5`, full containment still
  scores 1.0), each zone can be a trim *target* at most once per call, and a
  circuit breaker (`ZONE_RECONCILE_MAX_FRACTION = 0.20`, evaluated only once
  there are at least 5 input zones) discards the whole pass and returns the
  input unchanged — logging a warning and incrementing
  `auto_trade:zone_reconcile_aborted:{symbol}` — instead of letting a
  runaway cascade strip the map further. Added `auto_trade:zone_dropped:
  {symbol}` alongside the existing `auto_trade:zone_reconciled:{symbol}`
  counter, and a debug/info summary log line per call
  (`zone reconcile: in=.. trimmed=.. dropped=.. out=..`). Mitigated live via
  `AUTO_TRADE_ZONE_RECONCILE_ENABLED=false`; this PR ships with the flag
  still `false` and re-enabling is a separate follow-up step.
- Fixed range-scalp stops being placed inside the sweep wick, including an
  explicit `stop_exceeds_envelope_after_wick` rejection and counter when the
  safe stop cannot fit the configured risk envelope.
- Fixed duplicate `/algo` TP accounting and announcements: the engine owns
  broker TP fills and booked percentages, while the watcher only reports
  subsequent runner extension until the position closes.

- Box-scalp (both the private gate's own candidates and the scanner-bridge
  `Range Edge Scalp` match, labeled "Range Box Scalp") no longer fires
  outside the `chop` regime. This mean-reversion mutual-exclusion guard
  existed when the regime classifier first shipped but was silently dropped
  once scanner strategy-match selection landed ("private strategies select
  the higher-confluence match instead of using a regime label as a global
  veto"), so a box-labeled trade could fire straight into an active trend.
  Fixes a 22 Jul incident where a Range Edge Scalp BUY filled at the bottom
  of a sharp post-rally pullback and was stopped within a minute. Other
  scanner strategies (Box Breakout, Liquidity Sweep, Mapped Zone Reaction)
  are trend/breakout-appropriate by design and stay ungated. `/auto_status`
  telemetry (`selected_strategy`) now agrees with what actually publishes.
- Added an opposing-barrier veto (`AUTO_TRADE_OPPOSING_BARRIER_VETO_ENABLED`)
  for HTF supply/demand zones and round-number/reaction key levels sitting
  just ahead of an entry, and wired it into `_publish_strategy_match` (the
  scanner-bridge path), which previously had no opposing-zone check of any
  kind — the existing `AUTO_TRADE_HTF_VETO_ENABLED` check only protects the
  zone a trade retests *from*, not what could cap the move ahead of it.
  Fixes a 22 Jul incident where a Box Breakout BUY filled straight into an
  untested round-number supply level with nothing checking for it.
- Connected structural Market Map zones to an executable `Mapped Zone
  Reaction` strategy: Algo now evaluates M1 touches/rejections with
  M5/M15/M30 context instead of showing a valid map level while producing no
  strategy candidate. Round-number display fallbacks remain non-executable.
- Reworked `/auto_status` around strategy selection: it no longer labels the
  private Range Box strategy as a global gate, and now shows the selected
  strategy/source, scanner M5 result, private-strategy states, execution state,
  and current regime explicitly as telemetry only.
- Fixed `Range Edge Scalp` being modeled as a confirmation regime that could
  suppress otherwise valid scanner strategies. It is now one executable
  strategy alongside the other detector matches.

- Re-anchored the equity sizing table to `$200-$900 -> 0.02-0.06`,
  `$1,000-$2,000 -> 0.09-0.15`, and `$3,000-$5,000 -> 0.25-0.30`, holding
  `0.06` and `0.15` across the intervening gaps with intentional jumps at
  `$1,000` and `$3,000`; sizing selection is now explicit through
  `AUTO_TRADE_SIZING_MODE`, whose code default preserves the previous `min`
  behavior while deployment uses `table`.
- Enabled deployment zone-fill laddering with a `0.09`-lot minimum guard;
  smaller plans record the reason and use the existing single-entry path.
  Deployment keeps the recently raised `BE+6` buffer.
- At a `$2,072.02` balance and 65-pip stop, deployment table sizing changes
  per-trade risk from about `$39` (`1.9%`) to `$97.50` (`4.7%`). P&L across the
  eventual deploy timestamp is therefore not directly comparable; record that
  timestamp when this release reaches the VPS.
- Deployment configuration now protects positions at `BE+6` pips instead of
  `BE+3`; the engine's code fallback remains unchanged.
- cTrader token state now persists access-token expiry, reports its serving
  tier at startup, and requires `--yes-i-know` before token-cache reset.
- Auto-trade pip size is now configuration-owned (`0.1` for XAUUSD) instead of
  broker-derived, with a startup invariant across pip size, 100 oz contract
  size, and pip value per lot; the trend-stop maximum is now 65 pips to match
  the existing 6.5-price risk envelope.
- Switched the demo auto-trade account from Fusion Markets to FP Markets;
  `AUTO_TRADE_EXPECTED_BROKER` default moved from `Fusion` to `fpmarkets`
  (matches the `fpmarketssc` broker string cTrader reports). Credentials
  rotated in the deploy vault, not in this repo. Also switched the
  `/auto_status` and event-card icon from ⚡ to 🤖 for ApexVoid Algo.
- Scale-in/pyramiding is now restricted to the trend regime; an add
  candidate whose `regime` is not `"trend"` is rejected before the
  existing scale-in trigger checks run.
- Range-box candidates now require flat XAU exposure, bypass scale-in and
  planned zone-fill, and use one broker-valid 100% target; legacy executor
  target plans remain unchanged.
- Removed the six-trade daily ceiling from ApexVoid Algo; qualified box cycles
  remain unlimited until box invalidation or another safety gate blocks entry.
- Initial and add sizing now use `min(risk-based, equity-table)` from realised
  balance; the single-position guard is now a lifetime tranche-count limit,
  and initial/add stops share the same 15-65 pip structure-stop planner.
- Auto-trade trailing now holds the existing stop after TP2, moves it to TP1
  only after TP3, and moves it to TP2 after TP4 so the runner is not tightened
  one target too early.
- Auto-trade position size now follows the operator-defined balance schedule
  from `$200 -> 0.02` through `$5,000 -> 0.30`, floored to `0.01` lots.
  Low-volume plans close `0.02` at TP1/TP3, `0.03` through TP3, and `0.04`
  through TP4 instead of rejecting every position below five volume steps.
- Auto-trade configuration failures now disable only the executor for the
  current process, while distinct transient failures may retry on the next feed
  session and all startup faults publish a deduplicated operator event.
- Replaced scanner-fed auto entries with an independent `Auto Range Scalp`
  gate: M5/M15 build role-aware rails, M1 confirms rejection, active adverse M5
  momentum is blocked, entry drift is capped at 10 pips, and the nearest
  opposite-role rail must leave at least 30 pips of room.
- Added a broker-valid `0.08`-lot tier for demo balances from `$500` to `$999`,
  so a drawdown below `$1,000` does not permanently disable the executor.
- Increased two-sided range-scalp sensitivity with a longer local window,
  two-touch scored barriers, wider entry tolerance, and strict wick-rejection
  confirmation as an alternative to micro-CHoCH.

- Shared the conservative `rr_entry` and `pips_between` trade-math convention
  between entry cards and watcher accounting; SL/TP alerts now distinguish the
  booked fill from a materially farther bar extreme.
- Label Market Map SCALP rails as explicit `🟢 BUY` or `🔴 SELL` actions instead
  of positional arrows, including scanner-alert rail references.
- Evaluate automatic Market Maps once per configurable 60-minute bucket instead
  of only at session boundaries; materially unchanged boards remain suppressed.
- Restrict actionable SCALP output to the validated `ScalpRange` support and
  resistance pair; internal micro swings, round numbers, and standalone
  trendlines no longer receive misleading `BUY`/`SELL` labels.
- Reorganized `webhook/app/` from a flat module layout into `core/`,
  `persistence/`, `bot/`, `signals/`, `analysis/`, and `autotrade/`
  subpackages with no runtime behavior change; also fixed stale repo-name
  and branch references in the docs and swapped the SQLite-era backup
  procedure for a Postgres `pg_dump`/`psql` one.
- Renamed `webhook/` to `telegram-bot/` (it hasn't hosted a webhook since the
  bot moved to long-polling) and `ctrader-feed/` to `ctrader-engine/` (it has
  always run both the market-data feed and demo auto-trade execution off one
  cTrader session, not just a feed). Directory names, the compose service
  key, and CI build contexts moved.
- Renamed the published `apexvoid-ctrader-feed` Docker Hub image/container to
  `apexvoid-ctrader-engine` to match. The next deploy's `docker compose up
  --remove-orphans` (run by `ansible-library`'s `deploy_image` role) removes
  the old `apexvoid-ctrader-feed` container automatically since the compose
  project name is unchanged and only the service key moved — no manual VPS
  cleanup needed. `ansible-library`/`action-library` were checked: both are
  fully parameterized by this repo's own templates and needed no changes,
  aside from a stale `ctrader-feed` mention in a comment.
  `apexvoid-trading-bot` (the Telegram bot image) is unchanged.

### Fixed

- Watcher TP alerts now always book the configured TP level even when a candle
  opens or runs far beyond it; ApexVoid Algo reply cards no longer expose
  broker position IDs, and full-TP cards include the realized trade result
  without a duplicate technical group-result reply.
- Block false market-chased box breakouts unless closed M1 bars are continuous,
  the break receives a directional edge retest, and at least 35 pips remain to
  the nearest pre-break M1/M5/M15 barrier; nearby barriers now join the target
  ladder instead of being skipped in favor of distant levels. Room and targets
  are measured from the fresh execution spot rather than the prior bar close.
- Disable trend-continuation chase entries by default; the auto-scalp engine
  now waits for a pullback to the broken level before considering execution.
- Dedicated signal-bot scanner and auto-trade events now remain owner-DM-only;
  `SIGNAL_PUBLIC_CHANNEL_ID` is reserved for manual general-bot broadcasts.
- Auto-trade Telegram cursors now advance only after owner delivery succeeds,
  preventing transient DM failures from silently dropping an event.

- Fixed a 10x pip-unit mismatch that blocked every auto-trade candidate on FP
  Markets (`pipPosition=2`); brokers reporting `pipPosition=1` were unaffected.
- Startup recovery from a rejected cTrader access token no longer sends a
  duplicate account-authorization request after refresh already authorized the
  channel, avoiding the `ALREADY_LOGGED_IN` reconnect loop.
- cTrader token rotation now re-authorizes the configured trading account with
  the new access token before releasing the request lock; reconcile retries one
  lost-account-auth response, and refresh failures force a clean feed reconnect.
- Auto-trade session cleanup is now serialized with spot processing so a queued
  tick cannot race `_client` teardown and emit a secondary "session is not
  connected" fault.
- Fixed scale-in sizing that ignored the equity-table exposure ceiling and a
  worst-case rule that blocked valid adds; banked profit and trailed stops now
  contribute to a hard group loss-ceiling headroom without using floating
  equity.
- Cached cTrader refresh tokens no longer shadow a newly authorized `.env`
  token, which previously preserved stale account grants across restarts.
- Auto-trade startup and spot-processing faults no longer cancel the shared
  market-data session or trap the feed in a reconnect loop with no bars.
- Untyped Telegram forming cards and rendered Market Maps cannot create or
  suppress Algo candidates; scanner execution now uses only the explicit typed
  strategy-match contract, while the private worker remains independent.
- Auto Trader quote-gate failures such as stale prices, excessive spread, or
  entry drift now terminate the candidate and advance its Redis cursor instead
  of retrying the same candidate and spamming repeated owner error messages.
- Unexpected Auto Trader candidate failures now use a bounded retry delay and
  emit at most one owner error per candidate while recovery is attempted.
- Watcher SL accounting now treats fills anywhere inside the entry zone as
  breakeven, preserves signed profit for trailed stops, and only books a loss
  when the actual stop fill lands beyond the losing side of the zone.
- `watcher`: price ordinary SL/TP hits at the configured level instead of the
  bar extreme, while preserving honest open-gap fills; this removes inflated
  losses/profits and the midpoint-entry mismatch with the published card.
- Updated the reusable deploy-workflow reference and container source metadata
  for the GitHub username change to `st-mich43l`.
- Manual-signal setup tags are no longer silently dropped when written without
  the literal `/ setup` prefix, including slashless human-entered tags.
- Market Map: reject weak or ATR-distant zones, prevent key levels/trendlines
  from widening entry bands, and compact noisy tags in the owner render.
- Market Map: cap merged band width, remove same-side render overlap, deduplicate
  tags case-insensitively, and require genuine HTF confluence for MAJOR tiers.
- Route on-demand and session-open Market Maps through the dedicated scanner
  bot instead of the general signal-management bot.
- Register and poll owner-only `/trade_map` on the dedicated signal bot while
  retaining the same command on the general bot.
- Give the dedicated signal bot the same `/start` welcome and public
  channel/Knowledge Base links as the general bot.
- `ctrader-feed`: stamp live closed-bar close from the last in-period spot bid,
  with range clamping and an authoritative historical fallback when no spot is
  available; live trendbars without `deltaClose` no longer persist
  `close == low` and poison scanner structure/regime analysis.
- `ctrader-feed`: perform a full-window historical upsert on startup so every
  deployment repairs previously poisoned Redis bars; reconnect backfill remains
  incremental.
- `ctrader-feed`: warn when consecutive live bars keep closing at the same range
  extreme, controlled by `BAR_QUALITY_LOOKBACK` (default `6`).
- `watcher`: count a SELL whole-price TP as hit as soon as price enters that
  handle (for example, `4017.xx` now reaches TP `4017`).
- `watcher`: attach the owner Close/partial-close button to VIP SL-hit alerts
  and book those closes with negative pips instead of TP-style profit pips.

## 2026-07-15

This baseline summarizes the production changes merged from 2026-07-10 through
2026-07-15.

### Added

- Added the in-repo cTrader Open API feed service with Redis OHLC and live spot
  ingestion, health reporting, token refresh persistence, and deployment
  wiring.
- Added the notify-only price-action scanner and its analysis toolkit, including
  market structure, dealing ranges, session levels, liquidity sweeps, zone
  scoring, and multi-timeframe context.
- Added chop-regime detection and the WAIT protocol: trend-continuation setups
  are muted in chop, while grade-A edge fades remain eligible.
- Added setup-agnostic zone-band deduplication to prevent different detectors
  from repeatedly alerting the same trade idea.
- Added a dedicated Telegram token option for scanner notifications.
- Added a public `/start` welcome message linking to `@apexvoidtrading` and the
  trading knowledge base.
- Added automatic daily cancellation of pending orders that were not filled on
  their signal day.

### Changed

- Improved scanner alert quality with tighter reachability, correct-side,
  freshness, zone-width, overlap, and confluence checks.
- Added session-range sweeps and zone-quality scoring to scanner setup ranking.
- Polished weekly performance recap output and removed obsolete WAE scanner
  gates.
- Capped chop-fade TP guidance at the opposite edge of the active range.

### Fixed

- Fixed cTrader trendbar and spot-price scaling before values are written to
  Redis.
- Added a spot plausibility guard so missing, non-finite, non-positive, or
  mis-scaled live prices fall back to the execution-timeframe close instead of
  silencing detection.
- Fixed cTrader feed subscription diagnostics, liveness reporting, and refresh
  token persistence.
- Fixed scanner silence when owner notifications are disabled by keeping the
  analysis status path active.
