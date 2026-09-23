package trendline_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

// trendlineTestConfig mirrors config/analysis.yml's real production
// values (analysis.trendlines.*), not invented numbers.
func trendlineTestConfig() trendline.Config {
	return trendline.Config{
		MinimumSlopeATR:                        0.02,
		MaximumSlopeATR:                        0.15,
		MinimumTouchSpacingBars:                3,
		MinimumSpanBars:                        20,
		MinimumValidationTouchSpacingBars:      5,
		ValidationTouchToleranceATR:            0.30,
		InvalidationPenetrationATR:             0.50,
		CloseViolationATR:                      0.15,
		ApproachMinDistanceATR:                 0.10,
		MinimumValidationFavorableExcursionATR: 0.10,
		ValidationReactionBars:                 2,
		MinimumValidationTouches:               1,
		ExhaustionValidationTouches:            4,
		MaximumWickViolations:                  2,
		DedupValueATR:                          0.5,
		DedupSlopePercent:                      0.2,
		InteractionBandATR:                     0.20,
	}
}

func flatATR(n int, value float64) []float64 {
	series := make([]float64, n)
	for i := range series {
		series[i] = value
	}
	return series
}

func flatCandle(index int, value float64) market.Candle {
	return market.Candle{Time: int64(index), Open: value, High: value, Low: value, Close: value, Volume: 1}
}

func swingAt(id string, kind structure.PivotKind, index int, price float64) structure.Swing {
	return structure.Swing{ID: id, Kind: kind, Price: market.Price(price), Time: int64(index), ConfirmedAt: int64(index)}
}

// TestBuildOnlyPairsChronologicallyAdjacentPivots is the core causal
// invariant this whole domain rests on (see doc.go): three swings whose
// direct first-to-last slope would be ACCEPTED, but whose two adjacent
// slopes are each individually rejected (one too steep, one wrong-sign),
// must yield zero lines. If Build ever tried the non-adjacent A-C pair,
// this would produce one.
func TestBuildOnlyPairsChronologicallyAdjacentPivots(t *testing.T) {
	candles := make([]market.Candle, 21)
	for i := range candles {
		candles[i] = flatCandle(i, 200)
	}
	swings := []structure.Swing{
		swingAt("A", structure.SwingLow, 5, 100), // A-B slope = (300-100)/5 = 40, way past maximum_slope
		swingAt("B", structure.SwingLow, 10, 300),
		swingAt("C", structure.SwingLow, 20, 110), // B-C slope = (110-300)/10 = -19, wrong sign for support
		// A-C slope directly = (110-100)/15 = 0.667 — would be ACCEPTED (inside [0.2,1.5]*atr=10)
		// if non-adjacent pairing were (incorrectly) attempted.
	}
	lines := trendline.Build(candles, flatATR(21, 10), swings, trendlineTestConfig())
	if len(lines) != 0 {
		t.Fatalf("expected zero lines (both adjacent slopes rejected, A-C must never be tried directly), got %+v", lines)
	}
}

// TestBuildRejectsPairsBelowMinimumSpacing: two swings closer together
// than MinimumTouchSpacingBars must never even reach the slope filter.
func TestBuildRejectsPairsBelowMinimumSpacing(t *testing.T) {
	candles := make([]market.Candle, 10)
	for i := range candles {
		candles[i] = flatCandle(i, 200)
	}
	swings := []structure.Swing{
		swingAt("A", structure.SwingLow, 5, 100),
		swingAt("B", structure.SwingLow, 6, 101), // 1 bar apart, spacing < 3
	}
	lines := trendline.Build(candles, flatATR(10, 10), swings, trendlineTestConfig())
	if len(lines) != 0 {
		t.Fatalf("expected zero lines below minimum spacing, got %+v", lines)
	}
}

