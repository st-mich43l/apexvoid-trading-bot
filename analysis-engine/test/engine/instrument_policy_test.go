package engine_test

import (
	"fmt"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

func defendedCandidate(direction market.Direction, low, high float64) opportunity.Candidate {
	return opportunity.Candidate{Direction: direction, Entry: opportunity.EntryZone{Low: low, High: high}}
}

func TestBlockedByDefendedLevel_OnlyABuyNearTheLevelIsBlocked(t *testing.T) {
	s := engine.Settings{DefendedLevels: []float64{160.0}, DefendedLevelBuffer: 0.30}

	cases := []struct {
		name      string
		candidate opportunity.Candidate
		blocked   bool
	}{
		{"buy just under the level", defendedCandidate(market.Buy, 159.60, 159.80), true},
		{"buy straddling the level", defendedCandidate(market.Buy, 159.90, 160.10), true},
		{"buy just above the level", defendedCandidate(market.Buy, 160.20, 160.40), true},
		{"buy exactly at the buffer edge", defendedCandidate(market.Buy, 159.50, 159.70), true},
		{"buy outside the buffer", defendedCandidate(market.Buy, 159.00, 159.40), false},
		{"sell near the level stays eligible (aligned with intervention)", defendedCandidate(market.Sell, 159.60, 159.80), false},
	}
	for _, c := range cases {
		if _, blocked := s.BlockedByDefendedLevel(c.candidate); blocked != c.blocked {
			t.Errorf("%s: blocked = %v, want %v", c.name, blocked, c.blocked)
		}
	}
}

func TestBlockedByDefendedLevel_NoConfiguredLevelNeverBlocks(t *testing.T) {
	if _, blocked := (engine.Settings{}).BlockedByDefendedLevel(defendedCandidate(market.Buy, 159.9, 160.0)); blocked {
		t.Fatal("an instrument with no defended level must never block")
	}
	zeroBuffer := engine.Settings{DefendedLevels: []float64{160}}
	if _, blocked := zeroBuffer.BlockedByDefendedLevel(defendedCandidate(market.Buy, 159.9, 160.0)); blocked {
		t.Fatal("a zero buffer disables the guard")
	}
}

func TestApplyInstrument_GBPJPYKeyLevelIsStricterThanTheDefault(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	keyLevel := func(symbol string) map[string]any {
		settings, err := engine.LoadSettings(doc, "M5", false)
		if err != nil {
			t.Fatal(err)
		}
		if err := engine.ApplyInstrument(&settings, doc, symbol); err != nil {
			t.Fatal(err)
		}
		for _, s := range settings.Strategies {
			if s.ID == "key_level" {
				return s.Parameters
			}
		}
		t.Fatal("no key_level strategy configured")
		return nil
	}
	gbpjpy, xau := keyLevel("GBPJPY"), keyLevel("XAU")
	if gbpjpy["minimum_touches"] != 3.0 || gbpjpy["require_explicit_role"] != true {
		t.Fatalf("GBPJPY key_level params = %v, want minimum_touches 3 and require_explicit_role true", gbpjpy)
	}
	if fmt.Sprint(xau["minimum_touches"]) != "2" || xau["require_explicit_role"] != nil {
		t.Fatalf("XAU key_level must keep the default rules, got %v", xau)
	}
}
