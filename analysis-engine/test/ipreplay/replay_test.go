// Package ipreplay_test measures Impulse Pullback Scalp (v3) on real XAU bars. Its parity with the frozen Python lane was proven on synthetic M1 only; this replays
// the committed real M1 captures (those with M1 bars) through the engine and reports, per parameter variant, what the strategy publishes and what a resting limit order at the proximal
// edge of each entry band would have done on the following M1 bars.
//
//	IP_REPLAY_OUT=/tmp/ip.json go test ./test/ipreplay -run TestImpulsePullbackOnRealM1 -v
//
// What this is and is not. The counts are publish counts and SIMULATED outcomes on closed bars, never P&L and never evidence of an edge:
//
//   - a fill needs the ask to reach the limit: a BUY limit at Entry.High fills when low + spread <= limit, a SELL limit at Entry.Low when high >= limit
//     (bars are bid prices; the spread is charged on the side that crosses it);
//   - the stop is the opportunity's invalidation price, hit when the bid (BUY) or the ask (SELL) trades through it; the engine invalidates a setup on an M1
//     close beyond that price, so a candidate whose M1 close breaks it before a fill is counted as invalidated before any fill;
//   - the ladder is the adapter's: 1R and 2R when the last target offers at least 2R, 1R only when it offers at least 1R;
//   - when one bar reaches the stop and a target, the stop is taken first; a fill is followed for at most followBars M1 bars after the fill bar.
package ipreplay_test

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

const (
	// spreadPrice is the XAU spread charged on the crossing side: 0.30 price = 3 pips (pip 0.1), a typical demo spread.
	spreadPrice = 0.30
	// followBars is how long a fill is followed on M1 bars (4 hours) before it is reported as still open.
	followBars = 240
)

type variant struct {
	Name       string
	Parameters map[string]any
}

// variants is the A/B reported next to the configured behaviour: the technical expiry (the old Python build lived 120 minutes) and the impulse displacement.
func variants() []variant {
	out := []variant{{Name: "as configured", Parameters: nil}}
	for _, expiry := range []float64{15, 60, 120} {
		for _, displacement := range []float64{4.0, 3.0} {
			if expiry == 15 && displacement == 4.0 {
				continue // the configured behaviour
			}
			out = append(out, variant{
				Name:       fmt.Sprintf("expiry %.0f min, displacement %.1f ATR", expiry, displacement),
				Parameters: map[string]any{"expiry_minutes": expiry, "impulse_displacement_atr_multiple": displacement},
			})
		}
	}
	return out
}

type outcome struct {
	ID        string
	Direction string
	CreatedAt int64
	Filled    bool
	// Result is "stop", "1R" (reached 1R, not 2R, before the stop or the end of the follow window), "2R", "open" (neither stop nor 1R in the window),
	// or "" when the candidate never filled.
	Result      string
	Invalidated bool // an M1 close broke the invalidation price before any fill
	Expired     bool // the technical expiry passed without a fill or an invalidation
	RiskPips    float64
	AvailableR  float64
	FilledAt    int64 // open time of the fill bar
	ClosedAt    int64 // open time of the bar that ended the follow (stop / 2R), 0 when it ran out
	Limit, Stop float64
}

type runResult struct {
	Capture    string
	Variant    string
	Candidates []outcome
}

func candlesOf(t *testing.T, capture *replaycapture.Capture, tf market.Timeframe) []market.Candle {
	t.Helper()
	bars, err := capture.Candles(tf)
	if err != nil {
		t.Fatal(err)
	}
	return bars
}

