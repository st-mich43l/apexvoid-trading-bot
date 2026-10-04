// Package techniquezone builds the market-structure zones and technique
// construction chain: swings, structure, levels and
// zones.py): swings, structure breaks, displacement legs, supply/demand,
// order blocks, breakers, flip zones, fair-value gaps, mitigation stamping and
// same-side merging.
//
// The Go engine's own zone package (internal/zone) is a redesign with a very
// different tradeable zone population. This package produces the
// technique-qualified population consumed by the strategies.
//
// The committed testdata fixtures protect the behavior of this pipeline. The
// package is pure: bars in,
// zones out, no state, no config, no clock.
package techniquezone

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/reaction"
)

// Swing mirrors app/analysis/types.py::Swing.
type Swing struct {
	Index          int
	Kind           string // "high" | "low"
	Price          float64
	Label          string
	ConfirmedIndex int
}

// Leg mirrors types.Leg: a run of same-direction bars that moved far enough.
type Leg struct {
	Start, End int
	Direction  string // "up" | "down"
	Size       float64
}

// Break mirrors types.Break (a close beyond the last opposite swing).
type Break struct {
	Kind      string // "BOS" | "CHoCH"
	Direction string // "up" | "down"
	Level     float64
	Index     int
}

// Level mirrors types.Level.
type Level struct {
	Price    float64
	Kind     string
	Touches  int
	Band     float64
	Strength float64
}

// Zone is the immutable zone representation used by the technique pipeline.
type Zone struct {
	Bottom, Top float64
	Side        string // "demand" | "supply"
	OriginIndex int
	Touches     int
	Mitigated   bool
	Source      string
	Sources     []string
	BreakKind   string // "" when None
	BreakIndex  int    // -1 when None
}

func (z Zone) Low() float64  { return z.Bottom }
func (z Zone) High() float64 { return z.Top }

func newZone(bottom, top float64, side string, origin int, source string) Zone {
	if bottom > top {
		bottom, top = top, bottom
	}
	z := Zone{Bottom: bottom, Top: top, Side: side, OriginIndex: origin, Source: source, BreakIndex: -1}
	if source != "" {
		z.Sources = []string{source}
	}
	return z
}

// ---- math_utils ---------------------------------------------------------------

// ATRSeries is the simple (rolling mean, min_periods=1) true-range average.
func ATRSeries(bars []market.Candle, length int) []float64 {
	tr := make([]float64, len(bars))
	for i, b := range bars {
		r := b.High - b.Low
		if i > 0 {
			prev := bars[i-1].Close
			r = math.Max(r, math.Max(math.Abs(b.High-prev), math.Abs(b.Low-prev)))
		}
		tr[i] = r
	}
	out := make([]float64, len(bars))
	for i := range bars {
		lo := i - length + 1
		if lo < 0 {
			lo = 0
		}
		sum := 0.0
		for j := lo; j <= i; j++ {
			sum += tr[j]
		}
		out[i] = sum / float64(i-lo+1)
	}
	return out
}

// atrAt mirrors math_utils.atr_at.
func atrAt(atr []float64, index int, fallback float64) float64 {
	if len(atr) == 0 {
		return fallback
	}
	if index < 0 {
		index = 0
	}
	if index > len(atr)-1 {
		index = len(atr) - 1
	}
	v := atr[index]
	if math.IsNaN(v) || math.IsInf(v, 0) || v <= 0 {
		return fallback
	}
	return v
}

// ATRScalar mirrors math_utils.atr_scalar: the median of the series.
func ATRScalar(atr []float64, fallback float64) float64 {
	clean := make([]float64, 0, len(atr))
	for _, v := range atr {
		if !math.IsNaN(v) {
			clean = append(clean, v)
		}
	}
	if len(clean) == 0 {
		return fallback
	}
	sort.Float64s(clean)
	n := len(clean)
	var med float64
	if n%2 == 1 {
		med = clean[n/2]
	} else {
		med = (clean[n/2-1] + clean[n/2]) / 2
	}
	if math.IsNaN(med) || math.IsInf(med, 0) || med <= 0 {
		return fallback
	}
	return med
}

func bodyFraction(b market.Candle) float64 {
	span := b.High - b.Low
	if span <= 0 {
		return 0
	}
	return math.Abs(b.Close-b.Open) / span
}

func candleDirection(b market.Candle) string {
	switch {
	case b.Close > b.Open:
		return "up"
	case b.Close < b.Open:
		return "down"
	}
	return ""
}

// ---- swings.py ----------------------------------------------------------------

// FindSwings mirrors swings.find_swings (fractal candidates, zigzag filter,
// HH/HL/LH/LL labels). asOf < 0 means None.
func FindSwings(bars []market.Candle, fractalN int, zigzagPct, zigzagATRMult float64, atr []float64, asOf int) []Swing {
	if len(bars) < fractalN*2+1 {
		return nil
	}
	if atr == nil {
		atr = ATRSeries(bars, 14)
	}
	candidates := fractalCandidates(bars, fractalN, asOf)
	filtered := zigzagFilter(candidates, atr, zigzagPct, zigzagATRMult)
	return labelSwings(filtered)
}

func fractalCandidates(bars []market.Candle, n int, asOf int) []Swing {
	var out []Swing
	for i := n; i < len(bars)-n; i++ {
		if asOf >= 0 && i+n > asOf {
			continue
		}
		maxHigh, minLow := bars[i-n].High, bars[i-n].Low
		for j := i - n; j <= i+n; j++ {
			if bars[j].High > maxHigh {
				maxHigh = bars[j].High
			}
			if bars[j].Low < minLow {
				minLow = bars[j].Low
			}
		}
		high, low := bars[i].High, bars[i].Low
		if high == maxHigh {
			ok := true
			for j := i - n; j < i; j++ {
				if !(bars[j].High < high) {
					ok = false
					break
				}
			}
			if ok {
				out = append(out, Swing{Index: i, Kind: "high", Price: high, ConfirmedIndex: i + n})
			}
		}
		if low == minLow {
			ok := true
			for j := i - n; j < i; j++ {
				if !(bars[j].Low > low) {
					ok = false
					break
				}
			}
			if ok {
				out = append(out, Swing{Index: i, Kind: "low", Price: low, ConfirmedIndex: i + n})
			}
		}
	}
	sort.SliceStable(out, func(a, b int) bool {
		if out[a].Index != out[b].Index {
			return out[a].Index < out[b].Index
		}
		return out[a].Kind < out[b].Kind
	})
	return out
}

