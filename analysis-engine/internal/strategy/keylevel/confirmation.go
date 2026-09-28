package keylevel

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// confirmedReaction is Key Level's own M5 close-bar rejection confirmation,
// generalized from supply's confirmedRejection (internal/strategy/supply/
// confirmation.go) to an arbitrary reaction band + direction rather than a
// zone.Zone's own lifecycle fields — a Level carries no State/TouchCount/
// BreakIndex to key off. A level being nearby is a standing thesis, not a
// trade: only a directional close outside [low, high] after a real touch of
// the band produces a confirmed reaction. The confirmation bar may be the
// touch bar itself or the immediately following closed bar; a stale
// historical touch never creates a delayed fresh confirmation.
//
// direction == Buy: price must touch down into the band from above and the
// confirmation bar close back above high (support rejection).
// direction == Sell: price must touch up into the band from below and the
// confirmation bar close back below low (resistance rejection).
func confirmedReaction(candles []market.Candle, direction market.Direction, low, high market.Price, levelID string) *opportunity.ReactionConfirmation {
	if len(candles) < 1 {
		return nil
	}
	current := candles[len(candles)-1]
	rejected := current.IsBullish() && current.Close > float64(high)
	if direction == market.Sell {
		rejected = current.IsBearish() && current.Close < float64(low)
	}
	if !rejected {
		return nil
	}
	// The confirming close itself may also be the touch, or the touch may
	// be the immediately preceding closed bar — never further back (a
	// stale touch does not create a fresh confirmation this cycle).
	touch := current
	if !touches(current, low, high) {
		if len(candles) < 2 {
			return nil
		}
		touch = candles[len(candles)-2]
		if !touches(touch, low, high) {
			return nil
		}
		// The touching bar already closed outside the band on its own —
		// that bar was the confirmation; a later follow-through bar must
		// not create a second, delayed opportunity for the same episode.
		touchAlreadyRejected := touch.IsBullish() && touch.Close > float64(high)
		if direction == market.Sell {
			touchAlreadyRejected = touch.IsBearish() && touch.Close < float64(low)
		}
		if touchAlreadyRejected {
			return nil
		}
	}
	return &opportunity.ReactionConfirmation{
		ZoneID:              levelID,
		TouchBarTime:        touch.Time,
		ConfirmationBarTime: current.Time,
		ReactionType:        "rejection",
	}
}

func touches(c market.Candle, low, high market.Price) bool {
	return c.Low <= float64(high) && c.High >= float64(low)
}
