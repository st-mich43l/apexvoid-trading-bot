package scalpbreakoutretest

import (
	"math"
	"sort"
	"strconv"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// microSwing is one closed-bar swing of the setup window (the frozen
// build_micro_structure swing, not the layered structure engine's swing).
type microSwing struct {
	Kind  string // "high" | "low"
	Price float64
	Time  int64
	Index int
}

type microStructure struct {
	Swings     []microSwing
	EqualHighs []float64
	EqualLows  []float64
}

// buildMicroStructure mirrors microstructure.build_micro_structure: a swing is
// a bar whose high (low) is at least (at most) every neighbour within
// `lookback` bars on each side, so a tied extreme yields more than one swing.
// Equal highs/lows are the rounded mean of every pair of same-side swings
// within `equalTol` of each other, de-duplicated and sorted.
func buildMicroStructure(bars []market.Candle, lookback int, equalTol float64, digits int) microStructure {
	var out microStructure
	if len(bars) == 0 {
		return out
	}
	lb := lookback
	if lb < 1 {
		lb = 1
	}
	for i := lb; i < len(bars)-lb; i++ {
		hi, lo := bars[i-lb].High, bars[i-lb].Low
		for j := i - lb; j <= i+lb; j++ {
			hi = math.Max(hi, bars[j].High)
			lo = math.Min(lo, bars[j].Low)
		}
		if bars[i].High >= hi {
			out.Swings = append(out.Swings, microSwing{Kind: "high", Price: bars[i].High, Time: bars[i].Time, Index: i})
		}
		if bars[i].Low <= lo {
			out.Swings = append(out.Swings, microSwing{Kind: "low", Price: bars[i].Low, Time: bars[i].Time, Index: i})
		}
	}
	var highs, lows []microSwing
	for _, s := range out.Swings {
		if s.Kind == "high" {
			highs = append(highs, s)
		} else {
			lows = append(lows, s)
		}
	}
	out.EqualHighs = equalLevels(highs, equalTol, digits)
	out.EqualLows = equalLevels(lows, equalTol, digits)
	return out
}

func equalLevels(swings []microSwing, tol float64, digits int) []float64 {
	seen := map[float64]bool{}
	var out []float64
	for i, first := range swings {
		for _, second := range swings[i+1:] {
			if math.Abs(first.Price-second.Price) <= tol {
				v := pyRound((first.Price+second.Price)/2.0, digits)
				if !seen[v] {
					seen[v] = true
					out = append(out, v)
				}
			}
		}
	}
	sort.Float64s(out)
	return out
}

// pyRound is Python's round(x, digits): the correctly rounded decimal of the
// exact binary value, ties to even.
func pyRound(x float64, digits int) float64 {
	if digits < 0 {
		digits = 0
	}
	v, err := strconv.ParseFloat(strconv.FormatFloat(x, 'f', digits, 64), 64)
	if err != nil {
		return x
	}
	return v
}

// structureFlipCandidates mirrors microstructure.structure_flip_candidates:
// the setup window's own swing highs (BUY) / lows (SELL), newest first, aged
// between minAge and maxAge bars and spaced at least `spacing` apart.
func structureFlipCandidates(m microStructure, side market.Direction, currentIndex int, timeframe string, minAge, maxAge int, atr, minSpacingATR float64) []levelCandidate {
	kind, source := "high", sourceM1SwingHigh
	if side == market.Sell {
		kind, source = "low", sourceM1SwingLow
	}
	spacing := math.Max(0, minSpacingATR) * math.Max(0, atr)
	var swings []microSwing
	for _, s := range m.Swings {
		if s.Kind == kind {
			swings = append(swings, s)
		}
	}
	sort.SliceStable(swings, func(i, j int) bool { return swings[i].Index > swings[j].Index })
	var kept []microSwing
	for _, s := range swings {
		age := currentIndex - s.Index
		if age < maxInt(0, minAge) || age > maxInt(0, maxAge) {
			continue
		}
		near := false
		if spacing > 0 {
			for _, k := range kept {
				if math.Abs(s.Price-k.Price) < spacing {
					near = true
					break
				}
			}
		}
		if near {
			continue
		}
		kept = append(kept, s)
	}
	out := make([]levelCandidate, 0, len(kept))
	for _, s := range kept {
		index := s.Index
		out = append(out, levelCandidate{Level: s.Price, Side: side, Source: source, Subtype: subtypeStructureFlip, Timeframe: timeframe, SourceIndex: &index})
	}
	return out
}

// keyLevel is one M5 key level as the frozen scalp context carries it.
type keyLevel struct {
	Price   float64
	Touches int
}

// m5StructureFlipCandidates mirrors microstructure.m5_structure_flip_candidates.
func m5StructureFlipCandidates(levels []keyLevel, side market.Direction, currentPrice float64, minTouches int) []levelCandidate {
	source := sourceM5SwingHigh
	if side == market.Sell {
		source = sourceM5SwingLow
	}
	var out []levelCandidate
	for _, l := range levels {
		if l.Touches < maxInt(1, minTouches) {
			continue
		}
		if side == market.Buy && l.Price <= currentPrice || side == market.Sell && l.Price >= currentPrice {
			continue
		}
		out = append(out, levelCandidate{Level: l.Price, Side: side, Source: source, Subtype: subtypeStructureFlip, Timeframe: "M5"})
	}
	return out
}

// liquidityLevelCandidates mirrors microstructure.liquidity_level_candidates.
func liquidityLevelCandidates(m microStructure, side market.Direction, timeframe string) []levelCandidate {
	prices, source := m.EqualHighs, sourceEQH
	if side == market.Sell {
		prices, source = m.EqualLows, sourceEQL
	}
	out := make([]levelCandidate, 0, len(prices))
	for _, p := range prices {
		out = append(out, levelCandidate{Level: p, Side: side, Source: source, Subtype: subtypeLiquidityLevelBreak, Timeframe: timeframe})
	}
	return out
}

// compressionBox is a recent tight, multi-touch setup-window range.
type compressionBox struct {
	Low, High    float64
	Bars         int
	EndIndex     int
	CompressionA float64
	TouchCount   int
}

// findCompressionBox mirrors microstructure.find_compression_box: the most
// recent window of minBars..maxBars bars that is no wider than boxMaxATR·ATR
// and whose high and low are each touched minTouches times, leaving at least
// one bar after it for a break.
func findCompressionBox(bars []market.Candle, atr float64, minBars, maxBars int, boxMaxATR float64, minTouches int, touchTolATR float64) *compressionBox {
	if atr <= 0 {
		return nil
	}
	minB := maxInt(3, minBars)
	maxB := maxInt(minB, maxBars)
	if len(bars) < minB+2 {
		return nil
	}
	tol := math.Max(0, touchTolATR) * atr
	maxWidth := math.Max(0, boxMaxATR) * atr
	minT := maxInt(1, minTouches)
	for end := len(bars) - 2; end >= minB-1; end-- {
		for width := minB; width <= minInt(maxB, end+1); width++ {
			start := end - width + 1
			if start < 0 {
				continue
			}
			window := bars[start : end+1]
			boxHigh, boxLow := window[0].High, window[0].Low
			for _, b := range window[1:] {
				boxHigh = math.Max(boxHigh, b.High)
				boxLow = math.Min(boxLow, b.Low)
			}
			span := boxHigh - boxLow
			if span <= 0 || span > maxWidth {
				continue
			}
			hi, lo := 0, 0
			for _, b := range window {
				if b.High >= boxHigh-tol {
					hi++
				}
				if b.Low <= boxLow+tol {
					lo++
				}
			}
			if hi < minT || lo < minT {
				continue
			}
			return &compressionBox{Low: boxLow, High: boxHigh, Bars: width, EndIndex: end, CompressionA: span / atr, TouchCount: hi + lo}
		}
	}
	return nil
}

type boxEpisode struct {
	Armed                   bool
	Level                   float64
	Close                   float64
	BarTime                 int64
	BreakDisplacement       float64
	RetestRejectionRequired bool
}

// detectBoxBreakRetest mirrors microstructure.detect_breakout_retest for a
// compression box: first qualifying breakout bar after the box (a real close
// beyond the edge by at least minDisplacement, in the trade direction), no
// close through the opposite side afterwards, at least one post-break close
// that held beyond the edge, a rejection (or plain touch) retest within the
// retest lookback, and a final close still holding.
func detectBoxBreakRetest(bars []market.Candle, side market.Direction, boxHigh, boxLow, minDisplacement float64, retestLookback int, requireRejection bool, boxEndIndex int) boxEpisode {
	n := len(bars)
	none := boxEpisode{}
	if n < 5 || boxHigh <= boxLow {
		return none
	}
	retestLB := maxInt(1, retestLookback)
	breakLB := maxInt(8, retestLB+4)
	breakLB = minInt(breakLB, maxInt(3, n-1))
	level := boxHigh
	if side == market.Sell {
		level = boxLow
	}
	minDisp := math.Max(0, minDisplacement)

	acceptedAt := -1
	for i := n - breakLB; i < n; i++ {
		if i < 1 || i <= boxEndIndex {
			continue
		}
		bar := bars[i]
		if side == market.Buy {
			if bar.Close > boxHigh && bar.Close-boxHigh >= minDisp && bar.Close > bar.Open {
				acceptedAt = i
				break
			}
		} else if bar.Close < boxLow && boxLow-bar.Close >= minDisp && bar.Close < bar.Open {
			acceptedAt = i
			break
		}
	}
	if acceptedAt < 0 {
		return none
	}
	for i := acceptedAt + 1; i < n; i++ {
		if side == market.Buy && bars[i].Close < boxLow || side == market.Sell && bars[i].Close > boxHigh {
			return none
		}
	}
	held := false
	for i := acceptedAt + 1; i < n; i++ {
		if side == market.Buy && bars[i].Close >= boxHigh || side == market.Sell && bars[i].Close <= boxLow {
			held = true
			break
		}
	}
	if !held {
		return none
	}
	retestAt := -1
	window := maxInt(1, minInt(retestLB, n-acceptedAt-1))
	for offset := 1; offset <= window; offset++ {
		idx := n - offset
		if idx <= acceptedAt {
			continue
		}
		bar := bars[idx]
		if requireRejection {
			rejected := side == market.Buy && bar.Low <= level && bar.Close > level || side == market.Sell && bar.High >= level && bar.Close < level
			if rejected {
				retestAt = idx
				break
			}
		} else if side == market.Buy && bar.Low <= level || side == market.Sell && bar.High >= level {
			retestAt = idx
			break
		}
	}
	if retestAt < 0 {
		return none
	}
	last := bars[n-1]
	if side == market.Buy && last.Close < boxHigh || side == market.Sell && last.Close > boxLow {
		return none
	}
	breakClose := bars[acceptedAt].Close
	displacement := breakClose - boxHigh
	if side == market.Sell {
		displacement = boxLow - breakClose
	}
	return boxEpisode{Armed: true, Level: level, Close: last.Close, BarTime: last.Time, BreakDisplacement: displacement, RetestRejectionRequired: requireRejection}
}
