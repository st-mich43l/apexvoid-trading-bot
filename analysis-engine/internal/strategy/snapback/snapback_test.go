package snapback

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

func params() map[string]any {
	return legacyfixture.Params(map[string]any{
		"extension_atr": 1.5, "invalidation_buffer_atr": .25, "target_r": 2.0, "expiry_hours": 4.0,
		"extension_source": "impulse", "strict_premium_discount": true,
	})
}

func newStrategy(t *testing.T, p map[string]any) strategy.Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: "v2", Parameters: p})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// bars end with a bar that sweeps below the demand zone's liquidity and
// reclaims it, closing back inside.
func bars() []market.Candle {
	out := legacyfixture.Bars(10, 100)
	out[9] = market.Candle{Time: 3000, Open: 99.4, High: 100.8, Low: 98.5, Close: 100.5}
	return out
}

func demandZone(score float64) techniquezone.Zone {
	return techniquezone.Zone{Bottom: 99, Top: 100, Side: "demand", Source: "supply_demand", Sources: []string{"supply_demand"}, OriginIndex: 2, BreakIndex: -1, Score: score}
}

func bullGrab(grade string) techniquezone.Grab {
	return techniquezone.Grab{Index: 9, Direction: "bull", Grade: grade, Pool: techniquezone.Pool{Side: "sell", Level: 99.15, Band: .1, Touches: 3}}
}

func qualifying(mutate func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead)) *analysiscontext.MarketContext {
	return legacyfixture.Context(bars(), 1, func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		f.Swings = []techniquezone.Swing{{Index: 4, Kind: "low", Price: 96}}
		f.Zones = []techniquezone.Zone{demandZone(12)}
		f.Grabs = []techniquezone.Grab{bullGrab("B")}
		if mutate != nil {
			mutate(f, r)
		}
	})
}

func TestSnapBackBuysTheScoredZoneAfterAnExtensionSweepAndReaction(t *testing.T) {
	got := newStrategy(t, params()).Evaluate(qualifying(nil))
	if len(got) != 1 {
		t.Fatalf("a qualified snap back is missing: %+v", got)
	}
	c := got[0]
	if c.Direction != market.Buy || c.Entry.Low != 99 || c.Entry.High != 100 {
		t.Fatalf("entry = %v [%v, %v]", c.Direction, c.Entry.Low, c.Entry.High)
	}
	if c.Reaction == nil || c.Reaction.Pattern != "sweep_reclaim" || c.Reaction.ConfirmationBarTime != 3000 {
		t.Fatalf("the grab bar is the confirmation: %+v", c.Reaction)
	}
	if c.DetectorConfluence == nil || c.DetectorConfluence.SelectedStars != 2 {
		t.Fatalf("a 12/24.5 zone is 2 stars: %+v", c.DetectorConfluence)
	}
	if c.StructuralID != "snap:zone:demand:supply_demand:900" {
		t.Fatalf("stable zone identity expected, got %q", c.StructuralID)
	}
}

func TestSnapBackRejections(t *testing.T) {
	cases := map[string]func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead){
		"no grab": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) { f.Grabs = nil },
		"grade C grab": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Grabs = []techniquezone.Grab{bullGrab("C")}
		},
		"extension too small": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Swings = []techniquezone.Swing{{Index: 4, Kind: "low", Price: 100.2}}
		},
		"no direction": func(_ *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
			r.LocalStructure, r.HTFBias = "range", "range"
		},
		"premium location": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Range = &fib.DealingRange{Zone: "premium"}
		},
		"equilibrium location": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Range = &fib.DealingRange{Zone: "eq"}
		},
		"below the confluence floor": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			f.Zones = []techniquezone.Zone{demandZone(5)}
		},
		"zone is on the wrong side of price": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			z := demandZone(12)
			z.Bottom, z.Top = 101.5, 102.5
			f.Zones = []techniquezone.Zone{z}
		},
		"wrong-direction grab": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			g := bullGrab("B")
			g.Direction = "bear"
			f.Grabs = []techniquezone.Grab{g}
		},
		"no reaction on the bars": func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
			quiet := legacyfixture.Bars(10, 104)
			f.Bars = quiet
		},
	}
	s := newStrategy(t, params())
	for name, mutate := range cases {
		if got := s.Evaluate(qualifying(mutate)); len(got) != 0 {
			t.Fatalf("%s must reject: %+v", name, got)
		}
	}
}

func TestSnapBackFallsBackToTheNearestValidKeyLevel(t *testing.T) {
	ctx := qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Zones = nil
		f.Levels = []techniquezone.Level{{Price: 99.1, Kind: "reaction", Touches: 4, Band: .3, Strength: 4}}
	})
	got := newStrategy(t, params()).Evaluate(ctx)
	if len(got) != 1 {
		t.Fatalf("a key-level snap back is missing: %+v", got)
	}
	found := false
	for _, e := range got[0].Evidence {
		found = found || e.Code == "structural_source_key_level"
	}
	if !found || got[0].StructuralID != "snap:level:reaction:99.10000000" {
		t.Fatalf("expected the key-level anchor, got %q evidence %+v", got[0].StructuralID, got[0].Evidence)
	}
	// A level above price is not a valid BUY anchor.
	none := qualifying(func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Zones = nil
		f.Levels = []techniquezone.Level{{Price: 103, Kind: "reaction", Touches: 4, Band: .3}}
	})
	if got := newStrategy(t, params()).Evaluate(none); len(got) != 0 {
		t.Fatalf("no valid-side level: %+v", got)
	}
}

func TestSnapBackSellMirrorsBuy(t *testing.T) {
	out := legacyfixture.Bars(10, 100)
	out[9] = market.Candle{Time: 3000, Open: 100.6, High: 101.5, Low: 99.2, Close: 99.5}
	ctx := legacyfixture.Context(out, 1, func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		r.LocalStructure, r.HTFBias = "down", "down"
		f.Swings = []techniquezone.Swing{{Index: 4, Kind: "high", Price: 104}}
		f.Zones = []techniquezone.Zone{{Bottom: 100, Top: 101, Side: "supply", Source: "supply_demand", Sources: []string{"supply_demand"}, OriginIndex: 2, BreakIndex: -1, Score: 12}}
		f.Grabs = []techniquezone.Grab{{Index: 9, Direction: "bear", Grade: "A", Pool: techniquezone.Pool{Side: "buy", Level: 100.85, Band: .1, Touches: 3}}}
	})
	got := newStrategy(t, params()).Evaluate(ctx)
	if len(got) != 1 || got[0].Direction != market.Sell || got[0].Entry.Low != 100 || got[0].Entry.High != 101 {
		t.Fatalf("sell snap back wrong: %+v", got)
	}
}

func TestSnapBackRequiresDetectorSettings(t *testing.T) {
	p := params()
	delete(p, "fvg_entry_max_width_price")
	if _, err := New(strategy.Config{ID: ID, Version: "v2", Parameters: p}); err == nil {
		t.Fatal("a missing injected detector parameter must fail closed")
	}
	if got := newStrategy(t, params()).Evaluate(&analysiscontext.MarketContext{}); len(got) != 0 {
		t.Fatal("a context without the detector-contract frame cannot produce a candidate")
	}
}
