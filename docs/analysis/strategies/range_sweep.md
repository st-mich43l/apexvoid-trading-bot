# Range Sweep Strategy V2

`range_sweep` requires canonical M5 range state, then an M1 excursion beyond
one established edge by the configured ATR distance and a close back inside.
Entry is the reclaimed edge band, invalidation is beyond the M1 sweep and the
opposite range edge is the target. Identity anchors the M5 range and side.
No sweep/reclaim or non-range structure rejects. Rollout remains disabled.