// simulate walks the M1 bars after the candidate was created.
func simulate(c opportunity.Candidate, m1 []market.Candle, pip float64) outcome {
	out := outcome{ID: c.ID, Direction: string(c.Direction), CreatedAt: c.CreatedAt}
	buy := c.Direction == market.Buy
	limit := c.Entry.High
	if !buy {
		limit = c.Entry.Low
	}
	stop := float64(c.Invalidation.Price)
	risk := math.Abs(limit - stop)
	if risk <= 0 || len(c.Targets) == 0 {
		return out
	}
	out.RiskPips = risk / pip
	out.Limit, out.Stop = limit, stop
	last := float64(c.Targets[len(c.Targets)-1].Price.Price)
	out.AvailableR = math.Abs(last-limit) / risk
	ladder := []float64(nil)
	switch {
	case out.AvailableR+1e-9 >= 2.0:
		ladder = []float64{1.0, 2.0}
	case out.AvailableR+1e-9 >= 1.0:
		ladder = []float64{1.0}
	default:
		ladder = nil
	}
	level := func(r float64) float64 {
		if buy {
			return limit + r*risk
		}
		return limit - r*risk
	}
	// CreatedAt is the open time of the M1 bar whose close confirmed the setup (strategyutil: CreatedAt = ConfirmedAt = confirm.Time); that bar has
	// already closed when the candidate exists, so the first bar an order could trade on opens one minute later.
	start := sort.Search(len(m1), func(i int) bool { return m1[i].Time >= c.CreatedAt+60 })
	filledAt := -1
	for i := start; i < len(m1); i++ {
		bar := m1[i]
		if bar.Time >= c.ExpiresAt && filledAt < 0 {
			out.Expired = true
			return out
		}
		if filledAt < 0 {
			reached := (buy && bar.Low+spreadPrice <= limit) || (!buy && bar.High >= limit)
			if reached {
				filledAt = i
				out.Filled = true
				out.FilledAt = bar.Time
				out.Result = "open"
				// the fill bar can also run through the stop
				if (buy && bar.Low <= stop) || (!buy && bar.High+spreadPrice >= stop) {
					out.Result = "stop"
					out.ClosedAt = bar.Time
					return out
				}
				continue
			}
			broke := (buy && bar.Close < stop) || (!buy && bar.Close > stop)
			if broke {
				out.Invalidated = true
				return out
			}
			continue
		}
		if i-filledAt > followBars {
			return out
		}
		stopHit := (buy && bar.Low <= stop) || (!buy && bar.High+spreadPrice >= stop)
		if stopHit {
			if out.Result == "open" {
				out.Result = "stop"
			}
			out.ClosedAt = bar.Time
			return out // stop first when one bar spans the stop and a target
		}
		for _, r := range ladder {
			reachedTarget := (buy && bar.High >= level(r)) || (!buy && bar.Low+spreadPrice <= level(r))
			if reachedTarget {
				if r >= 2.0 {
					out.Result = "2R"
					out.ClosedAt = bar.Time
					return out
				}
				out.Result = "1R"
			}
		}
	}
	return out
}

