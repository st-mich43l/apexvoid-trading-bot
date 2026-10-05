package techniquezone

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// This file ports scalp_ranges.py: symmetric micro-barrier detection with
// dynamic clustering, the controlled one-sided fallback barrier and the
// explicit range states (no_range / provisional / confirmed / post_impulse /
// broken). Every threshold is a ScalpConfig field filled from configuration.

const scalpEpsilon = 1e-9

// Range states.
const (
	RangeStateNoRange     = "no_range"
	RangeStateProvisional = "provisional_range"
	RangeStateConfirmed   = "confirmed_range"
	RangeStatePostImpulse = "post_impulse_range"
	RangeStateBroken      = "broken_range"
)

var scalpSessionNames = map[string]bool{
	"ASIA_H": true, "ASIA_L": true, "LONDON_H": true, "LONDON_L": true, "NY_H": true, "NY_L": true,
	"PDH": true, "PDL": true, "PWH": true, "PWL": true,
}

// ScalpConfig is every tunable of the structure builder.
type ScalpConfig struct {
	Lookback                  int
	ClusterATR                float64
	ClusterMinAbs             float64
	ClusterPipMult            float64
	MinimumTouches            int
	MinimumWickFraction       float64
	EntryToleranceATR         float64
	MaximumEdgeWidthATR       float64
	MinimumWidthATR           float64
	MaximumWidthATR           float64
	MinimumRoomATR            float64
	BreakCloses               int
	MinimumInsideCloses       int
	InsideLookbackBars        int
	RecentBreakoutLookback    int
	RecentBreakoutBufferATR   float64
	RecentBreakoutMinSpanATR  float64
	FallbackEnabled           bool
	FallbackMinConfirmations  int
	FallbackMinWidthATR       float64
	FallbackMaxWidthATR       float64
	FallbackWickFraction      float64
	ProvisionalEnabled        bool
	PostImpulseEnabled        bool
	PostImpulseMinDisplaceATR float64
	PostImpulseMaxContractATR float64
	PostImpulseMinInside      int
	PostImpulseLookbackBars   int
	PostImpulseRecentBars     int
	PipSize                   float64
	RoundStep                 float64
}

// ScalpBarrier mirrors scalp_ranges.ScalpBarrier.
type ScalpBarrier struct {
	Side             string // "support" | "resistance"
	Level, Low, High float64
	Touches          int
	WickRejections   int
	AcceptedCloses   int
	LastTouchIndex   int
	FirstTouchIndex  int
	BodyHolds        int
	Age              int
	Tags             []string
	Score            float64
	Grade            string
	ConfidenceGrade  string
	Sources          []string
	ClassName        string
	Fallback         bool
	Invalidated      bool
	Tested           bool
}

// ScalpRange mirrors scalp_ranges.ScalpRange.
type ScalpRange struct {
	Lower, Upper ScalpBarrier
	Equilibrium  float64
	WidthATR     float64
	Quality      float64
	State        string
	InsideCloses int
	OneSided     bool
	PostImpulse  bool
}

// ScalpLine is the part of an unbroken trendline the barrier tags read.
type ScalpLine struct {
	Kind    string // "support" | "resistance"
	Broken  bool
	Touches int
	// Value is the line's value at the latest bar.
	Value float64
}

// ScalpStructure is the builder's result.
type ScalpStructure struct {
	Barriers []ScalpBarrier
	Range    *ScalpRange
	State    string
}

type scalpContact struct {
	index        int
	price        float64
	wickFraction float64
	rejected     bool
	bodyHold     bool
	source       string
}

