// Package flipzone publishes the frozen flip_demand_zone_reaction /
// flip_supply_zone_reaction decisions: a confirmed reaction off a zone born of a
// role flip — a broken level accepted from the new side — and only while the
// level's own role still agrees with the trade.
//
// An ordinary zone touch is not a flip. The zone must carry the flip source, be
// unmitigated and entry-valid; the key level it is anchored to must classify, on
// the closed bars, as broken resistance (BUY) or broken support (SELL); and the
// zone must then show a confirmed structural reaction that clears the shared
// confluence floor. One candidate per side at most.
package flipzone

import (
	"fmt"
	"math"
	"sort"
	"strings"
	"sync"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "flip_zone"
const Version = "v6"

const (
	structureVersion = "v2"
	liquidityVersion = "v1"
	zoneVersionUsed  = "v1"
	configVersion    = 3
)

// Config is FlipZone's own parsed technical configuration.
type Config struct {
	InvalidationBufferATR    float64
	MinimumTargetDistanceATR float64
	ExpiryHours              float64
	// BreakoutAcceptBars is key_level_role.py's breakout_accept_bars.
	BreakoutAcceptBars int
	// Legacy qualifies the reaction through the frozen detector contract.
	Legacy strategyutil.LegacyDetectorSettings
	// StopBudget is the instrument's stop envelope (strategyutil.StopBudget); the zero value is no budget.
	StopBudget strategyutil.StopBudget
	// Barrier is the opposing-barrier policy the quality room is limited with.
	Barrier strategyutil.BarrierPolicy
	// StructureTimeframes are the frames a flip zone may be found on (v6, owner rule 23 Nov 2026: setups are found on M5, M15 and H1; M1 is execution only). The flip
	// level, the break and its acceptance, and the zone are read on the structure frame; the retest rejection, the stop and the entry are read on M5.
	StructureTimeframes []market.Timeframe
	// RequireConfirmation: a confirming M5 rejection is needed (the strategy's own require_confirmation, else the global analysis.require_candle_confirmation).
	RequireConfirmation bool
	// StructureCloseBufferATR is the margin, in ATRs of the structure's own frame, by which a close of that frame must pass the zone's far edge to withdraw the
	// opportunity (the engine's structure-close rule). It is read by the engine from the same key.
	StructureCloseBufferATR float64
}

// Refusals of Flip Zone (v6), by reason (strategy_refused{strategy="flip_zone", structure_timeframe, reason}).
const (
	// RefusedNoM5Reaction: a flip zone whose level broke and whose role agrees, with no confirmed M5 rejection (counted once per direction and evaluation).
	RefusedNoM5Reaction = "no_m5_reaction"
	// RefusedDuplicateThesis: the same flip level qualified on more than one frame; the higher timeframe (then more stars, then the nearer entry) published.
	RefusedDuplicateThesis = "duplicate_thesis"
)

// Strategy is FlipZoneStrategy.
type Strategy struct {
	strategyutil.RefusalCounter
	mu          sync.Mutex
	refusals    map[strategy.Refusal]int64
	cfg         Config
	fingerprint string
}

func (s *Strategy) refuse(tf market.Timeframe, reason string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.refusals == nil {
		s.refusals = map[strategy.Refusal]int64{}
	}
	s.refusals[strategy.Refusal{Reason: reason, Timeframe: tf}]++
}

// TakeRefusals returns the refusals counted since the last call and resets them (strategy.RefusalReporter).
func (s *Strategy) TakeRefusals() map[strategy.Refusal]int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := s.refusals
	s.refusals = nil
	return out
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("flipzone: New called with strategy ID %q, want %q", cfg.ID, ID)
	}
	var out Config
	var err error
	for key, dst := range map[string]*float64{
		"invalidation_buffer_atr":     &out.InvalidationBufferATR,
		"minimum_target_distance_atr": &out.MinimumTargetDistanceATR,
		"expiry_hours":                &out.ExpiryHours,
	} {
		if *dst, err = strategyutil.Float(cfg.Parameters, key); err != nil {
			return nil, fmt.Errorf("flipzone: %w", err)
		}
	}
	if out.BreakoutAcceptBars, err = strategyutil.Int(cfg.Parameters, "breakout_accept_bars"); err != nil {
		return nil, fmt.Errorf("flipzone: %w", err)
	}
	if out.Legacy, err = strategyutil.ParseLegacyDetectorSettings(cfg.Parameters); err != nil {
		return nil, fmt.Errorf("flipzone: %w", err)
	}
	if out.Barrier, err = strategyutil.ParseBarrierPolicy(cfg.Parameters); err != nil {
		return nil, fmt.Errorf("flipzone: %w", err)
	}
	if out.StopBudget, err = strategyutil.ParseStopBudget(cfg.Parameters); err != nil {
		return nil, fmt.Errorf("flipzone: %w", err)
	}
	if out.InvalidationBufferATR <= 0 || out.ExpiryHours <= 0 || out.BreakoutAcceptBars < 1 {
		return nil, fmt.Errorf("flipzone: invalidation_buffer_atr, expiry_hours and breakout_accept_bars must be positive")
	}
	if out.StructureTimeframes, err = strategyutil.ParseStructureTimeframes(cfg.Parameters, []market.Timeframe{market.M5, market.M15, market.H1}); err != nil {
		return nil, fmt.Errorf("flipzone: %w", err)
	}
	if out.RequireConfirmation, err = strategyutil.ParseRequireConfirmation(cfg.Parameters, strategyutil.RequireCandleConfirmation(cfg.Parameters)); err != nil {
		return nil, fmt.Errorf("flipzone: %w", err)
	}
	// The detector's own switch follows the strategy's: a bare touch publishes only when this strategy does not require a confirming candle.
	out.Legacy.CandleConfirmationOptional = !out.RequireConfirmation
	if out.StructureCloseBufferATR, err = strategyutil.Float(cfg.Parameters, "structure_close_buffer_atr"); err != nil {
		return nil, fmt.Errorf("flipzone: %w", err)
	}
	if out.StructureCloseBufferATR < 0 || math.IsNaN(out.StructureCloseBufferATR) || math.IsInf(out.StructureCloseBufferATR, 0) {
		return nil, fmt.Errorf("flipzone: structure_close_buffer_atr must be a finite number >= 0")
	}
	s := &Strategy{cfg: out, fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	s.cfg.StopBudget.Refusals = &s.RefusalCounter
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *context.MarketContext) []opportunity.Candidate {
	// The retest rejection, the stop and the entry are read on M5, whatever frame the flip zone lives on.
	m5, ok := strategyutil.NewLegacyDetectorForFrame(ctx, market.M5, s.cfg.Legacy)
	if !ok {
		return nil
	}
	var all []flipCandidate
	for _, tf := range s.cfg.StructureTimeframes {
		frame := m5
		if tf != market.M5 {
			var available bool
			if frame, available = strategyutil.NewLegacyDetectorForFrame(ctx, tf, s.cfg.Legacy); !available {
				continue
			}
		}
		for _, direction := range []market.Direction{market.Buy, market.Sell} {
			flip := s.decide(m5.WithDirection(direction), frame.WithDirection(direction), tf)
			if flip == nil {
				continue
			}
			candidate, ok := strategyutil.TechniqueCandidate(ctx, flip.decision, s.spec())
			if !ok {
				continue
			}
			candidate.Evidence = append([]opportunity.Evidence{{Code: "structure_tf_" + strings.ToLower(string(tf))}}, candidate.Evidence...)
			candidate.Structure = flip.structure
			candidate.StructureTimeframe = tf
			price := m5.Price
			entryReference := candidate.Entry.High
			if direction == market.Sell {
				entryReference = candidate.Entry.Low
			}
			all = append(all, flipCandidate{
				candidate: candidate, tf: tf, direction: direction, stars: flip.decision.Result.Stars, levelPrice: flip.levelPrice, levelBand: flip.levelBand,
				distance: math.Abs(flip.levelPrice - price), entryGap: math.Abs(entryReference - price),
			})
		}
	}
	return s.selectPerSide(all)
}