func replayOne(t *testing.T, doc *config.Document, name string, v variant) runResult {
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
		t.Fatal(err)
	}
	pip := settings.Geometry.PipSize
	m1 := candlesOf(t, capture, market.M1)
	if len(m1) == 0 {
		t.Fatalf("%s has no M1 bars", name)
	}
	params := map[string]map[string]any{}
	if v.Parameters != nil {
		params["impulse_pullback"] = v.Parameters
	}
	res := runResult{Capture: name, Variant: v.Name}
	seen := map[string]bool{}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{StrategyParameters: params, OnEvaluation: func(e engine.Evaluation) {
		for _, c := range e.Candidates {
			if c.Strategy != "impulse_pullback" || seen[c.ID] {
				continue
			}
			seen[c.ID] = true
			res.Candidates = append(res.Candidates, simulate(c, m1, pip))
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	return res
}

type tally struct {
	Opportunities, Filled, Stop, OneR, TwoR, StillOpen, InvalidatedBeforeFill, ExpiredUnfilled int
}

func (a *tally) add(o outcome) {
	a.Opportunities++
	switch {
	case o.Filled:
		a.Filled++
		switch o.Result {
		case "stop":
			a.Stop++
		case "1R":
			a.OneR++
		case "2R":
			a.TwoR++
		default:
			a.StillOpen++
		}
	case o.Invalidated:
		a.InvalidatedBeforeFill++
	case o.Expired:
		a.ExpiredUnfilled++
	}
}

func TestImpulsePullbackOnRealM1(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	var captures []string
	for _, pattern := range []string{"replay-xau-m1-production-capture-*.json", "replay-xau-production-capture-*.json", "scalpwindow-xau-*.json"} {
		matches, _ := filepath.Glob(filepath.Join("..", "..", "testdata", pattern))
		for _, m := range matches {
			captures = append(captures, filepath.Base(m))
		}
	}
	sort.Strings(captures)
	var withM1 []string
	for _, name := range captures {
		capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
		if err != nil {
			t.Fatal(err)
		}
		if len(candlesOf(t, capture, market.M1)) > 0 {
			withM1 = append(withM1, name)
		}
	}
	captures = withM1
	if len(captures) == 0 {
		t.Fatal("no committed XAU captures with M1 bars")
	}
	vs := variants()
	results := make([][]runResult, len(vs))
	var wg sync.WaitGroup
	for vi, v := range vs {
		results[vi] = make([]runResult, len(captures))
		for ci, name := range captures {
			wg.Add(1)
			go func(vi, ci int, name string, v variant) {
				defer wg.Done()
				results[vi][ci] = replayOne(t, doc, name, v)
			}(vi, ci, name, v)
		}
	}
	wg.Wait()

	report := map[string]any{"captures": captures, "spread_price": spreadPrice, "follow_bars": followBars}
	table := map[string]map[string]tally{}
	for vi, v := range vs {
		table[v.Name] = map[string]tally{}
		var total tally
		for ci, name := range captures {
			var per tally
			for _, o := range results[vi][ci].Candidates {
				per.add(o)
				total.add(o)
			}
			table[v.Name][name] = per
		}
		table[v.Name]["TOTAL"] = total
		t.Logf("%-40s opportunities %3d · filled %3d (stop %d, 1R %d, 2R %d, open %d) · invalidated before fill %d · expired unfilled %d",
			v.Name, total.Opportunities, total.Filled, total.Stop, total.OneR, total.TwoR, total.StillOpen, total.InvalidatedBeforeFill, total.ExpiredUnfilled)
	}
	report["table"] = table
	perCandidate := map[string][]outcome{}
	for vi, v := range vs {
		for _, r := range results[vi] {
			perCandidate[v.Name+" | "+r.Capture] = r.Candidates
		}
	}
	report["candidates"] = perCandidate
	for _, r := range results[0] {
		for _, o := range r.Candidates {
			t.Logf("  as configured · %s · %s %s · created %d · limit %.2f stop %.2f (%.0f pips, room %.1fR) · filled %d · %s at %d", r.Capture, o.Direction, o.ID[:12], o.CreatedAt, o.Limit, o.Stop, o.RiskPips, o.AvailableR, o.FilledAt, o.Result, o.ClosedAt)
		}
	}
	if path := os.Getenv("IP_REPLAY_OUT"); path != "" {
		raw, _ := json.MarshalIndent(report, "", " ")
		if err := os.WriteFile(path, append(raw, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// Determinism: a second run of the configured behaviour on the first capture publishes the same candidates.
	first := results[0][0]
	again := replayOne(t, doc, captures[0], vs[0])
	ids := func(r runResult) string {
		var out []string
		for _, o := range r.Candidates {
			out = append(out, o.ID)
		}
		return strings.Join(out, ",")
	}
	if ids(first) != ids(again) {
		t.Errorf("%s: a second replay published a different set of Impulse Pullback candidates", captures[0])
	}
	// Every candidate keeps its own contract: a positive stop inside the instrument cap.
	for _, per := range results[0] {
		for _, o := range per.Candidates {
			if o.RiskPips <= 0 {
				t.Errorf("%s %s: no positive risk", per.Capture, o.ID)
			}
		}
	}
}
