package keylevel

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

func newStrategy(t *testing.T, own map[string]any) strategy.Strategy {
	t.Helper()
	params := map[string]any{
		"minimum_touches": 2.0, "invalidation_buffer_atr": .5, "minimum_target_distance_atr": 1.0, "fallback_target_r": 2.0,
		"expiry_hours": 24.0, "breakout_accept_bars": 2.0, "price_digits": 2.0, "detector_contract": "profit_week",
	}
	for key, value := range own {
		params[key] = value
	}
	s, err := New(strategy.Config{ID: ID, Version: Version, Parameters: legacyfixture.Params(params)})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// bars is ten quiet bars with the last one replaced and the one before it
// closing at prev. A level whose band the last two closes both sit beyond is
// reported broken by its role, so the tests keep the prior close on the level.
func bars(prev float64, last market.Candle) []market.Candle {
	out := legacyfixture.Bars(10, 100)
	out[8] = market.Candle{Time: out[8].Time, Open: prev, High: prev + .2, Low: prev - .2, Close: prev}
	last.Time = out[9].Time
	out[9] = last
	return out
}

// A wick rejection in the buy direction (long lower wick, bullish close in the
// upper third) whose low reaches the bands used below.
var buyRejection = market.Candle{Open: 99.7, High: 100.3, Low: 98.0, Close: 100.2}

// The mirror: a long upper wick and a bearish close in the lower third.
var sellRejection = market.Candle{Open: 100.3, High: 102.0, Low: 99.8, Close: 99.9}

func level(price float64, kind string, touches int) techniquezone.Level {
	return techniquezone.Level{Price: price, Kind: kind, Touches: touches, Band: .3, Strength: float64(touches)}
}

func contextWith(last market.Candle, levels []techniquezone.Level, mutate func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead)) *analysiscontext.MarketContext {
	return contextWithPrev(levels[0].Price, last, levels, mutate)
}

func contextWithPrev(prev float64, last market.Candle, levels []techniquezone.Level, mutate func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead)) *analysiscontext.MarketContext {
	return legacyfixture.Context(bars(prev, last), 1, func(f *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		f.SwingLevels = levels
		if mutate != nil {
			mutate(f, r)
		}
	})
}

func TestAmbiguousLevelBelowPriceIsBoughtOnAConfirmedRejection(t *testing.T) {
	got := newStrategy(t, nil).Evaluate(contextWith(buyRejection, []techniquezone.Level{level(99, "reaction", 3)}, nil))
	if len(got) != 1 {
		t.Fatalf("want one BUY, got %+v", got)
	}
	c := got[0]
	if c.Direction != market.Buy || c.Reaction == nil || c.Reaction.Pattern != "strong_reclaim" {
		t.Fatalf("unexpected candidate %+v", c)
	}
	// The reaction band is the wider of the level band (0.3) and the proximal
	// band (0.5·ATR), not the level band alone.
	if c.Entry.Low != 98.5 || c.Entry.High != 99.5 {
		t.Fatalf("entry band %v-%v, want 98.5-99.5", c.Entry.Low, c.Entry.High)
	}
	if c.DetectorConfluence == nil || c.DetectorConfluence.SelectedStars != 3 || !c.DetectorConfluence.Factors.HTFAligned || !c.DetectorConfluence.Factors.StructuralAgreement {
		t.Fatalf("12 of 20.5 factor points is three stars with htf, touches, structural agreement and session: %+v", c.DetectorConfluence)
	}
	if c.StructuralID == "" || c.Reaction.ZoneID != c.StructuralID {
		t.Fatalf("the level, not the confirmation, is the thesis identity: %q vs %q", c.StructuralID, c.Reaction.ZoneID)
	}
}

func TestAWickRejectionInsideTheBandIsTheReactionAndTheWickFactor(t *testing.T) {
	last := market.Candle{Open: 99.6, High: 100.3, Low: 98.7, Close: 100.2}
	got := newStrategy(t, nil).Evaluate(contextWith(last, []techniquezone.Level{level(99, "reaction", 3)}, nil))
	if len(got) != 1 || got[0].Reaction.Pattern != "wick_rejection" || !got[0].DetectorConfluence.Factors.WickRejection {
		t.Fatalf("a long lower wick that does not pierce the band is a wick rejection: %+v", got)
	}
}

func TestAmbiguousLevelAbovePriceIsSold(t *testing.T) {
	last := market.Candle{Open: 100.3, High: 101.7, Low: 99.9, Close: 100.0}
	got := newStrategy(t, nil).Evaluate(contextWith(last, []techniquezone.Level{level(101, "reaction", 3)}, nil))
	if len(got) != 1 || got[0].Direction != market.Sell {
		t.Fatalf("want one SELL, got %+v", got)
	}
}

