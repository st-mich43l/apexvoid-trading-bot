package fvg

import (
	"math"
	"sort"
	"strings"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// retestMaxTouches is how many touches a gap may have taken and still be tradable (unchanged from the shared technique lifecycle this strategy inherited:
// the touch caps are a separate decision, see docs/strategies/fvg.md).
const retestMaxTouches = 30

// Refusal reasons of this strategy, counted by the engine as strategy_refused{strategy="fvg", reason}.
const (
	refusedGapSmall              = "gap_small"
	refusedDisplacementWeak      = "displacement_weak"
	refusedDisplacementDirection = "displacement_direction"
	refusedGapClosedThrough      = "gap_closed_through"
	refusedNoRejection           = "no_rejection"
	refusedStaleConfirmation     = "stale_confirmation"
	refusedNoTargetRoom          = "no_target_room"
	refusedEntryAtLegExtreme     = "entry_at_leg_extreme"
)

// zoneBand is a price band with the facts the qualification reads.
type zoneBand struct {
	Bottom, Top float64
	Side        string // "demand" (buy) | "supply" (sell)
	Score       float64
	Touches     int
}

func (z zoneBand) Low() float64  { return z.Bottom }
func (z zoneBand) High() float64 { return z.Top }

// gap is one three-candle fair value gap on a frame: its bounds, the index of the third candle, the touches it has taken and its zone score.
type gap struct {
	Side      string // "buy" | "sell"
	Low, High float64
	Origin    int // index of the third candle on its own frame
	Touches   int
	Mitigated bool
	Score     float64
}

// instance is a gap that passed the lifecycle tests and the gap definition: its entry band (clipped to the proximal slice when wide), the whole gap, and
// where it lives. OriginIndex and TouchableFrom are execution-frame (M5) indexes once the gap is bound; FrameOrigin indexes the gap's own frame.
type instance struct {
	Side           string
	Low, High      float64 // the entry band
	StructuralLow  float64
	StructuralHigh float64
	EntryClipped   bool
	OriginIndex    int
	OriginTime     int64
	TouchableFrom  int
	Touches        int
	Mitigated      bool
	Score          float64
	Timeframe      market.Timeframe
	Frame          *analysiscontext.LegacyFrame // the frame the gap was found on
	FrameOrigin    int
}

func (i instance) structural() (float64, float64) {
	if i.EntryClipped {
		return i.StructuralLow, i.StructuralHigh
	}
	return i.Low, i.High
}

// detectGaps finds the three-bar gaps of a frame (bullish: the first candle's high is below the third candle's low).
func detectGaps(bars []market.Candle) []gap {
	var gaps []gap
	for i := 2; i < len(bars); i++ {
		older, cur := bars[i-2], bars[i]
		if older.High < cur.Low {
			gaps = append(gaps, gap{Side: "buy", Low: older.High, High: cur.Low, Origin: i})
		}
		if older.Low > cur.High {
			gaps = append(gaps, gap{Side: "sell", Low: cur.High, High: older.Low, Origin: i})
		}
	}
	return gaps
}

// markTouches counts, for each gap, the times price re-entered it after the candle that completed it (a rising edge into the band), over the frame's
// closed bars but the last one.
func markTouches(gaps []gap, bars []market.Candle) {
	end := len(bars) - 1
	if end < 0 {
		end = 0
	}
	for k := range gaps {
		g := &gaps[k]
		touches, prev := 0, false
		for i := g.Origin + 1; i < end; i++ {
			touched := bars[i].Low <= g.High && bars[i].High >= g.Low
			if touched && !prev {
				touches++
			}
			prev = touched
		}
		g.Touches = touches
		g.Mitigated = touches > 0
	}
}

// attachScores reads each gap's zone score (the frame's multi-factor zone score: key levels, liquidity, higher-timeframe overlap, sessions) from the
// frame's scored zones, a fact of the frame the strategy does not recompute, and orders the gaps the way the frame orders its zones: score first, fewer
// touches first, lower first, otherwise chronological.
func attachScores(gaps []gap, frame *analysiscontext.LegacyFrame) {
	type key struct {
		origin    int
		low, high float64
	}
	scores := map[key]float64{}
	for _, z := range frame.TechniqueZones {
		for _, source := range z.Sources {
			if strings.HasSuffix(source, "_fvg") {
				scores[key{z.OriginIndex, z.Bottom, z.Top}] = z.Score
				break
			}
		}
	}
	for k := range gaps {
		gaps[k].Score = scores[key{gaps[k].Origin, gaps[k].Low, gaps[k].High}]
	}
	sort.SliceStable(gaps, func(i, j int) bool {
		a, b := gaps[i], gaps[j]
		if a.Score != b.Score {
			return a.Score > b.Score
		}
		if a.Touches != b.Touches {
			return a.Touches < b.Touches
		}
		return a.Low < b.Low
	})
}

// medianATR is the median of a frame's ATR series (NaN skipped), 1 when there is none.
func medianATR(series []float64) float64 {
	clean := make([]float64, 0, len(series))
	for _, v := range series {
		if !math.IsNaN(v) {
			clean = append(clean, v)
		}
	}
	if len(clean) == 0 {
		return 1
	}
	sort.Float64s(clean)
	n := len(clean)
	if n%2 == 1 {
		return clean[n/2]
	}
	return (clean[n/2-1] + clean[n/2]) / 2
}

// atrAt is the frame's ATR at bar i (the series value when aligned and valid, else the fallback).
func atrAt(series []float64, bars []market.Candle, i int, fallback float64) float64 {
	if len(series) == len(bars) && i >= 0 && i < len(series) && !math.IsNaN(series[i]) && series[i] > 0 {
		return series[i]
	}
	return fallback
}

// closedThrough reports whether any close of the gap's own frame after the candle that completed it went beyond the gap's far edge (a buy gap's low, a
// sell gap's high) by more than CloseThroughBufferATR of that bar's ATR. There is no reclaim forgiveness: a wick that closes back inside is the sweep, a
// close is the break.
func (s *Strategy) closedThrough(side string, structLow, structHigh float64, bars []market.Candle, series []float64, origin int, fallbackATR float64) bool {
	for i := origin + 1; i < len(bars); i++ {
		margin := s.cfg.CloseThroughBufferATR * atrAt(series, bars, i, fallbackATR)
		if side == "buy" && bars[i].Close < structLow-margin-1e-9 {
			return true
		}
		if side == "sell" && bars[i].Close > structHigh+margin+1e-9 {
			return true
		}
	}
	return false
}

func notFullyFilled(side string, low, high float64, bars []market.Candle, origin int) bool {
	for i := origin + 1; i < len(bars); i++ {
		if side == "buy" {
			if bars[i].Low <= low {
				return false
			}
		} else if bars[i].High >= high {
			return false
		}
	}
	return true
}

// clipEntry cuts a gap wider than maxWidth to its proximal slice (a buy keeps the top, a sell the bottom).
func clipEntry(side string, low, high, maxWidth float64) (float64, float64, bool) {
	if math.IsNaN(low) || math.IsNaN(high) || high <= low || math.IsNaN(maxWidth) || maxWidth <= 0 || high-low <= maxWidth+1e-12 {
		return low, high, false
	}
	if side == "sell" {
		return low, low + maxWidth, true
	}
	return high - maxWidth, high, true
}

// definitionRefusal judges a gap on its own three candles and the ATR of the frame it comes from AS OF its third candle: width, the middle candle's body
// and its direction. "" when the gap passes.
func (s *Strategy) definitionRefusal(in instance, bars []market.Candle, atr float64, series []float64) string {
	displacement := s.cfg.MinimumDisplacementBodyATR > 0 || s.cfg.MinimumDisplacementBodyRatio > 0
	if s.cfg.MinimumGapATR <= 0 && !displacement {
		return ""
	}
	origin := in.FrameOrigin
	if origin < 2 || origin >= len(bars) {
		return ""
	}
	frameATR := atrAt(series, bars, origin, atr)
	low, high := in.structural()
	if s.cfg.MinimumGapATR > 0 && high-low+1e-9 < s.cfg.MinimumGapATR*frameATR {
		return refusedGapSmall
	}
	if !displacement {
		return ""
	}
	middle := bars[origin-1]
	body, span := math.Abs(middle.Close-middle.Open), middle.High-middle.Low
	if s.cfg.MinimumDisplacementBodyATR > 0 && body+1e-9 < s.cfg.MinimumDisplacementBodyATR*frameATR {
		return refusedDisplacementWeak
	}
	if s.cfg.MinimumDisplacementBodyRatio > 0 && (span <= 0 || body/span+1e-9 < s.cfg.MinimumDisplacementBodyRatio) {
		return refusedDisplacementWeak
	}
	if (in.Side == "buy" && !(middle.Close > middle.Open)) || (in.Side == "sell" && !(middle.Close < middle.Open)) {
		return refusedDisplacementDirection
	}
	return ""
}

// structureInstances are the gaps of one structure frame that are still gaps: not spent, not closed through, not filled, a real width, and passing the gap
// definition. Where price is now is not asked here (a structure is a fact). refuse is told, once per gap, why a gap that reached the definition or the
// close-through test did not survive.
func (s *Strategy) structureInstances(frame *analysiscontext.LegacyFrame, tf market.Timeframe, refuse func(reason string)) []instance {
	bars := frame.Bars
	if len(bars) < 10 {
		return nil
	}
	atr := medianATR(frame.ATR)
	gaps := detectGaps(bars)
	markTouches(gaps, bars)
	attachScores(gaps, frame)
	var out []instance
	for _, g := range gaps {
		if g.Mitigated && (g.Touches <= 0 || g.Touches > retestMaxTouches) {
			continue
		}
		in := instance{
			Side: g.Side, Low: g.Low, High: g.High, StructuralLow: g.Low, StructuralHigh: g.High, FrameOrigin: g.Origin, TouchableFrom: g.Origin + 1,
			Touches: g.Touches, Mitigated: g.Mitigated, Score: g.Score, Timeframe: tf, Frame: frame,
		}
		if low, high, clipped := clipEntry(g.Side, g.Low, g.High, s.cfg.EntryMaxWidthPrice); clipped {
			in.EntryClipped, in.Low, in.High = true, low, high
		}
		if in.High-in.Low < s.cfg.PipSize {
			continue
		}
		if atr > 0 && in.High-in.Low > math.Max(0, s.cfg.MaximumZoneATR)*atr {
			continue
		}
		// Judged before the fill test: a close beyond the far edge has always traded through the whole gap, so after the fill test this rule could never be
		// reached and a gap that died by a close would be dropped without a reason.
		if s.closedThrough(in.Side, g.Low, g.High, bars, frame.ATR, g.Origin, atr) {
			refuse(refusedGapClosedThrough)
			continue
		}
		if !notFullyFilled(in.Side, in.Low, in.High, bars, g.Origin) {
			continue
		}
		if reason := s.definitionRefusal(in, bars, atr, frame.ATR); reason != "" {
			refuse(reason)
			continue
		}
		out = append(out, in)
	}
	return out
}

// bind maps a structure frame's gaps onto the execution frame by TIME: the origin to the first execution bar opening at or after the structure bar that
// completed the gap, and the first bar that may touch it to the first execution bar opening at or after that bar's close. A gap older than the execution
// window cannot be anchored there and is dropped.
func bind(tf market.Timeframe, structureBars, execBars []market.Candle, gaps []instance) []instance {
	if len(execBars) == 0 {
		return nil
	}
	minutes, known := tf.Minutes()
	var out []instance
	for _, in := range gaps {
		if in.FrameOrigin < 0 || in.FrameOrigin >= len(structureBars) {
			continue
		}
		originTime := structureBars[in.FrameOrigin].Time
		index := sort.Search(len(execBars), func(i int) bool { return execBars[i].Time >= originTime })
		if index >= len(execBars) || execBars[0].Time > originTime {
			continue
		}
		touchable := 0
		if known {
			closed := originTime + int64(minutes)*60
			touchable = sort.Search(len(execBars), func(i int) bool { return execBars[i].Time >= closed })
		}
		in.OriginIndex, in.OriginTime, in.TouchableFrom = index, originTime, touchable
		out = append(out, in)
	}
	return out
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
