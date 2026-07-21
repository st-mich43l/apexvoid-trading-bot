<!-- Every PR with behavior, config, deployment, or operator-facing changes
must add a concise entry under Unreleased. -->

# Changelog

All notable changes to ApexVoid Trading Bot are documented in this file.

The project deploys from `master` without tagged releases. Add new entries to
`Unreleased` in the same pull request as the code change, then move them into a
dated section after deployment.

## Unreleased

### Added

- Added weighted largest-remainder target splitting with deterministic
  five-exit feasibility remedies, a monotonic TP1-to-TP4 stop ladder, USD
  deposit-asset warnings/strict mode, a maximum-lots cap, and explicit risk,
  pip-value, target-weight, and break-even-buffer controls.
- Added fingerprint-based cTrader refresh-token seeding with automatic cache
  reset, the `--reset-token-cache` operator command, live-account grant
  warnings, actionable account-grant remediation, and the optional
  `AUTO_TRADE_REQUIRE_DEMO_ONLY_TOKEN` hardening switch.
- Added demo-only cTrader market execution for qualified scalp
  candidates, with Fusion/Hedged/Trading-scope hard locks, one-position and
  freshness/spread/news/daily-cap gates, restart reconciliation, and durable
  Redis candidate/event contracts.
- Added balance-tier volume planning (`0.12/0.20/0.30` lots), a server-side
  `$6.5` stop, and broker-valid partial closes at `30/50/70/90/130` pips.
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

### Changed

- Auto-trade position size is now recomputed per trade from account risk
  (default `2%`) and the fixed stop instead of a balance-to-lots table that
  risked about `5.9%` on the live demo; the target ladder is now
  `30/60/90/120/200`. Because sizing uses `AUTO_TRADE_SL_DISTANCE`, changing
  that fixed stop now also changes position size.
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

### Fixed

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

### Removed

- Removed the hardcoded `VolumePlanner.LotsForBalance` balance-tier table.

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
