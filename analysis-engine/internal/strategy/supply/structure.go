package supply

import (
	"math"
	"sort"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
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

// refusedZoneStrength counts a zone in play (its entry is valid) that is below minimum_zone_strength: a weak zone is context, never a trade.
const refusedZoneStrength = "zone_strength_below_minimum"

// The entry planner's refusals (swing model): no candidate price of the zone is feasible.
const (
	refusedStopBeyondCap   = "stop_beyond_cap"
	refusedNoFeasibleEntry = "no_feasible_entry"
)

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
	Premium    bool
	// Formation is the swing-anchored derivation of the zone (nil under the legacy model).
	Formation *structure.SwingZone
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
	Premium        bool                         // the zone is in the premium half of its frame's dealing range and earned the points for it
	PDPosition     float64                      // where the zone's proximal edge sits in that range, 0 (low) to 1 (high); NaN without a range
	Frame          *analysiscontext.LegacyFrame // the frame the zone was found on
	FrameOrigin    int                          // the zone's last base candle on that frame
	FrameConfirmed int                          // the last candle of the departure on that frame
	LegStart       int                          // the departure's first candle on that frame
	// Formation is the swing-anchored derivation of the zone (nil under the legacy model): the swing, its class, the base and the strength.
	Formation *structure.SwingZone
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
	if s.cfg.Formation.Swing() {
		return s.detectSwingZones(bars, atr)
	}
	return s.detectLegacyZones(bars, atr)
}