// BuildScalpStructure mirrors build_scalp_structure_detailed. atr is the
// analysis ATR series aligned with bars, sessions the frame's session levels,
// lines the frame's trendlines and the range bounds the frame's regime box.
func BuildScalpStructure(bars []market.Candle, atr []float64, sessions []SessionRef, lines []ScalpLine, regimeHigh, regimeLow float64, hasRegime bool, cfg ScalpConfig) ScalpStructure {
	if len(bars) == 0 {
		return ScalpStructure{State: RangeStateNoRange}
	}
	atrValue := lastPositive(atr)
	if atrValue <= 0 {
		return ScalpStructure{State: RangeStateNoRange}
	}
	lookback := maxInt(5, cfg.Lookback)
	start := maxInt(0, len(bars)-lookback)
	frame := bars[start:]
	offset := len(bars) - len(frame)
	clusterTolerance := scalpClusterTolerance(atrValue, frame, cfg)
	entryTolerance := math.Max(scalpEpsilon, atrValue*math.Max(0, cfg.EntryToleranceATR))
	maxEdgeWidth := math.Max(entryTolerance, atrValue*math.Max(0.05, cfg.MaximumEdgeWidthATR))
	minimumTouches := maxInt(2, cfg.MinimumTouches)
	minimumWick := math.Max(0, math.Min(1, cfg.MinimumWickFraction))
	breakCloses := maxInt(1, cfg.BreakCloses)

	var atrTail []float64
	if len(atr) >= len(frame) {
		atrTail = atr[len(atr)-len(frame):]
	}
	contacts := scalpContacts(frame, offset, atrTail, minimumWick)
	var barriers []ScalpBarrier
	for _, side := range []string{"support", "resistance"} {
		for _, cluster := range clusterContacts(contacts[side], clusterTolerance) {
			episodes := touchEpisodes(cluster)
			if len(episodes) < minimumTouches {
				continue
			}
			prices := make([]float64, len(episodes))
			wicks, holds := 0, 0
			for i, episode := range episodes {
				prices[i] = episode.price
				if episode.rejected {
					wicks++
				}
				if episode.bodyHold {
					holds++
				}
			}
			level := pySum(prices...) / float64(len(episodes))
			// Primary barriers still require two wick rejections.
			if wicks < 2 {
				continue
			}
			accepted := maxAcceptedCloseRun(bars, level, entryTolerance, side, episodes[0].index)
			if accepted >= breakCloses {
				continue
			}
			halfWidth := math.Min(entryTolerance, maxEdgeWidth/2)
			sources := uniqueSorted(episodes)
			tags := barrierTags(side, level, len(episodes), wicks, clusterTolerance, len(bars)-1, sessions, lines, regimeHigh, regimeLow, hasRegime, cfg.RoundStep)
			score := barrierScore(len(episodes), wicks, accepted, len(tags)-2, episodes[len(episodes)-1].index, len(bars), holds, false)
			grade := barrierGrade(score, len(episodes), wicks, tags)
			barriers = append(barriers, ScalpBarrier{
				Side: side, Level: level, Low: level - halfWidth, High: level + halfWidth,
				Touches: len(episodes), WickRejections: wicks, AcceptedCloses: accepted,
				LastTouchIndex: episodes[len(episodes)-1].index, FirstTouchIndex: episodes[0].index, BodyHolds: holds,
				Age: maxInt(0, len(bars)-1-episodes[len(episodes)-1].index), Tags: tags, Score: score,
				Grade: grade, ConfidenceGrade: grade, Sources: sources, ClassName: barrierClass(grade, tags), Tested: true,
			})
		}
	}

	barriers = dedupBarriers(barriers, clusterTolerance)
	current := bars[len(bars)-1].Close
	if recentBreakoutDisplacement(bars, atrValue, cfg) {
		return ScalpStructure{Barriers: barriers, State: RangeStateBroken}
	}
	var supports, resistances []ScalpBarrier
	for _, barrier := range barriers {
		if barrier.Side == "support" {
			supports = append(supports, barrier)
		} else {
			resistances = append(resistances, barrier)
		}
	}
	if cfg.FallbackEnabled {
		switch {
		case len(resistances) > 0 && len(supports) == 0:
			if fallback, ok := fallbackBarrier("support", frame, offset, bars, atrValue, entryTolerance, maxEdgeWidth, current, sessions, resistances, cfg); ok {
				barriers = append(barriers, fallback)
			}
		case len(supports) > 0 && len(resistances) == 0:
			if fallback, ok := fallbackBarrier("resistance", frame, offset, bars, atrValue, entryTolerance, maxEdgeWidth, current, sessions, supports, cfg); ok {
				barriers = append(barriers, fallback)
			}
		}
	}
	barriers = dedupBarriers(barriers, clusterTolerance)
	best, state := bestRangeWithState(barriers, current, atrValue, cfg, bars)
	return ScalpStructure{Barriers: barriers, Range: best, State: state}
}