// TestBuildRejectsSlopeOutsideConfiguredRange covers all four rejection
// edges: support too flat, support too steep, resistance too flat
// (wrong sign requirement), resistance too steep.
func TestBuildRejectsSlopeOutsideConfiguredRange(t *testing.T) {
	cfg := trendlineTestConfig() // atr=10 -> minimumSlope=0.2, maximumSlope=1.5
	candles := make([]market.Candle, 30)
	for i := range candles {
		candles[i] = flatCandle(i, 500)
	}
	cases := []struct {
		name   string
		swings []structure.Swing
	}{
		{"support too flat", []structure.Swing{
			swingAt("A", structure.SwingLow, 5, 100), swingAt("B", structure.SwingLow, 15, 101), // slope=0.1 < 0.2
		}},
		{"support too steep", []structure.Swing{
			swingAt("A", structure.SwingLow, 5, 100), swingAt("B", structure.SwingLow, 15, 300), // slope=20 > 1.5
		}},
		{"resistance wrong sign (rising, not falling)", []structure.Swing{
			swingAt("A", structure.SwingHigh, 5, 100), swingAt("B", structure.SwingHigh, 15, 130), // slope=+3, resistance needs <= -0.2
		}},
		{"resistance too steep", []structure.Swing{
			swingAt("A", structure.SwingHigh, 5, 300), swingAt("B", structure.SwingHigh, 15, 50), // slope=-25, |slope| > 1.5
		}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			lines := trendline.Build(candles, flatATR(30, 10), tc.swings, cfg)
			if len(lines) != 0 {
				t.Fatalf("expected zero lines, got %+v", lines)
			}
		})
	}
}

// TestBuildConfirmsCollinearPairWithValidationAndDedupesTheOverlap is
// the full end-to-end scenario: three perfectly collinear SwingLow
// pivots (A@10=100, B@20=110, C@40=130 — slope=1.0 throughout,
// atr=10). Both adjacent pairs (A,B) and (B,C) independently qualify,
// but project to the IDENTICAL line — dedupe must keep only the older
// anchor pair (A,B), not (B,C). C then serves as (A,B)'s own validation
// touch (a later point validates an EARLIER pair's projection — the
// central invariant this domain exists to enforce), pushing span_bars to
// 30 (>= minimum_span 20) with 1 validation (>= minimum 1), landing on
// StateConfirmed.
func TestBuildConfirmsCollinearPairWithValidationAndDedupesTheOverlap(t *testing.T) {
	cfg := trendlineTestConfig()
	// line(i) = 90 + 1.0*i. Default thresholds already comfortably admit
	// slope=1.0 (minimumSlope=0.2, maximumSlope=1.5 at atr=10); restated
	// explicitly here so the test doesn't silently depend on the shared
	// helper's defaults never changing.
	cfg.MinimumSlopeATR = 0.02
	cfg.MaximumSlopeATR = 0.15

	candles := make([]market.Candle, 43) // indices 0..42
	for i := 0; i < 10; i++ {
		candles[i] = flatCandle(i, 95) // filler before anchor A, never scanned
	}
	candles[10] = market.Candle{Time: 10, Open: 101, High: 102, Low: 100, Close: 100.5, Volume: 1} // anchor A
	for i := 11; i < 20; i++ {
		candles[i] = flatCandle(i, 105) // filler between anchors, never scanned
	}
	candles[20] = market.Candle{Time: 20, Open: 111, High: 112, Low: 110, Close: 110.5, Volume: 1} // anchor B
	for i := 21; i <= 38; i++ {
		lineVal := 90 + float64(i)
		candles[i] = flatCandle(i, lineVal+10) // comfortably clear of the line throughout the health scan
	}
	candles[39] = market.Candle{Time: 39, Open: 130, High: 131.5, Low: 130, Close: 131, Volume: 1} // approach bar before C (line=129, close=131 -> distance 2 >= approachMinDistance 1)
	candles[40] = market.Candle{Time: 40, Open: 130, High: 133, Low: 130, Close: 132, Volume: 1}   // C itself (line=130, touch_error=0)
	candles[41] = market.Candle{Time: 41, Open: 131, High: 131.5, Low: 131, Close: 131.2, Volume: 1}
	candles[42] = market.Candle{Time: 42, Open: 132, High: 133, Low: 132, Close: 133, Volume: 1} // reaction_end (line=132, close=133 -> reclaimed)

	swings := []structure.Swing{
		swingAt("A", structure.SwingLow, 10, 100),
		swingAt("B", structure.SwingLow, 20, 110),
		swingAt("C", structure.SwingLow, 40, 130),
	}

	lines := trendline.Build(candles, flatATR(43, 10), swings, cfg)
	if len(lines) != 1 {
		t.Fatalf("expected exactly 1 line after dedup (A-B and B-C are collinear duplicates), got %d: %+v", len(lines), lines)
	}
	line := lines[0]
	if line.AnchorAIndex != 10 || line.AnchorBIndex != 20 {
		t.Fatalf("expected the OLDER anchor pair (10,20) to survive dedup, got (%d,%d)", line.AnchorAIndex, line.AnchorBIndex)
	}
	if line.State != trendline.StateConfirmed {
		t.Errorf("State = %s, want confirmed (1 validation >= minimum 1, span 30 >= minimum 20)", line.State)
	}
	if len(line.ValidationTouches) != 1 || line.ValidationTouches[0].BarIndex != 40 {
		t.Fatalf("expected exactly 1 validation touch at bar 40, got %+v", line.ValidationTouches)
	}
	if line.SpanBars != 30 {
		t.Errorf("SpanBars = %d, want 30 (40-10)", line.SpanBars)
	}
	if line.Exhausted {
		t.Error("1 validation < exhaustion_validation_touches(4), must not be exhausted")
	}
	if line.BrokenAt != nil {
		t.Errorf("expected a clean health scan, got BrokenAt=%v", *line.BrokenAt)
	}
}

