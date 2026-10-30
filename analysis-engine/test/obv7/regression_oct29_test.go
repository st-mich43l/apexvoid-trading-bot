package obv7_test

import (
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/obv7/reference"
)

// The four production trades of 28-29 Oct 2026 (docs/strategies/order-block-definition.md section 1), replayed from the real Redis bars (testdata/obwindow-*-
// 20261029.json, closed bars only): three losing XAU order blocks and one winning EURUSD one. Their structural ids carry no timeframe: all four are M5 blocks.
//
//   - v5 (the vendored master with the close-break rule and the touch limit off) is what production ran: it publishes all four.
//   - v6 (the vendored master as it ran after the definition) refuses the losers by the close-break rule and the touch limit.
//   - v7 finds blocks on M15 and H1 only, so it must publish none of the three losers. Whether it publishes the winner is REPORTED, not tuned for.
func TestTheProductionBlocksOf29OctAreTradedByV5AndTheLosersNotByV7(t *testing.T) {
	doc := resolve(t)
	losers := map[string]string{
		"technique:order_block:buy:1793216100":  "XAU BUY, 28 Oct 19:35 origin, filled 23:51, -30 pips",
		"technique:order_block:sell:1793231400": "XAU SELL, 28 Oct 23:50 origin, filled 29 Oct 01:47, -51 pips",
		"technique:order_block:sell:1793122200": "XAU SELL, 27 Oct 17:30 origin, published 28 Oct 01:20, filled 19:49, -48 pips",
	}
	winner := "technique:order_block:buy:1793202300" // EURUSD BUY, 28 Oct 15:45 origin, filled 18:31, +24 pips
	seenV5, seenV6, seenV7 := map[string]bool{}, map[string]bool{}, map[string]bool{}
	var v7Published []string
	for _, window := range []string{"obwindow-xau-20261029.json", "obwindow-eurusd-20261029.json"} {
		capture, err := replaycapture.Load(testdata(window))
		if err != nil {
			t.Fatal(err)
		}
		p := newPair(t, doc, capture.Symbol)
		v5 := newV5(t, doc, capture.Symbol)
		_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{FrozenWindows: true, OnEvaluation: func(e engine.Evaluation) {
			if e.Timeframe != market.M5 || e.Context == nil {
				return
			}
			for _, c := range confirmed(v5.Evaluate(e.Context)) {
				seenV5[c.StructuralID] = true
			}
			for _, c := range confirmed(p.v6.Evaluate(e.Context)) {
				seenV6[c.StructuralID] = true
			}
			for _, c := range p.v7.Evaluate(e.Context) {
				seenV7[c.StructuralID] = true
				v7Published = append(v7Published, time.Unix(e.BarTime, 0).UTC().Format("02 15:04")+" "+describe(c))
			}
			p.v7.TakeRefusals()
		}})
		if err != nil {
			t.Fatal(err)
		}
	}
	for id, what := range losers {
		if !seenV5[id] {
			t.Errorf("v5 did not publish %s (%s): the regression proves nothing", id, what)
		}
		if seenV7[id] {
			t.Errorf("v7 publishes %s (%s)", id, what)
		}
		t.Logf("%s (%s): v5 publishes = %v, v6 publishes = %v, v7 publishes = %v", id, what, seenV5[id], seenV6[id], seenV7[id])
	}
	if !seenV5[winner] {
		t.Errorf("v5 did not publish the EURUSD winner %s", winner)
	}
	t.Logf("EURUSD winner %s: v5 publishes = %v, v6 publishes = %v, v7 publishes = %v (an M5 block: v7 finds blocks on M15 and H1 only)", winner, seenV5[winner], seenV6[winner], seenV7[winner])
	t.Logf("v7 publishes %d setups on the two windows", len(v7Published))
}

// newV5 is the vendored master with the order block definition switched off: what production ran on 28-29 Oct.
func newV5(t testing.TB, doc *config.Document, symbol market.Symbol) strategy.Strategy {
	t.Helper()
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(symbol)); err != nil {
		t.Fatal(err)
	}
	var cfg strategy.Config
	for _, c := range settings.Strategies {
		if c.ID == "order_block" {
			cfg = c
		}
	}
	cfg.Version = "v5"
	cfg.Parameters = masterParameters(settings)
	cfg.Parameters["confirmed_break_buffer_atr"] = 0.0
	delete(cfg.Parameters, "maximum_zone_touches")
	for k, v := range pairParameters(t, doc, symbol) {
		cfg.Parameters[k] = v
	}
	v5, err := reference.New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	return v5
}
