package fadescalp

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

func newStrategy(t *testing.T) strategy.Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: "v3", Parameters: legacyfixture.Params(map[string]any{
		"invalidation_buffer_atr": .25, "minimum_reward_risk": 1.0, "expiry_hours": 4.0, "chop_edge_fraction": .25, "strict_premium_discount": true,
	})})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func bars() []market.Candle {
	out := legacyfixture.Bars(10, 100)
	out[9] = market.Candle{Time: 3000, Open: 99.4, High: 100.8, Low: 98.5, Close: 100.5}
	return out
}

func equalLow(touches int) techniquezone.Pool {
	return techniquezone.Pool{Side: "sell", Level: 99.15, Band: .1, Touches: touches}
}

func grab(grade string) techniquezone.Grab {
	return techniquezone.Grab{Index: 9, Direction: "bull", Grade: grade, Pool: equalLow(3), ReactionDisplacement: true}
}

func qualifying(mutate func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead)) *analysiscontext.MarketContext {
	return legacyfixture.Context(bars(), 1, func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		f.Pools = []techniquezone.Pool{equalLow(3), {Side: "buy", Level: 103, Band: .1, Touches: 1}}
		f.Grabs = []techniquezone.Grab{grab("B")}
		if mutate != nil {
			mutate(f, r)
		}
	})
}

func TestFadeBuysAnEqualLowSweepWithAStructuralReaction(t *testing.T) {
	got := newStrategy(t).Evaluate(qualifying(nil))
	if len(got) != 1 {
		t.Fatalf("an equal-low BUY fade is missing: %+v", got)
	}
	c := got[0]
	if c.Direction != market.Buy || c.Reaction == nil || c.Reaction.Pattern != "sweep_reclaim" {
		t.Fatalf("unexpected candidate: %+v", c)
	}
	// The unconfirmed single-touch low at 103 is the objective of the fade.
	if c.DetectorConfluence == nil || c.DetectorConfluence.SelectedStars != 2 {
		t.Fatalf("factor score 10/20.5 is two stars: %+v", c.DetectorConfluence)
	}
	if float64(c.Targets[0].Price.Price) != 103 {
		t.Fatalf("the nearest opposing pool is the target, got %v", c.Targets[0].Price.Price)
	}
}

func TestFadeRejections(t *testing.T) {
	cases := map[string]func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead){
		"a swing low is not an equal level": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Pools = []techniquezone.Pool{equalLow(1)}
		},
		"pool on the wrong side": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			p := equalLow(3)
			p.Side = "buy"
			f.Pools = []techniquezone.Pool{p}
		},
		"no grab on the level": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) { f.Grabs = nil },
		"grade C grab": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Grabs = []techniquezone.Grab{grab("C")}
		},
		"grab on a different pool": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			g := grab("B")
			g.Pool.Level = 90
			f.Grabs = []techniquezone.Grab{g}
		},
		"premium location": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Range = &fib.DealingRange{Zone: "premium"}
		},
		"no direction": func(_ *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
			r.LocalStructure, r.HTFBias = "range", "range"
		},
		"no reaction": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Bars = legacyfixture.Bars(10, 104)
		},
	}
	s := newStrategy(t)
	for name, mutate := range cases {
		if got := s.Evaluate(qualifying(mutate)); len(got) != 0 {
			t.Fatalf("%s must reject: %+v", name, got)
		}
	}
}

func TestFadeInChopNeedsGradeAAtTheRangeEdge(t *testing.T) {
	s := newStrategy(t)
	chop := func(low, high float64, g string) *analysiscontext.MarketContext {
		return qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Regime = regime.State{Kind: "chop", RangeLow: low, RangeHigh: high}
			f.Grabs = []techniquezone.Grab{grab(g)}
		})
	}
	if got := s.Evaluate(chop(99, 110, "B")); len(got) != 0 {
		t.Fatal("in chop a Grade B sweep must be rejected")
	}
	if got := s.Evaluate(chop(99, 110, "A")); len(got) != 1 {
		t.Fatal("in chop a Grade A sweep at the lower edge qualifies")
	}
	if got := s.Evaluate(chop(90, 100, "A")); len(got) != 0 {
		t.Fatal("in chop a BUY away from the lower range edge must be rejected")
	}
	if got := s.Evaluate(chop(99, 110, "A")); len(got) == 1 {
		found := false
		for _, e := range got[0].Evidence {
			found = found || e.Code == "chop_edge_grade_a"
		}
		if !found {
			t.Fatal("the chop edge acceptance must be recorded as evidence")
		}
	}
}

