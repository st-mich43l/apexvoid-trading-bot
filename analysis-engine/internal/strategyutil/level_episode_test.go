package strategyutil

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func TestPivotHighsNeedStrictlyLowerNeighboursOnBothSides(t *testing.T) {
	highs := []float64{1, 2, 5, 3, 2, 4, 4, 1}
	bars := make([]market.Candle, len(highs))
	for i, h := range highs {
		bars[i] = market.Candle{Time: int64(i) * 300, High: h, Low: 0}
	}
	got := PivotHighs(bars, 2)
	if len(got) != 1 || got[0] != 2 {
		t.Fatalf("pivots = %v, want [2] (the 4,4 pair is not strict)", got)
	}
}

func TestRetestRejectionByEngulfingNeedsNoWick(t *testing.T) {
	const level = 100.0
	p := LevelRetestParams{PipSize: 0.1, AcceptanceCloses: 1, RetestMaxBars: 5, BandBelow: 0.5, BandAbove: 1.5, MinWickRatio: 0.4}
	bar := func(i int, open, high, low, closePrice float64) market.Candle {
		return market.Candle{Time: int64(i) * 300, Open: open, High: high, Low: low, Close: closePrice}
	}
	bars := []market.Candle{
		bar(0, 99, 99.5, 98.5, 99.2),
		bar(1, 99.2, 102.6, 99.1, 102.5), // break
		bar(2, 102.5, 103.0, 102.4, 102.9),
		bar(3, 102.9, 103.0, 101.0, 101.1),  // retest: enters the band (<= 101.5), closes down
		bar(4, 101.0, 103.2, 100.95, 103.0), // bullish engulfing of the retest candle, closes above the level
	}
	r, reason, ok := FindLevelRetest(bars, level, 1, p)
	if !ok || reason != "" || !r.Engulfing || r.WickRejection || r.Confirm != 4 || r.Retest != 3 {
		t.Fatalf("got %+v %q %v", r, reason, ok)
	}
}

func TestParseSymbolsAndScope(t *testing.T) {
	got, err := ParseSymbols(map[string]any{"symbols": []any{"XAU"}})
	if err != nil || len(got) != 1 || !SymbolAllowed(got, "XAU") || SymbolAllowed(got, "EURUSD") {
		t.Fatalf("%v %v", got, err)
	}
	if none, _ := ParseSymbols(map[string]any{}); !SymbolAllowed(none, "EURUSD") {
		t.Fatal("no scope allows every instrument")
	}
	if _, err := ParseSymbols(map[string]any{"symbols": "XAU"}); err == nil {
		t.Fatal("a scalar is not a list")
	}
}

func TestTradingDayRollsOverAtTheConfiguredHour(t *testing.T) {
	const day = int64(86400)
	base := 20000 * day
	if TradingDay(base+20*3600, 21) == TradingDay(base+21*3600, 21) {
		t.Fatal("the trading day must change at 21:00 UTC")
	}
	if TradingDay(base+21*3600, 21) != TradingDay(base+day+20*3600, 21) {
		t.Fatal("21:00 and the next day's 20:59 are one trading day")
	}
}
