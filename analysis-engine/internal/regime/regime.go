// Package regime ports the Python consolidation/regime helpers without
// making regime a second structure engine. It consumes the canonical Go
// candles, ATR series, promoted swings and dealing range already assembled by
// the engine.
package regime

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

const (
	DisplacementBodyFraction = 0.6
	DisplacementRangeATR     = 1.0
)

// Config mirrors the regime and breakout leaves consumed by the Python
// engine. Defaults are intentionally not applied here: configuration is
// resolved by the composition root and passed in explicitly.
type Config struct {
	ChopFilterEnabled    bool
	ChopLookback         int
	ChopRangeATR         float64
	CoilContract         float64
	DirectionEnabled     bool
	DirectionLookback    int
	MinDirectionalSwings int
	MinDisplacementATR   float64
	EqualToleranceATR    float64
	BreakoutBufferATR    float64
	BreakoutAcceptBars   int
}

// State is the engine-owned regime result. LegacyKind/NewKind are retained
// because the Python migration logged both the legacy range classifier and
// the directional counterfactual during the transition.
type State struct {
	Kind              string
	RangeHigh         float64
	RangeLow          float64
	HeightATR         float64
	Reasons           []string
	Coiling           bool
	LegacyKind        string
	NewKind           string
	DirectionalDetail string
	BoxBreak          *BoxBreak
}

// BoxBreak is the accepted consolidation break returned by the Python
// accepted_box_break helper.
type BoxBreak struct {
	BoxHigh    float64
	BoxLow     float64
	Direction  string
	AcceptBar  int
	Coiling    bool
	Acceptance string
}

// Classify is the pure regime entrypoint. The range is the canonical fib
// dealing range; no raw swing or ATR recomputation occurs here.
func Classify(candles []market.Candle, atrSeries []float64, swings []structure.Swing, structureKind string, rangeHigh, rangeLow float64, hasRange bool, cfg Config) State {
	close := lastClose(candles)
	coiling := isCoiling(candles, cfg.ChopLookback, cfg.CoilContract)
	if !cfg.ChopFilterEnabled {
		return State{Kind: "trend", RangeHigh: close, RangeLow: close, HeightATR: math.Inf(1), Reasons: []string{"chop filter disabled"}, Coiling: coiling, LegacyKind: "trend", NewKind: "trend"}
	}
	if !hasRange {
		return State{Kind: "trend", RangeHigh: close, RangeLow: close, HeightATR: math.Inf(1), Reasons: []string{"no dealing range"}, Coiling: coiling, LegacyKind: "trend", NewKind: "trend"}
	}
	if rangeHigh <= rangeLow {
		return State{Kind: "trend", RangeHigh: close, RangeLow: close, HeightATR: math.Inf(1), Reasons: []string{"invalid dealing range"}, Coiling: coiling, LegacyKind: "trend", NewKind: "trend"}
	}
	atr := lastATR(atrSeries)
	height := math.Max(0, rangeHigh-rangeLow)
	heightATR := math.Inf(1)
	if atr > 0 {
		heightATR = height / atr
	}
	reasons := make([]string, 0, 2)
	if heightATR < math.Max(0, cfg.ChopRangeATR) {
		reasons = append(reasons, fmt.Sprintf("range height %.2f ATR < %.2f", heightATR, cfg.ChopRangeATR))
	}
	if structureKind == "range" && closesInsideRange(candles, rangeLow, rangeHigh, cfg.ChopLookback) {
		reasons = append(reasons, fmt.Sprintf("range structure held %d bars", max1(cfg.ChopLookback)))
	}
	legacyKind := "trend"
	if len(reasons) > 0 {
		legacyKind = "chop"
	}
	newKind := legacyKind
	directionalDetail := ""
	if override, ok := directionalOverride(candles, swings, atr, cfg); ok {
		pairCount, label, net, lookback := override.pairCount, override.label, override.netDisplacement, override.lookback
		directionalDetail = fmt.Sprintf("%d %s, net %.1f ATR", pairCount, label, net)
		overrideReasons := []string{fmt.Sprintf("trend (directional override): %d consecutive %s, net %.1f ATR over %d bars", pairCount, label, net, lookback)}
		if legacyKind == "chop" && len(reasons) > 0 {
			overrideReasons = append(overrideReasons, "  ["+reasons[0]+" would have said chop]")
		}
		newKind = "trend"
		if cfg.DirectionEnabled {
			reasons = overrideReasons
		}
	}
	if len(reasons) == 0 {
		reasons = []string{"range expanded or broke edge"}
	}
	state := State{Kind: legacyKind, RangeHigh: rangeHigh, RangeLow: rangeLow, HeightATR: heightATR, Reasons: reasons, Coiling: coiling, LegacyKind: legacyKind, NewKind: newKind, DirectionalDetail: directionalDetail}
	if cfg.DirectionEnabled && newKind == "trend" {
		state.Kind = "trend"
	}
	state.BoxBreak = AcceptedBoxBreak(candles, atrSeries, rangeHigh, rangeLow, coiling, cfg.BreakoutBufferATR, cfg.BreakoutAcceptBars)
	return state
}

