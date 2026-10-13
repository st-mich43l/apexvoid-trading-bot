package confluencezone

import (
	"fmt"
	"math"
	"math/rand"
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structuralconfluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// scenario is a random closed-candle path around 4100 with a random population of
// structures near it, confirmable at random times, and opposing liquidity.
func scenario(r *rand.Rand) Input {
	n := 20 + r.Intn(40)
	price := 4100 + r.Float64()*10
	bars := make([]market.Candle, n)
	for i := range bars {
		o := price
		c := o + (r.Float64()-0.5)*4
		hi := math.Max(o, c) + r.Float64()*2.5
		lo := math.Min(o, c) - r.Float64()*2.5
		bars[i] = market.Candle{Time: t0 + int64(i)*300, Open: o, High: hi, Low: lo, Close: c, Volume: 1}
		price = c
	}
	var facts []structuralconfluence.Fact
	for i, k := 0, 2+r.Intn(7); i < k; i++ {
		side := structuralconfluence.Buy
		if r.Intn(2) == 0 {
			side = structuralconfluence.Sell
		}
		low := price - 8 + r.Float64()*16
		avail := t0 + int64(r.Intn(n+8))*300 - 3000
		f := fact(fmt.Sprintf("f%d", i), side, low, low+0.5+r.Float64()*7, avail, fmt.Sprintf("o%d", r.Intn(5)))
		f.Verified = r.Intn(12) != 0
		f.Touches = r.Intn(3)
		facts = append(facts, f)
	}
	var pools []techniquezone.Pool
	for i := 0; i < 1+r.Intn(4); i++ {
		side := "buy"
		if r.Intn(2) == 0 {
			side = "sell"
		}
		pools = append(pools, techniquezone.Pool{Side: side, Level: price - 40 + r.Float64()*80})
	}
	return Input{Bars: bars, ATR: 2 + r.Float64()*4, PeriodSeconds: 300, Facts: facts, Pools: pools}
}

func propertyConfig(t *testing.T) Config {
	return testConfig(t, map[string]any{"execution_stop_max_pips": 150.0})
}

func TestEverySetupSatisfiesTheTechnicalContract(t *testing.T) {
	cfg := propertyConfig(t)
	r := rand.New(rand.NewSource(21))
	accepted := map[market.Direction]int{}
	for trial := 0; trial < 1500; trial++ {
		in := scenario(r)
		a := Detect(cfg, in)
		last := len(in.Bars) - 1
		for _, s := range a.Setups {
			accepted[s.Direction]++
			buy := s.Direction == market.Buy
			sign := 1.0
			if !buy {
				sign = -1
			}
			// Mirror to the long side so one set of inequalities covers both.
			stop, entryLow, entryHigh, target := sign*s.Stop, sign*s.EntryLow, sign*s.EntryHigh, sign*s.Target
			if !buy {
				entryLow, entryHigh = -s.EntryHigh, -s.EntryLow
			}
			adverse, far := sign*s.Reaction.Adverse, sign*s.ProtectedFar
			if !(stop < adverse && stop < far && stop < entryLow) {
				t.Fatalf("trial %d: the stop %v is not beyond the adverse extreme %v, the structure %v and the entry %v", trial, stop, adverse, far, entryLow)
			}
			if !(target > entryHigh) || s.RewardRisk < cfg.MinimumRewardRisk-1e-9 {
				t.Fatalf("trial %d: target %v entry high %v rr %v", trial, target, entryHigh, s.RewardRisk)
			}
			if s.RiskPips > cfg.ExecutionStopMaxPips+1e-6 {
				t.Fatalf("trial %d: risk %v pips over the cap", trial, s.RiskPips)
			}
			if s.IndependentFacts < cfg.MinimumFacts {
				t.Fatalf("trial %d: %d independent facts", trial, s.IndependentFacts)
			}
			ids := map[string]structuralconfluence.Fact{}
			for _, f := range in.Facts {
				ids[f.ID] = f
			}
			for _, f := range s.Support {
				if !f.Verified || f.AvailableAt > s.TouchTime {
					t.Fatalf("trial %d: support fact %+v was not confirmable before the touch at %d", trial, f, s.TouchTime)
				}
			}
			if s.ConfirmIndex < s.TouchIndex || last-s.ConfirmIndex >= cfg.ReactionLookback {
				t.Fatalf("trial %d: touch %d confirm %d last %d", trial, s.TouchIndex, s.ConfirmIndex, last)
			}
			if s.Region.EpisodeID != structuralconfluence.EpisodeID(string(map[bool]string{true: structuralconfluence.Buy, false: structuralconfluence.Sell}[buy]), s.Region.Support) {
				t.Fatalf("trial %d: episode identity is not a function of the support set", trial)
			}
			// The entry is the region (or its proximal slice), never wider than it.
			if s.EntryLow < s.Region.Low-1e-9 || s.EntryHigh > s.Region.High+1e-9 || !(s.EntryHigh > s.EntryLow) {
				t.Fatalf("trial %d: entry [%v, %v] is not inside the region [%v, %v]", trial, s.EntryLow, s.EntryHigh, s.Region.Low, s.Region.High)
			}
			// Nothing after the confirmation closed beyond the reaction's extreme.
			for k := s.ConfirmIndex + 1; k <= last; k++ {
				c := in.Bars[k].Close
				if buy && c < s.Reaction.Adverse || !buy && c > s.Reaction.Adverse {
					t.Fatalf("trial %d: setup published after the reaction failed", trial)
				}
			}
		}
	}
	if accepted[market.Buy] < 20 || accepted[market.Sell] < 20 {
		t.Fatalf("the random scenarios exercised too little: %v", accepted)
	}
}

func TestInputOrderAndDuplicationDoNotChangeTheAnalysis(t *testing.T) {
	cfg := propertyConfig(t)
	r := rand.New(rand.NewSource(33))
	for trial := 0; trial < 400; trial++ {
		in := scenario(r)
		want := Detect(cfg, in)
		for k := 0; k < 4; k++ {
			shuffled := in
			shuffled.Facts = append([]structuralconfluence.Fact(nil), in.Facts...)
			r.Shuffle(len(shuffled.Facts), func(i, j int) { shuffled.Facts[i], shuffled.Facts[j] = shuffled.Facts[j], shuffled.Facts[i] })
			if len(shuffled.Facts) > 0 && r.Intn(2) == 0 {
				shuffled.Facts = append(shuffled.Facts, shuffled.Facts[r.Intn(len(shuffled.Facts))])
			}
			shuffled.Pools = append([]techniquezone.Pool(nil), in.Pools...)
			r.Shuffle(len(shuffled.Pools), func(i, j int) { shuffled.Pools[i], shuffled.Pools[j] = shuffled.Pools[j], shuffled.Pools[i] })
			got := Detect(cfg, shuffled)
			if !equalSetups(want.Setups, got.Setups) || !equalRestings(want.Resting, got.Resting) {
				t.Fatalf("trial %d: reordering or duplicating the inputs changed the decision", trial)
			}
		}
	}
}

func equalSetups(a, b []Setup) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		x, y := a[i], b[i]
		if x.Region.EpisodeID != y.Region.EpisodeID || x.ConfirmTime != y.ConfirmTime || x.TouchTime != y.TouchTime ||
			x.Stop != y.Stop || x.Target != y.Target || x.EntryLow != y.EntryLow || x.EntryHigh != y.EntryHigh ||
			!reflect.DeepEqual(x.Reaction, y.Reaction) || x.Direction != y.Direction {
			return false
		}
	}
	return true
}