func lastPositive(series []float64) float64 {
	for i := len(series) - 1; i >= 0; i-- {
		if v := series[i]; !math.IsNaN(v) && !math.IsInf(v, 0) {
			if v > 0 {
				return v
			}
			return 0
		}
	}
	return 0
}

func recentBreakoutDisplacement(bars []market.Candle, atrValue float64, cfg ScalpConfig) bool {
	breakCloses := maxInt(1, cfg.BreakCloses)
	if len(bars) < breakCloses+6 || atrValue <= 0 {
		return false
	}
	priorEnd := len(bars) - breakCloses
	priorStart := maxInt(0, priorEnd-cfg.RecentBreakoutLookback)
	if priorStart >= priorEnd {
		return false
	}
	prior, recent := bars[priorStart:priorEnd], bars[priorEnd:]
	priorHigh, priorLow := prior[0].High, prior[0].Low
	for _, bar := range prior[1:] {
		priorHigh, priorLow = math.Max(priorHigh, bar.High), math.Min(priorLow, bar.Low)
	}
	up, down := true, true
	for _, bar := range recent {
		up = up && bar.Close > priorHigh+cfg.RecentBreakoutBufferATR*atrValue
		down = down && bar.Close < priorLow-cfg.RecentBreakoutBufferATR*atrValue
	}
	if !up && !down {
		return false
	}
	high, low := recent[0].High, recent[0].Low
	for _, bar := range recent[1:] {
		high, low = math.Max(high, bar.High), math.Min(low, bar.Low)
	}
	return (high-low)/atrValue >= cfg.RecentBreakoutMinSpanATR
}

func scalpClusterTolerance(atrValue float64, frame []market.Candle, cfg ScalpConfig) float64 {
	atrComponent := atrValue * math.Max(0, cfg.ClusterATR)
	candleNoise := 0.0
	if len(frame) > 0 && atrComponent > 0 {
		spreads := make([]float64, 0, len(frame))
		for _, bar := range frame {
			spreads = append(spreads, bar.High-bar.Low)
		}
		sort.Float64s(spreads)
		n := len(spreads)
		median := spreads[n/2]
		if n%2 == 0 {
			median = (spreads[n/2-1] + spreads[n/2]) / 2
		}
		candleNoise = math.Min(median*0.10, atrComponent)
	}
	pip := math.Max(scalpEpsilon, cfg.PipSize)
	return math.Max(scalpEpsilon, math.Max(math.Max(0, cfg.ClusterMinAbs), math.Max(atrComponent, math.Max(pip*math.Max(0, cfg.ClusterPipMult), candleNoise))))
}

func scalpContacts(frame []market.Candle, offset int, atrTail []float64, minimumWick float64) map[string][]scalpContact {
	result := map[string][]scalpContact{"support": nil, "resistance": nil}
	micro := map[string]bool{}
	for _, swing := range FindSwings(frame, 1, 0, 0, atrTail, -1) {
		micro[fmt.Sprintf("%d:%s", swing.Index+offset, swing.Kind)] = true
	}
	for local, bar := range frame {
		index := local + offset
		span := bar.High - bar.Low
		if math.IsNaN(span) || span <= scalpEpsilon {
			continue
		}
		upperFraction := math.Max(0, bar.High-math.Max(bar.Open, bar.Close)) / span
		lowerFraction := math.Max(0, math.Min(bar.Open, bar.Close)-bar.Low) / span
		upperRejected := upperFraction >= minimumWick && bar.Close < bar.High
		lowerRejected := lowerFraction >= minimumWick && bar.Close > bar.Low
		upperBodyHold := bar.Close >= bar.High-span*0.15 && bar.Close <= bar.Open
		lowerBodyHold := bar.Close <= bar.Low+span*0.15 && bar.Close >= bar.Open
		if upperRejected || micro[fmt.Sprintf("%d:high", index)] {
			source := "swing"
			if upperRejected {
				source = "wick"
			}
			result["resistance"] = append(result["resistance"], scalpContact{index: index, price: bar.High, wickFraction: upperFraction, rejected: upperRejected, bodyHold: upperBodyHold, source: source})
		}
		if lowerRejected || micro[fmt.Sprintf("%d:low", index)] {
			source := "swing"
			if lowerRejected {
				source = "wick"
			}
			result["support"] = append(result["support"], scalpContact{index: index, price: bar.Low, wickFraction: lowerFraction, rejected: lowerRejected, bodyHold: lowerBodyHold, source: source})
		}
	}
	return result
}

