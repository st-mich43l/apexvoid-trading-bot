// Package sessionlevel publishes the frozen session_level_reaction decision: a
// confirmed reaction off an Asia/London/NY high or low, or the previous day's or
// week's, qualified through the shared detector contract.
//
// A high implies resistance (sell reaction) and a low support (buy reaction). A
// level price has since traded through stays valid only with a reclaim-type
// confirmation. The session levels are the frame's, built by the frozen
// session_liquidity rules over the bounded analysis window (not the layered
// session primitive, which orders and expires them differently).
package sessionlevel

import (
	"fmt"
	"math"
	"sort"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/reaction"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "session_level"
const Version = "v3"

const (
	structureVersion = "v2"
	liquidityVersion = "v1"
	zoneVersionUsed  = "v1"
	configVersion    = 3
)

// Config is SessionLevel's own parsed technical configuration.
type Config struct {
	InvalidationBufferATR    float64
	MinimumTargetDistanceATR float64
	ExpiryHours              float64
	// Legacy qualifies the reaction through the frozen detector contract; its
	// ProximalBandATR is the reaction band around the level.
	Legacy strategyutil.LegacyDetectorSettings
}

// Strategy is SessionLevelStrategy.
type Strategy struct {
	cfg         Config
	fingerprint string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("sessionlevel: New called with strategy ID %q, want %q", cfg.ID, ID)
	}
	parsed, err := parseConfig(cfg.Parameters)
	if err != nil {
		return nil, fmt.Errorf("sessionlevel: %w", err)
	}
	return &Strategy{cfg: parsed, fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}, nil
}

func parseConfig(params map[string]any) (Config, error) {
	var out Config
	var err error
	for key, dst := range map[string]*float64{
		"invalidation_buffer_atr":     &out.InvalidationBufferATR,
		"minimum_target_distance_atr": &out.MinimumTargetDistanceATR,
		"expiry_hours":                &out.ExpiryHours,
	} {
		if *dst, err = strategyutil.Float(params, key); err != nil {
			return Config{}, err
		}
	}
	if out.Legacy, err = strategyutil.ParseLegacyDetectorSettings(params); err != nil {
		return Config{}, err
	}
	if out.InvalidationBufferATR <= 0 || out.ExpiryHours <= 0 || out.Legacy.ProximalBandATR <= 0 {
		return Config{}, fmt.Errorf("invalidation_buffer_atr, expiry_hours and proximal_band_atr must be > 0")
	}
	return out, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

// Evaluate mirrors session_level_reaction: the session levels nearest to price
// first, each judged for a reaction off its band, and the best-scored
// qualifying one (the first on a tie) is published.
func (s *Strategy) Evaluate(ctx *context.MarketContext) []opportunity.Candidate {
	base, ok := strategyutil.NewLegacyDetectorForFrame(ctx, market.M5, s.cfg.Legacy)
	if !ok {
		return nil
	}
	band := math.Max(1e-9, s.cfg.Legacy.ProximalBandATR*math.Max(0, base.ATR))
	sessions := append([]techniquezone.SessionRef(nil), base.Frame.Sessions...)
	sort.SliceStable(sessions, func(i, j int) bool {
		return math.Abs(sessions[i].Price-base.Price) < math.Abs(sessions[j].Price-base.Price)
	})
	var best *strategyutil.TechniqueDecision
	for _, session := range sessions {
		direction, known := levelDirection(session.Name)
		if !known {
			continue
		}
		d := base.WithDirection(direction)
		low, high := session.Price-band, session.Price+band
		conf := d.Reaction(low, high, nil)
		if conf == nil {
			continue
		}
		if session.Swept && conf.Type != reaction.TypeSweepReclaim && conf.Type != reaction.TypeStrongReclaim && conf.Type != reaction.TypeRejectionCHoCH {
			continue
		}
		side := "supply"
		if direction == market.Buy {
			side = "demand"
		}
		zone := techniquezone.Zone{Bottom: low, Top: high, Side: side, Source: "level", BreakIndex: -1}
		if !d.EntryValid(zone) {
			continue
		}
		factors := strategyutil.FactorsForConfirmation(strategyutil.ReactionFactors(conf.Type, d.HTFAligned(), 2), conf.Type)
		result := d.Finish(session.Price, zone, factors, session.Name, &low, &high)
		if result == nil {
			continue
		}
		if best == nil || result.Stars > best.Result.Stars {
			best = &strategyutil.TechniqueDecision{
				Technique: "session_level", Direction: direction, Detector: d, Confirmation: conf, Result: result,
				ID: fmt.Sprintf("session:%s:%.5f", session.Name, session.Price),
			}
		}
	}
	candidate, ok := strategyutil.TechniqueCandidate(ctx, best, strategyutil.TechniqueSpec{
		ID: string(ID), Version: Version, ZoneEvidence: "session_level_" + strings.ToLower(sessionName(best)), ConfirmedEvidence: "session_level_reaction_confirmed",
		InvalidationLabel: "session_level_invalidated", InvalidationBufferATR: s.cfg.InvalidationBufferATR,
		MinimumTargetDistanceATR: s.cfg.MinimumTargetDistanceATR, ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
		Versions: opportunity.AnalysisProvenance{
			StructureVersion: structureVersion, LiquidityVersion: liquidityVersion, ZoneVersion: zoneVersionUsed,
			ConfigVersion: configVersion, ConfigFingerprint: s.fingerprint,
		},
	})
	if !ok {
		return nil
	}
	return []opportunity.Candidate{candidate}
}

// sessionName is the level name inside the decision's identity.
func sessionName(dec *strategyutil.TechniqueDecision) string {
	if dec == nil {
		return ""
	}
	parts := strings.Split(dec.ID, ":")
	if len(parts) < 3 {
		return ""
	}
	return parts[1]
}

// levelDirection reads the reaction direction off the level's name: a high
// (ASIA_H, LONDON_H, NY_H, PDH, PWH) is resistance, a low support.
func levelDirection(name string) (market.Direction, bool) {
	switch {
	case strings.HasSuffix(name, "_H"), name == "PDH", name == "PWH":
		return market.Sell, true
	case strings.HasSuffix(name, "_L"), name == "PDL", name == "PWL":
		return market.Buy, true
	default:
		return "", false
	}
}
