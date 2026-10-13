package structuralconfluence_test

import (
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structuralconfluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const m5Seconds = 300

func testdata(name string) string {
	_, file, _, _ := runtime.Caller(0)
	return filepath.Join(filepath.Dir(file), "..", "..", "testdata", name)
}

func loadM5(t *testing.T, name string) []market.Candle {
	t.Helper()
	capture, err := replaycapture.Load(testdata(name))
	if err != nil {
		t.Fatal(err)
	}
	candles, err := capture.Candles(market.M5)
	if err != nil {
		t.Fatal(err)
	}
	return candles
}

func at(t *testing.T, stamp string) int64 {
	t.Helper()
	parsed, err := time.Parse("2006-01-02 15:04", stamp)
	if err != nil {
		t.Fatal(err)
	}
	return parsed.UTC().Unix()
}

// windowAt returns the engine's 400-bar execution window ending with the candle
// that opens at the stamp: what the strategies see when that candle has closed.
func windowAt(t *testing.T, bars []market.Candle, stamp string) []market.Candle {
	t.Helper()
	want := at(t, stamp)
	for i, b := range bars {
		if b.Time == want {
			lo := i + 1 - 400
			if lo < 0 {
				lo = 0
			}
			return bars[lo : i+1]
		}
	}
	t.Fatalf("no candle opens at %s", stamp)
	return nil
}

// chain runs the zone chain exactly as the engine does and returns the
// structure the facts are derived from and the instances it publishes.
func chain(bars []market.Candle) (structuralconfluence.Frame, []techniquezone.Instance) {
	c := techniquezone.ProductionChainSettings()
	atr := techniquezone.ATRSeries(bars, c.ATRLength)
	swings := techniquezone.FindSwings(bars, c.SwingFractalN, c.ZigzagPct, c.ZigzagATRMult, atr, -1)
	frame := structuralconfluence.Frame{
		Bars: bars, Swings: swings, PeriodSeconds: m5Seconds,
		Breaks: techniquezone.StructureBreaks(swings, bars, false, c.SwingFractalN),
		Legs:   techniquezone.Displacement(bars, atr, c.DisplacementATRMult, c.MomentumBodyFrac),
	}
	return frame, techniquezone.TechniqueInstances(bars, c, techniquezone.ProductionTechniqueSettings())
}

func timeStr(u int64) string { return time.Unix(u, 0).UTC().Format("01-02 15:04") }
