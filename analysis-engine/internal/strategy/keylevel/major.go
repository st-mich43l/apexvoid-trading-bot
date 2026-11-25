package keylevel

// Key Level v13, level_model: major (PR-KL13, owner rule 25 Nov 2026: Key Level trades only strong or major levels, never minor ones).
//
// The major-level book is owned by this strategy and built from data the frames already hold (LegacyFrame.Swings, .Bars, .Zones and .Sessions of each frame in
// structure_timeframes). Nothing another strategy reads is changed: techniquezone.KeyLevels and LegacyFrame.SwingLevels are untouched.
//
// Sources: (1) per frame, clusters of that frame's CONFIRMED swings, every member within band_atr x the frame's ATR of every other, priced at the cluster's median;
// (2) the listed, finalized session levels (AvailableAt <= now); (3) the $50/$100 handles. Facts are measured on the level's own frame bars (H1 for session and round
// levels): reactions (entries into the band followed, within reaction_window_bars, by a departure of departure_ref_atr reference-frame ATRs on the side price came
// from, before any close beyond the far edge), their span and largest departure, an accepted flip with held reactions on the new side, whether the level is intact
// (no accepted break since its last reaction), the session names and round class inside the band, and an overlapping unmitigated M15/H1 zone. A level is MAJOR when it
// is intact and meets S (session), R (round handle with a reaction), W (swing with enough reactions, span and departure) or F (held flip). Everything else is minor.
//
// The book is causal: a swing counts once confirmed, a session level from its AvailableAt, a reaction only when its departure bar has closed, and the moment a level
// became major (knownAt) is the close of the bar that completed its first satisfied rule. The book only holds levels within maxBookDistanceRefATR reference ATRs of
// price: a level further away cannot be touched by an M5 reaction now.