func zigzagFilter(candidates []Swing, atr []float64, pct, atrMult float64) []Swing {
	var confirmed []Swing
	for _, c := range candidates {
		if len(confirmed) == 0 {
			confirmed = append(confirmed, c)
			continue
		}
		last := confirmed[len(confirmed)-1]
		if c.Kind == last.Kind {
			if (c.Kind == "high" && c.Price > last.Price) || (c.Kind == "low" && c.Price < last.Price) {
				confirmed[len(confirmed)-1] = c
			}
			continue
		}
		threshold := math.Max(math.Abs(last.Price)*math.Max(0, pct), atrAt(atr, c.Index, 0)*math.Max(0, atrMult))
		if math.Abs(c.Price-last.Price) >= threshold {
			confirmed = append(confirmed, c)
		}
	}
	return confirmed
}

func labelSwings(in []Swing) []Swing {
	var lastHigh, lastLow *float64
	out := make([]Swing, 0, len(in))
	for _, s := range in {
		if s.Kind == "high" {
			if lastHigh == nil || s.Price > *lastHigh {
				s.Label = "HH"
			} else {
				s.Label = "LH"
			}
			p := s.Price
			lastHigh = &p
		} else {
			if lastLow == nil || s.Price > *lastLow {
				s.Label = "HL"
			} else {
				s.Label = "LL"
			}
			p := s.Price
			lastLow = &p
		}
		out = append(out, s)
	}
	return out
}

// ---- structure.py -------------------------------------------------------------

// MarketStructure mirrors structure.market_structure.
func MarketStructure(items []Swing) string {
	var highs, lows []Swing
	for _, s := range items {
		if s.Kind == "high" {
			highs = append(highs, s)
		} else if s.Kind == "low" {
			lows = append(lows, s)
		}
	}
	if len(highs) >= 2 && len(lows) >= 2 {
		h1, h2 := highs[len(highs)-1], highs[len(highs)-2]
		l1, l2 := lows[len(lows)-1], lows[len(lows)-2]
		if h1.Price > h2.Price && l1.Price > l2.Price {
			return "up"
		}
		if h1.Price < h2.Price && l1.Price < l2.Price {
			return "down"
		}
	}
	tail := items
	if len(tail) > 4 {
		tail = tail[len(tail)-4:]
	}
	labels := map[string]bool{}
	for _, s := range tail {
		labels[s.Label] = true
	}
	if labels["HH"] && labels["HL"] {
		return "up"
	}
	if labels["LH"] && labels["LL"] {
		return "down"
	}
	return "range"
}

func breakKind(trend, direction string) string {
	if trend == "up" || trend == "down" {
		if trend == direction {
			return "BOS"
		}
		return "CHoCH"
	}
	return "BOS"
}

type brokenKey struct {
	dir   string
	level float64
}

// StructureBreaks mirrors structure.structure_breaks (causal or look-ahead).
func StructureBreaks(swings []Swing, bars []market.Candle, causal bool, fractalN int) []Break {
	if !causal {
		return breaksLookahead(swings, bars)
	}
	return breaksCausal(swings, bars, fractalN)
}

func lastOfKind(prior []Swing, kind string) (Swing, bool) {
	for i := len(prior) - 1; i >= 0; i-- {
		if prior[i].Kind == kind {
			return prior[i], true
		}
	}
	return Swing{}, false
}

func emitBreaks(prior []Swing, trend string, close float64, i int, broken map[brokenKey]bool, out *[]Break) {
	if h, ok := lastOfKind(prior, "high"); ok {
		key := brokenKey{"up", h.Price}
		if close > h.Price && !broken[key] {
			broken[key] = true
			*out = append(*out, Break{Kind: breakKind(trend, "up"), Direction: "up", Level: h.Price, Index: i})
		}
	}
	if l, ok := lastOfKind(prior, "low"); ok {
		key := brokenKey{"down", l.Price}
		if close < l.Price && !broken[key] {
			broken[key] = true
			*out = append(*out, Break{Kind: breakKind(trend, "down"), Direction: "down", Level: l.Price, Index: i})
		}
	}
}

func breaksLookahead(swings []Swing, bars []market.Candle) []Break {
	var out []Break
	trend := MarketStructure(swings)
	broken := map[brokenKey]bool{}
	for i := range bars {
		var prior []Swing
		for _, s := range swings {
			if s.Index < i {
				prior = append(prior, s)
			}
		}
		emitBreaks(prior, trend, bars[i].Close, i, broken, &out)
	}
	return out
}

func breaksCausal(swings []Swing, bars []market.Candle, fractalN int) []Break {
	var out []Break
	broken := map[brokenKey]bool{}
	ordered := append([]Swing(nil), swings...)
	sort.SliceStable(ordered, func(a, b int) bool { return ordered[a].Index < ordered[b].Index })
	ptr := 0
	var confirmed []Swing
	for i := range bars {
		for ptr < len(ordered) && ordered[ptr].Index+fractalN <= i {
			confirmed = append(confirmed, ordered[ptr])
			ptr++
		}
		var prior []Swing
		for _, s := range confirmed {
			if s.Index < i {
				prior = append(prior, s)
			}
		}
		emitBreaks(prior, MarketStructure(prior), bars[i].Close, i, broken, &out)
	}
	return out
}

// ---- levels.py ----------------------------------------------------------------

