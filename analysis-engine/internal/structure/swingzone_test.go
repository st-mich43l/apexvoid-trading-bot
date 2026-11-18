package structure

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func zoneTestConfig() ZoneConfig {
	return ZoneConfig{
		Major: majorTestConfig(), DepartureGapBars: 3, BaseMaxCandles: 6, BaseMaxCandleATR: 1.5, MinimumZoneATR: 0.2, MaximumZoneATR: 3.0, StrongDepartureATR: 2.0,
	}
}

type spec struct{ open, high, low, close float64 }

func candlesOf(specs []spec) ([]market.Candle, []float64) {
	bars := make([]market.Candle, len(specs))
	atr := make([]float64, len(specs))
	for i, s := range specs {
		bars[i] = market.Candle{Time: int64(1_800_000_000 + i*stepSeconds), Open: s.open, High: s.high, Low: s.low, Close: s.close}
		atr[i] = 1.0
	}
	return bars, atr
}

// supplyAt builds a rising frame (internal swings, then three impulse bars), a consolidation of `base` candles whose middle candle makes the high, and a four-bar bearish
// departure. It returns the bars, the index of the swing candle and the first and last bar of the leg.
func supplyAt(base int) (specs []spec, swing, legStart, legEnd int) {
	price := 100.0
	for i := 0; i < 32; i++ {
		w := 0.1 + 0.02*float64((i*7)%5)
		if i%4 == 3 {
			specs = append(specs, spec{price, price + w, price - 2.5 - 3*w, price - 2.5})
			price -= 2.5
		} else {
			specs = append(specs, spec{price, price + 1.0 + w, price - w, price + 1.0})
			price += 1.0
		}
	}
	for i := 0; i < 3; i++ { // the impulse into the base: ranges above the base-candle limit
		specs = append(specs, spec{price, price + 1.8 + 0.1, price - 0.05, price + 1.8})
		price += 1.8
	}
	mid := base / 2
	for i := 0; i < base; i++ {
		top := price + 0.5
		if i == mid {
			top = price + 1.1 // the swing candle: the highest high of the base
			swing = len(specs)
		}
		specs = append(specs, spec{price, top, price - 0.4 - 0.05*float64(i), price - 0.1})
	}
	legStart = len(specs)
	for i := 0; i < 4; i++ {
		specs = append(specs, spec{price, price + 0.05, price - 1.8 - 0.1, price - 1.8})
		price -= 1.8
	}
	legEnd = len(specs) - 1
	return specs, swing, legStart, legEnd
}

func TestAMajorSwingWithItsDepartureFormsAStrongSupplyZone(t *testing.T) {
	specs, swing, legStart, legEnd := supplyAt(5)
	bars, atr := candlesOf(specs)
	zones := FormSwingZones(true, bars, atr, []Leg{{legStart, legEnd}}, zoneTestConfig())
	if len(zones) != 1 {
		t.Fatalf("want one zone, got %d", len(zones))
	}
	z := zones[0]
	if z.Strength != StrengthStrong || z.Swing.Class != SwingMajor || z.Swing.BarIndex != swing {
		t.Fatalf("zone %+v swing %s", z, brief(z.Swing))
	}
	if z.Proximal != z.Low || z.Distal != z.High || z.AnchorPrice != z.High || z.AnchorPrice != bars[swing].High {
		t.Fatalf("a supply zone is entered from below and invalidated above its swing high: %+v", z)
	}
	if z.BaseEnd != legStart-1 || z.LegStart != legStart || z.DepartureConfirmedAt != bars[legEnd].Time {
		t.Fatalf("the base ends where the leg begins: %+v", z)
	}
	if z.BaseFormedAt != bars[z.BaseEnd].Time {
		t.Fatalf("base formed at the last base candle: %+v", z)
	}
	// The swing is known two bars after it, the departure when its last bar closed: the zone is touchable from the later of the two, never earlier.
	if z.ConfirmedIndex != legEnd {
		t.Fatalf("confirmed index %d, want the leg end %d", z.ConfirmedIndex, legEnd)
	}
	if z.DepartureATR < 6 || z.Efficiency < 0.5 {
		t.Fatalf("a 7.2-ATR departure of strong bars: departure %.2f efficiency %.2f", z.DepartureATR, z.Efficiency)
	}
}

// The base is what the candles show, not a fixed count: a longer consolidation gives a longer base, and the impulse into it is never part of it.
func TestTheBaseIsTheActualConsolidationNotAFixedCount(t *testing.T) {
	var lengths []int
	for _, n := range []int{2, 3, 5} {
		specs, _, legStart, legEnd := supplyAt(n)
		bars, atr := candlesOf(specs)
		zones := FormSwingZones(true, bars, atr, []Leg{{legStart, legEnd}}, zoneTestConfig())
		if len(zones) != 1 {
			t.Fatalf("base %d: want one zone, got %d", n, len(zones))
		}
		z := zones[0]
		lengths = append(lengths, z.BaseEnd-z.BaseStart+1)
		if got := z.BaseEnd - z.BaseStart + 1; got != n {
			t.Fatalf("a %d-candle consolidation must be a %d-candle base (the impulse before it excluded), got %d [%d..%d]", n, n, got, z.BaseStart, z.BaseEnd)
		}
	}
	if lengths[0] == lengths[2] {
		t.Fatalf("the base must vary with the market: %v", lengths)
	}
}

