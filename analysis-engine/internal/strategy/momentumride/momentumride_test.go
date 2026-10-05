package momentumride

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/momentum"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

func newStrategy(t *testing.T, va bool) strategy.Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: "v2", Parameters: legacyfixture.Params(map[string]any{
		"minimum_body_fraction": .6, "maximum_overlap_fraction": .35, "invalidation_buffer_atr": .25,
		"minimum_target_distance_atr": 1.0, "expiry_hours": 4.0, "require_momentum_va": va, "momentum_opposition_tolerance": .15,
	})})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// bars end with a strong bullish body closing above the last swing high.
func bars() []market.Candle {
	out := legacyfixture.Bars(10, 99.6)
	out[8] = market.Candle{Time: 2700, Open: 99, High: 100, Low: 98.8, Close: 99.8}
	out[9] = market.Candle{Time: 3000, Open: 100, High: 102, Low: 99.9, Close: 101.8}
	return out
}

func demand(score float64) techniquezone.Zone {
	return techniquezone.Zone{Bottom: 100, Top: 100.5, Side: "demand", Source: "supply_demand", Sources: []string{"supply_demand"}, OriginIndex: 3, BreakIndex: -1, Score: score}
}

func qualifying(mutate func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead)) *analysiscontext.MarketContext {
	return legacyfixture.Context(bars(), 1, func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		f.Swings = []techniquezone.Swing{{Index: 4, Kind: "high", Price: 100}}
		f.Zones = []techniquezone.Zone{demand(10)}
		if mutate != nil {
			mutate(f, r)
		}
	})
}

func TestMomentumRidesAStrongBodyBreakNearAScoredZone(t *testing.T) {
	got := newStrategy(t, false).Evaluate(qualifying(nil))
	if len(got) != 1 {
		t.Fatalf("a structural momentum candidate is missing: %+v", got)
	}
	c := got[0]
	if c.Direction != market.Buy || c.Entry.Low != 100 || c.Entry.High != 100.5 || c.Reaction != nil {
		t.Fatalf("momentum has no reaction confirmation: %+v", c)
	}
	if c.DetectorConfluence == nil || c.DetectorConfluence.SelectedStars != 2 || !c.DetectorConfluence.Factors.DisplacementGrade {
		t.Fatalf("a 10/24.5 zone is two stars with a displacement factor: %+v", c.DetectorConfluence)
	}
	// With no opposing pool the target falls back to the configured distance.
	if float64(c.Targets[0].Price.Price) <= c.Entry.High {
		t.Fatalf("target must lie beyond the entry: %+v", c.Targets)
	}
}

func TestMomentumRejections(t *testing.T) {
	cases := map[string]func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead){
		"chop": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Regime = regime.State{Kind: "chop"}
		},
		"no direction": func(_ *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
			r.LocalStructure, r.HTFBias = "range", "range"
		},
		"premium": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Range = &fib.DealingRange{Zone: "premium"}
		},
		"no broken swing": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Swings = []techniquezone.Swing{{Index: 4, Kind: "high", Price: 101.9}}
		},
		"weak body": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Bars = append([]market.Candle(nil), f.Bars...)
			f.Bars[9] = market.Candle{Time: 3000, Open: 100.9, High: 102, Low: 99.9, Close: 101.8}
		},
		"bearish bar": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Bars = append([]market.Candle(nil), f.Bars...)
			f.Bars[9] = market.Candle{Time: 3000, Open: 101.8, High: 102, Low: 99.9, Close: 100}
		},
		"below the confluence floor": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Zones = []techniquezone.Zone{demand(5)}
		},
	}
	s := newStrategy(t, false)
	for name, mutate := range cases {
		if got := s.Evaluate(qualifying(mutate)); len(got) != 0 {
			t.Fatalf("%s must reject: %+v", name, got)
		}
	}
}

func TestMomentumFallsBackToAKeyLevelWhenNoZoneIsValid(t *testing.T) {
	ctx := qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Zones = nil
		f.Levels = []techniquezone.Level{{Price: 101.2, Kind: "reaction", Touches: 4, Band: .3, Strength: 4}}
	})
	got := newStrategy(t, false).Evaluate(ctx)
	if len(got) != 1 {
		t.Fatalf("a key-level momentum candidate is missing: %+v", got)
	}
	found := false
	for _, e := range got[0].Evidence {
		found = found || e.Code == "structural_source_key_level"
	}
	if !found {
		t.Fatalf("expected the key-level anchor: %+v", got[0].Evidence)
	}
	none := qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) { f.Zones = nil })
	if got := newStrategy(t, false).Evaluate(none); len(got) != 0 {
		t.Fatalf("with neither zone nor level there is no anchor: %+v", got)
	}
}

func TestMomentumVelocityAccelerationGateWhenEnabled(t *testing.T) {
	bull := func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Momentum, f.MomentumAcceleration = momentum.Bull, .01
	}
	if got := newStrategy(t, true).Evaluate(qualifying(bull)); len(got) != 1 {
		t.Fatalf("an aligned bull momentum read must pass the optional gate: %+v", got)
	}
	opposed := func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Momentum, f.MomentumAcceleration = momentum.Bull, -.5
	}
	if got := newStrategy(t, true).Evaluate(qualifying(opposed)); len(got) != 0 {
		t.Fatal("a strongly opposing acceleration must fail the gate")
	}
	if got := newStrategy(t, true).Evaluate(qualifying(nil)); len(got) != 0 {
		t.Fatal("a neutral momentum read must fail the enabled gate")
	}
	if got := newStrategy(t, false).Evaluate(qualifying(nil)); len(got) != 1 {
		t.Fatal("the gate is optional and off in the frozen production config")
	}
}
