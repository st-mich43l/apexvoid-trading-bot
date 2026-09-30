// Package barrier builds the canonical opposing-structure book from the zones
// the engine already tracks. It is a faithful port of the normalization in
// algo-bot/app/autotrade/structural_barriers.py (execution-width gate,
// same-side merge, cross-side reconciliation) so Go, which owns technical
// structure, publishes the barrier set the execution-time target-room check
// consumes instead of leaving every consumer to re-derive it.
//
// The package is pure: no I/O, clock or randomness, and its output order is
// deterministic.
package barrier

import (
	"math"
	"sort"
	"strconv"
	"strings"
)

// Side is the barrier's side in the execution vocabulary: "buy" is a demand
// wall below price, "sell" a supply wall above it.
type Side string

const (
	Buy  Side = "buy"
	Sell Side = "sell"
)

// Tier ranks a barrier. The zone book carries no multi-timeframe scoring
// reason, so every barrier built here is TierZone - the same value the
// Python adapter assigned before this port.
type Tier string

const (
	TierLevel Tier = "level"
	TierZone  Tier = "zone"
	TierMajor Tier = "major"
)

func (t Tier) rank() int {
	switch t {
	case TierLevel:
		return 1
	case TierZone:
		return 2
	case TierMajor:
		return 3
	default:
		return 0
	}
}

// Zone is one tracked zone as published in the zone book. ATR is the ATR of
// the zone's own timeframe; the width gate is relative to it.
type Zone struct {
	Timeframe string
	Side      Side
	Low, High float64
	ATR       float64
	Score     float64
	Touches   int
	// Live reports whether the zone still counts as a standing barrier
	// (fresh, touched or partially mitigated).
	Live bool
}

// Barrier is one merged opposing-structure wall.
type Barrier struct {
	Side             Side     `json:"side"`
	Low              float64  `json:"low"`
	High             float64  `json:"high"`
	Tier             Tier     `json:"tier"`
	Score            float64  `json:"score"`
	Touches          int      `json:"touches"`
	SourceTimeframes []string `json:"source_timeframes"`
}

// Config carries the execution-width policy and timeframe scope.
type Config struct {
	// Timeframes limits which timeframes contribute (canonically M5, M15, H1).
	Timeframes   []string
	PipSize      float64
	MaxWidthATR  float64
	MaxWidthPips float64
}

// DefaultTimeframes mirrors structural_barriers.DEFAULT_STRUCTURAL_TIMEFRAMES.
var DefaultTimeframes = []string{"M5", "M15", "H1"}

const (
	// crossSideOverlapRatio and crossSideMaxDropFraction mirror the constants
	// of the same name in structural_barriers.py: never let one pass strip
	// more than a third of the pool, and fail open instead.
	crossSideOverlapRatio    = 0.5
	crossSideMaxDropFraction = 0.34
)

// Build applies the scope, width gate, same-side merge and cross-side
// reconciliation, in that order.
func Build(zones []Zone, cfg Config) []Barrier {
	timeframes := cfg.Timeframes
	if len(timeframes) == 0 {
		timeframes = DefaultTimeframes
	}
	allowed := make(map[string]struct{}, len(timeframes))
	for _, tf := range timeframes {
		allowed[strings.ToUpper(tf)] = struct{}{}
	}
	var raw []Barrier
	for _, z := range zones {
		if !z.Live || (z.Side != Buy && z.Side != Sell) {
			continue
		}
		tf := strings.ToUpper(z.Timeframe)
		if _, ok := allowed[tf]; !ok {
			continue
		}
		if !meetsExecutionWidth(z, cfg) {
			continue
		}
		raw = append(raw, Barrier{
			Side: z.Side, Low: z.Low, High: z.High, Tier: TierZone,
			Score: z.Score, Touches: z.Touches, SourceTimeframes: []string{tf},
		})
	}
	return resolveCrossSide(mergeSameSide(raw))
}

// meetsExecutionWidth ports structural_target_room.zone_meets_execution_width:
// a zone must have finite positive width, and a positive ATR and pip size to
// measure it against, and stay within both the ATR and pip limits.
func meetsExecutionWidth(z Zone, cfg Config) bool {
	width := z.High - z.Low
	if math.IsNaN(width) || math.IsInf(width, 0) || width <= 0 || cfg.PipSize <= 0 || z.ATR <= 0 {
		return false
	}
	return !(width/z.ATR > cfg.MaxWidthATR || width/cfg.PipSize > cfg.MaxWidthPips)
}