func (s *Strategy) spec() strategyutil.TechniqueSpec {
	return strategyutil.TechniqueSpec{
		ID: string(ID), Version: Version, AnchorToReaction: true, StopBudget: s.cfg.StopBudget, Barrier: s.cfg.Barrier, ZoneEvidence: "m5_flip_zone_role_confirmed", ConfirmedEvidence: "m5_flip_zone_rejection_confirmed",
		InvalidationLabel: "flip_zone_invalidated", InvalidationBufferATR: s.cfg.InvalidationBufferATR,
		MinimumTargetDistanceATR: s.cfg.MinimumTargetDistanceATR, ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
		Versions: opportunity.AnalysisProvenance{
			StructureVersion: structureVersion, LiquidityVersion: liquidityVersion, ZoneVersion: zoneVersionUsed,
			ConfigVersion: configVersion, ConfigFingerprint: s.fingerprint,
		},
	}
}

// flipCandidate is one published flip zone with what the duplicate-thesis order and the selection read.
type flipCandidate struct {
	candidate  opportunity.Candidate
	tf         market.Timeframe
	direction  market.Direction
	stars      int
	levelPrice float64
	levelBand  float64
	distance   float64 // |flip level - the latest M5 close|
	entryGap   float64 // |entry reference - the latest M5 close|
}