func equalRestings(a, b []Resting) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i].Region.EpisodeID != b[i].Region.EpisodeID || a[i].Stop != b[i].Stop || a[i].Target != b[i].Target {
			return false
		}
	}
	return true
}

// Extra history older than anything the decision reads must not change it. The
// decision reads the reaction window, the candles from the supporting structures'
// confirmation to the touch (integrity), and nothing earlier.
func TestExtraOlderHistoryDoesNotChangeTheDecision(t *testing.T) {
	cfg := propertyConfig(t)
	r := rand.New(rand.NewSource(44))
	checked := 0
	for trial := 0; trial < 600; trial++ {
		in := scenario(r)
		if len(in.Bars) < 30 {
			continue
		}
		full := Detect(cfg, in)
		cut := 10
		trimmed := in
		trimmed.Bars = in.Bars[cut:]
		short := Detect(cfg, trimmed)
		// Compare what was decided about candles the shorter series contains.
		var a, b []Setup
		for _, s := range full.Setups {
			if s.TouchIndex >= cut {
				a = append(a, s)
			}
		}
		b = short.Setups
		if len(a) != len(b) {
			// A touch in the trimmed-off part cannot be reproduced; only the
			// overlapping decisions are compared, and the window is 3 candles.
			t.Fatalf("trial %d: %d vs %d setups", trial, len(a), len(b))
		}
		for i := range a {
			if a[i].Region.EpisodeID != b[i].Region.EpisodeID || a[i].Stop != b[i].Stop || a[i].Target != b[i].Target || a[i].ConfirmTime != b[i].ConfirmTime {
				t.Fatalf("trial %d: older history changed setup %d", trial, i)
			}
			checked++
		}
	}
	if checked < 10 {
		t.Fatalf("only %d setups compared", checked)
	}
}

