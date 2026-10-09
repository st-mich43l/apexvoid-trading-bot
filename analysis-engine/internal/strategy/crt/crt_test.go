package crt

import (
	"strings"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

// crtParams is the strategy's own parameter set as config/analysis.yml
// declares it (XAU scale), merged with the shared detector contract the engine
// injects.
func crtParams(override map[string]any) map[string]any {
	own := map[string]any{
		"minimum_h1_range_atr": 1.5, "atr_length": 14.0, "atr_window_bars": 140.0, "sweep_window_h1_periods": 1.0,
		"minimum_sweep_pips": 2.0, "minimum_sweep_atr": 0.10, "minimum_reclaim_pips": 1.0, "minimum_reclaim_atr": 0.05, "reclaim_max_bars": 6.0,
		"confirmation_mode": "mss", "structure_pivot_bars": 2.0, "structure_lookback_bars": 24.0, "mss_max_bars": 12.0,
		"minimum_mss_body_ratio": 0.5, "minimum_mss_displacement_atr": 0.5, "minimum_mss_close_strength": 0.6, "displacement_grade_atr": 1.0,
		"confirmation_max_age_bars": 2.0, "confirmation_buffer_pips": 0.0,
		"entry_model": "mss_retest", "entry_depth_atr": 0.5, "entry_tolerance_atr": 0.1, "entry_max_width_price": 5.0,
		"invalidation_buffer_atr": 0.25, "minimum_target_room_atr": 0.55, "minimum_reward_risk": 1.15, "execution_stop_max_pips": 120.0,
		"expiry_hours": 4.0,
	}
	for k, v := range override {
		own[k] = v
	}
	return legacyfixture.Params(own)
}

func newStrategy(t *testing.T, override map[string]any) strategy.Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: crtParams(override)})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// marketContext is a context carrying the closed H1 and M5 candles and an
// HTF bias, the way the engine hands them to a strategy.
func marketContext(h1, m5 []market.Candle, htf string) *analysiscontext.MarketContext {
	return &analysiscontext.MarketContext{
		Symbol: "XAU",
		Legacy: &analysiscontext.LegacyRead{LocalStructure: "up", HTFBias: htf, AllowCounterTrend: true},
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
			market.M5: {Timeframe: market.M5, Candles: m5},
			market.H1: {Timeframe: market.H1, Candles: h1},
		},
	}
}

func TestCandidateCarriesTheTechnicalContractAndPassesValidation(t *testing.T) {
	m5, h1, idx := bullishCRT()
	s := newStrategy(t, nil)
	got := s.Evaluate(marketContext(h1, m5, "up"))
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
	if c.StructuralID != "technique:crt:buy:"+itoa(fixtureHour) {
		t.Fatalf("structural id = %q", c.StructuralID)
	}
	// Timing: formed at the sweep candle, actionable at the confirming candle.
	if c.FormedAt != m5[idx.sweep].Time || c.CreatedAt != m5[idx.shift].Time || c.ExpiresAt != c.CreatedAt+4*3600 {
		t.Fatalf("formed %d created %d expires %d", c.FormedAt, c.CreatedAt, c.ExpiresAt)
	}
	// Geometry: stop beyond the sweep extreme, objective the opposite H1 edge.
	if !(float64(c.Invalidation.Price) < 4098.6) || c.Invalidation.Label != "crt_sweep_extreme_lost" {
		t.Fatalf("invalidation = %+v", c.Invalidation)
	}
	if len(c.Targets) != 1 || float64(c.Targets[0].Price.Price) != 4118 || c.Targets[0].Price.Label != "opposite_h1_range" {
		t.Fatalf("targets = %+v", c.Targets)
	}
	// The Algo Bot's CRT scope: a confirmed reaction and its own evidence prefixes.
	if c.Reaction == nil || c.Reaction.ReactionType != "rejection" || c.Reaction.Pattern != "sweep_reclaim" ||
		c.Reaction.TouchBarTime != m5[idx.sweep].Time || c.Reaction.ConfirmationBarTime != m5[idx.shift].Time || c.Reaction.ZoneID != c.StructuralID {
		t.Fatalf("reaction = %+v", c.Reaction)
	}
	codes := map[string]bool{}
	for _, e := range c.Evidence {
		codes[e.Code] = true
	}
	if !codes["h1_impulse_range"] || !codes["m5_range_sweep_reclaim"] || !codes["m5_crt_structure_shift"] {
		t.Fatalf("evidence = %v", codes)
	}
	// Confluence: HTF aligned (4) + wick (3) + structure (3) + displacement (3).
	if c.DetectorConfluence == nil || c.DetectorConfluence.SelectedStars != 3 || c.Quality.Overall != 1 {
		t.Fatalf("confluence = %+v quality = %+v", c.DetectorConfluence, c.Quality)
	}
	f := c.DetectorConfluence.Factors
	if !f.HTFAligned || !f.WickRejection || !f.StructuralAgreement || !f.DisplacementGrade || f.CHoCH || f.FibTouch {
		t.Fatalf("factors = %+v", f)
	}
	for _, key := range []string{"h1_range_atr", "sweep_depth_atr", "reclaim_depth_atr", "mss_displacement_atr", "mss_body_ratio", "mss_close_strength", "technical_rr", "technical_risk_pips"} {
		if _, ok := c.Quality.Components[key]; !ok {
			t.Errorf("quality component %q missing", key)
		}
	}
}

