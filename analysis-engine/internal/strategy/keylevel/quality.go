package keylevel

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// QualityModel is the version of the scoring rule below. A change to a dimension or a range is a new version.
const QualityModel = "key_level.quality.v1"

// minimumLevelHistory is the fewest bars between a level's first visit and the reaction for its integrity to be measured at all.
const minimumLevelHistory = 10

// qualityInputs are the facts of one qualified Key Level reaction that the score reads. Everything here exists when the
// opportunity is created: closed bars up to the confirmation, the level, the engine's structural read, the entry and the target.
type qualityInputs struct {
	bars         []market.Candle // the frame's closed bars, the last being the latest closed bar
	touchIndex   int             // bar that first reached the band
	confirmIndex int             // bar that confirmed the rejection
	level, band  float64
	atr          float64
	buy          bool
	price        float64 // latest close
	reference    float64 // proximal edge of the entry band
	invalidation float64
	target       float64
	structural   bool   // the target is a real opposing liquidity, not a fixed reward:risk
	localTrend   string // the frame's structure: "up", "down" or "range"
	choch        bool   // a change of character in the trade's direction within the reaction window
	htfAligned   bool
	htfTouches   int     // touches of the best higher-timeframe level at this price (H1 weighted fully, M15 by half), 0 when none/unavailable
	htfWeight    float64 // 1 for an H1 level, 0.5 for an M15 level
}

// scoreQuality is Key Level's technical quality: how strong this level and this reaction are as a Key Level thesis. Six dimensions,
// each from a different source, each in [0, 1], averaged without weights (see package quality).
//
//	level_significance  independent episodes at the level, counting the present reaction: Peak(E; 1, 3, 12, 30). One test is a price,
//	                    three are a level, about one a quarter-hour over the 12-hour window is still a level, and a price visited every
//	                    25 minutes is a chop zone whose resting interest has been spent, so more is not better.
//	htf_level           the same price is a higher-timeframe level: Ramp(touches; 1, 3), an H1 level in full, an M15 level by half.
//	level_integrity     the share of bars between the level's first visit and the reaction that closed beyond the defended side:
//	                    Fall(share; 0.05, 0.4), and 0 without at least ten bars of history. A level price keeps closing through is
//	                    not defended; what price did before it ever reached the level is not counted.
//	rejection           the reaction candles: half the refusal wick of the touch bar Ramp(wick; 0.2, 0.6), half the close strength
//	                    of the confirmation bar Ramp(close; 0.5, 0.9).
//	entry_location      how far price has already left the proximal edge, in ATR: Fall(d; 0.25, 1.5). A late entry is a worse one.
//	structure           the direction of the market, graded, not added: with the local structure and the higher timeframe 1.0;
//	                    with the structure only 0.7; a confirmed reversal (change of character) 0.9 with the higher timeframe and
//	                    0.7 without; a range 0.6 / 0.5; against the structure with no reversal evidence 0.3 / 0.1.
//	room                reward:risk to the nearest opposing liquidity, 1R scoring 0 and 3R scoring 1; 0 for a fixed-ratio target.
func scoreQuality(in qualityInputs) opportunity.StrategyQuality {
	m := quality.New(QualityModel)

	prior := in.bars
	if in.touchIndex >= 0 && in.touchIndex <= len(in.bars) {
		prior = in.bars[:in.touchIndex]
	}
	episodes := quality.Episodes(prior, in.level, in.band) + 1 // the reaction itself is the latest episode
	m.Score("level_significance", quality.Peak(float64(episodes), 1, 3, 12, 30))
	m.Measure("level_episodes", float64(episodes))

	htf := 0.0
	if in.htfTouches > 0 {
		htf = in.htfWeight * quality.Ramp(float64(in.htfTouches), 1, 3)
	}
	m.Score("htf_level", htf)
	m.Measure("htf_level_touches", float64(in.htfTouches))

	through, failed := 0, math.NaN()
	if first := quality.FirstVisit(prior, in.level, in.band); first >= 0 && len(prior)-first >= minimumLevelHistory {
		span := prior[first:]
		through = quality.ClosesThrough(span, in.level, in.band, in.buy)
		failed = float64(through) / float64(len(span))
	}
	m.Score("level_integrity", quality.Fall(failed, 0.05, 0.4))
	m.Measure("closes_through_level", float64(through))
	m.Measure("closes_through_share", failed)

	m.Score("rejection", quality.RejectionScore(in.bars, in.touchIndex, in.confirmIndex, in.buy))

	m.Score("entry_location", quality.LocationScore(in.price, in.reference, in.atr))
	if in.atr > 0 {
		m.Measure("entry_chase_atr", math.Abs(in.price-in.reference)/in.atr)
	}

	m.Score("structure", quality.StructureScore(in.buy, in.localTrend, in.choch, in.htfAligned))

	room, rewardRisk := quality.GeometryRoom(in.reference, in.invalidation, in.target, in.structural)
	m.Score("room", room)
	m.Measure("room_reward_risk", rewardRisk)

	return opportunity.StrategyQuality{Overall: m.Overall(), Components: m.Components()}
}

// higherTimeframeLevel finds the best higher-timeframe level at this price: the touches of an H1 level (weight 1) or an M15 level
// (weight 0.5) within half of that frame's own ATR of the level. Unavailable frames contribute nothing.
func higherTimeframeLevel(ctx *context.MarketContext, levelPrice float64, contract string, settings strategyutil.LegacyDetectorSettings) (touches int, weight float64) {
	for _, candidate := range []struct {
		tf     market.Timeframe
		weight float64
	}{{market.H1, 1}, {market.M15, 0.5}} {
		frame, ok := strategyutil.NewLegacyDetectorForFrame(ctx, candidate.tf, settings)
		if !ok {
			continue
		}
		levels := frame.Frame.Levels
		if contract == contractProfitWeek {
			levels = frame.Frame.SwingLevels
		}
		for _, l := range levels {
			if math.Abs(l.Price-levelPrice) <= 0.5*frame.ATR && l.Touches > 0 && candidate.weight*float64(min(l.Touches, 3)) > weight*float64(min(touches, 3)) {
				touches, weight = l.Touches, candidate.weight
			}
		}
	}
	return touches, weight
}
