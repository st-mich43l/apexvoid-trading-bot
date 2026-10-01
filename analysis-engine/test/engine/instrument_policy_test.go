package engine_test

import (
	"testing"

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