func clusterContacts(contacts []scalpContact, tolerance float64) [][]scalpContact {
	sorted := append([]scalpContact(nil), contacts...)
	sort.SliceStable(sorted, func(i, j int) bool {
		if sorted[i].price != sorted[j].price {
			return sorted[i].price < sorted[j].price
		}
		return sorted[i].index < sorted[j].index
	})
	var clusters [][]scalpContact
	for _, contact := range sorted {
		if len(clusters) == 0 {
			clusters = append(clusters, []scalpContact{contact})
			continue
		}
		current := clusters[len(clusters)-1]
		prices := make([]float64, len(current))
		low, high := contact.price, contact.price
		for i, item := range current {
			prices[i] = item.price
			low, high = math.Min(low, item.price), math.Max(high, item.price)
		}
		center := pySum(prices...) / float64(len(current))
		if math.Abs(contact.price-center) <= tolerance && high-low <= 2*tolerance {
			clusters[len(clusters)-1] = append(current, contact)
		} else {
			clusters = append(clusters, []scalpContact{contact})
		}
	}
	return clusters
}

// touchEpisodes mirrors _touch_episodes: adjacent contacts form one episode,
// represented by the rejected, then body-held, then widest-wick, then earliest
// contact (the first maximal element wins ties, as Python's max does).
func touchEpisodes(cluster []scalpContact) []scalpContact {
	sorted := append([]scalpContact(nil), cluster...)
	sort.SliceStable(sorted, func(i, j int) bool { return sorted[i].index < sorted[j].index })
	var episodes [][]scalpContact
	for _, contact := range sorted {
		if n := len(episodes); n > 0 && contact.index <= episodes[n-1][len(episodes[n-1])-1].index+1 {
			episodes[n-1] = append(episodes[n-1], contact)
		} else {
			episodes = append(episodes, []scalpContact{contact})
		}
	}
	out := make([]scalpContact, 0, len(episodes))
	for _, episode := range episodes {
		best := episode[0]
		for _, item := range episode[1:] {
			if episodeBetter(item, best) {
				best = item
			}
		}
		out = append(out, best)
	}
	return out
}

func episodeBetter(a, b scalpContact) bool {
	if a.rejected != b.rejected {
		return a.rejected
	}
	if a.bodyHold != b.bodyHold {
		return a.bodyHold
	}
	if a.wickFraction != b.wickFraction {
		return a.wickFraction > b.wickFraction
	}
	return -a.index > -b.index
}

func uniqueSorted(episodes []scalpContact) []string {
	seen := map[string]bool{}
	var out []string
	for _, episode := range episodes {
		if !seen[episode.source] {
			seen[episode.source] = true
			out = append(out, episode.source)
		}
	}
	sort.Strings(out)
	if len(out) == 0 {
		return []string{"wick_cluster"}
	}
	return out
}

func maxAcceptedCloseRun(bars []market.Candle, level, tolerance float64, side string, start int) int {
	longest, current := 0, 0
	for _, bar := range bars[maxInt(0, start):] {
		accepted := bar.Close > level+tolerance
		if side == "support" {
			accepted = bar.Close < level-tolerance
		}
		if accepted {
			current++
		} else {
			current = 0
		}
		if current > longest {
			longest = current
		}
	}
	return longest
}

