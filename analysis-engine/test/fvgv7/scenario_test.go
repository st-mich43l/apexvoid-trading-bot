package fvgv7_test

import (
	"math"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/fvg"
)

// A hand-built market in which a bullish M15 gap forms on M5 bars 66-68 and price comes back to it. Everything is derived from the M5 path: the M15 frame
// is the aggregate of complete M15 candles, exactly as the engine builds it, so cutting the path at any M5 bar gives the context of that moment.
//
//	bars 0-59   a flat chop around 210.00 (no gap, no pivot)
//	bars 60-62  candle a (the gap's first): high 210.10
//	bars 63-65  candle b (the displacement): 210.08 -> 210.60
//	bars 66-68  candle c (the third): low 210.42, high 211.00      => gap 210.10-210.42 (1.07 M15 ATR)
//	bars 69-70  the pullback
//	bar 71      the touch of the band
//	bar 72      the rejection: closes above the band with a lower wick
type tick struct{ o, h, l, c float64 }

const (
	m5Seconds = 300
	startTime = 1_792_999_800 // a multiple of 900
	// The path below is drawn in "ticks" of a wider market than GBPJPY's 25-pip stop cap allows; px scales it down around 210.00 (every ratio is kept).
	scale  = 0.55
	m5ATR  = 0.10 * scale
	m15ATR = 0.30 * scale
)

func px(x float64) float64 { return 210.00 + (x-210.00)*scale }

func chop() tick { return tick{210.00, 210.04, 209.96, 210.00} }

func basePath() []tick {
	var path []tick
	for i := 0; i < 60; i++ {
		path = append(path, chop())
	}
	return append(path,
		tick{210.00, 210.05, 209.98, 210.04}, tick{210.04, 210.08, 210.02, 210.07}, tick{210.07, 210.10, 210.05, 210.08}, // a
		tick{210.08, 210.30, 210.07, 210.28}, tick{210.28, 210.50, 210.26, 210.48}, tick{210.48, 210.62, 210.45, 210.60}, // b
		tick{210.60, 210.72, 210.42, 210.70}, tick{210.70, 210.85, 210.66, 210.82}, tick{210.82, 211.00, 210.78, 210.95}, // c
		tick{210.95, 210.96, 210.70, 210.72}, tick{210.72, 210.74, 210.50, 210.52}, // the pullback
		tick{210.52, 210.54, 210.36, 210.40}, // 71: the touch
		tick{210.41, 210.50, 210.30, 210.48}, // 72: the wick rejection
	)
}

func candles(path []tick) []market.Candle {
	out := make([]market.Candle, len(path))
	for i, p := range path {
		out[i] = market.Candle{Time: startTime + int64(i)*m5Seconds, Open: px(p.o), High: px(p.h), Low: px(p.l), Close: px(p.c), Volume: 1}
	}
	return out
}

func aggregate(m5 []market.Candle) []market.Candle {
	var out []market.Candle
	for i := 0; i+3 <= len(m5); i += 3 {
		c := market.Candle{Time: m5[i].Time, Open: m5[i].Open, High: m5[i].High, Low: m5[i].Low, Close: m5[i+2].Close, Volume: 3}
		for _, b := range m5[i : i+3] {
			c.High, c.Low = math.Max(c.High, b.High), math.Min(c.Low, b.Low)
		}
		out = append(out, c)
	}
	return out
}

func frame(bars []market.Candle, atr float64) *analysiscontext.LegacyFrame {
	series := make([]float64, len(bars))
	for i := range series {
		series[i] = atr
	}
	return &analysiscontext.LegacyFrame{Bars: bars, ATR: series, DetectorATR: atr}
}

// contextAt is the market after the closed M5 bar `last` of path.
func contextAt(path []tick, last int, htf string) *analysiscontext.MarketContext {
	m5 := candles(path[:last+1])
	return &analysiscontext.MarketContext{
		Symbol: "GBPJPY",
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
			market.M5:  {Timeframe: market.M5, Legacy: frame(m5, m5ATR)},
			market.M15: {Timeframe: market.M15, Legacy: frame(aggregate(m5), m15ATR)},
		},
		Legacy: &analysiscontext.LegacyRead{LocalStructure: "up", HTFBias: htf},
	}
}

