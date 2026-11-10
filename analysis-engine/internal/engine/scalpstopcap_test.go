package engine

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

var scalpIDs = []opportunity.StrategyID{"impulse_pullback", "range_scalp", "impulse_retrace"}

// The real configuration resolves the 30-40 scalp book into every scalp detector and into the stop envelope Go attaches to their opportunities.
func TestScalpStopCapResolvesFromTheRealConfiguration(t *testing.T) {
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
	if settings.StopEnvelope.ScalpMinPips != 30 || settings.StopEnvelope.ScalpMaxPips != 40 {
		t.Fatalf("scalp envelope config = %v-%v, want 30-40", settings.StopEnvelope.ScalpMinPips, settings.StopEnvelope.ScalpMaxPips)
	}
	seen := map[opportunity.StrategyID]bool{}
	for _, s := range settings.Strategies {
		id := opportunity.StrategyID(s.ID)
		for _, want := range scalpIDs {
			if id != want {
				continue
			}
			seen[id] = true
			if got := s.Parameters["stop_minimum_pips"]; got != 30.0 {
				t.Errorf("%s stop_minimum_pips = %v, want 30", id, got)
			}
			if got := s.Parameters["stop_maximum_pips"]; got != 40.0 {
				t.Errorf("%s stop_maximum_pips = %v, want 40", id, got)
			}
		}
	}
	for _, id := range scalpIDs {
		if !seen[id] {
			t.Errorf("%s is not in the resolved strategy list", id)
		}
	}
}

// A scalp opportunity carries the dedicated 30-40 envelope, never the XAU instrument envelope (50-70) that the normal strategies carry.
func TestScalpOpportunitiesCarryTheDedicatedEnvelope(t *testing.T) {
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
	if cfg.InstrumentMinPips != 50 || cfg.InstrumentMaxPips != 70 {
		t.Fatalf("XAU instrument envelope = %v-%v, want 50-70", cfg.InstrumentMinPips, cfg.InstrumentMaxPips)
	}
	geometry := market.Geometry{Symbol: "XAU", PipSize: 0.1, PriceDigits: 2}
	candidate := func(id opportunity.StrategyID) opportunity.Candidate {
		c := opportunity.Candidate{Strategy: id, Symbol: "XAU", Direction: market.Buy}
		c.Entry.Low, c.Entry.High = 4000, 4000.4
		c.Targets = []opportunity.Target{{Price: market.PriceLevel{Price: 4006, Label: "scalp_ladder"}}}
		return c
	}
	for _, id := range scalpIDs {
		env := computeStopEnvelope(candidate(id), geometry, cfg)
		if env == nil || env.FloorPips != 30 || env.CapPips != 40 || env.Source != "scalp_stop_envelope" {
			t.Errorf("%s envelope = %+v, want floor 30 cap 40 source scalp_stop_envelope", id, env)
		}
		if env != nil && env.DesiredMinimumPips > 40 {
			t.Errorf("%s desired minimum %v exceeds the cap", id, env.DesiredMinimumPips)
		}
	}
	// A normal strategy on the same instrument keeps the instrument envelope, untouched.
	for _, id := range []opportunity.StrategyID{"key_level", "supply", "ifvg", "crt", "trendline"} {
		env := computeStopEnvelope(candidate(id), geometry, cfg)
		if env == nil || env.FloorPips != 50 || env.CapPips != 70 {
			t.Errorf("%s envelope = %+v, want the unchanged XAU 50-70 envelope", id, env)
		}
	}
}
