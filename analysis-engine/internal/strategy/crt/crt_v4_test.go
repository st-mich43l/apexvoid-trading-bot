package crt_test

// CRT v4 (PR-CRT4) on synthetic XAU bars with the shipped configuration (instrument overrides applied): the reclaim-leg entry, the stop-budget fit, the zone
// minimum, the opposite-edge objective, candle 2's close rule, M15 + H1 anchors, symmetry and causality.

import (
	"math"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/crt"
)

const (
	t0       int64 = 1794960000 // a Monday 00:00 UTC, aligned to H1 and M15
	pip            = 0.1
	h1Bars         = 220
	m5Before       = 200 // M5 bars before the anchor closes
	anchorH        = 3020.0
	anchorL        = 3000.0
)

func xauParams(t *testing.T, overrides map[string]any) map[string]any {
	t.Helper()
	doc, err := config.ResolveDocument("../../../../config/apexvoid.yml")
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, "XAU"); err != nil {
		t.Fatal(err)
	}
	for _, s := range settings.Strategies {
		if s.ID == crt.ID {
			p := map[string]any{}
			for k, v := range s.Parameters {
				p[k] = v
			}
			for k, v := range overrides {
				p[k] = v
			}
			return p
		}
	}
	t.Fatal("crt is not configured")
	return nil
}

func bar(t int64, o, h, l, c float64) market.Candle {
	return market.Candle{Time: t, Open: o, High: h, Low: l, Close: c, Volume: 1}
}

// episode describes one bullish CRT after an anchor of frame tf closing at anchorClose: a descent with an M5 swing high at pivot (the structure the shift
// breaks), filler bars, the sweep to sweepLow, a reclaim, and the structure shift closing at mss.
type episode struct {
	tf        market.Timeframe
	pivot     float64
	mss       float64
	sweepLow  float64
	fillers   int     // M5 bars between the pivot sequence and the sweep
	candle2C  float64 // the close of candle 2 on the anchor frame (0: candle 2 not in the series)
	trailing  int     // M5 bars after the shift (confirmation age)
	preInside bool    // the drop and the swing high are the last M5 bars before the anchor closes (an M15 candle 2 is only 3 M5 bars long)
}

type series struct {
	h1, m15, m5 []market.Candle
	anchorClose int64
}

func build(e episode) series {
	var s series
	period := int64(3600)
	if e.tf == market.M15 {
		period = 900
	}
	// Background: the anchor frame closes flat at 3010 with a 5.00 range (ATR 5), M5 with a 1.00 range. The anchor is the last closed candle before the pattern.
	anchorOpen := t0 + int64(h1Bars)*3600 - period
	s.anchorClose = anchorOpen + period
	for i := 0; i < h1Bars; i++ {
		ts := t0 + int64(i)*3600
		s.h1 = append(s.h1, bar(ts, 3010, 3012.5, 3007.5, 3010))
	}
	for ts := t0; ts < s.anchorClose; ts += 900 {
		s.m15 = append(s.m15, bar(ts, 3010, 3011.5, 3008.5, 3010))
	}
	anchor := bar(anchorOpen, 3010, anchorH, anchorL, 3010)
	if e.tf == market.M15 {
		s.m15[len(s.m15)-1] = anchor
	} else {
		s.h1[len(s.h1)-1] = anchor
	}
	start := s.anchorClose - int64(m5Before)*300
	for ts := start; ts < s.anchorClose; ts += 300 {
		s.m5 = append(s.m5, bar(ts, 3010, 3010.5, 3009.5, 3010))
	}
	at := s.anchorClose
	if e.preInside {
		s.m5 = s.m5[:len(s.m5)-6]
		at = s.anchorClose - 6*300
	}
	add := func(o, h, l, c float64) {
		s.m5 = append(s.m5, bar(at, o, h, l, c))
		at += 300
	}
	p := e.pivot
	add(3010, 3010.2, 3000.6, 3000.9) // the drop into the range
	add(3000.9, p-0.6, 3000.6, 3000.8)
	add(3000.8, p-0.4, 3000.6, 3000.9)
	add(3000.9, p, 3000.7, 3001.0) // the swing high the shift breaks
	add(3001.0, p-0.3, 3000.6, 3000.8)
	add(3000.8, p-0.5, 3000.5, 3000.7)
	for i := 0; i < e.fillers; i++ {
		add(3000.7, 3000.9, 3000.5, 3000.7)
	}
	add(3000.7, 3000.9, e.sweepLow, 2999.6) // sweep
	add(2999.6, 3000.8, 2999.5, 3000.5)     // reclaim
	add(3000.5, e.mss+0.1, 3000.4, e.mss)   // structure shift
	for i := 0; i < e.trailing; i++ {
		add(e.mss, e.mss+0.2, e.mss-0.2, e.mss)
	}
	if e.candle2C != 0 {
		c2 := bar(s.anchorClose, 3010, 3010.2, e.sweepLow, e.candle2C)
		if e.tf == market.M15 {
			s.m15 = append(s.m15, c2)
		} else {
			s.h1 = append(s.h1, c2)
		}
	}
	return s
}

