package mad

import (
	"fmt"
	"math"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Sequence statuses. A sequence is only ever as complete as the bars that have
// closed: Partial is a real state (a lone sweep, a lone expansion), never a
// stand-in for a missing phase.
const (
	SequencePartial     = "partial"
	SequenceComplete    = "complete"
	SequenceInvalidated = "invalidated"
	SequenceStale       = "stale"
)

// Stages named in Sequence.Stages, in the order they can occur.
const (
	StageRange     = "range"
	StageSweep     = "sweep"
	StageReclaim   = "reclaim"
	StageExpansion = "expansion"
)

// Invalidation reasons. They are machine-readable and stable.
const (
	InvalidTwoSidedSweep     = "two_sided_sweep"
	InvalidConflictingSweep  = "conflicting_sweep"
	InvalidSweepExtremeLost  = "sweep_extreme_lost"
	InvalidExpansionBackInto = "expansion_failed_back_inside"
)

// minimumRangeCoverage is the share of the bars the Asia window should hold that
// must be present for the box to be trusted: a feed gap is not an Asia range.
const minimumRangeCoverage = 0.5

// maximumRangeMinutes is the coarsest timeframe whose bars line up with the
// window edges. An H4 or D1 bar straddles the session open, so its high and low
// would belong to more than the Asia session: no range is read from it.
const maximumRangeMinutes = 60

// Sweep is the manipulation event: a bar traded beyond a boundary of the sealed
// range and a bar then closed back inside it. Times are bar OPEN times (the
// repository's candle convention); the information is known only when that bar
// closes. ReclaimAt is 0 until a bar has closed back inside.
type Sweep struct {
	Side            string  // "high" or "low": the boundary that was swept
	Level           float64 // the swept boundary
	Extreme         float64 // the furthest price beyond the boundary so far
	At              int64
	ReclaimAt       int64
	Direction       string // the trade direction the reclaimed sweep supports: SELL after a high, BUY after a low
	PenetrationATR  float64
	ReclaimDepthATR float64
	CloseLocation   float64
}

// Reclaimed reports whether a closed bar has taken the boundary back.
func (s Sweep) Reclaimed() bool { return s.ReclaimAt > 0 }

// Expansion is the directional move that followed (or, with no sweep, left) the
// range: distribution/expansion is directional price movement in this model, not
// proof of institutional manipulation. StartedAt is the first bar of the move,
// ConfirmedAt the bar whose close established it; Basis names the evidence
// ("break_accepted" or "displacement").
type Expansion struct {
	Direction        string
	Basis            string
	StartedAt        int64
	ConfirmedAt      int64
	DisplacementATR  float64
	BreakDistanceATR float64
	AcceptedCloses   int
	Strength         float64
}

// Sequence is the event-linked AMD context of one sealed range. ID is derived
// from the first event that identifies it, so the same real sequence keeps its
// identity as later events are added. Direction is empty until the evidence
// (a reclaimed sweep or a confirmed expansion) gives one.
type Sequence struct {
	ID                 string
	RangeID            string
	Status             string
	Stages             []string
	Sweep              *Sweep
	Expansion          *Expansion
	Direction          string
	InvalidationReason string
	InvalidatedAt      int64
	LastEventAt        int64
	// Note records a fact that is neither an event nor an invalidation, for
	// instance that a probe beyond the range was accepted and so was a breakout,
	// not a sweep.
	Note string
}

// OrderingError verifies the temporal contract of the sequence against its range:
// range sealed <= sweep <= reclaim <= expansion start <= expansion confirmation.
// It returns nil for a sequence whose events respect it.
func (s Sequence) OrderingError(rangeSealedAt int64) error {
	last := rangeSealedAt
	check := func(name string, at int64) error {
		if at < last {
			return fmt.Errorf("mad: %s at %d precedes the previous event at %d", name, at, last)
		}
		last = at
		return nil
	}
	if s.Sweep != nil {
		if err := check("sweep", s.Sweep.At); err != nil {
			return err
		}
		if s.Sweep.ReclaimAt > 0 {
			if err := check("reclaim", s.Sweep.ReclaimAt); err != nil {
				return err
			}
		}
	}
	if s.Expansion != nil {
		if err := check("expansion start", s.Expansion.StartedAt); err != nil {
			return err
		}
		if err := check("expansion confirmation", s.Expansion.ConfirmedAt); err != nil {
			return err
		}
	}
	return nil
}

// Input is everything Classify reads: closed bars only.
type Input struct {
	Candles   []market.Candle // time ascending, the latest closed bar last
	Timeframe market.Timeframe
	ATR       float64   // the current ATR; the fallback when ATRSeries is short
	ATRSeries []float64 // aligned to the tail of Candles
	Structure string    // the canonical trend read: "range"/"unknown"/"" qualifies as accumulation
	Asia      *AsiaRangeSeal
	Config    Config
}

// asiaWindow resolves the Asia session occurrence in force at now.
func asiaWindow(now int64, cfg Config) (start, end int64, ok bool) {
	if cfg.AsiaWindow != nil {
		return cfg.AsiaWindow(now)
	}
	start, end = asiaBounds(now, cfg.AsiaStartHour, cfg.LondonStartHour)
	return start, end, true
}

// SealAsiaRange derives the Asia range of the occurrence in force at the last
// bar, from the closed bars of that window only. The range is Sealed only once a
// bar at or after the window's close exists, never from a session label, so it is
// causal on every timeframe. A reason code explains a nil range.
func SealAsiaRange(candles []market.Candle, tf market.Timeframe, cfg Config) (*AsiaRangeSeal, string) {
	if len(candles) == 0 {
		return nil, "asia_range_missing"
	}
	minutes, ok := tf.Minutes()
	if !ok || minutes <= 0 {
		return nil, "asia_timeframe_unknown"
	}
	if minutes > maximumRangeMinutes {
		return nil, "asia_timeframe_too_coarse"
	}
	now := candles[len(candles)-1].Time
	start, end, ok := asiaWindow(now, cfg)
	if !ok || end <= start {
		return nil, "asia_window_unavailable"
	}
	if candles[0].Time >= start {
		// Nothing older than the window is held, so the history may have begun
		// inside it and the box would be missing its start.
		return nil, "asia_range_incomplete_history"
	}
	var high, low float64
	count := 0
	for _, c := range candles {
		if c.Time < start || c.Time >= end {
			continue
		}
		if count == 0 || c.High > high {
			high = c.High
		}
		if count == 0 || c.Low < low {
			low = c.Low
		}
		count++
	}
	if count == 0 {
		return nil, "asia_range_missing"
	}
	// A forming range is judged against the part of the window that has elapsed.
	covered := end
	if now+int64(minutes*60) < end {
		covered = now + int64(minutes*60)
	}
	expected := int((end - start) / int64(minutes*60))
	if due := int((covered - start) / int64(minutes*60)); due > 0 && float64(count) < minimumRangeCoverage*float64(due) {
		return nil, "asia_range_incomplete_history"
	}
	sealed := now >= end
	var sealedAt int64
	if sealed {
		sealedAt = end
	}
	return &AsiaRangeSeal{
		ID: fmt.Sprintf("asia:%d", start), DayKey: time.Unix(start, 0).UTC().Format("2006-01-02"), Timeframe: tf,
		High: high, Low: low, Start: start, End: end, Sealed: sealed, SealedAt: sealedAt,
		BarCount: count, ExpectedBars: expected, Source: string(tf), UpdatedAt: now,
	}, ""
}

// Classify reads the AMD context of one timeframe from its closed bars.
func Classify(in Input) Snapshot {
	cfg := in.Config
	snap := Snapshot{Phase: PhaseUnclear, Timeframe: in.Timeframe, Version: Version, Measured: map[string]float64{}, ReasonCode: "asia_range_missing"}
	candles := in.Candles
	if len(candles) == 0 {
		return snap
	}
	last := candles[len(candles)-1]
	now := last.Time
	snap.ObservedAt = now
	asia := in.Asia
	if asia == nil {
		var reason string
		if asia, reason = SealAsiaRange(candles, in.Timeframe, cfg); asia == nil {
			snap.ReasonCode = reason
			return snap
		}
	}
	snap.Asia = asia
	if asia.Width() <= 0 {
		return snap
	}
	if asia.End > 0 && now >= asia.End && !asia.Sealed {
		// A range handed in as forming whose window has since closed is not current.
		snap.ReasonCode = "asia_range_stale_or_missing"
		return snap
	}
	atr := positive(in.ATR)
	if atr <= 0 {
		snap.ReasonCode = "atr_unavailable"
		return snap
	}
	rq := asia.Width() / atr
	snap.RangeQualityATR = &rq
	vs := priceVsAsia(last.Close, *asia)
	snap.PriceVsAsia = vs
	switch vs {
	case "above":
		snap.Measured["break_distance_atr"] = (last.Close - asia.High) / atr
	case "below":
		snap.Measured["break_distance_atr"] = (asia.Low - last.Close) / atr
	}
	snap.Measured["displacement_atr"] = displacementATR(candles, atr, 5)
	snap.Measured["accepted_closes"] = float64(closesBeyond(candles, *asia, vs))

	structureOK := in.Structure == "range" || in.Structure == "unknown" || in.Structure == ""
	if !asia.Sealed {
		// The box is still forming: it has no edge to sweep yet.
		if vs == "inside" && structureOK && rq >= cfg.AccumMinimumRQ && rq <= 24 {
			snap.Phase, snap.ReasonCode, snap.Active = PhaseAccum, "asia_building_accum", true
			snap.Confidence = accumConfidence(rq, true, cfg)
			return snap
		}
		snap.ReasonCode = "asia_range_forming"
		return snap
	}

	atrAt := func(i int) float64 {
		// ATRSeries is aligned to the tail of the candles; an index it does not
		// cover uses the current ATR.
		if k := len(in.ATRSeries) - (len(candles) - i); k >= 0 && k < len(in.ATRSeries) && in.ATRSeries[k] > 0 {
			return in.ATRSeries[k]
		}
		return atr
	}
	seq := scan(candles, *asia, atrAt, cfg)
	snap.Sequence = seq
	validity := int64(cfg.SequenceValidityHours * 3600)
	reference := asia.End
	if seq != nil && seq.LastEventAt > reference {
		reference = seq.LastEventAt
	}
	stale := validity > 0 && now-reference > validity

	if seq != nil {
		if sw := seq.Sweep; sw != nil {
			snap.SweepSide, snap.Reclaim = sw.Side, sw.Reclaimed()
			snap.Measured["sweep_penetration_atr"] = sw.PenetrationATR
			if sw.Reclaimed() {
				snap.ManipulationDirection = sw.Direction
				snap.Measured["reclaim_depth_atr"], snap.Measured["close_location"] = sw.ReclaimDepthATR, sw.CloseLocation
			}
		}
		if ex := seq.Expansion; ex != nil {
			snap.ExpansionDirection = ex.Direction
		}
	}
	switch {
	case stale:
		// Staleness outranks invalidation: a dead thesis from hours ago is old
		// context, not a live contradiction. The invalidation reason stays on the
		// sequence for audit.
		if seq != nil {
			seq.Status = SequenceStale
		}
		snap.ReasonCode = "mad_evidence_stale"
	case seq != nil && seq.Status == SequenceInvalidated:
		snap.ReasonCode = seq.InvalidationReason
	case seq != nil && seq.Status == SequenceComplete:
		snap.Phase, snap.ReasonCode, snap.Active = PhaseExpand, "asia_sweep_reclaim_expansion", true
		snap.Confidence = math.Min(manipConfidence(snap.Measured, cfg), seq.Expansion.Strength)
	case seq != nil && seq.Expansion != nil:
		snap.Phase, snap.ReasonCode, snap.Active = PhaseExpand, "asia_strong_displacement", true
		if seq.Expansion.Basis == "break_accepted" {
			snap.ReasonCode = "asia_break_accepted"
		}
		snap.Confidence = seq.Expansion.Strength
	case seq != nil && seq.Sweep != nil && seq.Sweep.Reclaimed():
		snap.Phase, snap.ReasonCode, snap.Active = PhaseManip, "asia_sweep_reclaim", true
		snap.Confidence = manipConfidence(snap.Measured, cfg)
	case seq != nil && seq.Sweep != nil:
		snap.ReasonCode, snap.Active = "asia_sweep_pending_reclaim", true
	case vs == "inside" && structureOK && rq >= cfg.AccumMinimumRQ && rq <= cfg.AccumMaximumRQ:
		snap.Phase, snap.ReasonCode, snap.Active = PhaseAccum, "asia_box_accum", true
		snap.Confidence = accumConfidence(rq, false, cfg)
	default:
		snap.ReasonCode = "no_mad_signature"
	}
	return snap
}

// scan walks the closed bars at and after the range's seal and links the events
// it finds. It reads only those bars, in order, so the result at a bar never
// depends on a later one.
func scan(candles []market.Candle, rng AsiaRangeSeal, atrAt func(int) float64, cfg Config) *Sequence {
	high, low := rng.High, rng.Low
	accept := max1(cfg.ExpandAcceptCloses)
	seq := &Sequence{RangeID: rng.ID}
	var sw *Sweep
	var exp *Expansion
	reclaimIdx := -1
	var runSide string
	var runCount int
	var runStart int64

	invalidate := func(reason string, at int64) {
		seq.InvalidationReason, seq.InvalidatedAt = reason, at
	}
	for i, c := range candles {
		if c.Time < rng.End {
			continue
		}
		atr := atrAt(i)
		if atr <= 0 {
			continue
		}
		tol := math.Max(cfg.PipSize, atr*0.05)
		sweptHigh, sweptLow := c.High > high+tol, c.Low < low-tol
		side := ""
		switch {
		case c.Close > high:
			side = "high"
		case c.Close < low:
			side = "low"
		}
		switch {
		case side == "":
			runSide, runCount = "", 0
		case side == runSide:
			runCount++
		default:
			runSide, runCount, runStart = side, 1, c.Time
		}
		if sweptHigh && sweptLow && sw == nil {
			invalidate(InvalidTwoSidedSweep, c.Time)
			break
		}
		if sw != nil {
			if (sw.Side == "high" && sweptLow) || (sw.Side == "low" && sweptHigh) {
				invalidate(InvalidConflictingSweep, c.Time)
				break
			}
		}

		if exp != nil {
			// A confirmed expansion fails when price returns through what it left.
			failed := false
			if sw != nil && sw.Reclaimed() {
				failed = (exp.Direction == "BUY" && c.Close < sw.Extreme) || (exp.Direction == "SELL" && c.Close > sw.Extreme)
				if failed {
					invalidate(InvalidSweepExtremeLost, c.Time)
				}
			} else if c.Close >= low && c.Close <= high {
				invalidate(InvalidExpansionBackInto, c.Time)
				failed = true
			}
			if failed {
				break
			}
			continue
		}

		if sw == nil {
			if sweptHigh || sweptLow {
				s := &Sweep{Side: "high", Level: high, Extreme: c.High, At: c.Time, Direction: "SELL"}
				if sweptLow {
					s.Side, s.Level, s.Extreme, s.Direction = "low", low, c.Low, "BUY"
				}
				s.PenetrationATR = penetration(*s, atr)
				sw = s
				if reclaimed(*sw, c, tol) {
					sw.ReclaimAt, reclaimIdx = c.Time, i
					sw.ReclaimDepthATR, sw.CloseLocation = reclaimQuality(*sw, c, atr)
				}
				continue
			}
		} else if !sw.Reclaimed() {
			if extends(*sw, c) {
				sw.Extreme = extreme(*sw, c)
				sw.PenetrationATR = penetration(*sw, atr)
			}
			if reclaimed(*sw, c, tol) {
				sw.ReclaimAt, reclaimIdx = c.Time, i
				sw.ReclaimDepthATR, sw.CloseLocation = reclaimQuality(*sw, c, atr)
				continue
			}
			if runSide == sw.Side && runCount >= accept {
				// Accepted beyond the boundary: this was a breakout, not a sweep.
				seq.Note = "sweep_accepted_beyond_level"
				sw, reclaimIdx = nil, -1
			} else {
				continue
			}
		} else {
			// A reclaimed sweep: it fails if price closes beyond its extreme again.
			if (sw.Direction == "BUY" && c.Close < sw.Extreme) || (sw.Direction == "SELL" && c.Close > sw.Extreme) {
				invalidate(InvalidSweepExtremeLost, c.Time)
				break
			}
		}

		// Expansion.
		switch {
		case sw != nil && sw.Reclaimed() && i > reclaimIdx:
			sign := 1.0
			farSide := "high"
			if sw.Direction == "SELL" {
				sign, farSide = -1, "low"
			}
			reclaimClose := candles[reclaimIdx].Close
			disp := sign * (c.Close - reclaimClose) / atr
			beyond, accepted := 0.0, 0
			if runSide == farSide {
				accepted = runCount
				beyond = math.Abs(c.Close-boundary(farSide, high, low)) / atr
			}
			breakOK := beyond >= cfg.ExpandBreakATR && accepted >= accept
			if breakOK || disp >= cfg.ExpandDisplacementATR {
				basis := "displacement"
				if breakOK {
					basis = "break_accepted"
				}
				exp = &Expansion{
					Direction: sw.Direction, Basis: basis, StartedAt: candles[reclaimIdx+1].Time, ConfirmedAt: c.Time,
					DisplacementATR: math.Max(0, disp), BreakDistanceATR: beyond, AcceptedCloses: accepted,
				}
			}
		case sw == nil && runSide != "":
			beyond := math.Abs(c.Close-boundary(runSide, high, low)) / atr
			disp := displacementATR(candles[:i+1], atr, 5)
			breakOK := beyond >= cfg.ExpandBreakATR && runCount >= accept
			if breakOK || disp >= cfg.ExpandDisplacementATR {
				dir := "BUY"
				if runSide == "low" {
					dir = "SELL"
				}
				basis := "displacement"
				if breakOK {
					basis = "break_accepted"
				}
				exp = &Expansion{
					Direction: dir, Basis: basis, StartedAt: runStart, ConfirmedAt: c.Time,
					DisplacementATR: disp, BreakDistanceATR: beyond, AcceptedCloses: runCount,
				}
			}
		}
		if exp != nil {
			exp.Strength = expandConfidence(map[string]float64{
				"break_distance_atr": exp.BreakDistanceATR, "displacement_atr": exp.DisplacementATR, "accepted_closes": float64(exp.AcceptedCloses),
			}, cfg)
		}
	}
	if sw == nil && exp == nil && seq.InvalidationReason == "" {
		// Nothing identifies a sequence: absence is reported as absence.
		return nil
	}
	return finish(seq, rng, sw, exp)
}

// finish links the found events into the sequence: its identity, stages, status,
// direction and last event time.
func finish(seq *Sequence, rng AsiaRangeSeal, sw *Sweep, exp *Expansion) *Sequence {
	seq.Sweep, seq.Expansion = sw, exp
	seq.Stages = []string{StageRange}
	last := rng.End
	bump := func(at int64) {
		if at > last {
			last = at
		}
	}
	switch {
	case sw != nil:
		seq.ID = fmt.Sprintf("%s:%s:%d", rng.ID, sw.Side, sw.At)
		seq.Stages = append(seq.Stages, StageSweep)
		bump(sw.At)
		if sw.Reclaimed() {
			seq.Stages = append(seq.Stages, StageReclaim)
			seq.Direction = sw.Direction
			bump(sw.ReclaimAt)
		}
	case exp != nil:
		seq.ID = fmt.Sprintf("%s:expansion:%s:%d", rng.ID, exp.Direction, exp.StartedAt)
	default:
		seq.ID = fmt.Sprintf("%s:invalid:%d", rng.ID, seq.InvalidatedAt)
	}
	if exp != nil {
		seq.Stages = append(seq.Stages, StageExpansion)
		seq.Direction = exp.Direction
		bump(exp.ConfirmedAt)
	}
	if seq.InvalidationReason != "" {
		bump(seq.InvalidatedAt)
		seq.Status = SequenceInvalidated
	} else if sw != nil && sw.Reclaimed() && exp != nil {
		seq.Status = SequenceComplete
	} else {
		seq.Status = SequencePartial
	}
	seq.LastEventAt = last
	return seq
}

func boundary(side string, high, low float64) float64 {
	if side == "high" {
		return high
	}
	return low
}

func reclaimed(s Sweep, c market.Candle, tol float64) bool {
	if s.Side == "high" {
		return c.Close <= s.Level+tol
	}
	return c.Close >= s.Level-tol
}

func extends(s Sweep, c market.Candle) bool {
	if s.Side == "high" {
		return c.High > s.Extreme
	}
	return c.Low < s.Extreme
}

func extreme(s Sweep, c market.Candle) float64 {
	if s.Side == "high" {
		return math.Max(s.Extreme, c.High)
	}
	return math.Min(s.Extreme, c.Low)
}

func penetration(s Sweep, atr float64) float64 {
	return math.Abs(s.Extreme-s.Level) / atr
}

// reclaimQuality is how far inside the boundary the reclaiming bar closed (in ATR)
// and where in its own range it closed (1 = at the extreme away from the sweep).
func reclaimQuality(s Sweep, c market.Candle, atr float64) (depth, location float64) {
	r := c.High - c.Low
	if s.Side == "high" {
		return (s.Level - c.Close) / atr, safe(c.High-c.Close, r)
	}
	return (c.Close - s.Level) / atr, safe(c.Close-c.Low, r)
}

// closesBeyond counts the consecutive latest closes beyond the range on the side
// the last close is on.
func closesBeyond(c []market.Candle, a AsiaRangeSeal, vs string) int {
	if vs != "above" && vs != "below" {
		return 0
	}
	n := 0
	for i := len(c) - 1; i >= 0; i-- {
		ok := c[i].Close > a.High
		if vs == "below" {
			ok = c[i].Close < a.Low
		}
		if !ok {
			break
		}
		n++
	}
	return n
}
