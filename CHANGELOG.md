<!-- Every PR with behavior, config, deployment, or operator-facing changes
must add a concise entry under Unreleased. -->

# Changelog

All notable changes to ApexVoid Trading Bot are documented in this file.

The project deploys from `master` without tagged releases. Add new entries to
`Unreleased` in the same pull request as the code change, then move them into a
dated section after deployment.

## Unreleased

### Added

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
