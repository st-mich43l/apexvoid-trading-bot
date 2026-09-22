package structure_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// level is a SwingHigh at price 100 — breaking it is a bullish (Buy)
// event throughout this file. cfg uses this session's own real,
// already-shipped thresholds (PR #574: 0.5 ATR tolerance/6-bar reclaim in
// the legacy system; here scaled down to keep fixtures small and
// hand-traceable: tol=0.1 ATR, sweep window 3 bars, failed window 1 bar).
func breakLevel() structure.Swing {
	return structure.Swing{Kind: structure.SwingHigh, Price: 100, ID: "lvl1", Layer: structure.StructureMajor}
}

func breakCfg(dispRangeATR float64) structure.BreakConfig {
	return structure.BreakConfig{
		MinimumPenetrationATR: 0.1, SweepReclaimBars: 3, FailedBreakReclaimBars: 1,
		DisplacementMaxBars: 3,
		Displacement:        structure.DisplacementConfig{RangeATR: dispRangeATR, BodyDominance: 0.55},
	}
}

func TestDetectBreak_WickNeverClosesBeyond(t *testing.T) {
	bars := []market.Candle{
		c(1, 99, 100.2, 98.9, 99.5), c(2, 99.5, 99.8, 99.3, 99.6),
		c(3, 99.6, 99.9, 99.4, 99.7), c(4, 99.7, 99.9, 99.5, 99.8),
	}
	atr := flatATR(len(bars), 1.0)
	brk := structure.DetectBreak(bars, 0, breakLevel(), atr, breakCfg(100))
	if brk == nil {
		t.Fatal("expected a break record (a wick beyond, even if never held)")
	}
	if brk.Type != structure.BreakWick {
		t.Errorf("expected BreakWick, got %v", brk.Type)
	}
	if brk.CloseBeyond {
		t.Error("CloseBeyond must be false — no bar ever closed past the level")
	}
	if brk.ConfirmedAt != bars[3].Time {
		t.Errorf("expected ConfirmedAt = end of the wait window (bar 4), got %d", brk.ConfirmedAt)
	}
	if brk.Direction != market.Buy || brk.Layer != structure.StructureMajor || brk.BrokenSwingID != "lvl1" {
		t.Errorf("Direction/Layer/BrokenSwingID must carry the level's identity through")
	}
}

func TestDetectBreak_HeldCloseWithNoDisplacement(t *testing.T) {
	bars := []market.Candle{
		c(1, 99.8, 100.6, 99.7, 100.5), c(2, 100.5, 100.7, 100.3, 100.6),
		c(3, 100.6, 100.8, 100.4, 100.7), c(4, 100.7, 100.9, 100.5, 100.8),
	}
	atr := flatATR(len(bars), 1.0)
	brk := structure.DetectBreak(bars, 0, breakLevel(), atr, breakCfg(100)) // displacement threshold unreachable
	if brk == nil {
		t.Fatal("expected a break")
	}
	if brk.Type != structure.BreakClose {
		t.Errorf("expected BreakClose, got %v", brk.Type)
	}
	if !brk.CloseBeyond {
		t.Error("CloseBeyond must be true")
	}
	if brk.Displacement {
		t.Error("Displacement must be false with an unreachable threshold")
	}
	if brk.ConfirmedAt != bars[3].Time {
		t.Errorf("expected ConfirmedAt = end of the reclaim-wait window, got %d", brk.ConfirmedAt)
	}
}

func TestDetectBreak_HeldCloseWithQualifyingDisplacement(t *testing.T) {
	bars := []market.Candle{
		c(1, 99, 105, 98.9, 104.8), c(2, 104.8, 105.2, 104.5, 105.0),
		c(3, 105.0, 105.3, 104.7, 105.1), c(4, 105.1, 105.4, 104.8, 105.2),
	}
	atr := flatATR(len(bars), 1.0)
	brk := structure.DetectBreak(bars, 0, breakLevel(), atr, breakCfg(1.5)) // real threshold, bar 0 clears it alone
	if brk == nil {
		t.Fatal("expected a break")
	}
	if brk.Type != structure.BreakDisplacement {
		t.Errorf("expected BreakDisplacement, got %v", brk.Type)
	}
	if !brk.Displacement {
		t.Error("Displacement must be true")
	}
}

func TestDetectBreak_SweptAndReclaimedOutsideTheFailedWindow(t *testing.T) {
	bars := []market.Candle{
		c(1, 99.8, 100.6, 99.7, 100.5), // closeBeyondAt = 0
		c(2, 100.5, 100.7, 100.3, 100.6),
		c(3, 100.6, 100.65, 99.8, 99.9), // reclaims 2 bars later — > FailedBreakReclaimBars(1), <= SweepReclaimBars(3)
		c(4, 99.9, 100.0, 99.7, 99.85),
	}
	atr := flatATR(len(bars), 1.0)
	brk := structure.DetectBreak(bars, 0, breakLevel(), atr, breakCfg(100))
	if brk == nil {
		t.Fatal("expected a break")
	}
	if brk.Type != structure.BreakSweep {
		t.Errorf("expected BreakSweep, got %v", brk.Type)
	}
	if brk.ConfirmedAt != bars[2].Time {
		t.Errorf("expected ConfirmedAt = the reclaim bar (index 2), got %d", brk.ConfirmedAt)
	}
}

func TestDetectBreak_SweptAndReclaimedWithinTheFailedWindow(t *testing.T) {
	bars := []market.Candle{
		c(1, 99.8, 100.6, 99.7, 100.5), // closeBeyondAt = 0
		c(2, 100.5, 100.6, 99.8, 99.9), // reclaims 1 bar later — <= FailedBreakReclaimBars(1)
		c(3, 99.9, 100.0, 99.7, 99.85),
		c(4, 99.85, 99.95, 99.7, 99.8),
	}
	atr := flatATR(len(bars), 1.0)
	brk := structure.DetectBreak(bars, 0, breakLevel(), atr, breakCfg(100))
	if brk == nil {
		t.Fatal("expected a break")
	}
	if brk.Type != structure.BreakFailed {
		t.Errorf("expected BreakFailed, got %v", brk.Type)
	}
	if brk.ConfirmedAt != bars[1].Time {
		t.Errorf("expected ConfirmedAt = the reclaim bar (index 1), got %d", brk.ConfirmedAt)
	}
}

func TestDetectBreak_NoTouchAtAllReturnsNil(t *testing.T) {
	bars := []market.Candle{c(1, 99, 99.5, 98.5, 99.2), c(2, 99.2, 99.6, 98.8, 99.3)}
	atr := flatATR(len(bars), 1.0)
	if brk := structure.DetectBreak(bars, 0, breakLevel(), atr, breakCfg(100)); brk != nil {
		t.Errorf("expected nil (price never approached the level), got %+v", brk)
	}
}