func TestFadeSellMirrorsBuy(t *testing.T) {
	out := legacyfixture.Bars(10, 100)
	out[9] = market.Candle{Time: 3000, Open: 100.6, High: 101.5, Low: 99.2, Close: 99.5}
	ctx := legacyfixture.Context(out, 1, func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		r.LocalStructure, r.HTFBias = "down", "down"
		pool := techniquezone.Pool{Side: "buy", Level: 100.85, Band: .1, Touches: 3}
		f.Pools = []techniquezone.Pool{pool, {Side: "sell", Level: 97, Band: .1, Touches: 1}}
		f.Grabs = []techniquezone.Grab{{Index: 9, Direction: "bear", Grade: "B", Pool: pool, ReactionDisplacement: true}}
	})
	got := newStrategy(t).Evaluate(ctx)
	if len(got) != 1 || got[0].Direction != market.Sell {
		t.Fatalf("equal-high SELL fade missing: %+v", got)
	}
}

// The thesis is a reversion to a structural objective: the nearest of the local mean and the opposing liquidity, and
// with neither there is no opportunity (v2 invented a 2R target).
func TestFadeAimsAtTheNearestStructuralObjectiveAndNeverInventsOne(t *testing.T) {
	// The opposing pool at 103 is the only objective.
	got := newStrategy(t).Evaluate(qualifying(nil))
	if len(got) != 1 || got[0].Targets[0].Price.Label != "opposing_liquidity" {
		t.Fatalf("a pool target is opposing liquidity: %+v", got)
	}
	// The dealing range's equilibrium at 101.8 is nearer than the pool: it decides.
	mean := newStrategy(t).Evaluate(qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Range = &fib.DealingRange{Zone: "discount", Equilibrium: 101.8}
	}))
	if len(mean) != 1 || mean[0].Targets[0].Price.Label != strategyutil.MeanDealingEquilibrium || float64(mean[0].Targets[0].Price.Price) != 101.8 {
		t.Fatalf("the nearer local mean is the objective: %+v", mean)
	}
	// The established range's own equilibrium is preferred to the dealing range's.
	ranged := newStrategy(t).Evaluate(qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Range = &fib.DealingRange{Zone: "discount", Equilibrium: 102.5}
		f.ScalpRange = &techniquezone.ScalpRange{Equilibrium: 101.9, State: techniquezone.RangeStateConfirmed}
	}))
	if len(ranged) != 1 || ranged[0].Targets[0].Price.Label != strategyutil.MeanRangeEquilibrium {
		t.Fatalf("an unbroken range's equilibrium is the mean: %+v", ranged)
	}
	// No pool, no mean: a fixed multiple of the risk is not a structural objective.
	none := newStrategy(t).Evaluate(qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Pools = []techniquezone.Pool{equalLow(3)}
	}))
	if len(none) != 0 {
		t.Fatalf("a fade with no structural objective must not be published: %+v", none)
	}
}

func TestFadeNeedsTheRoomItsRiskDemands(t *testing.T) {
	// An objective 0.65 above the entry leaves less than the 1.0 risk.
	near := newStrategy(t).Evaluate(qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Pools = []techniquezone.Pool{equalLow(3), {Side: "buy", Level: 99.9, Band: .1, Touches: 1}}
	}))
	if len(near) != 0 {
		t.Fatalf("an objective that leaves less than the minimum reward:risk must reject: %+v", near)
	}
	// The nearest objective decides: a far pool behind a too-near mean is not a way out.
	blocked := newStrategy(t).Evaluate(qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Range = &fib.DealingRange{Zone: "discount", Equilibrium: 99.9}
	}))
	if len(blocked) != 0 {
		t.Fatalf("a near mean must not be skipped in favour of a farther pool: %+v", blocked)
	}
}

func TestFadeStopSitsBeyondTheSweepExtreme(t *testing.T) {
	got := newStrategy(t).Evaluate(qualifying(nil))
	if len(got) != 1 {
		t.Fatal("fixture must qualify")
	}
	// The sweep candle ran to 98.5; the thesis fails when that is exceeded.
	if invalid := float64(got[0].Invalidation.Price); !(invalid < 98.5) {
		t.Fatalf("the stop %v is inside the sweep's wick (low 98.5)", invalid)
	}
}

