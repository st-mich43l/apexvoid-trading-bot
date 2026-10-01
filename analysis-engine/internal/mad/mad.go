// Package mad ports the Python MAD-0 Asia-range classifier. It is a pure
// technical-analysis domain: it owns no Redis, account, execution, or
// strategy-policy state.
package mad

import (
	"math"
	"strings"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

const (
	Version      = 2
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
}

type AsiaRangeSeal struct {
	DayKey    string
	High      float64
	Low       float64
	Sealed    bool
	SealedAt  int64
	BarCount  int
	Source    string
	UpdatedAt int64
}

func (r AsiaRangeSeal) Width() float64 { return math.Max(0, r.High-r.Low) }

type Snapshot struct {
	Phase                 string
	Asia                  *AsiaRangeSeal
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
}

// SoftBonus is the small family-specific MAD confluence bonus from the
// Python detector.  It is telemetry/scoring only and must never be used as a
// publish or execution veto.
func SoftBonus(phase, family string) float64 {
	p := normalize(phase)
	f := normalize(family)
	if p == PhaseAccum && (f == "range_scalp" || f == "range_edge" || f == "range_edge_mean_reversion") {
		return 0.12
	}
	if p == PhaseManip && (f == "reaction" || f == "liquidity" || f == "structural_reaction" || f == "liquidity_sweep_reversal") {
		return 0.12
	}
	return 0
}

// StrategyKey maps the registered Go strategy IDs to the same MAD affinity
// vocabulary used by mad_gate_strategy_for_setup in Python.  The mapping is
// explicit: substring guesses were a source of silent detector drift.
func StrategyKey(strategy string) string {
	switch normalize(strategy) {
	case "range_edge", "range_sweep":
		return "range_edge_mean_reversion"
	case "liquidity_sweep":
		return "liquidity_sweep_reversal"
	case "impulse_pullback", "momentum_ride":
		return "impulse_pullback_continuation"
	case "box_breakout", "scalp_breakout_retest":
		return "breakout_retest"
	case "":
		return ""
	default:
		// Zone, key-level, CRT, flip, session and trendline candidates are
		// structural reactions in the Python taxonomy.
		return "structural_reaction"
	}
}

// Affinity computes the Python MAD affinity formula exactly: phase evidence,
// directional agreement, strategy-family agreement, and snapshot confidence
// are multiplied and clamped to [0,1].
func Affinity(snapshot Snapshot, direction, strategy string) AffinityScore {
	features := Features(snapshot)
	want := normalize(direction)
	key := StrategyKey(strategy)
	score := AffinityScore{Confidence: clamp(snapshot.Confidence)}
	switch snapshot.Phase {
	case PhaseAccum:
		score.PhaseScore = features.Accum
		if want == "BUY" || want == "SELL" {
			score.DirectionScore = 1
		}
		if key == "range_edge_mean_reversion" || key == "range_sweep" {
			score.StrategyScore = 1
		}
	case PhaseManip:
		score.PhaseScore = features.Manip
		if snapshot.ManipulationDirection != "" && normalize(snapshot.ManipulationDirection) == want {
			score.DirectionScore = 1
		}
		if key == "structural_reaction" || key == "liquidity_sweep_reversal" {
			score.StrategyScore = 1
		}
	case PhaseExpand:
		score.PhaseScore = features.Expand
		if snapshot.ExpansionDirection != "" && normalize(snapshot.ExpansionDirection) == want {
			score.DirectionScore = 1
		}
		if key == "impulse_pullback_continuation" || key == "breakout_retest" {
			score.StrategyScore = 1
		}
	}
	score.PhaseScore = clamp(score.PhaseScore)
	score.DirectionScore = clamp(score.DirectionScore)
	score.StrategyScore = clamp(score.StrategyScore)
	score.Final = clamp(score.PhaseScore * score.DirectionScore * score.StrategyScore * score.Confidence)
	return score
}

// AsiaDayKey matches mad_phase.py's session-day identity. UTC is explicit so
// a host timezone can never change the technical read.
func AsiaDayKey(ts int64, asiaStartHour, londonStartHour int) string {
	d := time.Unix(ts, 0).UTC()
	if d.Hour() < londonStartHour || d.Hour() >= 24 {
		d = d.AddDate(0, 0, -1)
	} else if d.Hour() < asiaStartHour {
		d = d.AddDate(0, 0, -1)
	}
	return time.Date(d.Year(), d.Month(), d.Day(), asiaStartHour, 0, 0, 0, time.UTC).Format("2006-01-02")
}

// UpdateAsiaRangeSeal derives the current day's Asia box from closed bars.
// The input must be time ascending and include the latest closed bar.
func UpdateAsiaRangeSeal(candles []market.Candle, now int64, session string, cfg Config) *AsiaRangeSeal {
	if len(candles) == 0 {
		return nil
	}
	day := AsiaDayKey(now, cfg.AsiaStartHour, cfg.LondonStartHour)
	start, end := asiaBounds(now, cfg.AsiaStartHour, cfg.LondonStartHour)
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
		return nil
	}
	sealed := session != "ASIA" && session != "asia"
	var sealedAt int64
	if sealed {
		sealedAt = now
	}
	return &AsiaRangeSeal{DayKey: day, High: high, Low: low, Sealed: sealed, SealedAt: sealedAt, BarCount: count, Source: "m5", UpdatedAt: now}
}