// flipDecision is a decision with the structure it rests on and the flip level it was anchored to.
type flipDecision struct {
	decision   *strategyutil.TechniqueDecision
	structure  *opportunity.StructureZone
	levelPrice float64
	levelBand  float64
}

// decide mirrors _sd_zone_reaction with require_source="flip_zone" and the key level role enforced, on the structure frame fd (tf): the flip zone, the level it is
// anchored to, the break and its acceptance (the role) are read on fd; the retest rejection, and so the stop and the entry, are read on m5.
func (s *Strategy) decide(m5, fd *strategyutil.LegacyDetector, tf market.Timeframe) *flipDecision {
	var zones []techniquezone.Zone
	for _, zone := range fd.CandidateZones() {
		if !zone.Mitigated && hasSource(zone, "flip_zone") {
			zones = append(zones, zone)
		}
	}
	// Whether the zone can be entered from here is judged where the order is placed (M5 price and ATR), as Finish does.
	zone, _, ok := m5.BestValidZone(zones)
	if !ok {
		return nil
	}
	level, found := flipLevel(fd.Frame.Levels, zone)
	if !found {
		return nil
	}
	closes := make([]float64, len(fd.Frame.Bars))
	for i, bar := range fd.Frame.Bars {
		closes[i] = bar.Close
	}
	// The level's band is its own on its frame, never thinner than proximal_band_atr of THAT frame's ATR.
	band := math.Max(level.Band, math.Max(1e-9, fd.Settings.ProximalBandATR*math.Max(0, fd.ATR)))
	role := keylevel.Role(level.Kind, market.Price(level.Price-band), market.Price(level.Price+band), closes, s.cfg.BreakoutAcceptBars)
	if m5.Direction == market.Buy && role != keylevel.RoleBrokenResistance || m5.Direction == market.Sell && role != keylevel.RoleBrokenSupport {
		return nil
	}
	// The zone is a flip only once price has broken through the old structure: the break bar built it and is no reaction to it. On a higher frame that bar's CLOSE
	// is the earliest moment an M5 bar may react (on M5 itself, the bar after it, as before v6).
	formation := maxInt(zone.OriginIndex, zone.BreakIndex)
	knownFrom := reactionKnownFrom(m5.Frame.Bars, fd.Frame, tf, formation)
	conf := m5.ReactionFrom(zone.Low(), zone.High(), m5.ZoneGrab(zone), knownFrom)
	if conf == nil {
		s.refuse(tf, RefusedNoM5Reaction)
		return nil
	}
	factors := strategyutil.FactorsForConfirmation(strategyutil.ReactionFactors(conf.Type, m5.HTFAligned(), zone.Touches, m5.SessionSweepContext(conf)), conf.Type)
	low, high := zone.Low(), zone.High()
	result := m5.Finish(m5.ZoneKey(zone), zone, factors, zone.Side, &low, &high)
	if result == nil {
		return nil
	}
	id := fd.ZoneID(zone)
	if tf != market.M5 {
		id += "@" + string(tf)
	}
	return &flipDecision{
		decision: &strategyutil.TechniqueDecision{
			Technique: "flip_zone", Direction: m5.Direction, Detector: m5, Confirmation: conf, Result: result, ID: id,
			Score: func(dec *strategyutil.TechniqueDecision, g strategyutil.QualityGeometry) opportunity.StrategyQuality {
				return scoreQuality(zone, level, dec, g)
			},
		},
		structure:  structureOf(fd, zones, zone, formation, tf, m5.Settings.PipSize),
		levelPrice: level.Price, levelBand: band,
	}
}

// structureOf is the flip zone as drawn on its own frame: its whole bounds (before any proximal clip), rounded OUTWARD to the instrument's price grid so it always
// contains the published entry, formed at the bar that completed it.
func structureOf(fd *strategyutil.LegacyDetector, zones []techniquezone.Zone, chosen techniquezone.Zone, formation int, tf market.Timeframe, pip float64) *opportunity.StructureZone {
	low, high := chosen.Low(), chosen.High()
	for _, z := range zones {
		if z.Side == chosen.Side && z.Source == chosen.Source && z.OriginIndex == chosen.OriginIndex && z.Low() <= chosen.Low()+1e-9 && z.High() >= chosen.High()-1e-9 {
			low, high = math.Min(low, z.Low()), math.Max(high, z.High())
		}
	}
	digits := 2
	if pip > 0 {
		digits = int(math.Round(-math.Log10(pip))) + 1
	}
	scale := math.Pow(10, float64(digits))
	low, high = math.Floor(low*scale+1e-6)/scale, math.Ceil(high*scale-1e-6)/scale
	formedAt := int64(0)
	if formation >= 0 && formation < len(fd.Frame.Bars) {
		formedAt = fd.Frame.Bars[formation].Time
	}
	return &opportunity.StructureZone{Timeframe: tf, Low: low, High: high, FormedAt: formedAt}
}

