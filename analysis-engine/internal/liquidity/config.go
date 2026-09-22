package liquidity

// Config mirrors config/analysis.yml's analysis.liquidity.* leaves.
type Config struct {
	Version string // "v1" — see internal/engine's version-gate wiring

	EqualLevelToleranceATR float64
	PoolMinimumTouches     int
	SweepReclaimBars       int
}
