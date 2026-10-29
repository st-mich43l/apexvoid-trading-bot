package asiarange

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/scribe"
)

// london is London open (an hour-aligned UTC instant): the Asia range becomes knowable then.
const london = int64(1_760_000_400 - 1_760_000_400%86400 + 7*3600)

// The Asia range is [4100, 4130]: 30 price units, 3.75 M15 ATR of 8 (inside the 1 to 6 window), mid 4115.
const (
	asiaLow, asiaHigh, asiaMid = 4100.0, 4130.0, 4115.0
)

func asiaParams(override map[string]any) map[string]any {
	own := map[string]any{
		"atr_length": 14.0, "atr_window_bars": 140.0, "minimum_range_atr": 1.0, "maximum_range_atr": 6.0, "active_window_hours": 7.0,
		"minimum_sweep_pips": 3.0, "minimum_sweep_atr": 0.10, "minimum_reclaim_pips": 1.0, "minimum_reclaim_atr": 0.05, "reclaim_max_bars": 6.0,
		"structure_pivot_bars": 2.0, "structure_lookback_bars": 24.0, "mss_max_bars": 12.0, "minimum_mss_body_ratio": 0.5,
		"minimum_mss_displacement_atr": 0.5, "minimum_mss_close_strength": 0.6, "confirmation_max_age_bars": 2.0, "entry_band_pips": 20.0,
		"break_buffer_atr": 0.15, "acceptance_closes": 2.0, "retest_max_bars": 24.0, "displacement_grade_atr": 1.0, "minimum_wick_ratio": 0.4,
		"measured_move_fraction": 0.5, "stop_buffer_pips": 5.0, "stop_buffer_atr": 0.10, "minimum_target_r": 1.0, "expiry_hours": 4.0,
		"session_momentum_minimum_atr": 1.0, "symbols": []any{"XAU"}, "execution_stop_max_pips": 70.0, "execution_stop_min_pips": 50.0, "execution_pip_size": 0.1,
	}
	for k, v := range override {
		own[k] = v
	}
	return legacyfixture.Params(own)
}

func testConfig(t *testing.T, override map[string]any) Config {
	t.Helper()
	cfg, err := ParseConfig(asiaParams(override))
	if err != nil {
		t.Fatal(err)
	}
	return cfg
}

func anchorInput() *RawAnchor { return &RawAnchor{High: asiaHigh, Low: asiaLow, AvailableAt: london} }

// m15Series is n closed M15 candles ending at `end` (exclusive) of range 8 around centre: M15 ATR 8.
func m15Series(end int64, n int, centre float64) []market.Candle {
	out := make([]market.Candle, n)
	for i := range out {
		open := centre - 0.5
		if i%2 == 1 {
			open = centre + 0.5
		}
		out[i] = market.Candle{Time: end - int64(n-i)*900, Open: open, High: centre + 4, Low: centre - 4, Close: centre + (centre - open)}
	}
	return out
}

type raidIndex struct{ sweep, shift int }

// bullishRaid is the canonical accepted XAU BUY: London opens, price falls from 4105 to the Asia low 4100 making a lower swing high of 4106.4, a
// candle sweeps 15 pips under it and closes back above, and a displacement candle closes above the swing high. It returns the M5 and M15 series
// through the structure-shift candle (the last M5 candle).
func bullishRaid() (m5, m15 []market.Candle, idx raidIndex) {
	s := scribe.New(london-70*300, 4104)
	s.Filler(70, 4104) // Asia
	s.Bar(4105.2, 0.4, 0.3)
	s.Bar(4106.0, 0.4, 0.3) // swing high 4106.4 (pivot, n=2)
	s.Bar(4105.0, 0.1, 0.4)
	s.Bar(4103.6, 0.1, 0.4)
	s.Bar(4101.0, 0.3, 0.4)
	idx.sweep = s.Index()
	s.Bar(4101.4, 0.2, 2.4) // sweep: low 4098.6 (14 pips under 4100), a bullish candle that closes back above on the same candle
	s.Bar(4102.0, 0.3, 0.2)
	idx.shift = s.Index()
	s.Bar(4107.2, 0.2, 0.1) // closes above 4106.4: structure shift
	return s.Bars, m15Series(london, 40, 4115), idx
}

type breakIndex struct{ brk, retest int }

// bullishAcceptance is the canonical accepted XAU BUY off the top of the range: an M15 candle closes above the Asia high 4130 by more than 0.15 M15
// ATR, two M5 closes hold above it, the first retest of the edge band wicks under the level and rejects.
func bullishAcceptance() (m5, m15 []market.Candle, idx breakIndex) {
	s := scribe.New(london-70*300, 4126)
	s.Filler(70, 4126)
	s.Filler(20, 4126)
	idx.brk = s.Index()
	s.Bar(4131.8, 0.2, 0.2) // the M5 candle that closes with the M15 break candle
	s.Bar(4132.2, 0.3, 0.3) // acceptance 1
	s.Bar(4132.0, 0.3, 0.3) // acceptance 2
	idx.retest = s.Index()
	s.Bar(4132.6, 0.1, 3.1) // retest: low 4128.9 (under the level), closes above the band top with a long lower wick
	m15 = m15Series(london, 40, 4126)
	for j := 0; j < 6; j++ {
		m15 = append(m15, market.Candle{Time: london + int64(j)*900, Open: 4125.5, High: 4129.0, Low: 4122.0, Close: 4126.0})
	}
	m15 = append(m15, market.Candle{Time: london + 6*900, Open: 4125.0, High: 4134.0, Low: 4124.5, Close: 4133.5})
	return s.Bars, m15, idx
}

func marketContext(m5, m15 []market.Candle, htf string) *analysiscontext.MarketContext {
	return &analysiscontext.MarketContext{
		Symbol: "XAU",
		Legacy: &analysiscontext.LegacyRead{LocalStructure: "up", HTFBias: htf, AllowCounterTrend: true},
		Session: analysiscontext.SessionContext{State: session.State{Levels: []session.Level{
			{Name: EdgeHigh, Price: asiaHigh, Final: true, AvailableAt: london},
			{Name: EdgeLow, Price: asiaLow, Final: true, AvailableAt: london},
		}}},
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
			market.M5:  {Timeframe: market.M5, Candles: m5},
			market.M15: {Timeframe: market.M15, Candles: m15},
		},
	}
}

func newStrategy(t *testing.T, override map[string]any) strategy.Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: asiaParams(override)})
	if err != nil {
		t.Fatal(err)
	}
	return s
}