func TestADepartureThatDoesNotLeaveTheSwingMakesNoZone(t *testing.T) {
	specs, _, legStart, legEnd := supplyAt(5)
	bars, atr := candlesOf(specs)
	cfg := zoneTestConfig()
	cfg.DepartureGapBars = 0 // the leg starts after the swing candle, so it did not leave it
	if zones := FormSwingZones(true, bars, atr, []Leg{{legStart, legEnd}}, cfg); len(zones) != 0 {
		t.Fatalf("a leg that begins after the allowed gap is a different move: %d zones", len(zones))
	}
}

func TestADisplacementWithNoSwingUnderItIsWeak(t *testing.T) {
	// A shallow mid-range wobble followed by a leg: the high before it is a minor swing.
	var specs []spec
	price := 100.0
	for i := 0; i < 30; i++ {
		d := 0.3
		if i%2 == 1 {
			d = -0.3
		}
		w := 0.1 + 0.02*float64((i*7)%5)
		specs = append(specs, spec{price, math.Max(price, price+d) + w, math.Min(price, price+d) - w, price + d})
		price += d
	}
	legStart := len(specs)
	for i := 0; i < 3; i++ {
		specs = append(specs, spec{price, price + 0.05, price - 1.8 - 0.1, price - 1.8})
		price -= 1.8
	}
	bars, atr := candlesOf(specs)
	zones := FormSwingZones(true, bars, atr, []Leg{{legStart, len(specs) - 1}}, zoneTestConfig())
	for _, z := range zones {
		if z.Strength != StrengthWeak {
			t.Fatalf("a base with no swing of weight under it is weak: %s strength %s", brief(z.Swing), z.Strength)
		}
	}
}

func TestZoneWidthIsJudgedOnTheWholeStructure(t *testing.T) {
	specs, _, legStart, legEnd := supplyAt(5)
	bars, atr := candlesOf(specs)
	cfg := zoneTestConfig()
	cfg.MaximumZoneATR = 0.8
	zones := FormSwingZones(true, bars, atr, []Leg{{legStart, legEnd}}, cfg)
	if len(zones) != 1 || !zones[0].TooWide {
		t.Fatalf("a structure wider than the limit is a region: %+v", zones)
	}
	cfg = zoneTestConfig()
	cfg.MinimumZoneATR = 5
	cfg.MaximumZoneATR = 8
	zones = FormSwingZones(true, bars, atr, []Leg{{legStart, legEnd}}, cfg)
	if len(zones) != 1 || !zones[0].TooNarrow {
		t.Fatalf("a structure narrower than the minimum is an incidental region: %+v", zones)
	}
}

// A demand zone is the exact price mirror of a supply zone: same base, same swing class, same strength, mirrored edges.
func TestDemandIsThePriceMirrorOfSupply(t *testing.T) {
	specs, _, legStart, legEnd := supplyAt(5)
	bars, atr := candlesOf(specs)
	mirror := make([]market.Candle, len(bars))
	for i, c := range bars {
		mirror[i] = market.Candle{Time: c.Time, Open: 200 - c.Open, High: 200 - c.Low, Low: 200 - c.High, Close: 200 - c.Close}
	}
	s := FormSwingZones(true, bars, atr, []Leg{{legStart, legEnd}}, zoneTestConfig())
	d := FormSwingZones(false, mirror, atr, []Leg{{legStart, legEnd}}, zoneTestConfig())
	if len(s) != 1 || len(d) != 1 {
		t.Fatalf("%d supply and %d demand zones", len(s), len(d))
	}
	a, b := s[0], d[0]
	if b.Swing.Kind != PivotLow || a.Strength != b.Strength || a.Swing.Class != b.Swing.Class || a.BaseStart != b.BaseStart || a.BaseEnd != b.BaseEnd {
		t.Fatalf("mirror mismatch: %+v vs %+v", a, b)
	}
	for name, pair := range map[string][2]float64{"low": {a.Low, 200 - b.High}, "high": {a.High, 200 - b.Low}, "proximal": {a.Proximal, 200 - b.Proximal}, "distal": {a.Distal, 200 - b.Distal}, "anchor": {a.AnchorPrice, 200 - b.AnchorPrice}} {
		if math.Abs(pair[0]-pair[1]) > 1e-9 {
			t.Fatalf("%s is not mirrored: %v", name, pair)
		}
	}
	if b.Proximal != b.High || b.Distal != b.Low {
		t.Fatalf("a demand zone is entered from above and invalidated below: %+v", b)
	}
}