func barrierTags(side string, level float64, touches, wicks int, tolerance float64, barIndex int, sessions []SessionRef, lines []ScalpLine, regimeHigh, regimeLow float64, hasRegime bool, roundStep float64) []string {
	tags := []string{fmt.Sprintf("micro ×%d", touches), fmt.Sprintf("wick ×%d", wicks)}
	for _, session := range sessions {
		name := strings.ToUpper(session.Name)
		if scalpSessionNames[name] && math.Abs(session.Price-level) <= tolerance {
			tags = append(tags, "session "+name)
		}
	}
	if hasRegime {
		if side == "resistance" && math.Abs(regimeHigh-level) <= tolerance {
			tags = append(tags, "box-top")
		}
		if side == "support" && math.Abs(regimeLow-level) <= tolerance {
			tags = append(tags, "box-bottom")
		}
	}
	for _, line := range lines {
		if line.Broken || line.Kind != side {
			continue
		}
		if math.Abs(line.Value-level) <= tolerance {
			tags = append(tags, fmt.Sprintf("TL %s ×%d", side, line.Touches))
		}
	}
	if roundStep > 0 {
		nearest := math.RoundToEven(level/roundStep) * roundStep
		if math.Abs(nearest-level) <= tolerance {
			tags = append(tags, "round")
		}
	}
	return uniqueTags(tags)
}

func uniqueTags(tags []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, tag := range tags {
		key := strings.ToLower(tag)
		if tag != "" && !seen[key] {
			out = append(out, tag)
			seen[key] = true
		}
	}
	return out
}

func barrierScore(touches, wicks, acceptedCloses, confluences, lastTouch, barCount, bodyHolds int, fallback bool) float64 {
	score := math.Min(5, float64(touches)) * 1.2
	score += math.Min(4, float64(wicks))
	score += math.Min(3, float64(bodyHolds)) * 0.5
	score += math.Min(3, float64(maxInt(0, confluences))) * 0.75
	if lastTouch >= barCount-3 {
		score += 1.0
	}
	score -= float64(maxInt(0, acceptedCloses)) * 2.0
	if fallback {
		score *= 0.65
	}
	return math.Max(0, pythonRound(score, 3))
}

// pythonRound mirrors Python's round(value, digits): the exact binary value is
// rounded to the nearest decimal, so a score such as 6.9875 (stored just below
// the tie) rounds down.
func pythonRound(value float64, digits int) float64 {
	rounded, err := strconv.ParseFloat(strconv.FormatFloat(value, 'f', digits, 64), 64)
	if err != nil {
		return value
	}
	return rounded
}

func barrierGrade(score float64, touches, wicks int, tags []string) string {
	structural := false
	for _, tag := range tags {
		if strings.HasPrefix(tag, "session ") || strings.HasPrefix(tag, "TL ") || tag == "box-top" || tag == "box-bottom" {
			structural = true
			break
		}
	}
	switch {
	case score >= 8.0 && touches >= 3 && (wicks >= 2 || structural):
		return "A"
	case score >= 4.0 && touches >= 2:
		return "B"
	case score > 0:
		return "C"
	default:
		return "invalid"
	}
}

func barrierClass(grade string, tags []string) string {
	for _, tag := range tags {
		if strings.HasPrefix(tag, "session ") {
			return "major"
		}
	}
	switch grade {
	case "A":
		return "structural"
	case "B":
		return "local"
	default:
		return "micro"
	}
}

func dedupBarriers(barriers []ScalpBarrier, tolerance float64) []ScalpBarrier {
	sorted := append([]ScalpBarrier(nil), barriers...)
	sort.SliceStable(sorted, func(i, j int) bool {
		a, b := sorted[i], sorted[j]
		if a.Side != b.Side {
			return a.Side < b.Side
		}
		if a.Level != b.Level {
			return a.Level < b.Level
		}
		return -a.Score < -b.Score
	})
	var result []ScalpBarrier
	for _, barrier := range sorted {
		if n := len(result); n > 0 && result[n-1].Side == barrier.Side && math.Abs(result[n-1].Level-barrier.Level) <= tolerance {
			if barrierRankGreater(barrier, result[n-1]) {
				result[n-1] = barrier
			}
			continue
		}
		result = append(result, barrier)
	}
	return result
}

func barrierRankGreater(a, b ScalpBarrier) bool {
	ra, rb := 1, 1
	if a.Fallback {
		ra = 0
	}
	if b.Fallback {
		rb = 0
	}
	if ra != rb {
		return ra > rb
	}
	if a.Score != b.Score {
		return a.Score > b.Score
	}
	if a.Touches != b.Touches {
		return a.Touches > b.Touches
	}
	if a.WickRejections != b.WickRejections {
		return a.WickRejections > b.WickRejections
	}
	if a.LastTouchIndex != b.LastTouchIndex {
		return a.LastTouchIndex > b.LastTouchIndex
	}
	return -a.Level > -b.Level
}

