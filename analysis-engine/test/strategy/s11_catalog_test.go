package strategy_test

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/boxbreakout"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/confluencezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/crt"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/ifvg"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/impulsepullback"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/liquiditysweep"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/momentumride"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/rangeedge"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/rangesweep"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/scalpbreakoutretest"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/snapback"
	strategytrendline "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/trendline"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	technicaltrendline "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

func bar(t int64, open, high, low, close float64) market.Candle {
	return market.Candle{Time: t, Open: open, High: high, Low: low, Close: close, Volume: 1}
}

func ctx(timeframes map[market.Timeframe]*analysiscontext.TimeframeContext) *analysiscontext.MarketContext {
	return &analysiscontext.MarketContext{Symbol: "XAU", Timeframes: timeframes, Volatility: analysiscontext.VolatilityContext{ATR: 1}}
}

func cfg(id strategy.StrategyID, params map[string]any) strategy.Config {
	return strategy.Config{ID: id, Version: "v2", Enabled: true, Parameters: params}
}

func assertOne(t *testing.T, instance strategy.Strategy, marketCtx *analysiscontext.MarketContext) {
	t.Helper()
	candidates := instance.Evaluate(marketCtx)
	if len(candidates) != 1 {
		t.Fatalf("%s: expected one qualifying candidate, got %d", instance.ID(), len(candidates))
	}
	if err := candidates[0].Validate(); err != nil {
		t.Fatalf("%s: invalid candidate: %v", instance.ID(), err)
	}
	if again := instance.Evaluate(marketCtx); len(again) != 1 || again[0].ID != candidates[0].ID {
		t.Fatalf("%s: replay output is not deterministic", instance.ID())
	}
	if rejected := instance.Evaluate(ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{})); len(rejected) != 0 {
		t.Fatalf("%s: missing required technical context must reject", instance.ID())
	}
	book := opportunity.NewBook()
	created, err := book.Observe(candidates[0], candidates[0].CreatedAt)
	if err != nil || created.Kind != opportunity.TransitionCreated {
		t.Fatalf("%s: lifecycle creation failed: transition=%s err=%v", instance.ID(), created.Kind, err)
	}
	active, err := book.Observe(candidates[0], candidates[0].CreatedAt+1)
	if err != nil || active.Kind != opportunity.TransitionActivated {
		t.Fatalf("%s: lifecycle activation failed: transition=%s err=%v", instance.ID(), active.Kind, err)
	}
	expired := book.Expire(candidates[0].ExpiresAt)
	if len(expired) != 1 || expired[0].Kind != opportunity.TransitionExpired {
		t.Fatalf("%s: lifecycle expiry failed: %+v", instance.ID(), expired)
	}
}