func Classify(candles []market.Candle, atr, atrLong float64, session, structure string, asia *AsiaRangeSeal, now int64, cfg Config) Snapshot {
	base := Snapshot{Phase: PhaseUnclear, Asia: asia, ReasonCode: "asia_range_missing", Version: Version, Measured: map[string]float64{}}
	if asia == nil || asia.Width() <= 0 {
		return base
	}
	if now > 0 && asia.DayKey != AsiaDayKey(now, cfg.AsiaStartHour, cfg.LondonStartHour) {
		base.ReasonCode = "asia_range_stale_or_missing"
		return base
	}
	atr = positive(atr)
	if atr <= 0 {
		return base
	}
	rq := asia.Width() / atr
	base.RangeQualityATR = &rq
	price := candles[len(candles)-1].Close
	vs := priceVsAsia(price, *asia)
	base.PriceVsAsia = vs
	base.Measured["session"] = 0 // presence marker; string is carried by ReasonCode/fields in the envelope
	if atrLong > 0 {
		base.Measured["atr_short_long_ratio"] = atr / atrLong
	}
	if vs == "above" {
		base.Measured["break_distance_atr"] = (price - asia.High) / atr
	}
	if vs == "below" {
		base.Measured["break_distance_atr"] = (asia.Low - price) / atr
	}
	base.Measured["displacement_atr"] = displacementATR(candles, atr, 5)
	base.Measured["accepted_closes"] = float64(acceptedCloses(candles, *asia, vs))

	last := candles[len(candles)-1]
	base.SweepSide, base.Reclaim = sweepReclaim(last, *asia, math.Max(cfg.PipSize, atr*0.05))
	if base.SweepSide == "both" {
		base.ReasonCode = "asia_double_sweep"
		return base
	}
	if base.SweepSide != "" && base.Reclaim {
		base.Phase, base.ReasonCode = PhaseManip, "asia_sweep_reclaim"
		base.ManipulationDirection = map[string]string{"high": "SELL", "low": "BUY"}[base.SweepSide]
		base.Measured["sweep_penetration_atr"], base.Measured["reclaim_depth_atr"], base.Measured["close_location"] = manipQuality(last, *asia, atr, base.SweepSide)
		base.Confidence = manipConfidence(base.Measured, cfg)
		return base
	}
	breakOK := base.Measured["break_distance_atr"] >= cfg.ExpandBreakATR && base.Measured["accepted_closes"] >= float64(max1(cfg.ExpandAcceptCloses))
	displacedOK := base.Measured["displacement_atr"] >= cfg.ExpandDisplacementATR
	if asia.Sealed && (vs == "above" || vs == "below") && (breakOK || displacedOK) {
		base.Phase = PhaseExpand
		base.ReasonCode = "asia_strong_displacement"
		if breakOK {
			base.ReasonCode = "asia_break_accepted"
		}
		if vs == "above" {
			base.ExpansionDirection = "BUY"
		} else {
			base.ExpansionDirection = "SELL"
		}
		base.Confidence = expandConfidence(base.Measured, cfg)
		return base
	}
	building := (session == "ASIA" || session == "asia") && !asia.Sealed && vs == "inside" && rq >= cfg.AccumMinimumRQ && rq <= 24
	// Match Python's grouped predicate exactly: a building Asia box still
	// requires the canonical range/unknown structure read.  The previous Go
	// expression let `|| building` bypass that owner boundary.
	structureOK := structure == "range" || structure == "unknown" || structure == ""
	sealedRQOK := rq >= cfg.AccumMinimumRQ && rq <= cfg.AccumMaximumRQ
	buildingRQOK := building
	if (vs == "inside" || building) && structureOK && (sealedRQOK || buildingRQOK) {
		base.Phase, base.ReasonCode = PhaseAccum, "asia_box_accum"
		if building && rq > cfg.AccumMaximumRQ {
			base.ReasonCode = "asia_building_accum"
		}
		base.Confidence = accumConfidence(rq, building, cfg)
		return base
	}
	base.ReasonCode = "no_mad_signature"
	return base
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

func sweepReclaim(c market.Candle, a AsiaRangeSeal, tolerance float64) (string, bool) {
	high := c.High > a.High+tolerance
	low := c.Low < a.Low-tolerance
	if high && low {
		return "both", false
	}
	if high {
		return "high", c.Close <= a.High+tolerance
	}
	if low {
		return "low", c.Close >= a.Low-tolerance
	}
	return "", false
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
func acceptedCloses(c []market.Candle, a AsiaRangeSeal, vs string) int {
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
func manipQuality(c market.Candle, a AsiaRangeSeal, atr float64, side string) (float64, float64, float64) {
	r := c.High - c.Low
	if side == "high" {
		return (c.High - a.High) / atr, (a.High - c.Close) / atr, safe(c.High-c.Close, r)
	}
	return (a.Low - c.Low) / atr, (c.Close - a.Low) / atr, safe(c.Close-c.Low, r)
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