func detect(t *testing.T, params map[string]any, s series, tf market.Timeframe) crt.Analysis {
	t.Helper()
	cfg, err := crt.ParseConfig(params)
	if err != nil {
		t.Fatal(err)
	}
	anchors := s.h1
	if tf == market.M15 {
		anchors = s.m15
	}
	return crt.Detect(cfg, crt.Input{H1: anchors, AnchorTF: tf, M5: s.m5})
}

func reasons(a crt.Analysis) string {
	var out []string
	for _, r := range a.Rejections {
		out = append(out, r.Reason)
	}
	return strings.Join(out, ",")
}

func near(a, b float64) bool { return math.Abs(a-b) < 1e-6 }

func basic() episode {
	return episode{tf: market.H1, pivot: 3003.5, mss: 3004.0, sweepLow: 2999.3, fillers: 2}
}

// A 40-pip reclaim leg publishes with the zone [swept edge, shift close], the stop beyond the manipulation extreme and the opposite edge as the objective.
func TestCRT4ReclaimLegPublishesTheLegAsTheZone(t *testing.T) {
	a := detect(t, xauParams(t, nil), build(basic()), market.H1)
	if len(a.Setups) != 1 {
		t.Fatalf("one setup, got %d (%s)", len(a.Setups), reasons(a))
	}
	s := a.Setups[0]
	if s.Direction != market.Buy || !near(s.EntryLow, anchorL) || !near(s.EntryHigh, 3004.0) {
		t.Fatalf("zone [%v, %v], want [3000, 3004]", s.EntryLow, s.EntryHigh)
	}
	if !(s.Stop < 2999.3) || (s.EntryHigh-s.Stop)/pip > 70 {
		t.Fatalf("stop %v beyond the extreme 2999.3 and inside the cap", s.Stop)
	}
	if !near(s.Target, anchorH) || s.TargetKind != "opposite_h1_range" {
		t.Fatalf("the objective is the opposite edge: %v %s", s.Target, s.TargetKind)
	}
}

// A 20-pip leg is refused by CRT itself (the XAU minimum entry zone is 30 pips), never stretched.
func TestCRT4ThinLegIsRefusedNotStretched(t *testing.T) {
	e := basic()
	e.pivot, e.mss = 3001.6, 3002.0
	a := detect(t, xauParams(t, nil), build(e), market.H1)
	if len(a.Setups) != 0 || !strings.Contains(reasons(a), crt.ReasonEntryZoneBelowMinimumWidth) {
		t.Fatalf("refused %s, got %d setups (%s)", crt.ReasonEntryZoneBelowMinimumWidth, len(a.Setups), reasons(a))
	}
}

