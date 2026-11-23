// Package keylevel implements Key Level, the strategy that produced the
// profitable XAU week (14–18 Sep 2026: +567 pips, 6W/3L) in the Python era.
//
// It is the frozen Python detector key_level_reaction, run on the same
// detector-contract frame as the other legacy-contract strategies, so the same
// bars give the same level, role, direction, reaction and entry:
//
//  1. Levels are walked nearest first; one with fewer touches than the
//     configured minimum is skipped.
//  2. The level's reaction band is the wider of its own band and the proximal
//     band (proximal_band_atr · ATR). Its closed-bar role (support,
//     resistance, ambiguous, or broken after enough accepted closes) decides
//     the directions tried: support BUY, resistance SELL, and for an ambiguous
//     role the side price sits on, unless a live opposing zone overlaps the
//     band — then both sides are tried over the widened window and the zone's
//     edge is the level the opposite side reacts off. A broken level is
//     Break & Retest's, never reinterpreted here.
//  3. A direction needs a confirmed structural reaction off the band; a level
//     where both sides confirm is a contradiction and yields nothing.
//  4. The reaction passes the shared qualification (level on the right side of
//     price, entry no further than max_entry_atr, confluence at or above the
//     floor).
//  5. Of all levels that qualify, ONE candidate is kept: the highest
//     confluence among the candidates that are PUBLISHED (a refused one, for the
//     stop budget or for want of a sweep, never suppresses another), nearest
//     level winning ties. The strategy owns that selection; nothing downstream
//     has to choose between its levels.
//  6. With require_reaction_sweep (XAU, docs/strategies/keylevel-sweep-first.md)
//     a reaction is published only if it swept the level.
//
// Key Level carries no strength, proximity or target-room filter of its own:
// the frozen detector had none (its quality gates were confluence and the
// execution policy), and each one measurably removed decisions the profitable
// system took. The candidate's target is the nearest UNSWEPT opposing liquidity
// pool ahead of the entry (the candidate's own wall excluded) at whatever
// distance, and only when none stands in the way a fixed reward:risk of the
// EFFECTIVE risk beyond the entry (v8: a pool nearer than the old minimum
// distance, or already swept, no longer decides whether the path is "clear");
// whether the room is enough is the execution policy's decision, as it was.
package keylevel

