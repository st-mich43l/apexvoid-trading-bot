package ifvgreplay_test

import (
	"fmt"
	"math"
	"sort"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// row is one published opportunity with its hypothetical outcome and the
// technical measurements the comparison reports.
type row struct {
	position
	Window           string // first | second half of the capture timeline
	Out              outcome
	PlannedRR        float64
	RiskPips         float64
	RewardATR        float64
	ZoneATR          float64
	StopInsideRecent bool // stop inside the range of the 12 candles before the observation
	// v2 only: how late, in candles, the confirmation came after the inversion.
	ConfirmDelayBars float64
	// Overall is Quality.Overall as live arbitration sees it.
	Overall float64
	Stars   int
}

func absDiff(a, b int64) int64 {
	if a > b {
		return a - b
	}
	return b - a
}

// finish simulates the position and derives its technical measurements.
func finish(r *row, bars []market.Candle) {
	r.Out = simulate(bars, r.position)
	prox := r.proximal()
	risk, reward := math.Abs(prox-r.Stop), math.Abs(r.Target-prox)
	if risk > 0 {
		r.PlannedRR = reward / risk
	}
	pip := 0.0001
	switch {
	case prox > 1000:
		pip = 0.1
	case prox > 50:
		pip = 0.01
	}
	r.RiskPips = risk / pip
	if r.ATR > 0 {
		r.RewardATR = reward / r.ATR
		r.ZoneATR = (r.EntryHigh - r.EntryLow) / r.ATR
	}
	// Is the stop inside the range of the 12 candles before the observation (the
	// swing the retest just made)? A stop there is inside the noise it must survive.
	lo, hi := math.Inf(1), math.Inf(-1)
	for _, b := range bars {
		if b.Time > r.ObservedAt-12*300 && b.Time <= r.ObservedAt {
			lo, hi = math.Min(lo, b.Low), math.Max(hi, b.High)
		}
	}
	if !math.IsInf(lo, 0) {
		if r.Direction == market.Sell {
			r.StopInsideRecent = r.Stop <= hi
		} else {
			r.StopInsideRecent = r.Stop >= lo
		}
	}
}

func median(values []float64) float64 {
	if len(values) == 0 {
		return math.NaN()
	}
	sort.Float64s(values)
	return values[len(values)/2]
}

type stats struct {
	N, Filled, Target, Stop, Timeout, Unfilled, Ambiguous, InsideRecent int
	MeanR, MedianRR, MedianRiskPips, MedianRewardATR, MedianZoneATR     float64
	MeanMFE, MeanMAE, MedianBarsToFill                                  float64
}

func summarise(rows []row, filter func(row) bool) stats {
	var s stats
	var rs, rrs, risks, rewards, zones, mfe, mae, fillBars []float64
	for _, r := range rows {
		if filter != nil && !filter(r) {
			continue
		}
		s.N++
		rrs, risks, rewards, zones = append(rrs, r.PlannedRR), append(risks, r.RiskPips), append(rewards, r.RewardATR), append(zones, r.ZoneATR)
		if r.StopInsideRecent {
			s.InsideRecent++
		}
		switch r.Out.Result {
		case "unfilled", "invalid":
			s.Unfilled++
			continue
		case "target":
			s.Target++
		case "stop":
			s.Stop++
		case "timeout":
			s.Timeout++
		}
		s.Filled++
		if r.Out.SameBarAmbiguous {
			s.Ambiguous++
		}
		rs, mfe, mae, fillBars = append(rs, r.Out.R), append(mfe, r.Out.MFE), append(mae, r.Out.MAE), append(fillBars, float64(r.Out.BarsToFill))
	}
	mean := func(v []float64) float64 {
		if len(v) == 0 {
			return math.NaN()
		}
		sum := 0.0
		for _, x := range v {
			sum += x
		}
		return sum / float64(len(v))
	}
	s.MeanR, s.MeanMFE, s.MeanMAE = mean(rs), mean(mfe), mean(mae)
	s.MedianRR, s.MedianRiskPips, s.MedianRewardATR, s.MedianZoneATR = median(rrs), median(risks), median(rewards), median(zones)
	s.MedianBarsToFill = median(fillBars)
	return s
}

func fmtStats(s stats) string {
	if s.N == 0 {
		return "n=0"
	}
	return fmt.Sprintf("n=%d filled=%d (target %d / stop %d / timeout %d, same-bar ambiguous %d) unfilled=%d meanR=%.2f MFE=%.2fR MAE=%.2fR | planned RR med %.2f, risk med %.1f pips, reward med %.1f ATR, zone med %.2f ATR, median bars-to-fill %.0f, stop inside the last-12-candle range %d",
		s.N, s.Filled, s.Target, s.Stop, s.Timeout, s.Ambiguous, s.Unfilled, s.MeanR, s.MeanMFE, s.MeanMAE, s.MedianRR, s.MedianRiskPips, s.MedianRewardATR, s.MedianZoneATR, s.MedianBarsToFill, s.InsideRecent)
}

func sortedCounts(m map[string]int) string {
	if len(m) == 0 {
		return "none"
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, len(keys))
	for i, k := range keys {
		parts[i] = fmt.Sprintf("%s=%d", k, m[k])
	}
	return strings.Join(parts, ", ")
}
