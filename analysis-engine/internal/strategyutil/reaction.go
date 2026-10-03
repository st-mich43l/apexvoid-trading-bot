package strategyutil

import (
	"fmt"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/reaction"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// ReactionConfig is the legacy Python reaction-confirmation tuning
// (execution.policy.structural_reaction_lookback_bars and
// engulfing_minimum_range_atr), read from a strategy's own parameters.
type ReactionConfig struct {
	LookbackBars             int
	EngulfingMinimumRangeATR float64
}

// ParseReactionConfig reads reaction_lookback_bars and
// engulfing_minimum_range_atr; both are required (no hidden defaults).
func ParseReactionConfig(params map[string]any) (ReactionConfig, error) {
	lookback, err := Int(params, "reaction_lookback_bars")
	if err != nil {
		return ReactionConfig{}, err
	}
	if lookback < 1 {
		return ReactionConfig{}, fmt.Errorf("reaction_lookback_bars must be >= 1")
	}
	engulfing, err := Float(params, "engulfing_minimum_range_atr")
	if err != nil {
		return ReactionConfig{}, err
	}
	if engulfing < 0 {
		return ReactionConfig{}, fmt.Errorf("engulfing_minimum_range_atr must be >= 0")
	}
	return ReactionConfig{LookbackBars: lookback, EngulfingMinimumRangeATR: engulfing}, nil
}

// ConfirmReaction asks the shared reaction routine whether the timeframe's
// latest closed bars show a confirmed reaction off [low, high] in direction,
// and returns it in the opportunity model's terms. The CHoCH flag is the
// legacy rule: a CHoCH break in the reaction direction at or after the bar
// lookback+1 back from the latest. Bars before notBefore (when the structure
// came into existence) can be neither touch nor confirmation.
func ConfirmReaction(tf *analysiscontext.TimeframeContext, zoneID string, direction market.Direction, low, high, atr float64, notBefore int64, cfg ReactionConfig) *opportunity.ReactionConfirmation {
	return ConfirmReactionWithLookbacks(tf, zoneID, direction, low, high, atr, notBefore, cfg.LookbackBars, cfg.LookbackBars, cfg.EngulfingMinimumRangeATR)
}

// ConfirmReactionWithLookbacks preserves Range Edge's legacy distinction:
// an established barrier may have an older qualifying touch while the actual
// confirmation still has to be recent.
func ConfirmReactionWithLookbacks(tf *analysiscontext.TimeframeContext, zoneID string, direction market.Direction, low, high, atr float64, notBefore int64, touchLookback, confirmationLookback int, engulfingMinimumRangeATR float64) *opportunity.ReactionConfirmation {
	if tf == nil || len(tf.Candles) == 0 {
		return nil
	}
	candles := tf.Candles
	side := "BUY"
	if direction == market.Sell {
		side = "SELL"
	}
	earliest := len(candles) - confirmationLookback - 1
	if earliest < 0 {
		earliest = 0
	}
	minIndex := 0
	for minIndex < len(candles) && candles[minIndex].Time < notBefore {
		minIndex++
	}
	params := reaction.Params{
		MinIndex:  minIndex,
		Direction: side, Low: low, High: high,
		TouchLookback: touchLookback, ConfirmLookback: confirmationLookback,
		HasCHoCH: recentCHoCH(tf.Structure.Breaks, direction, candles[earliest].Time),
		ATR:      atr, EngulfingMinimumRangeATR: engulfingMinimumRangeATR,
	}
	confirmation := reaction.Evaluate(candles, params)
	if confirmation == nil {
		return nil
	}
	// One touch is one reaction episode: if the same touch already produced a
	// confirmation on an earlier bar, a later bar that also looks like a
	// confirmation (follow-through) is not a second reaction.
	if confirmation.ConfirmationIndex > 0 {
		if earlier := reaction.Evaluate(candles[:confirmation.ConfirmationIndex], params); earlier != nil && earlier.TouchIndex == confirmation.TouchIndex {
			return nil
		}
	}
	return &opportunity.ReactionConfirmation{
		ZoneID:              zoneID,
		TouchBarTime:        candles[confirmation.TouchIndex].Time,
		ConfirmationBarTime: candles[confirmation.ConfirmationIndex].Time,
		ReactionType:        "rejection",
		Pattern:             confirmation.Type,
	}
}

func recentCHoCH(breaks []structure.StructureBreak, direction market.Direction, earliestTime int64) bool {
	for _, b := range breaks {
		if b.Event == structure.EventCHoCH && b.Direction == direction && b.Time >= earliestTime {
			return true
		}
	}
	return false
}

// ConfirmedVariant derives the distinct, causally identified confirmed-
// reaction opportunity from a strategy's resting-zone candidate: its own ID
// (built from the zone, touch bar and confirmation bar), created at the
// confirmation bar, carrying the reaction and one more evidence code. The
// resting candidate stays a technical observation; only this variant can
// enter Algo Bot's confirmed-reaction policy.
func ConfirmedVariant(base opportunity.Candidate, rc *opportunity.ReactionConfirmation, zoneID string, formedAt int64, expiryHours float64, evidenceCode string) (opportunity.Candidate, error) {
	id, err := opportunity.DeterministicID(opportunity.Identity{
		Strategy: base.Strategy, StrategyVersion: base.StrategyVersion, Symbol: base.Symbol, Direction: base.Direction,
		SetupKey: fmt.Sprintf("zone:%s:rejection:%d:%d", zoneID, rc.TouchBarTime, rc.ConfirmationBarTime),
	})
	if err != nil {
		return opportunity.Candidate{}, err
	}
	confirmed := base
	confirmed.ID = id
	confirmed.FormedAt = formedAt
	confirmed.CreatedAt = rc.ConfirmationBarTime
	confirmed.ExpiresAt = confirmed.CreatedAt + int64(expiryHours*3600)
	confirmed.Reaction = rc
	confirmed.Evidence = append(append([]opportunity.Evidence(nil), base.Evidence...), opportunity.Evidence{Code: evidenceCode})
	return confirmed, nil
}
