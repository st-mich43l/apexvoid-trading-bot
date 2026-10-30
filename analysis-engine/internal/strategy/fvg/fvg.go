// Package fvg is the fair value gap strategy. It owns its whole mechanism: what a gap is and when it is dead (structure.go), how a reaction off it is
// confirmed (confirm.go), whether the entry is valid and the qualification (decision.go), and the candidate it publishes (candidate.go). It reads the
// market only through the closed bars and facts of the frames in the context.
package fvg

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "fvg"
const Version = "v6"

const (
	structureVersion = "v2"
	liquidityVersion = "v1"
	zoneVersionUsed  = "v1"
	configVersion    = 3
)

// Strategy is the fair value gap strategy.
type Strategy struct {
	strategyutil.RefusalCounter
	cfg         Config
	fingerprint string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("fvg: New called with strategy ID %q, want %q", cfg.ID, ID)
	}
	parsed, err := parseConfig(cfg.Parameters)
	if err != nil {
		return nil, fmt.Errorf("fvg: %w", err)
	}
	s := &Strategy{cfg: parsed, fingerprint: configFingerprint(cfg)}
	s.cfg.StopBudget.Refusals = &s.RefusalCounter
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

// Evaluate returns the confirmed reaction off the best gap of the execution frame and, separately, off the best gap of the configured higher frames:
// a higher-frame decision never competes with the execution frame's.
func (s *Strategy) Evaluate(ctx *context.MarketContext) []opportunity.Candidate {
	base, ok := newView(ctx, &s.cfg)
	if !ok {
		return nil
	}
	exec := s.frameInstances(base.frame, true, nil)
	var higher []instance
	for _, tf := range s.cfg.HigherTimeframes {
		frameContext := ctx.Timeframes[tf]
		if frameContext == nil || frameContext.Legacy == nil {
			continue
		}
		higher = append(higher, bind(tf, frameContext.Legacy.Bars, base.frame.Bars, s.frameInstances(frameContext.Legacy, false, nil))...)
	}
	var out []opportunity.Candidate
	for _, d := range []*decision{s.best(base, exec), s.best(base, higher)} {
		if d == nil {
			continue
		}
		if candidate, ok := s.candidate(candidateContext{symbol: ctx.Symbol}, d); ok {
			out = append(out, candidate)
		}
	}
	return out
}

// configFingerprint is the strategy's version-pinned parameter digest.
func configFingerprint(cfg strategy.Config) string {
	keys := make([]string, 0, len(cfg.Parameters))
	for k := range cfg.Parameters {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	canonical := string(cfg.ID) + "\x00" + cfg.Version
	for _, k := range keys {
		canonical += "\x00" + k + "=" + fmt.Sprint(cfg.Parameters[k])
	}
	sum := sha256.Sum256([]byte(canonical))
	return hex.EncodeToString(sum[:16])
}
