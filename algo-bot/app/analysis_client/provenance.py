"""Stable provenance and catalog identity for Go-owned opportunities."""

GO_ORIGIN_TAG = "origin:go"
CATALOG_TAG = "catalog:"

# Keep this registry beside the event provenance contract. It is the producer
# Every enabled Go strategy must have an explicit adapter in
# ``go_opportunity_policy``; catalog completeness is checked at import time.
CATALOG_STRATEGY_IDS = frozenset({
  "key_level",
  "confluence_zone",
  "supply",
  "demand",
  "order_block",
  "fvg",
  "ifvg",
  "crt",
  "flip_zone",
  "trendline",
  "break_retest",
  "fade_scalp",
  "impulse_pullback",
})

# Evidence code the Go engine appends when a legacy-family invalidation was set by the extreme of the reaction's own bars and not by the structure
# (analysis-engine/internal/strategyutil/reaction_stop.go). The adapter requires only the strategy's own prefix, so the extra code is accepted.
REACTION_EXTREME_EVIDENCE = "invalidation_beyond_reaction_extreme"