func TestS11MissingStrategiesKnownQualifyingFixtures(t *testing.T) {
	const base = int64(1_700_000_000)
	t.Run("ifvg", func(t *testing.T) {
		s, err := ifvg.New(cfg(ifvg.ID, map[string]any{"minimum_strength": .4, "invalidation_buffer_atr": .5, "minimum_target_distance_atr": 1.0, "expiry_hours": 4.0}))
		if err != nil {
			t.Fatal(err)
		}
		assertOne(t, s, ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: []market.Candle{bar(base, 100, 101, 99, 100)}, Zones: zone.ZoneState{Zones: []zone.Zone{{ID: "ifvg-1", Kind: zone.KindIFVG, Side: zone.Demand, Low: 99, High: 100, OriginTime: base - 300, Strength: .8, State: zone.StateFresh, Relevance: zone.Immediate}}}, Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 103, High: 104}}}}}))
	})
	t.Run("trendline", func(t *testing.T) {
		s, err := strategytrendline.New(cfg(strategytrendline.ID, map[string]any{"minimum_validation_touches": 2.0, "invalidation_buffer_atr": .25, "target_r": 2.0, "expiry_hours": 4.0}))
		if err != nil {
			t.Fatal(err)
		}
		line := technicaltrendline.Trendline{Kind: technicaltrendline.KindSupport, AnchorA: "a", AnchorB: "b", Intercept: 100, SpanBars: 2, ValidationTouches: []technicaltrendline.ValidationTouch{{Time: 1}, {Time: 2}}}
		assertOne(t, s, ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: []market.Candle{bar(base-300, 102, 102.5, 101.5, 102), bar(base, 100, 100.5, 99.8, 100.2)}, Trendline: technicaltrendline.TrendlineState{Lines: []technicaltrendline.Trendline{line}}}}))
	})
	t.Run("range_edge", func(t *testing.T) {
		s, e := rangeedge.New(cfg(rangeedge.ID, map[string]any{"lookback_bars": 5.0, "minimum_rejections": 2.0, "edge_tolerance_atr": .2, "invalidation_buffer_atr": .25, "expiry_hours": 4.0}))
		if e != nil {
			t.Fatal(e)
		}
		bars := []market.Candle{bar(1, 101, 102, 100, 100.5), bar(2, 101, 102, 99.9, 100.5), bar(3, 101, 102, 100.5, 101), bar(4, 101, 102, 99.8, 100.6), bar(5, 100.5, 101, 100.2, 100.7), bar(6, 100.5, 101, 99.9, 100.7)}
		assertOne(t, s, ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: bars, Structure: structure.StructureState{Internal: structure.LayerState{Trend: structure.TrendRange}}}}))
	})
	t.Run("liquidity_sweep", func(t *testing.T) {
		s, e := liquiditysweep.New(cfg(liquiditysweep.ID, map[string]any{"minimum_rejection_body_atr": .3, "invalidation_buffer_atr": .25, "target_r": 2.0, "expiry_hours": 4.0}))
		if e != nil {
			t.Fatal(e)
		}
		at := int64(2)
		assertOne(t, s, ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: []market.Candle{bar(2, 99, 100.5, 98.5, 100)}, Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{ID: "p", Side: liquidity.LiquiditySellSide, Low: 99, High: 99.2, SweptAt: &at, ReclaimedAt: &at}}}}}))
	})
	t.Run("snap_back", func(t *testing.T) {
		s, e := snapback.New(cfg(snapback.ID, map[string]any{"extension_atr": 2.0, "invalidation_buffer_atr": .25, "target_fraction": .5, "expiry_hours": 4.0}))
		if e != nil {
			t.Fatal(e)
		}
		assertOne(t, s, ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: []market.Candle{bar(2, 104, 104.5, 102.5, 103)}, KeyLevel: keylevel.State{Levels: []keylevel.Level{{ID: "k", Price: 100, Band: .2, Touches: 3, Strength: 1}}}}}))
	})
	t.Run("crt", func(t *testing.T) {
		s, e := crt.New(cfg(crt.ID, map[string]any{"minimum_h1_range_atr": 1.5, "invalidation_buffer_atr": .25, "expiry_hours": 4.0}))
		if e != nil {
			t.Fatal(e)
		}
		assertOne(t, s, ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.H1: {Timeframe: market.H1, Candles: []market.Candle{bar(base-3600, 100, 103, 99, 102), bar(base, 102, 103, 101, 102)}}, market.M5: {Timeframe: market.M5, Candles: []market.Candle{bar(base-300, 100, 101, 99.5, 100), bar(base, 100, 101, 98.5, 100)}}}))
	})
	t.Run("confluence_zone", func(t *testing.T) {
		s, e := confluencezone.New(cfg(confluencezone.ID, map[string]any{"minimum_facts": 2.0, "invalidation_buffer_atr": .5, "minimum_target_distance_atr": 1.0, "expiry_hours": 4.0}))
		if e != nil {
			t.Fatal(e)
		}
		zones := []zone.Zone{{ID: "a", Kind: zone.KindDemand, Side: zone.Demand, Low: 99, High: 100, OriginTime: 1, State: zone.StateFresh}, {ID: "b", Kind: zone.KindFVG, Side: zone.Demand, Low: 99.5, High: 100.5, OriginTime: 2, State: zone.StateFresh}}
		assertOne(t, s, ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: []market.Candle{bar(3, 100, 101, 99.6, 100)}, Zones: zone.ZoneState{Zones: zones}, Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 103, High: 104}}}}}))
	})
	t.Run("momentum_ride", func(t *testing.T) {
		s, e := momentumride.New(cfg(momentumride.ID, map[string]any{"minimum_body_atr": .5, "maximum_overlap_fraction": .35, "invalidation_buffer_atr": .25, "minimum_target_distance_atr": 1.0, "expiry_hours": 4.0}))
		if e != nil {
			t.Fatal(e)
		}
		bars := []market.Candle{bar(1, 100, 101, 99.9, 100.8), bar(2, 101, 102, 100.9, 101.8), bar(3, 102, 103, 101.9, 102.8)}
		assertOne(t, s, ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: bars, Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 105, High: 106}}}}}))
	})
	t.Run("box_breakout", func(t *testing.T) {
		s, e := boxbreakout.New(cfg(boxbreakout.ID, map[string]any{"box_bars": 5.0, "maximum_width_atr": 2.0, "retest_tolerance_atr": .2, "invalidation_buffer_atr": .25, "target_r": 2.0, "expiry_hours": 4.0}))
		if e != nil {
			t.Fatal(e)
		}
		bars := []market.Candle{bar(1, 100, 101, 100, 100.5), bar(2, 100.5, 101, 100, 100.4), bar(3, 100.4, 101, 100, 100.5), bar(4, 100.5, 101, 100, 100.4), bar(5, 100.4, 101, 100, 100.5), bar(6, 100.8, 102, 100.8, 101.5), bar(7, 101.4, 101.6, 100.9, 101.2)}
		assertOne(t, s, ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: bars}}))
	})
	t.Run("range_sweep", func(t *testing.T) {
		s, e := rangesweep.New(cfg(rangesweep.ID, map[string]any{"range_bars": 5.0, "minimum_sweep_atr": .1, "invalidation_buffer_atr": .2, "expiry_hours": 2.0}))
		if e != nil {
			t.Fatal(e)
		}
		m5 := []market.Candle{bar(1, 100, 102, 100, 101), bar(2, 101, 102, 100, 101), bar(3, 101, 102, 100, 101), bar(4, 101, 102, 100, 101), bar(5, 101, 102, 100, 101)}
		assertOne(t, s, ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: m5, Structure: structure.StructureState{Internal: structure.LayerState{Trend: structure.TrendRange}}}, market.M1: {Timeframe: market.M1, Candles: []market.Candle{bar(6, 99.8, 100.5, 99.5, 100.3)}}}))
	})
	t.Run("impulse_pullback", func(t *testing.T) {
		s, e := impulsepullback.New(cfg(impulsepullback.ID, map[string]any{"minimum_impulse_atr": 1.0, "minimum_pullback_fraction": .25, "maximum_pullback_fraction": .65, "invalidation_buffer_atr": .2, "target_r": 2.0, "expiry_hours": 2.0}))
		if e != nil {
			t.Fatal(e)
		}
		assertOne(t, s, ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: []market.Candle{bar(1, 99, 100, 98.8, 99.5), bar(2, 100, 102, 100, 102), bar(3, 102, 102.2, 101.8, 102)}}, market.M1: {Timeframe: market.M1, Candles: []market.Candle{bar(4, 101.8, 102, 101.2, 101.4), bar(5, 101.4, 102.1, 101.3, 102.05)}}}))
	})
	t.Run("scalp_breakout_retest", func(t *testing.T) {
		s, e := scalpbreakoutretest.New(cfg(scalpbreakoutretest.ID, map[string]any{"m5_box_bars": 5.0, "m1_accept_bars": 1.0, "maximum_width_atr": 2.0, "retest_tolerance_atr": .15, "invalidation_buffer_atr": .2, "target_r": 2.0, "expiry_hours": 2.0}))
		if e != nil {
			t.Fatal(e)
		}
		m5 := []market.Candle{bar(1, 100, 101, 100, 100.5), bar(2, 100.5, 101, 100, 100.4), bar(3, 100.4, 101, 100, 100.5), bar(4, 100.5, 101, 100, 100.4), bar(5, 100.4, 101, 100, 100.5)}
		m1 := []market.Candle{bar(6, 101, 102, 101, 101.5), bar(7, 101.4, 101.6, 100.95, 101.2)}
		assertOne(t, s, ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: m5}, market.M1: {Timeframe: market.M1, Candles: m1}}))
	})
}

