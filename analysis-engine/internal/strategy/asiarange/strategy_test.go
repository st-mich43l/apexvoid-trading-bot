package asiarange

import (
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

func TestCandidateCarriesTheTechnicalContractAndPassesValidation(t *testing.T) {
	m5, m15, idx := bullishRaid()
	got := newStrategy(t, nil).Evaluate(marketContext(m5, m15, "up"))
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
	if !strings.HasPrefix(c.StructuralID, "technique:asia_range:asia_l:") {
		t.Fatalf("structural id = %q", c.StructuralID)
	}
	if c.FormedAt != m5[idx.sweep].Time || c.CreatedAt != m5[idx.shift].Time || c.ExpiresAt != c.CreatedAt+4*3600 {
		t.Fatalf("formed %d created %d expires %d", c.FormedAt, c.CreatedAt, c.ExpiresAt)
	}
	if c.Reaction == nil || c.Reaction.Pattern != "sweep_reclaim" || c.Reaction.ZoneID != c.StructuralID || c.Reaction.ConfirmationBarTime != m5[idx.shift].Time {
		t.Fatalf("reaction = %+v", c.Reaction)
	}
	for _, code := range []string{"m5_asia_range_sweep", "m5_asia_range_reclaim", "m5_asia_range_structure_shift"} {
		if !evidenceHas(c.Evidence, code) {
			t.Errorf("evidence %q missing: %v", code, c.Evidence)
		}
	}
	f := c.DetectorConfluence.Factors
	if !f.HTFAligned || !f.WickRejection || !f.StructuralAgreement || !f.SessionContext || f.DisplacementGrade {
		t.Fatalf("factors = %+v", f)
	}
	if err := quality.Verify(c.Quality.Overall, c.Quality.Components); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"technical_rr", "technical_risk_pips", "asia_range_atr"} {
		if _, ok := c.Quality.Components[key]; !ok {
			t.Errorf("quality component %q missing", key)
		}
	}
}

func TestAcceptanceCandidateCarriesItsOwnEvidenceAndPattern(t *testing.T) {
	m5, m15, _ := bullishAcceptance()
	got := newStrategy(t, map[string]any{"execution_stop_min_pips": 20.0}).Evaluate(marketContext(m5, m15, "up"))
	if len(got) != 1 {
		t.Fatalf("candidates = %d", len(got))
	}
	c := got[0]
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	if c.Reaction.Pattern != "wick_rejection" {
		t.Fatalf("pattern = %q", c.Reaction.Pattern)
	}
	for _, code := range []string{"m15_asia_range_break", "m5_asia_range_acceptance", "m5_asia_range_retest_rejection"} {
		if !evidenceHas(c.Evidence, code) {
			t.Errorf("evidence %q missing: %v", code, c.Evidence)
		}
	}
	if f := c.DetectorConfluence.Factors; f.SessionContext || !f.WickRejection {
		t.Fatalf("factors = %+v", f)
	}
}

// No measured fact, no factor.
func TestEveryConfluenceFactorIsMeasuredNeverDefaulted(t *testing.T) {
	s := newStrategy(t, nil).(*Strategy)
	ctx := &analysiscontext.MarketContext{Symbol: "XAU"}
	if got := s.factors(ctx, Setup{Kind: KindBreakRetest, Direction: market.Buy}); got != (confluence.Factors{}) {
		t.Fatalf("accepted break with no measured fact scored %+v", got)
	}
	// A stop run's session context is the swept finalized edge itself, nothing else is credited.
	want := confluence.Factors{SessionContext: true}
	if got := s.factors(ctx, Setup{Kind: KindSweepReclaim, Direction: market.Buy}); got != want {
		t.Fatalf("stop run with no measured fact scored %+v, want only the swept session edge", got)
	}
}

func TestCandidateIdentityIsStableAcrossFreshEvaluations(t *testing.T) {
	m5, m15, idx := bullishRaid()
	var first string
	for extra := 0; extra <= 2; extra++ {
		bars := append([]market.Candle(nil), m5...)
		for i := 0; i < extra; i++ {
			bars = append(bars, market.Candle{Time: m5[len(m5)-1].Time + int64(i+1)*300, Open: 4107.2, High: 4107.6, Low: 4106.9, Close: 4107.2})
		}
		got := newStrategy(t, nil).Evaluate(marketContext(bars, m15, "up"))
		if len(got) != 1 {
			t.Fatalf("%d extra candles after candle %d: %d candidates", extra, idx.shift, len(got))
		}
		if first == "" {
			first = got[0].ID
		} else if got[0].ID != first {
			t.Fatalf("the same technical event changed identity: %s vs %s", first, got[0].ID)
		}
	}
}

// strategy_refused{strategy="asia_range", reason}: every refused episode is counted once, by its reason, however many closes re-read it.
func TestEveryRefusalIsCountedOnceByItsReason(t *testing.T) {
	m5, m15, _ := bullishRaid()
	s := newStrategy(t, map[string]any{"minimum_range_atr": 4.0, "maximum_range_atr": 8.0}).(*Strategy) // 3.75 ATR: too narrow
	ctx := marketContext(m5, m15, "up")
	for i := 0; i < 3; i++ {
		s.Evaluate(ctx)
	}
	got := s.TakeRefusals()
	if got[strategy.Refusal{Reason: ReasonRangeTooNarrow, Timeframe: market.M5}] != 1 || len(got) != 1 {
		t.Fatalf("refusals = %v, want one range_too_narrow", got)
	}
	if again := s.TakeRefusals(); len(again) != 0 {
		t.Fatalf("the counts must reset: %v", again)
	}
	// a stop above the cap is a refusal too (the stop budget count stays as well)
	capped := newStrategy(t, map[string]any{"execution_stop_max_pips": 10.0, "execution_stop_min_pips": 5.0}).(*Strategy)
	capped.Evaluate(ctx)
	if n := capped.TakeRefusals()[strategy.Refusal{Reason: ReasonRiskExceedsEnvelope, Timeframe: market.M5}]; n != 1 {
		t.Fatalf("risk_exceeds_execution_envelope counted %d times", n)
	}
}

func TestTheStrategyReportsItsRefusalsToTheRegistry(t *testing.T) {
	var _ strategy.RefusalReporter = (*Strategy)(nil)
}
