package supply

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// confirmedRejection is supply's independent, strict M5 close-bar
// confirmation. A touched zone is a standing thesis, not a trade.
// Only a directional close *outside* the zone after a real touch produces
// a separately identified rejection opportunity. The confirmation bar may
// be the touch bar itself, or the immediately following closed bar; a
// stale historical touch never creates a delayed fresh confirmation.
func confirmedRejection(z zone.Zone, candles []market.Candle) *opportunity.ReactionConfirmation {
	if len(candles) < 2 || z.Kind != zone.KindSupply || z.State != zone.StateTouched ||
		z.TouchCount < 1 || z.LastTouchedAt == nil || z.BreakIndex >= len(candles)-1 {
		return nil
	}
	current := candles[len(candles)-1]
	if current.Time <= z.CreatedAt || !current.IsBearish() || current.Close >= float64(z.Low) {
		return nil
	}
	touchTime := *z.LastTouchedAt
	if touchTime > current.Time {
		return nil
	}
	touch := current
	if touchTime != current.Time {
		touch = candles[len(candles)-2]
		if touch.Time != touchTime {
			return nil
		}
		if touch.IsBearish() && touch.Close < float64(z.Low) {
			// The touching bar already confirmed this episode; a later
			// follow-through candle must not create another opportunity.
			return nil
		}
	}
	if touch.Low > float64(z.High) || touch.High < float64(z.Low) {
		return nil
	}
	return &opportunity.ReactionConfirmation{
		ZoneID: z.ID,
		TouchBarTime: touchTime,
		ConfirmationBarTime: current.Time,
		ReactionType: "rejection",
	}
}
