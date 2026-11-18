package structure

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// SD-3 audit probes. Each "Defect" test failed on the zone formation as merged in #886/#887 and states what a base must be.

// climb is the supply fixture's rising frame (32 bars of a staircase and three impulse bars), ending at the price a consolidation starts from.
func climb() ([]spec, float64) {
	specs, _, _, _ := supplyAt(1)
	specs = specs[:35]
	return specs, specs[len(specs)-1].close
}

// baseBar is a quiet consolidation candle: a 0.9-ATR range around p.
func baseBar(p, top float64) spec { return spec{p, top, p - 0.4, p - 0.1} }

func departure(from float64, n int) []spec {
	var out []spec
	price := from
	for i := 0; i < n; i++ {
		out = append(out, spec{price, price + 0.05, price - 1.9, price - 1.8})
		price -= 1.8
	}
	return out
}

func formAudit(specs []spec, legStart, legEnd int, mutate func(*ZoneConfig)) []SwingZone {
	bars, atr := candlesOf(specs)
	cfg := zoneTestConfig()
	if mutate != nil {
		mutate(&cfg)
	}
	return FormSwingZones(true, bars, atr, []Leg{{legStart, legEnd}}, cfg)
}

// Defect: forward candles (between the swing and the departure) joined the base unconditionally, so a large rebound candle that is nothing like the base stretched the zone down to
// its low. The departure must leave THE BASE: every candle up to its first bar is a quiet candle that overlaps the base, or there is no zone.
func TestAnUnrelatedImpulseBeforeTheDepartureIsNotPartOfTheBase(t *testing.T) {
	specs, p := climb()
	specs = append(specs, baseBar(p, p+0.5))
	specs = append(specs, spec{p, p + 1.1, p - 0.4, p - 0.1})       // the swing candle
	specs = append(specs, baseBar(p, p+0.4))                        // quiet
	specs = append(specs, spec{p - 0.2, p + 0.6, p - 2.2, p + 0.4}) // a 2.8-ATR rebound: its low is a different price region
	legStart := len(specs)
	specs = append(specs, departure(p, 4)...)
	zones := formAudit(specs, legStart, len(specs)-1, nil)
	for _, z := range zones {
		if z.Low < p-0.6 {
			t.Fatalf("the base was stretched to %.2f by a rebound candle that is not part of it (base %d..%d, structure %.2f..%.2f)", z.Low, z.BaseStart, z.BaseEnd, z.Low, z.High)
		}
	}
	if len(zones) != 0 {
		t.Fatalf("a departure that did not leave the base makes no zone: %d", len(zones))
	}
}

// Defect: base_max_candles bounded only the backward extension; the candles between the swing and the departure were not counted, so a base could exceed it.
func TestBaseMaxCandlesBoundsTheCompleteBase(t *testing.T) {
	specs, p := climb()
	specs = append(specs, baseBar(p, p+0.5), spec{p, p + 1.1, p - 0.4, p - 0.1})
	for i := 0; i < 3; i++ {
		specs = append(specs, baseBar(p, p+0.4))
	}
	legStart := len(specs)
	specs = append(specs, departure(p, 4)...)
	zones := formAudit(specs, legStart, len(specs)-1, func(c *ZoneConfig) { c.DepartureGapBars = 4; c.BaseMaxCandles = 3 })
	for _, z := range zones {
		if n := z.BaseEnd - z.BaseStart; n > 3 {
			t.Fatalf("a %d-candle consolidation with base_max_candles 3 (swing %d, base %d..%d)", n, z.Swing.BarIndex, z.BaseStart, z.BaseEnd)
		}
	}
	if len(zones) != 0 {
		t.Fatalf("the swing is not inside a 3-candle base that ends at the departure: %d zones", len(zones))
	}
}

