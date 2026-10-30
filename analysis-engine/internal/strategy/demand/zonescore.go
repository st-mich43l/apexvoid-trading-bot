package demand

import (
	"math"
	"strings"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

// The zone score: how much independent evidence sits under a zone. It sets the confluence stars together with the confirmation, so the strategy owns it.
// Points per fact (the values the shared zone score always used):
//
//	fresh (never touched) 3 · touched once 1 · a supply/demand base 1.5 · a key level inside 2 · a round number inside 1 · a sell-side liquidity pool just
//	below 2 · each unswept session low in or just below 2 · price in the discount half of the dealing range 2 · a grade-A bullish sweep into the zone 2
//	· inside a higher-timeframe demand zone 3 · an unbroken trendline through it 1.5
const (
	freshScore        = 3.0
	singleTouchScore  = 1.0
	baseScore         = 1.5
	keyLevelScore     = 2.0
	roundNumberScore  = 1.0
	liquidityScore    = 2.0
	sessionLevelScore = 2.0
	pdPositionScore   = 2.0
	sweepAScore       = 2.0
	htfZoneScore      = 3.0
	trendlineScore    = 1.5
)

// scoreInputs are the facts a zone is scored against, all read from frames.
type scoreInputs struct {
	levels      []techniquezone.Level
	pools       []techniquezone.Pool
	roundStep   float64
	sessions    []techniquezone.SessionRef
	hasEq       bool
	equilibrium float64
	grabs       []techniquezone.Grab
	lineValues  []float64
	pip         float64
	higher      []techniquezone.Zone // the zones of the frames above this one
}

// inputsFor reads a frame's scoring facts. higher are the frames with a longer period than the zone's own frame.
func (s *Strategy) inputsFor(frame *analysiscontext.LegacyFrame, higher []*analysiscontext.LegacyFrame) scoreInputs {
	in := scoreInputs{levels: frame.Levels, pools: frame.Pools, roundStep: s.cfg.RoundStep, sessions: frame.Sessions, grabs: frame.Grabs, pip: s.cfg.PipSize}
	if frame.Range != nil {
		in.hasEq, in.equilibrium = true, float64(frame.Range.Equilibrium)
	}
	last := len(frame.Bars) - 1
	for _, line := range frame.Trendlines {
		if line.BrokenAt == nil {
			in.lineValues = append(in.lineValues, float64(trendline.ValueAt(line, last)))
		}
	}
	for _, h := range higher {
		in.higher = append(in.higher, h.Zones...)
	}
	return in
}

// higherFrames are the frames of the context with a longer period than tf.
func higherFrames(ctx *analysiscontext.MarketContext, tf market.Timeframe) []*analysiscontext.LegacyFrame {
	minutes, _ := tf.Minutes()
	var out []*analysiscontext.LegacyFrame
	for other, frameContext := range ctx.Timeframes {
		if m, ok := other.Minutes(); ok && m > minutes && frameContext != nil && frameContext.Legacy != nil {
			out = append(out, frameContext.Legacy)
		}
	}
	return out
}

// scoreZone is the score of one zone (a "buy" zone is a demand zone, a "sell" zone a supply zone).
func scoreZone(z zone, in scoreInputs) float64 {
	buy := z.Side == "buy"
	score := 0.0
	switch z.Touches {
	case 0:
		score += freshScore
	case 1:
		score += singleTouchScore
	}
	score += baseScore
	for _, level := range in.levels {
		if overlapsLevel(z, level) {
			score += keyLevelScore
			break
		}
	}
	if in.roundStep > 0 && math.Ceil(z.Low/in.roundStep)*in.roundStep <= z.High {
		score += roundNumberScore
	}
	if hasLiquidity(z, in.pools, in.pip) {
		score += liquidityScore
	}
	score += sessionLevelScore * float64(sessionConfluences(z, in.sessions, in.pip))
	if in.hasEq && (buy && z.High <= in.equilibrium || !buy && z.Low >= in.equilibrium) {
		score += pdPositionScore
	}
	if hasSweepA(z, in.grabs, in.pip) {
		score += sweepAScore
	}
	for _, h := range in.higher {
		sameSide := h.Side == "demand" && buy || h.Side == "supply" && !buy
		if sameSide && z.Low >= h.Low() && z.High <= h.High() {
			score += htfZoneScore
			break
		}
	}
	for _, value := range in.lineValues {
		if z.Low <= value && value <= z.High {
			score += trendlineScore
			break
		}
	}
	return score
}

func overlapsLevel(z zone, level techniquezone.Level) bool {
	band := math.Max(level.Band, 0)
	if band == 0 {
		return z.Low <= level.Price && level.Price <= z.High
	}
	return z.Low <= level.Price+band && z.High >= level.Price-band
}

// hasLiquidity: a sell-side pool within the zone's width (at least a pip) below its low (demand), a buy-side pool just above its high (supply).
func hasLiquidity(z zone, pools []techniquezone.Pool, pip float64) bool {
	width := math.Max(z.High-z.Low, 0)
	for _, pool := range pools {
		tolerance := math.Max(pool.Band, math.Max(width, pip))
		if z.Side == "buy" && pool.Side == "sell" && z.Low-tolerance <= pool.Level && pool.Level <= z.Low {
			return true
		}
		if z.Side == "sell" && pool.Side == "buy" && z.High <= pool.Level && pool.Level <= z.High+tolerance {
			return true
		}
	}
	return false
}

// sessionConfluences counts the distinct unswept session levels on the zone's side (lows under a demand zone, highs over a supply zone) inside it or just
// beyond its proximal edge.
func sessionConfluences(z zone, sessions []techniquezone.SessionRef, pip float64) int {
	width := math.Max(z.High-z.Low, 0)
	tolerance := math.Max(width, pip)
	seen := map[string]bool{}
	for _, level := range sessions {
		if level.Swept || seen[level.Name] {
			continue
		}
		low := strings.HasSuffix(level.Name, "_L") || level.Name == "PDL" || level.Name == "PWL"
		high := strings.HasSuffix(level.Name, "_H") || level.Name == "PDH" || level.Name == "PWH"
		if z.Side == "buy" && low {
			if z.Low <= level.Price && level.Price <= z.High || z.Low-tolerance <= level.Price && level.Price <= z.Low {
				seen[level.Name] = true
			}
		}
		if z.Side == "sell" && high {
			if z.Low <= level.Price && level.Price <= z.High || z.High <= level.Price && level.Price <= z.High+tolerance {
				seen[level.Name] = true
			}
		}
	}
	return len(seen)
}

// hasSweepA: a grade-A, non-inducement sweep on the zone's side whose pool points into the zone.
func hasSweepA(z zone, grabs []techniquezone.Grab, pip float64) bool {
	width := math.Max(z.High-z.Low, 0)
	for _, grab := range grabs {
		if grab.Grade != "A" || grab.Inducement {
			continue
		}
		tolerance := math.Max(grab.Pool.Band, math.Max(width, pip))
		if z.Side == "buy" && grab.Direction == "bull" && grab.Pool.Side == "sell" && z.Low-tolerance <= grab.Pool.Level && grab.Pool.Level <= z.High {
			return true
		}
		if z.Side == "sell" && grab.Direction == "bear" && grab.Pool.Side == "buy" && z.Low <= grab.Pool.Level && grab.Pool.Level <= z.High+tolerance {
			return true
		}
	}
	return false
}
