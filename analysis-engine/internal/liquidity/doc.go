// Package liquidity produces objective market facts about liquidity: pools,
// equal highs/lows, sweeps, grabs, session-relative liquidity, and a
// queryable map of where liquidity sits. "A liquidity pool exists," "it
// was swept," "it was reclaimed" — never whether those facts form a trade
// (that is internal/strategy's job).
//
// Ports (docs/go-analysis-migration-audit.md computation table):
// liquidity.liquidity_pools/liquidity_grabs -> pool.go/grab.go;
// structure.equal_highs_lows (a duplicate wrapper to remove, not port,
// per the audit's §2.3) has no Go equivalent needed beyond this package's
// own equal_high_low.go. Not yet implemented — boundary reservation only.
//
// Dependency rule: liquidity MUST NOT import internal/opportunity or
// internal/strategy.
package liquidity