// A leg whose risk from the proximal edge exceeds the 70-pip cap is cut toward the stop (deep entry, the stop never moves); a cut band under 30 pips is refused.
func TestCRT4WideLegIsCutToTheCapOrRefused(t *testing.T) {
	e := basic()
	e.pivot, e.mss = 3008.0, 3009.0
	a := detect(t, xauParams(t, nil), build(e), market.H1)
	if len(a.Setups) != 1 {
		t.Fatalf("one setup, got %d (%s)", len(a.Setups), reasons(a))
	}
	s := a.Setups[0]
	risk := (s.EntryHigh - s.Stop) / pip
	// the fit cuts the 90-pip leg toward the stop to the 70-pip cap, then the 50-pip maximum width (entry_max_width_price) keeps the part nearest the new
	// proximal edge
	if !(s.EntryHigh < 3009.0) || risk > 70+1e-6 || risk < 69.9 || !near(s.EntryHigh-s.EntryLow, 5.0) || s.StopBudgetCode != "stop_budget_deep_entry" {
		t.Fatalf("cut to the cap: zone [%v, %v] risk %.2f code %q", s.EntryLow, s.EntryHigh, risk, s.StopBudgetCode)
	}
	if (s.EntryHigh-s.EntryLow)/pip < 30 {
		t.Fatalf("the cut band keeps at least 30 pips: %.1f", (s.EntryHigh-s.EntryLow)/pip)
	}
	e.sweepLow = 2996.0 // a deeper manipulation: the cut band would be about 26 pips
	a = detect(t, xauParams(t, nil), build(e), market.H1)
	if len(a.Setups) != 0 || !strings.Contains(reasons(a), crt.ReasonRiskExceedsEnvelope) {
		t.Fatalf("a cut band under 30 pips is refused: %d (%s)", len(a.Setups), reasons(a))
	}
}

// The objective is the opposite edge even with a nearer unswept swing in the way (published as the obstacle), and the RR is judged on the effective risk.
func TestCRT4ObjectiveIsTheOppositeEdgeOnTheEffectiveRisk(t *testing.T) {
	s := build(basic())
	// an unswept swing high at 3012 inside the range, confirmed before the episode, above the entry
	i := len(s.m5) - 30
	for k := i - 2; k <= i+2; k++ {
		s.m5[k] = bar(s.m5[k].Time, 3010, 3010.5, 3009.5, 3010)
	}
	s.m5[i] = bar(s.m5[i].Time, 3010, 3012.0, 3009.5, 3010)
	a := detect(t, xauParams(t, nil), s, market.H1)
	if len(a.Setups) != 1 {
		t.Fatalf("one setup, got %d (%s)", len(a.Setups), reasons(a))
	}
	got := a.Setups[0]
	if !near(got.Target, anchorH) || !got.HasObstacle || !near(got.NearestObstacle, 3012.0) {
		t.Fatalf("target %v obstacle %v %v", got.Target, got.HasObstacle, got.NearestObstacle)
	}
	floor := 50.0 // XAU execution_stop_min_pips
	want := math.Max((got.EntryHigh-got.Stop)/pip, floor)
	if !near(got.EffectiveRiskPips, want) || !near(got.TechnicalRR, (got.Target-got.EntryHigh)/(want*pip)) {
		t.Fatalf("effective risk %v (want %v), rr %v", got.EffectiveRiskPips, want, got.TechnicalRR)
	}
	// a minimum RR above what the opposite edge pays on the effective risk refuses it
	a = detect(t, xauParams(t, map[string]any{"minimum_reward_risk": got.TechnicalRR + 0.01}), s, market.H1)
	if len(a.Setups) != 0 || !strings.Contains(reasons(a), crt.ReasonRewardRiskBelowMinimum) {
		t.Fatalf("RR on the effective risk: %d (%s)", len(a.Setups), reasons(a))
	}
}