// KeyLevels mirrors levels.key_levels (clusters, round levels, de-dup, wick
// touch episodes).
func KeyLevels(swings []Swing, atr []float64, clusterATR, roundStep float64, minTouches int, maxSpanMultiple float64, bars []market.Candle) []Level {
	tolerance := ATRScalar(atr, 1) * math.Max(0, clusterATR)
	clusters := priceClusters(swings, tolerance, maxSpanMultiple)
	var levels []Level
	for _, c := range clusters {
		if len(c) >= minTouches {
			sum := 0.0
			for _, s := range c {
				sum += s.Price
			}
			levels = append(levels, Level{Price: sum / float64(len(c)), Kind: "reaction", Touches: len(c), Band: tolerance, Strength: float64(len(c))})
		}
	}
	levels = append(levels, roundLevels(swings, atr, roundStep, tolerance, minTouches)...)
	sort.SliceStable(levels, func(a, b int) bool { return levels[a].Price < levels[b].Price })
	var deduped []Level
	for _, l := range levels {
		if n := len(deduped); n > 0 && math.Abs(deduped[n-1].Price-l.Price) <= math.Max(l.Band, tolerance) {
			prev := deduped[n-1]
			kind := l.Kind
			if prev.Touches >= l.Touches {
				kind = prev.Kind
			}
			deduped[n-1] = Level{
				Price: (prev.Price + l.Price) / 2, Kind: kind,
				Touches: maxInt(prev.Touches, l.Touches), Band: math.Max(prev.Band, l.Band), Strength: math.Max(prev.Strength, l.Strength),
			}
		} else {
			deduped = append(deduped, l)
		}
	}
	if len(bars) > 0 {
		for i, l := range deduped {
			deduped[i] = withWickTouches(l, bars, tolerance)
		}
	}
	return deduped
}

func priceClusters(swings []Swing, tolerance, maxSpanMultiple float64) [][]Swing {
	sorted := append([]Swing(nil), swings...)
	sort.SliceStable(sorted, func(a, b int) bool { return sorted[a].Price < sorted[b].Price })
	maxSpan := tolerance * math.Max(0, maxSpanMultiple)
	var clusters [][]Swing
	for _, s := range sorted {
		if n := len(clusters); n > 0 && canJoinCluster(clusters[n-1], s, tolerance, maxSpan) {
			clusters[n-1] = append(clusters[n-1], s)
		} else {
			clusters = append(clusters, []Swing{s})
		}
	}
	return clusters
}

func canJoinCluster(cluster []Swing, s Swing, tolerance, maxSpan float64) bool {
	lo, hi := s.Price, s.Price
	for _, c := range cluster {
		lo, hi = math.Min(lo, c.Price), math.Max(hi, c.Price)
	}
	if hi-lo > maxSpan {
		return false
	}
	for _, c := range cluster {
		if !(math.Abs(c.Price-s.Price) <= tolerance) {
			return false
		}
	}
	return true
}

func roundLevels(swings []Swing, atr []float64, step, tolerance float64, minTouches int) []Level {
	if len(swings) == 0 || step <= 0 {
		return nil
	}
	lo, hi := swings[0].Price, swings[0].Price
	for _, s := range swings {
		lo, hi = math.Min(lo, s.Price), math.Max(hi, s.Price)
	}
	low := math.Floor(lo/step) * step
	high := math.Ceil(hi/step) * step
	steps := int((high-low)/step) + 1
	var out []Level
	for k := 0; k < steps; k++ {
		price := low + float64(k)*step
		touches := 0
		for _, s := range swings {
			if math.Abs(s.Price-price) <= math.Max(tolerance, atrAt(atr, s.Index, 1)*0.25) {
				touches++
			}
		}
		if touches >= minTouches {
			out = append(out, Level{Price: price, Kind: "round", Touches: touches, Band: tolerance, Strength: float64(touches)})
		}
	}
	return out
}

func wickTouchEpisodes(bars []market.Candle, price, band float64) int {
	if len(bars) == 0 || band < 0 {
		return 0
	}
	lo, hi := price-band, price+band
	episodes, in := 0, false
	for _, b := range bars {
		touched := b.Low <= hi && b.High >= lo
		if touched {
			if b.Open > hi && b.Close < lo {
				touched = false
			} else if b.Open < lo && b.Close > hi {
				touched = false
			}
		}
		if touched && !in {
			episodes++
		}
		in = touched
	}
	return episodes
}

