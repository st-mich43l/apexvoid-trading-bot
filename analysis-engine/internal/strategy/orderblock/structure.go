package orderblock

import (
	"math"
	"sort"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// The order block and its lifecycle, defined here and judged on the bars of the frame the block was found on. These constants were the shared technique
// settings (techniquezone.ProductionTechniqueSettings and the legacy read's displacement leaves) the confirmed path inherited; the strategy now owns them.
const (
	epsilonATRFraction       = 0.05
	retestMaxTouches         = 30
	invalidationToleranceATR = 0.5
	sweepReclaimBars         = 6
	maxBreakEpisodes         = 2
	maximumZoneATR           = 3.0 // a block wider than this share of its frame's ATR is a region, not a block
	momentumBodyFraction     = 0.6 // the body share a displacement candle and an order block's own candle must have
	displacementATRMult      = 1.5 // a displacement leg moves at least this share of the ATR
)

// RefusalClosedThrough is the order block definition's refusal reason: a close of the block's own frame went beyond its far edge by more than the buffer.
const refusedClosedThrough = "ob_closed_through"

// zoneBand is a price band with the facts the qualification reads.
type zoneBand struct {
	Bottom, Top float64
	Side        string // "demand" (buy) | "supply" (sell)
	Score       float64
	Touches     int
}

func (z zoneBand) Low() float64  { return z.Bottom }
func (z zoneBand) High() float64 { return z.Top }

// leg is a run of same-direction candles that moved far and strongly: the displacement that breaks structure.
type leg struct {
	Start, End int
	Direction  string // "up" | "down"
}

// block is one order block on a frame: the last opposite candle before a displacement leg that broke structure.
type block struct {
	Side       string // "buy" | "sell"
	Low, High  float64
	Origin     int // index of the block's own candle on its frame
	BreakIndex int // the structure break that qualified the block
	Touches    int
	Mitigated  bool
	Score      float64
	rank       int
}

// instance is a block that passed the lifecycle tests: its entry band (clipped to the proximal slice when wide), the whole block, and where it lives.
type instance struct {
	Side           string
	Low, High      float64 // the entry band
	StructuralLow  float64
	StructuralHigh float64
	EntryClipped   bool
	OriginIndex    int // on the execution frame once bound; on its own frame before
	OriginTime     int64
	TouchableFrom  int
	Touches        int
	Mitigated      bool
	Score          float64
	Timeframe      market.Timeframe // "" for an execution-frame block
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

// originBodyFraction is the same share for the block's own candle (a candle without range reads as a tiny range, not as no body).
func originBodyFraction(c market.Candle) float64 {
	return math.Abs(c.Close-c.Open) / math.Max(c.High-c.Low, 1e-9)
}

func atrAt(series []float64, i int, fallback float64) float64 {
	if i >= 0 && i < len(series) && !math.IsNaN(series[i]) && !math.IsInf(series[i], 0) && series[i] > 0 {
		return series[i]
	}
	return fallback
}

// displacement finds the legs: runs of same-direction candles whose net move clears displacementATRMult x ATR and whose bodies are mostly strong.
func displacement(bars []market.Candle, atr []float64) []leg {
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
		if size < atrAt(atr, end, 1)*displacementATRMult {
			return
		}
		strong := 0
		for i := start; i <= end; i++ {
			if bodyFraction(bars[i]) >= momentumBodyFraction {
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

// causingBreak is the structure break (BOS or CHoCH) of the leg's direction that happened inside the leg.
func causingBreak(l leg, breaks []techniquezone.Break) (int, bool) {
	for _, b := range breaks {
		if (b.Kind != "BOS" && b.Kind != "CHoCH") || b.Direction != l.Direction {
			continue
		}
		if l.Start <= b.Index && b.Index <= l.End {
			return b.Index, true
		}
	}
	return 0, false
}

// lastOppositeCandle is the last candle before the leg that moved the other way.
func lastOppositeCandle(bars []market.Candle, l leg) int {
	opposite := "up"
	if l.Direction == "up" {
		opposite = "down"
	}
	for i := l.Start - 1; i >= 0; i-- {
		if candleDirection(bars[i]) == opposite {
			return i
		}
	}
	return -1
}

// detectBlocks builds the order blocks of a frame: for every displacement leg that broke structure, the body of the last opposite candle before it. A block
// price already closed through (below a demand block's low, above a supply block's high) is dead for good and carries at least one touch.
func detectBlocks(bars []market.Candle, atr []float64, breaks []techniquezone.Break) []block {
	var blocks []block
	for _, l := range displacement(bars, atr) {
		breakIndex, ok := causingBreak(l, breaks)
		if !ok {
			continue
		}
		origin := lastOppositeCandle(bars, l)
		if origin < 0 {
			continue
		}
		low, high := math.Min(bars[origin].Open, bars[origin].Close), math.Max(bars[origin].Open, bars[origin].Close)
		side := "sell"
		if l.Direction == "up" {
			side = "buy"
		}
		b := block{Side: side, Low: low, High: high, Origin: origin, BreakIndex: breakIndex}
		if closedBeyond(b, bars) {
			b.Touches, b.Mitigated = 1, true
		}
		blocks = append(blocks, b)
	}
	return blocks
}

// closedBeyond reports whether any close after the block's own candle is strictly beyond its far edge.
func closedBeyond(b block, bars []market.Candle) bool {
	for i := maxInt(0, b.Origin+1); i < len(bars); i++ {
		c := bars[i].Close
		if b.Side == "buy" && c < b.Low || b.Side == "sell" && c > b.High {
			return true
		}
	}
	return false
}

// markTouches counts, for each block, the times price re-entered it after the structure break that qualified it (a rising edge into the band), over the
// frame's closed bars but the last one, which is the bar being decided on.
func markTouches(blocks []block, bars []market.Candle) {
	end := maxInt(0, len(bars)-1)
	for k := range blocks {
		b := &blocks[k]
		touches, prev := 0, false
		for i := maxInt(0, b.BreakIndex+1); i < end; i++ {
			touched := bars[i].Low <= b.High && bars[i].High >= b.Low
			if touched && !prev {
				touches++
			}
			prev = touched
		}
		b.Touches = maxInt(touches, b.Touches)
		b.Mitigated = b.Mitigated || b.Touches > 0
	}
}

// attachScores reads each block's zone score (the frame's multi-factor zone score: key levels, liquidity, higher-timeframe overlap, sessions) from the
// frame's scored zones, a fact of the frame the strategy does not recompute, and orders the blocks the way the frame orders its zones.
func attachScores(blocks []block, frame *analysiscontext.LegacyFrame) {
	type key struct {
		origin    int
		low, high float64
		side      string
	}
	type scored struct {
		score float64
		rank  int
	}
	found := map[key][]scored{}
	for rank, z := range frame.TechniqueZones {
		for _, source := range z.Sources {
			if source == "order_block" {
				k := key{z.OriginIndex, z.Bottom, z.Top, z.Side}
				found[k] = append(found[k], scored{z.Score, rank})
				break
			}
		}
	}
	for k := range blocks {
		side := "supply"
		if blocks[k].Side == "buy" {
			side = "demand"
		}
		id := key{blocks[k].Origin, blocks[k].Low, blocks[k].High, side}
		blocks[k].rank = math.MaxInt32
		if list := found[id]; len(list) > 0 {
			blocks[k].Score, blocks[k].rank = list[0].score, list[0].rank
			found[id] = list[1:]
		}
	}
	sort.SliceStable(blocks, func(i, j int) bool { return blocks[i].rank < blocks[j].rank })
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

// notInvalidated: price never accepted beyond the block's far edge. A close beyond it by more than the tolerance starts an episode, forgiven when a close
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

// spent: a block that was touched and has either taken too many touches or been invalidated.
func spent(mitigated bool, touches int, side string, low, high float64, bars []market.Candle, origin int, atr, pip float64) bool {
	if !mitigated {
		return false
	}
	if touches <= 0 || touches > retestMaxTouches {
		return true
	}
	return !notInvalidated(side, low, high, bars, origin, atr, pip)
}

func proximalRetest(side string, low, high, price, atr, pip float64) bool {
	e := epsilon(pip, atr)
	proximal := low
	if side == "buy" {
		proximal = high
	}
	return math.Abs(price-proximal) <= e || (low-e <= price && price <= high+e)
}

// clipEntry cuts a block wider than maxWidth to its proximal slice (a buy keeps the top, a sell the bottom).
func clipEntry(side string, low, high, maxWidth float64) (float64, float64, bool) {
	if math.IsNaN(low) || math.IsNaN(high) || high <= low || math.IsNaN(maxWidth) || maxWidth <= 0 || high-low <= maxWidth+1e-12 {
		return low, high, false
	}
	if side == "sell" {
		return low, low + maxWidth, true
	}
	return high - maxWidth, high, true
}

// closedThroughRefusal reports whether any close after the block's own candle, up to the frame's last closed bar, is beyond the block's far edge by more than
// BreakBufferATR of the frame's ATR at that bar. The far edge is the block's structural one when its entry band was clipped.
func (s *Strategy) closedThroughRefusal(in instance, bars []market.Candle, atr float64, series []float64, origin int) bool {
	if s.cfg.BreakBufferATR <= 0 || origin < 0 || origin >= len(bars)-1 {
		return false
	}
	low, high := in.structural()
	aligned := len(series) == len(bars)
	for i := origin + 1; i < len(bars); i++ {
		frameATR := atr
		if aligned && series[i] > 0 && !math.IsNaN(series[i]) {
			frameATR = series[i]
		}
		buffer := s.cfg.BreakBufferATR * frameATR
		if in.Side == "buy" && bars[i].Close < low-buffer-1e-9 || in.Side == "sell" && bars[i].Close > high+buffer+1e-9 {
			return true
		}
	}
	return false
}

// frameInstances are the order blocks of one frame that pass every lifecycle test and the definition. proximity is true for the execution frame (price must
// be at the block now) and false for a higher frame, whose block is judged as a fact and traded when M5 price reaches it. refused is told about every block
// the close-break rule refuses.
func (s *Strategy) frameInstances(frame *analysiscontext.LegacyFrame, proximity bool, refused func(reason string)) []instance {
	bars := frame.Bars
	if len(bars) < 10 {
		return nil
	}
	atr := medianATR(frame.ATR)
	price := bars[len(bars)-1].Close
	blocks := detectBlocks(bars, frame.ATR, frame.Breaks)
	markTouches(blocks, bars)
	attachScores(blocks, frame)
	pip := s.cfg.PipSize
	var out []instance
	for _, b := range blocks {
		if spent(b.Mitigated, b.Touches, b.Side, b.Low, b.High, bars, b.Origin, atr, pip) {
			continue
		}
		in := instance{
			Side: b.Side, Low: b.Low, High: b.High, OriginIndex: b.Origin, TouchableFrom: maxInt(b.Origin, b.BreakIndex) + 1,
			Touches: b.Touches, Mitigated: b.Mitigated, Score: b.Score,
		}
		if low, high, clipped := clipEntry(b.Side, b.Low, b.High, s.cfg.EntryMaxWidthPrice); clipped && s.cfg.EntryMaxWidthPrice > 0 {
			in.StructuralLow, in.StructuralHigh, in.EntryClipped = b.Low, b.High, true
			in.Low, in.High = low, high
		}
		if spent(in.Mitigated, in.Touches, in.Side, in.Low, in.High, bars, b.Origin, atr, pip) || !notInvalidated(in.Side, in.Low, in.High, bars, b.Origin, atr, pip) {
			continue
		}
		if proximity && !proximalRetest(in.Side, in.Low, in.High, price, atr, pip) {
			continue
		}
		if atr > 0 && in.High-in.Low > math.Max(0, maximumZoneATR)*atr {
			continue
		}
		if originBodyFraction(bars[b.Origin]) < momentumBodyFraction {
			continue
		}
		if s.closedThroughRefusal(in, bars, atr, frame.ATR, b.Origin) {
			if refused != nil {
				refused(refusedClosedThrough)
			}
			continue
		}
		out = append(out, in)
	}
	return out
}

// bind maps a higher frame's blocks onto the execution frame by TIME: the origin to the first execution bar opening at or after the higher bar that holds
// the block's own candle, and the first bar that may touch it to the first execution bar opening at or after the close of the higher bar that confirmed it
// (the later of the block's candle and its structure break). A block older than the execution window cannot be anchored there and is dropped.
func bind(tf market.Timeframe, higherBars, execBars []market.Candle, blocks []instance) []instance {
	if len(execBars) == 0 {
		return nil
	}
	minutes, known := tf.Minutes()
	var out []instance
	for _, in := range blocks {
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
