package techniquezone_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// TestFrozenCRTOriginIndexIsAnH1PositionOnM5Bars documents a defect that the
// frozen Python port (CollectCRT -> validateInstance) carries and that the
// CRT v3 strategy deliberately does NOT inherit.
//
// DiscoverCRT stores the H1 candle's position in the H1 slice as the
// instance's OriginIndex. CollectCRT then hands that very number to
// validateInstance, which indexes the *M5* bars with it (notInvalidated scans
// bars[originIndex+1:]). The same M5 sweep episode is therefore judged against
// a different slice of M5 history depending only on how many H1 bars were
// loaded: with 400 H1 bars the origin is past the end of 150 M5 bars and the
// invalidation scan is skipped entirely.
//
// The frozen port stays bar-for-bar identical to the Python oracle (the oracle
// golden is not touched); this test pins the defect so it is explicit, and so
// a future change to the oracle port is a conscious one.
func TestFrozenCRTOriginIndexIsAnH1PositionOnM5Bars(t *testing.T) {
	m5 := crtDefectM5()
	anchor := market.Candle{Time: 0, Open: 4010, High: 4020, Low: 4000, Close: 4010}

	run := func(h1Count int) int {
		h1 := make([]market.Candle, h1Count)
		for i := range h1 {
			h1[i] = market.Candle{Time: int64(i) * 3600, Open: 4010, High: 4011, Low: 4009, Close: 4010}
		}
		h1[h1Count-1] = anchor
		h1[h1Count-1].Time = int64(h1Count-1) * 3600
		got := techniquezone.CollectCRT(h1, m5, 10, 2, techniquezone.CRTSettings{
			MinATR: 1.5, ReclaimBars: 6, EntryMaxWidthPrice: 5, H1LookbackBars: 3, ReactionLookbackBars: 3,
		}, techniquezone.ProductionTechniqueSettings())
		return len(got)
	}

	withShortWarmup := run(20)
	withProductionWarmup := run(400)
	if withShortWarmup == withProductionWarmup {
		t.Fatalf("expected the frozen port to depend on the H1 warm-up count (20 H1 bars -> %d instances, 400 H1 bars -> %d); "+
			"if this now passes the defect was fixed in the oracle port: update docs/strategies/crt.md and the parity notes",
			withShortWarmup, withProductionWarmup)
	}
	if withProductionWarmup != 1 || withShortWarmup != 0 {
		t.Fatalf("unexpected characterization: 20 H1 bars -> %d, 400 H1 bars -> %d (want 0 and 1)", withShortWarmup, withProductionWarmup)
	}
}

// crtDefectM5 is 150 M5 bars: a long excursion that closes below the H1 low
// (bars 30-80, an unreclaimed episode), a recovery into the range, then a
// fresh sweep of the H1 low that is reclaimed and rejected on the last bars.
func crtDefectM5() []market.Candle {
	bars := make([]market.Candle, 150)
	for i := range bars {
		c := market.Candle{Time: int64(i)*300 + 1_000_000, Open: 4010, High: 4011, Low: 4009, Close: 4010}
		switch {
		case i >= 30 && i <= 80:
			c.Open, c.High, c.Low, c.Close = 3995, 3996, 3994, 3995
		case i >= 81 && i < 139:
			c.Open, c.High, c.Low, c.Close = 4012, 4013, 4011, 4012
		}
		bars[i] = c
	}
	bars[139] = market.Candle{Time: bars[139].Time, Open: 4008, High: 4009, Low: 4005, Close: 4006}
	bars[140] = market.Candle{Time: bars[140].Time, Open: 4006, High: 4007, Low: 4003, Close: 4004}
	bars[141] = market.Candle{Time: bars[141].Time, Open: 4004, High: 4005, Low: 4001, Close: 4002}
	bars[142] = market.Candle{Time: bars[142].Time, Open: 4002, High: 4003, Low: 3999, Close: 4001}
	bars[143] = market.Candle{Time: bars[143].Time, Open: 4001, High: 4002, Low: 3998, Close: 4001}
	bars[144] = market.Candle{Time: bars[144].Time, Open: 4001, High: 4003, Low: 4000.5, Close: 4002.5}
	bars[145] = market.Candle{Time: bars[145].Time, Open: 4002.5, High: 4004, Low: 4001.5, Close: 4003}
	bars[146] = market.Candle{Time: bars[146].Time, Open: 4003, High: 4004, Low: 4002, Close: 4002.5}
	bars[147] = market.Candle{Time: bars[147].Time, Open: 4002.5, High: 4003.5, Low: 4001.5, Close: 4002}
	bars[148] = market.Candle{Time: bars[148].Time, Open: 4002, High: 4003, Low: 4000.8, Close: 4002.2}
	bars[149] = market.Candle{Time: bars[149].Time, Open: 4002, High: 4003, Low: 4000.5, Close: 4002.8}
	return bars
}