import (
	"fmt"
	"math"
	"sort"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const (
	levelModelSwing = "swing"
	levelModelMajor = "major"

	// RefusedMinorLevel: under level_model major, a level that is not major and on which a candidate would otherwise have been published.
	RefusedMinorLevel = "minor_level"

	// maxBookDistanceRefATR bounds the book to the levels near price (also the round-handle window of the specification).
	maxBookDistanceRefATR = 3.0
)

// Level sources, in merge priority order (highest first).
const (
	sourceSession  = "session"
	sourceRound    = "round"
	sourceSwingH1  = "swing_h1"
	sourceSwingM15 = "swing_m15"
	sourceSwingM5  = "swing_m5"
)

var sourcePriority = map[string]int{sourceSession: 5, sourceRound: 4, sourceSwingH1: 3, sourceSwingM15: 2, sourceSwingM5: 1}

// MajorConfig is the major block of the key_level configuration. Every value is a stated hypothesis (PR-KL13), not tuned to a result.
type MajorConfig struct {
	ReferenceTimeframe         market.Timeframe
	BandATR                    float64
	DepartureRefATR            float64
	ReactionWindowBars         int
	SwingMinReactions          int
	SwingMinSpanHours          float64
	SwingMinMaxDepartureRefATR float64
	SessionLevels              []string
	MajorRoundSteps            []float64
	MinorRoundStep             float64
	RoundMinReactions          int
	FlipMinReactions           int
}

func parseMajorConfig(raw any) (MajorConfig, error) {
	params, ok := raw.(map[string]any)
	if !ok {
		return MajorConfig{}, fmt.Errorf("major must be a mapping, got %T", raw)
	}
	var c MajorConfig
	var err error
	tf, ok := params["reference_timeframe"].(string)
	if !ok {
		return MajorConfig{}, fmt.Errorf("major.reference_timeframe must be a timeframe name")
	}
	c.ReferenceTimeframe = market.Timeframe(strings.ToUpper(tf))
	if c.ReferenceTimeframe != market.M5 && c.ReferenceTimeframe != market.M15 && c.ReferenceTimeframe != market.H1 {
		return MajorConfig{}, fmt.Errorf("major.reference_timeframe %q must be M5, M15 or H1", tf)
	}
	for key, dst := range map[string]*float64{
		"band_atr": &c.BandATR, "departure_ref_atr": &c.DepartureRefATR, "swing_min_span_hours": &c.SwingMinSpanHours,
		"swing_min_max_departure_ref_atr": &c.SwingMinMaxDepartureRefATR, "minor_round_step": &c.MinorRoundStep,
	} {
		if *dst, err = strategyutil.Float(params, key); err != nil {
			return MajorConfig{}, fmt.Errorf("major: %w", err)
		}
	}
	for key, dst := range map[string]*int{
		"reaction_window_bars": &c.ReactionWindowBars, "swing_min_reactions": &c.SwingMinReactions, "round_min_reactions": &c.RoundMinReactions,
		"flip_min_reactions": &c.FlipMinReactions,
	} {
		if *dst, err = strategyutil.Int(params, key); err != nil {
			return MajorConfig{}, fmt.Errorf("major: %w", err)
		}
	}
	names, ok := params["session_levels"].([]any)
	if !ok || len(names) == 0 {
		return MajorConfig{}, fmt.Errorf("major.session_levels must be a non-empty list")
	}
	for _, n := range names {
		name, isString := n.(string)
		if !isString || sessionPoints(name) == 0 {
			return MajorConfig{}, fmt.Errorf("major.session_levels: %v is not one of PDH, PDL, PWH, PWL, ASIA_H, ASIA_L, LONDON_H, LONDON_L", n)
		}
		c.SessionLevels = append(c.SessionLevels, name)
	}
	steps, ok := params["major_round_steps"].([]any)
	if !ok || len(steps) == 0 {
		return MajorConfig{}, fmt.Errorf("major.major_round_steps must be a non-empty list")
	}
	for _, v := range steps {
		f, isNumber := toFloat(v)
		if !isNumber || !(f > 0) {
			return MajorConfig{}, fmt.Errorf("major.major_round_steps: %v must be a positive number", v)
		}
		c.MajorRoundSteps = append(c.MajorRoundSteps, f)
	}
	sort.Float64s(c.MajorRoundSteps)
	switch {
	case !(c.BandATR > 0), !(c.DepartureRefATR > 0), !(c.MinorRoundStep > 0):
		return MajorConfig{}, fmt.Errorf("major: band_atr, departure_ref_atr and minor_round_step must be > 0")
	case c.ReactionWindowBars < 1, c.SwingMinReactions < 1, c.RoundMinReactions < 0, c.FlipMinReactions < 1:
		return MajorConfig{}, fmt.Errorf("major: reaction_window_bars, swing_min_reactions and flip_min_reactions must be >= 1, round_min_reactions >= 0")
	case c.SwingMinSpanHours < 0, c.SwingMinMaxDepartureRefATR < 0:
		return MajorConfig{}, fmt.Errorf("major: swing_min_span_hours and swing_min_max_departure_ref_atr must be >= 0")
	}
	return c, nil
}

func toFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	}
	return 0, false
}

// sessionPoints is a session level's strength points; 0 for a name the book does not know.
func sessionPoints(name string) int {
	switch name {
	case "PWH", "PWL":
		return 4
	case "PDH", "PDL":
		return 3
	case "ASIA_H", "ASIA_L", "LONDON_H", "LONDON_L":
		return 2
	}
	return 0
}

func sessionIsHigh(name string) bool {
	return strings.HasSuffix(name, "_H") || name == "PDH" || name == "PWH"
}

// levelReaction is one counted reaction of price at a level: the entry bar's open time, the close of the bar that reached the departure (when the reaction is known), the
// side price came from (+1 above, -1 below) and the largest departure within the window, in reference ATRs.
type levelReaction struct {
	at, doneAt int64
	side       int
	departure  float64
}