func TestExplicitSupportBuysAndExplicitResistanceSells(t *testing.T) {
	support := newStrategy(t, nil).Evaluate(contextWith(buyRejection, []techniquezone.Level{level(99, "support", 3)}, nil))
	if len(support) != 1 || support[0].Direction != market.Buy {
		t.Fatalf("support must be bought: %+v", support)
	}
	resistance := newStrategy(t, nil).Evaluate(contextWith(market.Candle{Open: 100.3, High: 101.7, Low: 99.9, Close: 100.0}, []techniquezone.Level{level(101, "resistance", 3)}, nil))
	if len(resistance) != 1 || resistance[0].Direction != market.Sell {
		t.Fatalf("resistance must be sold: %+v", resistance)
	}
}

func TestABrokenLevelBelongsToBreakAndRetest(t *testing.T) {
	// Two consecutive closes accepted below a support's band: its role is
	// broken, so Key Level must not buy it again.
	brokenSupport := legacyfixture.Context(func() []market.Candle {
		b := legacyfixture.Bars(10, 100)
		b[8] = market.Candle{Time: b[8].Time, Open: 99, High: 99.1, Low: 97.8, Close: 98.0}
		b[9] = market.Candle{Time: b[9].Time, Open: 98, High: 98.6, Low: 97.5, Close: 98.4}
		return b
	}(), 1, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.SwingLevels = []techniquezone.Level{level(99.4, "support", 3)}
	})
	if got := newStrategy(t, nil).Evaluate(brokenSupport); len(got) != 0 {
		t.Fatalf("a broken support must be skipped: %+v", got)
	}
	brokenResistance := legacyfixture.Context(func() []market.Candle {
		b := legacyfixture.Bars(10, 100)
		b[8] = market.Candle{Time: b[8].Time, Open: 101, High: 102.2, Low: 100.9, Close: 102.0}
		b[9] = market.Candle{Time: b[9].Time, Open: 102, High: 102.5, Low: 101.4, Close: 101.6}
		return b
	}(), 1, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.SwingLevels = []techniquezone.Level{level(100.6, "resistance", 3)}
	})
	if got := newStrategy(t, nil).Evaluate(brokenResistance); len(got) != 0 {
		t.Fatalf("a broken resistance must be skipped: %+v", got)
	}
}

func TestAnOpposingZoneWidensTheWindowAndLetsTheConfirmingSideWin(t *testing.T) {
	// Price sits above an ambiguous level, so the naive read is "support, BUY".
	// A live supply zone overlapping the band contradicts it; both sides are
	// tried over the widened window and the bearish rejection confirms SELL.
	supply := techniquezone.Zone{Bottom: 99.0, Top: 101.0, Side: "supply", Source: "supply_demand", BreakIndex: -1}
	got := newStrategy(t, nil).Evaluate(contextWith(sellRejection, []techniquezone.Level{level(99, "reaction", 3)}, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.ContractZones = []techniquezone.Zone{supply}
	}))
	if len(got) != 1 || got[0].Direction != market.Sell {
		t.Fatalf("want one SELL off the opposing zone, got %+v", got)
	}
	if got[0].Entry.Low != 98.5 || got[0].Entry.High != 101.0 {
		t.Fatalf("the entry is the widened window 98.5-101.0, got %v-%v", got[0].Entry.Low, got[0].Entry.High)
	}
	// A mitigated opposing zone no longer contradicts anything.
	supply.Mitigated = true
	if got := newStrategy(t, nil).Evaluate(contextWith(sellRejection, []techniquezone.Level{level(99, "reaction", 3)}, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.ContractZones = []techniquezone.Zone{supply}
	})); len(got) != 0 {
		t.Fatalf("a mitigated zone must not widen the window or flip the side: %+v", got)
	}
}

func TestALevelBothSidesConfirmIsDiscarded(t *testing.T) {
	// Price is exactly on the level, so both directions are valid, and one bar
	// reclaims a sell-side pool and rejects a buy-side pool: a contradiction.
	last := market.Candle{Open: 98.9, High: 99.4, Low: 98.6, Close: 99.0}
	got := newStrategy(t, nil).Evaluate(contextWith(last, []techniquezone.Level{level(99, "reaction", 3)}, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Grabs = []techniquezone.Grab{
			{Index: 9, Direction: "bull", Grade: "B", Pool: techniquezone.Pool{Side: "sell", Level: 99, Band: .1, Touches: 2}},
			{Index: 9, Direction: "bear", Grade: "B", Pool: techniquezone.Pool{Side: "buy", Level: 99, Band: .1, Touches: 2}},
		}
	}))
	if len(got) != 0 {
		t.Fatalf("a level both sides confirm yields nothing: %+v", got)
	}
}

func TestOneBestCandidateIsKeptNotOnePerLevel(t *testing.T) {
	// The nearest level is a two-touch level (two stars); a farther four-touch
	// level confirms off the same bar with three. Only the stronger is kept.
	levels := []techniquezone.Level{level(99.6, "reaction", 2), level(98.6, "reaction", 4)}
	last := market.Candle{Open: 99.7, High: 100.3, Low: 98.0, Close: 100.2}
	got := newStrategy(t, nil).Evaluate(contextWithPrev(99.1, last, levels, nil))
	if len(got) != 1 {
		t.Fatalf("exactly one candidate per evaluation, got %d", len(got))
	}
	if got[0].DetectorConfluence.SelectedStars != 3 || got[0].Entry.Low != 98.1 {
		t.Fatalf("the four-touch level (band 98.1-99.1, three stars) must win over the nearer two-touch one: %+v", got[0])
	}
	// On equal stars the nearest level wins: both with four touches.
	levels = []techniquezone.Level{level(99.6, "reaction", 4), level(98.6, "reaction", 4)}
	got = newStrategy(t, nil).Evaluate(contextWithPrev(99.1, last, levels, nil))
	if len(got) != 1 || got[0].Entry.Low != 99.1 {
		t.Fatalf("on a tie the nearest level wins: %+v", got)
	}
}

