package demand

import (
	"math"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// SD-4 quality audit. Each "Defect" test failed on demand.quality.v3 and states what the dimension must measure.

// qualityFixture is one demand opportunity: the swing zone of majorDemandFrame, an M5 touch bar of a chosen shape and a decision on it.
type qualityFixture struct {
	s        *Strategy
	d        *decision
	atrScale float64
}

// touchBar is a reaction bar for a SELL: a long lower wick that closes in its upper part.
var touchBar = market.Candle{Open: 0, High: 0, Low: 0, Close: 0}

func newQualityFixture(t *testing.T, scale float64, conf *confirmation, mutate func(*instance)) qualityFixture {
	t.Helper()
	frame, _ := majorDemandFrame()
	if scale != 1 {
		for i := range frame.Bars {
			c := &frame.Bars[i]
			c.Open, c.High, c.Low, c.Close = c.Open*scale, c.High*scale, c.Low*scale, c.Close*scale
			frame.ATR[i] *= scale
		}
	}
	s := swingStrategy(structure.StrengthModerate)
	instances, _, _ := readWith(t, s, frame)
	if len(instances) != 1 {
		t.Fatalf("setup: %d instances", len(instances))
	}
	in := instances[0]
	if mutate != nil {
		mutate(&in)
	}
	structLow, structHigh := in.structural()
	price := structHigh
	v := execView(s, in, price)
	// the M5 bars: quiet, with the touch bar a long-upper-wick rejection of the zone
	last := len(v.frame.Bars) - 1
	v.frame.Bars[last-1] = market.Candle{Time: v.frame.Bars[last-1].Time, Open: price, High: price + 0.05*scale, Low: price - 0.9*scale, Close: price + 0.02*scale}
	v.frame.Bars[last] = market.Candle{Time: v.frame.Bars[last].Time, Open: price + 0.02*scale, High: price + 0.6*scale, Low: price - 0.05*scale, Close: price + 0.55*scale}
	if conf == nil {
		conf = &confirmation{Type: patternWickRejection, TouchIndex: last - 1, ConfirmationIndex: last}
	}
	f := in.Formation
	reference, invalidation := f.Proximal, structLow-0.5*scale
	d := &decision{inst: in, v: v, conf: conf, res: &result{}, id: "z", reference: reference, entryLow: f.Low, entryHigh: f.High, invalidation: invalidation,
		room: roomResult{Target: reference + 3*(reference-invalidation), Label: "nearest_opposing_structure", RoomR: 3}}
	return qualityFixture{s: s, d: d, atrScale: scale}
}

func (q qualityFixture) score(execATR float64, structural bool) map[string]float64 {
	d := q.d
	got := q.s.quality(candidateContext{symbol: "XAU", market: nil}, d, d.reference, d.invalidation, d.room.Target, structural, execATR)
	return got.Components
}

func near(a, b float64) bool { return math.Abs(a-b) < 1e-9 }

// Defect: zone_precision divided the M15/H1 structure's width by the M5 EXECUTION ATR, so identical structure scored differently as M5 volatility changed (and about half of
// the published opportunities scored 0 because a 1.5-ATR M15 zone is 3 to 5 M5 ATR wide). Formation geometry is measured in the formation frame's own ATR.
func TestZonePrecisionDoesNotDependOnTheExecutionATR(t *testing.T) {
	q := newQualityFixture(t, 1, nil, nil)
	calm, wild := q.score(0.5, true), q.score(4.0, true)
	for _, name := range []string{"score_zone_precision", "zone_width_atr", "score_zone_departure", "score_swing_significance", "score_displacement_efficiency", "score_structure_shift", "score_zone_freshness"} {
		if !near(calm[name], wild[name]) {
			t.Fatalf("%s changed with the M5 ATR: %v at 0.5, %v at 4.0 (the structure did not change)", name, calm[name], wild[name])
		}
	}
	if width := calm["zone_width_atr"]; !(width > 0) || width > 3.0+1e-9 {
		t.Fatalf("the formation width (in the structure frame's ATR) must lie inside the zone validity bounds, got %v", width)
	}
}

// Defect: with no valid M5 ATR the model fabricated or lost evidence: a width and the touch count were skipped (so freshness, which has nothing to do with ATR, scored 0), and the
// view had silently substituted 1.0 for the ATR. Missing evidence earns no credit and the rest of the model is unaffected.
func TestMissingExecutionATRFabricatesNothingAndLosesNothingElse(t *testing.T) {
	q := newQualityFixture(t, 1, nil, nil)
	ref, missing := q.score(1.0, true), q.score(math.NaN(), true)
	if missing["score_entry_location"] != 0 {
		t.Fatalf("entry location needs the M5 ATR; without it there is no credit, got %v", missing["score_entry_location"])
	}
	if _, ok := missing["entry_chase_atr"]; ok {
		t.Fatal("no chase may be published without an ATR")
	}
	for _, name := range []string{"score_zone_precision", "score_zone_freshness", "score_zone_departure", "score_swing_significance"} {
		if !near(ref[name], missing[name]) {
			t.Fatalf("%s depends on the M5 ATR: %v vs %v", name, ref[name], missing[name])
		}
	}
	if missing["execution_atr_available"] != 0 || ref["execution_atr_available"] != 1 {
		t.Fatalf("the missing evidence must be observable: %v / %v", ref["execution_atr_available"], missing["execution_atr_available"])
	}
}

// Defect: CHoCH scored 1 and BOS 0.5 (and the swing's own class already requires a shift for MAJOR). A confirmed structure break is the evidence; a CHoCH supports a reversal-origin zone and a
// BOS a continuation-origin one, and neither is worth more as a Supply/Demand origin. The kind is published, not scored.
func TestStructureShiftScoresTheBreakNotAPreferenceForReversals(t *testing.T) {
	score := func(shift string) (float64, float64) {
		q := newQualityFixture(t, 1, nil, func(in *instance) {
			f := *in.Formation
			f.Swing.Shift = shift
			in.Formation = &f
		})
		c := q.score(1, true)
		return c["score_structure_shift"], c["structure_shift_kind"]
	}
	choch, kc := score(structure.ShiftCHoCH)
	bos, kb := score(structure.ShiftBOS)
	none, kn := score("")
	if choch != 1 || bos != 1 || none != 0 {
		t.Fatalf("CHoCH %v, BOS %v, none %v: a confirmed break is 1 either way", choch, bos, none)
	}
	if kc == kb || kn != 0 {
		t.Fatalf("the kind is published as a measurement: %v %v %v", kc, kb, kn)
	}
}

// Defect: swing_significance was the swing's prominence, the weaker of its two sides, which for a swing price has left is its DEPARTURE: the same displacement scored twice, in
// zone_departure and in swing_significance. The significance is how far price travelled INTO the swing (the approach), which the departure does not contain.
func TestSwingSignificanceDoesNotContainTheDeparture(t *testing.T) {
	score := func(prominence, departure, approach float64) float64 {
		q := newQualityFixture(t, 1, nil, func(in *instance) {
			f := *in.Formation
			f.Swing.ProminenceATR, f.Swing.DepartureATR, f.Swing.ApproachATR = prominence, departure, approach
			in.Formation = &f
		})
		return q.score(1, true)["score_swing_significance"]
	}
	if a, b := score(2.0, 2.0, 4.0), score(6.0, 6.0, 4.0); !near(a, b) {
		t.Fatalf("a bigger departure with the same approach changed the swing's significance: %v vs %v", a, b)
	}
	if a, b := score(3.0, 3.0, 2.0), score(3.0, 3.0, 5.0); !(b > a) {
		t.Fatalf("a longer approach is a more significant swing: %v vs %v", a, b)
	}
}

// Defect: the rejection dimension scored the shape of the touch bar whether or not any reaction pattern was recognised, so an ordinary touch (admitted because candle confirmation is not
// required) earned reaction credit from an incidental wick. No recognised reaction is no reaction evidence: a touch-only trade stays eligible and scores 0 here.
func TestATouchOnlyEarnsNoReactionCredit(t *testing.T) {
	last := 11
	touchOnly := newQualityFixture(t, 1, &confirmation{Type: "", TouchIndex: last - 1, ConfirmationIndex: last - 1}, nil)
	confirmed := newQualityFixture(t, 1, &confirmation{Type: patternWickRejection, TouchIndex: last - 1, ConfirmationIndex: last}, nil)
	a, b := touchOnly.score(1, true), confirmed.score(1, true)
	if a["score_rejection"] != 0 || a["reaction_confirmed"] != 0 {
		t.Fatalf("a touch with no recognised reaction earns no reaction credit: %v / %v", a["score_rejection"], a["reaction_confirmed"])
	}
	if !(b["score_rejection"] > 0) || b["reaction_confirmed"] != 1 {
		t.Fatalf("a confirmed reaction is scored from its candles: %v", b["score_rejection"])
	}
}

// Defect: zone_freshness was Fall(touches, 1, 5): a zone touched once before scored exactly as a never-touched one, and with the strategy's own limit of two touches the dimension had two
// values. Remaining freshness is measured against the touches the strategy allows before a zone is spent.
func TestFreshnessSeparatesFreshOnceRetestedAndTwiceRetested(t *testing.T) {
	at := func(touches int) float64 {
		q := newQualityFixture(t, 1, nil, func(in *instance) { in.Touches = touches })
		return q.score(1, true)["score_zone_freshness"]
	}
	f0, f1, f2 := at(0), at(1), at(2)
	if !(f0 > f1 && f1 > f2 && f2 > 0) {
		t.Fatalf("fresh %v, once retested %v, twice retested %v", f0, f1, f2)
	}
}

// A structure shifted into other price units has the same structural quality: every formation and decision measure is in ATR or a ratio.
func TestQualityIsPriceScaleInvariant(t *testing.T) {
	a := newQualityFixture(t, 1, nil, nil).score(1, true)
	b := newQualityFixture(t, 25, nil, nil).score(25, true)
	for name, v := range a {
		if len(name) > 6 && name[:6] == "score_" && !near(v, b[name]) {
			t.Fatalf("%s changed with the price unit: %v vs %v", name, v, b[name])
		}
	}
}

// Overall is the mean of the score_* dimensions, every dimension is in [0,1], and the model says which version scored it.
func TestOverallIsTheMeanAndEveryDimensionIsInRange(t *testing.T) {
	for _, structural := range []bool{true, false} {
		c := newQualityFixture(t, 1, nil, nil).score(1, structural)
		q := newQualityFixture(t, 1, nil, nil)
		got := q.s.quality(candidateContext{symbol: "XAU"}, q.d, q.d.reference, q.d.invalidation, q.d.room.Target, structural, 1)
		if err := quality.Verify(got.Overall, got.Components); err != nil {
			t.Fatal(err)
		}
		if c["quality_model_version"] != 4 {
			t.Fatalf("swing zones are scored by supply.quality.v4, got %v", c["quality_model_version"])
		}
		for name, v := range got.Components {
			if len(name) > 6 && name[:6] == "score_" && (v < 0 || v > 1) {
				t.Fatalf("%s = %v is outside [0,1]", name, v)
			}
		}
	}
}

// A target that is a fixed reward:risk (no observed structure) is not room: no credit without an actual barrier, and the geometry is published for the structural case.
func TestAProjectedTargetEarnsNoRoomCredit(t *testing.T) {
	q := newQualityFixture(t, 1, nil, nil)
	projected, structural := q.score(1, false), q.score(1, true)
	if projected["score_room"] != 0 || projected["room_structural"] != 0 {
		t.Fatalf("a projected target is not structural room: %v / %v", projected["score_room"], projected["room_structural"])
	}
	if !(structural["score_room"] > 0) || structural["room_structural"] != 1 {
		t.Fatalf("an observed structural target earns room: %v", structural["score_room"])
	}
	_ = strategyutil.EntryProximal
	_ = analysiscontext.MarketContext{}
	_ = touchBar
}

// supply and demand score the exact price mirror of one another identically: this vector is the same literal in both packages' quality tests, computed from the mirrored fixtures.
func TestMirroredFixturesHaveTheSameQualityVector(t *testing.T) {
	golden := map[string]float64{
		"effective_room_r":              3,
		"reaction_confirmed":            1,
		"score_displacement_efficiency": 1,
		"score_entry_location":          1,
		"score_rejection":               1,
		"score_room":                    1,
		"score_structure":               0.5,
		"score_structure_shift":         1,
		"score_swing_significance":      1,
		"score_zone_departure":          1,
		"score_zone_freshness":          1,
		"score_zone_precision":          0.9,
		"structure_shift_kind":          1,
		"swing_approach_atr":            8.48,
		"swing_prominence_atr":          8.4,
		"zone_departure_atr":            7.2,
		"zone_width_atr":                1.7,
	}
	got := newQualityFixture(t, 1, nil, nil).score(1, true)
	for name, want := range golden {
		if math.Abs(got[name]-want) > 1e-7 {
			t.Fatalf("%s = %.9f, the supply/demand golden is %.9f", name, got[name], want)
		}
	}
}