// levelFacts are the measured facts of one level (PR-KL13 §2.2).
type levelFacts struct {
	reactions    []levelReaction
	maxDeparture float64
	spanHours    float64
	flipped      bool
	flipKnownAt  int64
	intact       bool
	sessions     []string
	roundClass   int // 100, 50, 10 or 0
	htfZone      bool
}

// majorLevel is one level of the book.
type majorLevel struct {
	price, band float64
	source      string
	tf          market.Timeframe // the frame its facts and role are read on, and its structure timeframe
	facts       levelFacts
	rules       []string // S, R, W, F (empty: minor)
	knownAt     int64    // when it became major (0 when minor)
	score       int
	roleLows    int
	roleHighs   int
}

func (l majorLevel) major() bool { return len(l.rules) > 0 }

// bookFrames are the frames the book reads, by timeframe.
type bookFrames map[market.Timeframe]*strategyutil.LegacyDetector

func timeframeSeconds(tf market.Timeframe) int64 {
	minutes, _ := tf.Minutes()
	return int64(minutes) * 60
}

// buildBook builds the major-level book as of now (the close of the latest M5 bar), every level merged across sources and frames and classified.
func (s *Strategy) buildBook(frames bookFrames, price float64, nowTime int64) []majorLevel {
	mc := s.cfg.Major
	ref, ok := frames[mc.ReferenceTimeframe]
	if !ok || !(ref.ATR > 0) {
		return nil
	}
	refATR := ref.ATR
	window := maxBookDistanceRefATR * refATR
	var raw []majorLevel
	// (1) Swing levels per frame.
	for _, tf := range s.cfg.StructureTimeframes {
		d, available := frames[tf]
		if !available || !(d.ATR > 0) {
			continue
		}
		band := mc.BandATR * d.ATR
		for _, price0 := range swingClusters(d.Frame, band) {
			if math.Abs(price0-price) > window+band {
				continue
			}
			raw = append(raw, majorLevel{price: price0, band: band, source: "swing_" + strings.ToLower(string(tf)), tf: tf})
		}
	}
	// Session and round levels are read on the reference frame.
	refBand := mc.BandATR * refATR
	listed := map[string]bool{}
	for _, name := range mc.SessionLevels {
		listed[name] = true
	}
	// (2) Session levels, finalized only. Several frames carry the same references; the reference frame's are read.
	for _, ses := range ref.Frame.Sessions {
		if !listed[ses.Name] || ses.AvailableAt <= 0 || ses.AvailableAt > nowTime || math.Abs(ses.Price-price) > window+refBand {
			continue
		}
		l := majorLevel{price: ses.Price, band: refBand, source: sourceSession, tf: mc.ReferenceTimeframe}
		l.facts.sessions = []string{ses.Name}
		if sessionIsHigh(ses.Name) {
			l.roleHighs = 1
		} else {
			l.roleLows = 1
		}
		raw = append(raw, l)
	}
	// (3) Round handles: every multiple of each major step within the window.
	seen := map[int64]bool{}
	for _, step := range mc.MajorRoundSteps {
		for k := math.Ceil((price - window) / step); k*step <= price+window; k++ {
			handle := k * step
			key := int64(math.Round(handle * 100))
			if seen[key] {
				continue
			}
			seen[key] = true
			raw = append(raw, majorLevel{price: handle, band: refBand, source: sourceRound, tf: mc.ReferenceTimeframe})
		}
	}
	// Facts and classification of every source level on its own frame.
	sessionsByName := ref.Frame.Sessions
	for i := range raw {
		l := &raw[i]
		d := frames[l.tf]
		if d == nil {
			continue
		}
		s.measure(l, d, refATR, nowTime, sessionsByName, listed, frames)
	}
	merged := mergeLevels(raw)
	for i := range merged {
		merged[i].score = strengthScore(merged[i])
	}
	sort.SliceStable(merged, func(i, j int) bool { return math.Abs(merged[i].price-price) < math.Abs(merged[j].price-price) })
	return merged
}