// reactionKnownFrom is the first M5 bar a retest may react on: on M5 the bar after the formation bar (index + 1, as ZoneReaction did); on a higher frame the first M5 bar
// that opens at or after the close of the frame bar that completed the zone, so no M5 bar sees a bar that had not closed.
func reactionKnownFrom(m5Bars []market.Candle, frame *context.LegacyFrame, tf market.Timeframe, formation int) int {
	if tf == market.M5 || formation < 0 {
		return formation + 1
	}
	if formation >= len(frame.Bars) {
		return len(m5Bars)
	}
	minutes, _ := tf.Minutes()
	knownAt := frame.Bars[formation].Time + int64(minutes)*60
	return sort.Search(len(m5Bars), func(i int) bool { return m5Bars[i].Time >= knownAt })
}

// selectPerSide keeps at most one candidate per side. First one per flip-level thesis across frames (the same direction, a level within the wider of the two level
// bands, found on different frames) by strategyutil.PreferHigherTimeframe (the higher frame, then more stars, then the nearer entry; the others are counted
// duplicate_thesis); then, on each side, the highest stars with the nearest level winning ties.
func (s *Strategy) selectPerSide(all []flipCandidate) []opportunity.Candidate {
	var out []opportunity.Candidate
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		var mine []flipCandidate
		for _, c := range all {
			if c.direction == direction {
				mine = append(mine, c)
			}
		}
		if len(mine) == 0 {
			continue
		}
		var groups [][]int
		for i, c := range mine {
			placed := false
			for g := range groups {
				if sameThesis(mine, groups[g], c) {
					groups[g] = append(groups[g], i)
					placed = true
					break
				}
			}
			if !placed {
				groups = append(groups, []int{i})
			}
		}
		winners := make([]flipCandidate, 0, len(groups))
		for _, g := range groups {
			best := g[0]
			for _, i := range g[1:] {
				if strategyutil.PreferHigherTimeframe(rankOf(mine[i]), rankOf(mine[best])) {
					best = i
				}
			}
			for _, i := range g {
				if i != best {
					s.refuse(mine[i].tf, RefusedDuplicateThesis)
				}
			}
			winners = append(winners, mine[best])
		}
		sort.SliceStable(winners, func(i, j int) bool { return winners[i].distance < winners[j].distance })
		best := winners[0]
		for _, w := range winners[1:] {
			if w.stars > best.stars {
				best = w
			}
		}
		out = append(out, best.candidate)
	}
	return out
}

func rankOf(c flipCandidate) strategyutil.ThesisRank {
	return strategyutil.ThesisRank{Timeframe: c.tf, Stars: c.stars, EntryDistance: c.entryGap}
}

func sameThesis(all []flipCandidate, members []int, c flipCandidate) bool {
	for _, i := range members {
		m := all[i]
		if m.tf == c.tf || math.Abs(m.levelPrice-c.levelPrice) > math.Max(m.levelBand, c.levelBand) {
			return false
		}
	}
	return true
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func hasSource(zone techniquezone.Zone, source string) bool {
	if len(zone.Sources) == 0 {
		return zone.Source == source
	}
	for _, s := range zone.Sources {
		if s == source {
			return true
		}
	}
	return false
}

// flipLevel mirrors _flip_zone_level: the key level whose price lies within its
// band of the zone's anchor edge (the bottom of a demand zone, the top of a
// supply zone), the nearest one.
func flipLevel(levels []techniquezone.Level, zone techniquezone.Zone) (techniquezone.Level, bool) {
	anchor := zone.Top
	if zone.Side == "demand" {
		anchor = zone.Bottom
	}
	var best techniquezone.Level
	bestGap, found := 0.0, false
	for _, level := range levels {
		gap := math.Abs(level.Price - anchor)
		if gap > math.Max(level.Band, 0)+1e-9 {
			continue
		}
		if !found || gap < bestGap {
			best, bestGap, found = level, gap, true
		}
	}
	return best, found
}
