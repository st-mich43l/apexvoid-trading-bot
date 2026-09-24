package zone

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// Config aggregates every Zone V2 tunable, one struct instead of
// scattering parameters through every call site — the same "coherent
// model" choice structure.Settings already makes. Values are read from
// config/analysis.yml's canonical structure/techniques/zone_relevance/
// flip_zone leaves (plus, for Displacement,
// the SAME analysis.structure.break.displacement_* leaves structure
// itself uses — one canonical displacement formula, not a second copy —
// see doc.go) by internal/engine; zone has no config-package dependency
// of its own.
type Config struct {
	Version string

	// Displacement/DisplacementMaxBars feed structure.DetectDisplacement
	// directly (see doc.go on why zone reuses this rather than
	// reimplementing it). DisplacementMaxBars mirrors
	// internal/engine/config.go's own StructureConfigFromConfig choice of
	// scanning the same horizon a break's own reclaim window uses —
	// Lifecycle.SweepReclaimBars, not a second independent value.
	Displacement structure.DisplacementConfig

	Lifecycle LifecycleConfig
	Relevance RelevanceConfig

	// FlipAcceptBars is how many consecutive closes beyond a broken level
	// are required before the break is "accepted" and a flip zone forms —
	// zones.py::flip_zones' accept_bars.
	FlipAcceptBars int

	// FlipBandBodyFraction widens a flip zone's band by this fraction of
	// the break candle's body when the level-derived band alone is
	// narrower — zones.py::flip_zones' band_body_fraction.
	FlipBandBodyFraction float64

	// FlipLevelBandATR is the ATR-tolerance floor for a flip zone's band
	// before the body-fraction widening above — the same "widen a point
	// into a band via ATR tolerance" pattern liquidity.PoolFromSwing's
	// own toleranceATR already uses, since this package has no separate
	// key-level primitive with its own band field yet (see flip.go).
	FlipLevelBandATR float64

	// OrderBlockBodyFraction is the minimum body/range ratio a
	// confirming break's candle needs — mirrors
	// technique_geometry.py::validate_technique_instance's OB-only
	// momentum_body_frac gate.
	OrderBlockBodyFraction float64
}

func (c Config) displacementMaxBars() int {
	return c.Lifecycle.SweepReclaimBars
}

// ZoneState is the complete zone read for one symbol+timeframe at one
// point in time — the zone analogue of structure.StructureState/
// liquidity.LiquidityState. Zones is a flat slice across all seven
// Kinds; callers filter by Kind/Side as needed (mirrors how
// liquidity.LiquidityState.Pools is one flat slice, not split per
// source).
type ZoneState struct {
	Zones []Zone
}

// Book is the zone-domain slice of a symbol's canonical state — same
// shape as structure.Book/liquidity.Book.
type Book struct {
	ByTimeframe map[market.Timeframe]ZoneState
}

// NewBook returns an empty Book ready for Set/Get.
func NewBook() *Book {
	return &Book{ByTimeframe: make(map[market.Timeframe]ZoneState)}
}

// Set stores tf's latest ZoneState, replacing whatever was there.
func (b *Book) Set(tf market.Timeframe, state ZoneState) {
	b.ByTimeframe[tf] = state
}

// Get returns tf's latest ZoneState, or the zero value and false if none
// has been computed yet.
func (b *Book) Get(tf market.Timeframe) (ZoneState, bool) {
	state, ok := b.ByTimeframe[tf]
	return state, ok
}