func TestACounterBiasReactionStillTradesWithFewerStars(t *testing.T) {
	got := newStrategy(t, nil).Evaluate(contextWith(buyRejection, []techniquezone.Level{level(99, "reaction", 3)}, func(_ *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
		r.HTFBias, r.LocalStructure = "down", "down"
	}))
	if len(got) != 1 || got[0].Direction != market.Buy {
		t.Fatalf("Key Level is never vetoed by the higher-timeframe bias: %+v", got)
	}
	if got[0].DetectorConfluence.Factors.HTFAligned || got[0].DetectorConfluence.SelectedStars != 2 {
		t.Fatalf("counter-bias loses the 4-point htf factor (8 of 20.5, two stars): %+v", got[0].DetectorConfluence)
	}
}

func TestRejections(t *testing.T) {
	one := []techniquezone.Level{level(99, "reaction", 3)}
	cases := map[string]struct {
		last   market.Candle
		levels []techniquezone.Level
		own    map[string]any
		mutate func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead)
	}{
		"below the minimum touches":                          {last: buyRejection, levels: []techniquezone.Level{level(99, "reaction", 1)}},
		"an ambiguous role when an explicit one is required": {last: buyRejection, levels: one, own: map[string]any{"require_explicit_role": true}},
		"no confirmed reaction":                              {last: market.Candle{Open: 99.8, High: 100.6, Low: 99.4, Close: 99.5}, levels: one},
		"entry further than two ATR": {
			last: market.Candle{Open: 99.0, High: 102.6, Low: 98.4, Close: 102.5}, levels: one,
			mutate: func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
				f.Grabs = []techniquezone.Grab{{Index: 9, Direction: "bull", Grade: "B", Pool: techniquezone.Pool{Side: "sell", Level: 99, Band: .1, Touches: 2}}}
			},
		},
		"confluence below the floor": {
			last: buyRejection, levels: []techniquezone.Level{level(99, "reaction", 1)}, own: map[string]any{"minimum_touches": 1.0},
			mutate: func(_ *analysiscontext.LegacyFrame, r *analysiscontext.LegacyRead) {
				r.HTFBias, r.LocalStructure = "down", "down"
			},
		},
	}
	for name, tc := range cases {
		if got := newStrategy(t, tc.own).Evaluate(contextWith(tc.last, tc.levels, tc.mutate)); len(got) != 0 {
			t.Errorf("%s: want no candidate, got %+v", name, got)
		}
	}
}

func TestTheTargetIsTheNearestOpposingLiquidityElseAFixedRewardRisk(t *testing.T) {
	levels := []techniquezone.Level{level(99, "reaction", 3)}
	withPool := newStrategy(t, nil).Evaluate(contextWith(buyRejection, levels, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Pools = []techniquezone.Pool{{Side: "buy", Level: 103, Band: .1, Touches: 2}, {Side: "buy", Level: 106, Band: .1, Touches: 2}}
	}))
	if len(withPool) != 1 || float64(withPool[0].Targets[0].Price.Price) != 103 {
		t.Fatalf("the nearest buy-side pool is the target: %+v", withPool)
	}
	without := newStrategy(t, nil).Evaluate(contextWith(buyRejection, levels, nil))
	if len(without) != 1 {
		t.Fatalf("a missing liquidity target is not a reason to drop the setup: %+v", without)
	}
	// Entry high 99.5, invalidation 98.5-0.5=98.0: risk 1.5, 2R above 99.5.
	if got := float64(without[0].Targets[0].Price.Price); got != 102.5 {
		t.Fatalf("fixed 2R target beyond the entry is 102.5, got %v", got)
	}
}

func TestTheCurrentContractReadsTheWickCountedLevels(t *testing.T) {
	// Under the current contract the level comes from Levels, not SwingLevels.
	ctx := legacyfixture.Context(bars(99, buyRejection), 1, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) {
		f.Levels = []techniquezone.Level{level(99, "reaction", 3)}
	})
	if got := newStrategy(t, nil).Evaluate(ctx); len(got) != 0 {
		t.Fatalf("the profitable-week contract must ignore the wick-counted level: %+v", got)
	}
	if got := newStrategy(t, map[string]any{"detector_contract": "current"}).Evaluate(ctx); len(got) != 1 {
		t.Fatalf("the current contract must read it: %+v", got)
	}
}

func TestRequiredTimeframeIsM5(t *testing.T) {
	if tf := newStrategy(t, nil).RequiredTimeframes(); len(tf) != 1 || tf[0] != market.M5 {
		t.Fatalf("got %v", tf)
	}
}
