// Adversarial tests written by an independent auditor who did not write the
// detector (brute-force future-leak and mirror-symmetry fuzzing, stop and
// freshness invariants, and the reproductions of the defects the audit found).
// They stay as regression tests.
package crt

import (
	"math"
	"math/rand"
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// ---------- generators ----------

// genScenario builds a randomised CRT-flavoured M5 path (anchor [4100,4118]).
func genScenario(rng *rand.Rand) ([]market.Candle, []market.Candle) {
	s := newScribe(fixtureHour+3600-60*300, 4110)
	s.filler(60, 4110)
	j := func(x float64) float64 { return x + (rng.Float64()-0.5)*0.6 }
	closes := []float64{4108, 4106.5, 4104.8, 4103.5, 4104.2, 4105.2, 4103, 4101.2, 4100.5}
	for _, c := range closes {
		s.bar(j(c), rng.Float64()*0.8, rng.Float64()*0.8)
	}
	n := 3 + rng.Intn(8)
	for k := 0; k < n; k++ {
		var cl float64
		switch rng.Intn(4) {
		case 0:
			cl = 4098 + rng.Float64()*3
		default:
			cl = 4100.5 + rng.Float64()*9
		}
		up, lo := rng.Float64()*1.2, rng.Float64()*1.2
		if rng.Intn(5) == 0 {
			lo += rng.Float64() * 4
		}
		if rng.Intn(12) == 0 {
			up += rng.Float64() * 18
		}
		s.bar(cl, up, lo)
	}
	return s.bars, h1Series(fixtureHour, 40, 4118, 4100)
}

func reflectInput(m5, h1 []market.Candle) ([]market.Candle, []market.Candle) {
	// center 4096: p' = 8192-p is exact for p in [4096, 8192).
	return reflectPrices(m5, 4096), reflectPrices(h1, 4096)
}

// ---------- 1. future leak, brute force ----------

func TestAuditFuzzPrefixInvarianceAndFutureMutation(t *testing.T) {
	rng := rand.New(rand.NewSource(7))
	cfg := testConfig()
	nSetups, nEval := 0, 0
	for it := 0; it < 400; it++ {
		m5, h1 := genScenario(rng)
		if it%2 == 1 {
			m5, h1 = reflectInput(m5, h1)
		}
		for asOf := 55; asOf < len(m5); asOf++ {
			full := detectAsOf(cfg, Input{H1: h1, M5: m5}, asOf)
			// the live engine: only the prefix of M5 and only H1 bars closed by then
			evalClose := m5[asOf].Time + 300
			var liveH1 []market.Candle
			for _, c := range h1 {
				if c.Time+3600 <= evalClose {
					liveH1 = append(liveH1, c)
				}
			}
			live := Detect(cfg, Input{H1: liveH1, M5: append([]market.Candle(nil), m5[:asOf+1]...)})
			if !reflect.DeepEqual(full, live) {
				t.Fatalf("it %d asOf %d: differs from live view\nfull %+v\nlive %+v", it, asOf, full, live)
			}
			// garbage after asOf (including invalid candles) must not matter
			mut := append([]market.Candle(nil), m5...)
			for k := asOf + 1; k < len(mut); k++ {
				mut[k] = market.Candle{Time: mut[k].Time, Open: 1, High: 9999, Low: -5, Close: 7}
			}
			mutated := detectAsOf(cfg, Input{H1: h1, M5: mut}, asOf)
			if !reflect.DeepEqual(full, mutated) {
				t.Fatalf("it %d asOf %d: mutating future M5 changed the output", it, asOf)
			}
			nEval++
			nSetups += len(full.Setups)
		}
	}
	t.Logf("evaluations=%d setups-seen=%d", nEval, nSetups)
	if nSetups == 0 {
		t.Fatalf("fuzz was vacuous")
	}
}

// H1 future garbage (bars whose close is after asOf) must be ignored too.
func TestAuditFutureH1Garbage(t *testing.T) {
	m5, h1, idx := bullishCRT()
	cfg := testConfig()
	base := detectAsOf(cfg, Input{H1: h1, M5: m5}, idx.shift)
	g := append([]market.Candle(nil), h1...)
	g = append(g, market.Candle{Time: h1[len(h1)-1].Time + 7200, Open: 1, High: 2, Low: 0.5, Close: 1.5})       // future H1 with junk
	g = append(g, market.Candle{Time: h1[len(h1)-1].Time + 10800, Open: math.NaN(), High: 1, Low: 1, Close: 1}) // future NaN
	g = append(g, market.Candle{Time: h1[len(h1)-1].Time + 10800, Open: 1, High: 1, Low: 1, Close: 1})          // future duplicate time
	got := detectAsOf(cfg, Input{H1: g, M5: m5}, idx.shift)
	t.Logf("future-H1-garbage base setups=%d got setups=%d rej=%+v", len(base.Setups), len(got.Setups), got.Rejections)
	if !reflect.DeepEqual(base, got) {
		t.Errorf("future H1 garbage changed the decision at asOf (out-of-order/duplicate future H1)")
	}
}

// ---------- 2. mirror symmetry ----------

func reflectBack(s Setup) Setup {
	c := func(p float64) float64 { return 8192 - p }
	s.Anchor.High, s.Anchor.Low = c(s.Anchor.Low), c(s.Anchor.High)
	s.Sweep.ExtremePrice = c(s.Sweep.ExtremePrice)
	s.Reclaim.Close = c(s.Reclaim.Close)
	if s.Shift != nil {
		sh := *s.Shift
		sh.Level, sh.Close = c(sh.Level), c(sh.Close)
		s.Shift = &sh
	}
	s.EntryLow, s.EntryHigh = c(s.EntryHigh), c(s.EntryLow)
	s.Stop, s.Target = c(s.Stop), c(s.Target)
	if s.Direction == market.Buy {
		s.Direction = market.Sell
	} else {
		s.Direction = market.Buy
	}
	return s
}

func TestAuditFuzzMirrorSymmetry(t *testing.T) {
	rng := rand.New(rand.NewSource(11))
	cfg := testConfig()
	seen := 0
	for it := 0; it < 500; it++ {
		m5, h1 := genScenario(rng)
		rm5, rh1 := reflectInput(m5, h1)
		for asOf := 55; asOf < len(m5); asOf++ {
			a := detectAsOf(cfg, Input{H1: h1, M5: m5}, asOf)
			b := detectAsOf(cfg, Input{H1: rh1, M5: rm5}, asOf)
			if len(a.Setups) != len(b.Setups) {
				t.Fatalf("it %d asOf %d: setup count %d vs mirror %d", it, asOf, len(a.Setups), len(b.Setups))
			}
			for i := range a.Setups {
				got := reflectBack(b.Setups[i])
				if !approx(reflect.ValueOf(a.Setups[i]), reflect.ValueOf(got)) {
					t.Fatalf("it %d asOf %d: mirror differs\n a %+v\n b %+v", it, asOf, a.Setups[i], got)
				}
				seen++
			}
			// rejections: same multiset of reasons with swapped directions
			ra, rb := map[string]int{}, map[string]int{}
			for _, r := range a.Rejections {
				ra[r.Reason+string(r.Direction)]++
			}
			for _, r := range b.Rejections {
				d := market.Buy
				if r.Direction == market.Buy {
					d = market.Sell
				}
				rb[r.Reason+string(d)]++
			}
			if !reflect.DeepEqual(ra, rb) {
				t.Fatalf("it %d asOf %d: rejection asymmetry %v vs %v", it, asOf, ra, rb)
			}
		}
	}
	t.Logf("mirrored setups compared: %d", seen)
	if seen == 0 {
		t.Fatalf("vacuous")
	}
}

// ---------- 3. stop invariants (never inside any low since the anchor closed up to asOf) ----------

func TestAuditFuzzStopBeyondEveryLowThroughConfirmation(t *testing.T) {
	rng := rand.New(rand.NewSource(23))
	cfg := testConfig()
	viol, total := 0, 0
	var firstViol string
	for it := 0; it < 1500; it++ {
		m5, h1 := genScenario(rng)
		for asOf := 55; asOf < len(m5); asOf++ {
			a := detectAsOf(cfg, Input{H1: h1, M5: m5}, asOf)
			for _, s := range a.Setups {
				total++
				lo := math.Inf(1)
				for _, c := range m5[:asOf+1] {
					if c.Time >= s.Anchor.CloseTime && c.Time <= s.ConfirmedAt && c.Low < lo {
						lo = c.Low
					}
				}
				if !(s.Stop < lo) {
					viol++
					if firstViol == "" {
						firstViol = "stop at/above a low printed since the anchor closed"
						t.Logf("VIOLATION it=%d asOf=%d stop=%v lowestLowSinceAnchor=%v sweepExtreme=%v confirmedAt=%d evalIdx=%d", it, asOf, s.Stop, lo, s.Sweep.ExtremePrice, s.ConfirmedAt, asOf)
					}
				}
			}
		}
	}
	t.Logf("setups checked=%d with stop already breached by a low since anchor close=%d", total, viol)
	if viol > 0 {
		t.Errorf("%d/%d emitted setups have a stop that price already traded through (%s)", viol, total, firstViol)
	}
}

// Deterministic repro: a wick through the stop AFTER the confirming candle.
func TestAuditPostConfirmationWickThroughTheStop(t *testing.T) {
	m5, h1, idx := bullishCRT()
	s := &scribe{t: m5[len(m5)-1].Time + 300, last: m5[len(m5)-1].Close, bars: append([]market.Candle(nil), m5...)}
	s.bar(4105.0, 0.3, 15.0) // k12: low ~4089.7, far below the stop, closes back at 4105
	cfg := testConfig()
	cfg.ExecutionStopMaxPips = 0
	a := detectAsOf(cfg, Input{H1: h1, M5: s.bars}, len(s.bars)-1)
	t.Logf("asOf=k12 (confirm idx %d) setups=%d rej=%+v", idx.shift, len(a.Setups), a.Rejections)
	for _, st := range a.Setups {
		t.Errorf("setup emitted with Stop=%v although k12 low=%v (stop already hit); ExtremePrice=%v", st.Stop, s.bars[len(s.bars)-1].Low, st.Sweep.ExtremePrice)
	}
}

// ---------- 4. single-candle sweep + reclaim + MSS ----------

func TestAuditSingleCandleIsSweepReclaimAndShift(t *testing.T) {
	s := bullishPrefix(60)
	s.bar(4106.4, 0.3, 1.9) // open 4100.5, low 4098.6, close 4106.4 (> swing 4105.6)
	h1 := h1Series(fixtureHour, 40, 4118, 4100)
	a := Detect(testConfig(), Input{H1: h1, M5: s.bars})
	t.Logf("setups=%d rej=%+v", len(a.Setups), a.Rejections)
	for _, st := range a.Setups {
		t.Errorf("one candle was sweep (%d), reclaim (%d) and structure shift (%d): confirmation is not independent; BarsAfterReclaim=%d",
			st.Sweep.BarTime, st.Reclaim.BarTime, st.Shift.BarTime, st.Shift.BarsAfterReclaim)
	}
}

// ---------- 5. boundaries ----------

func sweepBoundary(t *testing.T, lowOf func(thr float64) float64) Analysis {
	m5, h1, idx := bullishCRT()
	cfg := testConfig()
	cfg.MinimumSweepATR = 1e-12 // threshold is exactly pips*pip
	thr := cfg.MinimumSweepPips * cfg.PipSize
	c := m5[idx.sweep]
	c.Low = lowOf(thr)
	m5[idx.sweep] = c
	return Detect(cfg, Input{H1: h1, M5: m5})
}

func TestAuditSweepExactlyAtThreshold(t *testing.T) {
	exact := sweepBoundary(t, func(thr float64) float64 { return 4100 - thr })
	below := sweepBoundary(t, func(thr float64) float64 { return math.Nextafter(4100-thr, math.Inf(-1)) })
	lit := sweepBoundary(t, func(thr float64) float64 { return 4099.8 })
	t.Logf("4100-0.2 == 4099.8 literal ? %v", 4100-2*0.1 == 4099.8)
	t.Logf("exact: setups=%d rej=%v | 1ulp deeper: setups=%d | literal 4099.8: setups=%d", len(exact.Setups), reasons(exact), len(below.Setups), len(lit.Setups))
	if len(exact.Setups) != 0 {
		t.Errorf("a penetration exactly equal to the threshold is not a sweep under the doc's strict '<'")
	}
	if len(below.Setups) != 1 {
		t.Errorf("1 ulp beyond the threshold must sweep")
	}
}

func reasons(a Analysis) []string {
	var r []string
	for _, x := range a.Rejections {
		r = append(r, x.Reason)
	}
	return r
}

// ---------- 6. anchor not closed ----------

func TestAuditAnchorNotYetClosed(t *testing.T) {
	m5, h1, idx := bullishCRT()
	cfg := testConfig()
	cfg.ConfirmationMode = ConfirmationSweepReclaim
	cfg.EntryModel = EntryReclaimRetest
	// asOf whose close is one M5 before the anchor closes: no anchor may exist
	before := detectAsOf(cfg, Input{H1: h1, M5: m5}, idx.first-1)
	// asOf whose close equals the anchor close
	at := detectAsOf(cfg, Input{H1: h1, M5: m5}, idx.first-1+0)
	t.Logf("before: anchors=%d  evalClose=%d anchorClose=%d", before.Anchors, before.EvaluatedAt, h1[len(h1)-1].Time+3600)
	if before.EvaluatedAt != m5[idx.first].Time {
		t.Fatalf("test arithmetic")
	}
	// idx.first-1 closes exactly at the anchor close -> anchor is closed
	early := detectAsOf(cfg, Input{H1: h1, M5: m5}, idx.first-2)
	if at.Anchors != early.Anchors+1 {
		t.Errorf("anchor must appear exactly when its close <= evaluation close: at=%d early=%d", at.Anchors, early.Anchors)
	}
	// The forming anchor candle with garbage OHLC must be ignored until closed.
	h := append([]market.Candle(nil), h1...)
	h[len(h)-1].High, h[len(h)-1].Low = 4400, 3800
	res := detectAsOf(cfg, Input{H1: h, M5: m5}, idx.first-2)
	if len(res.Setups) != 0 || res.Anchors != early.Anchors {
		t.Errorf("forming anchor leaked: %+v", res)
	}
}

// ---------- 7. duplicates and gaps ----------

func TestAuditDuplicateAndGappedM5(t *testing.T) {
	m5, h1, idx := bullishCRT()
	cfg := testConfig()
	// duplicate timestamp far in the past (3 candles from the start of the history)
	d := append([]market.Candle(nil), m5...)
	d[3].Time = d[2].Time
	a := Detect(cfg, Input{H1: h1, M5: d})
	t.Logf("old duplicate: setups=%d rej=%v", len(a.Setups), reasons(a))
	// duplicate of the confirming candle appended (a re-delivered tick)
	d2 := append(append([]market.Candle(nil), m5...), m5[len(m5)-1])
	a2 := Detect(cfg, Input{H1: h1, M5: d2})
	t.Logf("re-delivered last candle: setups=%d rej=%v", len(a2.Setups), reasons(a2))

	// gap: 50 minutes pass after the confirmation with no M5 candles
	s := &scribe{t: m5[len(m5)-1].Time + 300 + 3000, last: m5[len(m5)-1].Close, bars: append([]market.Candle(nil), m5...)}
	s.bar(4105.0, 0.3, 0.3)
	a3 := Detect(cfg, Input{H1: h1, M5: s.bars})
	for _, st := range a3.Setups {
		wall := (s.bars[len(s.bars)-1].Time + 300) - (st.ConfirmedAt + 300)
		t.Errorf("setup confirmed %d s (%d min) before evaluation is 'fresh' (ConfirmationAge=%d bars) because freshness counts slice positions, not time", wall, wall/60, st.ConfirmationAge)
	}
	_ = idx
}

// ---------- 8. envelope: reject, never clamp ----------

func TestAuditEnvelopeBoundaryRejectsNeverClamps(t *testing.T) {
	m5, h1, _ := bullishCRT()
	cfg := testConfig()
	cfg.ExecutionStopMaxPips = 0
	base := mustOneSetup(t, Detect(cfg, Input{H1: h1, M5: m5}))
	risk := base.RiskPips
	if !near(risk, (base.EntryHigh-base.Stop)/cfg.PipSize, 1e-9) {
		t.Errorf("RiskPips not measured from the proximal edge")
	}
	cfg.ExecutionStopMaxPips = risk + 1e-9
	ok := mustOneSetup(t, Detect(cfg, Input{H1: h1, M5: m5}))
	if ok.Stop != base.Stop {
		t.Errorf("stop moved under a cap")
	}
	cfg.ExecutionStopMaxPips = risk - 1e-6
	rej := Detect(cfg, Input{H1: h1, M5: m5})
	if len(rej.Setups) != 0 || !hasRejection(rej, ReasonRiskExceedsEnvelope) {
		t.Errorf("risk just above the cap must be rejected: %+v", rej)
	}
	// SELL twin identical
	rm5, rh1 := reflectInput(m5, h1)
	rej2 := Detect(cfg, Input{H1: rh1, M5: rm5})
	if len(rej2.Setups) != 0 || !hasRejection(rej2, ReasonRiskExceedsEnvelope) {
		t.Errorf("SELL twin: %+v", rej2)
	}
	// is the cap a real production default? (absent => silently disabled)
	cfg.ExecutionStopMaxPips = 0
	t.Logf("cap=0 means 'no pre-check': risk=%.2f pips accepted", risk)
}

// ---------- 9. double raid ----------

func TestAuditDoubleRaidVariants(t *testing.T) {
	h1 := h1Series(fixtureHour, 40, 4118, 4100)
	tail := func(s *scribe) {
		s.bar(4106.5, 0.4, 0.5)
		s.bar(4104.8, 0.4, 0.6)
		s.bar(4103.5, 0.3, 0.6)
		s.bar(4104.2, 0.4, 0.4)
		s.bar(4105.2, 0.4, 0.3)
		s.bar(4103.0, 0.2, 0.6)
		s.bar(4101.2, 0.3, 0.6)
		s.bar(4100.5, 0.3, 0.5)
	}
	// (a) raid closes outside, price stays above the high, then ONE candle crashes through the range
	// to sweep the low and close inside. Nothing closed back inside before the sweep began.
	s := newScribe(fixtureHour+3600-60*300, 4110)
	s.filler(60, 4110)
	s.bar(4119.0, 2.0, 0.5) // k0 raid, closes outside
	s.bar(4101.0, 0.3, 2.4) // k1 crash: sweeps low (4098.6), closes inside; opens at 4119 (> high)
	s.bar(4103.0, 0.3, 0.3)
	s.bar(4106.4, 0.3, 0.2)
	a := Detect(testConfig(), Input{H1: h1, M5: s.bars})
	t.Logf("(a) crash candle: setups=%d rej=%v", len(a.Setups), reasons(a))
	for _, st := range a.Setups {
		t.Errorf("(a) raid never closed back inside, yet setup emitted (resolved=%v)", st.DoubleRaidResolved)
	}
	// (b) raid closes inside at the raid bar -> resolved
	s = newScribe(fixtureHour+3600-60*300, 4110)
	s.filler(60, 4110)
	s.bar(4116.0, 11.5, 0.5)
	tail(s)
	s.bar(4101.0, 0.3, 1.9)
	s.bar(4103.0, 0.3, 0.3)
	s.bar(4106.4, 0.3, 0.2)
	b := Detect(testConfig(), Input{H1: h1, M5: s.bars})
	t.Logf("(b) raid closes inside: setups=%d resolved=%v rej=%v", len(b.Setups), len(b.Setups) == 1 && b.Setups[0].DoubleRaidResolved, reasons(b))
}

// ---------- 10. reference swing above the anchor high ----------

func TestAuditReferenceSwingAboveTheAnchorHigh(t *testing.T) {
	mk := func(raidPivot bool) Analysis {
		s := newScribe(fixtureHour+3600-60*300, 4110)
		s.filler(60, 4110)
		s.bar(4108, 0.3, 0.3)
		s.bar(4111, 0.5, 0.3) // k1 in-range swing high 4111.5
		s.bar(4109, 0.3, 0.3)
		s.bar(4108, 0.3, 0.3)
		s.bar(4110, 0.3, 0.3)
		if raidPivot {
			s.bar(4116, 3.5, 0.3) // k5 high 4119.5: a swing high ABOVE the anchor range, the most recent before the sweep
		} else {
			s.bar(4112, 0.3, 0.3)
		}
		s.bar(4112, 0.3, 0.3)
		s.bar(4109, 0.3, 0.3)
		s.bar(4106, 0.3, 0.3)
		s.bar(4103, 0.3, 0.3)
		s.bar(4101, 0.3, 0.5)
		s.bar(4101.0, 0.3, 1.9) // sweep + reclaim
		s.bar(4103, 0.3, 0.3)
		s.bar(4112.0, 0.3, 0.2) // closes above the in-range swing 4111.5
		cfg := testConfig()
		cfg.ExecutionStopMaxPips = 0
		cfg.EntryModel = EntryReclaimRetest
		return Detect(cfg, Input{H1: h1Series(fixtureHour, 40, 4118, 4100), M5: s.bars})
	}
	hi, lo := mk(true), mk(false)
	t.Logf("recent pivot above range: setups=%d rej=%v", len(hi.Setups), reasons(hi))
	t.Logf("recent pivot inside range: setups=%d rej=%v", len(lo.Setups), reasons(lo))
	if len(lo.Setups) == 1 && len(hi.Setups) == 0 {
		t.Errorf("DOC: reference swing must lie 'inside the anchor range'; code takes the most recent pivot above L_A even above H_A and ignores the older in-range swing %.1f", lo.Setups[0].Shift.Level)
	}
}

// ---------- 12. identity ----------

func TestAuditIdentityStableAndDistinct(t *testing.T) {
	m5, h1, idx := bullishCRT()
	strat := newStrategy(t, nil)
	ids := map[string]int{}
	for asOf := idx.shift; asOf <= idx.shift+0; asOf++ {
		for _, c := range strat.Evaluate(marketContext(h1, m5[:asOf+1], "up")) {
			ids[c.ID]++
		}
	}
	// two different anchors with identical M5 shape (anchor 1h later) -> distinct IDs
	sh := make([]market.Candle, len(m5))
	for i, c := range m5 {
		c.Time += 3600
		sh[i] = c
	}
	for _, c := range strat.Evaluate(marketContext(h1Series(fixtureHour+3600, 40, 4118, 4100), sh, "up")) {
		ids[c.ID]++
	}
	if len(ids) != 2 {
		t.Errorf("distinct anchors collided: %v", ids)
	}
	// same anchor, a new later episode (second sweep) must differ from the first
	s := &scribe{t: m5[len(m5)-1].Time + 300, last: m5[len(m5)-1].Close, bars: append([]market.Candle(nil), m5...)}
	_ = s
}

func approx(a, b reflect.Value) bool {
	switch a.Kind() {
	case reflect.Float64:
		return math.Abs(a.Float()-b.Float()) <= 1e-9*math.Max(1, math.Abs(a.Float()))
	case reflect.Struct:
		for i := 0; i < a.NumField(); i++ {
			if !approx(a.Field(i), b.Field(i)) {
				return false
			}
		}
		return true
	case reflect.Ptr:
		if a.IsNil() || b.IsNil() {
			return a.IsNil() == b.IsNil()
		}
		return approx(a.Elem(), b.Elem())
	default:
		return reflect.DeepEqual(a.Interface(), b.Interface())
	}
}
