package rereplay_test

import (
	"math"
	"math/rand"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// An open scalp is held until its stop or its last target, up to 288 M5 candles (24 hours):
// production's Range Edge winners took up to 15 hours.
const holdBars = 288

// Outcome is one hypothetical trade in R (multiples of its own initial risk).
type Outcome struct {
	Result string  `json:"result"` // target | stop | stop_be | timeout | unfilled | not_executable
	R      float64 `json:"r"`
	Fill   float64 `json:"fill,omitempty"`
	// RiskPips is the executed stop distance (the published invalidation widened to the
	// instrument's scalp floor, as the Algo Bot does).
	RiskPips float64 `json:"risk_pips,omitempty"`
}

// Schedule is an exit ladder: take-profit multiples of the initial risk and the share closed at each.
type Schedule struct {
	Multiples, Ratios []float64
}

var (
	// FX scalps: 1R and 2R, half each, the stop to break-even after the first
	// (config/instruments.yml targeting.target_r_multiples and close_ratios).
	fxLadder = Schedule{[]float64{1, 2}, []float64{.5, .5}}
	// XAU: the 1R-4R 40/20/20/20 ladder.
	xauLadder = Schedule{[]float64{1, 2, 3, 4}, []float64{.4, .2, .2, .2}}
	// Single target at 1R and at 2R, for reading the win rate without the ladder.
	oneR = Schedule{[]float64{1}, []float64{1}}
	twoR = Schedule{[]float64{2}, []float64{1}}
)

func scheduleFor(symbol string) Schedule {
	if symbol == "XAU" {
		return xauLadder
	}
	return fxLadder
}

// simulate places the order when the evaluated candle closes and fills it only on a
// LATER candle that trades to the proximal edge of the entry band (the better of the
// edge and that candle's open). The published invalidation is widened to the
// instrument's scalp floor (a stop below it is not placed by the Algo Bot) and a stop
// beyond its cap is not executable. The fill candle can only stop the trade out, and a
// stop and a target in one candle count as the stop; after the first target the stop
// moves to break-even. It never reads a candle before the one that fills. A simulated
// fill is hypothetical: no spread, slippage, commission or broker behaviour.
func simulate(r *Run, d *Decision, sched Schedule) Outcome {
	bars := r.Bars
	e, ok := r.Idx[d.EvalTime]
	if !ok {
		return Outcome{Result: "unfilled"}
	}
	sell := d.Direction == "SELL"
	px := d.EntryHigh
	if sell {
		px = d.EntryLow
	}
	fillIdx, fill := -1, 0.0
	for j := e + 1; j < len(bars) && bars[j].Time < d.EvalTime+12*3600; j++ {
		b := bars[j]
		if !sell && b.Low <= px {
			fillIdx, fill = j, math.Min(b.Open, px)
			break
		}
		if sell && b.High >= px {
			fillIdx, fill = j, math.Max(b.Open, px)
			break
		}
	}
	if fillIdx < 0 {
		return Outcome{Result: "unfilled"}
	}
	risk := fill - d.Invalidate
	if sell {
		risk = d.Invalidate - fill
	}
	if !(risk > 0) {
		return Outcome{Result: "unfilled"} // already through the invalidation: never placed
	}
	floor := r.StopMinPips * r.Pip
	if risk < floor {
		risk = floor
	}
	if r.StopMaxPips > 0 && risk > r.StopMaxPips*r.Pip+1e-12 {
		return Outcome{Result: "not_executable", RiskPips: risk / r.Pip}
	}
	stop := fill - risk
	dir := 1.0
	if sell {
		stop, dir = fill+risk, -1
	}
	o := Outcome{Fill: fill, RiskPips: risk / r.Pip}
	stopHit := func(b market.Candle, s float64) bool {
		if sell {
			return b.High >= s
		}
		return b.Low <= s
	}
	reached := func(b market.Candle, price float64) bool {
		if sell {
			return b.Low <= price
		}
		return b.High >= price
	}
	if stopHit(bars[fillIdx], stop) {
		o.Result, o.R = "stop", -1
		return o
	}
	end := minInt(len(bars)-1, fillIdx+holdBars)
	rem, total, cur, next, be := 1.0, 0.0, stop, 0, false
	for k := fillIdx + 1; k <= end; k++ {
		b := bars[k]
		if stopHit(b, cur) {
			total += rem * (dir * (cur - fill) / risk)
			o.Result, o.R = "stop", total
			if be {
				o.Result = "stop_be"
			}
			return o
		}
		for next < len(sched.Multiples) && reached(b, fill+dir*sched.Multiples[next]*risk) {
			total += sched.Ratios[next] * sched.Multiples[next]
			rem -= sched.Ratios[next]
			if next == 0 && len(sched.Multiples) > 1 {
				cur, be = fill, true
			}
			next++
		}
		if next == len(sched.Multiples) {
			o.Result, o.R = "target", total
			return o
		}
	}
	o.Result, o.R = "timeout", total+rem*dir*(bars[end].Close-fill)/risk
	return o
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// Stat summarises hypothetical outcomes in R with a seeded bootstrap 95% interval.
type Stat struct {
	N         int     `json:"n"`
	Mean      float64 `json:"mean_r"`
	Low       float64 `json:"ci95_low"`
	High      float64 `json:"ci95_high"`
	WinRate   float64 `json:"win_rate"`
	Worst     float64 `json:"worst_r"`
	SumR      float64 `json:"sum_r"`
	Unfilled  int     `json:"unfilled"`
	NotExec   int     `json:"not_executable"`
	Decisions int     `json:"decisions"`
}

func summarize(outcomes []Outcome, decisions int) Stat {
	s := Stat{Decisions: decisions}
	var rs []float64
	for _, o := range outcomes {
		switch o.Result {
		case "unfilled":
			s.Unfilled++
		case "not_executable":
			s.NotExec++
		default:
			rs = append(rs, o.R)
		}
	}
	s.N = len(rs)
	if s.N == 0 {
		return s
	}
	wins := 0
	for _, v := range rs {
		s.SumR += v
		if v > 0 {
			wins++
		}
		if v < s.Worst {
			s.Worst = v
		}
	}
	s.Mean = round(s.SumR/float64(s.N), 3)
	s.WinRate = round(float64(wins)/float64(s.N), 3)
	s.SumR = round(s.SumR, 2)
	rng := rand.New(rand.NewSource(20261014))
	means := make([]float64, 2000)
	for i := range means {
		sum := 0.0
		for j := 0; j < s.N; j++ {
			sum += rs[rng.Intn(s.N)]
		}
		means[i] = sum / float64(s.N)
	}
	sort.Float64s(means)
	s.Low, s.High = round(means[int(0.025*float64(len(means)))], 3), round(means[int(0.975*float64(len(means)))], 3)
	return s
}

// thin keeps the first decision per symbol, direction and hour: a stream of repeats of one
// thesis is one opportunity, not several.
func thin(ds []*Decision) []*Decision {
	seen := map[string]bool{}
	var out []*Decision
	for _, d := range ds {
		key := d.Symbol + "|" + d.Direction + "|" + itoa(d.EvalTime/3600)
		if !seen[key] {
			seen[key] = true
			out = append(out, d)
		}
	}
	return out
}

func itoa(v int64) string {
	if v == 0 {
		return "0"
	}
	neg := v < 0
	if neg {
		v = -v
	}
	var b []byte
	for v > 0 {
		b = append([]byte{byte('0' + v%10)}, b...)
		v /= 10
	}
	if neg {
		b = append([]byte{'-'}, b...)
	}
	return string(b)
}