// swingClusters clusters a frame's CONFIRMED swings by price: ascending, a swing joins the open cluster when it is within band of every member (so the cluster's
// span stays within the band, inside the 2-band limit). The cluster's price is the median of its members, computed once.
func swingClusters(frame *context.LegacyFrame, band float64) []float64 {
	last := len(frame.Bars) - 1
	var prices []float64
	for _, sw := range frame.Swings {
		if sw.ConfirmedIndex <= last {
			prices = append(prices, sw.Price)
		}
	}
	sort.Float64s(prices)
	var out []float64
	var cluster []float64
	flush := func() {
		if len(cluster) == 0 {
			return
		}
		n := len(cluster)
		median := cluster[n/2]
		if n%2 == 0 {
			median = (cluster[n/2-1] + cluster[n/2]) / 2
		}
		out = append(out, median)
		cluster = nil
	}
	for _, p := range prices {
		if len(cluster) > 0 && p-cluster[0] > band {
			flush()
		}
		cluster = append(cluster, p)
	}
	flush()
	return out
}

// measure fills a source level's facts on its own frame d and classifies it.
func (s *Strategy) measure(l *majorLevel, d *strategyutil.LegacyDetector, refATR float64, nowTime int64, sessions []techniquezone.SessionRef, listed map[string]bool, frames bookFrames) {
	mc := s.cfg.Major
	bars := d.Frame.Bars
	period := timeframeSeconds(l.tf)
	lo, hi := l.price-l.band, l.price+l.band
	departure := mc.DepartureRefATR * refATR
	knownSince := int64(0)
	for _, ses := range sessions {
		if listed[ses.Name] && ses.AvailableAt > 0 && ses.AvailableAt <= nowTime && ses.Price >= lo && ses.Price <= hi {
			if !containsString(l.facts.sessions, ses.Name) {
				l.facts.sessions = append(l.facts.sessions, ses.Name)
			}
			if knownSince == 0 || ses.AvailableAt < knownSince {
				knownSince = ses.AvailableAt
			}
		}
	}
	l.facts.reactions = reactionsAt(bars, lo, hi, departure, refATR, mc.ReactionWindowBars, period)
	breaks := acceptedBreaks(bars, lo, hi, s.cfg.BreakoutAcceptBars, period)
	f := &l.facts
	for _, r := range f.reactions {
		f.maxDeparture = math.Max(f.maxDeparture, r.departure)
	}
	if n := len(f.reactions); n > 0 {
		f.spanHours = float64(f.reactions[n-1].at-f.reactions[0].at) / 3600
	}
	// Flip: the latest accepted break, then held reactions on the new side.
	if n := len(breaks); n > 0 {
		last := breaks[n-1]
		held := 0
		for _, r := range f.reactions {
			if r.at >= last.at && r.side == last.side {
				held++
				if held == mc.FlipMinReactions {
					f.flipped, f.flipKnownAt = true, r.doneAt
				}
			}
		}
	}
	// Intact: no accepted break since the last counted reaction (since the level was knowable when it has none).
	since := knownSince
	if n := len(f.reactions); n > 0 {
		since = f.reactions[n-1].at
	}
	f.intact = true
	for _, b := range breaks {
		if b.at > since {
			f.intact = false
		}
	}
	f.roundClass = majorRoundClass(lo, hi, mc)
	f.htfZone = htfZoneOverlap(frames, lo, hi)

	l.rules, l.knownAt = classify(*f, knownSince, mc)
}

