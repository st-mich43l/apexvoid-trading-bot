package czreplay_test

import (
	"math"
	"math/rand"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

const holdBars = 96 // a setup is held at most 96 M5 candles (8 hours)

// Outcome is one hypothetical trade in R (multiples of its own initial risk).
type Outcome struct {
	Result string  `json:"result"` // target | stop | stop_be | timeout | unfilled | invalid
	R      float64 `json:"r"`
	MFE    float64 `json:"mfe_r"`
	MAE    float64 `json:"mae_r"`
	Fill   float64 `json:"fill,omitempty"`
}

// simulate places the order when the evaluated candle closes and fills it only on
// a LATER candle that trades to the proximal edge of the entry band (the fill is
// the better of the edge and that candle's open); the fill candle can only stop
// the trade out; a stop and a target in one candle count as the stop.
//
//	ladder=false: one target, the published one, with the published stop;
//	ladder=true : 1R/2R/3R/4R closing 40/20/20/20, the stop moved to break-even
//	              after the first, with the stop widened to minRisk when tighter
//	              (the Algo Bot widens a stop below the instrument's floor).
//
// It never reads a candle before the one that fills, and a simulated fill is
// hypothetical: no spread, slippage, commission or broker behaviour.
func simulate(bars []market.Candle, idx map[int64]int, d *Decision, ladder bool, minRisk float64) Outcome {
	e, ok := idx[d.EvalTime]
	if !ok {
		return Outcome{Result: "invalid"}
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
	stop := d.Stop
	risk := fill - stop
	if sell {
		risk = stop - fill
	}
	// A candle that opens beyond the stop never gives the order a valid fill: the
	// market is already through the invalidation, and the Algo Bot does not place it.
	if !(risk > 0) {
		return Outcome{Result: "unfilled"}
	}
	if ladder && risk < minRisk {
		risk = minRisk
		if sell {
			stop = fill + risk
		} else {
			stop = fill - risk
		}
	}
	o := Outcome{Fill: fill}
	dir := 1.0
	if sell {
		dir = -1
	}
	excursion := func(b market.Candle) {
		fav, adv := b.High-fill, fill-b.Low
		if sell {
			fav, adv = fill-b.Low, b.High-fill
		}
		o.MFE, o.MAE = math.Max(o.MFE, fav/risk), math.Max(o.MAE, adv/risk)
	}
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
	excursion(bars[fillIdx])
	if stopHit(bars[fillIdx], stop) {
		o.Result, o.R = "stop", -1
		return o
	}
	end := math.Min(float64(len(bars)-1), float64(fillIdx+holdBars))
	last := bars[int(end)]
	if !ladder {
		if !((sell && d.Target < fill) || (!sell && d.Target > fill)) {
			return Outcome{Result: "invalid"}
		}
		reward := math.Abs(d.Target - fill)
		for k := fillIdx + 1; k <= int(end); k++ {
			b := bars[k]
			excursion(b)
			if stopHit(b, stop) {
				o.Result, o.R = "stop", -1
				return o
			}
			if reached(b, d.Target) {
				o.Result, o.R = "target", reward/risk
				return o
			}
		}
		o.Result, o.R = "timeout", dir*(last.Close-fill)/risk
		return o
	}
	ratios := []float64{0.4, 0.2, 0.2, 0.2}
	rem, total, cur, be := 1.0, 0.0, stop, false
	for k := fillIdx + 1; k <= int(end); k++ {
		b := bars[k]
		excursion(b)
		if stopHit(b, cur) {
			total += rem * (dir * (cur - fill) / risk)
			o.Result, o.R = "stop", total
			if be {
				o.Result = "stop_be"
			}
			return o
		}
		for n := 0; n < 4; n++ {
			if ratios[n] == 0 {
				continue
			}
			if reached(b, fill+dir*float64(n+1)*risk) {
				total += ratios[n] * float64(n+1)
				rem -= ratios[n]
				ratios[n] = 0
				if n == 0 {
					cur, be = fill, true
				}
			}
		}
		if rem <= 1e-9 {
			o.Result, o.R = "target", total
			return o
		}
	}
	total += rem * dir * (last.Close - fill) / risk
	o.Result, o.R = "timeout", total
	return o
}

// Stat is a mean in R with a seeded 95 % bootstrap interval.
type Stat struct {
	N    int     `json:"n"`
	Mean float64 `json:"mean_r"`
	Lo   float64 `json:"ci95_lo"`
	Hi   float64 `json:"ci95_hi"`
	Wins int     `json:"wins"`
}

func stat(rs []float64) Stat {
	s := Stat{N: len(rs)}
	if len(rs) == 0 {
		return s
	}
	sum := 0.0
	for _, r := range rs {
		sum += r
		if r > 0 {
			s.Wins++
		}
	}
	s.Mean = sum / float64(len(rs))
	rnd := rand.New(rand.NewSource(20261013))
	means := make([]float64, 2000)
	for i := range means {
		t := 0.0
		for range rs {
			t += rs[rnd.Intn(len(rs))]
		}
		means[i] = t / float64(len(rs))
	}
	sort.Float64s(means)
	s.Lo, s.Hi = means[49], means[1949]
	return s
}

func round(v float64, places int) float64 {
	p := math.Pow(10, float64(places))
	return math.Round(v*p) / p
}

func median(xs []float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	c := append([]float64(nil), xs...)
	sort.Float64s(c)
	if len(c)%2 == 1 {
		return c[len(c)/2]
	}
	return (c[len(c)/2-1] + c[len(c)/2]) / 2
}