// Candle 2 closing beyond the swept edge is a failed CRT: refused before publication; once published, a close of the anchor frame beyond the swept edge
// withdraws the opportunity (the published structure and the engine's structure-close rule).
func TestCRT4Candle2CloseRule(t *testing.T) {
	e := basic()
	e.fillers = 5 // the sweep is the last M5 bar of candle 2, the shift is in candle 3
	e.candle2C = 2999.6
	a := detect(t, xauParams(t, nil), build(e), market.H1)
	if len(a.Setups) != 0 || !strings.Contains(reasons(a), crt.ReasonCandle2ClosedOutside) {
		t.Fatalf("candle 2 closed outside: %d (%s)", len(a.Setups), reasons(a))
	}
	e.candle2C = 3000.5
	a = detect(t, xauParams(t, nil), build(e), market.H1)
	if len(a.Setups) != 1 || !a.Setups[0].Candle2ClosedInside {
		t.Fatalf("candle 2 closed inside: %d (%s)", len(a.Setups), reasons(a))
	}

	// After publication: the structure is the anchor range on H1; an H1 close below its low retires the opportunity.
	_, candidates := analyze(t, xauParams(t, nil), build(basic()))
	if len(candidates) != 1 {
		t.Fatalf("one candidate, got %d", len(candidates))
	}
	c := candidates[0]
	if c.Structure == nil || c.Structure.Timeframe != market.H1 || !near(c.Structure.Low, anchorL) || !near(c.Structure.High, anchorH) || c.StructureTimeframe != market.H1 {
		t.Fatalf("published structure %+v", c.Structure)
	}
	book := opportunity.NewBook()
	if _, err := book.Observe(c, c.CreatedAt); err != nil {
		t.Fatal(err)
	}
	none := func(opportunity.StrategyID) (float64, bool) { return 0, true }
	if got := book.InvalidateByStructureClose(market.H1, anchorL+0.01, c.CreatedAt+3600, none); len(got) != 0 {
		t.Fatal("a close inside the range keeps it")
	}
	if got := book.InvalidateByStructureClose(market.M5, anchorL-1, c.CreatedAt+300, none); len(got) != 0 {
		t.Fatal("only a close of the anchor frame counts")
	}
	got := book.InvalidateByStructureClose(market.H1, anchorL-0.01, c.CreatedAt+3600, none)
	if len(got) != 1 || got[0].Record.Terminal == nil || got[0].Record.Terminal.Reason != opportunity.ReasonStructureClosedThrough {
		t.Fatalf("an H1 close below the swept edge withdraws it: %+v", got)
	}
}

func analyze(t *testing.T, params map[string]any, s series) (crt.Analysis, []opportunity.Candidate) {
	t.Helper()
	st, err := crt.New(strategy.Config{ID: crt.ID, Version: crt.Version, Parameters: params})
	if err != nil {
		t.Fatal(err)
	}
	ctx := &analysiscontext.MarketContext{Symbol: "XAU", Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
		market.M5: {Timeframe: market.M5, Candles: s.m5}, market.M15: {Timeframe: market.M15, Candles: s.m15}, market.H1: {Timeframe: market.H1, Candles: s.h1},
	}}
	return st.(*crt.Strategy).Analyze(ctx)
}

func evidenceHas(c opportunity.Candidate, code string) bool {
	for _, e := range c.Evidence {
		if e.Code == code {
			return true
		}
	}
	return false
}

// An M15 anchor publishes on its own; when an M15 and an H1 anchor are confirmed by the same structure shift, the H1 thesis is kept.
func TestCRT4AnchorsOnM15AndH1(t *testing.T) {
	e := basic()
	e.tf = market.M15
	e.fillers, e.preInside = 0, true
	_, candidates := analyze(t, xauParams(t, nil), build(e))
	if len(candidates) != 1 || !evidenceHas(candidates[0], "crt_anchor_tf_m15") || candidates[0].Structure.Timeframe != market.M15 {
		t.Fatalf("an M15 anchor publishes: %d", len(candidates))
	}
	// The expiry of an M15 anchor ends 3 M15 periods after candle 2 closes.
	if want := build(e).anchorClose + 900 + 3*900; candidates[0].ExpiresAt != want {
		t.Fatalf("M15 expiry %d, want %d", candidates[0].ExpiresAt, want)
	}

	both := build(e)
	both.h1[len(both.h1)-1] = bar(both.anchorClose-3600, 3010, anchorH, anchorL, 3010) // the H1 candle of the same hour has the same range
	analysis, candidates := analyze(t, xauParams(t, nil), both)
	if len(candidates) != 1 || !evidenceHas(candidates[0], "crt_anchor_tf_h1") {
		t.Fatalf("the H1 thesis is kept: %d %v", len(candidates), candidates)
	}
	if !strings.Contains(reasons(analysis), crt.ReasonDuplicateThesis) {
		t.Fatalf("the M15 one is counted duplicate_thesis: %s", reasons(analysis))
	}
}