// detectLegacyZones is the zone definition before SD-3, kept as the rollback (zone_model: legacy).
func (s *Strategy) detectLegacyZones(bars []market.Candle, atr []float64) []zone {
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

// detectSwingZones anchors every zone on a classified swing and its departure (structure.FormSwingZones): the zone is the base the swing was made in, the departure is
// this strategy's own displacement leg. A zone price already closed through its far edge is mitigated and carries at least one touch, as before.
func (s *Strategy) detectSwingZones(bars []market.Candle, atr []float64) []zone {
	var legs []structure.Leg
	for _, l := range s.displacement(bars, atr) {
		if l.Direction == "down" {
			legs = append(legs, structure.Leg{Start: l.Start, End: l.End})
		}
	}
	var zones []zone
	for _, formed := range structure.FormSwingZones(true, bars, atr, legs, s.cfg.Formation.Zone) {
		formation := formed
		z := zone{Low: formed.Low, High: formed.High, Origin: formed.BaseEnd, LegStart: formed.LegStart, BreakIndex: formed.ConfirmedIndex, Formation: &formation}
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

// breach is the verdict on a zone's far edge, judged only on bars that have closed. A close above the high by more than the tolerance starts a breach episode.
// An episode is forgiven when a close returns to or below the high within sweepReclaimBars bars after the breaching bar; the zone is terminal when a whole
// window closes with no reclaim, or when more than maxBreakEpisodes episodes were forgiven. While the window is still open nobody can know the outcome: the
// zone is Pending (neither retired nor offered), and the verdict only ever moves forward (Pending becomes reclaimed or Terminal, and Terminal is never
// undone by a later bar).
type breach struct {
	Terminal bool
	Pending  bool
}

// breachOf is the far-edge verdict of a zone whose last base candle is origin. high is the structural far edge, never the clipped entry slice.
func breachOf(high float64, bars []market.Candle, origin int, atr, pip float64) breach {
	if len(bars) == 0 || origin < 0 {
		return breach{}
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
			if count >= index+1+sweepReclaimBars {
				return breach{Terminal: true} // the whole window closed without a reclaim
			}
			return breach{Pending: true} // the window is still open
		}
		episodes++
		if episodes > maxBreakEpisodes {
			return breach{Terminal: true}
		}
		index = reclaimedAt + 1
	}
	return breach{}
}

// endReason names why a zone that is spent is finished: invalidated when price was accepted beyond it, otherwise mitigated (touched past its limit).
func endReason(b breach) opportunity.ReasonCode {
	if b.Terminal {
		return opportunity.ReasonZoneInvalidated
	}
	return opportunity.ReasonZoneMitigated
}

// spent: a zone that was touched and has either taken too many touches or been invalidated.
func spent(mitigated bool, touches int, b breach) bool {
	if !mitigated {
		return false
	}
	if touches <= 0 || touches > retestMaxTouches {
		return true
	}
	return b.Terminal
}

// clipEntry cuts a zone wider than maxWidth to its proximal (lower) slice.
func clipEntry(low, high, maxWidth float64) (float64, float64, bool) {
	if math.IsNaN(low) || math.IsNaN(high) || high <= low || math.IsNaN(maxWidth) || maxWidth <= 0 || high-low <= maxWidth+1e-12 {
		return low, high, false
	}
	return low, low + maxWidth, true
}

// structureInstances are the supply zones of one structure frame that are still zones: not spent, not invalidated, not in a pending breach, a real width. Where
// price is now is not asked here (a structure is a fact). higher are this strategy's own zones on the frames above this one (a zone inside one scores the
// higher-timeframe points); the second result is the zones of this frame that are valid structure for the frame below: never a terminal, spent, pending or
// over-wide one.
//
// Every test of a zone's life is made against the COMPLETE structural zone (z.Low..z.High), never the proximal slice the entry band is clipped to: the far edge
// that invalidates a supply zone is the structural high, and the width limit is the structure's, so a wide zone cannot be offered through a narrow slice.
func (s *Strategy) structureInstances(frame *analysiscontext.LegacyFrame, tf market.Timeframe, execBars []market.Candle, higher []band, verdict *strategyutil.ZoneVerdict) ([]instance, []band) {
	bars := frame.Bars
	if len(bars) < 10 {
		return nil, nil
	}
	verdict.Frame(tf)
	atr := medianATR(frame.ATR)
	zones := s.detectZones(bars, frame.ATR)
	markTouches(zones, bars)
	inputs := s.inputsFor(frame, tf, execBars, higher)
	for k := range zones {
		zones[k].Score, zones[k].Premium = scoreZone(zones[k], inputs)
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
	held := make([]band, 0, len(zones))
	var out []instance
	for _, z := range zones {
		id := ""
		if z.Origin >= 0 && z.Origin < len(bars) {
			id = zoneID(tf, bars[z.Origin].Time)
		}
		state := breachOf(z.High, bars, z.Origin, atr, pip)
		if spent(z.Mitigated, z.Touches, state) {
			verdict.Terminal(id, endReason(state))
			continue
		}
		verdict.Live(id) // still a supply zone, whether or not it is wide enough to enter right now or its breach is still being decided
		if state.Pending {
			continue
		}
		if z.Formation != nil {
			if z.Formation.TooWide || z.Formation.TooNarrow {
				continue // a region or an incidental candle region, judged on the whole structure
			}
		} else if atr > 0 && z.High-z.Low > math.Max(0, s.cfg.MaximumZoneATR)*atr {
			continue // a region, judged on the whole structure
		}
		in := instance{
			Low: z.Low, High: z.High, Touches: z.Touches, Mitigated: z.Mitigated, Score: z.Score, Timeframe: tf, Frame: frame,
			FrameOrigin: z.Origin, FrameConfirmed: maxInt(z.Origin, z.BreakIndex), LegStart: z.LegStart, Formation: z.Formation,
			Premium: z.Premium, PDPosition: inputs.pd.position(z.Low),
		}
		if low, high, clipped := clipEntry(z.Low, z.High, s.cfg.EntryMaxWidthPrice); clipped && s.cfg.EntryMaxWidthPrice > 0 {
			in.StructuralLow, in.StructuralHigh, in.EntryClipped = z.Low, z.High, true
			in.Low, in.High = low, high
		}
		if !in.weak(s.cfg.Formation) {
			held = append(held, band{Low: z.Low, High: z.High}) // only a zone that may publish is structure for the frame below
		}
		out = append(out, in)
	}
	return out, held
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

// weak reports a swing-anchored zone below the strategy's minimum strength: kept as context, never published, never higher-timeframe confluence. The legacy model has
// no strength and is never weak.
func (i instance) weak(f strategyutil.ZoneFormation) bool {
	return i.Formation != nil && i.Formation.Strength < f.MinimumStrength
}
