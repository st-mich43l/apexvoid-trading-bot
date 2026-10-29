package roundnumber

import (
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/scribe"
)

// future appends candles that would change every decision if they were read: a crash through the handle and a spike far above.
func future(bars []market.Candle) []market.Candle {
	s := scribe.New(bars[len(bars)-1].Time+300, bars[len(bars)-1].Close)
	s.Bars = append(s.Bars, bars...)
	s.Bar(4090.0, 1.0, 1.0)
	s.Bar(4140.0, 1.0, 1.0)
	s.Bar(4100.0, 1.0, 1.0)
	return s.Bars
}

// A decision made at candle e reads candles <= e only: the result with the future present equals the result with only the past, at every candle.
func TestAppendingFutureCandlesNeverChangesADecisionAlreadyMade(t *testing.T) {
	stopRun, _ := bullishStopRun()
	breakRetest, _ := bullishBreakRetest()
	for name, bars := range map[string][]market.Candle{
		"stop run BUY": stopRun, "stop run SELL": scribe.Reflect(stopRun, 4110),
		"break retest BUY": breakRetest, "break retest SELL": scribe.Reflect(breakRetest, 4110),
	} {
		cfg := testConfig(t, map[string]any{"max_handle_distance_atr": 30.0})
		withFuture := future(bars)
		for asOf := 40; asOf < len(bars); asOf++ {
			got := DetectAsOf(cfg, Input{M5: withFuture}, asOf)
			want := Detect(cfg, Input{M5: bars[:asOf+1]})
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("%s at candle %d: the future changed the decision\n got  %+v\n want %+v", name, asOf, got, want)
			}
		}
	}
}

// Bar-by-bar evaluation reaches the same setup the whole-window detection does, and publishes it for exactly the candles it is fresh.
func TestBarByBarEvaluationEqualsWholeWindowDetection(t *testing.T) {
	bars, idx := bullishStopRun()
	cfg := testConfig(t, map[string]any{"max_handle_distance_atr": 30.0})
	published := 0
	for asOf := 40; asOf < len(bars); asOf++ {
		got := Detect(cfg, Input{M5: bars[:asOf+1]})
		if asOf < idx.shift && len(got.Setups) != 0 {
			t.Fatalf("a setup was published at candle %d, before its confirmation at %d", asOf, idx.shift)
		}
		published += len(got.Setups)
	}
	if published != 1 {
		t.Fatalf("published %d times over the fixture, want exactly once (at the confirming candle)", published)
	}
}

func TestAHandleOutOfReachIsNotWatched(t *testing.T) {
	bars, _ := bullishStopRun()
	got := Detect(testConfig(t, map[string]any{"max_handle_distance_atr": 0.5}), Input{M5: bars})
	if len(got.Setups) != 0 {
		t.Fatalf("a handle further than max_handle_distance_atr from price must not be watched: %+v", got.Setups)
	}
}