// Zones are formed only from bars that exist: a zone appears once its swing (two bars after it) and its departure have closed, with the same base and identity from then on.
func TestZonesAreCausalAndTheirBaseNeverMoves(t *testing.T) {
	specs, _, legStart, legEnd := supplyAt(5)
	bars, atr := candlesOf(specs)
	cfg := zoneTestConfig()
	type key struct{ start, base int }
	var first *SwingZone
	for n := 20; n <= len(bars); n++ {
		// The strategy's legs on the prefix: the leg exists as far as its bars do.
		var legs []Leg
		if n > legStart {
			legs = []Leg{{legStart, minInt(legEnd, n-1)}}
		}
		zones := FormSwingZones(true, bars[:n], atr[:n], legs, cfg)
		for _, z := range zones {
			if z.ConfirmedIndex >= n || z.Swing.ConfirmedAt > bars[n-1].Time || z.DepartureConfirmedAt > bars[n-1].Time {
				t.Fatalf("prefix %d: zone uses bars that are not closed: %+v", n, z)
			}
			if first == nil {
				c := z
				first = &c
			} else if z.BaseStart != first.BaseStart || z.BaseEnd != first.BaseEnd || z.Low != first.Low || z.High != first.High {
				t.Fatalf("prefix %d: the base moved from %+v to %+v", n, first, z)
			}
		}
	}
	if first == nil {
		t.Fatal("the zone never formed")
	}
}

// Base candles that happen to be the departure's colour make the displacement run start inside the consolidation. The zone is the same: the departure begins at the
// first impulse candle after the swing, and the base is the consolidation, not the run.
func TestARunThatStartsInsideTheBaseGivesTheSameZone(t *testing.T) {
	specs, swing, legStart, legEnd := supplyAt(5)
	bars, atr := candlesOf(specs)
	clean := FormSwingZones(true, bars, atr, []Leg{{legStart, legEnd}}, zoneTestConfig())
	run := FormSwingZones(true, bars, atr, []Leg{{legStart - 4, legEnd}}, zoneTestConfig())
	if len(clean) != 1 || len(run) != 1 {
		t.Fatalf("%d and %d zones (swing %d)", len(clean), len(run), swing)
	}
	a, b := clean[0], run[0]
	if a.Low != b.Low || a.High != b.High || a.BaseEnd != b.BaseEnd || a.LegStart != b.LegStart || a.Strength != b.Strength {
		t.Fatalf("the same zone expected: %+v vs %+v", a, b)
	}
}

// departureBars builds the supply fixture with a departure of d strong bars and then a slow drift lower (a quiet tail), so the close through the structural low can come
// after the departure has ended.
func departureBars(d int) (specs []spec, legStart, legEnd int) {
	specs, _, legStart, legEnd = supplyAt(5)
	specs = specs[:legStart]
	price := specs[len(specs)-1].close
	for i := 0; i < d; i++ {
		specs = append(specs, spec{price, price + 0.05, price - 1.9, price - 1.8})
		price -= 1.8
	}
	legEnd = len(specs) - 1
	for i := 0; i < 14; i++ { // the tail: small bearish bars, each well under the departure threshold
		specs = append(specs, spec{price, price + 0.05, price - 0.65, price - 0.6})
		price -= 0.6
	}
	return specs, legStart, legEnd
}

// A zone's strength is the evidence available when it was FORMED (the swing known, the departure closed): it is fixed from then on. A close through the structure that comes
// later is the lifecycle's and the quality's business, never an upgrade of the zone that was formed. Strength at every later prefix equals strength at formation.
func TestZoneStrengthIsFixedAtFormation(t *testing.T) {
	cfg := zoneTestConfig()
	var checked int
	for d := 2; d <= 4; d++ {
		specs, legStart, legEnd := departureBars(d)
		bars, atr := candlesOf(specs)
		var atFormation *SwingZone
		for n := legEnd + 1; n <= len(bars); n++ {
			legs := []Leg{{legStart, minInt(legEnd, n-1)}}
			zones := FormSwingZones(true, bars[:n], atr[:n], legs, cfg)
			if len(zones) != 1 {
				continue
			}
			z := zones[0]
			if atFormation == nil {
				c := z
				atFormation = &c
				continue
			}
			if z.Strength != atFormation.Strength || z.Swing.Class != atFormation.Swing.Class || z.Swing.Shift != atFormation.Swing.Shift {
				t.Fatalf("departure %d, prefix %d: strength %s class %s shift %q, at formation %s %s %q", d, n, z.Strength, z.Swing.Class, z.Swing.Shift, atFormation.Strength, atFormation.Swing.Class, atFormation.Swing.Shift)
			}
			checked++
		}
	}
	if checked == 0 {
		t.Fatal("the fixture must form zones")
	}
}
