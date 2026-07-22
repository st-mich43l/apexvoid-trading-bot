<!-- Every PR with behavior, config, deployment, or operator-facing changes
must add a concise entry under Unreleased. -->

# Changelog

All notable changes to ApexVoid Trading Bot are documented in this file.

The project deploys from `master` without tagged releases. Add new entries to
`Unreleased` in the same pull request as the code change, then move them into a
dated section after deployment.

## Unreleased

### Added

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
  ApexVoid Algo, with a router that keeps trend/breakout candidates
  mutually exclusive with the box-scalp gate on every bar.
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
- Forming signals and their detector/Market Map gates can no longer create or
  suppress Auto Trader candidates; `SCANNER_ENABLED` no longer controls whether
  the private auto-scalp worker runs.
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