// classify is PR-KL13 §2.3: a level is MAJOR when it is intact and meets at least one of S (a listed session level), R (a $50/$100 handle with round_min_reactions
// reactions), W (swing_min_reactions reactions spanning swing_min_span_hours, one departure of swing_min_max_departure_ref_atr) or F (a held flip). It returns the
// rules met (none: minor) and the moment the first of them was met (knownSince is when a session level became knowable).
func classify(f levelFacts, knownSince int64, mc MajorConfig) ([]string, int64) {
	if !f.intact {
		return nil, 0
	}
	var rules []string
	knownAt, have := int64(0), false
	add := func(rule string, at int64) {
		rules = append(rules, rule)
		if !have || at < knownAt {
			knownAt, have = at, true
		}
	}
	if len(f.sessions) > 0 {
		add("S", knownSince)
	}
	if (f.roundClass == 100 || f.roundClass == 50) && len(f.reactions) >= mc.RoundMinReactions {
		at := int64(0)
		if mc.RoundMinReactions > 0 {
			at = f.reactions[mc.RoundMinReactions-1].doneAt
		}
		add("R", at)
	}
	if at, ok := swingRuleMetAt(f.reactions, mc); ok {
		add("W", at)
	}
	if f.flipped {
		add("F", f.flipKnownAt)
	}
	return rules, knownAt
}

// swingRuleMetAt is the close of the departure bar at which rule W was first met: swing_min_reactions reactions spanning swing_min_span_hours with a departure of
// swing_min_max_departure_ref_atr among them.
func swingRuleMetAt(rs []levelReaction, mc MajorConfig) (int64, bool) {
	maxDep := 0.0
	for i, r := range rs {
		maxDep = math.Max(maxDep, r.departure)
		if i+1 >= mc.SwingMinReactions && float64(r.at-rs[0].at)/3600 >= mc.SwingMinSpanHours && maxDep >= mc.SwingMinMaxDepartureRefATR {
			return r.doneAt, true
		}
	}
	return 0, false
}

// reactionsAt counts the distinct reaction episodes of price at the band [lo, hi] on closed bars. An episode starts on a bar that trades into the band while price
// came from one side (the last close outside the band) and the level is armed; it is a reaction when, within window bars after the entry bar, price moves at least
// departure beyond the edge on the side it came from before any close beyond the far edge. The level re-arms once a bar trades entirely outside the band and at
// least departure beyond an edge, so two episodes are distinct only when price left the band by the departure in between.
func reactionsAt(bars []market.Candle, lo, hi, departure, refATR float64, window int, period int64) []levelReaction {
	var out []levelReaction
	side, armed, start := 0, true, -1
	for i, b := range bars {
		if !armed && i > start && (b.Low > hi && b.High >= hi+departure || b.High < lo && b.Low <= lo-departure) {
			armed = true
		}
		if armed && side != 0 && b.Low <= hi && b.High >= lo {
			armed, start = false, i
			best, doneAt := 0.0, int64(0)
			for j := i + 1; j < len(bars) && j <= i+window; j++ {
				c := bars[j]
				var excursion float64
				if side > 0 {
					excursion = c.High - hi
				} else {
					excursion = lo - c.Low
				}
				if excursion > best {
					best = excursion
				}
				if doneAt == 0 && best >= departure {
					doneAt = c.Time + period
				}
				if side > 0 && c.Close < lo || side < 0 && c.Close > hi {
					break
				}
			}
			if doneAt > 0 {
				out = append(out, levelReaction{at: b.Time, doneAt: doneAt, side: side, departure: best / refATR})
			}
		}
		if b.Close > hi {
			side = 1
		} else if b.Close < lo {
			side = -1
		}
	}
	return out
}

// brk is an accepted break of a band: acceptBars consecutive closes beyond it on the side opposite to where price was accepted before (at first, the side of the first
// close outside the band); side is the new side and at the close of the accepting bar.
type brk struct {
	at   int64
	side int
}

func acceptedBreaks(bars []market.Candle, lo, hi float64, acceptBars int, period int64) []brk {
	var out []brk
	accepted, run, runSide := 0, 0, 0
	for _, b := range bars {
		closeSide := 0
		if b.Close > hi {
			closeSide = 1
		} else if b.Close < lo {
			closeSide = -1
		}
		if closeSide == 0 || closeSide != runSide {
			run, runSide = 0, closeSide
		}
		if closeSide == 0 {
			continue
		}
		run++
		if accepted == 0 {
			accepted = closeSide // where price was before anything was accepted: the first close outside the band
			continue
		}
		if run >= acceptBars && closeSide != accepted {
			out = append(out, brk{at: b.Time + period, side: closeSide})
			accepted = closeSide
		}
	}
	return out
}

