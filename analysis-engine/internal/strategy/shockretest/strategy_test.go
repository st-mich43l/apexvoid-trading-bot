package shockretest

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

func TestCandidateCarriesTheTechnicalContractAndPassesValidation(t *testing.T) {
	bars, idx := bullishShockRetest()
	got := newStrategy(t, workable).Evaluate(marketContext(bars, "up"))
	if len(got) != 1 {
		t.Fatalf("candidates = %d", len(got))
	}
	c := got[0]
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	if c.Strategy != ID || c.StrategyVersion != Version || c.Direction != market.Buy || c.Symbol != "XAU" {
		t.Fatalf("identity = %+v", c)
	}
	if c.CreatedAt != bars[idx.retest].Time || c.ExpiresAt != c.CreatedAt+2*3600 || c.FormedAt != bars[idx.shock].Time {
		t.Fatalf("formed %d created %d expires %d", c.FormedAt, c.CreatedAt, c.ExpiresAt)
	}
	if c.Reaction == nil || c.Reaction.Pattern != "wick_rejection" || c.Reaction.ReactionType != "rejection" || c.Reaction.ZoneID != c.StructuralID {
		t.Fatalf("reaction = %+v", c.Reaction)
	}
	for _, code := range []string{"m5_volatility_shock", "m5_shock_first_retest", "m5_shock_retest_rejection"} {
		if !evidenceHas(c.Evidence, code) {
			t.Errorf("evidence %q missing: %v", code, c.Evidence)
		}
	}
	if c.Targets[0].Price.Label != TargetShockExtreme {
		t.Fatalf("targets = %+v", c.Targets)
	}
	f := c.DetectorConfluence.Factors
	if !f.HTFAligned || !f.WickRejection || !f.DisplacementGrade || f.StructuralAgreement || f.SessionContext {
		t.Fatalf("factors = %+v", f)
	}
	if err := quality.Verify(c.Quality.Overall, c.Quality.Components); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"technical_rr", "technical_risk_pips", "shock_leg_atr"} {
		if _, ok := c.Quality.Components[key]; !ok {
			t.Errorf("quality component %q missing", key)
		}
	}
}

func TestAgainstTheBiasTheSetupIsBelowTheConfluenceFloor(t *testing.T) {
	bars, _ := bullishShockRetest()
	analysis, candidates := newStrategy(t, workable).(*Strategy).Analyze(marketContext(bars, "down"))
	if len(candidates) != 0 || !hasReason(analysis, ReasonConfluenceBelowFloor) {
		t.Fatalf("%d candidates, %+v", len(candidates), analysis.Rejections)
	}
}

// No measured fact, no factor.
func TestEveryConfluenceFactorIsMeasuredNeverDefaulted(t *testing.T) {
	s := newStrategy(t, workable).(*Strategy)
	if got := s.factors(&analysiscontext.MarketContext{Symbol: "XAU"}, Setup{Direction: market.Buy}); got != (confluence.Factors{}) {
		t.Fatalf("a setup with no measured fact scored %+v", got)
	}
}

func TestCandidateIdentityIsStableAcrossFreshEvaluations(t *testing.T) {
	bars, _ := bullishShockRetest()
	var first string
	for extra := 0; extra <= 2; extra++ {
		series := append([]market.Candle(nil), bars...)
		for i := 0; i < extra; i++ {
			last := series[len(series)-1]
			series = append(series, market.Candle{Time: last.Time + 300, Open: 4106.4, High: 4106.8, Low: 4106.1, Close: 4106.4})
		}
		got := newStrategy(t, workable).Evaluate(marketContext(series, "up"))
		if len(got) != 1 {
			t.Fatalf("%d extra candles: %d candidates", extra, len(got))
		}
		if first == "" {
			first = got[0].ID
		} else if got[0].ID != first {
			t.Fatalf("the same technical event changed identity: %s vs %s", first, got[0].ID)
		}
	}
}
