package flipzone_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/flipzone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

// The decision itself is the frozen flip_demand/supply_zone_reaction, proven bar
// by bar on real captures in test/legacyparity. These tests pin the contract's
// edges: configuration, the absence of a detector frame, and that a plain zone
// is never a flip.

func validParams() map[string]any {
	return legacyfixture.Params(map[string]any{
		"invalidation_buffer_atr": 0.5, "minimum_target_distance_atr": 1.0, "expiry_hours": 24.0, "breakout_accept_bars": 2.0,
	})
}

func newStrategy(t *testing.T, params map[string]any) strategy.Strategy {
	t.Helper()
	s, err := flipzone.New(strategy.Config{ID: flipzone.ID, Version: flipzone.Version, Enabled: true, Parameters: params})
	if err != nil {
		t.Fatalf("flipzone.New: %v", err)
	}
	return s
}

func TestFlipZone_RejectsMissingOrInvalidConfiguration(t *testing.T) {
	if _, err := flipzone.New(strategy.Config{ID: flipzone.ID, Parameters: map[string]any{}}); err == nil {
		t.Fatal("missing configuration must fail closed")
	}
	bad := validParams()
	bad["breakout_accept_bars"] = 0.0
	if _, err := flipzone.New(strategy.Config{ID: flipzone.ID, Parameters: bad}); err == nil {
		t.Fatal("a role flip needs at least one accepted close")
	}
}

func TestFlipZone_WithoutADetectorFramePublishesNothing(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := &context.MarketContext{Symbol: "XAU", Timeframes: map[market.Timeframe]*context.TimeframeContext{}, Volatility: context.VolatilityContext{ATR: 1}}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatalf("published without a frame: %+v", got)
	}
}

func TestFlipZone_APlainZoneIsNotAFlip(t *testing.T) {
	s := newStrategy(t, validParams())
	bars := make([]market.Candle, 0, 12)
	for i := 0; i < 12; i++ {
		bars = append(bars, market.Candle{Time: int64(1000 + 300*i), Open: 2021, High: 2023, Low: 2019, Close: 2021.5})
	}
	ctx := legacyfixture.Context(bars, 1, func(f *context.LegacyFrame, _ *context.LegacyRead) {
		// A demand zone with no flip source, sitting on the price.
		f.Zones = []techniquezone.Zone{{Bottom: 2019, Top: 2022, Side: "demand", Source: "supply_demand", Sources: []string{"supply_demand"}, BreakIndex: -1, Score: 12}}
	})
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatalf("an ordinary zone touch was published as a flip: %+v", got)
	}
}

func TestFlipZone_TheRoleMustAgreeWithTheDirection(t *testing.T) {
	// Closes that never break the level leave it a support/resistance, not a broken
	// one, so neither side's flip qualifies.
	closes := []float64{100, 101, 100.5, 101, 100.8}
	if role := keylevel.Role("support", 99, 99.5, closes, 2); role == keylevel.RoleBrokenSupport {
		t.Fatalf("unbroken level classified as a broken support: %v", role)
	}
}
