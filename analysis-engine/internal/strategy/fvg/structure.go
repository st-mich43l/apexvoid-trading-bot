package fvg

import (
	"math"
	"sort"
	"strings"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// The fair value gap and its lifecycle, defined here and judged on the bars of the frame the gap was found on. These constants were the shared technique
// lifecycle settings (techniquezone.ProductionTechniqueSettings) the confirmed path inherited; the strategy now owns them.
const (
	epsilonATRFraction       = 0.05
	retestMaxTouches         = 30
	invalidationToleranceATR = 0.5
	sweepReclaimBars         = 6
	maxBreakEpisodes         = 2
	maximumGapATR            = 2.0 // a gap wider than this share of its frame's ATR is a region, not a gap
	minimumGapPips           = 1.0
)

// Refusal reasons of the gap definition, recorded by the engine's refusal counter.
const (
	refusedGapSmall              = "gap_small"
	refusedDisplacementWeak      = "displacement_weak"
	refusedDisplacementDirection = "displacement_direction"
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

// instance is a gap that passed the lifecycle tests: its entry band (clipped to the proximal slice when wide), the whole gap, and where it lives.
type instance struct {
	Side           string
	Low, High      float64 // the entry band
	StructuralLow  float64
	StructuralHigh float64
	EntryClipped   bool
	OriginIndex    int // on the execution frame once bound
	OriginTime     int64
	TouchableFrom  int
	Touches        int
	Mitigated      bool
	Score          float64
	Timeframe      market.Timeframe // "" for an execution-frame gap
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
// closed bars but the last one, which is the bar being decided on.
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
		isGap := false
		for _, source := range z.Sources {
			if strings.HasSuffix(source, "_fvg") {
				isGap = true
			}
		}
		if isGap {
			scores[key{z.OriginIndex, z.Bottom, z.Top}] = z.Score
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

func epsilon(pip, atr float64) float64 { return math.Max(pip, epsilonATRFraction*math.Max(0, atr)) }

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

// notInvalidated: price never accepted beyond the gap's far edge. A close beyond it by more than the tolerance starts an episode, forgiven when a close
// returns inside within sweepReclaimBars; more than maxBreakEpisodes forgiven episodes kill it.
func notInvalidated(side string, low, high float64, bars []market.Candle, origin int, atr, pip float64) bool {
	if len(bars) == 0 || origin < 0 {
		return true
	}
	e := epsilon(pip, atr)
	tolerance := math.Max(e, invalidationToleranceATR*math.Max(atr, 0))
	count := len(bars)
	episodes := 0
	index := maxInt(0, origin+1)
	for index < count {
		close := bars[index].Close
		var beyond bool
		if side == "buy" {
			beyond = close < low-tolerance
		} else {
			beyond = close > high+tolerance
		}
		if !beyond {
			index++
			continue
		}
		reclaimedAt := -1
		for probe := index + 1; probe < minInt(count, index+1+sweepReclaimBars); probe++ {
			var back bool
			if side == "buy" {
				back = bars[probe].Close >= low
			} else {
				back = bars[probe].Close <= high
			}
			if back {
				reclaimedAt = probe
				break
			}
		}
		if reclaimedAt < 0 {
			return false
		}
		episodes++
		if episodes > maxBreakEpisodes {
			return false
		}
		index = reclaimedAt + 1
	}
	return true
}

// spent: a gap that was touched and has either taken too many touches or been invalidated.
func spent(g gap, bars []market.Candle, atr, pip float64) bool {
	if !g.Mitigated {
		return false
	}
	if g.Touches <= 0 || g.Touches > retestMaxTouches {
		return true
	}
	return !notInvalidated(g.Side, g.Low, g.High, bars, g.Origin, atr, pip)
}

func proximalRetest(side string, low, high, price, atr, pip float64) bool {
	e := epsilon(pip, atr)
	proximal := low
	if side == "buy" {
		proximal = high
	}
	return math.Abs(price-proximal) <= e || (low-e <= price && price <= high+e)
}

func notFullyFilled(side string, low, high float64, bars []market.Candle, origin int) bool {
	if len(bars) == 0 || origin < 0 {
		return true
	}
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
func (s *Strategy) definitionRefusal(in instance, origin int, bars []market.Candle, atr float64, series []float64) string {
	displacement := s.cfg.MinimumDisplacementBodyATR > 0 || s.cfg.MinimumDisplacementBodyRatio > 0
	if s.cfg.MinimumGapATR <= 0 && !displacement {
		return ""
	}
	if origin < 2 || origin >= len(bars) {
		return ""
	}
	frameATR := atr
	if len(series) == len(bars) && !math.IsNaN(series[origin]) && series[origin] > 0 {
		frameATR = series[origin]
	}
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

// frameInstances are the gaps of one frame that pass every lifecycle test and the gap definition. proximity is true for the execution frame (price must
// be at the gap now) and false for a higher frame, whose gap is judged as a fact and traded when M5 price reaches it. refused is told about every gap the
// definition refuses.
func (s *Strategy) frameInstances(frame *analysiscontext.LegacyFrame, proximity bool, refused func(reason string)) []instance {
	bars := frame.Bars
	if len(bars) < 10 {
		return nil
	}
	atr := medianATR(frame.ATR)
	price := bars[len(bars)-1].Close
	gaps := detectGaps(bars)
	markTouches(gaps, bars)
	attachScores(gaps, frame)
	var out []instance
	var pending []gap
	for _, g := range gaps {
		if spent(g, bars, atr, s.cfg.PipSize) {
			continue
		}
		pending = append(pending, g)
	}
	for _, g := range pending {
		in := instance{
			Side: g.Side, Low: g.Low, High: g.High, OriginIndex: g.Origin, TouchableFrom: g.Origin + 1,
			Touches: g.Touches, Mitigated: g.Mitigated, Score: g.Score,
		}
		if low, high, clipped := clipEntry(g.Side, g.Low, g.High, s.cfg.EntryMaxWidthPrice); clipped && s.cfg.EntryMaxWidthPrice > 0 {
			in.StructuralLow, in.StructuralHigh, in.EntryClipped = g.Low, g.High, true
			in.Low, in.High = low, high
		}
		if !notInvalidated(in.Side, in.Low, in.High, bars, g.Origin, atr, s.cfg.PipSize) {
			continue
		}
		if proximity && !proximalRetest(in.Side, in.Low, in.High, price, atr, s.cfg.PipSize) {
			continue
		}
		if in.High-in.Low < minimumGapPips*s.cfg.PipSize {
			continue
		}
		if !notFullyFilled(in.Side, in.Low, in.High, bars, g.Origin) {
			continue
		}
		if atr > 0 && in.High-in.Low > math.Max(0, maximumGapATR)*atr {
			continue
		}
		if reason := s.definitionRefusal(in, g.Origin, bars, atr, frame.ATR); reason != "" {
			if refused != nil {
				refused(reason)
			}
			continue
		}
		out = append(out, in)
	}
	return out
}

// bind maps a higher frame's gaps onto the execution frame by TIME: the origin to the first execution bar opening at or after the higher bar that
// completed the gap, and the first bar that may touch it to the first execution bar opening at or after that higher bar's close. A gap older than the
// execution window cannot be anchored there and is dropped.
func bind(tf market.Timeframe, higherBars, execBars []market.Candle, gaps []instance) []instance {
	if len(execBars) == 0 {
		return nil
	}
	minutes, known := tf.Minutes()
	var out []instance
	for _, in := range gaps {
		if in.OriginIndex < 0 || in.OriginIndex >= len(higherBars) {
			continue
		}
		originTime := higherBars[in.OriginIndex].Time
		index := sort.Search(len(execBars), func(i int) bool { return execBars[i].Time >= originTime })
		if index >= len(execBars) || execBars[0].Time > originTime {
			continue
		}
		touchable := 0
		if formed := in.TouchableFrom - 1; in.TouchableFrom > 0 && formed < len(higherBars) && known {
			closed := higherBars[formed].Time + int64(minutes)*60
			touchable = sort.Search(len(execBars), func(i int) bool { return execBars[i].Time >= closed })
		}
		in.OriginIndex, in.OriginTime, in.Timeframe, in.TouchableFrom = index, originTime, tf, touchable
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
