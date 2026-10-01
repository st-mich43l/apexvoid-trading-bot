// Package candle ports Python's Candle Confirmation V2 evidence layer.
//
// The result is descriptive technical context only.  It does not decide
// eligibility, risk, entry, or execution; those remain separate policy
// concerns.  All calculations use the closed bars supplied by the caller.
package candle

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

const Version = 2

type Geometry struct {
	Open, High, Low, Close float64
	RangePrice, BodyPrice  float64
	UpperWick, LowerWick   float64
	BodyFraction           float64
	UpperWickFraction      float64
	LowerWickFraction      float64
	CloseLocation          float64
	BodyATR                float64
	RangeATR               float64
}

func clamp(v float64) float64 {
	if v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}

func quality(v, minimum, strong float64) float64 {
	if strong <= minimum {
		if v >= minimum {
			return 1
		}
		return 0
	}
	return clamp((v - minimum) / (strong - minimum))
}

func atrQuality(v, minimum float64) float64 {
	if v <= 0 || minimum <= 0 {
		return 0
	}
	return clamp(v / (minimum * 4))
}

func GeometryOf(c market.Candle, atr float64) Geometry {
	rng := c.High - c.Low
	body := math.Abs(c.Close - c.Open)
	upper := math.Max(0, c.High-math.Max(c.Open, c.Close))
	lower := math.Max(0, math.Min(c.Open, c.Close)-c.Low)
	g := Geometry{Open: c.Open, High: c.High, Low: c.Low, Close: c.Close, RangePrice: math.Max(0, rng), BodyPrice: body, UpperWick: upper, LowerWick: lower, CloseLocation: .5}
	if rng > 0 {
		g.BodyFraction = clamp(body / rng)
		g.UpperWickFraction = clamp(upper / rng)
		g.LowerWickFraction = clamp(lower / rng)
		g.CloseLocation = clamp((c.Close - c.Low) / rng)
	}
	if atr > 0 {
		g.BodyATR = body / atr
		g.RangeATR = math.Max(0, rng) / atr
	}
	return g
}

func (g Geometry) Bullish() bool { return g.Close > g.Open }
func (g Geometry) Bearish() bool { return g.Close < g.Open }

type Rejection struct {
	Score, WickFraction, BodyFraction, CloseLocation float64
	Patterns                                         []string
	Sweep, Reclaim                                   bool
	SweepPenetrationATR, ReclaimDepthATR             *float64
}

type Displacement struct {
	Score, BodyATR, RangeATR, BodyDominance, CloseLocation float64
	Patterns                                               []string
	Reclaim, Engulfing                                     bool
	ReclaimDepthATR, EngulfingQuality                      *float64
}

type Indecision struct {
	Doji, SpinningTop, InsideBar   bool
	BodyFraction, CompressionScore float64
}

type Sequence struct {
	Score    float64
	Patterns []string
	Bars     int
}

type Evidence struct {
	Version                             int
	Direction                           string
	Rejection                           *Rejection
	Displacement                        *Displacement
	Sequence                            *Sequence
	Indecision                          *Indecision
	BaseScore, SynergyBonus, FinalScore float64
	PrimaryPattern                      string
	AllPatterns                         []string
	Geometry                            Geometry
}

func penetration(direction string, c Geometry, level, atr float64) *float64 {
	if atr <= 0 {
		return nil
	}
	v := (level - c.Low) / atr
	if direction == "SELL" {
		v = (c.High - level) / atr
	}
	if v <= 0 {
		return nil
	}
	return &v
}

func reclaim(direction string, c Geometry, level, atr float64) *float64 {
	if atr <= 0 {
		return nil
	}
	v := (c.Close - level) / atr
	if direction == "SELL" {
		v = (level - c.Close) / atr
	}
	if v <= 0 {
		return nil
	}
	return &v
}

func touched(g Geometry, low, high *float64) bool {
	return low == nil || high == nil || (g.Low <= *high+1e-12 && g.High >= *low-1e-12)
}

func contains(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}