// AcceptedBoxBreak ports regime.accepted_box_break. The latest qualifying
// break wins, and a displacement on the first close is accepted immediately.
func AcceptedBoxBreak(candles []market.Candle, atrSeries []float64, boxHigh, boxLow float64, coiling bool, bufferATR float64, acceptBars int) *BoxBreak {
	if len(candles) == 0 || boxHigh <= boxLow {
		return nil
	}
	acceptBars = max1(acceptBars)
	bufferATR = math.Max(0, bufferATR)
	upHolds, downHolds := 0, 0
	var accepted *BoxBreak
	for i, candle := range candles {
		atr := atrAt(atrSeries, i)
		buffer := bufferATR * atr
		above := candle.Close > boxHigh+buffer
		below := candle.Close < boxLow-buffer
		if above {
			upHolds++
		} else {
			upHolds = 0
		}
		if below {
			downHolds++
		} else {
			downHolds = 0
		}
		if upHolds == 1 && DisplacementGrade(candle, atr, "up") {
			accepted = &BoxBreak{BoxHigh: boxHigh, BoxLow: boxLow, Direction: "up", AcceptBar: i, Coiling: coiling, Acceptance: "displacement"}
		} else if upHolds == acceptBars {
			accepted = &BoxBreak{BoxHigh: boxHigh, BoxLow: boxLow, Direction: "up", AcceptBar: i, Coiling: coiling, Acceptance: fmt.Sprintf("%d closes", acceptBars)}
		}
		if downHolds == 1 && DisplacementGrade(candle, atr, "down") {
			accepted = &BoxBreak{BoxHigh: boxHigh, BoxLow: boxLow, Direction: "down", AcceptBar: i, Coiling: coiling, Acceptance: "displacement"}
		} else if downHolds == acceptBars {
			accepted = &BoxBreak{BoxHigh: boxHigh, BoxLow: boxLow, Direction: "down", AcceptBar: i, Coiling: coiling, Acceptance: fmt.Sprintf("%d closes", acceptBars)}
		}
	}
	return accepted
}

// DisplacementGrade ports regime.displacement_grade exactly.
func DisplacementGrade(candle market.Candle, atr float64, direction string) bool {
	rangeSize := candle.Range()
	if rangeSize <= 0 || atr <= 0 {
		return false
	}
	directional := (direction == "up" && candle.Close > candle.Open) || (direction != "up" && candle.Close < candle.Open)
	return directional && candle.Body() >= DisplacementBodyFraction*rangeSize && rangeSize >= DisplacementRangeATR*atr
}

type directionalResult struct {
	pairCount       int
	label           string
	netDisplacement float64
	lookback        int
}