// roundClass is the largest handle inside the band: 100, 50 (a major step), 10 (the minor step) or 0.
func majorRoundClass(lo, hi float64, mc MajorConfig) int {
	steps := append([]float64(nil), mc.MajorRoundSteps...)
	sort.Sort(sort.Reverse(sort.Float64Slice(steps)))
	for _, step := range steps {
		if handleInside(lo, hi, step) {
			return int(math.Round(step))
		}
	}
	if handleInside(lo, hi, mc.MinorRoundStep) {
		return int(math.Round(mc.MinorRoundStep))
	}
	return 0
}

func handleInside(lo, hi, step float64) bool {
	k := math.Ceil(lo/step - 1e-9)
	return k*step <= hi+1e-9
}

// htfZoneOverlap reports whether the band overlaps an unmitigated M15 or H1 supply or demand zone.
func htfZoneOverlap(frames bookFrames, lo, hi float64) bool {
	for _, tf := range []market.Timeframe{market.M15, market.H1} {
		d, ok := frames[tf]
		if !ok {
			continue
		}
		for _, z := range d.Frame.Zones {
			if z.Mitigated || (z.Side != "supply" && z.Side != "demand") {
				continue
			}
			if z.Low() <= hi && z.High() >= lo {
				return true
			}
		}
	}
	return false
}

// mergeLevels merges levels whose bands overlap into one level thesis: the union of their facts, the price, band and frame of the highest-priority member
// (session > round > swing H1 > swing M15 > swing M5, never an average), major when any member is.
func mergeLevels(raw []majorLevel) []majorLevel {
	n := len(raw)
	parent := make([]int, n)
	for i := range parent {
		parent[i] = i
	}
	var find func(int) int
	find = func(i int) int {
		for parent[i] != i {
			parent[i] = parent[parent[i]]
			i = parent[i]
		}
		return i
	}
	for i := 0; i < n; i++ {
		for j := i + 1; j < n; j++ {
			if math.Abs(raw[i].price-raw[j].price) <= raw[i].band+raw[j].band {
				parent[find(i)] = find(j)
			}
		}
	}
	groups := map[int][]int{}
	var order []int
	for i := 0; i < n; i++ {
		root := find(i)
		if _, ok := groups[root]; !ok {
			order = append(order, root)
		}
		groups[root] = append(groups[root], i)
	}
	out := make([]majorLevel, 0, len(order))
	for _, root := range order {
		members := groups[root]
		best := members[0]
		for _, i := range members[1:] {
			if sourcePriority[raw[i].source] > sourcePriority[raw[best].source] {
				best = i
			}
		}
		m := raw[best]
		m.facts.sessions = append([]string(nil), raw[best].facts.sessions...)
		m.rules = nil
		m.knownAt = 0
		knownSet := false
		for _, i := range members {
			r := raw[i]
			if len(r.facts.reactions) > len(m.facts.reactions) {
				m.facts.reactions, m.facts.spanHours = r.facts.reactions, r.facts.spanHours
			}
			m.facts.maxDeparture = math.Max(m.facts.maxDeparture, r.facts.maxDeparture)
			m.facts.flipped = m.facts.flipped || r.facts.flipped
			m.facts.htfZone = m.facts.htfZone || r.facts.htfZone
			if r.facts.roundClass > m.facts.roundClass {
				m.facts.roundClass = r.facts.roundClass
			}
			for _, name := range r.facts.sessions {
				if !containsString(m.facts.sessions, name) {
					m.facts.sessions = append(m.facts.sessions, name)
				}
			}
			for _, rule := range r.rules {
				if !containsString(m.rules, rule) {
					m.rules = append(m.rules, rule)
				}
			}
			if r.major() && (!knownSet || r.knownAt < m.knownAt) {
				m.knownAt, knownSet = r.knownAt, true
			}
			if i != best {
				m.roleLows += r.roleLows
				m.roleHighs += r.roleHighs
			}
		}
		sort.Slice(m.rules, func(a, b int) bool { return strings.Index("SRWF", m.rules[a]) < strings.Index("SRWF", m.rules[b]) })
		out = append(out, m)
	}
	return out
}