func TestCRT4M5IsNotAnAnchorFrame(t *testing.T) {
	if _, err := crt.ParseConfig(xauParams(t, map[string]any{"structure_timeframes": []any{"M5", "H1"}})); err == nil {
		t.Fatal("M5 in structure_timeframes must fail the configuration")
	}
	if _, err := crt.ParseConfig(xauParams(t, map[string]any{"structure_timeframes": []any{"M1"}})); err == nil {
		t.Fatal("M1 is execution only")
	}
}

// v3 is selectable: entry_model reclaim_retest with H1 only gives the v3 band centred on the swept edge (0.2 M5 ATR wide), and on XAU that band can never
// reach the 30-pip minimum, which is the defect v4 fixes.
func TestCRT4ReclaimRetestIsV3(t *testing.T) {
	a := detect(t, xauParams(t, map[string]any{"entry_model": "reclaim_retest", "structure_timeframes": []any{"H1"}}), build(basic()), market.H1)
	if len(a.Setups) != 1 {
		t.Fatalf("v3 setup: %d (%s)", len(a.Setups), reasons(a))
	}
	s := a.Setups[0]
	if !(s.EntryLow < anchorL && s.EntryHigh > anchorL) || (s.EntryHigh-s.EntryLow)/pip > 10 || s.Candle2Close != 0 {
		t.Fatalf("the v3 band straddles the edge and is a few pips wide: [%v, %v]", s.EntryLow, s.EntryHigh)
	}
}

// BUY and SELL are mirrors: the bars reflected about 6000 give the mirrored SELL setup.
func TestCRT4MirroredBuyAndSell(t *testing.T) {
	s := build(basic())
	mirror := func(bars []market.Candle) []market.Candle {
		out := make([]market.Candle, len(bars))
		for i, b := range bars {
			out[i] = bar(b.Time, 6000-b.Open, 6000-b.Low, 6000-b.High, 6000-b.Close)
		}
		return out
	}
	m := series{h1: mirror(s.h1), m15: mirror(s.m15), m5: mirror(s.m5), anchorClose: s.anchorClose}
	a, b := detect(t, xauParams(t, nil), s, market.H1), detect(t, xauParams(t, nil), m, market.H1)
	if len(a.Setups) != 1 || len(b.Setups) != 1 || b.Setups[0].Direction != market.Sell {
		t.Fatalf("%d / %d", len(a.Setups), len(b.Setups))
	}
	x, y := a.Setups[0], b.Setups[0]
	if !near(y.EntryLow, 6000-x.EntryHigh) || !near(y.EntryHigh, 6000-x.EntryLow) || !near(y.Stop, 6000-x.Stop) || !near(y.Target, 6000-x.Target) {
		t.Fatalf("mirror [%v %v] stop %v target %v vs [%v %v] %v %v", y.EntryLow, y.EntryHigh, y.Stop, y.Target, x.EntryLow, x.EntryHigh, x.Stop, x.Target)
	}
}

// Causality: the decision at the shift bar is the same with the bars after it present (DetectAsOf) and absent.
func TestCRT4TruncatedHistoryIsIdentical(t *testing.T) {
	e := basic()
	e.trailing = 6
	full := build(e)
	cfg, err := crt.ParseConfig(xauParams(t, nil))
	if err != nil {
		t.Fatal(err)
	}
	asOf := len(full.m5) - 1 - e.trailing
	a := crt.DetectAsOf(cfg, crt.Input{H1: full.h1, AnchorTF: market.H1, M5: full.m5}, asOf)
	b := crt.Detect(cfg, crt.Input{H1: full.h1, AnchorTF: market.H1, M5: full.m5[:asOf+1]})
	if len(a.Setups) != 1 || len(b.Setups) != 1 || a.Setups[0].EntryLow != b.Setups[0].EntryLow || a.Setups[0].EntryHigh != b.Setups[0].EntryHigh || a.Setups[0].Stop != b.Setups[0].Stop {
		t.Fatalf("as-of decision differs: %+v vs %+v", a.Setups, b.Setups)
	}
}
