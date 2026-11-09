package keylevel

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// QualityModel is the version of the scoring rule below. A change to a dimension or a range is a new version.
const QualityModel = "key_level.quality.v2"

// minimumLevelHistory is the fewest bars between a level's first visit and the reaction for its integrity to be measured at all.
const minimumLevelHistory = 10

// qualityInputs are the facts of one qualified Key Level reaction that the score reads. Everything here exists when the
// opportunity is created: closed bars up to the confirmation, the level, the engine's structural read, the entry and the target.
type qualityInputs struct {
	bars          []market.Candle // the frame's closed bars, the last being the latest closed bar
	touchIndex    int             // bar that first reached the band
	confirmIndex  int             // bar that confirmed the rejection
	level, band   float64
	atr           float64
	buy           bool
	price         float64 // latest close
	reference     float64 // proximal edge of the entry band
	invalidation  float64
	target        float64
	effectiveRisk float64 // the stop distance the trade is exposed to: the structural one, or the instrument floor when that is wider (StopBudget.EffectiveRisk)
	structural    bool    // the target is a real opposing liquidity, not a fixed reward:risk
	localTrend    string  // the frame's structure: "up", "down" or "range"
	choch         bool    // a change of character in the trade's direction on a bar from the touch bar to the confirmation bar (reactionCHoCH)
	htfAligned    bool
	htfTouches    int     // touches of the best higher-timeframe level at this price (H1 weighted fully, M15 by half), 0 when none/unavailable
	htfWeight     float64 // 1 for an H1 level, 0.5 for an M15 level

	// The level's own facts, published as measurements only (forward labels for a later decision; never a score, a gate or a star).
	levelKind string                // the level's kind as the strategy read it ("reaction", "round")
	levelBand float64               // the level's own band (price units)
	swings    []techniquezone.Swing // the frame's swings, whose members within the band say how the level formed
	coverage  float64               // share of the window's [low, high] inside any level band at this evaluation

	deepEntryPips float64 // how far the proximal edge of the entry band moved to meet the instrument stop cap (0 when the whole band fits); informational
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
//	entry_location      how far price has already run away from the proximal edge in the trade's favour, in ATR: Fall(d; 0.25, 1.5).
//	                    A late entry is a worse one; price back inside the band is a better fill, not a late one, and scores in full (v2).
//	structure           the direction of the market, graded, not added: with the local structure and the higher timeframe 1.0;
//	                    with the structure only 0.7; a confirmed reversal (change of character) 0.9 with the higher timeframe and
//	                    0.7 without; a range 0.6 / 0.5; against the structure with no reversal evidence 0.3 / 0.1. The change of
//	                    character counts only when it broke on a bar of the reaction itself, from the touch to the confirmation (v2).
//	room                reward:risk to the nearest opposing liquidity against the risk the trade is actually exposed to (the structural
//	                    stop distance, or the instrument's floor when execution widens it), 1R scoring 0 and 3R scoring 1; 0 for a
//	                    fixed-ratio target, which is a reward the strategy chose, not one the market offers (v2).
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

	m.Score("entry_location", entryLocation(in.buy, in.price, in.reference, in.atr))
	if in.atr > 0 {
		m.Measure("entry_chase_atr", math.Abs(in.price-in.reference)/in.atr)
	}

	m.Score("structure", quality.StructureScore(in.buy, in.localTrend, in.choch, in.htfAligned))

	// Room is scored against the risk execution takes: the structural stop distance, widened to the instrument floor where the structure is tighter. The structural
	// ratio is still published, as a measurement.
	_, structuralRR := quality.GeometryRoom(in.reference, in.invalidation, in.target, in.structural)
	m.Measure("room_reward_risk", structuralRR)
	risk := in.effectiveRisk
	if !(risk > 0) {
		risk = math.Abs(in.reference - in.invalidation)
	}
	reward := math.Abs(in.target - in.reference)
	if risk > 0 {
		m.Measure("room_r_effective", reward/risk)
	}
	m.Score("room", quality.Room(reward, risk, in.structural))

	// Forward labels: what kind of level this was, so realised outcomes can later be cut by it. Measurements only.
	if in.levelKind == "round" {
		m.Measure("level_kind", 1)
	} else {
		m.Measure("level_kind", 0)
	}
	m.Measure("level_round_class", roundClass(in.level))
	origin, age := levelOrigin(in.swings, in.level, in.levelBand, in.confirmIndex)
	m.Measure("level_origin", origin)
	m.Measure("level_age_bars", age)
	if in.atr > 0 {
		m.Measure("level_band_atr", in.levelBand/in.atr)
	}
	m.Measure("level_coverage", in.coverage)
	m.Measure("deep_entry_pips", in.deepEntryPips)

	return opportunity.StrategyQuality{Overall: m.Overall(), Components: m.Components()}
}

