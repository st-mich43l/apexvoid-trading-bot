package breakretest

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	technicaltrendline "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

func candle(t int64, open, high, low, close float64) market.Candle {
	return market.Candle{Time: t, Open: open, High: high, Low: low, Close: close, Volume: 1}
}

func newStrategy(t *testing.T) *Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: map[string]any{
		"breakout_accept_bars": 2.0, "trendline_tolerance_atr": .3, "momentum_body_fraction": .6,
		"strict_premium_discount": false, "maximum_entry_atr": 2.0, "invalidation_buffer_atr": .25,
		"target_r": 2.0, "expiry_hours": 4.0,
	}})
	if err != nil {
		t.Fatal(err)
	}
	return s.(*Strategy)
}

func context(direction market.Direction, candles []market.Candle, lines []technicaltrendline.Trendline, levels []keylevel.Level) *analysiscontext.MarketContext {
	return &analysiscontext.MarketContext{
		Symbol: "XAU", Bias: analysiscontext.BiasContext{Direction: direction},
		Volatility: analysiscontext.VolatilityContext{ATR: 1},
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {
			Timeframe: market.M5, Candles: candles,
			Trendline: technicaltrendline.TrendlineState{Lines: lines},
			KeyLevel:  keylevel.State{Levels: levels},
			Regime:    analysiscontext.RegimeContext{Kind: "trend"},
		}},
	}
}

func TestTrendlineBreakRetestBuyHasPriority(t *testing.T) {
	s := newStrategy(t)
	brokenAt := int64(2)
	lines := []technicaltrendline.Trendline{{Kind: technicaltrendline.KindResistance, AnchorA: "a", AnchorB: "b", AnchorAIndex: 0, BrokenAt: &brokenAt, Slope: 0, Intercept: 100}}
	bars := []market.Candle{candle(1, 99, 100, 98.8, 99.2), candle(2, 100.2, 101.2, 100, 100.8), candle(3, 100.8, 101.4, 100.5, 101.0), candle(4, 100.9, 101.3, 100.7, 101.1), candle(5, 100.3, 100.9, 99.5, 100.8)}
	got := s.Evaluate(context(market.Buy, bars, lines, []keylevel.Level{{ID: "level", Price: 100, Band: .1}}))
	if len(got) != 1 || got[0].StructuralID != "trendline:a:b" {
		t.Fatalf("trendline setup must win before key-level setup: %+v", got)
	}
}

func TestTrendlineBreakRetestSell(t *testing.T) {
	s := newStrategy(t)
	brokenAt := int64(2)
	lines := []technicaltrendline.Trendline{{Kind: technicaltrendline.KindSupport, AnchorA: "a", AnchorB: "b", AnchorAIndex: 0, BrokenAt: &brokenAt, Slope: 0, Intercept: 100}}
	bars := []market.Candle{candle(1, 101, 102, 100, 101.5), candle(2, 99.8, 100, 98.8, 99.2), candle(3, 99.2, 99.5, 98.5, 98.9), candle(4, 99, 99.4, 98.6, 98.8), candle(5, 99.7, 100.5, 99.1, 99.2)}
	if got := s.Evaluate(context(market.Sell, bars, lines, nil)); len(got) != 1 || got[0].Direction != market.Sell {
		t.Fatalf("sell trendline break/retest must qualify: %+v", got)
	}
}

func TestKeyLevelBreakRetestBuyAndRejectsUnacceptedBreak(t *testing.T) {
	s := newStrategy(t)
	bars := []market.Candle{candle(1, 99, 99.6, 98.8, 99.2), candle(2, 100.2, 101, 100, 100.6), candle(3, 100.6, 101.2, 100.3, 101), candle(4, 99.5, 100.3, 99, 100), candle(5, 99.5, 100.3, 99, 100)}
	level := []keylevel.Level{{ID: "k1", Price: 100, Band: .1, Touches: 3}}
	if got := s.Evaluate(context(market.Buy, bars, nil, level)); len(got) != 1 || got[0].StructuralID != "key_level:k1" {
		t.Fatalf("accepted key-level retest must qualify: %+v", got)
	}
	oneBreak := bars[:]
	oneBreak[2] = candle(3, 99.8, 100.1, 99.4, 99.9)
	oneBreak[3] = candle(4, 99.5, 100.3, 99, 99.9)
	oneBreak[4] = candle(5, 99.5, 100.3, 99, 100)
	if got := s.Evaluate(context(market.Buy, oneBreak, nil, level)); len(got) != 0 {
		t.Fatalf("a single accepted close must not create Break & Retest: %+v", got)
	}
}