// override changes the strategy's parameters over the engine-resolved GBPJPY ones.
func newStrategy(t testing.TB, override map[string]any) *fvg.Strategy {
	t.Helper()
	doc := resolve(t)
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, "GBPJPY"); err != nil {
		t.Fatal(err)
	}
	var cfg strategy.Config
	for _, c := range settings.Strategies {
		if c.ID == "fvg" {
			cfg = c
		}
	}
	params := map[string]any{}
	for k, v := range cfg.Parameters {
		params[k] = v
	}
	params["structure_timeframes"] = []any{"M15"}
	for k, v := range override {
		params[k] = v
	}
	cfg.Parameters = params
	s, err := fvg.New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	return s.(*fvg.Strategy)
}

func decide(t testing.TB, s *fvg.Strategy, path []tick, last int, htf string) ([]opportunity.Candidate, map[string]int64) {
	t.Helper()
	got := s.Evaluate(contextAt(path, last, htf))
	reasons := map[string]int64{}
	for r, n := range s.TakeRefusals() {
		reasons[r.Reason] += n
	}
	return got, reasons
}

func TestDetectsTheGapAndPublishesTheMeasuredRejection(t *testing.T) {
	got, reasons := decide(t, newStrategy(t, nil), basePath(), 72, "up")
	if len(got) != 1 {
		t.Fatalf("want one candidate, got %d (refusals %v)", len(got), reasons)
	}
	c := got[0]
	if c.Direction != market.Buy || c.Structure.Timeframe != market.M15 || math.Abs(c.Structure.Low-px(210.10)) > 1e-9 || math.Abs(c.Structure.High-px(210.42)) > 1e-9 {
		t.Fatalf("wrong structure: %s", describe(c))
	}
	if math.Abs(c.Entry.Low-px(210.10)) > 1e-9 || math.Abs(c.Entry.High-px(210.42)) > 1e-9 {
		t.Errorf("entry band %v-%v, want the whole gap", c.Entry.Low, c.Entry.High)
	}
	if c.Reaction == nil || c.Reaction.Pattern != "wick_rejection" || c.Reaction.ReactionType != "rejection" {
		t.Errorf("reaction %+v, want a wick_rejection", c.Reaction)
	}
	// The rejecting bar itself trades into the band (its wick reaches 210.30), so it is also the latest touch inside its window.
	if c.Reaction.ConfirmationBarTime != startTime+72*m5Seconds || c.Reaction.TouchBarTime < startTime+71*m5Seconds || c.Reaction.TouchBarTime > c.Reaction.ConfirmationBarTime {
		t.Errorf("touch/confirmation bars %d/%d", c.Reaction.TouchBarTime, c.Reaction.ConfirmationBarTime)
	}
	if want := px(210.10) - 0.5*m5ATR; math.Abs(float64(c.Invalidation.Price)-want) > 1e-9 {
		t.Errorf("stop %v, want %v (beyond the whole gap, plus the buffer)", c.Invalidation.Price, want)
	}
	target := c.Targets[0].Price
	if target.Label != "nearest_opposing_structure" || math.Abs(float64(target.Price)-px(211.00)) > 1e-9 {
		t.Errorf("target %v %q, want the 211.00 swing high", target.Price, target.Label)
	}
	if c.StrategyVersion != "v7" || c.StructuralID == "" {
		t.Errorf("version %q id %q", c.StrategyVersion, c.StructuralID)
	}
}

func TestWhatIsNotAGapIsNotTraded(t *testing.T) {
	cases := []struct {
		name     string
		mutate   func(p []tick)
		override map[string]any
		reason   string
	}{
		{"a gap under minimum_gap_atr", nil, map[string]any{"minimum_gap_atr": 1.2}, "gap_small"},
		{"a weak displacement candle", nil, map[string]any{"minimum_displacement_body_atr": 2.0}, "displacement_weak"},
		{"a middle candle against the gap", func(p []tick) {
			p[63], p[64], p[65] = tick{210.62, 210.70, 210.40, 210.45}, tick{210.45, 210.50, 210.20, 210.25}, tick{210.25, 210.30, 210.12, 210.15}
		}, nil, "displacement_direction"},
		{"a gap an M15 close went through", func(p []tick) {
			p[69] = tick{210.95, 210.96, 209.80, 209.90}
			p[70] = tick{209.90, 210.10, 209.80, 210.00}
			p[71] = tick{210.00, 210.10, 209.90, 209.95}
		}, nil, "gap_closed_through"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			path := basePath()
			if c.mutate != nil {
				c.mutate(path)
			}
			got, reasons := decide(t, newStrategy(t, c.override), path, 72, "up")
			if len(got) != 0 {
				t.Fatalf("published %s", describe(got[0]))
			}
			if reasons[c.reason] == 0 {
				t.Fatalf("want refusal %q, got %v", c.reason, reasons)
			}
		})
	}
}