// strengthScore is the published strength of a level (PR-KL13 §2.3): it orders candidates, it never gates one.
func strengthScore(l majorLevel) int {
	score := 0
	best := 0
	for _, name := range l.facts.sessions {
		if p := sessionPoints(name); p > best {
			best = p
		}
	}
	score += best
	switch l.facts.roundClass {
	case 100:
		score += 3
	case 50:
		score += 2
	case 10:
		score++
	}
	score += minInt(len(l.facts.reactions), 4)
	if containsString(l.rules, "W") {
		score += 2
	}
	if containsString(l.rules, "F") {
		score += 2
	}
	if l.facts.htfZone {
		score++
	}
	if l.tf == market.H1 {
		score++
	}
	return score
}

func containsString(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

// evaluateMajor is Evaluate under level_model major: the levels walked are the book's, nearest first. A major level goes through every per-level step of v12
// (evaluateLevel); a minor level is evaluated without publishing, and one that would have published is counted minor_level.
func (s *Strategy) evaluateMajor(ctx *context.MarketContext, m5 *strategyutil.LegacyDetector) []opportunity.Candidate {
	frames := bookFrames{market.M5: m5}
	for _, tf := range append(append([]market.Timeframe(nil), s.cfg.StructureTimeframes...), s.cfg.Major.ReferenceTimeframe) {
		if _, have := frames[tf]; have {
			continue
		}
		if d, ok := strategyutil.NewLegacyDetectorForFrame(ctx, tf, s.cfg.Detector); ok {
			frames[tf] = d
		}
	}
	bars := m5.Frame.Bars
	if len(bars) == 0 {
		return nil
	}
	nowTime := bars[len(bars)-1].Time + timeframeSeconds(market.M5)
	book := s.buildBook(frames, m5.Price, nowTime)
	s.countBook(book)
	var asLevels []techniquezone.Level
	for _, l := range book {
		if l.major() {
			asLevels = append(asLevels, techniquezone.Level{Price: l.price, Band: l.band, Touches: 1})
		}
	}
	closesByFrame := map[market.Timeframe][]float64{}
	var found []published
	for _, l := range book {
		d := frames[l.tf]
		if d == nil {
			continue
		}
		closes, ok := closesByFrame[l.tf]
		if !ok {
			closes = make([]float64, len(d.Frame.Bars))
			for i, b := range d.Frame.Bars {
				closes[i] = b.Close
			}
			closesByFrame[l.tf] = closes
		}
		zones := d.Frame.Zones
		if s.cfg.Contract == contractProfitWeek {
			zones = d.Frame.ContractZones
		}
		knownFrom := 0
		if l.major() {
			knownFrom = sort.Search(len(bars), func(i int) bool { return bars[i].Time >= l.knownAt })
		}
		level := techniquezone.Level{Price: l.price, Kind: "major", Touches: len(l.facts.reactions), Band: l.band}
		opts := levelOpts{band: l.band, bandFloor: l.band, roleTouches: 1, roleLows: l.roleLows, roleHighs: l.roleHighs, knownFrom: knownFrom, dryRun: !l.major()}
		p, ok := s.evaluateLevel(ctx, m5, d, l.tf, level, zones, closes, levelCoverage(asLevels, 1, d.Frame.Bars), opts)
		if !l.major() {
			if ok {
				s.refuse(l.tf, RefusedMinorLevel)
			}
			continue
		}
		if !ok {
			continue
		}
		annotateMajor(&p.candidate, l)
		p.score = l.score
		found = append(found, p)
	}
	best := s.selectOne(found)
	if best == nil {
		return nil
	}
	return []opportunity.Candidate{best.candidate}
}

// annotateMajor publishes the level's facts: evidence codes for its rules, source and session names, and quality measures (never scored).
func annotateMajor(c *opportunity.Candidate, l majorLevel) {
	codes := []string{"kl_level_source_" + l.source}
	for _, rule := range l.rules {
		codes = append(codes, map[string]string{"S": "kl_major_session", "R": "kl_major_round", "W": "kl_major_swing", "F": "kl_major_flip"}[rule])
	}
	for _, name := range l.facts.sessions {
		codes = append(codes, "kl_session_"+strings.ToLower(name))
	}
	for _, code := range codes {
		c.Evidence = append(c.Evidence, opportunity.Evidence{Code: code})
	}
	if c.Quality.Components == nil {
		c.Quality.Components = map[string]float64{}
	}
	htf := 0.0
	if l.facts.htfZone {
		htf = 1
	}
	m := c.Quality.Components
	m["kl_strength_score"] = float64(l.score)
	m["kl_reactions"] = float64(len(l.facts.reactions))
	m["kl_span_hours"] = l.facts.spanHours
	m["kl_max_departure_ref_atr"] = l.facts.maxDeparture
	m["kl_round_class"] = float64(l.facts.roundClass)
	m["kl_htf_zone"] = htf
	for _, rule := range []string{"S", "R", "W", "F"} {
		v := 0.0
		if containsString(l.rules, rule) {
			v = 1
		}
		m["kl_major_rule_"+strings.ToLower(rule)] = v
	}
}

// countBook records the size of the book by source (kl_major_levels_in_book{source}), drained by the engine after each evaluation.
func (s *Strategy) countBook(book []majorLevel) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.features == nil {
		s.features = map[strategy.Feature]int64{}
	}
	for _, l := range book {
		if l.major() {
			s.features[strategy.Feature{Name: "kl_major_levels_in_book", Value: l.source, Timeframe: l.tf}]++
		}
	}
}

