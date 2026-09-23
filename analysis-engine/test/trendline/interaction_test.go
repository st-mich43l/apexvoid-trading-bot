package trendline_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

// A support line(i) = 90 + 1.0*i. At index 50, line = 140. atr=10 ->
// interactionBand=2.0, closeViolation=1.5, approachMinDistance=1.0.
func supportLineAt50() trendline.Trendline {
	return trendline.Trendline{Kind: trendline.KindSupport, Slope: 1.0, Intercept: 90}
}

func candlesUpTo(prior, last market.Candle) []market.Candle {
	candles := make([]market.Candle, 51)
	for i := range candles {
		candles[i] = market.Candle{Time: int64(i), Open: 100, High: 101, Low: 99, Close: 100, Volume: 1}
	}
	candles[49] = prior
	candles[50] = last
	return candles
}

func TestEvaluateInteractionNoData(t *testing.T) {
	got := trendline.EvaluateInteraction(nil, supportLineAt50(), 10, trendlineTestConfig())
	if got.State != trendline.InteractionNoData {
		t.Errorf("State = %s, want NO_DATA", got.State)
	}
	if got.RejectionReason != "no_m5_data" {
		t.Errorf("RejectionReason = %q, want no_m5_data", got.RejectionReason)
	}
}

// TestEvaluateInteractionReclaimedSupport: prior close comfortably above
// the line (approach valid), latest bar touches the band and closes back
// above the line.
func TestEvaluateInteractionReclaimedSupport(t *testing.T) {
	prior := market.Candle{Time: 49, Open: 141, High: 142, Low: 140, Close: 142} // line(49)=139, distance=3 >= 1.0
	last := market.Candle{Time: 50, Open: 140, High: 141, Low: 139, Close: 141}  // line(50)=140, touch (low=139<=142,high=141>=138), close_distance=1 >= 0
	candles := candlesUpTo(prior, last)
	got := trendline.EvaluateInteraction(candles, supportLineAt50(), 10, trendlineTestConfig())
	if got.State != trendline.InteractionReclaimedSupport {
		t.Fatalf("State = %s, want RECLAIMED_SUPPORT", got.State)
	}
	if got.InteractionIndex == nil || *got.InteractionIndex != 50 {
		t.Errorf("InteractionIndex = %v, want 50", got.InteractionIndex)
	}
	if !got.ApproachDirectionValid {
		t.Error("ApproachDirectionValid should be true")
	}
}

// TestEvaluateInteractionTestingSupport: touch occurs, approach valid,
// but the close stays on the invalid side within the close-violation
// tolerance — TESTING, not RECLAIMED or FAILED.
func TestEvaluateInteractionTestingSupport(t *testing.T) {
	prior := market.Candle{Time: 49, Open: 141, High: 142, Low: 140, Close: 142}
	last := market.Candle{Time: 50, Open: 140, High: 141, Low: 139, Close: 139.5} // close_distance=-0.5, within [-1.5,0)
	candles := candlesUpTo(prior, last)
	got := trendline.EvaluateInteraction(candles, supportLineAt50(), 10, trendlineTestConfig())
	if got.State != trendline.InteractionTestingSupport {
		t.Fatalf("State = %s, want TESTING_SUPPORT", got.State)
	}
	if got.RejectionReason != "testing_without_reaction" {
		t.Errorf("RejectionReason = %q, want testing_without_reaction", got.RejectionReason)
	}
}

// TestEvaluateInteractionFailedSupportOnCloseViolation: the close is far
// enough below the line to be an outright close violation, regardless of
// touch/approach — checked FIRST, before anything else.
func TestEvaluateInteractionFailedSupportOnCloseViolation(t *testing.T) {
	prior := market.Candle{Time: 49, Open: 141, High: 142, Low: 140, Close: 142}
	last := market.Candle{Time: 50, Open: 139, High: 139, Low: 137, Close: 138} // close_distance=138-140=-2 < -1.5
	candles := candlesUpTo(prior, last)
	got := trendline.EvaluateInteraction(candles, supportLineAt50(), 10, trendlineTestConfig())
	if got.State != trendline.InteractionFailedSupport {
		t.Fatalf("State = %s, want FAILED_SUPPORT", got.State)
	}
	if got.RejectionReason != "close_violation" {
		t.Errorf("RejectionReason = %q, want close_violation", got.RejectionReason)
	}
}

// TestEvaluateInteractionAboveLineNoTouch: price sits well clear of the
// band, no touch, too far to even be "approaching."
func TestEvaluateInteractionAboveLineNoTouch(t *testing.T) {
	prior := market.Candle{Time: 49, Open: 141, High: 142, Low: 140, Close: 142}
	last := market.Candle{Time: 50, Open: 146, High: 147, Low: 145, Close: 146} // low=145 > line+band(142) -> no touch; distance=6 > band*2(4)
	candles := candlesUpTo(prior, last)
	got := trendline.EvaluateInteraction(candles, supportLineAt50(), 10, trendlineTestConfig())
	if got.State != trendline.InteractionAboveLine {
		t.Fatalf("State = %s, want ABOVE_LINE", got.State)
	}
	if got.InteractionIndex != nil {
		t.Error("a no-touch, non-violation state must not set InteractionIndex")
	}
}

// TestEvaluateInteractionApproachingSupport: no touch, but within
// interactionBand*2 of the line — an approach worth watching.
func TestEvaluateInteractionApproachingSupport(t *testing.T) {
	prior := market.Candle{Time: 49, Open: 141, High: 142, Low: 140, Close: 142}
	last := market.Candle{Time: 50, Open: 143, High: 144, Low: 143, Close: 143.5} // low=143 > 142 (no touch); distance=3.5 <= band*2(4)
	candles := candlesUpTo(prior, last)
	got := trendline.EvaluateInteraction(candles, supportLineAt50(), 10, trendlineTestConfig())
	if got.State != trendline.InteractionApproachingSupport {
		t.Fatalf("State = %s, want APPROACHING_SUPPORT", got.State)
	}
}

// TestEvaluateInteractionResistanceReclaimed mirrors the support case for
// the opposite kind, proving the kind-dependent branches both work.
// Resistance line(i) = 200 - 1.0*i. At index 50, line = 150. "Valid
// side" is BELOW the line.
func TestEvaluateInteractionResistanceReclaimed(t *testing.T) {
	line := trendline.Trendline{Kind: trendline.KindResistance, Slope: -1.0, Intercept: 200}
	prior := market.Candle{Time: 49, Open: 149, High: 150, Low: 148, Close: 149} // line(49)=151, distance=151-149=2 >= 1.0
	last := market.Candle{Time: 50, Open: 150, High: 149, Low: 147, Close: 148}  // line(50)=150, touch (high=149>=148, low=147<=152), close_distance=150-148=2>=0
	candles := candlesUpTo(prior, last)
	got := trendline.EvaluateInteraction(candles, line, 10, trendlineTestConfig())
	if got.State != trendline.InteractionReclaimedResistance {
		t.Fatalf("State = %s, want RECLAIMED_RESISTANCE", got.State)
	}
}
