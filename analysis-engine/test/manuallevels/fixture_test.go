package manuallevels_test

import (
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

// Three synthetic signals (fixtures/synthetic_signals.jsonl: no production data) placed on the committed captures, each built to sit on a structure the
// engine reports at the bar before it: XAU on the previous-day high, XAU on an M15 order block, EURUSD on an H1 swing level.
func TestSyntheticSignalsAreAttributedToTheStructuresTheyAreBuiltOn(t *testing.T) {
	signals, err := loadSignals(filepath.Join("fixtures", "synthetic_signals.jsonl"))
	if err != nil || len(signals) != 3 {
		t.Fatalf("fixture: %v (%d signals)", err, len(signals))
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	captureOf := map[string]string{"XAU": "replay-xau-production-capture-20261012.json", "EURUSD": "replay-eurusd-production-capture-20261006.json"}
	bySymbol := map[string][]signal{}
	for _, s := range signals {
		bySymbol[s.Symbol] = append(bySymbol[s.Symbol], s)
	}
	got := map[int64]attribution{}
	for symbol, list := range bySymbol {
		capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", captureOf[symbol]))
		if err != nil {
			t.Fatal(err)
		}
		for _, a := range analyse(t, doc, symbol, mergeCaptures([]*replaycapture.Capture{capture}), list) {
			got[a.ID] = a
		}
	}
	for _, want := range []struct {
		id       int64
		family   string // the structure the signal is built on: inside it
		notOn    string // a family it is nowhere near
		stopPips float64
	}{
		{9001, "pdh_pdl", "pwh_pwl", 50},
		{9002, "order_block|M15", "pdh_pdl", 54.4},
		{9003, "swing_level|H1", "pdh_pdl", 10},
	} {
		a, ok := got[want.id]
		if !ok || !a.Covered {
			t.Fatalf("signal %d was not attributed (covered %v)", want.id, ok && a.Covered)
		}
		row := a.Entry[want.family]
		if !row.Explains || row.Pips != 0 {
			t.Errorf("signal %d: %s should contain the entry, got %+v (explained by %v)", want.id, want.family, row, a.Explained)
		}
		if off := a.Entry[want.notOn]; off.Explains {
			t.Errorf("signal %d: %s must not explain it, got %+v", want.id, want.notOn, off)
		}
		if a.StopPips < want.stopPips-0.6 || a.StopPips > want.stopPips+0.6 {
			t.Errorf("signal %d: stop %.1f pips, want about %.1f", want.id, a.StopPips, want.stopPips)
		}
		if a.StateTF != "M5" || a.AgeSeconds > 300 {
			t.Errorf("signal %d: state %s aged %ds, want the last closed M5 bar", want.id, a.StateTF, a.AgeSeconds)
		}
	}
}

func TestMergedCapturesAreDeduplicatedAndTrimmedAfterTheirLastLongHole(t *testing.T) {
	row := func(t int64) []float64 { return []float64{float64(t), 1, 2, 0.5, 1.5, 0} }
	day := int64(86400)
	a := &replaycapture.Capture{Symbol: "XAU", Timeframes: map[string][][]float64{"M5": {row(0), row(300), row(600)}, "H1": {row(0), row(3600)}}}
	b := &replaycapture.Capture{Symbol: "XAU", Timeframes: map[string][][]float64{"M5": {row(600), row(900), row(900 + 5*day), row(900 + 5*day + 300)}, "H1": {row(3600), row(7200)}}}
	merged := mergeCaptures([]*replaycapture.Capture{a, b})
	m5 := merged.Timeframes["M5"]
	if len(m5) != 2 || int64(m5[0][0]) != 900+5*day {
		t.Errorf("M5 should start after the 5 day hole, got %d bars starting %v", len(m5), m5)
	}
	if h1 := merged.Timeframes["H1"]; len(h1) != 3 || int64(h1[2][0]) != 7200 {
		t.Errorf("H1 should merge 0, 3600, 7200 once each, got %v", h1)
	}
}
