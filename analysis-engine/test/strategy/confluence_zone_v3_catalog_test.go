package strategy_test

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/confluencezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

// confluenceZoneParams is the Confluence Zone v3 parameter set (config/analysis.yml, XAU scale).
func confluenceZoneParams() map[string]any {
	return legacyfixture.Params(map[string]any{
		"minimum_facts": 2.0, "minimum_overlap_pips": 1.0, "minimum_overlap_atr": 0.05, "reaction_lookback_bars": 3.0,
		"minimum_sweep_pips": 1.0, "minimum_sweep_atr": 0.05, "minimum_reclaim_atr": 0.0, "rejection_wick_ratio": 0.25,
		"rejection_close_strength": 0.6, "displacement_body_ratio": 0.6, "displacement_range_atr": 0.5, "displacement_grade_atr": 1.0,
		"invalidation_buffer_atr": 0.5, "minimum_target_distance_atr": 1.0, "minimum_reward_risk": 1.15, "expiry_hours": 12.0,
	})
}

const cz0 = int64(1_760_000_100)

func czBar(i int, o, h, l, c float64) market.Candle {
	return market.Candle{Time: cz0 + int64(i)*300, Open: o, High: h, Low: l, Close: c, Volume: 1}
}

// czBars is 30 quiet candles drifting down to 4106, then the last candle.
func czBars(lastOpen, lastHigh, lastLow, lastClose float64) []market.Candle {
	bars := make([]market.Candle, 30)
	for i := range bars {
		bars[i] = czBar(i, 4110, 4110.5, 4109.5, 4110)
	}
	bars[27] = czBar(27, 4109, 4109.5, 4106.5, 4107)
	bars[28] = czBar(28, 4107, 4107.2, 4105.5, 4106)
	bars[29] = czBar(29, lastOpen, lastHigh, lastLow, lastClose)
	return bars
}

// czContext carries two demand structures from two separate displacement legs
// (4100-4105 and 4103-4108, overlapping at 4103-4105) and one opposing pool.
func czContext(bars []market.Candle, structure string, mutate func(*analysiscontext.LegacyFrame)) *analysiscontext.MarketContext {
	return legacyfixture.Context(bars, 5, func(f *analysiscontext.LegacyFrame, read *analysiscontext.LegacyRead) {
		f.Structure = structure
		f.Legs = []techniquezone.Leg{{Start: 5, End: 8, Direction: "up"}, {Start: 12, End: 14, Direction: "up"}}
		f.Pools = []techniquezone.Pool{{Side: "buy", Level: 4125}}
		instances := []techniquezone.Instance{
			{Technique: "supply_demand", Side: "buy", Low: 4100, High: 4105, OriginIndex: 4, ZoneScore: 9.5},
			{Technique: "supply_demand", Side: "buy", Low: 4103, High: 4108, OriginIndex: 11, ZoneScore: 9},
		}
		f.Techniques = func() []techniquezone.Instance { return instances }
		if mutate != nil {
			mutate(f)
		}
	})
}

