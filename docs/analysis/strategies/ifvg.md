# iFVG Strategy V2

An `ifvg` setup uses a canonical fresh/touched inverse-FVG whose close-through
inversion is already established by the Zone domain. M5 supplies the zone,
reaction bar and opposing liquidity objective. Entry is the inverted gap,
invalidation lies beyond its far edge, and the nearest sufficiently distant
opposing liquidity is the target. Identity is the canonical iFVG ID. Quality
combines inversion strength and remaining freshness. It expires by its own
`expiry_hours`; invalidated/mitigated or weak zones reject. Rollout remains
disabled pending S11C shadow acceptance.
