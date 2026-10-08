package sessionlevel_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/sessionlevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

// The decision itself is the frozen session_level_reaction, proven bar by bar on
// real captures in test/legacyparity (316 oracle decisions). These tests pin the
// contract's edges.

func validParams() map[string]any {
	return legacyfixture.Params(map[string]any{
		"invalidation_buffer_atr": 0.5, "minimum_target_distance_atr": 1.0, "expiry_hours": 24.0, "proximal_band_atr": 0.5,
	})
}

func newStrategy(t *testing.T, params map[string]any) strategy.Strategy {
	t.Helper()
	s, err := sessionlevel.New(strategy.Config{ID: sessionlevel.ID, Version: sessionlevel.Version, Enabled: true, Parameters: params})
	if err != nil {
		t.Fatalf("sessionlevel.New: %v", err)
	}
	return s
}

func TestSessionLevel_RejectsMissingOrInvalidConfiguration(t *testing.T) {
	if _, err := sessionlevel.New(strategy.Config{ID: sessionlevel.ID, Parameters: map[string]any{}}); err == nil {
		t.Fatal("missing configuration must fail closed")
	}
	bad := validParams()
	bad["proximal_band_atr"] = 0.0
	if _, err := sessionlevel.New(strategy.Config{ID: sessionlevel.ID, Parameters: bad}); err == nil {
		t.Fatal("a zero reaction band must be rejected")
	}
}

func TestSessionLevel_WithoutADetectorFramePublishesNothing(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := &context.MarketContext{Symbol: "XAU", Timeframes: map[market.Timeframe]*context.TimeframeContext{}, Volatility: context.VolatilityContext{ATR: 1}}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatalf("published without a frame: %+v", got)
	}
}

func TestSessionLevel_ARestingLevelWithoutAReactionIsNeverPublished(t *testing.T) {
	s := newStrategy(t, validParams())
	bars := make([]market.Candle, 0, 12)
	for i := 0; i < 12; i++ {
		bars = append(bars, market.Candle{Time: int64(1000 + 300*i), Open: 2010, High: 2011, Low: 2009, Close: 2010.4})
	}
	ctx := legacyfixture.Context(bars, 1, func(f *context.LegacyFrame, _ *context.LegacyRead) {
		// An unswept Asia high just above price, but no bar touched it.
		f.Sessions = []techniquezone.SessionRef{{Name: "ASIA_H", Price: 2011.5}}
	})
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatalf("a level nobody reacted to was published: %+v", got)
	}
}
