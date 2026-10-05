package techniquezone

import (
	"math"
	"sort"
	"strconv"
	"strings"
)

// Zone score weights (zones.py module constants).
const (
	freshScore       = 3.0
	singleTouchScore = 1.0
	keyLevelScore    = 2.0
	roundNumberScore = 1.0
	liquidityScore   = 2.0
	htfScore         = 3.0
	sessionLevelScr  = 2.0
	pdPositionScore  = 2.0
	grabAScore       = 2.0
	trendlineScore   = 1.5
)

// SessionRef is the part of a session level zone scoring reads.
type SessionRef struct {
	Name  string
	Price float64
	Swept bool
}

// ScoreInputs are the confluence facts zones are scored against.
type ScoreInputs struct {
	Levels      []Level
	Pools       []Pool
	RoundStep   float64
	HTFZones    []Zone
	Sessions    []SessionRef
	HasEq       bool
	Equilibrium float64
	Grabs       []Grab
	LineValues  []float64 // value at the bar index of every unbroken trendline
	HasBarIndex bool
	PipSize     float64
}

// ScoreZones mirrors zones.score_zones: each zone is scored, then the result is
// ordered by score (high first), fewer touches first, lower first.
func ScoreZones(zones []Zone, in ScoreInputs) []Zone {
	scored := make([]Zone, len(zones))
	for i, zone := range zones {
		scored[i] = scoreZone(zone, in)
	}
	sort.SliceStable(scored, func(i, j int) bool {
		a, b := scored[i], scored[j]
		if a.Score != b.Score {
			return a.Score > b.Score
		}
		if a.Touches != b.Touches {
			return a.Touches < b.Touches
		}
		return a.Low() < b.Low()
	})
	return scored
}

func scoreZone(zone Zone, in ScoreInputs) Zone {
	score := 0.0
	var reasons []string
	switch zone.Touches {
	case 0:
		score += freshScore
		reasons = append(reasons, "fresh")
	case 1:
		score += singleTouchScore
		reasons = append(reasons, "1 touch")
	}
	sourceScore, sourceReasons := sourceScoreOf(zone)
	score += sourceScore
	reasons = append(reasons, sourceReasons...)
	for _, level := range in.Levels {
		if zoneOverlapsLevel(zone, level) {
			score += keyLevelScore
			reasons = append(reasons, "key level")
			break
		}
	}
	if round, ok := roundNumberInside(zone, in.RoundStep); ok {
		score += roundNumberScore
		reasons = append(reasons, "round "+numberText(round))
	}
	if hasLiquidityConfluence(zone, in.Pools, in.PipSize) {
		score += liquidityScore
		reasons = append(reasons, "liquidity pool")
	}
	for _, name := range sessionLevelConfluences(zone, in.Sessions, in.PipSize) {
		score += sessionLevelScr
		reasons = append(reasons, name)
	}
	if in.HasEq {
		if zone.Side == "demand" && zone.High() <= in.Equilibrium {
			score += pdPositionScore
			reasons = append(reasons, "discount")
		} else if zone.Side == "supply" && zone.Low() >= in.Equilibrium {
			score += pdPositionScore
			reasons = append(reasons, "premium")
		}
	}
	if hasGradeAGrab(zone, in.Grabs, in.PipSize) {
		score += grabAScore
		reasons = append(reasons, "sweep A")
	}
	for _, htf := range in.HTFZones {
		if zone.Side == htf.Side && zone.Low() >= htf.Low() && zone.High() <= htf.High() {
			score += htfScore
			reasons = append(reasons, "HTF zone")
			break
		}
	}
	if in.HasBarIndex {
		for _, value := range in.LineValues {
			if zone.Low() <= value && value <= zone.High() {
				score += trendlineScore
				reasons = append(reasons, "TL confluence")
				break
			}
		}
	}
	zone.Score, zone.ScoreReasons = score, reasons
	return zone
}

func sourceScoreOf(zone Zone) (float64, []string) {
	total := 0.0
	var reasons []string
	sources := zone.Sources
	if len(sources) == 0 && zone.Source != "" {
		sources = []string{zone.Source}
	}
	for _, source := range sources {
		value := sourceScores[source]
		if source == "order_block" && zone.BreakKind == "" {
			value = 0
		}
		if value <= 0 {
			continue
		}
		total += value
		reasons = append(reasons, sourceReason(source))
	}
	return math.Min(total, sourceScoreCap), reasons
}

