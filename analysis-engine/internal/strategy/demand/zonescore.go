package demand

import (
	"math"
	"strings"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

// The zone score: how much independent evidence sits under a demand zone. It sets the confluence stars together with the confirmation, so the strategy
// owns it. Points per fact:
//
//	fresh (never touched) 3 · touched once 1 · a supply/demand base 1.5 · a key level inside 2 · a round number inside 1 · a sell-side liquidity pool just
//	below 2 · each unswept session low in or just below 2 · the zone in the discount half of the dealing range (pd_position_score, 2) · a grade-A bullish
//	sweep into the zone 2 · inside a higher-timeframe demand zone of this strategy 3 · an unbroken trendline through it 1.5
const (
	freshScore        = 3.0
	singleTouchScore  = 1.0
	baseScore         = 1.5
	keyLevelScore     = 2.0
	roundNumberScore  = 1.0
	liquidityScore    = 2.0
	sessionLevelScore = 2.0
	sweepAScore       = 2.0
	htfZoneScore      = 3.0
	trendlineScore    = 1.5
)

// scoreInputs are the facts a zone is scored against, all read from frames.
type scoreInputs struct {
	levels     []techniquezone.Level
	pools      []techniquezone.Pool
	roundStep  float64
	sessions   []techniquezone.SessionRef
	pd         dealingBand // the dealing range of the zone's frame
	pdScore    float64
	grabs      []techniquezone.Grab
	lineValues []float64
	pip        float64
	higher     []band // this strategy's own zones on the frames above this one
}

// band is a price range: a demand zone this strategy found on a higher frame.
type band struct{ Low, High float64 }

// inputsFor reads a frame's scoring facts. higher are this strategy's own zones on the frames with a longer period than the zone's own frame; pd is the dealing
// range of the zone's frame (dealingRange).
func (s *Strategy) inputsFor(frame *analysiscontext.LegacyFrame, tf market.Timeframe, execBars []market.Candle, higher []band) scoreInputs {
	in := scoreInputs{levels: frame.Levels, pools: frame.Pools, roundStep: s.cfg.RoundStep, sessions: frame.Sessions, grabs: frame.Grabs, pip: s.cfg.PipSize, higher: higher, pdScore: s.cfg.PDPositionScore}
	in.pd = dealingRange(frame.Bars, frame.ATR, tf, execBars, s.cfg.StructurePivotBars)
	last := len(frame.Bars) - 1
	for _, line := range frame.Trendlines {
		if line.BrokenAt == nil {
			in.lineValues = append(in.lineValues, float64(trendline.ValueAt(line, last)))
		}
	}
	return in
}

// scoreZone is the score of one demand zone and whether it was awarded the discount points.
func scoreZone(z zone, in scoreInputs) (score float64, discount bool) {
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
	if in.pd.OK && z.High <= in.pd.equilibrium() {
		score += in.pdScore
		discount = in.pdScore > 0
	}
	if hasSweepA(z, in.grabs, in.pip) {
		score += sweepAScore
	}
	for _, h := range in.higher {
		if z.Low >= h.Low && z.High <= h.High {
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
	return score, discount
}

func overlapsLevel(z zone, level techniquezone.Level) bool {
	band := math.Max(level.Band, 0)
	if band == 0 {
		return z.Low <= level.Price && level.Price <= z.High
	}
	return z.Low <= level.Price+band && z.High >= level.Price-band
}

// hasLiquidity: a sell-side pool in the zone's low or within its width (at least a pip) below it.
func hasLiquidity(z zone, pools []techniquezone.Pool, pip float64) bool {
	width := math.Max(z.High-z.Low, 0)
	for _, pool := range pools {
		tolerance := math.Max(pool.Band, math.Max(width, pip))
		if pool.Side == "sell" && z.Low-tolerance <= pool.Level && pool.Level <= z.Low {
			return true
		}
	}
	return false
}

// sessionConfluences counts the distinct unswept session lows inside the zone or just below it.
func sessionConfluences(z zone, sessions []techniquezone.SessionRef, pip float64) int {
	width := math.Max(z.High-z.Low, 0)
	tolerance := math.Max(width, pip)
	seen := map[string]bool{}
	for _, level := range sessions {
		if level.Swept || seen[level.Name] {
			continue
		}
		if !(strings.HasSuffix(level.Name, "_L") || level.Name == "PDL" || level.Name == "PWL") {
			continue
		}
		if z.Low <= level.Price && level.Price <= z.High || z.Low-tolerance <= level.Price && level.Price <= z.Low {
			seen[level.Name] = true
		}
	}
	return len(seen)
}

// hasSweepA: a grade-A, non-inducement bullish sweep whose pool points into the zone.
func hasSweepA(z zone, grabs []techniquezone.Grab, pip float64) bool {
	width := math.Max(z.High-z.Low, 0)
	for _, grab := range grabs {
		if grab.Grade != "A" || grab.Inducement || grab.Direction != "bull" || grab.Pool.Side != "sell" {
			continue
		}
		tolerance := math.Max(grab.Pool.Band, math.Max(width, pip))
		if z.Low-tolerance <= grab.Pool.Level && grab.Pool.Level <= z.High {
			return true
		}
	}
	return false
}
