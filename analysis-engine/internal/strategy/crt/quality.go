package crt

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "crt.quality.v2"

// qualityContext are the facts of the surrounding market the score reads besides the setup itself.
type qualityContext struct {
	price      float64 // latest M5 close
	localTrend string  // the M5 structure: "up", "down" or "range"
	choch      bool    // a change of character in the trade's direction on a bar of the CRT sequence before the confirming candle (setupCHoCH)
	htfAligned bool
}

// score adds the CRT dimensions to the model m, each in [0, 1], each from a different event of the sequence, averaged without
// weights. Invalidation beyond the manipulation extreme is a validity rule of the setup (it is refused otherwise), not a score.
//
//	range_validity   the H1 reference range in H1 ATR: Peak(r; 0.6, 1.2, 3.5, 7). A sliver proves nothing; a very wide range is a trend.
//	sweep_depth      how far the manipulation went past the swept edge, in ATR: Peak(d; 0.05, 0.2, 1.0, 2.5). A wick that barely
//	                 pokes is not a raid of resting orders, and a very deep one is a breakout.
//	reclaim          the first close back inside the range: half Ramp(depth in ATR; 0.05, 0.6), half Fall(bars after the sweep; 0, 12).
//	structure_shift  the later M5 break of the counter-trend swing: Ramp(its displacement in ATR; 0.3, 1.2) when it is a later candle
//	                 than the reclaim, half of that when it is the reclaim candle itself (one candle is not two confirmations); 0
//	                 without a shift. Its candle is credited here and nowhere else.
//	range_position   (only with the mss_retest entry, v2) where in the reference range the entry sits for the thesis (buy low, sell
//	                 high): Fall(p; 0.25, 0.7). With reclaim_retest the entry band is centred on the swept edge by definition, so its
//	                 position is a consequence of the entry rule and not a strength: it is published as entry_range_position instead
//	                 of scoring 1.0 for every setup.
//	entry_location   how far the latest close has run from the proximal edge of the entry band in the trade's favour, in ATR:
//	                 Fall(d; 0.25, 1.5); price back inside the band is a better fill and scores in full (v2).
//	structure        the direction of the market, graded (quality.StructureScore). A change of character counts only when it broke on a
//	                 bar of the CRT sequence before the confirming candle (v2): from the sweep candle up to, but not including, the
//	                 structure shift, which structure_shift already credits.
//	room             reward over the risk the trade is actually exposed to, 1R scoring 0 and 3R scoring 1 (v2): the structural stop
//	                 distance, or the instrument's floor when execution widens a tighter stop.
func (s *Strategy) score(m *quality.Model, setup Setup, qc qualityContext) {
	m.Score("range_validity", quality.Peak(setup.Anchor.RangeATR, 0.6, 1.2, 3.5, 7))
	m.Score("sweep_depth", quality.Peak(setup.Sweep.DepthATR, 0.05, 0.2, 1.0, 2.5))
	m.Score("reclaim", 0.5*quality.Ramp(setup.Reclaim.DepthATR, 0.05, 0.6)+0.5*quality.Fall(float64(setup.Reclaim.BarsAfterSweep), 0, 12))
	shift := 0.0
	if setup.Shift != nil {
		shift = quality.Ramp(setup.Shift.DisplacementATR, 0.3, 1.2)
		if setup.Shift.BarsAfterReclaim < 1 {
			shift *= 0.5
		}
	}
	m.Score("structure_shift", shift)

	buy := setup.Direction == market.Buy
	reference := setup.EntryHigh
	if !buy {
		reference = setup.EntryLow
	}
	position := entryRangePosition(setup)
	if s.cfg.EntryModel == EntryMSSRetest {
		m.Score("range_position", quality.Fall(position, 0.25, 0.7))
	}
	m.Measure("entry_range_position", position)
	m.Score("entry_location", entryLocation(buy, qc.price, reference, setup.M5ATR))
	if setup.M5ATR > 0 {
		m.Measure("entry_chase_atr", math.Abs(qc.price-reference)/setup.M5ATR)
	}
	m.Score("structure", quality.StructureScore(buy, qc.localTrend, qc.choch, qc.htfAligned))
	if qc.choch {
		m.Measure("choch_in_sequence", 1)
	} else {
		m.Measure("choch_in_sequence", 0)
	}

	// Room is scored against the risk execution takes: the structural stop distance, widened to the instrument floor where the structure is
	// tighter. The structural ratio is still published (technical_rr), next to the executed one.
	budget := strategyutil.StopBudget{MinPips: s.cfg.ExecutionStopMinPips, PipSize: s.cfg.PipSize}
	risk := budget.EffectiveRisk(reference, setup.Stop)
	reward := math.Abs(setup.Target - reference)
	m.Score("room", quality.Room(reward, risk, true))
	if risk > 0 {
		m.Measure("room_r_effective", reward/risk)
	}
}

// entryRangePosition is where the proximal edge of the entry band sits in the reference range for the thesis: 0 at the swept edge (the
// discount of a buy, the premium of a sell), 1 at the opposite one. It is mirror-symmetric: a SELL is measured from the H1 high. NaN
// for a degenerate range.
func entryRangePosition(setup Setup) float64 {
	width := setup.Anchor.Width()
	if !(width > 0) {
		return math.NaN()
	}
	if setup.Direction == market.Buy {
		return (setup.EntryHigh - setup.Anchor.Low) / width
	}
	return (setup.Anchor.High - setup.EntryLow) / width
}

// entryLocation is how far the latest close has run away from the proximal edge of the entry band in the trade's favour (above it for a buy, below it for a
// sell), in ATR, Fall(d; 0.25, 1.5): price that has left the entry is a late one. Price back inside the band, on the far side of the edge, is a better fill than
// the edge and is not late, so it scores in full. Without a volatility there is no measurement (0).
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

// setupCHoCH reports whether the engine's M5 frame broke character in the trade's direction on a bar that belongs to this setup's sequence before its confirming
// candle: from the sweep candle (the manipulation) up to, but not including, the candle that confirmed it. The confirming candle is the structure shift (or, in the
// reclaim-only baseline mode, the reclaim), which structure_shift and reclaim credit as their own event; a break on it is that same fact counted twice, and a break
// before the sweep is the market's earlier history, not this setup's.
func setupCHoCH(d *strategyutil.LegacyDetector, setup Setup) bool {
	wanted := "down"
	if setup.Direction == market.Buy {
		wanted = "up"
	}
	for _, brk := range d.Frame.Breaks {
		if brk.Kind != "CHoCH" || brk.Direction != wanted || brk.Index < 0 || brk.Index >= len(d.Frame.Bars) {
			continue
		}
		if t := d.Frame.Bars[brk.Index].Time; t >= setup.Sweep.BarTime && t < setup.ConfirmedAt {
			return true
		}
	}
	return false
}
