# Go behavioral completion

Validated on 2026-10-02 after deployment of `0abb965`, before this change.
Production was observed read-only; no service was restarted and no order was
placed.

## Arbitration finding

Go correctly remained the sole technical opportunity producer, but its
full-book arbitration result was incorrectly reused as execution arbitration.
Technically live opportunities can remain in the Go book for hours, including
broad Key Level bands and opportunities that Algo Bot later rejects for
freshness, quote access, or target room. Those entries could therefore veto a
fresh admitted setup and produce `go_arbitration_no_winner`.

Algo Bot now arbitrates only its admitted intent set. It ranks Go quality,
considers only currently executable intents for a direction conflict, uses a
unique Go-owned `with_bias` direction to resolve a close quality tie, and keeps
same-direction candidates as safety-gate fallback. Go arbitration remains
technical-book telemetry and thesis correlation; it is not an execution gate.

## Strategy evidence

- `box_breakout` and `scalp_breakout_retest` both have positive, failed-retest,
  and delayed-retest tests. Their three-bar retest windows are configuration
  backed and registry enabled.
- HTF context is ordered H1, H4, then causal M15 fallback. Engine and consumer
  tests prove M15 survives when H1/H4 are absent and is tagged as `go_M15`.
- The short post-deploy production window contained no new HTF-unavailable
  rejection, but that observation is not used as proof; deterministic tests are.

## Ownership cleanup

- The pandas auto-scalp detector is deleted. Only its neutral range lifecycle
  data contracts remain.
- Python no longer recalculates barrier displacement from OHLC. The Go barrier
  lifecycle is consumed directly.
- The dormant Go-winner execution selector is deleted. The legacy Python
  arbitration function is now the active execution-policy arbiter.
- Exact Fibonacci-level and Grade-A reclaimed-pool provenance cross Kafka.

## Retired decisions

`Fade Scalp` and non-scalp `Break & Retest` are historical/manual labels only.
Their automatic roles belong to the explicit Go strategies documented in the
canonical strategy catalog.
