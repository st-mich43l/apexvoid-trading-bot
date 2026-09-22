# Effective instrument configuration context

## Motivation

The repository already supports a dynamic `instruments.<SYMBOL>` registry, but
most runtime consumers still read global XAU-shaped leaves
(`contract.instrument.*`, `market_data.lookbacks.*`,
`analysis.zones.symbol_contract.*`).

This document describes the typed **effective instrument context**: a
composition boundary that returns the complete configuration required to
evaluate one instrument safely, without changing the current XAU trading
runtime.

## Current single-symbol compatibility boundary

XAU remains the only operational trading instrument.

PR3 adds `InstrumentRuntimeRegistry` and rollout-aware scanner routing on top of
this context — still with **XAU live only** in production. See
[multi-symbol routing](../runtime/multi-symbol-routing.md).

Existing consumers continue to use:

```python
runtime_config.contract.instrument.pip_size
runtime_config.market_data.lookbacks
runtime_config.analysis.zones.symbol_contract
```

Those paths stay exactly equivalent for XAU. The new API coexists:

```python
runtime_config.for_instrument("XAU")
runtime_config.enabled_instruments()
runtime_config.live_instruments()
runtime_config.instrument_for_broker_symbol("XAUUSD")
```

**Declaring an additional instrument does not make it live-tradable in this PR.**

A second instrument may be declared (for example `feed_only` or `disabled`)
for registry and validation exercises. It must not become executable unless its
rollout is explicitly `paper` or `live`, and production configuration must keep
only XAU live.

## Composition order

Effective contexts are built from the already-resolved canonical runtime root.
There is no second loader, no ENV re-read, and no YAML re-parse.

```text
configuration sources
→ canonical resolver
→ validated root runtime config
→ effective instrument context factory
→ consumers
```

Composition conceptually is:

```text
schema defaults
+ active application profile
+ global configuration
+ selected instrument policy
+ instrument-specific overrides
+ compatibility derivations
= EffectiveInstrumentConfig
```

## Rollout stages

Typed stage: `disabled | feed_only | analysis_only | paper | live`.

| Stage | Meaning |
| --- | --- |
| `disabled` | Not subscribed or processed |
| `feed_only` | Market-data collection permitted |
| `analysis_only` | Analysis may run; no executable/public trading output |
| `paper` | Full flow without live broker execution |
| `live` | Broker execution permitted |

### Compatibility mapping

Legacy `enabled: true` without an explicit `rollout` maps to **`live`**.

Legacy `enabled: false` maps to **`disabled`**.

Conflicting `enabled` + `rollout` pairs fail closed. Existing production YAML
that uses `enabled: true` therefore keeps current XAU live behaviour; it is not
reinterpreted as `analysis_only` or `paper`.

Derived rollout values appear in effective provenance as
`derived_compatibility_rule` / `enabled_compatibility_mapping`.

## Policy inheritance

Instruments may reference a named policy. Currently registered:

- `xau_fixed_4r_v1` — XAU technique structure fixed_rr (1R/2R/3R/4R,
  40/20/20/20, BE at TP1/1R); pack expands stop envelope 50–60 pips
  (entry-targeted) and `tokyo_london_ny` publish windows. Renamed
  2026-09-22 from `xau_fixed_2r_v1` (owner-reported: the name had gone
  stale since the 2026-09-15 ladder change to 4R — no targeting-contract
  change). M1 scalping on XAU is gated separately
  (`technique_fixed_rr_targeting`).
- `xau_current_v1` — inherit the current resolved global trading domains
  (strategies, actionability, execution, risk, lifecycle, and shared market
  data / analysis shells) as the XAU ladder compatibility policy
- `fx_fixed_2r_v1` / `fx_fixed_2r_frontload_v1` — FX structure fixed_rr packs
  (uniform 1R/2R 50/50 + `breakeven_after_r: 1.0`; frontload name retained)

Unknown policies fail closed. When omitted for XAU without a pack, the
resolver still binds `xau_current_v1` deterministically for compatibility.

Sparse `instruments.<ID>.overrides` may override dotted catalog paths after
policy selection. Named packs (`reaction_session`, `stop_envelope`,
`activation`, `price_scale`) expand first; explicit overrides win over the
pack and over inherited global/policy values for the effective context only.
They do not mutate the shared root in place.

## Override rules

- Unknown catalog paths fail closed
- Secret paths fail closed
- Protocol-constant paths fail closed
- Paths absent from the Python runtime projection fail closed
- Override paths are recorded in effective provenance

## Fail-closed behaviour

Resolution rejects:

- unknown instrument policy
- non-disabled instruments without contract units
- duplicate active canonical/broker symbols (and aliases)
- missing/non-positive pip size or contract size
- invalid price digits
- missing required lookbacks for active instruments
- unsupported rollout values
- invalid overrides (unknown / secret / protocol / non-projected)
- unknown symbol lookup (`for_instrument` / broker lookup)

Disabled instruments may omit contract metadata in the registry. Calling
`for_instrument` on an incomplete disabled instrument still fails clearly.

There is **no** generic pip-size fallback and **no** silent fallback to XAU
units for another instrument.

## Provenance

Effective contexts expose secret-safe provenance entries describing whether a
value originated from schema default, profile, CONFIG_FILE, process ENV, init
value, compatibility derivation, policy binding, or instrument override.

When an effective value references an already-resolved global leaf, the
existing resolution trace is preferred over inventing a new source.

## XAU parity guarantee

`runtime_config.for_instrument("XAU")` must match the current XAU compatibility
projection for units, lookbacks, zone geometry, and shared trading domains
(execution, risk, lifecycle, strategies, actionability).

### Feed mapping ambiguity

Production YAML may set `instruments.XAU.broker_symbol: XAU` while
`market_data.ctrader_feed.symbol` is `XAUUSD`. This PR preserves that deployed
behaviour: the effective XAU context recognizes `XAUUSD` as an alias for lookup
without changing `broker_symbol` or feed configuration values.

## Symbol helpers

`app/core/symbols.py` now sources XAU pip/digits from the effective instrument
context rather than hard-coding unit literals. Channel routing remains
XAU-parity for this PR.

## Cross-service runtime manifest

PR2 compiles effective instrument contexts into a secret-safe
`ResolvedRuntimeManifest` shared with cTrader. See
[cross-service-runtime-manifest.md](./cross-service-runtime-manifest.md).

ENV remains live-authoritative in PR2; the manifest is a shadow parity source.

## What remains for future multi-symbol runtime work

- Shared runtime manifest for Python and cTrader
- Migrate trading consumers to explicit instrument contexts
- Multi-symbol feed and execution routing
- One cTrader worker per instrument (if required)
- Telegram channel redesign for multi-symbol delivery
- Remove duplicated shared trading ENV only after cross-service cutover
- Activate additional live symbols only through explicit rollout and policy
  work outside this boundary

This PR intentionally does **not** activate XAG, EURUSD, BTCUSD, US30, or any
non-XAU live trading path.