func TestS11BreakoutRetestsRejectDeepFailedReentry(t *testing.T) {
	box, err := boxbreakout.New(cfg(boxbreakout.ID, map[string]any{"box_bars": 5.0, "maximum_width_atr": 2.0, "retest_tolerance_atr": .2, "invalidation_buffer_atr": .25, "target_r": 2.0, "expiry_hours": 4.0}))
	if err != nil {
		t.Fatal(err)
	}
	boxBars := []market.Candle{bar(1, 100, 101, 100, 100.5), bar(2, 100.5, 101, 100, 100.4), bar(3, 100.4, 101, 100, 100.5), bar(4, 100.5, 101, 100, 100.4), bar(5, 100.4, 101, 100, 100.5), bar(6, 100.8, 102, 100.8, 101.5), bar(7, 100.5, 101.6, 99.5, 101.2)}
	if got := box.Evaluate(ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: boxBars}})); len(got) != 0 {
		t.Fatalf("box breakout accepted a deep failed retest: %+v", got)
	}

	scalp, err := scalpbreakoutretest.New(cfg(scalpbreakoutretest.ID, map[string]any{"m5_box_bars": 5.0, "m1_accept_bars": 1.0, "maximum_width_atr": 2.0, "retest_tolerance_atr": .15, "invalidation_buffer_atr": .2, "target_r": 2.0, "expiry_hours": 2.0}))
	if err != nil {
		t.Fatal(err)
	}
	m5 := boxBars[:5]
	m1 := []market.Candle{bar(6, 101, 102, 101, 101.5), bar(7, 100.5, 101.6, 99.5, 101.2)}
	if got := scalp.Evaluate(ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: m5}, market.M1: {Timeframe: market.M1, Candles: m1}})); len(got) != 0 {
		t.Fatalf("scalp breakout accepted a deep failed retest: %+v", got)
	}
}