func rejectionEvidence(g Geometry, direction string, atr, level float64, zoneLow, zoneHigh *float64) *Rejection {
	if direction != "BUY" && direction != "SELL" || !touched(g, zoneLow, zoneHigh) {
		return nil
	}
	directionalWick := g.LowerWickFraction
	oppositeWick := g.UpperWickFraction
	if direction == "SELL" {
		directionalWick, oppositeWick = g.UpperWickFraction, g.LowerWickFraction
	}
	sweep := penetration(direction, g, level, atr)
	reclaimDepth := reclaim(direction, g, level, atr)
	closeForDirection := g.CloseLocation
	closeMinimum := .65
	if direction == "SELL" {
		closeForDirection, closeMinimum = 1-g.CloseLocation, .65
	}
	patterns := make([]string, 0, 4)
	if directionalWick >= .30 {
		patterns = append(patterns, "wick_rejection")
	}
	if g.BodyFraction <= .30 && g.BodyPrice > 0 && directionalWick/math.Max(g.BodyFraction, 1e-9) >= 2 && oppositeWick <= .25 {
		if direction == "BUY" {
			patterns = append(patterns, "hammer")
		} else {
			patterns = append(patterns, "shooting_star")
		}
	}
	if g.BodyFraction <= .20 && directionalWick >= .66 && oppositeWick <= .15 {
		patterns = append(patterns, "pin_bar")
	}
	if g.BodyFraction <= .30 && oppositeWick/math.Max(g.BodyFraction, 1e-9) >= 2 && directionalWick <= .25 && reclaimDepth != nil {
		patterns = append(patterns, "inverted_hammer")
	}
	if sweep != nil && reclaimDepth != nil {
		patterns = append(patterns, "sweep_reclaim")
	}
	if len(patterns) == 0 {
		return nil
	}
	score := clamp(.30*quality(directionalWick, .30, .55) + .20*quality(closeForDirection, closeMinimum, 1) + .15*clamp(1-g.BodyFraction/.45) + .20*atrQuality(value(reclaimDepth), .05) + .15*atrQuality(value(sweep), .05))
	return &Rejection{Score: score, Patterns: patterns, WickFraction: directionalWick, BodyFraction: g.BodyFraction, CloseLocation: g.CloseLocation, Sweep: sweep != nil, SweepPenetrationATR: sweep, Reclaim: reclaimDepth != nil, ReclaimDepthATR: reclaimDepth}
}

func value(v *float64) float64 {
	if v == nil {
		return 0
	}
	return *v
}

func engulfing(g Geometry, prior *Geometry, direction string, minimumRange float64) (bool, *float64) {
	if prior == nil || prior.RangePrice <= 0 || g.RangeATR < minimumRange {
		return false, nil
	}
	lo, hi := math.Min(g.Open, g.Close), math.Max(g.Open, g.Close)
	plo, phi := math.Min(prior.Open, prior.Close), math.Max(prior.Open, prior.Close)
	if lo > plo || hi < phi {
		return false, nil
	}
	if direction == "BUY" && !g.Bullish() || direction == "SELL" && !g.Bearish() {
		return false, nil
	}
	priorOpposite := prior.BodyFraction < .1 || direction == "BUY" && prior.Bearish() || direction == "SELL" && prior.Bullish()
	if !priorOpposite {
		return false, nil
	}
	q := clamp((g.BodyPrice / math.Max(prior.BodyPrice, 1e-9) / 2) * func() float64 {
		if direction == "BUY" {
			return g.CloseLocation
		}
		return 1 - g.CloseLocation
	}())
	return true, &q
}

func displacementEvidence(g Geometry, prior *Geometry, direction string, atr, level float64, hasLevel bool) *Displacement {
	if direction != "BUY" && direction != "SELL" {
		return nil
	}
	engulf, engulfQ := engulfing(g, prior, direction, .50)
	var reclaimDepth *float64
	if hasLevel {
		reclaimDepth = reclaim(direction, g, level, atr)
	}
	directional := g.Bullish()
	if direction == "SELL" {
		directional = g.Bearish()
	}
	closeForDirection := g.CloseLocation
	if direction == "SELL" {
		closeForDirection = 1 - g.CloseLocation
	}
	patterns := make([]string, 0, 5)
	if directional && closeForDirection >= .70 {
		patterns = append(patterns, "strong_close")
	}
	if directional && g.RangeATR >= .40 {
		patterns = append(patterns, "body_close")
	}
	if engulf {
		patterns = append(patterns, "engulfing")
	}
	if reclaimDepth != nil {
		patterns = append(patterns, "strong_reclaim")
	}
	if directional && g.BodyFraction >= .55 && g.BodyATR >= .30 {
		patterns = append(patterns, "displacement_candle")
	}
	if len(patterns) == 0 {
		return nil
	}
	bodyDominance := g.BodyFraction
	if !directional {
		bodyDominance = 0
	}
	score := clamp(.30*quality(bodyDominance, .55, .85) + .25*atrQuality(g.RangeATR, .40) + .20*quality(func() float64 {
		if directional {
			return closeForDirection
		}
		return 0
	}(), .70, 1) + .15*atrQuality(value(reclaimDepth), .05) + .10*value(engulfQ))
	return &Displacement{Score: score, Patterns: patterns, BodyATR: g.BodyATR, RangeATR: g.RangeATR, BodyDominance: bodyDominance, CloseLocation: g.CloseLocation, Reclaim: reclaimDepth != nil, ReclaimDepthATR: reclaimDepth, Engulfing: engulf, EngulfingQuality: engulfQ}
}

