# Momentum Ride Strategy V2

`momentum_ride` requires a causal M5 sequence of directional displacement
bodies with limited overlap. Entry follows the last accepted impulse,
invalidation sits behind its origin and target uses available opposing
liquidity with a minimum distance. Identity anchors the impulse sequence.
Mixed direction, weak bodies, excessive overlap or missing room reject.
Rollout remains disabled.