// Update is Zone V2's single entrypoint: detect displacement runs once,
// then dispatch to every kind builder in dependency order (Supply/Demand
// need only displacement; Order Block additionally needs breaks; Breaker
// needs the just-built Order Blocks; FVG needs only candles; iFVG needs
// the just-built FVGs; Flip needs breaks+swings). Pure and causal, same
// contract as structure.Update/liquidity.Update: only ever reads
// candles/atrSeries/swings/breaks given to it.
//
// swings/breaks should be structure.StructureState.Swings/.Breaks from
// the SAME candles/atrSeries pass — zone never recomputes structure
// itself (source task §2's core redesign principle, restated at this
// boundary exactly as liquidity.Update's own doc comment restates it).
func Update(candles []market.Candle, atrSeries []float64, swings []structure.Swing, breaks []structure.StructureBreak, tf market.Timeframe, cfg Config) ZoneState {
	if len(candles) == 0 {
		return ZoneState{}
	}
	currentATR := lastValue(atrSeries)
	runs := detectDisplacementRuns(candles, atrSeries, cfg.displacementMaxBars(), cfg.Displacement)

	var zones []Zone
	zones = append(zones, buildSupplyZones(candles, runs, tf, currentATR, cfg)...)
	zones = append(zones, buildDemandZones(candles, runs, tf, currentATR, cfg)...)

	obZones := buildOrderBlockZones(candles, runs, breaks, tf, currentATR, cfg)
	zones = append(zones, obZones...)
	zones = append(zones, buildBreakerZones(candles, obZones, tf, currentATR, cfg)...)

	fvgZones := buildFVGZones(candles, tf, currentATR, cfg)
	zones = append(zones, fvgZones...)
	zones = append(zones, buildIFVGZones(candles, fvgZones, tf, currentATR, cfg)...)

	zones = append(zones, buildFlipZones(candles, swings, breaks, tf, currentATR, cfg)...)

	for i := range zones {
		touchFrom := indexAtOrAfter(candles, zones[i].CreatedAt)
		if touchFrom < 0 {
			continue
		}
		touches, lastTouchAt := countTouches(candles, touchFrom, zones[i])
		zones[i].TouchCount = touches
		if lastTouchAt != nil {
			zones[i].LastTouchedAt = lastTouchAt
		}
		lifecycleFrom := zones[i].BreakIndex + 1
		if lifecycleFrom < 0 {
			lifecycleFrom = touchFrom
		}
		zones[i].State = DeriveState(candles, lifecycleFrom, zones[i], touches, currentATR, cfg.Lifecycle)
		zones[i].Strength = applyLifecycleStrength(zones[i], candles, zones[i].State)
		zones[i].Relevance = ClassifyRelevance(zones[i], float64(candles[len(candles)-1].Close), currentATR, cfg.Relevance)
	}

	return ZoneState{Zones: zones}
}

func lastValue(series []float64) float64 {
	if len(series) == 0 {
		return 0
	}
	return series[len(series)-1]
}

// indexAtOrAfter mirrors structure/liquidity's own unexported helper of
// the same name — small enough (binary search over a Time-ascending
// slice) that duplicating it here is clearer than exporting internal
// plumbing from either package just for this one call site.
func indexAtOrAfter(candles []market.Candle, t int64) int {
	lo, hi := 0, len(candles)
	for lo < hi {
		mid := (lo + hi) / 2
		if candles[mid].Time < t {
			lo = mid + 1
		} else {
			hi = mid
		}
	}
	if lo == len(candles) {
		return -1
	}
	return lo
}

// countTouches is zones.py::mark_mitigation's touch-counting rule: a
// touch is a RISING EDGE of "price traded inside [low,high]" — a
// multi-bar dwell inside the zone counts as one touch, not one per bar.
// Scanning starts at BreakIndex+1 (see Zone.BreakIndex's own doc comment
// on why: never the zone's own formation/confirmation bar).
func countTouches(candles []market.Candle, from int, z Zone) (int, *int64) {
	start := from
	if z.BreakIndex+1 > start {
		start = z.BreakIndex + 1
	}
	if start >= len(candles) {
		return 0, nil
	}
	touches := 0
	wasInside := false
	var lastTouchAt *int64
	low, high := float64(z.Low), float64(z.High)
	for i := start; i < len(candles); i++ {
		c := candles[i]
		inside := c.Low <= high && c.High >= low
		if inside && !wasInside {
			touches++
			t := c.Time
			lastTouchAt = &t
		} else if inside {
			t := c.Time
			lastTouchAt = &t
		}
		wasInside = inside
	}
	return touches, lastTouchAt
}