// TakeFeatures returns the features counted since the last call and resets them (strategy.FeatureReporter).
func (s *Strategy) TakeFeatures() map[strategy.Feature]int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := s.features
	s.features = nil
	return out
}

// BookLevel is the read-only view of one level of the major book, for study tools (cmd/klstudy). Production never reads it.
type BookLevel struct {
	Price, Band        float64
	Source             string
	Timeframe          market.Timeframe
	Rules              []string
	Major              bool
	Reactions          int
	SpanHours          float64
	MaxDepartureRefATR float64
	Sessions           []string
	RoundClass         int
	HTFZone            bool
	Score              int
	KnownAt            int64
}

// StudyBook builds the major-level book from frames a study tool built itself (legacyread.Stage/Complete on bars cut at a past time), with the key_level
// parameters of the configuration (their major block is used whatever level_model says). Frames that are missing are simply not read; the reference frame is
// required. Read-only: nothing in production calls it.
func StudyBook(params map[string]any, frames map[market.Timeframe]*strategyutil.LegacyDetector, price float64, now int64) ([]BookLevel, error) {
	cfg, err := parseConfig(params)
	if err != nil {
		return nil, err
	}
	if _, present := params["major"]; !present {
		return nil, fmt.Errorf("the key_level parameters carry no major block")
	}
	s := &Strategy{cfg: cfg}
	var out []BookLevel
	for _, l := range s.buildBook(bookFrames(frames), price, now) {
		out = append(out, BookLevel{
			Price: l.price, Band: l.band, Source: l.source, Timeframe: l.tf, Rules: l.rules, Major: l.major(), Reactions: len(l.facts.reactions),
			SpanHours: l.facts.spanHours, MaxDepartureRefATR: l.facts.maxDeparture, Sessions: l.facts.sessions, RoundClass: l.facts.roundClass,
			HTFZone: l.facts.htfZone, Score: l.score, KnownAt: l.knownAt,
		})
	}
	return out, nil
}
