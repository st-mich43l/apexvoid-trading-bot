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
  "session_level",
  "trendline",
  "range_edge",
  "box_breakout",
  "momentum_ride",
  "snap_back",
  "liquidity_sweep",
  "range_sweep",
  "impulse_pullback",
  "scalp_breakout_retest",
})