func indecision(g, prior *Geometry) *Indecision {
	doji := g.BodyFraction <= .10
	spinning := !doji && g.BodyFraction <= .35 && math.Abs(g.UpperWickFraction-g.LowerWickFraction) <= .20
	inside := prior != nil && g.High <= prior.High && g.Low >= prior.Low
	return &Indecision{Doji: doji, SpinningTop: spinning, InsideBar: inside, BodyFraction: g.BodyFraction, CompressionScore: clamp(1 - atrQuality(g.RangeATR, .40))}
}

func sequenceScore(bars []Geometry, direction string, atr float64, level *float64) float64 {
	first, last := bars[0], bars[len(bars)-1]
	middle := bars[1 : len(bars)-1]
	if len(middle) == 0 {
		middle = []Geometry{bars[len(bars)/2]}
	}
	avg := 0.0
	for _, b := range middle {
		avg += b.BodyFraction
	}
	avg /= float64(len(middle))
	firstMove := math.Abs(first.Open - first.Close)
	recovery := 0.0
	if firstMove > 0 {
		if direction == "BUY" {
			recovery = (last.Close - first.Close) / firstMove
		} else {
			recovery = (first.Close - last.Close) / firstMove
		}
	}
	reclaimQuality := 0.0
	if level != nil {
		reclaimQuality = atrQuality(value(reclaim(direction, last, *level, atr)), .05)
	}
	return clamp(.25*atrQuality(first.BodyATR, .30) + .20*clamp(1-avg/.30) + .30*atrQuality(last.BodyATR, .25) + .15*quality(math.Max(0, recovery), .50, 1) + .10*reclaimQuality)
}

func sequences(bars []Geometry, direction string, atr float64, level *float64) *Sequence {
	if len(bars) < 3 {
		return nil
	}
	patterns := []string{}
	if len(bars) == 3 {
		a, b, c := bars[0], bars[1], bars[2]
		move := math.Abs(a.Open - a.Close)
		if move > 0 && b.BodyFraction <= .30 && a.BodyATR >= .30 && c.BodyATR >= .25 {
			if direction == "BUY" && a.Bearish() && c.Bullish() && (c.Close-a.Close)/move >= .50 {
				patterns = append(patterns, "morning_star")
			}
			if direction == "SELL" && a.Bullish() && c.Bearish() && (a.Close-c.Close)/move >= .50 {
				patterns = append(patterns, "evening_star")
			}
		}
		if level != nil {
			if penetration(direction, a, *level, atr) != nil && b.BodyFraction <= .30 && c.BodyATR >= .25 && value(reclaim(direction, c, *level, atr)) > 0 {
				patterns = append(patterns, "sweep_indecision_displacement")
			}
		}
	}
	if len(bars) >= 3 {
		compression := bars[:len(bars)-1]
		breakout := bars[len(bars)-1]
		if len(compression) >= 2 && len(compression) <= 4 {
			hi, lo := compression[0].High, compression[0].Low
			avg := 0.0
			for _, b := range compression {
				hi = math.Max(hi, b.High)
				lo = math.Min(lo, b.Low)
				avg += b.BodyFraction
			}
			avg /= float64(len(compression))
			if atr > 0 && (hi-lo)/atr <= .80 && avg <= .35 && breakout.BodyATR >= .30 && ((direction == "BUY" && breakout.Bullish() && breakout.Close > hi) || (direction == "SELL" && breakout.Bearish() && breakout.Close < lo)) {
				patterns = append(patterns, "compression_break")
			}
		}
	}
	if len(patterns) == 0 {
		return nil
	}
	return &Sequence{Score: sequenceScore(bars, direction, atr, level), Patterns: unique(patterns), Bars: len(bars)}
}

