# Impulse Pullback Strategy V2

`impulse_pullback` qualifies a directional M5 impulse, then requires an M1
correction within configured retracement bounds and a continuation close.
Entry is the continuation band, invalidation is behind the pullback extreme and
target is configured R. Identity anchors the M5 impulse. Too-small impulses,
shallow/deep corrections and absent continuation reject. Rollout remains
disabled.
