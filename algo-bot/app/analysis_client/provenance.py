"""Stable provenance tags for automatic analysis decisions.

These tags describe where a match came from. They are deliberately separate
from the retired ``authority.py`` scope-grant state machine: a Go-origin tag
is data provenance, not an operator approval gate.
"""

GO_ORIGIN_TAG = "authority:go"
CATALOG_TAG = "catalog:"
EPOCH_TAG = "authority_epoch:"