// TestMeasureValidationDefersUntilReactionWindowFullyCloses ports
// _measure_validation's core causality guard: a touch whose reaction
// window would extend past the given candles must NOT be counted yet —
// deferred to whenever enough bars actually exist, never counted early.
func TestMeasureValidationDefersUntilReactionWindowFullyCloses(t *testing.T) {
	cfg := trendlineTestConfig() // slope=1.0 clears default minimumSlope(0.2)/maximumSlope(1.5) comfortably

	// Identical setup to the confirmed-line test above, but candles stop
	// exactly at C's own bar (40) — its reaction window (40+2=42) is not
	// yet available.
	candles := make([]market.Candle, 41) // indices 0..40
	for i := 0; i < 10; i++ {
		candles[i] = flatCandle(i, 95)
	}
	candles[10] = market.Candle{Time: 10, Open: 101, High: 102, Low: 100, Close: 100.5, Volume: 1}
	for i := 11; i < 20; i++ {
		candles[i] = flatCandle(i, 105)
	}
	candles[20] = market.Candle{Time: 20, Open: 111, High: 112, Low: 110, Close: 110.5, Volume: 1}
	for i := 21; i <= 39; i++ {
		lineVal := 90 + float64(i)
		candles[i] = flatCandle(i, lineVal+10)
	}
	candles[40] = market.Candle{Time: 40, Open: 130, High: 133, Low: 130, Close: 132, Volume: 1}

	swings := []structure.Swing{
		swingAt("A", structure.SwingLow, 10, 100),
		swingAt("B", structure.SwingLow, 20, 110),
		swingAt("C", structure.SwingLow, 40, 130),
	}
	lines := trendline.Build(candles, flatATR(41, 10), swings, cfg)
	if len(lines) != 1 {
		t.Fatalf("expected exactly 1 line, got %d: %+v", len(lines), lines)
	}
	if len(lines[0].ValidationTouches) != 0 {
		t.Fatalf("C's reaction window is not yet closed — expected 0 validation touches, got %+v", lines[0].ValidationTouches)
	}
	if lines[0].State != trendline.StateTentative {
		t.Errorf("State = %s, want tentative (0 validations, span from anchor B only)", lines[0].State)
	}
}