func fallbackBarrier(side string, frame []market.Candle, offset int, bars []market.Candle, atrValue, entryTolerance, maxEdgeWidth, price float64, sessions []SessionRef, opposite []ScalpBarrier, cfg ScalpConfig) (ScalpBarrier, bool) {
	if len(frame) == 0 || len(opposite) == 0 {
		return ScalpBarrier{}, false
	}
	minimumConfirmations := maxInt(1, cfg.FallbackMinConfirmations)
	opp := opposite[0]
	relevantStart := maxInt(0, minInt(len(frame), opp.FirstTouchIndex-offset))
	relevant := frame
	if relevantStart < len(frame) {
		relevant = frame[relevantStart:]
	}
	var level float64
	var extremeIndex int
	if side == "support" {
		argmin := 0
		for i, bar := range relevant {
			if bar.Low < relevant[argmin].Low {
				argmin = i
			}
		}
		extremeIndex, level = argmin+relevantStart, relevant[argmin].Low
		if level >= price {
			found := false
			for i, bar := range relevant {
				if bar.Low < price-scalpEpsilon && (!found || bar.Low < level) {
					level, found = bar.Low, true
					extremeIndex = i + relevantStart
				}
			}
			if !found {
				return ScalpBarrier{}, false
			}
			// get_loc of the first bar carrying that low, in frame coordinates.
			for i := relevantStart; i < len(frame); i++ {
				if frame[i].Low == level {
					extremeIndex = i
					break
				}
			}
		}
	} else {
		argmax := 0
		for i, bar := range relevant {
			if bar.High > relevant[argmax].High {
				argmax = i
			}
		}
		extremeIndex, level = argmax+relevantStart, relevant[argmax].High
		if level <= price {
			found := false
			for i, bar := range relevant {
				if bar.High > price+scalpEpsilon && (!found || bar.High > level) {
					level, found = bar.High, true
					extremeIndex = i + relevantStart
				}
			}
			if !found {
				return ScalpBarrier{}, false
			}
			for i := relevantStart; i < len(frame); i++ {
				if frame[i].High == level {
					extremeIndex = i
					break
				}
			}
		}
	}
	width := math.Abs(opp.Level - level)
	if width < atrValue*cfg.FallbackMinWidthATR || width > atrValue*cfg.FallbackMaxWidthATR {
		return ScalpBarrier{}, false
	}
	absIndex := extremeIndex + offset
	tolerance := entryTolerance
	touches, wicks := 0, 0
	for _, bar := range frame {
		span := bar.High - bar.Low
		if span <= scalpEpsilon {
			continue
		}
		var touched, rejected bool
		if side == "support" {
			touched = bar.Low <= level+tolerance && bar.High >= level-tolerance
			rejected = touched && (math.Min(bar.Open, bar.Close)-bar.Low)/span >= cfg.FallbackWickFraction && bar.Close > level
		} else {
			touched = bar.High >= level-tolerance && bar.Low <= level+tolerance
			rejected = touched && (bar.High-math.Max(bar.Open, bar.Close))/span >= cfg.FallbackWickFraction && bar.Close < level
		}
		if touched {
			touches++
			if rejected {
				wicks++
			}
		}
	}
	sessionOverlap := false
	for _, session := range sessions {
		if scalpSessionNames[strings.ToUpper(session.Name)] && math.Abs(session.Price-level) <= tolerance {
			sessionOverlap = true
			break
		}
	}
	confirmations := 0
	if touches >= 2 {
		confirmations++
	}
	if wicks >= 1 {
		confirmations++
	}
	if sessionOverlap {
		confirmations++
	}
	inside := 0
	lo, hi := math.Min(level, opp.Level), math.Max(level, opp.Level)
	for _, bar := range frame {
		if lo-scalpEpsilon <= bar.Close && bar.Close <= hi+scalpEpsilon {
			inside++
		}
	}
	if inside >= maxInt(3, cfg.MinimumInsideCloses) {
		confirmations++
	}
	if confirmations < minimumConfirmations {
		return ScalpBarrier{}, false
	}
	halfWidth := math.Min(entryTolerance, maxEdgeWidth/2)
	tags := []string{"fallback_local_extreme", fmt.Sprintf("micro ×%d", maxInt(1, touches)), fmt.Sprintf("wick ×%d", wicks)}
	if sessionOverlap {
		tags = append(tags, "session overlap")
	}
	confluences := 0
	if sessionOverlap {
		confluences = 1
	}
	score := barrierScore(maxInt(1, touches), wicks, 0, confluences, absIndex, len(bars), 0, true)
	grade := "C"
	if confirmations >= 2 && score >= 3.0 {
		grade = "B"
	}
	return ScalpBarrier{
		Side: side, Level: level, Low: level - halfWidth, High: level + halfWidth, Touches: maxInt(1, touches),
		WickRejections: wicks, LastTouchIndex: absIndex, FirstTouchIndex: absIndex, Age: maxInt(0, len(bars)-1-absIndex),
		Tags: tags, Score: score, Grade: grade, ConfidenceGrade: grade, Sources: []string{"fallback_local_extreme"},
		ClassName: "micro", Fallback: true, Tested: touches >= 2,
	}, true
}