import (
	"fmt"
	"math"
	"sort"
	"strings"
	"sync"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "key_level"
const Version = "v12"

// ReactionSweptEvidence marks a candidate published under require_reaction_sweep: the reaction traded beyond the level band's far edge.
const ReactionSweptEvidence = "key_level_reaction_swept"

const (
	contractProfitWeek = "profit_week"
	contractCurrent    = "current"
)

const (
	structureVersion = "v2"
	liquidityVersion = "v1"
	zoneVersionUsed  = "v1"
	configVersion    = 3
)

// Config is Key Level's own parsed technical configuration.
type Config struct {
	MinimumTouches int
	// MinimumSellZoneScore is the optional floor on the nearest supply zone's
	// score for a SELL (0 disables it); the frozen detector carried it as an
	// instrument quality knob.
	MinimumSellZoneScore  float64
	InvalidationBufferATR float64
	FallbackTargetR       float64
	ExpiryHours           float64
	// StructureCloseBufferATR is the margin, in M5 ATRs, by which an M5 CLOSE must go beyond the reaction band's far edge to withdraw the opportunity
	// (the engine's structure-close rule, KL11): 0 means any close beyond it. A wick that closes back inside never withdraws.
	StructureCloseBufferATR float64
	PriceDigits             int
	// RequireExplicitRole skips levels whose support/resistance role is still
	// ambiguous (legacy per-instrument Key Level quality rule).
	RequireExplicitRole bool
	// RequireReactionSweep publishes a confirmed reaction only when it swept the level: the lowest low (BUY) or highest high (SELL) of the bars from the
	// touch to the confirmation lies strictly beyond the reaction band's far edge (the same reactLow/reactHigh ReactionAnchoredInvalidation reads, so it is
	// exactly when the invalidation is anchored to the reaction's own extreme). Override-only; docs/strategies/keylevel-sweep-first.md.
	RequireReactionSweep bool
	// BreakoutAcceptBars is key_level_role.py's breakout_accept_bars.
	BreakoutAcceptBars int
	// Contract selects the structure Key Level reads: "profit_week" is the
	// detector contract of 14–18 Sep 2026 (levels counted from swings, order
	// blocks caused by a BOS), "current" the one the other detectors read.
	Contract string
	Detector strategyutil.LegacyDetectorSettings
	// StopBudget is the instrument's stop envelope (strategyutil.StopBudget); the zero value is no budget.
	StopBudget strategyutil.StopBudget
	// StructureTimeframes are the frames a level may be found on (v12, owner rule 23 Nov 2026: setups are found on M5, M15 and H1; M1 is execution only). The
	// touch, the reaction, the stop and the entry are always read on M5, whatever frame the level lives on.
	StructureTimeframes []market.Timeframe
	// RequireConfirmation is whether a confirming M5 reaction is needed: the strategy's own require_confirmation, else the global
	// analysis.require_candle_confirmation. A bare touch is published only when it is false.
	RequireConfirmation bool
}

// Refusals of Key Level (v12), by reason (strategy_refused{strategy="key_level", structure_timeframe, reason}).
const (
	// RefusedNoM5Reaction: a level with enough touches on its own frame that no M5 reaction confirmed (a level is counted once per evaluation).
	RefusedNoM5Reaction = "no_m5_reaction"
	// RefusedBothSides: both directions confirmed off one level in one evaluation, a contradiction that publishes nothing.
	RefusedBothSides = "both_sides_confirmed"
	// RefusedDuplicateThesis: the same level qualified on more than one frame; the higher timeframe (then more stars, then the nearer entry) published and this one did not.
	RefusedDuplicateThesis = "duplicate_thesis"
)

// LevelSource replaces the levels Key Level reads (the frame's SwingLevels under the profit_week contract) for this strategy's evaluation only: the frame itself, and
// every other strategy that reads its levels, is untouched. seed is the latest closed bar's open time, so a source that draws random levels is deterministic.
// It is a measurement hook (replaycapture.Options.KeyLevelSource, docs/strategies/keylevel-edge-source.md) with no production caller; an architecture test enforces that.
type LevelSource func(frame *context.LegacyFrame, seed int64) []techniquezone.Level

// Strategy is KeyLevelStrategy.
type Strategy struct {
	strategyutil.RefusalCounter
	strategyutil.SweepRefusalCounter
	mu          sync.Mutex
	refusals    map[strategy.Refusal]int64
	cfg         Config
	timeframe   market.Timeframe
	fingerprint string
	levelSource LevelSource
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

// UseLevelSource installs a LevelSource. Test-only: nothing in production calls it.
func (s *Strategy) UseLevelSource(source LevelSource) { s.levelSource = source }

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("keylevel: New called with strategy ID %q, want %q", cfg.ID, ID)
	}
	parsed, err := parseConfig(cfg.Parameters)
	if err != nil {
		return nil, fmt.Errorf("keylevel: %w", err)
	}
	s := &Strategy{cfg: parsed, timeframe: market.M5, fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	s.cfg.StopBudget.Refusals = &s.RefusalCounter
	return s, nil
}

func parseConfig(params map[string]any) (Config, error) {
	var cfg Config
	var err error
	if cfg.MinimumTouches, err = strategyutil.Int(params, "minimum_touches"); err != nil {
		return Config{}, err
	}
	if cfg.InvalidationBufferATR, err = strategyutil.Float(params, "invalidation_buffer_atr"); err != nil {
		return Config{}, err
	}
	if cfg.FallbackTargetR, err = strategyutil.Float(params, "fallback_target_r"); err != nil {
		return Config{}, err
	}
	if cfg.ExpiryHours, err = strategyutil.Float(params, "expiry_hours"); err != nil {
		return Config{}, err
	}
	if cfg.StructureCloseBufferATR, err = strategyutil.Float(params, "structure_close_buffer_atr"); err != nil {
		return Config{}, err
	}
	if cfg.BreakoutAcceptBars, err = strategyutil.Int(params, "breakout_accept_bars"); err != nil {
		return Config{}, err
	}
	if cfg.PriceDigits, err = strategyutil.Int(params, "price_digits"); err != nil {
		return Config{}, err
	}
	contract, ok := params["detector_contract"].(string)
	if !ok || (contract != contractProfitWeek && contract != contractCurrent) {
		return Config{}, fmt.Errorf("detector_contract must be %q or %q", contractProfitWeek, contractCurrent)
	}
	cfg.Contract = contract
	if _, present := params["minimum_sell_zone_score"]; present {
		if cfg.MinimumSellZoneScore, err = strategyutil.Float(params, "minimum_sell_zone_score"); err != nil {
			return Config{}, err
		}
	}
	// Override-only parameter (set from an instrument's overrides, which the
	// config schema cannot express as a base boolean): absent means false.
	if raw, present := params["require_explicit_role"]; present {
		b, ok := raw.(bool)
		if !ok {
			return Config{}, fmt.Errorf("parameter \"require_explicit_role\" is not a boolean (got %T)", raw)
		}
		cfg.RequireExplicitRole = b
	}
	// Override-only as well: set from an instrument's overrides, absent means false.
	if raw, present := params["require_reaction_sweep"]; present {
		b, ok := raw.(bool)
		if !ok {
			return Config{}, fmt.Errorf("parameter \"require_reaction_sweep\" is not a boolean (got %T)", raw)
		}
		cfg.RequireReactionSweep = b
	}
	if cfg.Detector, err = strategyutil.ParseLegacyDetectorSettings(params); err != nil {
		return Config{}, err
	}
	if cfg.StopBudget, err = strategyutil.ParseStopBudget(params); err != nil {
		return Config{}, err
	}
	if cfg.StructureTimeframes, err = strategyutil.ParseStructureTimeframes(params, []market.Timeframe{market.M5, market.M15, market.H1}); err != nil {
		return Config{}, err
	}
	if cfg.RequireConfirmation, err = strategyutil.ParseRequireConfirmation(params, strategyutil.RequireCandleConfirmation(params)); err != nil {
		return Config{}, err
	}
	// The detector's own switch follows the strategy's: a bare touch publishes only when this strategy does not require a confirming candle.
	cfg.Detector.CandleConfirmationOptional = !cfg.RequireConfirmation
	switch {
	case cfg.MinimumTouches < 1:
		return Config{}, fmt.Errorf("minimum_touches must be >= 1")
	case cfg.InvalidationBufferATR <= 0:
		return Config{}, fmt.Errorf("invalidation_buffer_atr must be > 0")
	case cfg.FallbackTargetR <= 0:
		return Config{}, fmt.Errorf("fallback_target_r must be > 0")
	case cfg.ExpiryHours <= 0:
		return Config{}, fmt.Errorf("expiry_hours must be > 0")
	case cfg.StructureCloseBufferATR < 0 || math.IsNaN(cfg.StructureCloseBufferATR) || math.IsInf(cfg.StructureCloseBufferATR, 0):
		return Config{}, fmt.Errorf("structure_close_buffer_atr must be a finite number >= 0")
	case cfg.BreakoutAcceptBars < 1:
		return Config{}, fmt.Errorf("breakout_accept_bars must be >= 1")
	case cfg.PriceDigits < 0:
		return Config{}, fmt.Errorf("price_digits must be >= 0")
	}
	return cfg, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

func (s *Strategy) RequiredTimeframes() []market.Timeframe {
	return []market.Timeframe{s.timeframe}
}

// attempt is one direction's qualified reaction off one level.
type attempt struct {
	direction    market.Direction
	levelPrice   float64
	reactLow     float64
	reactHigh    float64
	confirmation *strategyutil.LegacyConfirmation
	result       *strategyutil.LegacyResult
	detector     *strategyutil.LegacyDetector
	level        techniquezone.Level
	role         keylevel.RoleKind
	widened      bool
	// structureTF is the frame the level was found on (M5 when unset); bandFloor is proximal_band_atr x THAT frame's ATR, the floor of the level's band.
	structureTF market.Timeframe
	bandFloor   float64
}

// published is one candidate the strategy would publish, with what the duplicate-thesis order and the selection across levels read.
type published struct {
	candidate  opportunity.Candidate
	stars      int
	tf         market.Timeframe
	direction  market.Direction
	levelPrice float64
	levelBand  float64
	distance   float64 // |level price - the latest M5 close|, the "nearest level" of the selection across levels
	entryGap   float64 // |entry reference - the latest M5 close|, the "nearer entry" of the duplicate-thesis order
}

func (s *Strategy) Evaluate(ctx *context.MarketContext) []opportunity.Candidate {
	// The touch, the reaction, the stop and the entry are read on M5, whatever frame a level lives on.
	m5, ok := strategyutil.NewLegacyDetectorForFrame(ctx, market.M5, s.cfg.Detector)
	if !ok {
		return nil
	}
	var found []published
	for _, tf := range s.cfg.StructureTimeframes {
		frame := m5
		if tf != market.M5 {
			var available bool
			if frame, available = strategyutil.NewLegacyDetectorForFrame(ctx, tf, s.cfg.Detector); !available {
				continue
			}
		}
		found = append(found, s.evaluateFrame(ctx, m5, frame, tf)...)
	}
	best := s.selectOne(found)
	if best == nil {
		return nil
	}
	return []opportunity.Candidate{best.candidate}
}

// evaluateFrame is every candidate Key Level would publish for the levels of ONE structure frame (frame, tf): the level, its touches, its role and the moment it
// became known are read on that frame; the touch of the band and the reaction (and so the stop and the entry) are read on M5 (m5).
//
// Each level's confirmed attempt becomes a full candidate (reaction-anchored invalidation, the sweep requirement, the stop budget, the target) BEFORE
// the highest-stars one is kept, so an attempt the strategy then refuses can no longer suppress a valid one on another level. (v5 chose the best
// attempt by stars first and refused it afterwards, dropping the rest.)
func (s *Strategy) evaluateFrame(ctx *context.MarketContext, m5, frameDetector *strategyutil.LegacyDetector, tf market.Timeframe) []published {
	frame, atr := frameDetector.Frame, frameDetector.ATR
	price := m5.Price
	closes := make([]float64, len(frame.Bars))
	for i, bar := range frame.Bars {
		closes[i] = bar.Close
	}
	// The level's band is its own on its frame, never thinner than proximal_band_atr of THAT frame's ATR.
	zoneBandFloor := math.Max(1e-9, s.cfg.Detector.ProximalBandATR*math.Max(0, atr))
	levels, zones := s.structure(frameDetector)
	coverage := levelCoverage(levels, s.cfg.MinimumTouches, frame.Bars)

	var out []published
	for _, level := range levels {
		if level.Touches < maxInt(1, s.cfg.MinimumTouches) {
			continue
		}
		band := math.Max(level.Band, zoneBandFloor)
		bandLow, bandHigh := level.Price-band, level.Price+band
		// The role is read from the confirmed swings that formed the level and from the closed bars since (keylevel.StructuralRole), never from its kind label.
		lows, highs := confirmedSwingsInBand(frame, level.Price, level.Band)
		role, acceptedBeyond := keylevel.StructuralRole(lows, highs, s.cfg.MinimumTouches, market.Price(bandLow), market.Price(bandHigh), closes, s.cfg.BreakoutAcceptBars)
		// An accepted role flip belongs to Break & Retest; Key Level never
		// reinterprets it in the opposite direction.
		if role == keylevel.RoleBrokenSupport || role == keylevel.RoleBrokenResistance {
			continue
		}
		// A level of mixed or insufficient structure has no side to break; price that has accepted beyond it is no longer reacting off it (the rule every level
		// followed before v9, when none had a role).
		if role == keylevel.RoleAmbiguous && acceptedBeyond {
			continue
		}
		if role == keylevel.RoleAmbiguous && s.cfg.RequireExplicitRole {
			continue
		}

		reactLow, reactHigh := bandLow, bandHigh
		var directions []market.Direction
		var contra market.Direction
		var contraLevel float64
		widened := false
		switch {
		case role == keylevel.RoleSupport:
			directions = []market.Direction{market.Buy}
		case role == keylevel.RoleResistance:
			directions = []market.Direction{market.Sell}
		case price > bandHigh:
			if opposing, found := opposingZone(zones, bandLow, bandHigh, "supply"); found {
				directions = []market.Direction{market.Buy, market.Sell}
				reactLow, reactHigh = math.Min(bandLow, opposing.Low()), math.Max(bandHigh, opposing.High())
				contra, contraLevel, widened = market.Sell, opposing.High(), true
			} else {
				directions = []market.Direction{market.Buy}
			}
		case price < bandLow:
			if opposing, found := opposingZone(zones, bandLow, bandHigh, "demand"); found {
				directions = []market.Direction{market.Sell, market.Buy}
				reactLow, reactHigh = math.Min(bandLow, opposing.Low()), math.Max(bandHigh, opposing.High())
				contra, contraLevel, widened = market.Buy, opposing.Low(), true
			} else {
				directions = []market.Direction{market.Sell}
			}
		default:
			// Price is inside the level's own band: the direction comes from which
			// side actually confirms, never a guess.
			directions = []market.Direction{market.Buy, market.Sell}
		}

		var confirmed []attempt
		// The reaction can only be at a level that already exists: the touch and the confirmation are M5 bars at or after the moment the level became known on its frame.
		knownFrom := reactionKnownFrom(m5.Frame.Bars, frame, tf, level, s.cfg.MinimumTouches)
		for _, direction := range directions {
			if direction == market.Sell && s.cfg.MinimumSellZoneScore > 0 {
				score, found := nearestSameSideZoneScore(zones, price, "supply")
				if !found || score < s.cfg.MinimumSellZoneScore {
					continue
				}
			}
			levelPrice := level.Price
			if contra != "" && direction == contra {
				levelPrice = contraLevel
			}
			d := m5.WithDirection(direction)
			conf := s.confirmReaction(d, reactLow, reactHigh, d.LevelGrab(level.Price, level.Band), knownFrom)
			if conf == nil {
				continue
			}
			zoneSide := "demand"
			if direction == market.Sell {
				zoneSide = "supply"
			}
			zone := techniquezone.Zone{Bottom: reactLow, Top: reactHigh, Side: zoneSide, Source: "level", BreakIndex: -1}
			factors := strategyutil.FactorsForConfirmation(reactionFactors(conf.Type, d.HTFAligned(), level.Touches, d.SessionSweepContext(conf)), conf.Type)
			result := d.Finish(levelPrice, zone, factors, level.Kind, &reactLow, &reactHigh)
			if result == nil {
				continue
			}
			confirmed = append(confirmed, attempt{
				direction: direction, levelPrice: levelPrice, reactLow: reactLow, reactHigh: reactHigh,
				confirmation: conf, result: result, detector: d, level: level, role: role, widened: widened,
				structureTF: tf, bandFloor: zoneBandFloor,
			})
		}
		switch len(confirmed) {
		case 0:
			s.refuse(tf, RefusedNoM5Reaction)
			continue
		case 1:
		default:
			// Two (both sides confirmed off the same level in one evaluation): a contradiction, not a coin flip.
			s.refuse(tf, RefusedBothSides)
			continue
		}
		chosen := confirmed[0]
		candidate, ok := s.candidate(ctx, m5, &chosen, coverage)
		if !ok {
			continue
		}
		entryReference := candidate.Entry.High
		if chosen.direction == market.Sell {
			entryReference = candidate.Entry.Low
		}
		out = append(out, published{
			candidate: candidate, stars: chosen.result.Stars, tf: tf, direction: chosen.direction, levelPrice: level.Price, levelBand: band,
			distance: math.Abs(level.Price - price), entryGap: math.Abs(entryReference - price),
		})
	}
	return out
}

// reactionKnownFrom is the M5 bar on which a level found on frame (tf) became known, the earliest M5 bar the touch and the reaction may be. On M5 it is the bar index
// the level's own frame gives (as before v12). On a higher frame the level is known when the bar that completed it CLOSED: the first M5 bar that opens at or after
// that close, so a reaction can never precede the level and no M5 bar sees an H1 bar that had not closed. A level with no swing in its band (a measurement hook's
// level) has no formation to wait for: 0.
func reactionKnownFrom(m5Bars []market.Candle, frame *context.LegacyFrame, tf market.Timeframe, level techniquezone.Level, minimumTouches int) int {
	index := levelKnownFrom(frame, level.Price, level.Band, minimumTouches)
	if tf == market.M5 || index <= 0 {
		return index
	}
	if index >= len(frame.Bars) {
		return len(m5Bars)
	}
	minutes, _ := tf.Minutes()
	knownAt := frame.Bars[index].Time + int64(minutes)*60
	return sort.Search(len(m5Bars), func(i int) bool { return m5Bars[i].Time >= knownAt })
}

// selectOne is the strategy's selection: first, one candidate per level thesis across frames (the same direction and a level within the wider of the two level
// bands, found on different frames), by strategyutil.PreferHigherTimeframe (the higher frame, then more stars, then the nearer entry); then the existing rule
// across levels, the highest stars with the nearest level winning ties. Candidates the thesis rule drops are counted (duplicate_thesis).
func (s *Strategy) selectOne(found []published) *published {
	if len(found) == 0 {
		return nil
	}
	type group struct{ members []int }
	var groups []group
	for i, c := range found {
		placed := false
		for g := range groups {
			if sameThesis(found, groups[g].members, c) {
				groups[g].members = append(groups[g].members, i)
				placed = true
				break
			}
		}
		if !placed {
			groups = append(groups, group{members: []int{i}})
		}
	}
	winners := make([]published, 0, len(groups))
	for _, g := range groups {
		best := g.members[0]
		for _, i := range g.members[1:] {
			if strategyutil.PreferHigherTimeframe(rankOf(found[i]), rankOf(found[best])) {
				best = i
			}
		}
		for _, i := range g.members {
			if i != best {
				s.refuse(found[i].tf, RefusedDuplicateThesis)
			}
		}
		winners = append(winners, found[best])
	}
	sort.SliceStable(winners, func(i, j int) bool { return winners[i].distance < winners[j].distance })
	best := &winners[0]
	for i := 1; i < len(winners); i++ {
		if winners[i].stars > best.stars {
			best = &winners[i]
		}
	}
	return best
}

func rankOf(p published) strategyutil.ThesisRank {
	return strategyutil.ThesisRank{Timeframe: p.tf, Stars: p.stars, EntryDistance: p.entryGap}
}

// sameThesis reports whether c is the same level thesis as every member of a group so far: the same direction, a different frame from each member, and a level
// within the wider of the two level bands.
func sameThesis(found []published, members []int, c published) bool {
	for _, i := range members {
		m := found[i]
		if m.direction != c.direction || m.tf == c.tf || math.Abs(m.levelPrice-c.levelPrice) > math.Max(m.levelBand, c.levelBand) {
			return false
		}
	}
	return true
}

// reactionFactors mirrors _reaction_factors: the evidence the confirmation
// actually showed, mapped onto the common rubric. The session context is the
// measured one (a finalized session level swept and reclaimed in the reaction's
// window); the frozen scanner set it on every decision.
func reactionFactors(confirmationType string, htfAligned bool, touches int, sessionSweep bool) confluence.Factors {
	return strategyutil.ReactionFactors(confirmationType, htfAligned, touches, sessionSweep)
}

func (s *Strategy) candidate(ctx *context.MarketContext, d *strategyutil.LegacyDetector, a *attempt, coverage float64) (opportunity.Candidate, bool) {
	atr := d.ATR
	direction := a.direction
	buffer := s.cfg.InvalidationBufferATR * atr
	low, high := a.reactLow, a.reactHigh
	// Beyond the reaction band and beyond the bars the reaction itself traded (a sweep's wick pierces the band).
	invalidation, anchored := strategyutil.ReactionAnchoredInvalidation(d.Frame.Bars, a.confirmation, direction, low, high, buffer)
	// The reaction swept the level exactly when its own extreme lies strictly beyond the band's far edge, which is when the invalidation is anchored to it.
	// Without a sweep the candidate is refused (counted apart from the stop budget); the edge of Key Level belongs to the reaction that took the liquidity.
	if s.cfg.RequireReactionSweep && !anchored {
		s.SweepRefusalCounter.CountUnswept()
		return opportunity.Candidate{}, false
	}
	// The instrument stop budget never moves the invalidation. A stop wider than the cap from the proximal edge of the reaction band is fitted by cutting the
	// band to the part within the cap (a deeper entry, stop_budget_deep_entry); a band whose far edge is still beyond the cap is refused.
	fullLow, fullHigh := low, high
	low, high, budgetCode, publish := s.cfg.StopBudget.Fit(direction, fullLow, fullHigh, invalidation)
	if !publish {
		return opportunity.Candidate{}, false
	}
	reference := high
	if direction == market.Sell {
		reference = low
	}
	target, targetLabel, ok := s.target(d, direction, reference, invalidation, atr, ownWall{low: fullLow, high: fullHigh, levelPrice: a.level.Price, levelBand: a.level.Band})
	if !ok {
		return opportunity.Candidate{}, false
	}
	levelID := fmt.Sprintf("keylevel:%s:%.*f", a.level.Kind, s.cfg.PriceDigits, a.level.Price)
	conf := a.confirmation
	setupKey := fmt.Sprintf("%s:%s:%d:%d", levelID, direction, conf.TouchTime, conf.ConfirmationTime)
	structureTF := a.structureTF
	if structureTF == "" {
		structureTF = market.M5
	}
	evidence := []string{
		"structure_tf_" + strings.ToLower(string(structureTF)),
		"m5_key_level_" + a.level.Kind,
		"m5_key_level_touches_sufficient",
		"m5_key_level_role_" + a.role.String(),
	}
	if conf.Type != "" { // the confirmation evidence is published only when a confirming candle showed it
		evidence = append(evidence, "m5_key_level_rejection_confirmed")
	} else {
		evidence = append(evidence, strategyutil.CandleConfirmationOptionalEvidence)
	}
	if a.widened {
		evidence = append(evidence, "m5_key_level_opposing_zone_widened")
	}
	if anchored {
		evidence = append(evidence, strategyutil.ReactionExtremeEvidence)
	}
	if s.cfg.RequireReactionSweep {
		evidence = append(evidence, ReactionSweptEvidence)
	}
	if budgetCode != "" {
		evidence = append(evidence, budgetCode)
	}
	// The band the reaction was read in: the level's own, but never thinner than the detector's proximal floor (a round number has none).
	effectiveBand := math.Max(a.level.Band, math.Max(1e-9, s.cfg.Detector.ProximalBandATR*math.Max(0, atr)))
	if a.bandFloor > 0 {
		effectiveBand = math.Max(a.level.Band, a.bandFloor) // the floor of the frame the level lives on
	}
	htfTouches, htfWeight := higherTimeframeLevel(ctx, a.level.Price, s.cfg.Contract, s.cfg.Detector)
	quality := scoreQuality(qualityInputs{
		bars: d.Frame.Bars, touchIndex: conf.TouchIndex, confirmIndex: conf.ConfirmationIndex,
		level: a.level.Price, band: effectiveBand, atr: atr, buy: direction == market.Buy, price: d.Price, reference: reference,
		invalidation: invalidation, target: target, structural: targetLabel == "nearest_opposing_liquidity", effectiveRisk: s.cfg.StopBudget.EffectiveRisk(reference, invalidation), pipSize: s.cfg.StopBudget.PipSize,
		localTrend: d.Frame.Structure, choch: reactionCHoCH(a.detector, conf.TouchIndex, conf.ConfirmationIndex), htfAligned: a.detector.HTFAligned(),
		htfTouches: htfTouches, htfWeight: htfWeight,
		levelKind: a.level.Kind, levelBand: a.level.Band, swings: d.Frame.Swings, coverage: coverage,
		deepEntryPips: deepEntryPips(s.cfg.StopBudget.PipSize, direction, fullLow, fullHigh, low, high),
	})
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: setupKey, Symbol: ctx.Symbol, Direction: direction,
		EntryLow: low, EntryHigh: high, Invalidation: invalidation, InvalidationLabel: "key_level_invalidated",
		Target: target, TargetLabel: targetLabel, Evidence: evidence, Quality: quality,
		FormedAt: conf.TouchTime, ConfirmedAt: conf.ConfirmationTime, ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, false
	}
	// The persistent identity of the thesis is the level, not this
	// confirmation: a re-confirmation of the same level is the same thesis.
	candidate.StructuralID = levelID
	// KL11: the reaction band as read (before any stop-budget cut) is the structure the entry rests on. An M5 close beyond its far edge withdraws the
	// opportunity (engine structure-close rule), so a reaction whose level has already failed is never filled later. The level's ROLE is unchanged (v9).
	// Rounded OUTWARD to the instrument's price digits, as the engine rounds the entry band, so the structure always contains the published entry and a close inside the
	// entry band can never read as a close through the structure.
	structLow, structHigh := roundOut(fullLow, fullHigh, s.cfg.PriceDigits)
	// v12: the structure's timeframe is the frame the level was found on (never the confirming M5 frame unless the level is an M5 one); a close of THAT frame beyond
	// the band's far edge withdraws the opportunity.
	candidate.Structure = &opportunity.StructureZone{Timeframe: structureTF, Low: structLow, High: structHigh, FormedAt: conf.TouchTime}
	candidate.StructureTimeframe = structureTF
	if conf.Type != "" {
		candidate.Reaction = &opportunity.ReactionConfirmation{
			ZoneID: levelID, TouchBarTime: conf.TouchTime, ConfirmationBarTime: conf.ConfirmationTime,
			ReactionType: "rejection", Pattern: conf.Type,
		}
	}
	candidate.DetectorConfluence = a.result.ConfluenceContext()
	candidate.Provenance = opportunity.AnalysisProvenance{
		StructureVersion: structureVersion, LiquidityVersion: liquidityVersion, ZoneVersion: zoneVersionUsed,
		ConfigVersion: configVersion, ConfigFingerprint: s.fingerprint,
	}
	return candidate, true
}

// confirmedSwingsInBand counts the frame's swing lows and swing highs inside the level's band that are CONFIRMED at the decision bar: a fractal swing is known only
// once its right-hand bars have closed (ConfirmedIndex), so a pivot that is still forming cannot give a level its role earlier than it could have been known.
func confirmedSwingsInBand(frame *context.LegacyFrame, price, band float64) (lows, highs int) {
	last := len(frame.Bars) - 1
	for _, sw := range frame.Swings {
		if sw.ConfirmedIndex > last || math.Abs(sw.Price-price) > band {
			continue
		}
		if sw.Kind == "high" {
			highs++
		} else {
			lows++
		}
	}
	return lows, highs
}

// ownWall is the candidate's own structure: the full reaction band the entry was read in (before any deep-entry cut) and the level's own cluster. A
// liquidity pool inside it is the wall the trade rests on, never an opposing obstacle.
type ownWall struct {
	low, high, levelPrice, levelBand float64
}

func (w ownWall) contains(pool techniquezone.Pool) bool {
	return pool.Level >= w.low-pool.Band && pool.Level <= w.high+pool.Band ||
		math.Abs(pool.Level-w.levelPrice) <= math.Max(w.levelBand, pool.Band)
}

// poolSwept reports whether price has already traded beyond the pool's outer edge after the swing that last defined it: the liquidity resting there has
// been taken, so it is neither an objective nor an obstacle. It is the sweep test liquidity grabs use (techniquezone.LiquidityGrabs): a high beyond
// level + band for a buy-side pool, a low beyond level - band for a sell-side one. Closed frame bars only.
func poolSwept(bars []market.Candle, pool techniquezone.Pool) bool {
	for i := pool.LastIndex + 1; i < len(bars); i++ {
		if pool.Side == "buy" && bars[i].High > pool.Level+pool.Band || pool.Side == "sell" && bars[i].Low < pool.Level-pool.Band {
			return true
		}
	}
	return false
}

// target is the candidate's objective, from the opposing liquidity ahead of the entry.
//
// Opposing liquidity is a pool on the side the trade runs toward that is strictly ahead of the proximal entry edge, has not been swept since its last
// defining swing, and is not the candidate's own wall. The nearest such pool is the target (nearest_opposing_liquidity) at whatever distance: a pool that
// is close leaves little room, and that is what the candidate then says. Only when no such pool stands in the way is the target a fixed
// fallback_target_r times the EFFECTIVE risk beyond the entry (fixed_reward_risk): the risk the trade is actually exposed to, the structural distance or the
// instrument stop floor when that is wider (StopBudget.EffectiveRisk), because execution widens a tighter stop to the floor. Whether the room is enough
// stays the execution policy's decision: nothing here refuses a candidate for its room.
func (s *Strategy) target(d *strategyutil.LegacyDetector, direction market.Direction, reference, invalidation, atr float64, wall ownWall) (float64, string, bool) {
	risk := s.cfg.StopBudget.EffectiveRisk(reference, invalidation)
	if !(risk > 0) {
		return 0, "", false
	}
	want := "buy"
	if direction == market.Sell {
		want = "sell"
	}
	// A pool within a tick or 5% of an ATR of the entry edge shares its boundary: it is the same wall, as the execution's shared-boundary tolerance reads it.
	glue := math.Max(s.cfg.StopBudget.PipSize, 0.05*math.Max(0, atr))
	best, bestDistance := 0.0, math.Inf(1)
	for _, pool := range d.Frame.Pools {
		if pool.Side != want {
			continue
		}
		distance := pool.Level - reference
		if direction == market.Sell {
			distance = reference - pool.Level
		}
		if distance <= glue || distance >= bestDistance || wall.contains(pool) || poolSwept(d.Frame.Bars, pool) {
			continue
		}
		best, bestDistance = pool.Level, distance
	}
	if !math.IsInf(bestDistance, 1) {
		return best, "nearest_opposing_liquidity", true
	}
	if direction == market.Sell {
		return reference - s.cfg.FallbackTargetR*risk, "fixed_reward_risk", true
	}
	return reference + s.cfg.FallbackTargetR*risk, "fixed_reward_risk", true
}

// structure returns the levels (nearest first) and zones Key Level reads under
// its configured detector contract.
func (s *Strategy) structure(d *strategyutil.LegacyDetector) ([]techniquezone.Level, []techniquezone.Zone) {
	levels, zones := d.Frame.Levels, d.Frame.Zones
	if s.cfg.Contract == contractProfitWeek {
		levels, zones = d.Frame.SwingLevels, d.Frame.ContractZones
	}
	if s.levelSource != nil && len(d.Frame.Bars) > 0 {
		levels = s.levelSource(d.Frame, d.Frame.Bars[len(d.Frame.Bars)-1].Time)
	}
	ordered := append([]techniquezone.Level(nil), levels...)
	sort.SliceStable(ordered, func(i, j int) bool {
		return math.Abs(ordered[i].Price-d.Price) < math.Abs(ordered[j].Price-d.Price)
	})
	return ordered, zones
}

// opposingZone mirrors _opposing_zone_contradicts: the first live zone of the
// given side overlapping the band. (The frozen detector scanned the scored
// zones and then their order-block view, which repeats members of the first.)
func opposingZone(zones []techniquezone.Zone, bandLow, bandHigh float64, side string) (techniquezone.Zone, bool) {
	for _, z := range zones {
		if z.Side != side || z.Mitigated {
			continue
		}
		if z.Low() <= bandHigh && z.High() >= bandLow {
			return z, true
		}
	}
	return techniquezone.Zone{}, false
}

// nearestSameSideZoneScore mirrors _nearest_same_side_zone_score: the score of
// the same-side zone containing price, else the one whose midpoint is nearest.
func nearestSameSideZoneScore(zones []techniquezone.Zone, price float64, side string) (float64, bool) {
	var best *techniquezone.Zone
	bestDistance := math.Inf(1)
	for i := range zones {
		z := zones[i]
		if z.Side != side {
			continue
		}
		distance := 0.0
		if !(z.Low() <= price && price <= z.High()) {
			distance = math.Abs((z.Low()+z.High())/2 - price)
		}
		if best == nil || distance < bestDistance {
			zone := z
			best, bestDistance = &zone, distance
		}
	}
	if best == nil {
		return 0, false
	}
	return best.Score, true
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// deepEntryPips is how far the proximal edge of the entry band moved from the reaction band's own edge to meet the stop cap (0 when the whole band fits). Informational.
// roundOut widens a band to the price grid (digits decimals): the low down, the high up, tolerant of float noise on a price already on the grid.
func roundOut(low, high float64, digits int) (float64, float64) {
	scale := math.Pow(10, float64(digits))
	return math.Floor(low*scale+1e-6) / scale, math.Ceil(high*scale-1e-6) / scale
}

func deepEntryPips(pip float64, direction market.Direction, fullLow, fullHigh, low, high float64) float64 {
	if !(pip > 0) {
		return 0
	}
	if direction == market.Sell {
		return math.Abs(low-fullLow) / pip
	}
	return math.Abs(high-fullHigh) / pip
}
