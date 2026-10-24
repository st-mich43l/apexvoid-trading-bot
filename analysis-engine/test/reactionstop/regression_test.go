package reactionstop_test

import (
	"math"
	"path/filepath"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

// A real confirmed setup of each strategy whose structural-only invalidation (the price the frozen publisher gave, pinned here from the base
// commit) sat inside the bars the reaction itself traded. The invalidation must now be the reaction's extreme minus the strategy's buffer.
type realCase struct {
	capture, strategy string
	direction         market.Direction
	confirmTime       int64
	adverse           float64 // extreme of the M5 bars from the touch to the confirmation, read from the committed capture
	oldInvalidation   float64 // structural-only price published before the anchor
}

var realCases = []realCase{
	// key_level BUY sweep_reclaim 2026-10-13 21:00: the confirming wick low 1.1193 pierced the stop at 1.1194.
	{"replay-eurusd-production-capture-20261014.json", "key_level", market.Buy, 1791925200, 1.1193, 1.1194},
	// demand BUY strong_reclaim 2026-10-13 21:25.
	{"replay-eurusd-production-capture-20261014.json", "demand", market.Buy, 1791926700, 1.11951, 1.11956},
	// order_block BUY strong_reclaim, same bar.
	{"replay-eurusd-production-capture-20261014.json", "order_block", market.Buy, 1791926700, 1.11951, 1.11969},
	// supply SELL strong_reclaim 2026-10-14 06:35: the bar high 1.12098 was above the stop at 1.12094.
	{"replay-eurusd-production-capture-20261014.json", "supply", market.Sell, 1791959700, 1.12098, 1.12094},
	{"replay-gbpusd-production-capture-20261013.json", "flip_zone", market.Buy, 1791763500, 1.32394, 1.32397},
	{"replay-gbpjpy-production-capture-20261006.json", "trendline", market.Buy, 1790835300, 209.694, 209.696},
}

func TestRealCapturedSetupsNowStopBeyondTheReactionExtreme(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	byCapture := map[string][]realCase{}
	for _, c := range realCases {
		byCapture[c.capture] = append(byCapture[c.capture], c)
	}
	var wg sync.WaitGroup
	for name, cases := range byCapture {
		wg.Add(1)
		go func(name string, cases []realCase) {
			defer wg.Done()
			capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
			if err != nil {
				t.Error(err)
				return
			}
			settings, err := engine.LoadSettings(doc, market.M5, false)
			if err != nil {
				t.Error(err)
				return
			}
			if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
				t.Error(err)
				return
			}
			m5, err := capture.Candles(market.M5)
			if err != nil {
				t.Error(err)
				return
			}
			index := map[int64]int{}
			for i, b := range m5 {
				index[b.Time] = i
			}
			found := map[int]int{}
			_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
				frame := e.Context.Timeframes[market.M5]
				if frame == nil || frame.Legacy == nil {
					return
				}
				for _, c := range e.Candidates {
					for i, want := range cases {
						if string(c.Strategy) != want.strategy || c.Direction != want.direction || c.Reaction == nil || c.Reaction.ConfirmationBarTime != want.confirmTime {
							continue
						}
						touch, confirm := index[c.Reaction.TouchBarTime], index[c.Reaction.ConfirmationBarTime]
						adverse := m5[touch].Low
						if want.direction == market.Sell {
							adverse = m5[touch].High
						}
						for _, b := range m5[touch : confirm+1] {
							if want.direction == market.Buy {
								adverse = math.Min(adverse, b.Low)
							} else {
								adverse = math.Max(adverse, b.High)
							}
						}
						tick := settings.Geometry.PipSize / 10
						bufferATR, _ := doc.Get("analysis.strategies." + want.strategy + ".invalidation_buffer_atr")
						buffer := bufferATR.(float64) * frame.Legacy.DetectorATR
						got := float64(c.Invalidation.Price)
						label := want.strategy + " " + string(want.direction)
						if math.Abs(adverse-want.adverse) > tick/2 {
							t.Errorf("%s: the capture's reaction extreme is %v, the fixture pins %v", label, adverse, want.adverse)
						}
						sign := 1.0 // BUY: the stop is below the extreme
						if want.direction == market.Sell {
							sign = -1
						}
						// The old price was inside the confirming bars ...
						if sign*(want.oldInvalidation-adverse) < 0 {
							t.Errorf("%s: the pinned old invalidation %v is not inside the reaction (extreme %v)", label, want.oldInvalidation, adverse)
						}
						// ... the new one is the extreme beyond the buffer, and is never closer than the old.
						if math.Abs(got-(adverse-sign*buffer)) > tick {
							t.Errorf("%s: invalidation %v, want the extreme %v beyond a buffer of %v = %v", label, got, adverse, buffer, adverse-sign*buffer)
						}
						if sign*(adverse-got) <= 0 || sign*(want.oldInvalidation-got) <= 0 {
							t.Errorf("%s: invalidation %v is not beyond the extreme %v and the old stop %v", label, got, adverse, want.oldInvalidation)
						}
						coded := false
						for _, ev := range evidenceCodes(c) {
							coded = coded || ev == anchoredCode
						}
						if !coded {
							t.Errorf("%s: the evidence code %s is missing", label, anchoredCode)
						}
						found[i]++
					}
				}
			}})
			if err != nil {
				t.Error(err)
				return
			}
			for i, want := range cases {
				if found[i] == 0 {
					t.Errorf("%s %s %s at %d was not published", name, want.strategy, want.direction, want.confirmTime)
				}
			}
		}(name, cases)
	}
	wg.Wait()
}
