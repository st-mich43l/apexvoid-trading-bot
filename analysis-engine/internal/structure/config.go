package structure

// Settings aggregates every Structure V2 tunable, one struct instead of
// scattering PivotConfig/PromotionConfig/BreakConfig/tolerance as four
// separate parameters through every call site — source task §69/§70
// ("only expose meaningful tunable parameters... prefer a coherent
// model"). Values are read from config/analysis.yml's analysis.structure
// section by internal/engine (structure has no config-package dependency
// of its own — see docs/architecture/dependency-rules.md).
type Settings struct {
	Version string // "v2" — see internal/engine's version-gate wiring; structure itself does not branch on this

	PivotLeftBars  int
	PivotRightBars int

	Promotion         PromotionConfig
	EqualToleranceATR float64
	Break             BreakConfig
}
