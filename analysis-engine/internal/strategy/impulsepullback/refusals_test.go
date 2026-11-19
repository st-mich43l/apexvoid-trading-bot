package impulsepullback

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

// Every refused setup is reported once per gate, impulse and direction, and TakeRefusals drains the counts (strategy.RefusalReporter).
func TestRefusalsAreCountedOncePerGateAndDrained(t *testing.T) {
	var _ strategy.RefusalReporter = (*Strategy)(nil)
	s := &Strategy{}
	s.countRefusal(refusedNoReference, 1000, market.Buy)
	s.countRefusal(refusedNoReference, 1000, market.Buy) // the same impulse on the next M1 bar
	s.countRefusal(refusedNoReference, 1000, market.Sell)
	s.countRefusal(refusedRoleMismatch, 1000, market.Buy)
	got := s.TakeRefusals()
	if got[strategy.Refusal{Reason: refusedNoReference, Timeframe: market.M1}] != 2 || got[strategy.Refusal{Reason: refusedRoleMismatch, Timeframe: market.M1}] != 1 {
		t.Fatalf("unexpected counts: %+v", got)
	}
	if again := s.TakeRefusals(); len(again) != 0 {
		t.Fatalf("a drained reporter must be empty: %+v", again)
	}
}
