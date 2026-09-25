package structure

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// Displacement is a reusable, multi-candle expansion model — source task
// §26: "do not reduce displacement to one large candle." RangeATR is the
// run's net directional move (|close(end) - open(start)|), ATR-normalized;
// BodyDominance is the fraction of candles in the run whose body is a
// large share of their own range (i.e. "strong", not mostly wick) — the
// same two-part test app/analysis/zones.py::displacement already proves
// in production (config/analysis.yml's structure.break.displacement_*
// values are that function's real k=1.5/body_frac=0.55, not fresh
// guesses), extended here to a shrinking multi-candle window instead of
// Python's own fixed leg grouping.
type Displacement struct {
	Direction market.Direction

	StartTime int64
	EndTime   int64

	RangeATR      float64
	BodyDominance float64

	Strength float64
}

// DisplacementConfig mirrors config/analysis.yml's analysis.structure.break
// displacement_range_atr/displacement_body_dominance leaves.
type DisplacementConfig struct {
	RangeATR      float64 // net move (ATR units) a run must clear
	BodyDominance float64 // per-candle body/range ratio a "strong" candle must clear
}

// DetectDisplacement scans candles[from:] for the SHORTEST prefix run (1
// candle up to maxBars) that qualifies as a Displacement, preferring the
// shortest qualifying run so a single huge candle is recognized
// immediately rather than only after maxBars have closed. Returns false
// if no prefix up to maxBars qualifies. atr must be the canonical ATR
// value at index from (indicator.CanonicalATR) — never independently
// recomputed.
func DetectDisplacement(candles []market.Candle, from, maxBars int, atr float64, cfg DisplacementConfig) (Displacement, bool) {
	if atr <= 0 || from < 0 || from >= len(candles) {
		return Displacement{}, false
	}
	end := from + maxBars
	if end > len(candles) {
		end = len(candles)
	}
	run := candles[from:end]
	if len(run) == 0 {
		return Displacement{}, false
	}

	for n := 1; n <= len(run); n++ {
		sub := run[:n]
		netMove := sub[n-1].Close - sub[0].Open
		rangeATR := absf(netMove) / atr
		if rangeATR < cfg.RangeATR {
			continue
		}
		strongCount := 0
		for _, c := range sub {
			r := c.Range()
			if r <= 0 {
				continue
			}
			if c.Body()/r >= cfg.BodyDominance {
				strongCount++
			}
		}
		bodyDominance := float64(strongCount) / float64(len(sub))
		minStrong := len(sub) / 2
		if minStrong < 1 {
			minStrong = 1
		}
		if strongCount < minStrong {
			continue
		}
		direction := market.Buy
		if netMove < 0 {
			direction = market.Sell
		}
		return Displacement{
			Direction: direction, StartTime: sub[0].Time, EndTime: sub[n-1].Time,
			RangeATR: rangeATR, BodyDominance: bodyDominance, Strength: rangeATR * bodyDominance,
		}, true
	}
	return Displacement{}, false
}