func directionalOverride(candles []market.Candle, swings []structure.Swing, atr float64, cfg Config) (directionalResult, bool) {
	if len(candles) == 0 || atr <= 0 {
		return directionalResult{}, false
	}
	lookback := max1(cfg.DirectionLookback)
	start := len(candles) - lookback
	if start < 0 {
		start = 0
	}
	window := candles[start:]
	labels := swingLabels(swings, atr, cfg.EqualToleranceATR)
	pairs := make([]int, 0)
	ordered := make([]string, 0)
	for i, swing := range swings {
		if swing.Time >= candles[start].Time && swing.Time <= candles[len(candles)-1].Time {
			ordered = append(ordered, labels[i])
		}
	}
	for i := 0; i+1 < len(ordered); {
		first, second := ordered[i], ordered[i+1]
		if (first == "LH" && second == "LL") || (first == "LL" && second == "LH") {
			pairs = append(pairs, -1)
			i += 2
		} else if (first == "HH" && second == "HL") || (first == "HL" && second == "HH") {
			pairs = append(pairs, 1)
			i += 2
		} else {
			i++
		}
	}
	bullish, bearish := 0, 0
	for _, pair := range pairs {
		if pair > 0 {
			bullish++
		} else {
			bearish++
		}
	}
	net := (window[len(window)-1].Close - window[0].Close) / atr
	minPairs, minDisp := max1(cfg.MinDirectionalSwings), math.Max(0, cfg.MinDisplacementATR)
	if bearish >= minPairs && bullish <= 1 && net <= -minDisp {
		return directionalResult{bearish, "LH/LL", net, lookback}, true
	}
	if bullish >= minPairs && bearish <= 1 && net >= minDisp {
		return directionalResult{bullish, "HH/HL", net, lookback}, true
	}
	return directionalResult{}, false
}

func swingLabels(swings []structure.Swing, atr, equalToleranceATR float64) []string {
	labels := make([]string, len(swings))
	var lastHigh, lastLow *structure.Swing
	for i := range swings {
		swing := swings[i]
		if swing.Kind == structure.SwingHigh {
			if lastHigh != nil {
				labels[i] = structure.ClassifySwingRelation(swing, *lastHigh, atr, equalToleranceATR).String()
			}
			copy := swing
			lastHigh = &copy
		} else {
			if lastLow != nil {
				labels[i] = structure.ClassifySwingRelation(swing, *lastLow, atr, equalToleranceATR).String()
			}
			copy := swing
			lastLow = &copy
		}
	}
	return labels
}

func isCoiling(candles []market.Candle, lookback int, contract float64) bool {
	required := max2(lookback)
	if len(candles) < required {
		return false
	}
	window := candles[len(candles)-required:]
	split := len(window) / 2
	first, second := window[:split], window[split:]
	firstRange, secondRange := envelope(first), envelope(second)
	return firstRange > 0 && secondRange < math.Max(0, contract)*firstRange
}

func closesInsideRange(candles []market.Candle, low, high float64, lookback int) bool {
	required := max1(lookback)
	if len(candles) < required {
		return false
	}
	for _, candle := range candles[len(candles)-required:] {
		if candle.Close < low || candle.Close > high {
			return false
		}
	}
	return true
}

func envelope(candles []market.Candle) float64 {
	if len(candles) == 0 {
		return 0
	}
	low, high := candles[0].Low, candles[0].High
	for _, candle := range candles[1:] {
		low = math.Min(low, candle.Low)
		high = math.Max(high, candle.High)
	}
	return high - low
}

func atrAt(series []float64, index int) float64 {
	if index < 0 || index >= len(series) || !isFinite(series[index]) {
		return 0
	}
	return series[index]
}
func lastATR(series []float64) float64 {
	for i := len(series) - 1; i >= 0; i-- {
		if isFinite(series[i]) && series[i] > 0 {
			return series[i]
		}
	}
	return 0
}
func lastClose(candles []market.Candle) float64 {
	if len(candles) == 0 || !isFinite(candles[len(candles)-1].Close) {
		return 0
	}
	return candles[len(candles)-1].Close
}
func isFinite(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }
func max1(v int) int {
	if v < 1 {
		return 1
	}
	return v
}
func max2(v int) int {
	if v < 2 {
		return 2
	}
	return v
}
