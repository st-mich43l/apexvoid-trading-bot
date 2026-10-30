package supply

import (
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// ZoneScore is a detected zone and its score, for the parity test against the shared zone score.
type ZoneScore struct {
	Origin    int
	Low, High float64
	Touches   int
	Score     float64
}

// ZoneScores detects the supply zones of the context's frame tf and scores them, exactly as the strategy does before any lifecycle test.
func (s *Strategy) ZoneScores(ctx *analysiscontext.MarketContext, tf market.Timeframe) []ZoneScore {
	frameContext := ctx.Timeframes[tf]
	if frameContext == nil || frameContext.Legacy == nil || len(frameContext.Legacy.Bars) < 10 {
		return nil
	}
	frame := frameContext.Legacy
	zones := s.detectZones(frame.Bars, frame.ATR)
	markTouches(zones, frame.Bars)
	inputs := s.inputsFor(frame, higherFrames(ctx, tf))
	out := make([]ZoneScore, len(zones))
	for i, z := range zones {
		out[i] = ZoneScore{Origin: z.Origin, Low: z.Low, High: z.High, Touches: z.Touches, Score: scoreZone(z, inputs)}
	}
	return out
}