func TestAConfirmationNeedsATouchAndARejection(t *testing.T) {
	t.Run("a touch without a rejection", func(t *testing.T) {
		path := basePath()
		path[72] = tick{210.41, 210.46, 210.30, 210.34} // closes inside the band, bearish
		got, reasons := decide(t, newStrategy(t, nil), path, 72, "up")
		if len(got) != 0 || reasons["no_rejection"] == 0 {
			t.Fatalf("published %d, refusals %v", len(got), reasons)
		}
	})
	t.Run("a rejection older than confirmation_max_age_bars", func(t *testing.T) {
		path := append(basePath(), tick{210.48, 210.52, 210.46, 210.50}, tick{210.50, 210.54, 210.48, 210.52}, tick{210.52, 210.56, 210.50, 210.54})
		got, reasons := decide(t, newStrategy(t, nil), path, 75, "up")
		if len(got) != 0 || reasons["stale_confirmation"] == 0 {
			t.Fatalf("published %d, refusals %v", len(got), reasons)
		}
	})
	t.Run("a close beyond the far edge is not a touch of the gap", func(t *testing.T) {
		path := basePath()
		path[71] = tick{210.52, 210.54, 209.90, 210.00} // closes through the gap's far edge
		path[72] = tick{210.01, 210.50, 209.98, 210.48}
		got, _ := decide(t, newStrategy(t, nil), path, 72, "up")
		if len(got) != 0 {
			t.Fatalf("published %s", describe(got[0]))
		}
	})
}

func TestAnEntryWithNoRoomIsRefused(t *testing.T) {
	path := basePath()
	// a swing high just above the entry that nothing has closed through: 210.50 is 0.08 over the band high, far less than one risk away
	path[66] = tick{210.60, 210.72, 210.42, 210.70}
	path[69] = tick{210.95, 210.96, 210.70, 210.72}
	got, reasons := decide(t, newStrategy(t, map[string]any{"minimum_target_r": 2.0, "fallback_target_r": 2.0}), path, 72, "up")
	if len(got) != 0 || reasons["no_target_room"] == 0 {
		t.Fatalf("published %d, refusals %v", len(got), reasons)
	}
}

func TestAStretchedLegEntryAtItsExtremeIsRefused(t *testing.T) {
	got, reasons := decide(t, newStrategy(t, map[string]any{"minimum_leg_retracement": 0.9}), basePath(), 72, "up")
	if len(got) != 0 || reasons["entry_at_leg_extreme"] == 0 {
		t.Fatalf("published %d, refusals %v", len(got), reasons)
	}
	got, reasons = decide(t, newStrategy(t, map[string]any{"minimum_leg_retracement": 0.9, "extension_minimum_leg_atr": 6.0}), basePath(), 72, "up")
	if len(got) != 1 {
		t.Fatalf("a leg under extension_minimum_leg_atr is not stretched: published %d, refusals %v", len(got), reasons)
	}
}

func TestConfluenceFactorsAreMeasuredNotAssumed(t *testing.T) {
	s := newStrategy(t, map[string]any{"confluence_floor": 1})
	with, _ := decide(t, s, basePath(), 72, "up")
	without, _ := decide(t, s, basePath(), 72, "down")
	if len(with) != 1 || len(without) != 1 {
		t.Fatalf("published %d / %d", len(with), len(without))
	}
	f, g := with[0].DetectorConfluence.Factors, without[0].DetectorConfluence.Factors
	if !f.HTFAligned || g.HTFAligned {
		t.Errorf("HTFAligned must follow the higher-timeframe bias: up=%v down=%v", f.HTFAligned, g.HTFAligned)
	}
	if !f.WickRejection {
		t.Errorf("the rejecting bar has a lower wick of 55%% of its range: %+v", f)
	}
	if !f.DisplacementGrade { // not an engulfing, but the gap's middle candle is a body of 1.7 structure ATR
		t.Errorf("the middle candle's body is over one structure ATR: %+v", f)
	}
	if f.CHoCH || f.FibTouch {
		t.Errorf("no change of character and no fibonacci level were measured: %+v", f)
	}
	if f.StructuralAgreement {
		t.Errorf("the confirming bar closed at 210.48, under the swing high at 211.00: %+v", f)
	}
}