// Defect: the first swing in bar order claimed the departure even when another swing in the same consolidation is the higher one, and the base (which then ran forward to the
// departure) took the higher extreme with it, so the zone's AnchorPrice was not the swing it was anchored to. One structural event is one zone, anchored on its extreme.
func TestTheHighestSwingOfTheBaseAnchorsItAndAnchorPriceIsItsExtreme(t *testing.T) {
	specs, p := climb()
	specs = append(specs, spec{p, p + 0.8, p - 0.4, p - 0.1}) // a lower high first (a swing high of its own)
	specs = append(specs, spec{p - 0.1, p + 0.2, p - 0.5, p - 0.3})
	specs = append(specs, spec{p - 0.3, p + 0.3, p - 0.5, p})
	specs = append(specs, spec{p, p + 1.3, p - 0.4, p + 0.1}) // the higher swing high of the same consolidation
	specs = append(specs, spec{p + 0.1, p + 0.4, p - 0.4, p - 0.1})
	legStart := len(specs)
	specs = append(specs, departure(p, 4)...)
	zones := formAudit(specs, legStart, len(specs)-1, func(c *ZoneConfig) { c.DepartureGapBars = 5; c.BaseMaxCandles = 8 })
	if len(zones) != 1 {
		t.Fatalf("one consolidation, one departure: one zone, got %d", len(zones))
	}
	z := zones[0]
	if z.AnchorPrice != float64(z.Swing.Price) || z.AnchorPrice != z.High {
		t.Fatalf("AnchorPrice %.2f must be the anchoring swing's own extreme (%.2f) and the base's high (%.2f)", z.AnchorPrice, float64(z.Swing.Price), z.High)
	}
	if want := 35 + 3; z.Swing.BarIndex != want {
		t.Fatalf("the highest swing of the base (bar %d) is the anchor, got bar %d", want, z.Swing.BarIndex)
	}
}

// Defect: a swing candle that is itself the first, impulsive candle of the departure belonged to the base with its WHOLE range, so the zone swallowed the departure's own travel.
// The base is what traded before the departure: from the candle's open (the departure's origin) up to its extreme.
func TestADepartureThatStartsOnTheSwingCandleDoesNotSwallowItsOwnTravel(t *testing.T) {
	specs, p := climb()
	specs = append(specs, baseBar(p, p+0.5), baseBar(p, p+0.6))
	legStart := len(specs)
	specs = append(specs, spec{p, p + 1.0, p - 1.4, p - 1.3}) // the swing candle: 2.4 ATR, bearish, opens 1.0 under its high
	price := p - 1.3
	for i := 0; i < 3; i++ {
		specs = append(specs, spec{price, price + 0.05, price - 1.9, price - 1.8})
		price -= 1.8
	}
	zones := formAudit(specs, legStart, len(specs)-1, nil)
	if len(zones) != 1 {
		t.Fatalf("zones %d", len(zones))
	}
	if z := zones[0]; z.Low < p-0.5 {
		t.Fatalf("the structure reaches down to %.2f, into the departure bar that closed at %.2f: the base is the region it traded before leaving (open %.2f)", z.Low, p-1.3, p)
	}
}

// Defect: the strength of a formed zone was recomputed against the length of the whole history (standing() read len(bars)), so adding bars downgraded a STRONG/MODERATE formation to
// WEAK once the swing was 150 bars old. Formation strength is fixed; age only ends the zone's tradeability.
func TestAppendingBarsNeverDowngradesAFormedZone(t *testing.T) {
	specs, _, legStart, legEnd := supplyAt(5)
	bars, atr := candlesOf(specs)
	cfg := zoneTestConfig()
	base := FormSwingZones(true, bars, atr, []Leg{{legStart, legEnd}}, cfg)
	if len(base) != 1 || base[0].Strength == StrengthWeak {
		t.Fatalf("setup: %+v", base)
	}
	last := specs[len(specs)-1].close
	for i := 0; i < cfg.Major.RelevanceBars+60; i++ { // a long quiet drift far below the zone
		specs = append(specs, spec{last, last + 0.15, last - 0.15, last})
	}
	bars, atr = candlesOf(specs)
	later := FormSwingZones(true, bars, atr, []Leg{{legStart, legEnd}}, cfg)
	if len(later) != 1 || later[0].Strength != base[0].Strength {
		t.Fatalf("strength at formation %s, after %d more bars %s", base[0].Strength, cfg.Major.RelevanceBars+60, later[0].Strength)
	}
	if math.IsNaN(later[0].Low) {
		t.Fatal("nan")
	}
}

// ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
// Association, causality, symmetry and invariants.

func mirrorSpecs(in []spec) []spec {
	out := make([]spec, len(in))
	for i, s := range in {
		out[i] = spec{200 - s.open, 200 - s.low, 200 - s.high, 200 - s.close}
	}
	return out
}