func czStrategy(t *testing.T) strategy.Strategy {
	t.Helper()
	s, err := confluencezone.New(strategy.Config{ID: confluencezone.ID, Version: confluencezone.Version, Enabled: true, Parameters: confluenceZoneParams()})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func confirmed(cs []opportunity.Candidate) []opportunity.Candidate {
	var out []opportunity.Candidate
	for _, c := range cs {
		if c.Reaction != nil {
			out = append(out, c)
		}
	}
	return out
}

func TestConfluenceZoneV3KnownQualifyingFixture(t *testing.T) {
	cs := confirmed(czStrategy(t).Evaluate(czContext(czBars(4104.5, 4106, 4102, 4105.5), "up", nil)))
	if len(cs) != 1 {
		t.Fatalf("want one confirmed candidate, got %d", len(cs))
	}
	c := cs[0]
	if c.Strategy != "confluence_zone" || c.StrategyVersion != "v3" || c.Direction != market.Buy {
		t.Fatalf("%s %s %s", c.Strategy, c.StrategyVersion, c.Direction)
	}
	// The entry is the overlap of the two independent structures, not their union.
	if c.Entry.Low != 4103 || c.Entry.High != 4105 {
		t.Fatalf("entry %+v", c.Entry)
	}
	if float64(c.Invalidation.Price) != 4097.5 || float64(c.Targets[0].Price.Price) != 4125 || c.Targets[0].Price.Label != "nearest_opposing_liquidity" {
		t.Fatalf("invalidation %v target %+v", c.Invalidation, c.Targets[0])
	}
	if c.Reaction.ZoneID != c.StructuralID || c.Reaction.Pattern != "sweep_reclaim" || c.Reaction.ReactionType != "rejection" {
		t.Fatalf("reaction %+v structural %s", c.Reaction, c.StructuralID)
	}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	// The evidence names the independent overlap and the reaction.
	have := map[string]bool{}
	for _, e := range c.Evidence {
		have[e.Code] = true
	}
	for _, want := range []string{"m5_distinct_zone_overlap", "m5_confluence_independent_facts", "m5_confluence_reaction"} {
		if !have[want] {
			t.Fatalf("missing evidence %s in %+v", want, c.Evidence)
		}
	}
	// Arbitration-facing quality keeps its scale: the confluence stars out of three.
	if c.Quality.Overall != 2.0/3 {
		t.Fatalf("quality %v", c.Quality.Overall)
	}
	comp := c.Quality.Components
	if comp["independent_fact_count"] != 2 || comp["source_dependency_count"] != 0 || comp["target_room_r"] < 2.6 || comp["rejection_wick_ratio"] != 0.625 {
		t.Fatalf("components %+v", comp)
	}
}

func TestConfluenceZoneV3FactorsAreMeasuredNotAssumed(t *testing.T) {
	// A displacement that closes through the region with almost no wick: the
	// confirmed candidate must not claim a wick rejection it did not show.
	cs := confirmed(czStrategy(t).Evaluate(czContext(czBars(4104.8, 4108, 4104.6, 4107.8), "up", nil)))
	if len(cs) != 1 {
		t.Fatalf("got %d", len(cs))
	}
	f := cs[0].DetectorConfluence.Factors
	if f.WickRejection {
		t.Fatal("a displacement candle with no wick must not report a wick rejection")
	}
	if f.DisplacementGrade {
		t.Fatal("a 0.6 ATR body is not displacement grade (1.0 ATR)")
	}
	// A sweep reclaim has the wick.
	rej := confirmed(czStrategy(t).Evaluate(czContext(czBars(4104.5, 4106, 4102, 4105.5), "up", nil)))[0].DetectorConfluence.Factors
	if !rej.WickRejection || !rej.StructuralAgreement {
		t.Fatalf("%+v", rej)
	}
	// The same candle in a down structure does not agree with a BUY.
	down := confirmed(czStrategy(t).Evaluate(czContext(czBars(4104.5, 4106, 4102, 4105.5), "down", nil)))[0]
	if down.DetectorConfluence.Factors.StructuralAgreement {
		t.Fatal("structural agreement must be false against the engine's own down structure")
	}
	if v, ok := down.Quality.Components["structural_alignment"]; !ok || v != 0 {
		t.Fatalf("structural_alignment %v %v", v, ok)
	}
	// When the engine has no structural read the fact is left out, not neutral.
	none := confirmed(czStrategy(t).Evaluate(czContext(czBars(4104.5, 4106, 4102, 4105.5), "range", nil)))[0]
	if _, ok := none.Quality.Components["structural_alignment"]; ok {
		t.Fatal("an unavailable fact must be omitted")
	}
}

func TestConfluenceZoneV3RestingObservationIsNeverAConfirmation(t *testing.T) {
	// The candle touches the overlap and rejects nothing.
	cs := czStrategy(t).Evaluate(czContext(czBars(4104.2, 4104.9, 4103.9, 4104.4), "up", nil))
	if len(cs) != 1 {
		t.Fatalf("want one resting observation, got %d", len(cs))
	}
	c := cs[0]
	if c.Reaction != nil {
		t.Fatalf("a resting overlap must not carry a reaction: %+v", c.Reaction)
	}
	for _, e := range c.Evidence {
		if e.Code == "m5_confluence_reaction" {
			t.Fatal("a resting observation must not claim reaction evidence")
		}
	}
}

func TestConfluenceZoneV3RepeatedEvaluationIsOneOpportunity(t *testing.T) {
	bars := czBars(4104.5, 4106, 4102, 4105.5)
	first := confirmed(czStrategy(t).Evaluate(czContext(bars, "up", nil)))
	next := append(append([]market.Candle(nil), bars...), czBar(30, 4105.5, 4106.4, 4105.2, 4106.0))
	again := confirmed(czStrategy(t).Evaluate(czContext(next, "up", func(f *analysiscontext.LegacyFrame) {})))
	if len(first) != 1 || len(again) != 1 || first[0].ID != again[0].ID || first[0].StructuralID != again[0].StructuralID {
		t.Fatalf("one confirmation, one opportunity: %d then %d", len(first), len(again))
	}
}

func TestConfluenceZoneV3RefusesStructuresOfOneOrigin(t *testing.T) {
	// Both structures cut from the same displacement leg.
	cs := czStrategy(t).Evaluate(czContext(czBars(4104.5, 4106, 4102, 4105.5), "up", func(f *analysiscontext.LegacyFrame) {
		f.Legs = []techniquezone.Leg{{Start: 5, End: 8, Direction: "up"}}
		instances := []techniquezone.Instance{
			{Technique: "supply_demand", Side: "buy", Low: 4100, High: 4105, OriginIndex: 4, ZoneScore: 9.5},
			{Technique: "order_block", Side: "buy", Low: 4103, High: 4108, OriginIndex: 4, ZoneScore: 9},
		}
		f.Techniques = func() []techniquezone.Instance { return instances }
	}))
	if len(cs) != 0 {
		t.Fatalf("one origin is not confluence: %d candidates", len(cs))
	}
}

func TestConfluenceZoneV3NeverSuppressesAnIndependentStrategysCandidate(t *testing.T) {
	// Confluence Zone reads facts; it publishes its own candidates and removes
	// nothing: the frame's instances are unchanged by an evaluation.
	ctx := czContext(czBars(4104.5, 4106, 4102, 4105.5), "up", nil)
	frame := ctx.Timeframes[market.M5].Legacy
	before := frame.Techniques()
	_ = czStrategy(t).Evaluate(ctx)
	after := frame.Techniques()
	if len(before) != len(after) {
		t.Fatal("the instances changed")
	}
}

func TestConfluenceZoneV3RejectsMissingConfiguration(t *testing.T) {
	for _, key := range []string{"minimum_facts", "minimum_overlap_pips", "rejection_wick_ratio", "minimum_reward_risk", "displacement_range_atr"} {
		params := confluenceZoneParams()
		delete(params, key)
		if _, err := confluencezone.New(strategy.Config{ID: confluencezone.ID, Version: "v3", Enabled: true, Parameters: params}); err == nil {
			t.Fatalf("a missing %s must fail construction", key)
		}
	}
	params := confluenceZoneParams()
	params["minimum_facts"] = 1.0
	if _, err := confluencezone.New(strategy.Config{ID: confluencezone.ID, Version: "v3", Enabled: true, Parameters: params}); err == nil {
		t.Fatal("one fact is not a confluence")
	}
}