// entryLocation is how far the latest close has already run away from the proximal edge in the trade's favour (above it for a buy, below it for a sell), in ATR,
// Fall(d; 0.25, 1.5). Price that has left the edge is a late entry. Price back inside the band, on the far side of the edge, is a better fill than the edge and is
// not late: it scores in full (its cost, a stop that is nearer, is room's, not location's). Without a volatility there is no measurement (0).
func entryLocation(buy bool, price, reference, atr float64) float64 {
	if !(atr > 0) || math.IsNaN(price) || math.IsInf(price, 0) || math.IsNaN(reference) || math.IsInf(reference, 0) {
		return 0
	}
	ran := price - reference
	if !buy {
		ran = reference - price
	}
	return quality.Fall(math.Max(0, ran)/atr, 0.25, 1.5)
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

// roundClass is how round the level's PRICE is: 0 none, 1 a multiple of 5, 2 of 10, 3 of 50, 4 of 100 (the most round class that holds, to 1e-6). A merged level whose price was averaged is not on a
// handle, whatever its kind says: this judges the price.
func roundClass(price float64) float64 {
	for _, c := range []struct {
		step  float64
		class float64
	}{{100, 4}, {50, 3}, {10, 2}, {5, 1}} {
		ratio := price / c.step
		if math.Abs(ratio-math.Round(ratio))*c.step <= 1e-6 {
			return c.class
		}
	}
	return 0
}

// levelOrigin reads how a level formed from the frame's swings within its band, as known at the confirmation bar (upto): +1 when swing lows formed it, -1 when swing highs did, 0 when mixed (or when no swing lies
// in the band), and the age in bars, at the confirmation, of its newest member swing (upto + 1, the bars read, when it has no member, so the measurement is always a finite number). Bars after the confirmation
// change nothing, like every other published component.
func levelOrigin(swings []techniquezone.Swing, price, band float64, upto int) (origin, ageBars float64) {
	highs, lows, newest := 0, 0, -1
	for _, s := range swings {
		if s.Index > upto || math.Abs(s.Price-price) > band {
			continue
		}
		if s.Kind == "high" {
			highs++
		} else {
			lows++
		}
		if s.Index > newest {
			newest = s.Index
		}
	}
	switch {
	case lows > highs:
		origin = 1
	case highs > lows:
		origin = -1
	}
	if newest < 0 {
		return origin, float64(upto + 1)
	}
	return origin, float64(upto - newest)
}

// levelCoverage is the share of the window's [low, high] that lies inside any level's band (the union of [price - band, price + band] of every level the strategy considers). A selective definition covers
// little; a level on every second dollar covers everything and so selects nothing.
func levelCoverage(levels []techniquezone.Level, minimumTouches int, bars []market.Candle) float64 {
	if len(bars) == 0 {
		return math.NaN()
	}
	low, high := bars[0].Low, bars[0].High
	for _, b := range bars {
		low, high = math.Min(low, b.Low), math.Max(high, b.High)
	}
	if !(high > low) {
		return math.NaN()
	}
	type span struct{ lo, hi float64 }
	var spans []span
	for _, l := range levels {
		if l.Touches < maxInt(1, minimumTouches) {
			continue
		}
		lo, hi := math.Max(low, l.Price-l.Band), math.Min(high, l.Price+l.Band)
		if hi > lo {
			spans = append(spans, span{lo, hi})
		}
	}
	sort.Slice(spans, func(i, j int) bool { return spans[i].lo < spans[j].lo })
	covered, edge := 0.0, low
	for _, sp := range spans {
		if sp.hi <= edge {
			continue
		}
		covered += sp.hi - math.Max(sp.lo, edge)
		edge = sp.hi
	}
	return covered / (high - low)
}
