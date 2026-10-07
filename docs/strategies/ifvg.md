# iFVG Strategy V2

An `ifvg` setup uses a canonical fresh/touched inverse-FVG whose close-through
inversion is already established by the Zone domain. M5 supplies the zone,
reaction bar and opposing liquidity objective. Entry is the inverted gap,
invalidation lies beyond its far edge, and the nearest sufficiently distant
opposing liquidity is the target. Identity is the canonical iFVG ID. Quality
combines inversion strength and remaining freshness. It expires by its own
`expiry_hours`; invalidated/mitigated or weak zones reject. It is enabled in
the live Go opportunity stream; Algo Bot applies execution policy before any
TradePlan is published.

## Status and execution

- **Certification**: `LEGACY_PARITY_PROVEN` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Trades on FX. On XAU it is observe-only: it matches the Python publisher bar for bar but lost in both independent windows reviewed, so it is analysed and published without a TradePlan.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
