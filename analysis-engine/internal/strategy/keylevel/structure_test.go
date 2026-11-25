package keylevel

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const t0 = int64(1_790_000_000)

// fixture is one confirmed swept reaction off a level, on 30 M5 bars (ATR 2.0, XAU pips of 0.1): the touch is bar 27 and the confirmation bar 28.
// BUY: the level is 4000 with the reaction band [bandLow, 4001]; SELL is the exact price mirror.
func fixture(t *testing.T, dir market.Direction, bandLow float64, mutate func(*Config)) (*Strategy, *attempt, *strategyutil.LegacyDetector) {
	t.Helper()
	cfg := Config{
		MinimumTouches: 2, InvalidationBufferATR: 0.5, FallbackTargetR: 2, ExpiryHours: 24, PriceDigits: 2, BreakoutAcceptBars: 2,
		StopBudget: strategyutil.StopBudget{MaxPips: 70, MinPips: 50, PipSize: 0.1, DeepEntryMinimumWidthPips: 30},
	}
	if mutate != nil {
		mutate(&cfg)
	}
	mirror := func(p float64) float64 {
		if dir == market.Sell {
			return 8000 - p
		}
		return p
	}
	bars := make([]market.Candle, 30)
	for i := range bars {
		bars[i] = market.Candle{Time: t0 + int64(i)*300, Open: mirror(4004), High: mirror(4005), Low: mirror(4003), Close: mirror(4004)}
	}
	// touch bar (27) sweeps 3 below the band's low and closes back inside; confirmation bar (28) closes above the band.
	bars[27] = market.Candle{Time: bars[27].Time, Open: mirror(4001), High: mirror(4002), Low: mirror(bandLow - 3), Close: mirror(bandLow + 1)}
	bars[28] = market.Candle{Time: bars[28].Time, Open: mirror(bandLow + 1), High: mirror(4005), Low: mirror(bandLow + 0.5), Close: mirror(4004)}
	if dir == market.Sell { // a SELL candle's high/low are mirrored: swap them
		for _, i := range []int{27, 28} {
			bars[i].High, bars[i].Low = bars[i].Low, bars[i].High
		}
	}
	frame := &context.LegacyFrame{Bars: bars, DetectorATR: 2, Structure: "range"}
	d := &strategyutil.LegacyDetector{Frame: frame, Direction: dir, Price: bars[29].Close, ATR: 2}
	lo, hi := bandLow, 4001.0
	if dir == market.Sell {
		lo, hi = 8000-4001, 8000-bandLow
	}
	a := &attempt{
		direction: dir, levelPrice: mirror(4000), reactLow: lo, reactHigh: hi,
		confirmation: &strategyutil.LegacyConfirmation{Type: "wick_rejection", TouchIndex: 27, ConfirmationIndex: 28, TouchTime: bars[27].Time, ConfirmationTime: bars[28].Time},
		result:       &strategyutil.LegacyResult{}, detector: d,
		level: techniquezone.Level{Price: mirror(4000), Kind: "swing", Touches: 3, Band: 1},
	}
	s := &Strategy{cfg: cfg, timeframe: market.M5, fingerprint: "f"}
	return s, a, d
}

