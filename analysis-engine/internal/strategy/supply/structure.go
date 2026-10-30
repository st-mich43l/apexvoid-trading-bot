package supply

import (
	"math"
	"sort"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// The supply zone and its lifecycle, defined here and judged on the bars of the frame the zone was found on. These constants were the shared technique
// settings (techniquezone.ProductionTechniqueSettings and the legacy read's displacement leaves) the confirmed path inherited; the strategy now owns them.
const (
	epsilonATRFraction       = 0.05
	retestMaxTouches         = 30
	invalidationToleranceATR = 0.5
	sweepReclaimBars         = 6
	maxBreakEpisodes         = 2
	baseCandles              = 3 // the zone is the range of this many candles before the departure
)

// Refusal reason of this strategy, counted by the engine as strategy_refused{strategy="supply", reason}.
const refusedNoTargetRoom = "no_target_room" // the nearest obstacle is closer than minimum_target_r x the risk

// zoneBand is a price band with the facts the qualification reads.
type zoneBand struct {
	Bottom, Top float64
	Side        string // "supply"
	Score       float64
	Touches     int
}

func (z zoneBand) Low() float64  { return z.Bottom }
func (z zoneBand) High() float64 { return z.Top }

// leg is a run of same-direction candles that moved far and strongly: the departure from the zone.
type leg struct {
	Start, End int
	Direction  string // "up" | "down"
}

// zone is one supply zone on a frame: the range of the candles before a strong downward departure.
type zone struct {
	Low, High  float64
	Origin     int // index of the last base candle on its frame
	LegStart   int // index of the first candle of the departure leg
	BreakIndex int // the last candle of the departure leg
	Touches    int
	Mitigated  bool
	Score      float64
	rank       int
}

// instance is a zone that passed the lifecycle tests: its entry band (clipped to the proximal slice when wide), the whole zone, and where it lives.
type instance struct {
	Low, High      float64 // the entry band
	StructuralLow  float64
	StructuralHigh float64
	EntryClipped   bool
	OriginIndex    int // the zone's last base candle on the execution frame once bound, -1 when it precedes the execution window
	OriginTime     int64
	TouchableFrom  int
	Touches        int
	Mitigated      bool
	Score          float64
	Timeframe      market.Timeframe
	Frame          *analysiscontext.LegacyFrame // the frame the zone was found on
	FrameOrigin    int                          // the zone's last base candle on that frame
	FrameConfirmed int                          // the last candle of the departure on that frame
	LegStart       int                          // the departure's first candle on that frame
}

func (i instance) structural() (float64, float64) {
	if i.EntryClipped {
		return i.StructuralLow, i.StructuralHigh
	}
	return i.Low, i.High
}

func candleDirection(c market.Candle) string {
	switch {
	case c.Close > c.Open:
		return "up"
	case c.Close < c.Open:
		return "down"
	}
	return ""
}

// bodyFraction is the body's share of the candle's range (0 for a candle without range).
func bodyFraction(c market.Candle) float64 {
	span := c.High - c.Low
	if span <= 0 {
		return 0
	}
	return math.Abs(c.Close-c.Open) / span
}

func atrAt(series []float64, i int, fallback float64) float64 {
	if i >= 0 && i < len(series) && !math.IsNaN(series[i]) && !math.IsInf(series[i], 0) && series[i] > 0 {
		return series[i]
	}
	return fallback
}

// displacement finds the legs: runs of same-direction candles whose net move clears displacement_atr_mult x ATR and whose bodies are mostly strong.
func (s *Strategy) displacement(bars []market.Candle, atr []float64) []leg {
	var legs []leg
	start, dir := -1, ""
	flush := func(end int) {
		if start < 0 || dir == "" || end < start {
			return
		}
		open, close := bars[start].Open, bars[end].Close
		size := open - close
		if dir == "up" {
			size = close - open
		}
		if size < atrAt(atr, end, 1)*s.cfg.DisplacementATRMult {
			return
		}
		strong := 0
		for i := start; i <= end; i++ {
			if bodyFraction(bars[i]) >= s.cfg.MomentumBodyFraction {
				strong++
			}
		}
		if need := maxInt(1, (end-start+1)/2); strong < need {
			return
		}
		legs = append(legs, leg{Start: start, End: end, Direction: dir})
	}
	for i, b := range bars {
		cur := candleDirection(b)
		switch {
		case cur == "":
			flush(i - 1)
			start, dir = -1, ""
		case dir == "":
			start, dir = i, cur
		case cur != dir:
			flush(i - 1)
			start, dir = i, cur
		}
	}
	flush(len(bars) - 1)
	return legs
}

// detectZones builds the supply zones of a frame: for every strong downward departure, the range (wicks included) of the baseCandles candles before it. A
// zone price already closed through (above its high) is dead for good and carries at least one touch.
func (s *Strategy) detectZones(bars []market.Candle, atr []float64) []zone {
	var zones []zone
	for _, l := range s.displacement(bars, atr) {
		if l.Direction != "down" || l.Start <= 0 {
			continue
		}
		baseStart := maxInt(0, l.Start-baseCandles)
		if baseStart >= l.Start {
			continue
		}
		low, high := bars[baseStart].Low, bars[baseStart].High
		for i := baseStart; i < l.Start; i++ {
			low, high = math.Min(low, bars[i].Low), math.Max(high, bars[i].High)
		}
		z := zone{Low: low, High: high, Origin: l.Start - 1, LegStart: l.Start, BreakIndex: l.End}
		if closedAbove(z, bars) {
			z.Touches, z.Mitigated = 1, true
		}
		zones = append(zones, z)
	}
	return zones
}

// closedAbove reports whether any close after the zone's last base candle is strictly above its high.
func closedAbove(z zone, bars []market.Candle) bool {
	for i := maxInt(0, z.Origin+1); i < len(bars); i++ {
		if bars[i].Close > z.High {
			return true
		}
	}
	return false
}

// markTouches counts, for each zone, the times price re-entered it after the departure (a rising edge into the band), over the frame's closed bars but the
// last one, which is the bar being decided on.
func markTouches(zones []zone, bars []market.Candle) {
	end := maxInt(0, len(bars)-1)
	for k := range zones {
		z := &zones[k]
		touches, prev := 0, false
		for i := maxInt(0, z.BreakIndex+1); i < end; i++ {
			touched := bars[i].Low <= z.High && bars[i].High >= z.Low
			if touched && !prev {
				touches++
			}
			prev = touched
		}
		z.Touches = maxInt(touches, z.Touches)
		z.Mitigated = z.Mitigated || z.Touches > 0
	}
}

// attachScores reads each zone's score (the frame's multi-factor zone score: key levels, liquidity, higher-timeframe overlap, sessions) from the frame's
// scored zones, a fact of the frame the strategy does not recompute, and orders the zones the way the frame orders its zones.
func attachScores(zones []zone, frame *analysiscontext.LegacyFrame) {
	type key struct {
		origin    int
		low, high float64
	}
	type scored struct {
		score float64
		rank  int
	}
	found := map[key][]scored{}
	for rank, z := range frame.TechniqueZones {
		if z.Side != "supply" {
			continue
		}
		for _, source := range z.Sources {
			if source == "supply_demand" {
				k := key{z.OriginIndex, z.Bottom, z.Top}
				found[k] = append(found[k], scored{z.Score, rank})
				break
			}
		}
	}
	for k := range zones {
		id := key{zones[k].Origin, zones[k].Low, zones[k].High}
		zones[k].rank = math.MaxInt32
		if list := found[id]; len(list) > 0 {
			zones[k].Score, zones[k].rank = list[0].score, list[0].rank
			found[id] = list[1:]
		}
	}
	sort.SliceStable(zones, func(i, j int) bool { return zones[i].rank < zones[j].rank })
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

// notInvalidated: price never accepted above the zone's high. A close above it by more than the tolerance starts an episode, forgiven when a close returns
// inside within sweepReclaimBars; more than maxBreakEpisodes forgiven episodes kill it.
func notInvalidated(high float64, bars []market.Candle, origin int, atr, pip float64) bool {
	if len(bars) == 0 || origin < 0 {
		return true
	}
	e := epsilon(pip, atr)
	tolerance := math.Max(e, invalidationToleranceATR*math.Max(atr, 0))
	count := len(bars)
	episodes := 0
	index := maxInt(0, origin+1)
	for index < count {
		if !(bars[index].Close > high+tolerance) {
			index++
			continue
		}
		reclaimedAt := -1
		for probe := index + 1; probe < minInt(count, index+1+sweepReclaimBars); probe++ {
			if bars[probe].Close <= high {
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

// spent: a zone that was touched and has either taken too many touches or been invalidated.
func spent(mitigated bool, touches int, high float64, bars []market.Candle, origin int, atr, pip float64) bool {
	if !mitigated {
		return false
	}
	if touches <= 0 || touches > retestMaxTouches {
		return true
	}
	return !notInvalidated(high, bars, origin, atr, pip)
}

// clipEntry cuts a zone wider than maxWidth to its proximal (lower) slice.
func clipEntry(low, high, maxWidth float64) (float64, float64, bool) {
	if math.IsNaN(low) || math.IsNaN(high) || high <= low || math.IsNaN(maxWidth) || maxWidth <= 0 || high-low <= maxWidth+1e-12 {
		return low, high, false
	}
	return low, low + maxWidth, true
}

// structureInstances are the supply zones of one structure frame that are still zones: not spent, not invalidated, a real width. Where price is now is not
// asked here (a structure is a fact).
func (s *Strategy) structureInstances(frame *analysiscontext.LegacyFrame, tf market.Timeframe) []instance {
	bars := frame.Bars
	if len(bars) < 10 {
		return nil
	}
	atr := medianATR(frame.ATR)
	zones := s.detectZones(bars, frame.ATR)
	markTouches(zones, bars)
	attachScores(zones, frame)
	pip := s.cfg.PipSize
	var out []instance
	for _, z := range zones {
		if spent(z.Mitigated, z.Touches, z.High, bars, z.Origin, atr, pip) {
			continue
		}
		in := instance{
			Low: z.Low, High: z.High, Touches: z.Touches, Mitigated: z.Mitigated, Score: z.Score, Timeframe: tf, Frame: frame,
			FrameOrigin: z.Origin, FrameConfirmed: maxInt(z.Origin, z.BreakIndex), LegStart: z.LegStart,
		}
		if low, high, clipped := clipEntry(z.Low, z.High, s.cfg.EntryMaxWidthPrice); clipped && s.cfg.EntryMaxWidthPrice > 0 {
			in.StructuralLow, in.StructuralHigh, in.EntryClipped = z.Low, z.High, true
			in.Low, in.High = low, high
		}
		if spent(in.Mitigated, in.Touches, in.High, bars, z.Origin, atr, pip) || !notInvalidated(in.High, bars, z.Origin, atr, pip) {
			continue
		}
		if atr > 0 && in.High-in.Low > math.Max(0, s.cfg.MaximumZoneATR)*atr {
			continue
		}
		out = append(out, in)
	}
	return out
}

// bind maps a structure frame's zones onto the execution frame by TIME: the origin to the first execution bar opening at or after the structure bar that holds
// the zone's last base candle, and the first bar that may touch it to the first execution bar opening at or after the close of the structure bar that ended the
// departure. A zone older than the execution window cannot be anchored there and is dropped.
func bind(tf market.Timeframe, structureBars, execBars []market.Candle, zones []instance, bounded bool) []instance {
	if len(execBars) == 0 {
		return nil
	}
	minutes, known := tf.Minutes()
	var out []instance
	for _, in := range zones {
		if in.FrameOrigin < 0 || in.FrameOrigin >= len(structureBars) {
			continue
		}
		originTime := structureBars[in.FrameOrigin].Time
		index := sort.Search(len(execBars), func(i int) bool { return execBars[i].Time >= originTime })
		if index >= len(execBars) {
			continue
		}
		if execBars[0].Time > originTime { // the origin precedes the execution window: its own frame still holds it, so it stays (OriginIndex -1)
			if bounded {
				continue // the frozen analysis dropped it (engine.WithFrozenWindows)
			}
			index = -1
		}
		touchable := 0
		if in.FrameConfirmed >= 0 && in.FrameConfirmed < len(structureBars) && known {
			closed := structureBars[in.FrameConfirmed].Time + int64(minutes)*60
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