func TestKeyLevelBreakRetestSellUsesResistanceRetest(t *testing.T) {
	s := newStrategy(t)
	bars := []market.Candle{
		candle(1, 101, 102, 100.5, 101.4),
		candle(2, 100.8, 101.1, 99.2, 99.6),
		candle(3, 99.8, 100.2, 98.9, 99.1),
		candle(4, 99.8, 100.5, 98.9, 99.2),
		candle(5, 99.8, 100.5, 98.9, 99.2),
	}
	level := []keylevel.Level{{ID: "k1", Price: 100, Band: .1, Touches: 3}}
	got := s.Evaluate(context(market.Sell, bars, nil, level))
	if len(got) != 1 || got[0].Direction != market.Sell || got[0].StructuralID != "key_level:k1" {
		t.Fatalf("accepted sell resistance retest must qualify: %+v", got)
	}
}

func TestBreakRetestRejectsWrongTrendlineKindAndFailedHold(t *testing.T) {
	s := newStrategy(t)
	brokenAt := int64(2)
	bars := []market.Candle{
		candle(1, 99, 100, 98.8, 99.2), candle(2, 100.2, 101.2, 100, 100.8),
		candle(3, 100.8, 101.4, 100.5, 101), candle(4, 100.9, 101.3, 100.7, 101.1),
		candle(5, 100.3, 100.9, 99.5, 100.8),
	}
	wrongKind := technicaltrendline.Trendline{Kind: technicaltrendline.KindSupport, AnchorA: "a", AnchorB: "b", AnchorAIndex: 0, BrokenAt: &brokenAt, Intercept: 100}
	if got := s.Evaluate(context(market.Buy, bars, []technicaltrendline.Trendline{wrongKind}, nil)); len(got) != 0 {
		t.Fatalf("buy must reject a broken support trendline: %+v", got)
	}
	failedHold := bars[:]
	failedHold[4] = candle(5, 100.8, 101.4, 100.5, 100.7)
	line := wrongKind
	line.Kind = technicaltrendline.KindResistance
	if got := s.Evaluate(context(market.Buy, failedHold, []technicaltrendline.Trendline{line}, nil)); len(got) != 0 {
		t.Fatalf("retest that closes back below resistance must reject: %+v", got)
	}
}

func TestBreakRetestRejectsChopWrongSideAndNoRejection(t *testing.T) {
	s := newStrategy(t)
	brokenAt := int64(2)
	line := technicaltrendline.Trendline{Kind: technicaltrendline.KindResistance, AnchorA: "a", AnchorB: "b", AnchorAIndex: 0, BrokenAt: &brokenAt, Intercept: 100}
	bars := []market.Candle{candle(1, 99, 100, 98, 99), candle(2, 100, 101, 99.8, 100.8), candle(3, 100.8, 101.2, 100.5, 101), candle(4, 100.9, 101.2, 100.7, 101), candle(5, 100.2, 101, 100, 100.7)}
	badChop := context(market.Buy, bars, []technicaltrendline.Trendline{line}, nil)
	badChop.Timeframes[market.M5].Regime.Kind = "chop"
	if got := s.Evaluate(badChop); len(got) != 0 {
		t.Fatalf("chop must reject: %+v", got)
	}
	noReject := context(market.Buy, bars, []technicaltrendline.Trendline{line}, nil)
	noReject.Timeframes[market.M5].Candles[4] = candle(5, 100.8, 101.2, 100.6, 101.1)
	if got := s.Evaluate(noReject); len(got) != 0 {
		t.Fatalf("no rejection must reject: %+v", got)
	}
}