func TestMeasuredFactorsDecideTheStars(t *testing.T) {
	m5, h1, _ := bullishCRT()
	// Without HTF alignment: wick 3 + structure 3 + displacement 3 = 9 -> 2 stars.
	two := newStrategy(t, nil).Evaluate(marketContext(h1, m5, "down"))
	if len(two) != 1 || two[0].DetectorConfluence.SelectedStars != 2 || two[0].DetectorConfluence.Factors.HTFAligned {
		t.Fatalf("unaligned: %+v", two)
	}
	if want := 2.0 / 3.0; two[0].Quality.Overall < want-1e-9 || two[0].Quality.Overall > want+1e-9 {
		t.Fatalf("overall = %v, want 2/3", two[0].Quality.Overall)
	}
	// A confluence floor of 3 refuses the unaligned setup, with its reason.
	strict := newStrategy(t, map[string]any{"confluence_floor": 3.0}).(*Strategy)
	analysis, candidates := strict.Analyze(marketContext(h1, m5, "down"))
	if len(candidates) != 0 || len(analysis.Setups) != 0 || !hasRejection(analysis, ReasonConfluenceBelowFloor) {
		t.Fatalf("floor 3: candidates %d analysis %+v", len(candidates), analysis)
	}
}

func TestCandidateIdentityIsStableAcrossFreshEvaluationsAndDistinctForNewEvents(t *testing.T) {
	m5, h1, idx := bullishCRT()
	s := &scribe{t: m5[len(m5)-1].Time + 300, last: m5[len(m5)-1].Close, bars: append([]market.Candle(nil), m5...)}
	s.bar(4107.0, 0.3, 0.3)
	s.bar(4107.4, 0.3, 0.3)
	strategy := newStrategy(t, nil)
	var first string
	for asOf := idx.shift; asOf <= idx.shift+2; asOf++ {
		got := strategy.Evaluate(marketContext(h1, s.bars[:asOf+1], "up"))
		if len(got) != 1 {
			t.Fatalf("asOf %d: %d candidates", asOf, len(got))
		}
		if first == "" {
			first = got[0].ID
		} else if got[0].ID != first {
			t.Fatalf("the same technical event changed identity: %s vs %s", first, got[0].ID)
		}
	}
	// A different sweep (shift the whole episode one hour later: a new anchor,
	// a new sweep and confirmation time) is a genuinely new event.
	shifted := make([]market.Candle, len(m5))
	for i, c := range m5 {
		c.Time += 3600
		shifted[i] = c
	}
	h1Shifted := h1Series(fixtureHour+3600, 40, 4118, 4100)
	other := strategy.Evaluate(marketContext(h1Shifted, shifted, "up"))
	if len(other) != 1 || other[0].ID == first {
		t.Fatalf("a new episode must have a new identity: %+v", other)
	}
}

func TestEvaluateWithoutH1OrM5ProducesNothing(t *testing.T) {
	m5, h1, _ := bullishCRT()
	s := newStrategy(t, nil)
	noH1 := marketContext(h1, m5, "up")
	delete(noH1.Timeframes, market.H1)
	if got := s.Evaluate(noH1); len(got) != 0 {
		t.Fatalf("no H1 anchor must produce nothing: %+v", got)
	}
	if got := s.Evaluate(nil); len(got) != 0 {
		t.Fatalf("nil context: %+v", got)
	}
	if got := s.Evaluate(marketContext(h1, nil, "up")); len(got) != 0 {
		t.Fatalf("no M5: %+v", got)
	}
}

func TestConfigRejectsInvalidAndMissingParameters(t *testing.T) {
	bad := map[string]map[string]any{
		"sweep needs a minimum":    {"minimum_sweep_pips": 0.0, "minimum_sweep_atr": 0.0},
		"unknown confirmation":     {"confirmation_mode": "wick"},
		"unknown entry model":      {"entry_model": "market"},
		"mss entry needs mss":      {"confirmation_mode": "sweep_reclaim", "entry_model": "mss_retest"},
		"window beyond bound":      {"sweep_window_h1_periods": 4.0},
		"atr window too short":     {"atr_window_bars": 10.0},
		"negative stop cap":        {"execution_stop_max_pips": -1.0},
		"zero reward risk":         {"minimum_reward_risk": 0.0},
		"body ratio above one":     {"minimum_mss_body_ratio": 1.5},
		"lookback below the pivot": {"structure_lookback_bars": 2.0},
	}
	for name, override := range bad {
		if _, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: crtParams(override)}); err == nil {
			t.Errorf("%s: construction succeeded", name)
		}
	}
	// A missing coefficient fails closed instead of trading a guessed value.
	params := crtParams(nil)
	delete(params, "minimum_sweep_atr")
	if _, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: params}); err == nil || !strings.Contains(err.Error(), "minimum_sweep_atr") {
		t.Fatalf("missing parameter error = %v", err)
	}
	// The stop cap is optional: absent means the pre-check is off.
	params = crtParams(nil)
	delete(params, "execution_stop_max_pips")
	if _, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: params}); err != nil {
		t.Fatalf("absent stop cap must be accepted: %v", err)
	}
	if _, err := New(strategy.Config{ID: "other", Version: Version, Parameters: crtParams(nil)}); err == nil {
		t.Fatal("wrong ID accepted")
	}
}

var _ = opportunity.Candidate{}

func itoa(v int64) string {
	const digits = "0123456789"
	if v == 0 {
		return "0"
	}
	var out []byte
	for v > 0 {
		out = append([]byte{digits[v%10]}, out...)
		v /= 10
	}
	return string(out)
}