func TestFadeRefusesASweepThatKeptRunning(t *testing.T) {
	out := bars()
	// The grabbed candle is bar 8; bar 9 trades below its low and is not reclaimed as a sweep: the move continued.
	out[8] = market.Candle{Time: 2700, Open: 99.4, High: 100.2, Low: 98.8, Close: 100}
	out[9] = market.Candle{Time: 3000, Open: 99.6, High: 100.8, Low: 98.4, Close: 100.5}
	got := newStrategy(t).Evaluate(legacyfixture.Context(out, 1, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Pools = []techniquezone.Pool{equalLow(3), {Side: "buy", Level: 103, Band: .1, Touches: 1}}
		f.Grabs = []techniquezone.Grab{{Index: 8, Direction: "bull", Grade: "B", Pool: equalLow(3), ReactionDisplacement: true}}
	}))
	if len(got) != 0 {
		t.Fatalf("a candle after the sweep traded beyond its extreme: not exhausted, must reject: %+v", got)
	}
}

// The confluence factors are what the candles showed: the frozen detector set the rejection factor on every decision.
func TestFadeFactorsAreMeasuredNotAsserted(t *testing.T) {
	plain := newStrategy(t).Evaluate(qualifying(nil))
	if len(plain) != 1 {
		t.Fatal("fixture must qualify")
	}
	// The fixture's sweep candle (open 99.4, close 100.5, low 98.5) has a lower wick shorter than its body.
	if f := plain[0].DetectorConfluence.Factors; f.WickRejection || !f.DisplacementGrade || f.SessionContext {
		t.Fatalf("factors must follow the candles: %+v", f)
	}
	hammer := bars()
	hammer[9] = market.Candle{Time: 3000, Open: 100.3, High: 100.8, Low: 98.5, Close: 100.6}
	got := newStrategy(t).Evaluate(legacyfixture.Context(hammer, 1, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Pools = []techniquezone.Pool{equalLow(3), {Side: "buy", Level: 103, Band: .1, Touches: 1}}
		f.Grabs = []techniquezone.Grab{grab("B")}
	}))
	if len(got) != 1 || !got[0].DetectorConfluence.Factors.WickRejection {
		t.Fatalf("a hammer sweep candle is a wick rejection: %+v", got)
	}
	for _, key := range []string{"extension_atr", "sweep_body_ratio", "sweep_lower_wick", "reward_risk", "target_room_atr"} {
		if _, ok := got[0].Quality.Components[key]; !ok {
			t.Errorf("component %q is missing", key)
		}
	}
	if _, constant := got[0].Quality.Components["reaction"]; constant {
		t.Error("a constant reaction component carries no information")
	}
}

// Every confirmed sweep reaction that is refused is accounted for with the reason, and the account never changes a decision.
func TestAnalyzeNamesTheReasonEachConfirmedReactionWasRefused(t *testing.T) {
	s := newStrategy(t).(*Strategy)
	reasonOf := func(ctx *analysiscontext.MarketContext) string {
		t.Helper()
		analysis, candidates := s.Analyze(ctx)
		if len(candidates) != 0 || len(analysis.Refused()) != 1 {
			t.Fatalf("want one refusal and no candidate, got %+v / %d", analysis.Refused(), len(candidates))
		}
		return analysis.Refused()[0].Reason
	}
	// Only the confluence is short: the displacement the sweep showed is not credited.
	if got := reasonOf(qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		g := grab("B")
		g.ReactionDisplacement = false
		f.Grabs = []techniquezone.Grab{g}
	})); got != ReasonConfluenceFloor {
		t.Fatalf("reason %q", got)
	}
	if got := reasonOf(qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Pools = []techniquezone.Pool{equalLow(3)}
	})); got != ReasonNoObjective {
		t.Fatalf("reason %q", got)
	}
	if got := reasonOf(qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Pools = []techniquezone.Pool{equalLow(3), {Side: "buy", Level: 99.9, Band: .1, Touches: 1}}
	})); got != ReasonRewardRisk {
		t.Fatalf("reason %q", got)
	}
	// The refusal carries the geometry it would have had, which is what a replay needs to measure the gate.
	analysis, _ := s.Analyze(qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Pools = []techniquezone.Pool{equalLow(3), {Side: "buy", Level: 99.9, Band: .1, Touches: 1}}
	}))
	r := analysis.Refused()[0]
	if r.Direction != market.Buy || !(r.Invalidation < 98.5) || r.RewardRisk <= 0 || r.RewardRisk >= 1 || r.ObjectiveLabel != "opposing_liquidity" {
		t.Fatalf("rejection geometry %+v", r)
	}
	// A qualifying setup is published and refused nothing.
	if analysis, candidates := s.Analyze(qualifying(nil)); len(candidates) != 1 || len(analysis.Refused()) != 0 {
		t.Fatalf("qualifying setup: %d candidates, rejections %+v", len(candidates), analysis.Refused())
	}
}
