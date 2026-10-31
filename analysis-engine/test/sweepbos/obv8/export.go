package obv8

import (
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Block is one order block of a structure frame as v8 found it: its side, its own candle and the candle at which its break was confirmed, both indexes of
// the frame. The measurement test runs this on a frame with the shared breaks (v8) and with the strategy's causal ones (v9) to count the blocks that move.
type Block struct {
	Side                        string
	FrameOrigin, FrameConfirmed int
}

// Blocks are the blocks of the frame that pass the structure tests, without binding them to the execution frame.
func (s *Strategy) Blocks(frame *analysiscontext.LegacyFrame, tf market.Timeframe) []Block {
	var out []Block
	for _, in := range s.structureInstances(frame, tf, func(string) {}) {
		out = append(out, Block{Side: in.Side, FrameOrigin: in.FrameOrigin, FrameConfirmed: in.FrameConfirmed})
	}
	return out
}
