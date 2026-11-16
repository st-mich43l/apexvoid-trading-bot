// Package mad is the Accumulation / Manipulation / Distribution (AMD) market
// context of the Go Analysis Engine. It is a pure technical-analysis domain: it
// owns no Redis, account, execution, or strategy-policy state, and nothing here is
// a gate. AMD is supporting evidence only.
//
// Version 3 reads the Asia range as an event-linked sequence over closed bars: the
// range is sealed when its session window closes, a sweep of its edge and the
// reclaim of that edge are dated events, and an expansion is confirmed from a later
// bar. Every event is causal (it is only ever read from bars that have closed) and
// ordered range sealed <= sweep <= reclaim <= expansion <= observation. A phase is
// never manufactured to complete a sequence: a lone sweep or a lone expansion is a
// partial sequence, and evidence that has gone stale or been invalidated is not
// reported as current.
package mad

import (
	"math"
	"strings"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

const (
	Version      = 3
	PhaseAccum   = "accum"
	PhaseManip   = "manip"
	PhaseExpand  = "expand"
	PhaseUnclear = "unclear"
)

// Config is the canonical MAD analysis contract. Session hours are supplied
// by the existing session configuration; these values are the MAD-specific
// thresholds formerly read from execution.mad in Python.
type Config struct {
	AsiaStartHour              int
	LondonStartHour            int
	AccumMinimumRQ             float64
	AccumMaximumRQ             float64
	ExpandBreakATR             float64
	ExpandDisplacementATR      float64
	ExpandAcceptCloses         int
	ManipMinimumPenetrationATR float64
	ManipMinimumReclaimATR     float64
	PipSize                    float64
	// SequenceValidityHours is how long after its last event (or, with no event,
	// after the range sealed) a sequence still counts as current evidence. It is a
	// descriptive freshness bound, never an eligibility gate.
	SequenceValidityHours float64
	// AsiaWindow returns the [start, end) of the most recent Asia session that has
	// opened at or before at. The engine supplies the session calendar here, so the
	// range follows daylight saving exactly like the session domain. Nil falls back
	// to the fixed UTC hours above (isolated tests only).
	AsiaWindow func(at int64) (start, end int64, ok bool)
}

// AsiaRangeSeal is the accumulation range: the Asia session's high and low over
// the closed bars of its own window. ID is stable for the session occurrence and
// identical on every timeframe; Timeframe records which bars measured it.
type AsiaRangeSeal struct {
	ID           string
	DayKey       string
	Timeframe    market.Timeframe
	High         float64
	Low          float64
	Start        int64 // window open, Unix seconds
	End          int64 // window close; the range is sealed by the first bar at or after it
	Sealed       bool
	SealedAt     int64 // End when sealed, else 0
	BarCount     int
	ExpectedBars int
	Source       string
	UpdatedAt    int64
}

func (r AsiaRangeSeal) Width() float64 { return math.Max(0, r.High-r.Low) }

// Snapshot is one timeframe's AMD read as of its last closed bar. The phase and
// direction fields are the compatible summary; Sequence carries the dated events
// they were derived from. Active is false whenever the evidence is stale,
// invalidated or unavailable: the facts stay for audit but are not current.
type Snapshot struct {
	Phase                 string
	Asia                  *AsiaRangeSeal
	Sequence              *Sequence
	Timeframe             market.Timeframe
	Active                bool
	ObservedAt            int64 // open time of the last closed bar read
	RangeQualityATR       *float64
	PriceVsAsia           string
	SweepSide             string
	Reclaim               bool
	ReasonCode            string
	ManipulationDirection string
	ExpansionDirection    string
	Confidence            float64
	Measured              map[string]float64
	Version               int
}

type FeatureScores struct{ Accum, Manip, Expand float64 }

// AffinityScore is the bounded, directional MAD quality used by the legacy
// detector as a soft confluence input.  It is deliberately not an eligibility
// result: a zero score records that this phase does not support the candidate
// direction/family, while the execution policy still owns all live gates.
type AffinityScore struct {
	PhaseScore     float64
	DirectionScore float64
	StrategyScore  float64
	Confidence     float64
	Final          float64
	// Neutral marks a read that is deliberately not evidence for or against a
	// setup (accumulation, or no phase): Final is 0 and it must not be read as a
	// penalty.
	Neutral bool
}

// StrategyKey maps the registered Go strategy IDs to the same MAD affinity
// vocabulary used by mad_gate_strategy_for_setup in Python.  The mapping is
// explicit: substring guesses were a source of silent detector drift.
func StrategyKey(strategy string) string {
	switch normalize(strategy) {
	case "impulse_pullback", "impulse_retrace":
		return "impulse_pullback_continuation"
	case "":
		return ""
	default:
		// Zone, key-level, CRT, flip, fade and trendline candidates are
		// structural reactions in the Python taxonomy.
		return "structural_reaction"
	}
}

// Affinity is the bounded, directional MAD affinity: phase evidence, directional
// agreement, strategy-family agreement and snapshot confidence, multiplied and
// clamped to [0,1]. It feeds only the V2 confluence telemetry; the selected (V1)
// stars never read it.
//
// Accumulation is neutral by definition. A box that has not been left says nothing
// about which way a setup should trade, so it carries no directional and no
// family evidence and Final is 0 with Neutral set. (The formula used to give the
// same 0 only because StrategyScore was never assigned in this phase while
// DirectionScore was set to 1 for any direction; the zero was an accident of the
// multiplication. A bonus here would be arbitrary and none is given.) Evidence that
// is not Active (stale, invalidated, unavailable) scores 0 as well, so old context
// can never lift a setup.
func Affinity(snapshot Snapshot, direction, strategy string) AffinityScore {
	features := Features(snapshot)
	want := normalize(direction)
	key := StrategyKey(strategy)
	score := AffinityScore{Confidence: clamp(snapshot.Confidence)}
	if !snapshot.Active {
		score.Neutral = snapshot.Phase == PhaseUnclear || snapshot.Phase == ""
		return score
	}
	switch snapshot.Phase {
	case PhaseAccum:
		score.Neutral = true
		score.PhaseScore = features.Accum
		score.Confidence = clamp(snapshot.Confidence)
		return score
	case PhaseManip:
		score.PhaseScore = features.Manip
		if snapshot.ManipulationDirection != "" && normalize(snapshot.ManipulationDirection) == want {
			score.DirectionScore = 1
		}
		if key == "structural_reaction" {
			score.StrategyScore = 1
		}
	case PhaseExpand:
		score.PhaseScore = features.Expand
		if snapshot.ExpansionDirection != "" && normalize(snapshot.ExpansionDirection) == want {
			score.DirectionScore = 1
		}
		if key == "impulse_pullback_continuation" {
			score.StrategyScore = 1
		}
	}
	score.PhaseScore = clamp(score.PhaseScore)
	score.DirectionScore = clamp(score.DirectionScore)
	score.StrategyScore = clamp(score.StrategyScore)
	score.Final = clamp(score.PhaseScore * score.DirectionScore * score.StrategyScore * score.Confidence)
	return score
}

func Features(s Snapshot) FeatureScores {
	var f FeatureScores
	if s.RangeQualityATR != nil && s.PriceVsAsia == "inside" {
		f.Accum = rqScore(*s.RangeQualityATR, s.Phase == PhaseAccum)
	}
	if s.Phase == PhaseAccum {
		f.Accum = math.Max(f.Accum, .72)
	}
	if s.Reclaim {
		f.Manip = .95
	} else if s.SweepSide != "" {
		f.Manip = .55
	}
	if s.Phase == PhaseManip {
		f.Manip = math.Max(f.Manip, .8)
	}
	f.Expand = math.Max(math.Min(s.Measured["break_distance_atr"]/math.Max(1e-9, .35)*.6, 1), 0)
	f.Expand = math.Max(f.Expand, math.Min(s.Measured["displacement_atr"]/1.25*.85, 1))
	f.Expand = math.Max(f.Expand, math.Min(s.Measured["accepted_closes"]/2*.5, 1))
	if s.Phase == PhaseExpand {
		f.Expand = math.Max(f.Expand, .78)
	}
	return f
}

func asiaBounds(ts int64, asiaStart, londonStart int) (int64, int64) {
	d := time.Unix(ts, 0).UTC()
	if d.Hour() < londonStart {
		start := time.Date(d.Year(), d.Month(), d.Day()-1, asiaStart, 0, 0, 0, time.UTC)
		return start.Unix(), time.Date(d.Year(), d.Month(), d.Day(), londonStart, 0, 0, 0, time.UTC).Unix()
	}
	if d.Hour() >= asiaStart {
		start := time.Date(d.Year(), d.Month(), d.Day(), asiaStart, 0, 0, 0, time.UTC)
		return start.Unix(), time.Date(d.Year(), d.Month(), d.Day()+1, londonStart, 0, 0, 0, time.UTC).Unix()
	}
	start := time.Date(d.Year(), d.Month(), d.Day()-1, asiaStart, 0, 0, 0, time.UTC)
	return start.Unix(), time.Date(d.Year(), d.Month(), d.Day(), londonStart, 0, 0, 0, time.UTC).Unix()
}

func priceVsAsia(p float64, a AsiaRangeSeal) string {
	if p > a.High {
		return "above"
	}
	if p < a.Low {
		return "below"
	}
	return "inside"
}
func positive(v float64) float64 {
	if v > 0 {
		return v
	}
	return 0
}
func max1(v int) int {
	if v < 1 {
		return 1
	}
	return v
}
func displacementATR(c []market.Candle, atr float64, lookback int) float64 {
	if len(c) < 2 || atr <= 0 {
		return 0
	}
	k := lookback
	if k >= len(c) {
		k = len(c) - 1
	}
	return math.Abs(c[len(c)-1].Close-c[len(c)-1-k].Close) / atr
}
func safe(a, b float64) float64 {
	if b <= 0 {
		return .5
	}
	return a / b
}
func manipConfidence(m map[string]float64, c Config) float64 {
	p := clamp(m["sweep_penetration_atr"] / math.Max(1e-9, c.ManipMinimumPenetrationATR*4))
	r := clamp(m["reclaim_depth_atr"] / math.Max(1e-9, c.ManipMinimumReclaimATR*4))
	return clamp(.45*p + .35*r + .20*clamp(m["close_location"]))
}
func expandConfidence(m map[string]float64, c Config) float64 {
	b := clamp(m["break_distance_atr"] / math.Max(1e-9, c.ExpandBreakATR*3))
	d := clamp(m["displacement_atr"] / math.Max(1e-9, c.ExpandDisplacementATR))
	a := clamp(m["accepted_closes"] / float64(max1(c.ExpandAcceptCloses)*2))
	return clamp(.4*b + .4*d + .2*a)
}
func accumConfidence(rq float64, building bool, c Config) float64 {
	max := c.AccumMaximumRQ
	if building {
		max = 24
	}
	mid := (c.AccumMinimumRQ + max) / 2
	span := math.Max(1e-9, (max-c.AccumMinimumRQ)/2)
	return clamp(1 - .5*math.Abs(rq-mid)/span)
}
func rqScore(rq float64, building bool) float64 {
	max := 6.
	if building {
		max = 24
	}
	if rq < .8 {
		return clamp(rq / .8 * .35)
	}
	if rq <= 2.5 {
		return clamp(.55 + (rq-.8)/(2.5-.8)*.45)
	}
	if rq <= max {
		return clamp(1 - (rq-2.5)/(max-2.5)*.55)
	}
	return .15
}
func clamp(v float64) float64 { return math.Max(0, math.Min(1, v)) }

func normalize(value string) string {
	return strings.ToLower(strings.TrimSpace(value))
}