func bandsOverlap(aLow, aHigh, bLow, bHigh float64) bool {
	return math.Min(aHigh, bHigh) >= math.Max(aLow, bLow)
}

// tfRank orders timeframes like structural_barriers._tf_rank (minutes).
func tfRank(tf string) int {
	tf = strings.ToUpper(tf)
	if len(tf) < 2 {
		return 0
	}
	value, err := strconv.Atoi(tf[1:])
	if err != nil {
		return 0
	}
	switch tf[0] {
	case 'M':
		return value
	case 'H':
		return value * 60
	default:
		return 0
	}
}

// mergeSameSide ports _merge_same_side: per side (buy first), sort by
// (low, high) and union-merge touching or overlapping bands. A merge keeps
// the stronger tier and score, the smaller touch count, and the union of the
// contributing timeframes.
func mergeSameSide(barriers []Barrier) []Barrier {
	var resolved []Barrier
	for _, side := range []Side{Buy, Sell} {
		var candidates []Barrier
		for _, b := range barriers {
			if b.Side == side {
				candidates = append(candidates, b)
			}
		}
		sort.SliceStable(candidates, func(i, j int) bool {
			if candidates[i].Low != candidates[j].Low {
				return candidates[i].Low < candidates[j].Low
			}
			return candidates[i].High < candidates[j].High
		})
		var merged []Barrier
		for _, b := range candidates {
			if len(merged) == 0 || !bandsOverlap(merged[len(merged)-1].Low, merged[len(merged)-1].High, b.Low, b.High) {
				merged = append(merged, b)
				continue
			}
			prev := merged[len(merged)-1]
			tier := prev.Tier
			if b.Tier.rank() > tier.rank() {
				tier = b.Tier
			}
			touches := prev.Touches
			if b.Touches < touches {
				touches = b.Touches
			}
			merged[len(merged)-1] = Barrier{
				Side: side, Low: math.Min(prev.Low, b.Low), High: math.Max(prev.High, b.High),
				Tier: tier, Score: math.Max(prev.Score, b.Score), Touches: touches,
				SourceTimeframes: unionTimeframes(prev.SourceTimeframes, b.SourceTimeframes),
			}
		}
		resolved = append(resolved, merged...)
	}
	return resolved
}

func unionTimeframes(a, b []string) []string {
	set := make(map[string]struct{}, len(a)+len(b))
	for _, tf := range a {
		set[tf] = struct{}{}
	}
	for _, tf := range b {
		set[tf] = struct{}{}
	}
	out := make([]string, 0, len(set))
	for tf := range set {
		out = append(out, tf)
	}
	sort.SliceStable(out, func(i, j int) bool {
		ri, rj := tfRank(out[i]), tfRank(out[j])
		if ri != rj {
			return ri < rj
		}
		return out[i] < out[j]
	})
	return out
}

// resolveCrossSide ports _resolve_cross_side_overlaps: of two opposing-side
// barriers that substantially overlap one price band, drop the weaker (by
// tier rank then score). A pass that would drop more than a third of the
// pool is discarded and the input returned unchanged.
func resolveCrossSide(barriers []Barrier) []Barrier {
	drop := make(map[int]bool)
	for i := range barriers {
		if drop[i] {
			continue
		}
		first := barriers[i]
		for j := i + 1; j < len(barriers); j++ {
			if drop[j] || barriers[j].Side == first.Side {
				continue
			}
			second := barriers[j]
			overlap := math.Max(0, math.Min(first.High, second.High)-math.Max(first.Low, second.Low))
			ratio := math.Max(overlapRatio(overlap, first.High-first.Low), overlapRatio(overlap, second.High-second.Low))
			if ratio < crossSideOverlapRatio {
				continue
			}
			if first.Tier.rank() > second.Tier.rank() || (first.Tier.rank() == second.Tier.rank() && first.Score >= second.Score) {
				drop[j] = true
			} else {
				drop[i] = true
			}
			if drop[i] {
				break
			}
		}
	}
	if len(barriers) == 0 || float64(len(drop))/float64(len(barriers)) > crossSideMaxDropFraction {
		return barriers
	}
	out := make([]Barrier, 0, len(barriers)-len(drop))
	for i, b := range barriers {
		if !drop[i] {
			out = append(out, b)
		}
	}
	return out
}

func overlapRatio(overlap, width float64) float64 {
	width = math.Max(0, width)
	if width > 0 {
		return overlap / width
	}
	if overlap > 0 {
		return 1
	}
	return 0
}
