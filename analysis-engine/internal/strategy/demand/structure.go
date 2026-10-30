package demand

import (
	"math"
	"sort"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// The supply and demand zones and their lifecycle, defined here and judged on the bars of the frame the zone was found on. These constants were the shared technique
// settings (techniquezone.ProductionTechniqueSettings and the legacy read's displacement leaves) the confirmed path inherited; the strategy now owns them.
const (
	epsilonATRFraction       = 0.05
	retestMaxTouches         = 30
	invalidationToleranceATR = 0.5
	sweepReclaimBars         = 6
	maxBreakEpisodes         = 2
	maximumZoneATR           = 3.0 // a zone wider than this share of its frame's ATR is a region, not a zone
	momentumBodyFraction     = 0.6 // the body share a displacement candle must have
	displacementATRMult      = 1.5 // a displacement leg moves at least this share of the ATR
	baseCandles              = 3   // the zone is the range of this many candles before the departure
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

// leg is a run of same-direction candles that moved far and strongly: the departure from the zone.
type leg struct {
	Start, End int
	Direction  string // "up" | "down"
}

// zone is one zone on a frame: the range of the candles before a strong departure (down: supply, up: demand).
type zone struct {
	Side       string // "buy" (demand) | "sell" (supply)
	Low, High  float64
	Origin     int // index of the last base candle on its frame
	BreakIndex int // the last candle of the departure leg
	Touches    int
	Mitigated  bool
	Score      float64
}

// instance is a zone that passed the lifecycle tests: its entry band (clipped to the proximal slice when wide), the whole zone, and where it lives.
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
	Timeframe      market.Timeframe // "" for an execution-frame zone
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

// detectZones builds the zones of a frame: for every strong departure, the range (wicks included) of the baseCandles candles before it. A zone price already
// closed through (above a supply zone's high, below a demand zone's low) is dead for good and carries at least one touch.
func detectZones(bars []market.Candle, atr []float64) []zone {
	var zones []zone
	for _, l := range displacement(bars, atr) {
		if l.Start <= 0 {
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
		side := "sell"
		if l.Direction == "up" {
			side = "buy"
		}
		z := zone{Side: side, Low: low, High: high, Origin: l.Start - 1, BreakIndex: l.End}
		if closedBeyond(z, bars) {
			z.Touches, z.Mitigated = 1, true
		}
		zones = append(zones, z)
	}
	return zones
}

// closedBeyond reports whether any close after the zone's last base candle is strictly beyond its far edge.
func closedBeyond(z zone, bars []market.Candle) bool {
	for i := maxInt(0, z.Origin+1); i < len(bars); i++ {
		c := bars[i].Close
		if z.Side == "sell" && c > z.High || z.Side == "buy" && c < z.Low {
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

// notInvalidated: price never accepted beyond the zone's far edge. A close beyond it by more than the tolerance starts an episode, forgiven when a close
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

// spent: a zone that was touched and has either taken too many touches or been invalidated.
func spent(mitigated bool, touches int, side string, low, high float64, bars []market.Candle, origin int, atr, pip float64) bool {
	if !mitigated {
		return false
	}
	if touches <= 0 || touches > retestMaxTouches {
		return true
	}
	return !notInvalidated(side, low, high, bars, origin, atr, pip)
}

// proximalRetest: price is at the zone, or within the tolerance of its proximal edge (a supply zone's low, a demand zone's high).
func proximalRetest(side string, low, high, price, atr, pip float64) bool {
	e := epsilon(pip, atr)
	proximal := low
	if side == "buy" {
		proximal = high
	}
	return math.Abs(price-proximal) <= e || (low-e <= price && price <= high+e)
}

// clipEntry cuts a zone wider than maxWidth to its proximal slice (a buy keeps the top, a sell the bottom).
func clipEntry(side string, low, high, maxWidth float64) (float64, float64, bool) {
	if math.IsNaN(low) || math.IsNaN(high) || high <= low || math.IsNaN(maxWidth) || maxWidth <= 0 || high-low <= maxWidth+1e-12 {
		return low, high, false
	}
	if side == "sell" {
		return low, low + maxWidth, true
	}
	return high - maxWidth, high, true
}

// frameInstances are the zones of one frame that pass every lifecycle test. proximity is true for the execution frame (price must be at the zone now)
// and false for a higher frame, whose zone is judged as a fact and traded when M5 price reaches it.
func (s *Strategy) frameInstances(ctx *analysiscontext.MarketContext, frame *analysiscontext.LegacyFrame, tf market.Timeframe, proximity bool) []instance {
	bars := frame.Bars
	if len(bars) < 10 {
		return nil
	}
	atr := medianATR(frame.ATR)
	price := bars[len(bars)-1].Close
	zones := detectZones(bars, frame.ATR)
	markTouches(zones, bars)
	inputs := s.inputsFor(frame, higherFrames(ctx, tf))
	for k := range zones {
		zones[k].Score = scoreZone(zones[k], inputs)
	}
	sort.SliceStable(zones, func(i, j int) bool {
		a, b := zones[i], zones[j]
		if a.Score != b.Score {
			return a.Score > b.Score
		}
		if a.Touches != b.Touches {
			return a.Touches < b.Touches
		}
		return a.Low < b.Low
	})
	pip := s.cfg.PipSize
	var out []instance
	for _, z := range zones {
		if spent(z.Mitigated, z.Touches, z.Side, z.Low, z.High, bars, z.Origin, atr, pip) {
			continue
		}
		in := instance{
			Side: z.Side, Low: z.Low, High: z.High, OriginIndex: z.Origin, TouchableFrom: maxInt(z.Origin, z.BreakIndex) + 1,
			Touches: z.Touches, Mitigated: z.Mitigated, Score: z.Score,
		}
		if low, high, clipped := clipEntry(z.Side, z.Low, z.High, s.cfg.EntryMaxWidthPrice); clipped && s.cfg.EntryMaxWidthPrice > 0 {
			in.StructuralLow, in.StructuralHigh, in.EntryClipped = z.Low, z.High, true
			in.Low, in.High = low, high
		}
		if spent(in.Mitigated, in.Touches, in.Side, in.Low, in.High, bars, z.Origin, atr, pip) || !notInvalidated(in.Side, in.Low, in.High, bars, z.Origin, atr, pip) {
			continue
		}
		if proximity && !proximalRetest(in.Side, in.Low, in.High, price, atr, pip) {
			continue
		}
		if atr > 0 && in.High-in.Low > math.Max(0, maximumZoneATR)*atr {
			continue
		}
		out = append(out, in)
	}
	return out
}

// bind maps a higher frame's zones onto the execution frame by TIME: the origin to the first execution bar opening at or after the higher bar that holds the
// zone's last base candle, and the first bar that may touch it to the first execution bar opening at or after the close of the higher bar that ended the
// departure. A zone older than the execution window cannot be anchored there and is dropped.
func bind(tf market.Timeframe, higherBars, execBars []market.Candle, zones []instance) []instance {
	if len(execBars) == 0 {
		return nil
	}
	minutes, known := tf.Minutes()
	var out []instance
	for _, in := range zones {
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
