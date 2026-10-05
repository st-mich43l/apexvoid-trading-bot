# Snap-Back Strategy V2

`snap_back` is a structural reversal after M5 price extends by
`extension_atr` x ATR (the frozen production value is 1.5) from the latest
impulse swing, or from the zone when `extension_source` is `zone`. It runs on
the engine's detector-contract frame.

It selects the highest-scored entry-valid zone of the trade side (ties by
distance, then the lower band), clipped to its proximal band when wider than the
configured zone-width ATR, and falls back to the nearest valid-side key level
with a compat entry zone. It then requires the strict premium/discount location,
a graded A/B liquidity grab whose pool points into the zone, and the shared
closed-bar structural reaction, and finally the shared qualification (entry
distance, valid-side level, confluence floor). The candidate carries the zone
identity, grab grade, reaction type and timestamps and the detector's
confluence stars.
