# Symbol-routed multi-instrument runtime

The runtime supports symbol-routed multi-instrument execution while keeping
cTrader manifest-authoritative.

## Absolute production boundary

```text
live instruments = XAU + EURUSD + GBPJPY + USDJPY + GBPUSD
feed instruments = XAU + EURUSD + GBPJPY + USDJPY + GBPUSD
CTRADER_CONFIGURATION_SOURCE = manifest
CTRADER_MANIFEST_PARITY_MODE = off
```

EURUSD, GBPJPY, USDJPY, and GBPUSD are demo-live with their own pip/lot/zone
geometry. Do not inherit XAU dollar merge/round/FVG widths onto FX.

XAU remains required in `live_instruments`. Additional live symbols are
allowed after explicit trading-policy review.

## Instrument execution policies

Trading policy is explicit per instrument in `config/trading-bot.yml`:

- XAU uses `xau_fixed_4r_v1` (pack `xau_fixed_4r_v1`, renamed 2026-09-22
  from `xau_fixed_2r_v1` — owner-reported: the name had gone stale since
  the ladder itself moved to 4R on 2026-09-15): **technique** SL from
  structure inside a 50–60 pip entry-targeted envelope, TP at 1R/2R/3R/4R
  with 40/20/20/20 closes and breakeven after TP1/1R (diverges from
  `fx_fixed_2r_v1`'s 1R/2R shape deliberately). **M1 scalping** stays on
  `strategies.scalping` discovery (1:2 / 1:1) via
  `technique_fixed_rr_targeting` — instrument fixed_rr must not expand scalp
  matches. Compatibility policy `xau_current_v1` remains for ladder inheritance
  in tests / non-live defaults.
- EURUSD and USDJPY use `fx_fixed_2r_v1`; GBPJPY keeps the historical
  `fx_fixed_2r_frontload_v1` policy name. All three share the uniform
  `FIXED_RR_REQUIRED_TARGETING` contract (1R/2R, 50/50, `breakeven_after_r: 1.0`)
  and require a **named reaction session**, **stop envelope**, **activation**,
  and **price scale** pack once live/paper. The envelope is pair-scale
  (EURUSD/USDJPY 10–18 pips, GBPJPY 15–30). Do not copy dotted stop or
  geometry paths per pair.
- A new live FX instrument declares `policy`, `reaction_session`
  (`london_ny`, `tokyo_london`, `tokyo_london_ny`, or a raw `7-11,13-16`
  list), `stop_envelope`, `activation`, and `price_scale`. Register a new
  session name in `REGISTERED_REACTION_SESSIONS` instead of duplicating
  hour strings. `overrides` remains an escape hatch for a single leaf not
  covered by a pack (e.g. a defended-level guard or the event-cluster
  news guard, neither of which is pack-composed).
- Reusable `instrument_packs` contain market, contract, analysis, targeting,
  and manual capability policy only. They may not contain identity or rollout
  (`broker_symbol`, `canonical_symbol`, `aliases`, `enabled`, `rollout`). Every
  concrete instrument that selects a pack must declare its own rollout,
  preventing a newly declared symbol from becoming broker-live merely by
  selecting that pack.
- Manual owner execution has its own per-instrument capability profile; it is
  not inferred from `targeting.mode`. `entry_mode: zone_ladder` keeps XAU's
  shallow/mid/deep entry distribution, while `single` is the current FX
  entry-at route. `risk_reference` is deliberately restricted to `shallow`.
- FX books 50% at 1R and 50% at 2R. TP1 moves the runner to protected
  break-even (group weighted fill). There is no R-trail after 1.5R.
- Broker-step rules may defer an undersized partial to a later target; they
  never inflate a small close beyond its declared share.
- FX keeps the existing equity sizing and strategy-specific risk multipliers.
  The target policy adds no FX-only lot multiplier to compensate for a shorter
  exit plan.

```yaml
policy: fx_fixed_2r_v1
reaction_session: london_ny   # or tokyo_london, tokyo_london_ny
stop_envelope: {min_pips: 10, max_pips: 18, sl_distance: 0.0018}
activation: {require_sweep_body: true, trigger_maximum_age_bars: 3, max_spread_pips: 1}
price_scale:
  round_step: 0.001
  market_map: {change_min: 0.0001, fallback_radius_price: 0.01, scalp_radius_price: 0.006}
  zone_merge_gap_price: 0.0005
  zone_merge_max_width: 0.0015
  opposing_minimum_separation_price: 0.0015
  fvg_entry_max_width_price: 0.0015
targeting:
  mode: fixed_rr
  reward_risk: 2.0
  target_r_multiples: [1.0, 2.0]
  close_ratios: [0.5, 0.5]
  breakeven_after_r: 1.0
  entry_clips: 2
manual:
  enabled: true
  algo_enabled: true
  entry_mode: single
  risk_reference: shallow
  risk_multiplier: 1.5
  target_close_ratios: [0.25, 0.25, 0.50]
```

The target is computed only after the entry route and protective stop are
final. Prefer 2R when opposing room holds it; fall back to a single 1R
all-in target when room is between 1R and 2R; reject below 1R. New FX
instruments must declare this policy, targeting block, `reaction_session`,
`stop_envelope`, `activation`, and `price_scale`; symbol-name hard-coding
is not used.

## Per-symbol position management

Beyond geometry, individual pairs own genuinely different management
mechanisms, grounded in each pair's real 2026 market behavior:

- **GBPJPY** (`fx_fixed_2r_frontload_v1` policy name retained; uniform 50/50
  targeting): ATR(14) ~180 pips/day vs EURUSD's ~70. Close-ratio front-load
  was retired so cells stay comparable across symbols. Separately, when a
  BoE and a BoJ high-impact calendar event land within 48h of each other
  ("volatility clusters" compound rather than add), the news guard widens
  from the normal 30-minute single-event window to 3 hours around
  whichever event is nearer (`actionability.gates.event_cluster_guard_*`,
  off by default, on only for GBPJPY via `overrides`). Dig 2026-08-25:
  Key Level Reaction was toxic (0/4, −118 pips) mainly from Asia/`outside`
  and weak/ambiguous prints — quality overrides keep the family live:
  `require_killzone`, `require_explicit_role`, `require_htf_alignment`,
  `min_grade: A`, and `analysis.levels.minimum_key_touches: 3`.
- **USDJPY** (defended-level guard): Japan/the US intervened when USDJPY
  breached 160. Fresh **BUY** entries within
  `risk.exposure.defended_level_buffer_price` of
  `risk.exposure.defended_levels` (via `overrides`) are hard-blocked;
  **SELL** near the level is allowed (aligned with intervention). Buffer is
  30 pips (`0.30`) — a prior symmetric 100-pip band zeroed the book.

## Account-level architecture

```text
CTraderAccountRuntimeHost / FeedRunner
├── one authenticated Open API connection
├── one account authorization state
├── one request serialization gate
├── one account snapshot/reconciliation partitioner
├── one candidate stream consumer
└── InstrumentRuntimeRegistry
    └── InstrumentRuntime (XAU, EURUSD, GBPJPY, USDJPY, GBPUSD live on demo)
```

## Instrument runtime registry

Python: `app.runtime.InstrumentRuntimeRegistry` built from resolved
configuration (no ENV/YAML parse inside the registry).

C#: `InstrumentRuntimeRegistry` with alias maps and symbol-id binding.

## Manifest V2

`manifest_version = 2` adds `instrument_runtimes`.

Top-level `feed` / `auto_trade` remain as **deprecated XAU compatibility
projections**. Manifest V1 mounts upgrade to an XAU-only V2-equivalent shape;
V1 is never silently interpreted as multi-symbol.

## Rollout semantics

| Rollout | Feed | Analysis | Public Telegram | Candidates | Broker orders |
|---|---|---|---|---|---|
| disabled | no | no | no | no | no |
| feed_only | yes | no | no | no | no |
| analysis_only | yes | yes | no | no | no |
| paper | yes | yes | no | paper only | no |
| live | yes | yes | yes | yes | yes |

Paper is **not** mapped to the global dry-run flag.

## Redis isolation

Existing XAU keys and shared streams
(`auto_trade:candidates`, `auto_trade:events`, `execution:trade_plans`) are
unchanged. New symbols must use canonical instrument IDs; aliases normalize
before key construction. Unknown pip/digits fail closed (no `1.0` / 2-digit
fallback).

## Candidate dispatch

One shared candidate consumer routes by `candidate.symbol` → canonical
instrument → execution runtime. Alias forms on the stream are rejected until
normalized. Paper instruments never place broker orders.

## Reconciliation

Account positions/orders are fetched once, then partitioned by resolved
instrument. Unknown-symbol broker positions are logged as unmanaged and are
not adopted into XAU.

The periodic legacy/manual reconcile uses one account-wide snapshot for all
bound instruments. TradePlan polling has a narrower freshness boundary: one
lazy account snapshot is shared by submitted-leg reconciliation and open
position management for an **active** symbol, while symbols with no relevant
state do not touch the broker snapshot at all. The snapshot is not reused
across active symbols because pending-entry evaluation may submit or cancel an
order before reconciliation; reusing an earlier symbol's snapshot would make
the later symbol reconcile against pre-mutation broker state.

Python market-data work follows the same symbol boundary:

- effective instrument contexts are cached on the immutable resolved runtime;
- ZoneWatch membership is indexed per canonical symbol (the global index is
  retained for migration/recovery);
- closed bars keep FIFO order within a symbol but use independent workers and
  OHLC caches across symbols;
- spot wakes are latest-only per symbol because the wake contains no price and
  evaluation always reads the latest Redis quote.

Broker mutations remain serialized by the account request gate. Cross-symbol
analysis concurrency must not be treated as an account-risk lock.

## Source-mode boundary

| Mode | Behaviour |
|---|---|
| `environment` | XAU-only compatibility; legacy trading ENV loaders (rollback inventory required) |
| `manifest` | Authoritative ResolvedRuntimeManifest V2; secrets + bootstrap ENV only |

PR4 flips production to `manifest` with `CTRADER_MANIFEST_PARITY_MODE=off`.
See [manifest authority cutover](../configuration/manifest-authority-cutover.md).

## Instrument onboarding procedure

1. Select or add a reusable instrument pack; keep identity and rollout out of
   the pack.
2. Add one concrete `instruments.<SYMBOL>` declaration with broker/canonical
   identity, aliases, contract deltas, manual capability, and an explicit
   initial rollout (`feed_only` is the safe default).
3. Compile/check the manifest. Confirm the Python effective context and the
   C# runtime registry both resolve every declared alias to the same book.
4. Validate feed-only, then analysis-only, then paper behavior and per-symbol
   pip/lot/stop geometry.
5. Move to `live` only after explicit trading-policy review. `/trade` will then
   list the symbol automatically; `/algo` remains fail-closed unless its manual
   profile enables execution.
6. Mirror the canonical config in deployment inventory until the remaining
   duplicated Ansible artifact is removed.