func TestBuyAndSellAreExactMirrorImagesOnRandomMarkets(t *testing.T) {
	cfg := propertyConfig(t)
	r := rand.New(rand.NewSource(55))
	const axis = 8200.0
	m := func(p float64) float64 { return axis - p }
	compared := 0
	for trial := 0; trial < 800; trial++ {
		in := scenario(r)
		mir := in
		mir.Bars = make([]market.Candle, len(in.Bars))
		for i, b := range in.Bars {
			mir.Bars[i] = market.Candle{Time: b.Time, Open: m(b.Open), High: m(b.Low), Low: m(b.High), Close: m(b.Close), Volume: b.Volume}
		}
		mir.Facts = make([]structuralconfluence.Fact, len(in.Facts))
		for i, f := range in.Facts {
			x := f
			x.Low, x.High, x.StructuralLow, x.StructuralHigh = m(f.High), m(f.Low), m(f.StructuralHigh), m(f.StructuralLow)
			if f.Side == structuralconfluence.Buy {
				x.Side = structuralconfluence.Sell
			} else {
				x.Side = structuralconfluence.Buy
			}
			mir.Facts[i] = x
		}
		mir.Pools = make([]techniquezone.Pool, len(in.Pools))
		for i, p := range in.Pools {
			x := p
			x.Level = m(p.Level)
			if p.Side == "buy" {
				x.Side = "sell"
			} else {
				x.Side = "buy"
			}
			mir.Pools[i] = x
		}
		a, b := Detect(cfg, in), Detect(cfg, mir)
		if len(a.Setups) != len(b.Setups) {
			t.Fatalf("trial %d: %d setups vs %d in the mirrored market", trial, len(a.Setups), len(b.Setups))
		}
		for i := range a.Setups {
			x, y := a.Setups[i], b.Setups[i]
			if x.Direction == y.Direction || x.Reaction.Pattern != y.Reaction.Pattern ||
				math.Abs(x.Stop-m(y.Stop)) > 1e-6 || math.Abs(x.Target-m(y.Target)) > 1e-6 ||
				math.Abs(x.EntryLow-m(y.EntryHigh)) > 1e-6 || math.Abs(x.EntryHigh-m(y.EntryLow)) > 1e-6 ||
				math.Abs(x.RiskPips-y.RiskPips) > 1e-6 || math.Abs(x.RewardRisk-y.RewardRisk) > 1e-6 {
				t.Fatalf("trial %d: setup %d is not the mirror image: %+v vs %+v", trial, i, x, y)
			}
			compared++
		}
	}
	if compared < 40 {
		t.Fatalf("only %d setups compared", compared)
	}
}
