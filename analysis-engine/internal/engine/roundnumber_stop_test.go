package engine

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// RN-1: Round Number is a technique-lane strategy (algo-bot catalog is_scalp False), so on XAU it carries the instrument's 50-70 pip envelope, never the 30-40 pip
// M1 scalp envelope, and its detector is given the same pip size (0.1: 1 pip is $0.10, a $10 handle is 100 pips) and cap the Algo Bot enforces.
func TestRoundNumberStopEnvelopeIsTheInstrumentEnvelope(t *testing.T) {
	doc, err := config.ResolveDocument("../../../config/apexvoid.yml")
	if err != nil {
		t.Fatal(err)
	}
	settings, err := LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := ApplyInstrument(&settings, doc, "XAU"); err != nil {
		t.Fatal(err)
	}
	cfg := settings.StopEnvelope
	cfg.InstrumentMinPips, cfg.InstrumentMaxPips, cfg.InstrumentConfigured = settings.InstrumentStopMinPips, settings.InstrumentStopMaxPips, true
	geometry := market.Geometry{Symbol: "XAU", PipSize: 0.1, PriceDigits: 2}
	c := opportunity.Candidate{Strategy: "round_number", Symbol: "XAU", Direction: market.Buy}
	c.Entry.Low, c.Entry.High = 4000, 4001.5
	c.Targets = []opportunity.Target{{Price: market.PriceLevel{Price: 4010, Label: "next_round_handle"}}}
	env := computeStopEnvelope(c, geometry, cfg)
	if env == nil || env.FloorPips != 50 || env.CapPips != 70 || env.Source == "scalp_stop_envelope" {
		t.Fatalf("round_number envelope = %+v, want the XAU 50-70 envelope and not the scalp one", env)
	}
	found := false
	for _, s := range settings.Strategies {
		if string(s.ID) != "round_number" {
			continue
		}
		found = true
		if got := s.Parameters["pip_size"]; got != 0.1 {
			t.Errorf("round_number pip_size = %v, want 0.1 (XAU: $0.10 per pip, never $1)", got)
		}
		if got := s.Parameters["execution_stop_max_pips"]; got != 70.0 {
			t.Errorf("round_number execution_stop_max_pips = %v, want 70", got)
		}
		if got := s.Parameters["handle_step"]; got != 10.0 {
			t.Errorf("round_number handle_step = %v, want 10 dollars", got)
		}
	}
	if !found {
		t.Fatal("round_number is not in the resolved strategy list")
	}
}