// Aged is the zone's current validity and never its formation: strength is the same, the zone stops being tradeable.
func TestAgeEndsTradeabilityAndNeverTheFormation(t *testing.T) {
	specs, _, legStart, legEnd := supplyAt(5)
	cfg := zoneTestConfig()
	bars, atr := candlesOf(specs)
	z := FormSwingZones(true, bars, atr, []Leg{{legStart, legEnd}}, cfg)[0]
	if z.Aged {
		t.Fatal("a fresh zone is not aged")
	}
	last := specs[len(specs)-1].close
	for i := 0; i < cfg.Major.RelevanceBars+5; i++ {
		specs = append(specs, spec{last, last + 0.15, last - 0.15, last})
	}
	bars, atr = candlesOf(specs)
	later := FormSwingZones(true, bars, atr, []Leg{{legStart, legEnd}}, cfg)[0]
	if !later.Aged || later.Strength != z.Strength || later.Low != z.Low || later.High != z.High || later.ConfirmedAt != z.ConfirmedAt {
		t.Fatalf("aged zone: strength %s -> %s, bounds %.2f..%.2f -> %.2f..%.2f, aged=%v", z.Strength, later.Strength, z.Low, z.High, later.Low, later.High, later.Aged)
	}
}

// A minor pivot that precedes the major one in the same consolidation does not claim the departure.
func TestAMinorPivotBeforeTheMajorOneDoesNotClaimTheDeparture(t *testing.T) {
	specs, p := climb()
	specs = append(specs, spec{p, p + 0.7, p - 0.4, p - 0.1}, spec{p - 0.1, p + 0.1, p - 0.5, p - 0.3}, spec{p - 0.3, p + 0.2, p - 0.5, p - 0.1}, spec{p, p + 1.2, p - 0.4, p + 0.1}, spec{p + 0.1, p + 0.3, p - 0.4, p - 0.2})
	legStart := len(specs)
	specs = append(specs, departure(p, 4)...)
	zones := formAudit(specs, legStart, len(specs)-1, func(c *ZoneConfig) { c.DepartureGapBars = 5; c.BaseMaxCandles = 8 })
	if len(zones) != 1 || zones[0].Swing.BarIndex != 35+3 {
		t.Fatalf("the higher swing high (bar 38) anchors the one zone: %+v", zones)
	}
}

// The departure may begin on the swing candle or one to three candles after it; each is the same structure and a gap beyond the configured one is not.
func TestTheDepartureMayStartOneToThreeCandlesAfterTheSwing(t *testing.T) {
	for after := 1; after <= 4; after++ { // the departure begins `after` bars after the swing candle
		specs, p := climb()
		specs = append(specs, baseBar(p, p+0.5), spec{p, p + 1.1, p - 0.4, p - 0.1})
		for i := 1; i < after; i++ {
			specs = append(specs, baseBar(p, p+0.4))
		}
		legStart := len(specs)
		specs = append(specs, departure(p, 4)...)
		zones := formAudit(specs, legStart, len(specs)-1, nil)
		want := after <= 3
		if (len(zones) == 1) != want {
			t.Fatalf("the departure begins %d bars after the swing (gap 3): %d zones", after, len(zones))
		}
		if want && (zones[0].Swing.BarIndex != 36 || zones[0].AnchorPrice != float64(zones[0].Swing.Price)) {
			t.Fatalf("anchor %+v", zones[0].Swing)
		}
	}
}

// A small first departure and a larger displacement later are one structural event: one zone, anchored once, from the first departure that left the base.
func TestASmallFirstDepartureAndALargerOneAreOneZone(t *testing.T) {
	specs, p := climb()
	specs = append(specs, baseBar(p, p+0.5), spec{p, p + 1.1, p - 0.4, p - 0.1}, baseBar(p, p+0.4))
	firstStart := len(specs)
	price := p
	for i := 0; i < 2; i++ { // a small departure of two 1.0-ATR bars
		specs = append(specs, spec{price, price + 0.05, price - 1.0, price - 0.9})
		price -= 0.9
	}
	firstEnd := len(specs) - 1
	specs = append(specs, spec{price, price + 0.5, price - 0.3, price + 0.2}, spec{price + 0.2, price + 0.6, price - 0.2, price}) // a pause
	secondStart := len(specs)
	for i := 0; i < 4; i++ {
		specs = append(specs, spec{price, price + 0.05, price - 1.9, price - 1.8})
		price -= 1.8
	}
	bars, atr := candlesOf(specs)
	zones := FormSwingZones(true, bars, atr, []Leg{{firstStart, firstEnd}, {secondStart, len(specs) - 1}}, zoneTestConfig())
	anchors := map[int]int{}
	for _, z := range zones {
		anchors[z.Swing.BarIndex]++
	}
	for idx, n := range anchors {
		if n > 1 {
			t.Fatalf("swing %d anchors %d zones: one structural event is one zone", idx, n)
		}
	}
}

