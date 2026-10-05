# Momentum Ride Strategy V2

`momentum_ride` runs on the engine's detector-contract frame. It requires a
non-chop regime, an allowing premium/discount location and a directional
strong-body close (`minimum_body_fraction`) through the latest opposite swing
on the current bar. The optional velocity/acceleration gate
(`require_momentum_va`) is off in the frozen production configuration. It
prefers the highest-scored entry-valid zone and falls back to the nearest
valid-side key level, then applies the shared qualification (entry distance,
valid-side level, confluence floor). The thesis is the break itself: there is no
shared reaction confirmation. The target is the nearest opposing pool at least
`minimum_target_distance_atr` away, otherwise that distance beyond the entry.
Identity anchors both the broken swing and the selected location source.
