# Canonical Zone Domain V2

The Analysis Engine owns one canonical technical-zone model in
`analysis-engine/internal/zone`. Strategies consume these facts; they do not
rebuild supply/demand, order blocks, gaps, breakers, or flips locally.

## Zone contract

Each `Zone` is a per-origin record. Overlapping records are not merged in this
domain because merging would erase provenance and mix lifecycle history. A
record carries its kind, demand/supply side, price band, timeframe, origin and
creation timestamps, structural and displacement references, strength, touch
history, and the current lifecycle/relevance classifications.

The domain currently emits these independent primitives:

- Supply and demand from qualifying displacement runs.
- Order blocks from a displacement run with a confirming BOS or CHoCH.
- Fair value gaps from the two-bar-apart high/low gap.
- Inverted FVGs only after a close through the original gap boundary.
- Breakers from a closing-price violation of an order block.
- Flip zones from an accepted structural break with a band anchored on the
  broken level.

Detection is causal: `Update` receives the candle/ATR window and the already
computed structure swings/breaks for the same window. It does not recompute
structure, import strategy packages, or decide whether a primitive is a trade.

## Lifecycle and relevance

Lifecycle is structural validity, derived on every update:

| State | Meaning |
| --- | --- |
| `fresh` | No rising-edge touch has occurred. |
| `touched` | One touch; the zone remains structurally valid. |
| `partially_mitigated` | Multiple touches, still valid and below the retest budget. |
| `mitigated` | Retest budget exhausted while the structural barrier still holds. |
| `invalidated` | An unreclaimed break or excessive break episodes invalidated it. |

Relevance is independent of lifecycle and is recalculated from current price
and ATR. A valid historical zone can therefore be `remote` or `dormant`
without being deleted, and can become `immediate` again when price returns:
`immediate`, `nearby`, `remote`, and `dormant`.

The hold-based invalidation rules preserve reclaimed sweeps. A close beyond the
far edge opens a break episode; reclaim within the configured window forgives
that episode, while an unreclaimed break or an exhausted episode budget makes
the zone invalid. Wick-only violations do not invalidate a zone.

## Configuration authority

`config/analysis.yml` is the runtime authority for the versioned `v1` zone
contract. `analysis-engine/internal/engine/config.go` maps it into
`zone.Config`, reusing the canonical structure displacement thresholds and
equal-level tolerance. The zone package remains independent of the config
package. Stage C2 parity and the generated V3 schema pin every newly surfaced
leaf.

## Ownership boundaries

- `internal/zone`: geometry, provenance, lifecycle, and relevance.
- `internal/structure`: swings, BOS/CHoCH, and displacement primitives.
- `internal/context`: combines the per-timeframe zone state and exposes the
  primary-timeframe convenience view.
- Strategies: location/trigger/entry/invalidation/target decisions from the
  canonical facts; no duplicate detector or generic reaction family.
- Presentation/confluence: may merge or score records for a consumer, but
  must preserve the original zone IDs and never mutate canonical state.

`zone.Book` is stored on `state.SymbolState`, updated only for the timeframe
whose bar closed, copied into immutable `AnalysisSnapshot`, and included in
`MarketContext.Timeframes` and its primary `Zones` view.
