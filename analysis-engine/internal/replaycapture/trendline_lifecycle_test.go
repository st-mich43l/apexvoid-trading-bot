package replaycapture_test

// TL-LIVE-2 lifecycle on the real EURUSD window: an unfilled one-price opportunity ends when its projected price goes stale, and ends sooner when its line breaks.
// Observed through the engine's live opportunity list after every closed bar, the same list the Algo Bot's live-set projection is published from.

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

type step struct {
	at   int64
	live map[string]bool
}

// liveAfterEveryBar dispatches the capture bar by bar and records which Trendline opportunities are live after each closed M15 bar.
func liveAfterEveryBar(t *testing.T, c *replaycapture.Capture, parameters map[string]any) []step {
	t.Helper()
	doc, err := config.ResolveDocument("../../../config/apexvoid.yml")
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, market.M15, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(c.Symbol)); err != nil {
		t.Fatal(err)
	}
	for i := range settings.Strategies {
		settings.Strategies[i].Enabled = settings.Strategies[i].ID == "trendline"
		if parameters != nil && settings.Strategies[i].ID == "trendline" {
			merged := map[string]any{}
			for k, v := range settings.Strategies[i].Parameters {
				merged[k] = v
			}
			for k, v := range parameters {
				merged[k] = v
			}
			settings.Strategies[i].Parameters = merged
		}
	}
	byTF := map[market.Timeframe][]market.Candle{}
	for name := range c.Timeframes {
		candles, err := c.Candles(market.Timeframe(name))
		if err != nil {
			t.Fatal(err)
		}
		byTF[market.Timeframe(name)] = candles
	}
	events, err := replaycapture.Events(c.Symbol, byTF, marketdata.EventOriginReplay)
	if err != nil {
		t.Fatal(err)
	}
	e := engine.NewEngine(nil)
	if err := e.Register(c.Symbol, settings); err != nil {
		t.Fatal(err)
	}
	var out []step
	for _, event := range events {
		snap, err := e.Dispatch(event)
		if err != nil {
			t.Fatal(err)
		}
		if event.Timeframe != market.M15 {
			continue
		}
		live := map[string]bool{}
		for _, o := range snap.Opportunities {
			if o.Strategy == "trendline" {
				live[o.ID] = true
			}
		}
		out = append(out, step{at: event.Candle.Time, live: live})
	}
	return out
}

func withBars(c *replaycapture.Capture, tf string, rows [][]float64) *replaycapture.Capture {
	out := &replaycapture.Capture{Version: c.Version, Symbol: c.Symbol, Columns: c.Columns, Timeframes: map[string][][]float64{}}
	for name, r := range c.Timeframes {
		out.Timeframes[name] = r
	}
	out.Timeframes[tf] = append(append([][]float64(nil), c.Timeframes[tf]...), rows...)
	return out
}

// quiet is a drifting-nowhere M15 bar near `price`: no new swing, no new line, no break.
func quiet(at int64, price float64) []float64 {
	return []float64{float64(at), price, price + 0.00004, price - 0.00004, price, 0}
}

func TestAnUnfilledProjectionExpiresWhenItGoesStale(t *testing.T) {
	s := load(t)
	base := capture(map[string][][]float64{"M15": s.M15, "H1": s.H1})
	o := only(t, replayTrendline(t, base, market.M15, nil))
	x := o.Execution
	price := x.Price - 0.0003 // well clear of the line and of the corridor: a SELL stays below its resistance
	var rows [][]float64
	last := int64(s.M15[len(s.M15)-1][0])
	for at := last + 900; at <= x.ValidUntil+3*900; at += 900 {
		rows = append(rows, quiet(at, price))
	}
	steps := liveAfterEveryBar(t, withBars(base, "M15", rows), nil)
	sawLive, ended := false, int64(0)
	for _, st := range steps {
		switch {
		case st.live[o.ID]:
			sawLive = true
			if st.at+900 > x.ValidUntil {
				t.Fatalf("still live at the close of the bar opening %d, after the projection went stale at %d", st.at, x.ValidUntil)
			}
		case sawLive && ended == 0:
			ended = st.at
		}
	}
	if !sawLive || ended == 0 {
		t.Fatalf("the opportunity must be live after its creation and end later (live=%v ended=%d)", sawLive, ended)
	}
	if ended+900 < x.ValidUntil {
		t.Fatalf("ended at %d, before its projection went stale at %d, with no break", ended, x.ValidUntil)
	}
}

// longValidity keeps a projection executable for six bars whatever the slope, so that a break inside that window is what ends the opportunity and nothing else.
var longValidity = map[string]any{"single_price_max_drift_atr": 50.0, "single_price_validity_bars": map[string]any{"M5": 6, "M15": 6}}

func TestABrokenLineEndsTheOpportunityBeforeItsProjectionGoesStale(t *testing.T) {
	s := load(t)
	base := capture(map[string][][]float64{"M15": s.M15, "H1": s.H1})
	o := only(t, replayTrendline(t, base, market.M15, longValidity))
	x := o.Execution
	if (x.ValidUntil-x.ProjectedAt)/900 != 6 {
		t.Fatalf("the control needs a 6-bar projection, got %d", (x.ValidUntil-x.ProjectedAt)/900)
	}
	atr := o.Technical.ATR
	// Closes beyond the resistance by more than the close-violation band (0.15 ATR) but short of the structural stop: only the LINE has broken.
	breakClose := x.Price + 0.25*atr
	if breakClose >= float64(o.Invalidation.Price) {
		t.Skipf("the fixture's stop %v leaves no room above the line for a break that is not also a stop-out", o.Invalidation.Price)
	}
	last := int64(s.M15[len(s.M15)-1][0])
	check := last + 2*900
	quietRows := [][]float64{quiet(last+900, x.Price-0.0003), quiet(last+1800, x.Price-0.0003)}
	broken := [][]float64{
		{float64(last + 900), x.Price, breakClose + 0.00003, x.Price - 0.00002, breakClose, 0},
		{float64(last + 1800), breakClose, breakClose + 0.00003, breakClose - 0.00003, breakClose, 0},
	}
	liveAt := func(rows [][]float64, at int64) (bool, bool) {
		var atCreation, atCheck bool
		for _, st := range liveAfterEveryBar(t, withBars(base, "M15", rows), longValidity) {
			if st.at == last {
				atCreation = st.live[o.ID]
			}
			if st.at == at {
				atCheck = st.live[o.ID]
			}
		}
		return atCreation, atCheck
	}
	if created, stillLive := liveAt(quietRows, check); !created || !stillLive {
		t.Fatalf("control: with no break the opportunity must be live at creation (%v) and two bars later (%v)", created, stillLive)
	}
	if created, stillLive := liveAt(broken, check); !created || stillLive {
		t.Fatalf("a broken line must end the opportunity two bars later, inside its projection (live at creation=%v, after the break=%v)", created, stillLive)
	}
}