// An ambiguous association is rejected with its reason, never guessed: equal adjacent highs are not confirmed swings, so the base's extreme has no anchor.
func TestAnAmbiguousBaseIsRejectedWithItsReason(t *testing.T) {
	specs, p := climb()
	specs = append(specs, spec{p, p + 0.7, p - 0.4, p - 0.1}, spec{p - 0.1, p + 0.1, p - 0.5, p - 0.3}, spec{p - 0.3, p + 0.2, p - 0.5, p - 0.1}) // a real swing high (bar 35)
	specs = append(specs, spec{p - 0.1, p + 1.1, p - 0.35, p - 0.2}, spec{p - 0.2, p + 1.1, p - 0.4, p - 0.3})                                    // then two adjacent equal highs, the base's extreme
	legStart := len(specs)
	specs = append(specs, departure(p, 4)...)
	bars, atr := candlesOf(specs)
	zones, rejected := FormSwingZonesDetailed(true, bars, atr, []Leg{{legStart, len(specs) - 1}}, func() ZoneConfig { c := zoneTestConfig(); c.BaseMaxCandles = 8; c.DepartureGapBars = 5; return c }())
	if len(zones) != 0 || len(rejected) != 1 || rejected[0].Reason != RejectExtremeNotSwing {
		t.Fatalf("zones %d rejections %+v", len(zones), rejected)
	}
}

// A zone forms only once the swing's right window and the departure have closed; before that its reason is "awaiting", not a permanent rejection.
func TestNoZoneExistsBeforeItsSwingAndDepartureAreConfirmed(t *testing.T) {
	specs, _, legStart, legEnd := supplyAt(5)
	bars, atr := candlesOf(specs)
	cfg := zoneTestConfig()
	var first int
	for n := 20; n <= len(bars); n++ {
		var legs []Leg
		if n > legStart {
			legs = []Leg{{legStart, minInt(legEnd, n-1)}}
		}
		zones, _ := FormSwingZonesDetailed(true, bars[:n], atr[:n], legs, cfg)
		for _, z := range zones {
			if first == 0 {
				first = n
			}
			if z.ConfirmedIndex > n-1 || z.BaseEnd > n-1 || z.ConfirmedAt > bars[n-1].Time || z.Swing.BarIndex+cfg.Major.PivotRightBars > n-1 {
				t.Fatalf("prefix %d: zone uses bars that do not exist yet: %+v", n, z)
			}
			if z.BaseEnd > z.LegStart {
				t.Fatalf("the base %d..%d runs into the departure at %d", z.BaseStart, z.BaseEnd, z.LegStart)
			}
		}
	}
	if first <= legStart {
		t.Fatalf("the zone appeared at prefix %d, before its departure began at %d", first, legStart)
	}
}

