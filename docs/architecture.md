# Architecture

## Component overview

ApexVoid is a **multi-service** XAU trading stack on one Docker host:

| Service | Role |
|---|---|
| `postgres` | Durable signal lifecycle, pips/stats accounting |
| `redis` | Closed OHLC bars, ZoneWatch state, TradePlans, executor events |
| `config-compiler` | One-shot: validate YAML + emit `ResolvedRuntimeManifest` |
| `ctrader-engine` | cTrader Open API feed + TradePlan V8 execution |
| `bot` (`algo-bot`) | Telegram, scanner, ZoneWatch activation, plan publish |

```text
┌──────────────────────────── single host (Docker) ───────────────────────────┐
│  postgres ◄── SQL ── algo-bot (python -m app.main)                           │
│  redis    ◄── ZSET / streams / keys ──► ctrader-engine (.NET)                │
│             ▲                                                                │
│             └── config-compiler writes /runtime/resolved-runtime.json         │
└──────────────────────────────────────────────────────────────────────────────┘
         │ outbound HTTPS / Open API
         ▼
  Telegram · Anthropic (optional) · cTrader demo/live API
```

No inbound application ports. Only SSH to the host is required.

## Process model (`algo-bot`)

`app/main.py` is the composition root:

1. Install **ZoneWatch execution cutover** and same-cycle publish retry.
2. Verify mounted runtime manifest; `init_db()` (Postgres); wait for Redis.
3. Publish Python config-health manifest; reconcile auto-trade startup state.
4. Start Telegram long-polling (owner bot; optional separate scanner bot).
5. Spawn supervised background loops (survive brief Redis DNS blips):

| Loop | Purpose |
|---|---|
| `bar_event_dispatcher_loop` | One `bars:new` subscriber: scanner + worker + ZoneWatch M1 + HFS |
| `zone_watch_execution_loop` | Spot-driven re-eval → activate → direct publish |
| `forming_price_track_loop` | Live edits on forming Telegram cards |
| `setup_expiry_sweeper_loop` | Age out stale setups / watches |
| `market_map_scan_loop` | Market Map refresh / delivery |
| `auto_trade_events_loop` | Executor events → Telegram |
| `auto_trade_stats_ingestion_loop` | Durable stats cursor for `/trade_stats` |
| `watcher_loop` / `calendar_sync_loop` / `weekly_report_loop` | Manual + calendar |

Production **does not** start `strategy_match_ready_loop` as the primary path:
ZoneWatch → direct TradePlan is authoritative. Ready-stream helpers remain for
exceptional durable fallback.

## Message and data flows

### Manual signal (DM → channel)

1. Owner DMs a zone entry string.
2. Parser extracts side, zone, SL, TPs (2-digit shorthand expanded).
3. Post to VIP (and public unless `/ vip`); insert Postgres lifecycle row.
4. Later `close` / `cancel` / reply-`cancel` update status and channel posts.

### Autonomous technique / reaction publish

```text
closed M5/H1/M15 bars (Redis)
        │
        ▼
 analysis detectors  ── five techniques + structural reactions
        │
        ▼
 ZoneWatch discover (watching_retest) + candidate StrategyMatch
        │
        ▼
 location (premium/discount) + activation (M1 trigger / chase)
        │
        ▼
 try_publish_executable_signal → TradePlan V8 (Redis)
        │
        ▼
 ctrader-engine claim → size → orders → TP/BE/SL events
        │
        ▼
 algo-bot delivery + stats ingestion → Telegram
```

Detailed technique rules, chase, closed-bar invalidation, and opposing-room
filters: [technique-zonewatch-publish.md](technique-zonewatch-publish.md).

### cTrader engine

- Writes closed bars into `bars:{SYMBOL}:{TF}` ZSETs and publishes `bars:new`.
- Consumes TradePlan V8 from Redis; manages multi-leg groups with shared
  absolute stop and equity-table volume.
- Never touches Postgres; Redis is the only cross-service boundary
  ([redis-contract.md](redis-contract.md)).

## Persistence

### PostgreSQL (`signals`)

Manual signal lifecycle, pips/results, and autonomous stats ingested from
executor events. Schema is owned by the algo-bot store / migrations — not a
bind-mounted SQLite file.

### Redis

| Family | Examples |
|---|---|
| OHLC | `bars:XAU:M5`, … |
| ZoneWatch | `analysis:zone_watch:{id}`, candidate + location-range keys |
| Plans / execution | TradePlan V8 payloads, leases, exposure |
| Telemetry | `auto_trade:metrics:*`, funnel counters, last location/activation |

## Configuration authority

- **Secrets / bootstrap:** `.env` (`TELEGRAM_*`, `CTRADER_*`, `POSTGRES_*`,
  `DATABASE_URL`, `REDIS_URL`).
- **Non-secret tuning:** `config/trading-bot.yml` (detectors, techniques,
  actionability, execution envelopes).
- **ResolvedRuntimeManifest:** compiled at compose start; cTrader reads
  `CTRADER_CONFIGURATION_SOURCE=manifest`.

See [configuration/configuration-architecture.md](configuration/configuration-architecture.md).

## Design decisions

- **Outbound-only host.** Long-polling + Open API; no public webhook.
- **Python owns the plan; C# executes.** Geometry, room, and targets are
  decided in algo-bot; the engine sizes and manages broker orders.
- **ZoneWatch before setup.** Detected structure is retained until quote +
  activation prove executability — avoids card spam and ready-stream ACK loss.
- **Techniques are zone-family, not reaction-family.** Exact-name taxonomy in
  `strategy_taxonomy.py`; no substring classification.
- **XAU-first.** Multi-symbol routing exists as a programme; production live
  instrument remains XAU ([runtime/multi-symbol-routing.md](runtime/multi-symbol-routing.md)).
- **Fail closed.** Stale quotes, spread, news guards, opposing room, stop
  envelope, and demo-token checks block rather than guess.
