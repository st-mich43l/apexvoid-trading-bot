package engine

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

func widthCandidate(strategy opportunity.StrategyID, low, high float64) opportunity.Candidate {
	return opportunity.Candidate{Strategy: strategy, Entry: opportunity.EntryZone{Low: low, High: high}}
}

// XAU: pip_size 0.1, so 30 pips is 3.00 price units (checked against the instrument's own pip, never assumed).
func TestEntryZoneMinimumWidthXAU(t *testing.T) {
	xau := market.Geometry{Symbol: "XAU", PipSize: 0.1, PriceDigits: 2}
	cases := []struct {
		name     string
		low, hi  float64
		refused  bool
		strategy opportunity.StrategyID
	}{
		{"10 pips refused", 4300.00, 4301.00, true, "supply"},
		{"20 pips refused", 4300.00, 4302.00, true, "demand"},
		{"29.9 pips refused", 4300.00, 4302.99, true, "order_block"},
		{"30 pips accepted", 4300.00, 4303.00, false, "supply"},
		{"40 pips accepted", 4300.00, 4304.00, false, "fvg"},
		{"50 pips accepted", 4300.00, 4305.00, false, "key_level"},
		{"a level band of 3 pips is refused (Break and Retest)", 4115.14, 4115.44, true, "break_retest"},
		// A scalp may enter at one price: its own declared kind exempts it, nothing is reclassified.
		{"scalp 3 pips exempt", 4300.00, 4300.30, false, "range_scalp"},
		{"impulse pullback scalp exempt", 4300.00, 4300.30, false, "impulse_pullback"},
		{"impulse retrace scalp exempt", 4300.00, 4300.30, false, "impulse_retrace"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := EntryZoneBelowMinimumWidth(widthCandidate(tc.strategy, tc.low, tc.hi), xau, 30); got != tc.refused {
				t.Fatalf("refused=%v, want %v", got, tc.refused)
			}
		})
	}
}

// FX has no minimum configured: its single-price entry behaviour is untouched.
func TestEntryZoneMinimumWidthFXUnchanged(t *testing.T) {
	eur := market.Geometry{Symbol: "EURUSD", PipSize: 0.0001, PriceDigits: 5}
	if EntryZoneBelowMinimumWidth(widthCandidate("supply", 1.10000, 1.10005), eur, 0) {
		t.Fatal("an instrument with no minimum must never refuse a zone")
	}
}

// The minimum is a pip count of the instrument: the same 3.00-wide zone is 300 pips of EURUSD-sized pips and must not be mistaken for 30.
func TestEntryZoneMinimumWidthUsesInstrumentPip(t *testing.T) {
	odd := market.Geometry{Symbol: "XAU", PipSize: 0.01, PriceDigits: 2}
	if !EntryZoneBelowMinimumWidth(widthCandidate("supply", 4300, 4302.5), odd, 300) {
		t.Fatal("2.50 price units is 250 pips of a 0.01 pip: below 300")
	}
	if EntryZoneBelowMinimumWidth(widthCandidate("supply", 4300, 4303), odd, 300) {
		t.Fatal("3.00 price units is 300 pips of a 0.01 pip")
	}
}

// The production configuration: XAU carries the 30-pip minimum and its pip is 0.10 (so 30 pips is 3.00 price units); FX carries none.
func TestProductionConfigEntryZoneMinimum(t *testing.T) {
	doc, err := config.ResolveDocument("../../../config/apexvoid.yml")
	if err != nil {
		t.Skipf("production config not reachable from this checkout: %v", err)
	}
	want := map[string]float64{"XAU": 30, "EURUSD": 0, "GBPUSD": 0, "GBPJPY": 0, "USDJPY": 0}
	for symbol, minimum := range want {
		settings, err := LoadSettings(doc, market.M5, false)
		if err != nil {
			t.Fatal(err)
		}
		if err := ApplyInstrument(&settings, doc, symbol); err != nil {
			t.Fatalf("%s: %v", symbol, err)
		}
		if settings.InstrumentEntryZoneMinimumWidthPips != minimum {
			t.Fatalf("%s: minimum entry zone %v pips, want %v", symbol, settings.InstrumentEntryZoneMinimumWidthPips, minimum)
		}
		if symbol == "XAU" {
			if got := minimum * settings.Geometry.PipSize; got < 2.999999 || got > 3.000001 {
				t.Fatalf("XAU: 30 pips is %v price units, want 3.00", got)
			}
			if settings.InstrumentDeepEntryMinimumWidthPips != 30 {
				t.Fatalf("XAU: a stop-budget cut band must keep 30 pips, got %v", settings.InstrumentDeepEntryMinimumWidthPips)
			}
		}
	}
}