func executableEdge(barrier ScalpBarrier) bool {
	grade := strings.ToUpper(firstNonEmpty(barrier.ConfidenceGrade, barrier.Grade, "C"))
	if barrier.Invalidated || grade == "INVALID" {
		return false
	}
	if barrier.Fallback {
		session := false
		for _, tag := range barrier.Tags {
			if strings.HasPrefix(tag, "session") {
				session = true
			}
		}
		return (grade == "A" || grade == "B") && (barrier.Touches >= 2 || barrier.WickRejections >= 1 || session)
	}
	return grade == "A" || grade == "B"
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

func insideCloses(bars []market.Candle, lower, upper float64, lookback int) int {
	count := 0
	for _, bar := range bars[maxInt(0, len(bars)-lookback):] {
		if lower-scalpEpsilon <= bar.Close && bar.Close <= upper+scalpEpsilon {
			count++
		}
	}
	return count
}

func isPostImpulse(bars []market.Candle, atrValue, lower, upper float64, cfg ScalpConfig) bool {
	if len(bars) < 8 || atrValue <= 0 {
		return false
	}
	window := bars[maxInt(0, len(bars)-minInt(cfg.PostImpulseLookbackBars, len(bars))):]
	maxClose, minClose := window[0].Close, window[0].Close
	for _, bar := range window {
		maxClose, minClose = math.Max(maxClose, bar.Close), math.Min(minClose, bar.Close)
	}
	if (maxClose-minClose)/atrValue < cfg.PostImpulseMinDisplaceATR {
		return false
	}
	recent := bars[len(bars)-minInt(cfg.PostImpulseRecentBars, len(bars)):]
	high, low := recent[0].High, recent[0].Low
	inside := 0
	for _, bar := range recent {
		high, low = math.Max(high, bar.High), math.Min(low, bar.Low)
		if lower-scalpEpsilon <= bar.Close && bar.Close <= upper+scalpEpsilon {
			inside++
		}
	}
	if (high-low)/atrValue > cfg.PostImpulseMaxContractATR {
		return false
	}
	return inside >= cfg.PostImpulseMinInside
}

func bestRangeWithState(barriers []ScalpBarrier, price, atr float64, cfg ScalpConfig, bars []market.Candle) (*ScalpRange, string) {
	if atr <= 0 {
		return nil, RangeStateNoRange
	}
	minimumRoom := math.Max(0, cfg.MinimumRoomATR)
	minimumWidth := math.Max(math.Max(0, cfg.MinimumWidthATR), 2*minimumRoom)
	maximumWidth := math.Max(minimumWidth, cfg.MaximumWidthATR)
	minInside := maxInt(1, cfg.MinimumInsideCloses)
	var supports, resistances []ScalpBarrier
	for _, barrier := range barriers {
		if barrier.Side == "support" && !barrier.Invalidated && barrier.Low <= price+scalpEpsilon {
			supports = append(supports, barrier)
		}
		if barrier.Side == "resistance" && !barrier.Invalidated && barrier.High >= price-scalpEpsilon {
			resistances = append(resistances, barrier)
		}
	}
	var candidates []ScalpRange
	for _, lower := range supports {
		for _, upper := range resistances {
			width := upper.Level - lower.Level
			if width <= 0 {
				continue
			}
			widthATR := width / atr
			if !(minimumWidth <= widthATR && widthATR <= maximumWidth) {
				continue
			}
			if price < lower.Low-scalpEpsilon || price > upper.High+scalpEpsilon {
				continue
			}
			eq := (lower.Level + upper.Level) / 2
			room := math.Min(eq-lower.Level, upper.Level-eq) / atr
			if room < minimumRoom {
				continue
			}
			inside := insideCloses(bars, lower.Level, upper.Level, cfg.InsideLookbackBars)
			postImpulse := cfg.PostImpulseEnabled && isPostImpulse(bars, atr, lower.Level, upper.Level, cfg)
			lowerOK, upperOK := executableEdge(lower), executableEdge(upper)
			bothStrong := lowerOK && upperOK && !lower.Fallback && !upper.Fallback
			oneFallback := lower.Fallback != upper.Fallback && (lowerOK || upperOK)
			rejectionOK := lower.WickRejections >= 1 || upper.WickRejections >= 1 || lower.BodyHolds >= 1 || upper.BodyHolds >= 1
			abGrade := func(b ScalpBarrier) bool { return b.ConfidenceGrade == "A" || b.ConfidenceGrade == "B" }
			var state string
			switch {
			case bothStrong && inside >= minInside && rejectionOK:
				state = RangeStateConfirmed
				if postImpulse {
					state = RangeStatePostImpulse
				}
			case cfg.ProvisionalEnabled && oneFallback && inside >= minInside && rejectionOK:
				state = RangeStateProvisional
			case cfg.ProvisionalEnabled && (lowerOK || upperOK) && inside >= minInside && rejectionOK &&
				(lower.Fallback && abGrade(upper) || upper.Fallback && abGrade(lower) || abGrade(lower) && abGrade(upper)):
				state = RangeStateProvisional
				if postImpulse {
					state = RangeStatePostImpulse
				}
			default:
				continue
			}
			breakCloses := maxInt(1, cfg.BreakCloses)
			if lower.AcceptedCloses >= breakCloses || upper.AcceptedCloses >= breakCloses {
				continue
			}
			quality := lower.Score + upper.Score
			if state == RangeStateProvisional {
				quality *= 0.75
			}
			if state == RangeStatePostImpulse {
				quality *= 0.85
			}
			candidates = append(candidates, ScalpRange{
				Lower: lower, Upper: upper, Equilibrium: eq, WidthATR: widthATR, Quality: quality, State: state,
				InsideCloses: inside, OneSided: lower.Fallback || upper.Fallback, PostImpulse: state == RangeStatePostImpulse,
			})
		}
	}
	if len(candidates) == 0 {
		return nil, RangeStateNoRange
	}
	rank := func(state string) int {
		switch state {
		case RangeStateConfirmed:
			return 0
		case RangeStatePostImpulse:
			return 1
		default:
			return 2
		}
	}
	best := candidates[0]
	for _, candidate := range candidates[1:] {
		if rangeBefore(candidate, best, price, rank) {
			best = candidate
		}
	}
	return &best, best.State
}

func rangeBefore(a, b ScalpRange, price float64, rank func(string) int) bool {
	if rank(a.State) != rank(b.State) {
		return rank(a.State) < rank(b.State)
	}
	if a.Quality != b.Quality {
		return a.Quality > b.Quality
	}
	da, db := math.Abs(price-a.Equilibrium), math.Abs(price-b.Equilibrium)
	if da != db {
		return da < db
	}
	if a.WidthATR != b.WidthATR {
		return a.WidthATR < b.WidthATR
	}
	if a.Lower.Level != b.Lower.Level {
		return a.Lower.Level < b.Lower.Level
	}
	return a.Upper.Level < b.Upper.Level
}