func withWickTouches(l Level, bars []market.Candle, tolerance float64) Level {
	band := math.Max(l.Band, tolerance)
	episodes := wickTouchEpisodes(bars, l.Price, band)
	if episodes <= l.Touches {
		return l
	}
	return Level{Price: l.Price, Kind: l.Kind, Touches: episodes, Band: l.Band, Strength: math.Max(l.Strength, float64(episodes))}
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// ---- zones.py -----------------------------------------------------------------

// Displacement mirrors zones.displacement: runs of same-direction bars whose
// net move clears k ATR and whose bodies are mostly strong.
func Displacement(bars []market.Candle, atr []float64, k, bodyFrac float64) []Leg {
	if len(bars) == 0 {
		return nil
	}
	if atr == nil {
		atr = ATRSeries(bars, 14)
	}
	var legs []Leg
	start, dir := -1, ""
	for i, b := range bars {
		cur := candleDirection(b)
		if cur == "" {
			appendLeg(bars, atr, &legs, start, i-1, dir, k, bodyFrac)
			start, dir = -1, ""
			continue
		}
		if dir == "" {
			start, dir = i, cur
			continue
		}
		if cur != dir {
			appendLeg(bars, atr, &legs, start, i-1, dir, k, bodyFrac)
			start, dir = i, cur
		}
	}
	appendLeg(bars, atr, &legs, start, len(bars)-1, dir, k, bodyFrac)
	return legs
}

func appendLeg(bars []market.Candle, atr []float64, legs *[]Leg, start, end int, dir string, k, bodyFrac float64) {
	if start < 0 || dir == "" || end < start {
		return
	}
	open, close := bars[start].Open, bars[end].Close
	size := open - close
	if dir == "up" {
		size = close - open
	}
	if size < atrAt(atr, end, 1)*k {
		return
	}
	strong := 0
	for i := start; i <= end; i++ {
		if bodyFraction(bars[i]) >= bodyFrac {
			strong++
		}
	}
	need := (end - start + 1) / 2
	if need < 1 {
		need = 1
	}
	if strong < need {
		return
	}
	*legs = append(*legs, Leg{Start: start, End: end, Direction: dir, Size: size})
}

// SupplyDemand mirrors zones.supply_demand.
func SupplyDemand(bars []market.Candle, legs []Leg) []Zone {
	var zones []Zone
	for _, leg := range legs {
		if leg.Start <= 0 {
			continue
		}
		baseStart := maxInt(0, leg.Start-3)
		if baseStart >= leg.Start {
			continue
		}
		lo, hi := bars[baseStart].Low, bars[baseStart].High
		for i := baseStart; i < leg.Start; i++ {
			lo, hi = math.Min(lo, bars[i].Low), math.Max(hi, bars[i].High)
		}
		side := "supply"
		if leg.Direction == "up" {
			side = "demand"
		}
		z := newZone(lo, hi, side, leg.Start-1, "supply_demand")
		z.BreakIndex = leg.End
		zones = append(zones, z)
	}
	return zones
}

func causingBOS(leg Leg, breaks []Break) (Break, bool) {
	for _, b := range breaks {
		if (b.Kind != "BOS" && b.Kind != "CHoCH") || b.Direction != leg.Direction {
			continue
		}
		if leg.Start <= b.Index && b.Index <= leg.End {
			return b, true
		}
	}
	return Break{}, false
}

func lastOppositeCandle(bars []market.Candle, leg Leg) int {
	opposite := "up"
	if leg.Direction == "up" {
		opposite = "down"
	}
	for i := leg.Start - 1; i >= 0; i-- {
		if candleDirection(bars[i]) == opposite {
			return i
		}
	}
	return -1
}

func zoneBand(b market.Candle, width string) (float64, float64) {
	if width == "range" {
		return b.Low, b.High
	}
	return math.Min(b.Open, b.Close), math.Max(b.Open, b.Close)
}

// OrderBlocks mirrors zones.order_blocks (zoneWidth "body" or "range").
func OrderBlocks(bars []market.Candle, legs []Leg, breaks []Break, zoneWidth string) []Zone {
	var zones []Zone
	for _, leg := range legs {
		bos, ok := causingBOS(leg, breaks)
		if !ok {
			continue
		}
		origin := lastOppositeCandle(bars, leg)
		if origin < 0 {
			continue
		}
		bottom, top := zoneBand(bars[origin], zoneWidth)
		side := "supply"
		if leg.Direction == "up" {
			side = "demand"
		}
		z := newZone(bottom, top, side, origin, "order_block")
		z.BreakKind, z.BreakIndex = bos.Kind, bos.Index
		zones = append(zones, z)
	}
	return zones
}

func breakerViolation(z Zone, bars []market.Candle) int {
	for i := maxInt(0, z.OriginIndex+1); i < len(bars); i++ {
		c := bars[i].Close
		if z.Side == "demand" && c < z.Low() {
			return i
		}
		if z.Side == "supply" && c > z.High() {
			return i
		}
	}
	return -1
}

// BreakerBlocks mirrors zones.breaker_blocks: a zone price closed through is
// dead and flips to the opposite side from the violation bar.
func BreakerBlocks(blocks []Zone, bars []market.Candle) []Zone {
	var zones []Zone
	for _, z := range blocks {
		v := breakerViolation(z, bars)
		if v < 0 {
			zones = append(zones, z)
			continue
		}
		dead := z
		dead.Touches = maxInt(z.Touches, 1)
		dead.Mitigated = true
		zones = append(zones, dead)
		side := "demand"
		if z.Side == "demand" {
			side = "supply"
		}
		flipped := newZone(z.Low(), z.High(), side, v, "breaker")
		flipped.BreakKind, flipped.BreakIndex = "breaker", v
		zones = append(zones, flipped)
	}
	return zones
}

// FlipZones mirrors zones.flip_zones. maxBreakAge < 0 means None.
func FlipZones(levels []Level, breaks []Break, bars []market.Candle, acceptBars, maxBreakAge int, bandBodyFraction float64) []Zone {
	required := maxInt(1, acceptBars)
	n := len(bars)
	accepted := func(index int, price float64, dir string) bool {
		if index < 0 || index+required > n {
			return false
		}
		for off := 0; off < required; off++ {
			c := bars[index+off].Close
			if dir == "up" && !(c > price) {
				return false
			}
			if dir == "down" && !(c < price) {
				return false
			}
		}
		return true
	}
	type seenKey struct {
		price float64
		side  string
	}
	seen := map[seenKey]bool{}
	var zones []Zone
	for _, item := range breaks {
		if maxBreakAge >= 0 && n-1-item.Index > maxBreakAge {
			continue
		}
		if !accepted(item.Index, item.Level, item.Direction) {
			continue
		}
		for _, level := range levels {
			if math.Abs(item.Level-level.Price) > math.Max(level.Band, 0) {
				continue
			}
			side := "supply"
			if item.Direction == "up" {
				side = "demand"
			}
			key := seenKey{round6(level.Price), side}
			if seen[key] {
				continue
			}
			band := math.Max(level.Band, 0)
			var bottom, top float64
			if item.Direction == "up" {
				bottom, top = level.Price, level.Price+band
			} else {
				bottom, top = level.Price-band, level.Price
			}
			row := bars[item.Index]
			body := math.Abs(row.Close - row.Open)
			width := math.Max(top-bottom, body*bandBodyFraction)
			if item.Direction == "up" {
				top = bottom + width
			} else {
				bottom = top - width
			}
			z := newZone(bottom, top, side, item.Index, "flip_zone")
			z.BreakKind, z.BreakIndex = item.Kind, item.Index
			const eps = 1e-9
			invalid := math.IsNaN(z.Bottom) || math.IsInf(z.Bottom, 0) || math.IsNaN(z.Top) || math.IsInf(z.Top, 0) ||
				(side == "demand" && z.Bottom < level.Price-eps) ||
				(side == "supply" && z.Top > level.Price+eps) ||
				z.Top-z.Bottom <= 0
			if invalid {
				continue
			}
			seen[key] = true
			zones = append(zones, z)
		}
	}
	return zones
}

func round6(v float64) float64 { return math.Round(v*1e6) / 1e6 }

// FVG mirrors zones.fvg: three-bar gaps.
func FVG(bars []market.Candle) []Zone {
	var zones []Zone
	for i := 2; i < len(bars); i++ {
		older, cur := bars[i-2], bars[i]
		if older.High < cur.Low {
			zones = append(zones, newZone(older.High, cur.Low, "demand", i, "bullish_fvg"))
		}
		if older.Low > cur.High {
			zones = append(zones, newZone(cur.High, older.Low, "supply", i, "bearish_fvg"))
		}
	}
	return zones
}

// MarkMitigation mirrors zones.mark_mitigation. cutoff < 0 means None (full
// history); otherwise the exclusive scan end.
func MarkMitigation(zones []Zone, bars []market.Candle, cutoff int) []Zone {
	end := len(bars)
	if cutoff >= 0 {
		end = cutoff
		if end > len(bars) {
			end = len(bars)
		}
	}
	out := make([]Zone, 0, len(zones))
	for _, z := range zones {
		startFrom := z.OriginIndex
		if z.BreakIndex >= 0 {
			startFrom = z.BreakIndex
		}
		start := maxInt(0, startFrom+1)
		touches := 0
		if start < end {
			prev := false
			for i := start; i < end; i++ {
				touched := bars[i].Low <= z.Top && bars[i].High >= z.Bottom
				if touched && !prev {
					touches++
				}
				prev = touched
			}
		}
		final := maxInt(touches, z.Touches)
		z.Touches = final
		z.Mitigated = z.Mitigated || final > 0
		out = append(out, z)
	}
	return out
}

// AsSingleZones mirrors zones.as_single_zones.
func AsSingleZones(zones []Zone) []Zone {
	out := make([]Zone, len(zones))
	for i, z := range zones {
		z.Sources = uniqueSources([]Zone{z})
		out[i] = z
	}
	return out
}

func overlapRatio(a, b Zone) float64 {
	overlap := math.Min(a.High(), b.High()) - math.Max(a.Low(), b.Low())
	if overlap <= 0 {
		return 0
	}
	smaller := math.Min(a.High()-a.Low(), b.High()-b.Low())
	if smaller <= 0 {
		if a.Low() <= b.High() && b.Low() <= a.High() {
			return 1
		}
		return 0
	}
	return overlap / smaller
}

func mergedWidth(zs []Zone) float64 {
	hi, lo := zs[0].High(), zs[0].Low()
	for _, z := range zs {
		hi, lo = math.Max(hi, z.High()), math.Min(lo, z.Low())
	}
	return hi - lo
}

// MergeZones mirrors zones.merge_zones. maxWidth < 0 means None.
func MergeZones(zones []Zone, minOverlap, maxWidth float64) []Zone {
	sorted := append([]Zone(nil), zones...)
	sort.SliceStable(sorted, func(a, b int) bool {
		x, y := sorted[a], sorted[b]
		if x.Side != y.Side {
			return x.Side < y.Side
		}
		if x.Low() != y.Low() {
			return x.Low() < y.Low()
		}
		return x.High() < y.High()
	})
	var groups [][]Zone
	for _, z := range sorted {
		placed := false
		for gi := range groups {
			if groups[gi][0].Side != z.Side {
				continue
			}
			hit := false
			for _, m := range groups[gi] {
				if overlapRatio(z, m) >= minOverlap {
					hit = true
					break
				}
			}
			if !hit {
				continue
			}
			if maxWidth >= 0 && mergedWidth(append(append([]Zone(nil), groups[gi]...), z)) > maxWidth {
				continue
			}
			groups[gi] = append(groups[gi], z)
			placed = true
			break
		}
		if !placed {
			groups = append(groups, []Zone{z})
		}
	}
	out := make([]Zone, 0, len(groups))
	for _, g := range groups {
		out = append(out, compositeZone(g))
	}
	return out
}

var sourceScores = map[string]float64{
	"order_block": 3, "breaker": 2, "flip_zone": 2, "supply_demand": 1.5, "bullish_fvg": 1, "bearish_fvg": 1, "box_breakout": 5,
}

const sourceScoreCap = 5.0

func sourceQuality(z Zone) float64 {
	total := 0.0
	srcs := z.Sources
	if len(srcs) == 0 && z.Source != "" {
		srcs = []string{z.Source}
	}
	for _, s := range srcs {
		v := sourceScores[s]
		if s == "order_block" && z.BreakKind == "" {
			v = 0
		}
		if v <= 0 {
			continue
		}
		total += v
	}
	return math.Min(total, sourceScoreCap)
}

func uniqueSources(zs []Zone) []string {
	var out []string
	for _, z := range zs {
		srcs := z.Sources
		if len(srcs) == 0 && z.Source != "" {
			srcs = []string{z.Source}
		}
		for _, s := range srcs {
			if s == "" {
				continue
			}
			dup := false
			for _, o := range out {
				if o == s {
					dup = true
					break
				}
			}
			if !dup {
				out = append(out, s)
			}
		}
	}
	return out
}

func compositeZone(group []Zone) Zone {
	if len(group) == 1 {
		z := group[0]
		z.Sources = uniqueSources([]Zone{z})
		return z
	}
	best := group[0]
	for _, z := range group[1:] {
		if sourceQuality(z) > sourceQuality(best) { // first maximal element wins
			best = z
		}
	}
	earliest := group[0]
	for _, z := range group[1:] {
		if z.OriginIndex < earliest.OriginIndex {
			earliest = z
		}
	}
	touches := group[0].Touches
	lo, hi := group[0].Low(), group[0].High()
	for _, z := range group {
		if z.Touches < touches {
			touches = z.Touches
		}
		lo, hi = math.Min(lo, z.Low()), math.Max(hi, z.High())
	}
	out := newZone(lo, hi, group[0].Side, earliest.OriginIndex, best.Source)
	out.Touches = touches
	out.Mitigated = touches > 0
	out.Sources = uniqueSources(group)
	out.BreakKind, out.BreakIndex = best.BreakKind, best.BreakIndex
	return out
}

// ---- technique_geometry.py: technique instances ------------------------------

// TechniqueSettings mirrors technique_geometry.TechniqueGeometrySettings (the
// fields the instance validation uses).
type TechniqueSettings struct {
	PipSize                  float64
	EpsilonATRFrac           float64
	MaxZoneATR               float64
	FVGMaxATR                float64
	FVGMinPips               float64
	FVGEntryMaxWidthPrice    float64
	MomentumBodyFrac         float64
	RetestMaxTouches         int
	InvalidationToleranceATR float64
	SweepReclaimBars         int
	MaxBreakEpisodes         int
}

// Instance mirrors technique_geometry.TechniqueInstance for the techniques
// built from the zone chain (supply_demand, order_block, fvg, ifvg).
type Instance struct {
	Technique string // "supply_demand" | "order_block" | "fvg" | "ifvg"
	Side      string // "buy" | "sell"
	Low, High float64
	Sources   []string
	// OriginIndex is the bar the structure is anchored to (frame-relative).
	OriginIndex int
	// Measured facts (instance.measured).
	Touches        int
	Mitigated      bool
	EntryClipped   bool
	StructuralLow  float64
	StructuralHigh float64
	BodyFrac       float64
	HasBOS         bool
	// Score carries the legacy source-quality score into the parity instance.
	Score float64
	// H1Time is the H1 candle a CRT instance is built on (open time).
	H1Time int64
}

func tEpsilon(s TechniqueSettings, atr float64) float64 {
	return math.Max(s.PipSize, s.EpsilonATRFrac*math.Max(0, atr))
}

func zoneSideToTradeSide(side string) string {
	if side == "demand" {
		return "buy"
	}
	return "sell"
}

// notInvalidated mirrors technique_geometry.not_invalidated: price never
// accepted beyond the far edge (a close beyond starts an episode, forgiven when
// a close returns within SweepReclaimBars; too many forgiven episodes kill it).
func notInvalidated(side string, low, high float64, bars []market.Candle, originIndex int, atr float64, s TechniqueSettings) bool {
	if len(bars) == 0 || originIndex < 0 {
		return true
	}
	e := tEpsilon(s, atr)
	tolerance := math.Max(e, s.InvalidationToleranceATR*math.Max(atr, 0))
	count := len(bars)
	reclaimWindow := maxInt(0, s.SweepReclaimBars)
	episodes := 0
	index := maxInt(0, originIndex+1)
	for index < count {
		close := bars[index].Close
		var beyond bool
		if side == "buy" {
			beyond = close < low-tolerance
		} else {
			beyond = close > high+tolerance
		}
		if !beyond {
			index++
			continue
		}
		reclaimedAt := -1
		for probe := index + 1; probe < minInt(count, index+1+reclaimWindow); probe++ {
			var back bool
			if side == "buy" {
				back = bars[probe].Close >= low
			} else {
				back = bars[probe].Close <= high
			}
			if back {
				reclaimedAt = probe
				break
			}
		}
		if reclaimedAt < 0 {
			return false
		}
		episodes++
		if episodes > s.MaxBreakEpisodes {
			return false
		}
		index = reclaimedAt + 1
	}
	return true
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// zoneIsSpent mirrors technique_geometry.zone_is_spent.
func zoneIsSpent(mitigated bool, touches int, side string, low, high float64, bars []market.Candle, originIndex int, atr float64, s TechniqueSettings) bool {
	if !mitigated {
		return false
	}
	if s.RetestMaxTouches <= 0 || touches <= 0 {
		return true
	}
	if touches > s.RetestMaxTouches {
		return true
	}
	return !notInvalidated(side, low, high, bars, originIndex, atr, s)
}

func proximalRetest(side string, low, high, price, atr float64, s TechniqueSettings) bool {
	e := tEpsilon(s, atr)
	proximal := low
	if side == "buy" {
		proximal = high
	}
	return math.Abs(price-proximal) <= e || (low-e <= price && price <= high+e)
}

func widthWithinATR(low, high, atr, maxATR float64) bool {
	if atr <= 0 {
		return true
	}
	return high-low <= math.Max(0, maxATR)*atr
}

func fvgNotFullyFilled(side string, low, high float64, bars []market.Candle, originIndex int) bool {
	if len(bars) == 0 || originIndex < 0 {
		return true
	}
	for i := originIndex + 1; i < len(bars); i++ {
		if side == "buy" {
			if bars[i].Low <= low {
				return false
			}
		} else if bars[i].High >= high {
			return false
		}
	}
	return true
}

// optimizeTechniqueEntry mirrors optimize_technique_entry_zone.
func optimizeTechniqueEntry(z Zone, maxWidth float64) (Zone, bool) {
	low, high := z.Low(), z.High()
	if math.IsNaN(low) || math.IsInf(low, 0) || math.IsNaN(high) || math.IsInf(high, 0) || high <= low {
		return z, false
	}
	if math.IsNaN(maxWidth) || math.IsInf(maxWidth, 0) || maxWidth <= 0 {
		return z, false
	}
	if high-low <= maxWidth+1e-12 {
		return z, false
	}
	out := z
	if z.Side == "supply" {
		out.Bottom, out.Top = low, low+maxWidth
	} else {
		out.Bottom, out.Top = high-maxWidth, high
	}
	return out, true
}

func instanceFromZone(z Zone, technique string, entryMax float64) Instance {
	sources := z.Sources
	if len(sources) == 0 && z.Source != "" {
		sources = []string{z.Source}
	}
	inst := Instance{
		Technique: technique, Side: zoneSideToTradeSide(z.Side), Low: z.Low(), High: z.High(),
		Sources: append([]string(nil), sources...), OriginIndex: z.OriginIndex, Touches: z.Touches, Mitigated: z.Mitigated,
		Score: sourceQuality(z),
	}
	if (technique == "supply_demand" || technique == "order_block" || technique == "fvg") && entryMax > 0 {
		if clipped, ok := optimizeTechniqueEntry(z, entryMax); ok {
			inst.StructuralLow, inst.StructuralHigh, inst.EntryClipped = z.Low(), z.High(), true
			inst.Low, inst.High = clipped.Low(), clipped.High()
		}
	}
	return inst
}

func obBodyFrac(bars []market.Candle, origin int) float64 {
	if origin < 0 || origin >= len(bars) {
		return 0
	}
	b := bars[origin]
	return math.Abs(b.Close-b.Open) / math.Max(b.High-b.Low, 1e-9)
}

func discoverIFVG(fvgZones []Zone, bars []market.Candle, entryMax float64) []Instance {
	var out []Instance
	for _, z := range fvgZones {
		if z.OriginIndex < 0 {
			continue
		}
		zLo, zHi := z.Low(), z.High()
		invertedSide, invertIndex := "", -1
		for i := z.OriginIndex + 1; i < len(bars); i++ {
			c := bars[i].Close
			if z.Side == "demand" && c < zLo {
				invertedSide, invertIndex = "sell", i
				break
			}
			if z.Side == "supply" && c > zHi {
				invertedSide, invertIndex = "buy", i
				break
			}
		}
		if invertedSide == "" {
			continue
		}
		tradeSide := invertedSide
		for i := invertIndex + 1; i < len(bars); i++ {
			c := bars[i].Close
			if (tradeSide == "buy" && c < zLo) || (tradeSide == "sell" && c > zHi) {
				invertedSide = ""
				break
			}
		}
		if invertedSide == "" {
			continue
		}
		inst := Instance{Technique: "ifvg", Side: tradeSide, Low: zLo, High: zHi, Sources: []string{"ifvg"}, OriginIndex: invertIndex}
		if entryMax > 0 && !math.IsNaN(entryMax) && !math.IsInf(entryMax, 0) && zHi-zLo > entryMax+1e-12 {
			if tradeSide == "sell" {
				inst.Low, inst.High = zLo, zLo+entryMax
			} else {
				inst.Low, inst.High = zHi-entryMax, zHi
			}
			inst.StructuralLow, inst.StructuralHigh, inst.EntryClipped = zLo, zHi, true
		}
		out = append(out, inst)
	}
	return out
}

func validateInstance(in Instance, bars []market.Candle, price, atr float64, s TechniqueSettings) bool {
	if zoneIsSpent(in.Mitigated, in.Touches, in.Side, in.Low, in.High, bars, in.OriginIndex, atr, s) {
		return false
	}
	if !notInvalidated(in.Side, in.Low, in.High, bars, in.OriginIndex, atr, s) {
		return false
	}
	if !proximalRetest(in.Side, in.Low, in.High, price, atr, s) {
		return false
	}
	maxATR := s.MaxZoneATR
	if in.Technique == "fvg" {
		if in.High-in.Low < s.FVGMinPips*s.PipSize {
			return false
		}
		maxATR = s.FVGMaxATR
		if !fvgNotFullyFilled(in.Side, in.Low, in.High, bars, in.OriginIndex) {
			return false
		}
	}
	if !widthWithinATR(in.Low, in.High, atr, maxATR) {
		return false
	}
	if in.Technique == "order_block" {
		if in.BodyFrac < s.MomentumBodyFrac || !in.HasBOS {
			return false
		}
	}
	return true
}

// SourceViews mirrors engine._zone_views for the technique zones: the order
// block, supply/demand and FVG subsets (a zone belongs to every view whose
// source it carries).
func SourceViews(zones []Zone) (ob, sd, fvg []Zone) {
	has := func(z Zone, source string) bool {
		if z.Source == source {
			return true
		}
		for _, s := range z.Sources {
			if s == source {
				return true
			}
		}
		return false
	}
	for _, z := range zones {
		if has(z, "order_block") {
			ob = append(ob, z)
		}
		if has(z, "supply_demand") {
			sd = append(sd, z)
		}
		isFVG := false
		for _, s := range z.Sources {
			if len(s) >= 4 && s[len(s)-4:] == "_fvg" {
				isFVG = true
			}
		}
		if isFVG {
			fvg = append(fvg, z)
		}
	}
	return
}

// CollectInstances mirrors technique_geometry.collect_technique_instances
// (without CRT, which needs H1 bars, and with validation enabled and no
// reaction requirement): every spent, invalidated, off-price, mis-sized or
// weak-body structure is dropped; what is left is a zone price is at now.
func CollectInstances(sdZones, obZones, fvgZones []Zone, bars []market.Candle, price, atr float64, s TechniqueSettings) []Instance {
	spent := func(z Zone) bool {
		return zoneIsSpent(z.Mitigated, z.Touches, zoneSideToTradeSide(z.Side), z.Low(), z.High(), bars, z.OriginIndex, atr, s)
	}
	var pending []Instance
	for _, z := range sdZones {
		if spent(z) {
			continue
		}
		pending = append(pending, instanceFromZone(z, "supply_demand", s.FVGEntryMaxWidthPrice))
	}
	for _, z := range obZones {
		if z.BreakKind == "" || spent(z) {
			continue
		}
		in := instanceFromZone(z, "order_block", s.FVGEntryMaxWidthPrice)
		in.BodyFrac, in.HasBOS = obBodyFrac(bars, in.OriginIndex), true
		pending = append(pending, in)
	}
	for _, z := range fvgZones {
		if spent(z) {
			continue
		}
		pending = append(pending, instanceFromZone(z, "fvg", s.FVGEntryMaxWidthPrice))
	}
	pending = append(pending, discoverIFVG(fvgZones, bars, s.FVGEntryMaxWidthPrice)...)
	var out []Instance
	for _, in := range pending {
		if validateInstance(in, bars, price, atr, s) {
			out = append(out, in)
		}
	}
	return out
}

// ---- production settings and the one-call pipeline ---------------------------

// ChainSettings are the detector settings for the zone-construction chain.
type ChainSettings struct {
	ATRLength           int
	SwingFractalN       int
	ZigzagPct           float64
	ZigzagATRMult       float64
	DisplacementATRMult float64
	MomentumBodyFrac    float64
	ZoneWidth           string
	CausalStructure     bool
}

// ProductionChainSettings are the algo-bot production values (pinned to the
// production golden parameters).
func ProductionChainSettings() ChainSettings {
	return ChainSettings{
		ATRLength: 14, SwingFractalN: 2, ZigzagPct: 0, ZigzagATRMult: 1.0,
		DisplacementATRMult: 1.5, MomentumBodyFrac: 0.6, ZoneWidth: "body", CausalStructure: false,
	}
}

// ProductionTechniqueSettings are the algo-bot production technique validation
// values; PipSize and FVGEntryMaxWidthPrice are per instrument and set by the
// caller (the defaults here are XAU's).
func ProductionTechniqueSettings() TechniqueSettings {
	return TechniqueSettings{
		PipSize: 0.1, EpsilonATRFrac: 0.05, MaxZoneATR: 3.0, FVGMaxATR: 2.0, FVGMinPips: 1.0,
		FVGEntryMaxWidthPrice: 5.0, MomentumBodyFrac: 0.6, RetestMaxTouches: 30,
		InvalidationToleranceATR: 0.5, SweepReclaimBars: 6, MaxBreakEpisodes: 2,
	}
}

// TechniqueInstances runs the whole chain over bars and returns the technique
// instances eligible for publication right now (without CRT).
func TechniqueInstances(bars []market.Candle, c ChainSettings, s TechniqueSettings) []Instance {
	if len(bars) == 0 {
		return nil
	}
	atr := ATRSeries(bars, c.ATRLength)
	asOf := -1
	if c.CausalStructure {
		asOf = len(bars) - 1
	}
	swings := FindSwings(bars, c.SwingFractalN, c.ZigzagPct, c.ZigzagATRMult, atr, asOf)
	breaks := StructureBreaks(swings, bars, c.CausalStructure, c.SwingFractalN)
	legs := Displacement(bars, atr, c.DisplacementATRMult, c.MomentumBodyFrac)
	sd := BreakerBlocks(SupplyDemand(bars, legs), bars)
	ob := BreakerBlocks(OrderBlocks(bars, legs, breaks, c.ZoneWidth), bars)
	fvg := FVG(bars)
	all := append(append(append([]Zone(nil), sd...), ob...), fvg...)
	marked := MarkMitigation(AsSingleZones(all), bars, maxInt(0, len(bars)-1))
	obV, sdV, fvgV := SourceViews(marked)
	return CollectInstances(sdV, obV, fvgV, bars, bars[len(bars)-1].Close, ATRScalar(atr, 1), s)
}

// ---- technique_geometry.py: CRT ------------------------------------------------

// CRTSettings are discover_crt_instances' inputs.
type CRTSettings struct {
	MinATR               float64 // crt_min_atr (x H1 ATR)
	ReclaimBars          int
	EntryMaxWidthPrice   float64
	H1LookbackBars       int
	ReactionLookbackBars int // structural_reaction_lookback_bars
}

// ProductionCRTSettings are the algo-bot production values (entry cap is per
// instrument and set by the caller; this is XAU's).
func ProductionCRTSettings() CRTSettings {
	return CRTSettings{MinATR: 1.5, ReclaimBars: 6, EntryMaxWidthPrice: 5.0, H1LookbackBars: 3, ReactionLookbackBars: 3}
}

// DiscoverCRT mirrors technique_geometry.discover_crt_instances: a recent
// closed H1 candle at least MinATR H1-ATRs tall that the execution timeframe
// swept and reclaimed (within ReclaimBars), price in the correct half of the
// range, with a structural reaction on the execution bars off the full H1
// range. h1Bars are CLOSED bars only.
func DiscoverCRT(h1Bars, execBars []market.Candle, h1ATR float64, s CRTSettings) []Instance {
	if len(h1Bars) == 0 || len(execBars) == 0 || h1ATR <= 0 {
		return nil
	}
	minRange := s.MinATR * h1ATR
	reclaim := maxInt(1, s.ReclaimBars)
	lookback := maxInt(1, s.H1LookbackBars)
	start := maxInt(0, len(h1Bars)-lookback)
	foundSides := map[string]bool{}
	var out []Instance
	for rowPos := len(h1Bars) - 1; rowPos >= start; rowPos-- {
		row := h1Bars[rowPos]
		rangeHigh, rangeLow := row.High, row.Low
		if rangeHigh-rangeLow < minRange {
			continue
		}
		mid := (rangeHigh + rangeLow) / 2
		for _, side := range []string{"buy", "sell"} {
			if foundSides[side] {
				continue
			}
			swept, sweepIndex := false, -1
			for index := maxInt(0, len(execBars)-reclaim-5); index < len(execBars); index++ {
				if side == "buy" && execBars[index].Low < rangeLow {
					swept, sweepIndex = true, index
				}
				if side == "sell" && execBars[index].High > rangeHigh {
					swept, sweepIndex = true, index
				}
			}
			if !swept {
				continue
			}
			reclaimed := false
			for index := sweepIndex; index < minInt(len(execBars), sweepIndex+reclaim+1); index++ {
				c := execBars[index].Close
				if (side == "buy" && c >= rangeLow) || (side == "sell" && c <= rangeHigh) {
					reclaimed = true
					break
				}
			}
			if !reclaimed {
				continue
			}
			price := execBars[len(execBars)-1].Close
			if (side == "buy" && price > mid) || (side == "sell" && price < mid) {
				continue
			}
			direction := "BUY"
			if side == "sell" {
				direction = "SELL"
			}
			if reaction.Evaluate(execBars, reaction.Params{
				Direction: direction, Low: rangeLow, High: rangeHigh, TouchLookback: s.ReactionLookbackBars,
			}) == nil {
				continue
			}
			inst := Instance{
				Technique: "crt", Side: side, Low: rangeLow, High: rangeHigh, Sources: []string{"crt"},
				OriginIndex: rowPos, StructuralLow: rangeLow, StructuralHigh: rangeHigh, H1Time: row.Time,
			}
			// CRT keeps the SWEPT edge (BUY -> low, SELL -> high).
			if rangeHigh-rangeLow > s.EntryMaxWidthPrice+1e-12 && s.EntryMaxWidthPrice > 0 {
				if side == "sell" {
					inst.Low, inst.High = rangeHigh-s.EntryMaxWidthPrice, rangeHigh
				} else {
					inst.Low, inst.High = rangeLow, rangeLow+s.EntryMaxWidthPrice
				}
				inst.EntryClipped = true
			}
			out = append(out, inst)
			foundSides[side] = true
		}
		if len(foundSides) >= 2 {
			break
		}
	}
	return out
}

// CollectCRT returns the CRT instances that pass the technique validation
// (collect_technique_instances applied them to CRT like any other).
func CollectCRT(h1Bars, execBars []market.Candle, h1ATR, execATR float64, crt CRTSettings, s TechniqueSettings) []Instance {
	var out []Instance
	price := 0.0
	if len(execBars) > 0 {
		price = execBars[len(execBars)-1].Close
	}
	for _, in := range DiscoverCRT(h1Bars, execBars, h1ATR, crt) {
		if validateInstance(in, execBars, price, execATR, s) {
			out = append(out, in)
		}
	}
	return out
}