// The published structure is the whole reaction band as the strategy read it, on M5, formed at the touch bar; a band cut by the stop budget does not shrink it.
func TestKeyLevelPublishesTheFullReactionBandAsItsStructure(t *testing.T) {
	cases := []struct {
		name    string
		bandLow float64 // 3999: the whole band fits the stop cap; 3990: the stop is too wide from the proximal edge, so the entry is cut deeper
		cut     bool
	}{
		{"band fits the stop budget", 3999, false},
		{"band cut by the stop budget (deep entry)", 3990, true},
	}
	for _, dir := range []market.Direction{market.Buy, market.Sell} {
		for _, tc := range cases {
			t.Run(string(dir)+": "+tc.name, func(t *testing.T) {
				s, a, d := fixture(t, dir, tc.bandLow, nil)
				c, ok := s.candidate(&context.MarketContext{Symbol: "XAUUSD"}, d, a, 1, false)
				if !ok {
					t.Fatal("the fixture reaction must publish")
				}
				if c.Structure == nil || c.Structure.Timeframe != market.M5 || c.Structure.FormedAt != a.confirmation.TouchTime {
					t.Fatalf("structure must be the M5 band formed at the touch: %+v", c.Structure)
				}
				if c.Structure.Low != a.reactLow || c.Structure.High != a.reactHigh {
					t.Fatalf("structure %v..%v must be the full band %v..%v", c.Structure.Low, c.Structure.High, a.reactLow, a.reactHigh)
				}
				normalised, err := opportunity.NormalizeGeometry(c, market.Geometry{Symbol: "XAUUSD", PipSize: 0.1, PriceDigits: 2})
				if err != nil {
					t.Fatal(err)
				}
				if normalised.Structure.Low > normalised.Entry.Low || normalised.Structure.High < normalised.Entry.High {
					t.Fatalf("the structure %v..%v must contain the normalised entry %v..%v", normalised.Structure.Low, normalised.Structure.High, normalised.Entry.Low, normalised.Entry.High)
				}
				if c.Structure.Swing != nil || c.ZoneID != "" {
					t.Fatalf("Key Level publishes no swing and no zone id: %+v / %q", c.Structure.Swing, c.ZoneID)
				}
				// The cap is measured from the stop, so a BUY band loses its upper (proximal) part and a SELL band its lower one.
				cutBand := c.Entry.High < a.reactHigh-1e-9 && dir == market.Buy || c.Entry.Low > a.reactLow+1e-9 && dir == market.Sell
				if cutBand != tc.cut {
					t.Fatalf("entry %v..%v: cut=%v, want %v", c.Entry.Low, c.Entry.High, cutBand, tc.cut)
				}
				if err := c.Validate(); err != nil {
					t.Fatalf("the published candidate must validate: %v", err)
				}
				want := a.confirmation.ConfirmationTime + int64(24*3600)
				if c.ExpiresAt != want {
					t.Fatalf("ExpiresAt %d, want confirmation + expiry_hours = %d", c.ExpiresAt, want)
				}
			})
		}
	}
}

// Shortening expiry_hours moves ExpiresAt with it: the entry window is the key, not a constant.
func TestKeyLevelExpiryFollowsExpiryHours(t *testing.T) {
	s, a, d := fixture(t, market.Buy, 3999, func(c *Config) { c.ExpiryHours = 1 })
	c, ok := s.candidate(&context.MarketContext{Symbol: "XAUUSD"}, d, a, 1, false)
	if !ok {
		t.Fatal("must publish")
	}
	if c.ExpiresAt != a.confirmation.ConfirmationTime+3600 {
		t.Fatalf("ExpiresAt %d, want confirmation + 1h", c.ExpiresAt)
	}
}

// The structure is rounded outward to the price grid, never inward, and a band already on the grid is unchanged.
func TestRoundOutWidensToThePriceGrid(t *testing.T) {
	for _, tc := range []struct{ low, high, wantLow, wantHigh float64 }{
		{4123.0035, 4126.9965, 4123.00, 4127.00},
		{4123.00, 4127.00, 4123.00, 4127.00},
		{4084.63, 4092.34, 4084.63, 4092.34},
		{1.10544, 1.1061, 1.10544, 1.1061},
	} {
		digits := 2
		if tc.low < 10 {
			digits = 5
		}
		low, high := roundOut(tc.low, tc.high, digits)
		if low != tc.wantLow || high != tc.wantHigh {
			t.Errorf("roundOut(%v, %v, %d) = %v, %v; want %v, %v", tc.low, tc.high, digits, low, high, tc.wantLow, tc.wantHigh)
		}
	}
}
