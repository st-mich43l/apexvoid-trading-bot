package strategyutil_test

import (
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

func TestCandidateRejectsLaggingCrossTimeframeConfirmation(t *testing.T) {
	_, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: "crt", Version: "v2", SetupKey: "lagging-m5", Symbol: "XAU",
		Direction: market.Buy, EntryLow: 100, EntryHigh: 101,
		Invalidation: 99, InvalidationLabel: "failed", Target: 103,
		TargetLabel: "objective", Evidence: []string{"h1_range", "m5_reclaim"},
		Quality: opportunity.StrategyQuality{Overall: 1}, FormedAt: 200, ConfirmedAt: 100, ExpiryHours: 4,
		Fingerprint: "fixture",
	})
	if err == nil || !strings.Contains(err.Error(), "formation must not follow creation") {
		t.Fatalf("expected causal timestamp rejection, got %v", err)
	}
}