func TestS11RangeEdgeTriggerCannotDefineItsOwnRange(t *testing.T) {
	s, err := rangeedge.New(cfg(rangeedge.ID, map[string]any{"lookback_bars": 5.0, "minimum_rejections": 2.0, "edge_tolerance_atr": .2, "invalidation_buffer_atr": .25, "expiry_hours": 4.0}))
	if err != nil {
		t.Fatal(err)
	}
	bars := []market.Candle{bar(1, 101, 102, 100, 101), bar(2, 101, 102, 100, 101), bar(3, 101, 102, 100, 101), bar(4, 101, 102, 100, 101), bar(5, 101, 102, 100, 101), bar(6, 100, 100.5, 95, 95.5)}
	marketCtx := ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: bars, Structure: structure.StructureState{Internal: structure.LayerState{Trend: structure.TrendRange}}}})
	if got := s.Evaluate(marketCtx); len(got) != 0 {
		t.Fatalf("trigger candle was allowed to redefine the established range: %+v", got)
	}
}

func TestS11MissingStrategiesRejectMissingConfiguration(t *testing.T) {
	factories := []struct {
		name    string
		id      strategy.StrategyID
		factory strategy.Factory
	}{
		{"ifvg", ifvg.ID, ifvg.New}, {"trendline", strategytrendline.ID, strategytrendline.New}, {"range_edge", rangeedge.ID, rangeedge.New}, {"liquidity_sweep", liquiditysweep.ID, liquiditysweep.New}, {"snap_back", snapback.ID, snapback.New}, {"crt", crt.ID, crt.New}, {"confluence_zone", confluencezone.ID, confluencezone.New}, {"momentum_ride", momentumride.ID, momentumride.New}, {"box_breakout", boxbreakout.ID, boxbreakout.New}, {"range_sweep", rangesweep.ID, rangesweep.New}, {"impulse_pullback", impulsepullback.ID, impulsepullback.New}, {"scalp_breakout_retest", scalpbreakoutretest.ID, scalpbreakoutretest.New},
	}
	for _, tc := range factories {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := tc.factory(cfg(tc.id, map[string]any{})); err == nil {
				t.Fatal("missing strategy-owned config must fail closed")
			}
		})
	}
}