// Property: over many pseudo-random markets, every zone satisfies the structural invariants and demand is the exact mirror of supply.
func TestZoneInvariantsHoldOnRandomMarkets(t *testing.T) {
	cfg := zoneTestConfig()
	rng := uint64(88172645463325252)
	next := func() float64 {
		rng ^= rng << 13
		rng ^= rng >> 7
		rng ^= rng << 17
		return float64(rng%100000) / 100000
	}
	checked := 0
	for run := 0; run < 60; run++ {
		var specs []spec
		price := 100.0
		for i := 0; i < 300; i++ {
			regime := 0.35
			if (i/40)%2 == 1 {
				regime = 1.4
			}
			move := (next() - 0.5) * 2 * regime
			w := 0.1 + 0.4*next()
			open, close := price, price+move
			specs = append(specs, spec{open, math.Max(open, close) + w*next(), math.Min(open, close) - w*next(), close})
			price = close
		}
		bars, atr := candlesOf(specs)
		legs := runLegs(bars, atr, true)
		zones, _ := FormSwingZonesDetailed(true, bars, atr, legs, cfg)
		mirrored := mirrorSpecs(specs)
		mbars, matr := candlesOf(mirrored)
		mzones, _ := FormSwingZonesDetailed(false, mbars, matr, runLegs(mbars, matr, false), cfg)
		if len(zones) != len(mzones) {
			t.Fatalf("run %d: %d supply zones, %d demand zones on the mirrored market", run, len(zones), len(mzones))
		}
		highs := map[int]bool{}
		for _, sw := range ClassifyMajor(bars, atr, cfg.Major) {
			if sw.Kind == PivotHigh {
				highs[sw.BarIndex] = true
			}
		}
		seen := map[int]bool{}
		for k, z := range zones {
			checked++
			if z.AnchorPrice != float64(z.Swing.Price) || z.AnchorPrice != z.High {
				t.Fatalf("run %d: anchor %.4f swing %.4f base high %.4f", run, z.AnchorPrice, float64(z.Swing.Price), z.High)
			}
			if n := z.BaseEnd - z.BaseStart; n < 0 || n > cfg.BaseMaxCandles {
				t.Fatalf("run %d: %d consolidation candles in the base (max %d)", run, n, cfg.BaseMaxCandles)
			}
			if z.Swing.BarIndex < z.BaseStart || z.Swing.BarIndex > z.BaseEnd || z.LegStart-z.Swing.BarIndex > cfg.DepartureGapBars {
				t.Fatalf("run %d: swing %d outside base %d..%d / departure %d", run, z.Swing.BarIndex, z.BaseStart, z.BaseEnd, z.LegStart)
			}
			if z.BaseEnd > z.LegStart || z.ConfirmedIndex < z.LegEnd || z.ConfirmedIndex < z.Swing.BarIndex+cfg.Major.PivotRightBars {
				t.Fatalf("run %d: base end %d, departure %d..%d, confirmed %d", run, z.BaseEnd, z.LegStart, z.LegEnd, z.ConfirmedIndex)
			}
			for j := z.BaseStart; j <= z.BaseEnd; j++ {
				impulsive := bars[j].High-bars[j].Low > cfg.BaseMaxCandleATR*atrAt(atr, j)+1e-9
				if impulsive && !highs[j] && j != z.LegStart {
					t.Fatalf("run %d: an impulse candle (%d) that is not a swing is inside the base %d..%d", run, j, z.BaseStart, z.BaseEnd)
				}
				if bars[j].High > z.High+1e-12 {
					t.Fatalf("run %d: candle %d rises above the structure", run, j)
				}
				if !impulsive && j != z.LegStart && bars[j].Low < z.Low-1e-12 { // a departure's first candle that is the swing contributes only what it traded before leaving
					t.Fatalf("run %d: quiet candle %d lies below the structure", run, j)
				}
			}
			if seen[z.Swing.BarIndex] {
				t.Fatalf("run %d: swing %d anchors two zones", run, z.Swing.BarIndex)
			}
			seen[z.Swing.BarIndex] = true
			m := mzones[k]
			if m.Swing.Kind != PivotLow || z.Swing.BarIndex != m.Swing.BarIndex || z.BaseStart != m.BaseStart || z.BaseEnd != m.BaseEnd || z.LegStart != m.LegStart || z.Strength != m.Strength ||
				z.Swing.Class != m.Swing.Class || z.ConfirmedIndex != m.ConfirmedIndex || math.Abs(z.Low-(200-m.High)) > 1e-9 || math.Abs(z.High-(200-m.Low)) > 1e-9 || math.Abs(z.AnchorPrice-(200-m.AnchorPrice)) > 1e-9 {
				t.Fatalf("run %d: demand is not the mirror of supply: %+v vs %+v", run, z, m)
			}
		}
	}
	if checked < 20 {
		t.Fatalf("the property test must exercise zones: %d", checked)
	}
}

// runLegs is the displacement legs of a frame as the strategies define them: runs of same-direction candles whose net move is at least 1.5 ATR and at least half of whose bodies are
// at least 60 % of their range.
func runLegs(bars []market.Candle, atr []float64, down bool) []Leg {
	var legs []Leg
	flush := func(start, end int) {
		if start < 0 || end < start {
			return
		}
		net := bars[start].Open - bars[end].Close
		if !down {
			net = -net
		}
		if net < 1.5*atrAt(atr, end) {
			return
		}
		strong := 0
		for i := start; i <= end; i++ {
			if r := bars[i].High - bars[i].Low; r > 0 && math.Abs(bars[i].Close-bars[i].Open)/r >= 0.6 {
				strong++
			}
		}
		if strong < maxInt(1, (end-start+1)/2) {
			return
		}
		legs = append(legs, Leg{start, end})
	}
	start := -1
	dir := 0
	for i, b := range bars {
		d := 0
		if b.Close < b.Open {
			d = -1
		} else if b.Close > b.Open {
			d = 1
		}
		want := 1
		if down {
			want = -1
		}
		switch {
		case d != want:
			flush(start, i-1)
			start, dir = -1, 0
		case start < 0 || dir != d:
			flush(start, i-1)
			start, dir = i, d
		}
	}
	flush(start, len(bars)-1)
	return legs
}