func sourceReason(source string) string {
	switch {
	case source == "order_block":
		return "OB"
	case source == "breaker":
		return "breaker"
	case source == "flip_zone":
		return "flip"
	case source == "supply_demand":
		return "S/D"
	case strings.HasSuffix(source, "_fvg"):
		return "FVG"
	case source == "box_breakout":
		return "box breakout"
	default:
		return source
	}
}

func zoneOverlapsLevel(zone Zone, level Level) bool {
	band := math.Max(level.Band, 0)
	if band == 0 {
		return zone.Low() <= level.Price && level.Price <= zone.High()
	}
	return zone.Low() <= level.Price+band && zone.High() >= level.Price-band
}

func roundNumberInside(zone Zone, step float64) (float64, bool) {
	if step <= 0 {
		return 0, false
	}
	first := math.Ceil(zone.Low()/step) * step
	return first, first <= zone.High()
}

func hasLiquidityConfluence(zone Zone, pools []Pool, pipSize float64) bool {
	width := math.Max(zone.High()-zone.Low(), 0)
	for _, pool := range pools {
		tolerance := math.Max(pool.Band, math.Max(width, pipSize))
		if zone.Side == "demand" && pool.Side == "sell" && zone.Low()-tolerance <= pool.Level && pool.Level <= zone.Low() {
			return true
		}
		if zone.Side == "supply" && pool.Side == "buy" && zone.High() <= pool.Level && pool.Level <= zone.High()+tolerance {
			return true
		}
	}
	return false
}

func sessionLevelConfluences(zone Zone, sessions []SessionRef, pipSize float64) []string {
	var result []string
	width := math.Max(zone.High()-zone.Low(), 0)
	tolerance := math.Max(width, pipSize)
	for _, level := range sessions {
		if level.Swept {
			continue
		}
		seen := false
		for _, name := range result {
			if name == level.Name {
				seen = true
				break
			}
		}
		if seen {
			continue
		}
		if zone.Side == "demand" && isLowSessionLevel(level.Name) {
			if zone.Low() <= level.Price && level.Price <= zone.High() || zone.Low()-tolerance <= level.Price && level.Price <= zone.Low() {
				result = append(result, level.Name)
			}
		}
		if zone.Side == "supply" && isHighSessionLevel(level.Name) {
			if zone.Low() <= level.Price && level.Price <= zone.High() || zone.High() <= level.Price && level.Price <= zone.High()+tolerance {
				result = append(result, level.Name)
			}
		}
	}
	return result
}

func isLowSessionLevel(name string) bool {
	return strings.HasSuffix(name, "_L") || name == "PDL" || name == "PWL"
}

func isHighSessionLevel(name string) bool {
	return strings.HasSuffix(name, "_H") || name == "PDH" || name == "PWH"
}

func hasGradeAGrab(zone Zone, grabs []Grab, pipSize float64) bool {
	for _, grab := range grabs {
		if grab.Grade != "A" || grab.Inducement {
			continue
		}
		if zone.Side == "demand" && grab.Direction == "bull" || zone.Side == "supply" && grab.Direction == "bear" {
			if PoolPointsIntoZone(zone, grab.Pool, pipSize) {
				return true
			}
		}
	}
	return false
}

// PoolPointsIntoZone mirrors zones._pool_points_into_zone.
func PoolPointsIntoZone(zone Zone, pool Pool, pipSize float64) bool {
	width := math.Max(zone.High()-zone.Low(), 0)
	tolerance := math.Max(pool.Band, math.Max(width, pipSize))
	if zone.Side == "demand" && pool.Side == "sell" {
		return zone.Low()-tolerance <= pool.Level && pool.Level <= zone.High()
	}
	if zone.Side == "supply" && pool.Side == "buy" {
		return zone.Low() <= pool.Level && pool.Level <= zone.High()+tolerance
	}
	return false
}

func numberText(value float64) string {
	return strconv.FormatFloat(value, 'f', -1, 64)
}
