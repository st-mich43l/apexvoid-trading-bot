package engine

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

func floorOf(t *testing.T, settings Settings, id strategy.StrategyID) float64 {
	t.Helper()
	for _, s := range settings.Strategies {
		if s.ID != id {
			continue
		}
		switch v := s.Parameters["confluence_floor"].(type) {
		case float64:
			return v
		case int:
			return float64(v)
		default:
			t.Fatalf("%s: confluence_floor is %#v", id, s.Parameters["confluence_floor"])
		}
	}
	t.Fatalf("%s is not configured", id)
	return 0
}

// RUN-1: CRT declares its own star floor (1) and keeps it; every other detector-contract strategy takes the shared detector floor (2), exactly as before.
func TestAStrategyMayDeclareItsOwnConfluenceFloorAndNoOtherStrategyDoes(t *testing.T) {
	settings := settingsFor(t, copyConfig(t, "false"))
	if got := floorOf(t, settings, "crt"); got != 1 {
		t.Fatalf("crt floor %v, want its own 1", got)
	}
	for _, id := range []strategy.StrategyID{"key_level", "supply", "demand", "confluence_zone", "order_block", "fvg", "ifvg", "flip_zone", "trendline", "break_retest"} {
		for _, s := range settings.Strategies {
			if s.ID != id {
				continue
			}
			if _, declared := s.Parameters["confluence_floor"]; !declared {
				continue
			}
			if got := floorOf(t, settings, id); got != 2 {
				t.Errorf("%s floor %v, want the shared detector floor 2", id, got)
			}
		}
	}
}

// The shipped values that let the three quiet strategies publish (RUN-1): Round Number's entry band is at least XAU's minimum entry zone, so ZL-1 no longer refuses it.
func TestRoundNumberBandIsNotBelowTheXAUMinimumEntryZone(t *testing.T) {
	settings := settingsFor(t, copyConfig(t, "false"))
	band, ok := strategyParams(t, settings, "round_number")["entry_band_pips"].(float64)
	if !ok || band < settings.InstrumentEntryZoneMinimumWidthPips {
		t.Fatalf("round_number entry_band_pips %v is below the XAU minimum entry zone %v", band, settings.InstrumentEntryZoneMinimumWidthPips)
	}
}
