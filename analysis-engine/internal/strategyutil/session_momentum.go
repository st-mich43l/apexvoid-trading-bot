package strategyutil

import (
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
)

// SessionMomentum is the London session's net move measured in M15 ATR, read causally from the finalized session levels: London opens at the
// latest finalized ASIA level's AvailableAt and closes at the latest finalized LONDON level's AvailableAt (the New York open). It is evidence,
// not a gate: it applies only to something confirmed while New York is the active session, and ok is false outside New York, before the
// confirmation reached the New York open, or when either boundary or the data is missing.
//
// The net move is the close of the last M5 candle that closed before the New York open minus the open of the first M5 candle at or after the
// London open, divided by the M15 ATR as of the New York open. at is the open time of the confirming candle; no candle opening after it is read.
func SessionMomentum(ctx *analysiscontext.MarketContext, at int64) (dir market.Direction, netATR float64, ok bool) {
	if ctx == nil || ctx.Session.State.Active != session.NY {
		return "", 0, false
	}
	londonOpen, nyOpen := int64(0), int64(0)
	for _, level := range ctx.Session.State.Levels {
		if !level.Final || level.AvailableAt > at {
			continue
		}
		switch level.Name {
		case "ASIA_H", "ASIA_L":
			if level.AvailableAt > londonOpen {
				londonOpen = level.AvailableAt
			}
		case "LONDON_H", "LONDON_L":
			if level.AvailableAt > nyOpen {
				nyOpen = level.AvailableAt
			}
		}
	}
	if londonOpen == 0 || nyOpen == 0 || nyOpen <= londonOpen || at < nyOpen {
		return "", 0, false
	}
	m5, m15 := ctx.Timeframes[market.M5], ctx.Timeframes[market.M15]
	if m5 == nil || m15 == nil {
		return "", 0, false
	}
	first, last := -1, -1
	for i, c := range m5.Candles {
		if c.Time > at {
			break
		}
		if first < 0 && c.Time >= londonOpen {
			first = i
		}
		if c.Time+levelBarSeconds <= nyOpen {
			last = i
		}
	}
	if first < 0 || last < first {
		return "", 0, false
	}
	var closed []market.Candle
	for _, c := range m15.Candles {
		if c.Time+15*60 > nyOpen {
			break
		}
		closed = append(closed, c)
	}
	window := indicator.NewWilderWindow(closed, 14, 140)
	if !window.Enough() {
		return "", 0, false
	}
	atr, found := window.At(len(closed) - 1)
	if !found {
		return "", 0, false
	}
	net := (m5.Candles[last].Close - m5.Candles[first].Open) / atr
	if net == 0 || math.IsNaN(net) || math.IsInf(net, 0) {
		return "", 0, false
	}
	if net > 0 {
		return market.Buy, net, true
	}
	return market.Sell, net, true
}

// SessionMomentumEvidence is the evidence code and the signed, direction-adjusted component for a candidate of the given direction: the evidence is
// session_momentum_aligned when the London move is at least minimumATR and the candidate trades with it, session_momentum_opposed when it trades
// against it, and none when the move is smaller. component is the net move in ATR, positive when it favours the candidate; has is false without a
// reading.
func SessionMomentumEvidence(ctx *analysiscontext.MarketContext, at int64, direction market.Direction, minimumATR float64) (code string, component float64, has bool) {
	dir, net, ok := SessionMomentum(ctx, at)
	if !ok {
		return "", 0, false
	}
	component = math.Abs(net)
	if dir != direction {
		component = -component
	}
	switch {
	case math.Abs(net) < minimumATR:
		return "", component, true
	case dir == direction:
		return "session_momentum_aligned", component, true
	default:
		return "session_momentum_opposed", component, true
	}
}
