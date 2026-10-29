package shockretest

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/scribe"
)

const fixtureHour = int64(1_760_000_400 - 1_760_000_400%3600)

func shockParams(override map[string]any) map[string]any {
	own := map[string]any{
		"atr_length": 14.0, "atr_window_bars": 140.0, "shock_range_atr": 3.0, "minimum_shock_body_ratio": 0.6, "shock_leg_atr": 4.0, "settle_bars": 1.0,
		"retest_max_bars": 24.0, "minimum_wick_ratio": 0.4, "displacement_grade_atr": 1.0, "confirmation_max_age_bars": 2.0, "stop_buffer_pips": 5.0,
		"stop_buffer_atr": 0.10, "minimum_target_r": 1.0, "expiry_hours": 2.0, "session_momentum_minimum_atr": 1.0, "symbols": []any{"XAU"},
		"execution_stop_max_pips": 70.0, "execution_stop_min_pips": 50.0, "execution_pip_size": 0.1,
	}
	for k, v := range override {
		own[k] = v
	}
	return legacyfixture.Params(own)
}

// workable lowers the room requirement to what the geometry can give: entering at the 50 percent retracement with the stop behind the origin
// leaves a reward:risk below one (0.5 L over 0.5 L plus the buffer), so the shipped 1.0 can never be met. TestTheShippedRoomRequirementCannotBeMet
// pins that.
var workable = map[string]any{"minimum_target_r": 0.8}

func testConfig(t *testing.T, override map[string]any) Config {
	t.Helper()
	cfg, err := ParseConfig(shockParams(override))
	if err != nil {
		t.Fatal(err)
	}
	return cfg
}

type shockIndex struct{ shock, retest int }

// bullishShockRetest: a quiet hour at 4100 (ATR 2), one wide decisive candle to 4110.3 (a 100 pip leg, 5 ATR), a settle candle, a pullback into the
// 50 to 61.8 percent band [4104.12, 4105.3], and a retest candle that rejects with a long lower wick. The last candle is the confirming one.
func bullishShockRetest() (bars []market.Candle, idx shockIndex) {
	s := scribe.New(fixtureHour-60*300, 4100)
	s.Filler(60, 4100)
	idx.shock = s.Index()
	s.Bar(4110.0, 0.3, 0.3) // shock: range 10.3 against an ATR of 2, body 0.94
	s.Bar(4109.5, 0.3, 0.3) // settle
	s.Bar(4108.0, 0.3, 0.3)
	s.Bar(4106.0, 0.2, 0.3)
	idx.retest = s.Index()
	s.Bar(4106.4, 0.1, 1.6) // retest: low 4104.4 inside the band, closes up with a lower wick of 0.7
	return s.Bars, idx
}

func newStrategy(t *testing.T, override map[string]any) strategy.Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: shockParams(override)})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func marketContext(m5 []market.Candle, htf string) *analysiscontext.MarketContext {
	return &analysiscontext.MarketContext{
		Symbol: "XAU",
		Legacy: &analysiscontext.LegacyRead{LocalStructure: "up", HTFBias: htf, AllowCounterTrend: true},
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
			market.M5: {Timeframe: market.M5, Candles: m5},
		},
	}
}