func unique(xs []string) []string {
	out := make([]string, 0, len(xs))
	for _, x := range xs {
		if !contains(out, x) {
			out = append(out, x)
		}
	}
	return out
}

func mergeSequences(candidates []*Sequence) *Sequence {
	if len(candidates) == 0 {
		return nil
	}
	patterns := []string{}
	score := 0.0
	bars := 0
	for _, candidate := range candidates {
		if candidate == nil {
			continue
		}
		patterns = append(patterns, candidate.Patterns...)
		score += candidate.Score
		if candidate.Bars > bars {
			bars = candidate.Bars
		}
	}
	if len(patterns) == 0 {
		return nil
	}
	return &Sequence{Score: score / float64(len(candidates)), Patterns: unique(patterns), Bars: bars}
}

func Evaluate(bars []market.Candle, direction string, atr, level float64, zoneLow, zoneHigh *float64) *Evidence {
	direction = normalize(direction)
	if len(bars) == 0 || (direction != "BUY" && direction != "SELL") || atr <= 0 {
		return nil
	}
	gs := make([]Geometry, len(bars))
	for i, b := range bars {
		gs[i] = GeometryOf(b, atr)
	}
	current := gs[len(gs)-1]
	var prior *Geometry
	if len(gs) > 1 {
		prior = &gs[len(gs)-2]
	}
	rejection := rejectionEvidence(current, direction, atr, level, zoneLow, zoneHigh)
	displacement := displacementEvidence(current, prior, direction, atr, level, zoneLow != nil && zoneHigh != nil)
	ind := indecision(&current, prior)
	sequenceCandidates := []*Sequence{}
	for _, n := range []int{5, 4, 3} {
		if len(gs) >= n {
			if x := sequences(gs[len(gs)-n:], direction, atr, func() *float64 {
				if zoneLow == nil || zoneHigh == nil {
					return nil
				}
				if direction == "BUY" {
					return zoneLow
				}
				return zoneHigh
			}()); x != nil {
				sequenceCandidates = append(sequenceCandidates, x)
			}
		}
	}
	seq := mergeSequences(sequenceCandidates)
	if rejection == nil && displacement == nil && seq == nil {
		return nil
	}
	weighted, active := 0.0, 0.0
	if rejection != nil {
		weighted += rejection.Score * .30
		active += .30
	}
	if displacement != nil {
		weighted += displacement.Score * .45
		active += .45
	}
	if seq != nil {
		weighted += seq.Score * .25
		active += .25
	}
	base := weighted / active
	synergy := 0.0
	if rejection != nil && displacement != nil {
		synergy += .08
	}
	if rejection != nil && rejection.Sweep && rejection.Reclaim {
		synergy += .10
	}
	if seq != nil && displacement != nil {
		synergy += .06
	}
	if synergy > .12 {
		synergy = .12
	}
	patterns := []string{}
	if rejection != nil {
		patterns = append(patterns, rejection.Patterns...)
	}
	if displacement != nil {
		patterns = append(patterns, displacement.Patterns...)
	}
	if seq != nil {
		patterns = append(patterns, seq.Patterns...)
	}
	patterns = unique(patterns)
	primary := ""
	for _, p := range []string{"sweep_reclaim", "sweep_indecision_displacement", "strong_reclaim", "engulfing", "morning_star", "evening_star", "strong_close", "hammer", "shooting_star", "pin_bar", "wick_rejection", "compression_break", "displacement_candle", "body_close", "inverted_hammer"} {
		if contains(patterns, p) {
			primary = p
			break
		}
	}
	return &Evidence{Version: Version, Direction: direction, Rejection: rejection, Displacement: displacement, Sequence: seq, Indecision: ind, BaseScore: base, SynergyBonus: synergy, FinalScore: math.Min(1, base+synergy), PrimaryPattern: primary, AllPatterns: patterns, Geometry: current}
}

func normalize(direction string) string {
	if direction == "buy" || direction == "Buy" {
		return "BUY"
	}
	if direction == "sell" || direction == "Sell" {
		return "SELL"
	}
	return direction
}
