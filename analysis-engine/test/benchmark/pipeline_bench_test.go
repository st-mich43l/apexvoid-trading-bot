// Package benchmark_test measures the pipeline stages source task §55
// names, at the realistic history sizes §55 specifies (M1 2000, M5 1000,
// M15 1000, H1 500) — establishing a baseline, per §56 ("do not optimize
// blindly. Establish baseline first."), not asserting a performance
// target (none is claimed anywhere in this codebase's docs).
//
// Run: go test -bench=. -benchmem ./test/benchmark/...
package benchmark_test

import (
	"math/rand"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

func randomCandles(n int) []market.Candle {
	r := rand.New(rand.NewSource(1))
	price := 100.0
	out := make([]market.Candle, n)
	for i := 0; i < n; i++ {
		move := (r.Float64() - 0.5) * 2.0
		open := price
		cl := price + move
		hi, lo := open, open
		if cl > hi {
			hi = cl
		}
		if cl < lo {
			lo = cl
		}
		hi += r.Float64() * 0.5
		lo -= r.Float64() * 0.5
		out[i] = market.Candle{Time: int64(i + 1), Open: open, High: hi, Low: lo, Close: cl, Volume: 1}
		price = cl
	}
	return out
}

func structureSettings() structure.Settings {
	return structure.Settings{
		Version: "v2", PivotLeftBars: 2, PivotRightBars: 2,
		Promotion:         structure.PromotionConfig{MinimumExcursionATR: 0.5, InternalATR: 1.0, IntermediateATR: 2.0, MajorATR: 3.5},
		EqualToleranceATR: 0.05,
		Break: structure.BreakConfig{
			MinimumPenetrationATR: 0.5, SweepReclaimBars: 6, FailedBreakReclaimBars: 3, DisplacementMaxBars: 6,
			Displacement: structure.DisplacementConfig{RangeATR: 1.5, BodyDominance: 0.55},
		},
	}
}

func BenchmarkHistoryAppend(b *testing.B) {
	candles := randomCandles(b.N + 2000)
	h := marketdata.NewTimeframeHistory("XAU", "M5", 2000, false)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		h.Append(candles[i])
	}
}

func BenchmarkCanonicalATR_M5_1000(b *testing.B) {
	candles := randomCandles(1000)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, _ = indicator.CanonicalATR(candles, 14, indicator.AlgorithmSimple)
	}
}

func BenchmarkRollingSimpleATR_PerBar(b *testing.B) {
	candles := randomCandles(b.N)
	r := indicator.NewRollingSimpleATR(14)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		r.Update(candles[i])
	}
}

func BenchmarkDetectPivots_M5_1000(b *testing.B) {
	candles := randomCandles(1000)
	atr, _ := indicator.CanonicalATR(candles, 14, indicator.AlgorithmSimple)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		structure.DetectPivots(candles, 2, 2, atr)
	}
}

func BenchmarkStructureUpdate_M1_2000(b *testing.B) {
	benchStructureUpdate(b, 2000, "M1")
}

func BenchmarkStructureUpdate_M5_1000(b *testing.B) {
	benchStructureUpdate(b, 1000, "M5")
}

func BenchmarkStructureUpdate_H1_500(b *testing.B) {
	benchStructureUpdate(b, 500, "H1")
}

func benchStructureUpdate(b *testing.B, n int, tf market.Timeframe) {
	candles := randomCandles(n)
	atr, _ := indicator.CanonicalATR(candles, 14, indicator.AlgorithmSimple)
	cfg := structureSettings()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		structure.Update(candles, atr, tf, cfg)
	}
}

func BenchmarkLiquidityUpdate_M5_1000(b *testing.B) {
	candles := randomCandles(1000)
	atr, _ := indicator.CanonicalATR(candles, 14, indicator.AlgorithmSimple)
	structState := structure.Update(candles, atr, "M5", structureSettings())
	cfg := liquidity.Config{Version: "v1", EqualLevelToleranceATR: 0.05, PoolMinimumTouches: 2, SweepReclaimBars: 6}
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		liquidity.Update(candles, atr, structState.Swings, cfg)
	}
}

// BenchmarkFullSymbolEventProcessing_M5_1000 measures one complete
// history-append -> ATR -> structure -> liquidity pass over a realistic
// M5 window — the per-bar cost a live SymbolWorker.Apply call actually
// pays (minus telemetry/context overhead, both cheap map operations
// already excluded from the isolated benchmarks above).
func BenchmarkFullSymbolEventProcessing_M5_1000(b *testing.B) {
	candles := randomCandles(1000)
	cfg := structureSettings()
	liqCfg := liquidity.Config{Version: "v1", EqualLevelToleranceATR: 0.05, PoolMinimumTouches: 2, SweepReclaimBars: 6}
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		atr, _ := indicator.CanonicalATR(candles, 14, indicator.AlgorithmSimple)
		structState := structure.Update(candles, atr, "M5", cfg)
		liquidity.Update(candles, atr, structState.Swings, liqCfg)
	}
}
