package roundnumber

import (
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

func newStrategy(t *testing.T, override map[string]any) strategy.Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: roundParams(override)})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// marketContext is a context carrying the closed M5 candles and an HTF bias, the way the engine hands them to a strategy.
func marketContext(m5 []market.Candle, htf string) *analysiscontext.MarketContext {
	return &analysiscontext.MarketContext{
		Symbol: "XAU",
		Legacy: &analysiscontext.LegacyRead{LocalStructure: "up", HTFBias: htf, AllowCounterTrend: true},
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
			market.M5: {Timeframe: market.M5, Candles: m5},
		},
	}
}

func evidenceHas(list []opportunity.Evidence, code string) bool {
	for _, e := range list {
		if e.Code == code {
			return true
		}
	}
	return false
}

func TestCandidateCarriesTheTechnicalContractAndPassesValidation(t *testing.T) {
	bars, idx := bullishStopRun()
	got := newStrategy(t, nil).Evaluate(marketContext(bars, "up"))
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
	if !strings.HasPrefix(c.StructuralID, "technique:round_number:buy:4110.00:") {
		t.Fatalf("structural id = %q", c.StructuralID)
	}
	if c.FormedAt != bars[idx.sweep].Time || c.CreatedAt != bars[idx.shift].Time || c.ExpiresAt != c.CreatedAt+2*3600 {
		t.Fatalf("formed %d created %d expires %d", c.FormedAt, c.CreatedAt, c.ExpiresAt)
	}
	if c.Reaction == nil || c.Reaction.ReactionType != "rejection" || c.Reaction.Pattern != "sweep_reclaim" ||
		c.Reaction.TouchBarTime != bars[idx.sweep].Time || c.Reaction.ConfirmationBarTime != bars[idx.shift].Time || c.Reaction.ZoneID != c.StructuralID {
		t.Fatalf("reaction = %+v", c.Reaction)
	}
	for _, code := range []string{"m5_round_number_sweep", "m5_round_number_reclaim", "m5_round_number_structure_shift"} {
		if !evidenceHas(c.Evidence, code) {
			t.Errorf("evidence %q missing: %v", code, c.Evidence)
		}
	}
	if c.DetectorConfluence == nil || c.DetectorConfluence.SelectedStars < 2 {
		t.Fatalf("confluence = %+v", c.DetectorConfluence)
	}
	f := c.DetectorConfluence.Factors
	if !f.HTFAligned || !f.WickRejection || !f.StructuralAgreement || f.DisplacementGrade || f.SessionContext {
		t.Fatalf("factors = %+v", f)
	}
	if err := quality.Verify(c.Quality.Overall, c.Quality.Components); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"technical_rr", "technical_risk_pips"} {
		if _, ok := c.Quality.Components[key]; !ok {
			t.Errorf("quality component %q missing", key)
		}
	}
}

func TestBreakRetestCandidateCarriesItsOwnEvidenceAndPattern(t *testing.T) {
	bars, _ := bullishBreakRetest()
	got := newStrategy(t, map[string]any{"execution_stop_min_pips": 20.0}).Evaluate(marketContext(bars, "up"))
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
	for _, code := range []string{"m5_round_number_break", "m5_round_number_acceptance", "m5_round_number_retest_rejection"} {
		if !evidenceHas(c.Evidence, code) {
			t.Errorf("evidence %q missing: %v", code, c.Evidence)
		}
	}
	f := c.DetectorConfluence.Factors
	if !f.DisplacementGrade || !f.WickRejection || f.StructuralAgreement {
		t.Fatalf("factors = %+v", f)
	}
}

// No measured fact, no factor: a setup whose facts are all false scores no factor (the detectors once hardcoded wick_rejection and
// structural_agreement to true).
func TestEveryConfluenceFactorIsMeasuredNeverDefaulted(t *testing.T) {
	s := newStrategy(t, nil).(*Strategy)
	ctx := &analysiscontext.MarketContext{Symbol: "XAU"}
	if got := s.factors(ctx, Setup{Kind: KindSweepReclaim, Direction: market.Buy}); got != (confluence.Factors{}) {
		t.Fatalf("stop run with no measured fact scored %+v", got)
	}
	if got := s.factors(ctx, Setup{Kind: KindBreakRetest, Direction: market.Sell}); got != (confluence.Factors{}) {
		t.Fatalf("accepted break with no measured fact scored %+v", got)
	}
}

func TestCandidateIdentityIsStableWhileFreshAndDistinctForANewEvent(t *testing.T) {
	bars, idx := bullishStopRun()
	var first string
	for asOf := idx.shift; asOf <= idx.shift+2; asOf++ {
		extended := append(append([]market.Candle(nil), bars...), padFuture(bars, asOf-idx.shift)...)
		got := newStrategy(t, nil).Evaluate(marketContext(extended, "up"))
		if len(got) != 1 {
			t.Fatalf("asOf %d: %d candidates", asOf, len(got))
		}
		if first == "" {
			first = got[0].ID
		} else if got[0].ID != first {
			t.Fatalf("the same technical event changed identity: %s vs %s", first, got[0].ID)
		}
	}
}

// padFuture appends n quiet candles above the structure shift.
func padFuture(bars []market.Candle, n int) []market.Candle {
	var out []market.Candle
	last := bars[len(bars)-1]
	for i := 0; i < n; i++ {
		t := last.Time + int64(i+1)*300
		out = append(out, market.Candle{Time: t, Open: 4115.6, High: 4116.0, Low: 4115.2, Close: 4115.6})
	}
	return out
}