// Defect (found on production windows): a leg that starts on a candle whose high is the base's extreme gave a zone anchored on an EARLIER swing until that candle's right window
// closed, and a different zone anchored on the candle itself afterwards: one departure, two identities as bars arrived. The zone must wait ("awaiting"), then be the same.
func TestOneDepartureHasOneIdentityAsTheSwingOnItsFirstCandleConfirms(t *testing.T) {
	specs, p := climb()
	specs = append(specs, spec{p, p + 0.5, p - 0.4, p - 0.1}, spec{p - 0.1, p + 0.6, p - 0.4, p - 0.2}) // a consolidation under p+0.6
	legStart := len(specs)
	specs = append(specs, spec{p, p + 1.3, p - 1.4, p - 1.2}) // the departure's first candle: its high is above the base, so it IS the extreme
	price := p - 1.2
	for i := 0; i < 3; i++ {
		specs = append(specs, spec{price, price + 0.05, price - 1.9, price - 1.8})
		price -= 1.8
	}
	bars, atr := candlesOf(specs)
	cfg := zoneTestConfig()
	var first *SwingZone
	for n := legStart + 2; n <= len(bars); n++ {
		zones, rejected := FormSwingZonesDetailed(true, bars[:n], atr[:n], []Leg{{legStart, n - 1}}, cfg)
		confirmed := legStart+cfg.Major.PivotRightBars <= n-1
		if !confirmed {
			if len(zones) != 0 {
				t.Fatalf("prefix %d: the swing on the departure's first candle is not confirmed yet, so there is no zone: %+v", n, zones[0].Swing)
			}
			if len(rejected) != 1 || rejected[0].Reason != RejectAwaitingSwing {
				t.Fatalf("prefix %d: want awaiting_swing_confirmation, got %+v", n, rejected)
			}
			continue
		}
		if len(zones) != 1 {
			t.Fatalf("prefix %d: %d zones, rejections %+v", n, len(zones), rejected)
		}
		z := zones[0]
		if z.Swing.BarIndex != legStart || z.AnchorPrice != p+1.3 {
			t.Fatalf("prefix %d: anchored on bar %d at %.2f, want the departure's first candle", n, z.Swing.BarIndex, z.AnchorPrice)
		}
		if first == nil {
			c := z
			first = &c
		} else if z.BaseStart != first.BaseStart || z.BaseEnd != first.BaseEnd || z.Low != first.Low || z.High != first.High || z.Swing.BarIndex != first.Swing.BarIndex {
			t.Fatalf("prefix %d: the zone changed after formation", n)
		}
	}
	if first == nil {
		t.Fatal("the zone never formed")
	}
}

// Defect (found on production XAU H1): each candle only had to overlap the base built so far, so a slowly rising staircase of quiet candles was absorbed one by one into a "base" that is
// really a trend (seven candles, 3.1 ATR wide). A base is a LOCAL consolidation: a candle that would stretch it past maximum_zone_atr is not part of it.
func TestASlowTrendIsNotAbsorbedIntoTheBase(t *testing.T) {
	specs, p := climb()
	price := p - 4.0
	for i := 0; i < 8; i++ { // eight quiet overlapping candles climbing 0.55 ATR each: 4.4 ATR from first to last
		specs = append(specs, spec{price, price + 0.8, price - 0.1, price + 0.55})
		price += 0.55
	}
	specs = append(specs, spec{price, price + 0.9, price - 0.1, price + 0.3}) // the swing candle, the top of the staircase
	legStart := len(specs)
	specs = append(specs, departure(price, 4)...)
	zones := formAudit(specs, legStart, len(specs)-1, func(c *ZoneConfig) { c.BaseMaxCandles = 10; c.DepartureGapBars = 3 })
	for _, z := range zones {
		if z.WidthATR > zoneTestConfig().MaximumZoneATR+1e-9 || z.TooWide {
			t.Fatalf("a trending staircase was absorbed: %d candles %.2f..%.2f (%.2f ATR)", z.BaseEnd-z.BaseStart+1, z.Low, z.High, z.WidthATR)
		}
	}
	if len(zones) != 1 {
		t.Fatalf("the top of the staircase is still a swing with a departure: want one zone from its local base, got %d", len(zones))
	}
}