// TestHealthWickViolationAloneDoesNotBreakTheLine: a deep wick
// penetration that the SAME bar's close recovers from must not set
// Broken — only an UNRESOLVED close violation does (see lifecycle.go).
func TestHealthWickViolationAloneDoesNotBreakTheLine(t *testing.T) {
	cfg := trendlineTestConfig() // slope=1.0 clears default minimumSlope(0.2)/maximumSlope(1.5) comfortably
	// line(i) = 90 + 1.0*i, atr=10 -> invalidationPenetration=5, closeViolation=1.5
	candles := make([]market.Candle, 22)
	for i := 0; i < 10; i++ {
		candles[i] = flatCandle(i, 95)
	}
	candles[10] = market.Candle{Time: 10, Open: 101, High: 102, Low: 100, Close: 100.5, Volume: 1}
	for i := 11; i < 20; i++ {
		candles[i] = flatCandle(i, 105)
	}
	candles[20] = market.Candle{Time: 20, Open: 111, High: 112, Low: 110, Close: 110.5, Volume: 1}
	// idx21: line=111. Low deeply pierces (penetration=111-91=20 > 5), but
	// close recovers comfortably above the line.
	candles[21] = market.Candle{Time: 21, Open: 112, High: 113, Low: 91, Close: 116, Volume: 1}

	swings := []structure.Swing{
		swingAt("A", structure.SwingLow, 10, 100),
		swingAt("B", structure.SwingLow, 20, 110),
	}
	lines := trendline.Build(candles, flatATR(22, 10), swings, cfg)
	if len(lines) != 1 {
		t.Fatalf("expected exactly 1 line, got %d: %+v", len(lines), lines)
	}
	if lines[0].State == trendline.StateBroken || lines[0].BrokenAt != nil {
		t.Errorf("a recovered wick violation alone must not break the line, got State=%s BrokenAt=%v", lines[0].State, lines[0].BrokenAt)
	}
	if lines[0].WickViolations != 1 {
		t.Errorf("WickViolations = %d, want 1", lines[0].WickViolations)
	}
}

// TestHealthUnresolvedCloseViolationBreaksTheLine: a close beyond the
// invalidated side that NOTHING later reclaims must set Broken, with
// BrokenAt pointing at that bar.
func TestHealthUnresolvedCloseViolationBreaksTheLine(t *testing.T) {
	cfg := trendlineTestConfig() // slope=1.0 clears default minimumSlope(0.2)/maximumSlope(1.5) comfortably
	candles := make([]market.Candle, 22)
	for i := 0; i < 10; i++ {
		candles[i] = flatCandle(i, 95)
	}
	candles[10] = market.Candle{Time: 10, Open: 101, High: 102, Low: 100, Close: 100.5, Volume: 1}
	for i := 11; i < 20; i++ {
		candles[i] = flatCandle(i, 105)
	}
	candles[20] = market.Candle{Time: 20, Open: 111, High: 112, Low: 110, Close: 110.5, Volume: 1}
	// idx21: line=111. Close is 5 below the line (distance=-5 < -1.5),
	// and it is the LAST candle — nothing later reclaims it.
	candles[21] = market.Candle{Time: 21, Open: 111, High: 111, Low: 105, Close: 106, Volume: 1}

	swings := []structure.Swing{
		swingAt("A", structure.SwingLow, 10, 100),
		swingAt("B", structure.SwingLow, 20, 110),
	}
	lines := trendline.Build(candles, flatATR(22, 10), swings, cfg)
	if len(lines) != 1 {
		t.Fatalf("expected exactly 1 line, got %d: %+v", len(lines), lines)
	}
	if lines[0].State != trendline.StateBroken {
		t.Fatalf("State = %s, want broken", lines[0].State)
	}
	if lines[0].BrokenAt == nil || *lines[0].BrokenAt != 21 {
		t.Errorf("BrokenAt = %v, want 21", lines[0].BrokenAt)
	}
}
